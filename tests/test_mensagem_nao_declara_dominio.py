"""A MENSAGEM AO ALUNO SEGUE A DECISÃO MAIS ESPECÍFICA QUE EXISTE.

O QUE SE MEDIU NO NAVEGADOR, EM 2026-10-07
===========================================
Aluno QA respondeu a sondagem de Estequiometria: acertou leitura de fórmula,
acertou proporção, **errou massa molar**. A tela disse:

    "Muito bem!
     Você demonstrou um bom domínio de Estequiometria e cálculos químicos."

E o botão logo abaixo levava a investigar massa molar.

AS DUAS FRASES ESTÃO CERTAS, CADA UMA SOB A SUA REGRA
======================================================
A política concluiu `PROCEED`: 2 de 3, acurácia 0,667, acima do corte de
melhoria. É uma decisão OPERACIONAL — "ele pode começar a atividade" — e
está correta.

A assimetria diagnóstica concluiu `SUSPECTED_GAP` em massa molar: um erro
basta para suspeitar. Também está correta.

O defeito é a COMUNICAÇÃO: "bom domínio do conteúdo" dito a quem tem um
gargalo identificado naquele mesmo conteúdo. O aluno lê que domina e, no
clique seguinte, é levado a estudar.

A CORREÇÃO, E O QUE ELA NÃO FAZ
================================
Não mexe em `PerformanceThresholdPolicy`. Não mexe em `min_sample_size`.
Não elimina `PROCEED`. A decisão operacional continua sendo a mesma.

O que muda é só a frase: havendo alvo de intervenção, ela nomeia o que foi
observado em vez de declarar domínio genérico. Ausência de bloqueio não é
declaração de mastery.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.feedback_pedagogico import feedback_do_diagnostico
from agente_ia_edu.services.micro_diagnostic import (
    DECISION_INSUFFICIENT,
    DECISION_PREPARE,
    DECISION_PROCEED,
)
from agente_ia_edu.services.pedagogical_analysis import (
    BAND_IMPROVEMENT,
    BAND_INSUFFICIENT,
    BAND_STRONG,
)

CONTEUDO = "Estequiometria e cálculos químicos"

# Palavras que AFIRMAM conhecimento consolidado. Nenhuma pode aparecer
# quando há um gargalo identificado no mesmo conteúdo.
DECLARACOES_DE_DOMINIO = ("bom domínio", "você domina", "dominou",
                          "consolidado", "você já sabe")


def _fala(decision, band, alvo_nome=None):
    fb = feedback_do_diagnostico(decision=decision, band=band,
                                 content_name=CONTEUDO, alvo_nome=alvo_nome)
    return f"{fb['titulo']} {fb['detalhe']}"


class COMGARGALONAODECLARADOMINIO(unittest.TestCase):
    """O caso medido: PROCEED global + gargalo local."""

    def test_nao_diz_bom_dominio_quando_ha_alvo(self):
        texto = _fala(DECISION_PROCEED, BAND_STRONG, alvo_nome="massa molar")
        for frase in DECLARACOES_DE_DOMINIO:
            with self.subTest(frase=frase):
                self.assertNotIn(frase, texto.lower())

    def test_e_nomeia_o_ponto_encontrado(self):
        texto = _fala(DECISION_PROCEED, BAND_STRONG, alvo_nome="massa molar")
        self.assertIn("massa molar", texto.lower())

    def test_a_frase_reconhece_o_que_ele_acertou(self):
        """Encontrar um gargalo não apaga o resto. O aluno acertou duas de
        três, e a frase que ignora isso é tão imprecisa quanto a que
        declarava domínio."""
        texto = _fala(DECISION_PROCEED, BAND_STRONG,
                      alvo_nome="massa molar").lower()
        self.assertTrue(any(p in texto for p in ("já", "bem", "consegue")),
                        texto)

    def test_a_frase_nao_acusa(self):
        texto = _fala(DECISION_PROCEED, BAND_STRONG,
                      alvo_nome="massa molar").lower()
        for proibido in ("você errou", "você falhou", "deficiência",
                         "insuficiente", "fraco", "não domina"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, texto)

    def test_a_frase_nao_usa_jargao_do_sistema(self):
        texto = _fala(DECISION_PROCEED, BAND_STRONG,
                      alvo_nome="massa molar").lower()
        for jargao in ("suspected_gap", "band", "readiness", "proceed",
                       "mastery", "micro-habilidade", "threshold"):
            with self.subTest(jargao=jargao):
                self.assertNotIn(jargao, texto)


class SEMGARGALOACOMUNICACAOPOSITIVACONTINUA(unittest.TestCase):
    """Não transformar toda mensagem em ressalva.

    Quando não há alvo e a evidência sustenta, dizer que foi bem é verdade —
    e esconder isso seria o erro oposto.
    """

    def test_sem_alvo_a_frase_de_proceed_continua_a_de_antes(self):
        texto = _fala(DECISION_PROCEED, BAND_STRONG).lower()
        self.assertIn("bom domínio", texto)

    def test_sem_alvo_o_tom_e_positivo(self):
        fb = feedback_do_diagnostico(decision=DECISION_PROCEED,
                                     band=BAND_STRONG, content_name=CONTEUDO)
        self.assertEqual("BOM", fb["tom"])

    def test_com_alvo_o_tom_deixa_de_ser_celebracao(self):
        fb = feedback_do_diagnostico(decision=DECISION_PROCEED,
                                     band=BAND_STRONG, content_name=CONTEUDO,
                                     alvo_nome="massa molar")
        self.assertNotEqual("BOM", fb["tom"])


class ASOUTRASDECISOESNAOMUDAM(unittest.TestCase):
    """O alvo informa a frase de PROCEED. As outras já falavam certo."""

    def test_prepare_continua_convidando_a_revisar(self):
        texto = _fala(DECISION_PREPARE, BAND_IMPROVEMENT,
                      alvo_nome="massa molar").lower()
        self.assertIn("revisar", texto)

    def test_prepare_sem_alvo_tambem(self):
        self.assertIn("revisar", _fala(DECISION_PREPARE,
                                       BAND_IMPROVEMENT).lower())

    def test_insufficient_continua_admitindo_que_nao_da_para_concluir(self):
        texto = _fala(DECISION_INSUFFICIENT, BAND_INSUFFICIENT).lower()
        self.assertIn("não dá para concluir", texto)

    def test_decisao_desconhecida_nao_elogia_nem_acusa(self):
        texto = _fala("DECISAO_QUE_NAO_EXISTE", BAND_STRONG).lower()
        for frase in DECLARACOES_DE_DOMINIO:
            with self.subTest(frase=frase):
                self.assertNotIn(frase, texto)


class APOLITICANAOFOITOCADA(unittest.TestCase):
    """§18: corrigir a frase, não os cortes."""

    def test_o_minimo_de_amostra_continua_o_mesmo(self):
        from agente_ia_edu.services.pedagogical_analysis import (
            PerformanceThresholdPolicy,
        )
        self.assertEqual(3, PerformanceThresholdPolicy.default().min_sample_size)

    def test_proceed_continua_existindo_como_decisao(self):
        from agente_ia_edu.services.micro_diagnostic import MicroDiagnosticService
        d = MicroDiagnosticService.decidir(
            MicroDiagnosticService(session=None), answered=3, accuracy=0.667)
        self.assertEqual(DECISION_PROCEED, d["decision"])

    def test_a_banda_de_2_de_3_continua_a_mesma(self):
        from agente_ia_edu.services.pedagogical_analysis import (
            PerformanceThresholdPolicy,
        )
        banda = PerformanceThresholdPolicy.default().band(answered=3,
                                                          accuracy=0.667)
        self.assertNotIn(banda, (BAND_INSUFFICIENT,))


if __name__ == "__main__":
    unittest.main()
