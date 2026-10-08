"""O PASSO DE INVESTIGAÇÃO NO CONTRATO — e o CTA que vem do backend.

Teste J do bloco: quando o motor já conhece a próxima ação pedagógica, o
botão que o aluno vê tem de ser o dela. "Revisar questões" num momento em que
o sistema sabe que o próximo passo é olhar a proporção estequiométrica é o
produto escondendo do aluno a decisão que ele mesmo tomou.

A regra que este arquivo guarda é a de sempre, aplicada a um passo novo:
quem decide é o backend, e a UI apenas apresenta. Um `kind` sem CTA
correspondente deixaria a tela montar o texto no JavaScript — que é
exatamente de onde o feedback do diagnóstico já teve de ser retirado uma vez.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.proximo_passo import (
    ESTADO_CONCLUIDO,
    ESTADO_EM_ANDAMENTO,
    ESTADO_NAO_INICIADO,
    PASSO_ENSINO,
    PASSO_ESCALONAMENTO,
    PASSO_GUIADA,
    PASSO_INVESTIGACAO,
    PASSO_PRATICA,
    PASSO_VERIFICACAO,
    ETAPA_ATIVIDADE,
    ETAPA_PREPARACAO,
    cta_para,
    jornada_de,
)
from agente_ia_edu.services.readiness_route import ROTA_PREPARACAO

TODOS_OS_PASSOS_DE_APOIO = (PASSO_INVESTIGACAO, PASSO_ENSINO, PASSO_GUIADA,
                            PASSO_PRATICA, PASSO_VERIFICACAO,
                            PASSO_ESCALONAMENTO)


class OPASSOEXISTENOCONTRATO(unittest.TestCase):

    def test_o_codigo_e_estavel_e_em_ingles_como_os_outros(self):
        self.assertEqual("INVESTIGATE", PASSO_INVESTIGACAO)


class OCTAVEMDOBACKEND(unittest.TestCase):

    def test_ha_cta_para_cada_estado(self):
        for estado in (ESTADO_NAO_INICIADO, ESTADO_EM_ANDAMENTO,
                       ESTADO_CONCLUIDO):
            with self.subTest(estado=estado):
                self.assertTrue((cta_para(PASSO_INVESTIGACAO, estado) or "").strip())

    def test_o_cta_nao_promete_questoes(self):
        """A investigação não é um lote de exercícios, e o verbo não pode
        sugerir isso — o aluno clica esperando outra coisa."""
        for estado in (ESTADO_NAO_INICIADO, ESTADO_EM_ANDAMENTO):
            texto = (cta_para(PASSO_INVESTIGACAO, estado) or "").lower()
            with self.subTest(estado=estado):
                for proibido in ("praticar", "questões", "questoes",
                                 "exercício", "exercicio"):
                    self.assertNotIn(proibido, texto)

    def test_o_cta_do_fim_anuncia_o_degrau_seguinte(self):
        """Concluir a investigação não é concluir o conteúdo."""
        self.assertTrue(
            (cta_para(PASSO_INVESTIGACAO, ESTADO_CONCLUIDO) or "").strip())

    def test_nenhum_passo_de_apoio_fica_sem_cta(self):
        """Se um ficar, a tela monta o texto sozinha — e já aconteceu."""
        for kind in TODOS_OS_PASSOS_DE_APOIO:
            for estado in (ESTADO_NAO_INICIADO, ESTADO_EM_ANDAMENTO,
                           ESTADO_CONCLUIDO):
                with self.subTest(kind=kind, estado=estado):
                    self.assertTrue((cta_para(kind, estado) or "").strip())


class AJORNADAACENDEPREPARACAO(unittest.TestCase):
    """Investigar é preparação, como ensinar e praticar.

    Sem esta linha o passo cairia no ramo "desconhecido" e a barra acenderia
    ATIVIDADE — exatamente o bug medido em 2026-10-05 com VERIFY e ESCALATE.
    """

    def _etapa_ativa(self, kind: str) -> str:
        etapas = jornada_de(origens={}, estado_atividade=ESTADO_NAO_INICIADO,
                            rota=ROTA_PREPARACAO, kind=kind)
        return next(e["id"] for e in etapas
                    if e["state"] == ESTADO_EM_ANDAMENTO)

    def test_a_etapa_ativa_e_a_preparacao(self):
        self.assertEqual(ETAPA_PREPARACAO,
                         self._etapa_ativa(PASSO_INVESTIGACAO))

    def test_investigar_acende_a_mesma_etapa_que_ensinar(self):
        self.assertEqual(self._etapa_ativa(PASSO_ENSINO),
                         self._etapa_ativa(PASSO_INVESTIGACAO))

    def test_e_nao_acende_a_atividade(self):
        self.assertNotEqual(ETAPA_ATIVIDADE,
                            self._etapa_ativa(PASSO_INVESTIGACAO))


class OFEEDBACKDOPASSOFALAPELOBACKEND(unittest.TestCase):
    """Teste I/J: a tela não monta a frase da investigação."""

    def test_ha_titulo_e_detalhe_para_investigar(self):
        from agente_ia_edu.services.assessor_pedagogico import ACAO_INVESTIGAR
        from agente_ia_edu.services.feedback_pedagogico import feedback_do_passo
        from agente_ia_edu.services.trajetoria_do_aluno import TENDENCIA_INDEFINIDA

        fb = feedback_do_passo(action=ACAO_INVESTIGAR,
                               trend=TENDENCIA_INDEFINIDA, cycle=1,
                               skill_name="Massa molar",
                               content_name="Estequiometria")
        self.assertTrue(fb["titulo"].strip())
        self.assertTrue(fb["detalhe"].strip())

    def test_a_frase_nao_acusa_o_aluno(self):
        from agente_ia_edu.services.assessor_pedagogico import ACAO_INVESTIGAR
        from agente_ia_edu.services.feedback_pedagogico import feedback_do_passo
        from agente_ia_edu.services.trajetoria_do_aluno import TENDENCIA_INDEFINIDA

        fb = feedback_do_passo(action=ACAO_INVESTIGAR,
                               trend=TENDENCIA_INDEFINIDA, cycle=1,
                               skill_name="Massa molar",
                               content_name="Estequiometria")
        junto = f"{fb['titulo']} {fb['detalhe']}".lower()
        for proibido in ("você errou", "você não domina", "você falhou",
                         "insuficiente", "deficiência", "fraco"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, junto)

    def test_a_frase_nomeia_a_habilidade_quando_ela_e_conhecida(self):
        from agente_ia_edu.services.assessor_pedagogico import ACAO_INVESTIGAR
        from agente_ia_edu.services.feedback_pedagogico import feedback_do_passo
        from agente_ia_edu.services.trajetoria_do_aluno import TENDENCIA_INDEFINIDA

        fb = feedback_do_passo(action=ACAO_INVESTIGAR,
                               trend=TENDENCIA_INDEFINIDA, cycle=1,
                               skill_name="Massa molar",
                               content_name="Estequiometria")
        self.assertIn("massa molar",
                      f"{fb['titulo']} {fb['detalhe']}".lower())


if __name__ == "__main__":
    unittest.main()
