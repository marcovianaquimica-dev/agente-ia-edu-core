"""A jornada que o aluno vê: Diagnóstico → Preparação → Atividade → Resultado.

A barra que existia mostrava QUESTÕES dentro de uma etapa — útil, mas não
responde "onde estou na jornada". Esta responde.

A REGRA QUE MAIS IMPORTA
=========================
Uma etapa só é "concluída" por **estado real**. Navegar até uma tela não
conclui nada: se bastasse abrir, o aluno que clicasse em tudo veria a jornada
inteira verde sem ter aprendido nada, e a barra viraria decoração.

Aqui "concluída" significa evidência registrada — `origin_breakdown` diz
quais origens já produziram evidência, e o estado da tentativa diz se a
atividade foi entregue.

PREPARAÇÃO NÃO APARECE SEMPRE
==============================
Quem não precisou praticar não deve ver uma etapa vazia no meio do caminho.
Ela entra quando o sistema a pediu (rota de preparação) ou quando já houve
prática — e, uma vez que entrou, não some, senão a jornada encolheria na
frente do aluno.
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
    jornada_de,
)
from agente_ia_edu.services.readiness_route import ROTA_PREPARACAO


def _nomes(j):
    return [e["id"] for e in j]


def _estado(j, qual):
    return next(e["state"] for e in j if e["id"] == qual)


class SemPreparacaoTests(unittest.TestCase):
    """Quem não precisou praticar vê três etapas, não quatro."""

    def test_no_comeco_so_o_diagnostico_esta_em_andamento(self):
        j = jornada_de(origens={}, estado_atividade=ESTADO_NAO_INICIADO,
                       rota="DIAGNOSTIC")
        self.assertEqual(_nomes(j), [ETAPA_DIAGNOSTICO, ETAPA_ATIVIDADE,
                                     ETAPA_RESULTADO])
        self.assertEqual(_estado(j, ETAPA_DIAGNOSTICO), ESTADO_EM_ANDAMENTO)
        self.assertEqual(_estado(j, ETAPA_ATIVIDADE), ESTADO_NAO_INICIADO)

    def test_diagnostico_respondido_fica_concluido(self):
        j = jornada_de(origens={"MICRO_DIAGNOSTIC": 3},
                       estado_atividade=ESTADO_NAO_INICIADO, rota="DIRECT")
        self.assertEqual(_estado(j, ETAPA_DIAGNOSTICO), ESTADO_CONCLUIDO)
        self.assertEqual(_estado(j, ETAPA_ATIVIDADE), ESTADO_EM_ANDAMENTO)

    def test_atividade_entregue_conclui_atividade_e_resultado(self):
        j = jornada_de(origens={"MICRO_DIAGNOSTIC": 3, "OFFICIAL_ACTIVITY": 5},
                       estado_atividade=ESTADO_CONCLUIDO, rota="DIRECT")
        self.assertEqual(_estado(j, ETAPA_ATIVIDADE), ESTADO_CONCLUIDO)
        self.assertEqual(_estado(j, ETAPA_RESULTADO), ESTADO_EM_ANDAMENTO)

    def test_atividade_comecada_aparece_em_andamento(self):
        j = jornada_de(origens={"MICRO_DIAGNOSTIC": 3},
                       estado_atividade=ESTADO_EM_ANDAMENTO, rota="DIRECT")
        self.assertEqual(_estado(j, ETAPA_ATIVIDADE), ESTADO_EM_ANDAMENTO)
        self.assertEqual(_estado(j, ETAPA_RESULTADO), ESTADO_NAO_INICIADO)


class ComPreparacaoTests(unittest.TestCase):

    def test_a_rota_de_preparacao_acrescenta_a_etapa(self):
        j = jornada_de(origens={"MICRO_DIAGNOSTIC": 3},
                       estado_atividade=ESTADO_NAO_INICIADO,
                       rota=ROTA_PREPARACAO)
        self.assertEqual(_nomes(j), [ETAPA_DIAGNOSTICO, ETAPA_PREPARACAO,
                                     ETAPA_ATIVIDADE, ETAPA_RESULTADO])
        self.assertEqual(_estado(j, ETAPA_PREPARACAO), ESTADO_EM_ANDAMENTO)

    def test_pratica_ja_feita_acrescenta_a_etapa_e_a_conclui(self):
        j = jornada_de(origens={"MICRO_DIAGNOSTIC": 3, "PRACTICE": 5},
                       estado_atividade=ESTADO_NAO_INICIADO, rota="DIRECT")
        self.assertIn(ETAPA_PREPARACAO, _nomes(j))
        self.assertEqual(_estado(j, ETAPA_PREPARACAO), ESTADO_CONCLUIDO)

    def test_a_etapa_de_preparacao_nao_some_depois_de_aparecer(self):
        """A jornada não pode encolher na frente do aluno."""
        j = jornada_de(origens={"MICRO_DIAGNOSTIC": 3, "PRACTICE": 5},
                       estado_atividade=ESTADO_CONCLUIDO, rota="DIRECT")
        self.assertIn(ETAPA_PREPARACAO, _nomes(j))


class NadaSeConcluiPorNavegacaoTests(unittest.TestCase):
    """A asserção central."""

    def test_sem_evidencia_nenhuma_etapa_esta_concluida(self):
        j = jornada_de(origens={}, estado_atividade=ESTADO_NAO_INICIADO,
                       rota="DIAGNOSTIC")
        concluidas = [e["id"] for e in j if e["state"] == ESTADO_CONCLUIDO]
        self.assertEqual(concluidas, [],
                         "etapa concluída sem evidência nenhuma")

    def test_origem_zerada_nao_conclui(self):
        """`{"MICRO_DIAGNOSTIC": 0}` é a chave existindo sem evidência atrás."""
        j = jornada_de(origens={"MICRO_DIAGNOSTIC": 0},
                       estado_atividade=ESTADO_NAO_INICIADO, rota="DIAGNOSTIC")
        self.assertEqual(_estado(j, ETAPA_DIAGNOSTICO), ESTADO_EM_ANDAMENTO)

    def test_sempre_ha_exatamente_uma_etapa_atual(self):
        """Duas etapas atuais confundem; nenhuma deixa o aluno sem referência."""
        casos = [
            ({}, ESTADO_NAO_INICIADO, "DIAGNOSTIC"),
            ({"MICRO_DIAGNOSTIC": 3}, ESTADO_NAO_INICIADO, "DIRECT"),
            ({"MICRO_DIAGNOSTIC": 3}, ESTADO_EM_ANDAMENTO, "DIRECT"),
            ({"MICRO_DIAGNOSTIC": 3, "PRACTICE": 5}, ESTADO_NAO_INICIADO,
             ROTA_PREPARACAO),
        ]
        for origens, atividade, rota in casos:
            j = jornada_de(origens=origens, estado_atividade=atividade, rota=rota)
            atuais = [e["id"] for e in j if e["state"] == ESTADO_EM_ANDAMENTO]
            with self.subTest(origens=origens, rota=rota):
                self.assertEqual(len(atuais), 1,
                                 f"{len(atuais)} etapas atuais: {atuais}")

    def test_toda_etapa_tem_rotulo_para_o_aluno(self):
        j = jornada_de(origens={}, estado_atividade=ESTADO_NAO_INICIADO,
                       rota=ROTA_PREPARACAO)
        for e in j:
            with self.subTest(etapa=e["id"]):
                self.assertTrue(e["label"].strip())
                self.assertNotIn("_", e["label"], "rótulo com código interno")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
