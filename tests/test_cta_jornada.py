"""O CTA sai do ESTADO, não de um mapa fixo por tipo de passo.

O BUG QUE ORIGINOU ESTE ARQUIVO
================================
O aluno acertava as três perguntas do diagnóstico de Balanceamento, a tela
dizia "DIAGNÓSTICO CONCLUÍDO — você demonstrou bom domínio", e o botão
continuava escrito **"Responder"**.

Não era um texto errado. Era o rótulo vindo de um dicionário

    DIAGNOSTIC -> "Responder"
    PRACTICE   -> "Revisar agora"
    ACTIVITY   -> "Começar"

que mapeia o TIPO do próximo passo e ignora se a etapa já foi iniciada,
está em andamento ou terminou. Trocar a string resolveria aquela tela e
deixaria as outras seis combinações erradas.

A regra agora é uma matriz (tipo × estado), no backend, onde o estado
pedagógico já mora. O frontend traduz; não decide.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.proximo_passo import (
    ESTADO_CONCLUIDO,
    ESTADO_EM_ANDAMENTO,
    ESTADO_NAO_INICIADO,
    PASSO_ATIVIDADE,
    PASSO_DIAGNOSTICO,
    PASSO_NENHUM,
    PASSO_PRATICA,
    cta_para,
)


class MatrizDeCTATests(unittest.TestCase):
    """As oito combinações que o produto precisa distinguir."""

    def test_diagnostico_nao_iniciado(self):
        self.assertEqual(cta_para(PASSO_DIAGNOSTICO, ESTADO_NAO_INICIADO),
                         "Responder diagnóstico")

    def test_diagnostico_em_andamento(self):
        self.assertEqual(cta_para(PASSO_DIAGNOSTICO, ESTADO_EM_ANDAMENTO),
                         "Continuar diagnóstico")

    def test_pratica_nao_iniciada(self):
        self.assertEqual(cta_para(PASSO_PRATICA, ESTADO_NAO_INICIADO),
                         "Praticar agora")

    def test_pratica_em_andamento(self):
        self.assertEqual(cta_para(PASSO_PRATICA, ESTADO_EM_ANDAMENTO),
                         "Continuar prática")

    def test_atividade_disponivel(self):
        self.assertEqual(cta_para(PASSO_ATIVIDADE, ESTADO_NAO_INICIADO),
                         "Começar atividade")

    def test_atividade_em_andamento(self):
        self.assertEqual(cta_para(PASSO_ATIVIDADE, ESTADO_EM_ANDAMENTO),
                         "Continuar atividade")

    def test_atividade_concluida(self):
        self.assertEqual(cta_para(PASSO_ATIVIDADE, ESTADO_CONCLUIDO),
                         "Ver resultado")

    def test_sem_passo_nao_tem_CTA(self):
        """Botão sem destino é pior que nenhum botão."""
        self.assertIsNone(cta_para(PASSO_NENHUM, ESTADO_NAO_INICIADO))


class ORegressaoDoBugTests(unittest.TestCase):

    def test_diagnostico_concluido_NUNCA_diz_Responder(self):
        """O bug literal: depois de concluído, o CTA não pode convidar a
        responder de novo o que acabou de ser respondido."""
        for kind in (PASSO_DIAGNOSTICO, PASSO_PRATICA):
            with self.subTest(kind=kind):
                rotulo = cta_para(kind, ESTADO_CONCLUIDO) or ""
                self.assertNotIn("Responder", rotulo)
                self.assertNotIn("Praticar agora", rotulo)

    def test_uma_etapa_concluida_convida_a_CONTINUAR(self):
        """Quando a etapa terminou, o aluno segue - não repete."""
        self.assertEqual(cta_para(PASSO_DIAGNOSTICO, ESTADO_CONCLUIDO), "Continuar")
        self.assertEqual(cta_para(PASSO_PRATICA, ESTADO_CONCLUIDO), "Continuar")

    def test_nenhum_CTA_e_ambiguo_entre_estados(self):
        """Dois estados diferentes do mesmo passo não podem dizer a mesma
        coisa: se dissessem, o aluno não saberia se está começando ou
        retomando."""
        for kind in (PASSO_DIAGNOSTICO, PASSO_PRATICA, PASSO_ATIVIDADE):
            rotulos = [cta_para(kind, e) for e in
                       (ESTADO_NAO_INICIADO, ESTADO_EM_ANDAMENTO, ESTADO_CONCLUIDO)]
            with self.subTest(kind=kind):
                self.assertEqual(len(set(rotulos)), 3,
                                 f"{kind}: rótulos repetidos entre estados: {rotulos}")

    def test_estado_desconhecido_cai_no_inicio_sem_explodir(self):
        """Fail-safe: um estado que esta matriz não conhece não pode deixar a
        tela sem botão nenhum."""
        self.assertTrue(cta_para(PASSO_DIAGNOSTICO, "COISA_NOVA"))


class VocabularioTests(unittest.TestCase):

    def test_os_estados_sao_os_do_player(self):
        """NÃO são um vocabulário novo: são os mesmos que `ActivityAttempt`
        usa. Uma segunda lista de estados divergiria da primeira."""
        from agente_ia_edu.services.activity_player_store import (
            STATUS_COMPLETED, STATUS_IN_PROGRESS,
        )

        self.assertEqual(ESTADO_EM_ANDAMENTO, STATUS_IN_PROGRESS)
        self.assertEqual(ESTADO_CONCLUIDO, STATUS_COMPLETED)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
