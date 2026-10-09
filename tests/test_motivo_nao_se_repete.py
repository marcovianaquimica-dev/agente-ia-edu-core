"""O CONTEÚDO NÃO É A BASE DE SI MESMO.

MEDIDO NO NAVEGADOR, EM 2026-10-07
===================================
Aluno QA, passo de ENSINO de Estequiometria. A tela dizia:

    "Pelas suas respostas, massa molar ainda está travando.
     Estequiometria e cálculos químicos é a base de
     Estequiometria e cálculos químicos — vale firmar isso antes."

A frase se repete porque, quando a atividade exige o MESMO conteúdo que está
travando, `objetivo_nome` e `conteudo_nome` chegam iguais. Não é um erro de
dado: é o caso normal de uma atividade cujo próprio conteúdo precisa de
preparação. O `aluno.js` já tinha o guarda (`para && para !== alvo`); o
backend, que é a autoridade sobre a frase, não.

E é o backend que importa: a regra deste projeto é que a voz pedagógica vem
de lá, e o texto local existe só como queda.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.assessor_pedagogico import decidir_intervencao
from agente_ia_edu.services.pedagogical_analysis import BAND_IMPROVEMENT

HABILIDADES = {"por_habilidade": {
    "MASSA_MOLAR": {"band": BAND_IMPROVEMENT, "answered": 4, "correct": 1,
                    "accuracy": 0.25, "name": "massa molar"}}}


def _motivo(objetivo: str | None, conteudo: str | None) -> str:
    return decidir_intervencao(
        habilidades=HABILIDADES,
        banda_do_conteudo=BAND_IMPROVEMENT,
        ja_ensinado=False, praticas_concluidas=0,
        ha_material=True, tentativas=[{"answered": 4, "correct": 1}],
        objetivo_nome=objetivo, conteudo_nome=conteudo)["reason"]


class OMESMONOMENAOAPARECEDUASVEZES(unittest.TestCase):

    MESMO = "Estequiometria e cálculos químicos"

    def test_conteudo_igual_ao_objetivo_nao_vira_base_de_si_mesmo(self):
        """A regra é NUNCA DUAS VEZES, e não "exatamente uma".

        Escrevi `== 1` primeiro e o teste reprovou a correção certa: a frase
        curta não cita o conteúdo nenhuma vez, porque o que o aluno precisa
        saber é a HABILIDADE que travou — "massa molar", não o nome do
        capítulo. Zero menções está certo; duas é que é o defeito.
        """
        motivo = _motivo(self.MESMO, self.MESMO)
        self.assertLessEqual(
            motivo.count(self.MESMO), 1,
            f"o nome aparece {motivo.count(self.MESMO)} vezes: {motivo!r}")

    def test_e_a_frase_nao_diz_base_de(self):
        motivo = _motivo(self.MESMO, self.MESMO)
        self.assertNotIn("é a base de", motivo)

    def test_a_frase_continua_dizendo_o_que_trava(self):
        """Tirar a repetição não pode tirar a informação."""
        self.assertIn("massa molar", _motivo(self.MESMO, self.MESMO))

    def test_a_frase_continua_convidando_a_firmar(self):
        self.assertIn("firmar", _motivo(self.MESMO, self.MESMO))


class QUANDOSAODIFERENTESAPONTEPERMANECE(unittest.TestCase):
    """O caso que a frase existe para servir: o aluno respondendo sobre um
    conteúdo que ninguém pediu merece saber PARA QUE aquilo serve."""

    def test_objetivo_diferente_aparece(self):
        motivo = _motivo("Estequiometria", "Balanceamento de equações")
        self.assertIn("Estequiometria", motivo)
        self.assertIn("Balanceamento de equações", motivo)
        self.assertIn("é a base de", motivo)

    def test_sem_objetivo_a_frase_e_a_curta(self):
        motivo = _motivo(None, "Balanceamento de equações")
        self.assertNotIn("é a base de", motivo)
        self.assertIn("firmar", motivo)

    def test_sem_nome_nenhum_a_frase_ainda_existe(self):
        self.assertTrue(_motivo(None, None).strip())


class AMESMAREGRAVALEPARAOFEEDBACK(unittest.TestCase):
    """`feedback_do_passo` monta a mesma ponte, e com o mesmo risco."""

    MESMO = "Estequiometria e cálculos químicos"

    def _detalhe(self, action, objetivo, conteudo):
        from agente_ia_edu.services.feedback_pedagogico import feedback_do_passo
        from agente_ia_edu.services.trajetoria_do_aluno import (
            TENDENCIA_CONFIRMADA,
        )
        fb = feedback_do_passo(action=action, trend=TENDENCIA_CONFIRMADA,
                               cycle=1, skill_name="massa molar",
                               content_name=conteudo, objective_name=objetivo)
        return f"{fb['titulo']} {fb['detalhe']}"

    def test_o_avanco_nao_manda_avancar_para_onde_ele_ja_esta(self):
        texto = self._detalhe(None, self.MESMO, self.MESMO)
        self.assertNotIn(f"avançar para {self.MESMO}", texto)


if __name__ == "__main__":
    unittest.main()
