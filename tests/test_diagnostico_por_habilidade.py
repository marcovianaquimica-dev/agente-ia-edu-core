"""Dizer algo mais útil que "Estequiometria: precisa de atenção".

Cada item do banco declara a micro-habilidade que mede (`diagnostic_skill`).
Depois do diagnóstico dá para olhar o desempenho POR HABILIDADE e, às vezes,
dizer algo melhor:

    "Você entende a proporção entre os coeficientes, mas ainda precisa
     praticar a relação entre mol e massa."

AS VEZES. E aí está o assunto deste arquivo.

Três questões por sessão, quatro habilidades: o normal é cada habilidade
receber UMA resposta. Uma resposta não distingue quem sabe de quem chutou — e
afirmar "você entende proporção" a partir de um acerto é inventar sobre a
pessoa que confiou no diagnóstico.

A amostra mínima é a da `PerformanceThresholdPolicy`, a mesma que decide todo
o resto. Nada de número novo.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.diagnostico_por_habilidade import (
    diagnostico_por_habilidade,
)
from agente_ia_edu.services.pedagogical_analysis import PerformanceThresholdPolicy

POLITICA = PerformanceThresholdPolicy.default()
N = POLITICA.min_sample_size

FORTE = "PROPORCAO_ESTEQUIOMETRICA"
FRACA = "RELACAO_MASSA_MOL"


def _respostas(skill: str, certas: int, erradas: int) -> list[dict]:
    return ([{"diagnostic_skill": skill, "is_correct": True}] * certas
            + [{"diagnostic_skill": skill, "is_correct": False}] * erradas)


class AmostraInsuficienteTests(unittest.TestCase):
    """O caso NORMAL de uma sessão de três perguntas."""

    def test_uma_resposta_por_habilidade_nao_gera_texto_especifico(self):
        respostas = (_respostas(FORTE, 1, 0) + _respostas(FRACA, 0, 1)
                     + _respostas("RELACAO_MOL_MOL", 1, 0))
        d = diagnostico_por_habilidade(respostas)
        self.assertFalse(d["suficiente"])
        self.assertIsNone(d["texto"],
                          "afirmou algo sobre uma habilidade com uma resposta")

    def test_as_habilidades_medidas_ficam_registradas_mesmo_assim(self):
        """Não dá para CONCLUIR, mas o professor pode ver o que foi perguntado."""
        d = diagnostico_por_habilidade(_respostas(FORTE, 1, 0))
        self.assertIn(FORTE, d["por_habilidade"])
        self.assertEqual(d["por_habilidade"][FORTE]["answered"], 1)

    def test_amostra_zero_nao_explode(self):
        d = diagnostico_por_habilidade([])
        self.assertFalse(d["suficiente"])
        self.assertIsNone(d["texto"])
        self.assertEqual(d["por_habilidade"], {})


class AmostraSuficienteTests(unittest.TestCase):
    """Depois de praticar, a amostra por habilidade cresce."""

    def test_contraste_claro_vira_texto(self):
        respostas = _respostas(FORTE, N, 0) + _respostas(FRACA, 0, N)
        d = diagnostico_por_habilidade(respostas)
        self.assertTrue(d["suficiente"])
        self.assertIsNotNone(d["texto"])
        self.assertIn("proporção", d["texto"].lower())
        self.assertIn("massa", d["texto"].lower())

    def test_o_texto_nomeia_a_forte_antes_da_fraca(self):
        """Reconhecer o que ele sabe vem primeiro. É ajuda, não boletim."""
        d = diagnostico_por_habilidade(
            _respostas(FORTE, N, 0) + _respostas(FRACA, 0, N))
        texto = d["texto"].lower()
        self.assertLess(texto.index("proporção"), texto.index("mol e massa"))

    def test_tudo_forte_nao_inventa_uma_fraqueza(self):
        d = diagnostico_por_habilidade(
            _respostas(FORTE, N, 0) + _respostas(FRACA, N, 0))
        self.assertIsNone(d["texto"],
                          "sem contraste nao ha o que dizer de especifico")

    def test_tudo_fraco_nao_inventa_uma_forca(self):
        d = diagnostico_por_habilidade(
            _respostas(FORTE, 0, N) + _respostas(FRACA, 0, N))
        self.assertIsNone(d["texto"])

    def test_uma_habilidade_so_nao_da_contraste(self):
        d = diagnostico_por_habilidade(_respostas(FORTE, N, 0))
        self.assertIsNone(d["texto"])


class NaoHaCorteNovoTests(unittest.TestCase):

    def test_o_modulo_nao_contem_numero_de_corte(self):
        import ast
        import pathlib

        import agente_ia_edu.services.diagnostico_por_habilidade as mod

        arvore = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
        numeros = [n.value for n in ast.walk(arvore)
                   if isinstance(n, ast.Constant) and isinstance(n.value, float)]
        self.assertEqual(numeros, [], f"corte escrito a mao: {numeros}")

    def test_endurecer_a_politica_cala_o_texto(self):
        """Se a política passar a exigir mais evidência, o diagnóstico textual
        tem de se calar sozinho — sem editar este módulo."""
        exigente = PerformanceThresholdPolicy(
            min_sample_size=N + 5, strong_accuracy=0.8, improvement_accuracy=0.6)
        d = diagnostico_por_habilidade(
            _respostas(FORTE, N, 0) + _respostas(FRACA, 0, N),
            thresholds=exigente)
        self.assertFalse(d["suficiente"])
        self.assertIsNone(d["texto"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
