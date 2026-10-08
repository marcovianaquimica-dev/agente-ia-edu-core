"""A prática guiada entra ENTRE o exemplo e a prática autônoma.

    LEARN  ->  GUIDED  ->  PRACTICE

E entra como mais uma linha da MESMA matriz (tipo × estado), não como um caso
especial costurado na tela — a mesma disciplina que já manteve as nove células
anteriores intactas quando LEARN chegou.

ONDE ELA APARECE NA JORNADA
============================
Dentro de "Preparação", junto com estudar e praticar. A barra principal
continua com quatro etapas: uma quinta contaria ao aluno um detalhe de
implementação.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.proximo_passo import (
    ESTADO_CONCLUIDO,
    ESTADO_EM_ANDAMENTO,
    ESTADO_NAO_INICIADO,
    ETAPA_ATIVIDADE,
    ETAPA_DIAGNOSTICO,
    ETAPA_PREPARACAO,
    ETAPA_RESULTADO,
    PASSO_ATIVIDADE,
    PASSO_DIAGNOSTICO,
    PASSO_ENSINO,
    PASSO_GUIADA,
    PASSO_PRATICA,
    cta_para,
    jornada_de,
)

ESTADOS = (ESTADO_NAO_INICIADO, ESTADO_EM_ANDAMENTO, ESTADO_CONCLUIDO)


class OPassoGuiadoTemCTAProprioTests(unittest.TestCase):

    def test_os_tres_estados_tem_rotulo(self):
        for estado in ESTADOS:
            with self.subTest(estado=estado):
                self.assertTrue((cta_para(PASSO_GUIADA, estado) or "").strip())

    def test_os_tres_rotulos_sao_diferentes(self):
        rotulos = [cta_para(PASSO_GUIADA, e) for e in ESTADOS]
        self.assertEqual(len(set(rotulos)), 3, rotulos)

    def test_quem_nao_comecou_e_convidado_a_TENTAR(self):
        """O verbo importa: a prática guiada não é "ver" nem "estudar" — o
        aluno tenta primeiro, e a ajuda vem depois."""
        self.assertIn("entar", cta_para(PASSO_GUIADA, ESTADO_NAO_INICIADO))

    def test_concluida_o_rotulo_anuncia_a_tentativa_SOZINHO(self):
        """É a diferença que o bloco inteiro existe para marcar."""
        self.assertIn("sozinho", cta_para(PASSO_GUIADA, ESTADO_CONCLUIDO))


class ElaCabeDENTRODaPreparacaoTests(unittest.TestCase):

    def test_o_passo_guiado_acende_PREPARACAO(self):
        j = jornada_de(origens={"MICRO_DIAGNOSTIC": 3},
                       estado_atividade=ESTADO_NAO_INICIADO,
                       rota="PREREQUISITE_PREPARATION", kind=PASSO_GUIADA)
        atuais = [e["id"] for e in j if e["state"] == ESTADO_EM_ANDAMENTO]
        self.assertEqual(atuais, [ETAPA_PREPARACAO])

    def test_a_jornada_continua_com_quatro_etapas(self):
        j = jornada_de(origens={"MICRO_DIAGNOSTIC": 3, "PRACTICE": 2},
                       estado_atividade=ESTADO_NAO_INICIADO,
                       rota="PREREQUISITE_PREPARATION", kind=PASSO_GUIADA)
        self.assertEqual([e["id"] for e in j],
                         [ETAPA_DIAGNOSTICO, ETAPA_PREPARACAO,
                          ETAPA_ATIVIDADE, ETAPA_RESULTADO])


class AMatrizAnteriorNaoMudouTests(unittest.TestCase):
    """Regressão: doze células já validadas continuam idênticas."""

    ESPERADO = {
        (PASSO_DIAGNOSTICO, ESTADO_NAO_INICIADO): "Responder diagnóstico",
        (PASSO_DIAGNOSTICO, ESTADO_EM_ANDAMENTO): "Continuar diagnóstico",
        (PASSO_DIAGNOSTICO, ESTADO_CONCLUIDO): "Continuar",
        (PASSO_ENSINO, ESTADO_NAO_INICIADO): "Entender o conceito",
        (PASSO_ENSINO, ESTADO_EM_ANDAMENTO): "Continuar estudando",
        (PASSO_PRATICA, ESTADO_NAO_INICIADO): "Praticar agora",
        (PASSO_PRATICA, ESTADO_EM_ANDAMENTO): "Continuar prática",
        (PASSO_PRATICA, ESTADO_CONCLUIDO): "Continuar",
        (PASSO_ATIVIDADE, ESTADO_NAO_INICIADO): "Começar atividade",
        (PASSO_ATIVIDADE, ESTADO_EM_ANDAMENTO): "Continuar atividade",
        (PASSO_ATIVIDADE, ESTADO_CONCLUIDO): "Ver resultado",
    }

    def test_as_celulas_antigas_continuam_identicas(self):
        for (kind, estado), rotulo in self.ESPERADO.items():
            with self.subTest(kind=kind, estado=estado):
                self.assertEqual(cta_para(kind, estado), rotulo)

    def test_o_fim_do_ensino_agora_leva_a_TENTAR_e_nao_a_praticar_sozinho(self):
        """Com a prática guiada no meio, "Praticar agora" depois da
        explicação pularia a etapa que o bloco acabou de criar."""
        self.assertIn("entar", cta_para(PASSO_ENSINO, ESTADO_CONCLUIDO))

    def test_um_tipo_desconhecido_continua_sem_botao(self):
        self.assertIsNone(cta_para("NAO_EXISTE", ESTADO_NAO_INICIADO))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
