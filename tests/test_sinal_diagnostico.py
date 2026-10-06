"""A ASSIMETRIA DIAGNÓSTICA — e a linha que ela não pode cruzar.

O PROBLEMA MEDIDO, EM 2026-10-06
=================================
A sondagem pergunta UMA coisa de cada micro-habilidade — é o que a torna
discriminativa. A `PerformanceThresholdPolicy` exige TRÊS respostas antes de
concluir qualquer coisa. As duas regras estão certas isoladamente e se
anulam em série:

    LEITURA_DE_FORMULA  1/1   INSUFFICIENT_SAMPLE
    MASSA_MOLAR         0/1   INSUFFICIENT_SAMPLE

Nenhuma lacuna medida, nenhum alvo, nenhuma intervenção.

O PRINCÍPIO
============
    Evidência insuficiente para AFIRMAR DOMÍNIO pode ser suficiente
    para ORIENTAR UMA INTERVENÇÃO.

A assimetria vem da diferença de custo. Intervir sobre uma suspeita é barato
e reversível: o aluno recebe uma explicação que talvez já soubesse. Declarar
domínio com uma resposta é caro e errado — com quatro alternativas, o chute
acerta uma vez em quatro.

O QUE ESTE ARQUIVO TRAVA
=========================
Metade é a regra nova. A outra metade — a que importa mais — é que ela
**não encosta no domínio**. Um sinal diagnóstico escolhe a próxima ação; ele
não é evidência, não vira mastery e não contorna o mínimo de amostra.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from agente_ia_edu.services.grafo_pedagogico import (
    GrafoPedagogico,
    MicroHabilidade,
)
from agente_ia_edu.services.sinal_diagnostico import (
    SEM_SINAL,
    SUSPEITA_DE_LACUNA,
    alvo_sugerido,
    sinais_de_sondagem,
)

MATH = GrafoPedagogico(
    conteudo="MATH-EQ1",
    habilidades=[
        MicroHabilidade(code="OPS", label="Operações", objetivo=""),
        MicroHabilidade(code="INV", label="Inversas", objetivo="",
                        prerequisitos=("OPS",)),
        MicroHabilidade(code="SOLVE", label="Resolver", objetivo="",
                        prerequisitos=("INV",)),
    ],
)


def _medido(**pares) -> dict:
    """Saída de `diagnostico_por_habilidade`: {skill: (correct, answered)}."""
    return {"por_habilidade": {
        s: {"correct": c, "answered": a} for s, (c, a) in pares.items()}}


class UmErroBastaParaSUSPEITAR(unittest.TestCase):

    def test_um_item_errado_vira_suspeita(self):
        sinais = sinais_de_sondagem(_medido(OPS=(0, 1)))
        self.assertEqual(SUSPEITA_DE_LACUNA, sinais["OPS"])

    def test_dois_de_tres_errados_tambem(self):
        sinais = sinais_de_sondagem(_medido(OPS=(1, 3)))
        self.assertEqual(SUSPEITA_DE_LACUNA, sinais["OPS"])

    def test_a_suspeita_nao_depende_de_quantas_foram(self):
        """Uma errada de uma é tão suspeita quanto uma errada de duas."""
        self.assertEqual(sinais_de_sondagem(_medido(OPS=(0, 1)))["OPS"],
                         sinais_de_sondagem(_medido(OPS=(1, 2)))["OPS"])


class UmAcertoNAOBastaParaCONFIRMAR(unittest.TestCase):
    """A metade assimétrica. Com quatro alternativas, o chute acerta uma vez
    em quatro — e um acerto não distingue quem sabe de quem chutou."""

    def test_um_item_certo_nao_vira_confirmacao(self):
        sinais = sinais_de_sondagem(_medido(OPS=(1, 1)))
        self.assertEqual(SEM_SINAL, sinais["OPS"])

    def test_nenhum_valor_do_modulo_significa_dominio(self):
        """Se existisse um CONFIRMADO aqui, alguém o leria como mastery."""
        import agente_ia_edu.services.sinal_diagnostico as mod

        publicos = [v for k, v in vars(mod).items()
                    if k.isupper() and isinstance(v, str)]
        for valor in publicos:
            with self.subTest(valor=valor):
                for proibido in ("CONFIRM", "MASTER", "DOMIN"):
                    self.assertNotIn(proibido, valor.upper())

    def test_tudo_certo_com_uma_amostra_nao_produz_sinal_nenhum(self):
        sinais = sinais_de_sondagem(_medido(OPS=(1, 1), INV=(1, 1), SOLVE=(1, 1)))
        self.assertEqual({SEM_SINAL}, set(sinais.values()))


class OALVOSEGUEOGRAFO(unittest.TestCase):

    def test_so_massa_molar_errada_e_ela_o_alvo(self):
        alvo = alvo_sugerido(MATH, _medido(OPS=(1, 1), INV=(0, 1)))
        self.assertEqual("INV", alvo)

    def test_as_duas_erradas_comecam_pela_base(self):
        alvo = alvo_sugerido(MATH, _medido(OPS=(0, 1), INV=(0, 1)))
        self.assertEqual("OPS", alvo)

    def test_a_cadeia_inteira_errada_comeca_na_base(self):
        alvo = alvo_sugerido(MATH,
                             _medido(OPS=(0, 1), INV=(0, 1), SOLVE=(0, 1)))
        self.assertEqual("OPS", alvo)

    def test_sem_erro_nenhum_nao_ha_alvo(self):
        self.assertIsNone(alvo_sugerido(MATH, _medido(OPS=(1, 1))))

    def test_sem_medida_nenhuma_nao_ha_alvo(self):
        self.assertIsNone(alvo_sugerido(MATH, {}))
        self.assertIsNone(alvo_sugerido(MATH, None))

    def test_habilidade_fora_do_grafo_nao_vira_alvo(self):
        """O grafo não sabe interpretá-la, e quem chamou decide o que fazer."""
        self.assertIsNone(alvo_sugerido(MATH, _medido(FANTASMA=(0, 1))))

    def test_mistura_de_conhecida_e_desconhecida_escolhe_a_conhecida(self):
        alvo = alvo_sugerido(MATH, _medido(FANTASMA=(0, 1), INV=(0, 1)))
        self.assertEqual("INV", alvo)

    def test_o_alvo_e_estavel(self):
        dados = _medido(OPS=(0, 1), INV=(0, 1))
        self.assertEqual(alvo_sugerido(MATH, dados),
                         alvo_sugerido(MATH, dados))


class OSINALNAOENCOSTANODOMINIO(unittest.TestCase):
    """A metade que impede a assimetria de virar um atalho para mastery."""

    FONTE = (pathlib.Path(__file__).resolve().parent.parent
             / "src/agente_ia_edu/services/sinal_diagnostico.py")

    def test_o_modulo_nao_toca_banco_nem_provedor(self):
        baixo = self.FONTE.read_text(encoding="utf-8").lower()
        for proibido in ("asyncsession", "sqlalchemy", "db.models",
                         "provider", "openai"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, baixo)

    def test_o_modulo_nao_escreve_numero_de_corte(self):
        """Um número aqui seria uma segunda política de evidência."""
        for no in ast.walk(ast.parse(self.FONTE.read_text(encoding="utf-8"))):
            if isinstance(no, ast.Constant) and isinstance(no.value, float):
                self.fail(f"literal float no sinal diagnostico: {no.value}")

    def test_o_modulo_nao_importa_a_politica_de_dominio(self):
        """Se ele importasse a política, alguém acabaria usando as duas
        juntas — e a separação viraria convenção.

        A verificação é pela AST, e não por texto: a docstring do módulo
        CITA a política para explicar por que não a importa, e procurar o
        nome no arquivo acusaria justamente a explicação."""
        arvore = ast.parse(self.FONTE.read_text(encoding="utf-8"))
        importados: list[str] = []
        for no in ast.walk(arvore):
            if isinstance(no, ast.ImportFrom):
                importados.append(no.module or "")
                importados += [a.name for a in no.names]
            elif isinstance(no, ast.Import):
                importados += [a.name for a in no.names]
        for proibido in ("PerformanceThresholdPolicy", "pedagogical_analysis",
                         "curriculum_domain_map"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, importados)

    def test_a_banda_do_dominio_continua_insuficiente(self):
        """O sinal existe AO LADO da política, não no lugar dela."""
        from agente_ia_edu.services.diagnostico_por_habilidade import (
            diagnostico_por_habilidade,
        )
        from agente_ia_edu.services.pedagogical_analysis import BAND_INSUFFICIENT

        d = diagnostico_por_habilidade([
            {"diagnostic_skill": "MASSA_MOLAR", "is_correct": False}])
        self.assertEqual(BAND_INSUFFICIENT,
                         d["por_habilidade"]["MASSA_MOLAR"]["band"])

    def test_o_minimo_de_amostra_nao_mudou(self):
        from agente_ia_edu.services.pedagogical_analysis import (
            PerformanceThresholdPolicy,
        )

        self.assertEqual(3, PerformanceThresholdPolicy.default().min_sample_size)


class ONOMENAOACUSAOALUNO(unittest.TestCase):
    """"Você não domina massa molar" afirma mais do que se sabe."""

    def test_o_valor_fala_de_suspeita_e_nao_de_falta(self):
        """O código canônico é inglês, como os outros do sistema. O que
        importa é que ele diga SUSPEITA, e não ausência de domínio."""
        self.assertIn("SUSPECT", SUSPEITA_DE_LACUNA.upper())
        for acusatorio in ("NOT_MASTERED", "FAILED", "CANNOT", "UNKNOWN"):
            self.assertNotEqual(acusatorio, SUSPEITA_DE_LACUNA.upper())


if __name__ == "__main__":
    unittest.main()
