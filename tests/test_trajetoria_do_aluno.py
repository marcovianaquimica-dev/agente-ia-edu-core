"""A TRAJETORIA do aluno, e nao so a media dele.

POR QUE ESTE MODULO EXISTE
===========================
Medido no banco de desenvolvimento em 2026-10-05, pelo caminho real da API:

    diagnostico 0 de 3      acumulado 0/3   = 0,00
    pratica     1 de 5      acumulado 1/8   = 0,125
    pratica     4 de 5      acumulado 5/13  = 0,385   -> ainda PONTO_MELHORIA
    pratica     5 de 5      acumulado 10/18 = 0,556   -> ainda PONTO_MELHORIA

O aluno acertou 9 das ultimas 10 e continuou na mesma tela, com a mesma
explicacao, e o sistema marcou `escalate`. Com a media acumulada, quem comeca
mal precisaria de ONZE acertos seguidos so para sair da faixa de melhoria - e
de VINTE E SETE para chegar a faixa forte.

Nao e um limiar errado: e a pergunta errada. "Qual a media historica dele?" nao
responde "ele aprendeu?".

O QUE ESTE MODULO NAO FAZ
==========================
Nao apaga o historico e nao libera ninguem. Ele classifica a trajetoria; quem
decide o proximo passo e o assessor, e uma tentativa forte sozinha leva a
VERIFICAR, nao a avancar.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.pedagogical_analysis import (
    PerformanceThresholdPolicy,
)
from agente_ia_edu.services.trajetoria_do_aluno import (
    TENDENCIA_CONFIRMADA,
    TENDENCIA_INDEFINIDA,
    TENDENCIA_PERSISTENTE,
    TENDENCIA_RECUPERANDO,
    tendencia,
    tentativas_de_resultados,
)

POLITICA = PerformanceThresholdPolicy.default()


def t(respondidas: int, certas: int) -> dict:
    return {"answered": respondidas, "correct": certas}


class SemTentativaNaoHaTrajetoria(unittest.TestCase):
    def test_lista_vazia(self):
        self.assertEqual(tendencia([]), TENDENCIA_INDEFINIDA)

    def test_none(self):
        self.assertEqual(tendencia(None), TENDENCIA_INDEFINIDA)


class UmaTentativaForteNaoConfirma(unittest.TestCase):
    """A regra que o bloco pediu em letras maiusculas: 4/5 nao libera."""

    def test_quatro_de_cinco_depois_de_historico_ruim(self):
        historico = [t(3, 0), t(5, 1), t(5, 4)]
        self.assertEqual(tendencia(historico), TENDENCIA_RECUPERANDO)

    def test_recuperando_nao_e_confirmado(self):
        self.assertNotEqual(tendencia([t(3, 0), t(5, 4)]), TENDENCIA_CONFIRMADA)


class DuasForTesSeguidasConfirmam(unittest.TestCase):
    def test_quatro_de_cinco_e_depois_tres_de_tres(self):
        historico = [t(3, 0), t(5, 1), t(5, 4), t(3, 3)]
        self.assertEqual(tendencia(historico), TENDENCIA_CONFIRMADA)

    def test_a_media_acumulada_continuaria_fraca(self):
        """O ponto do modulo: 8/16 e 0,50 e mesmo assim confirma."""
        historico = [t(3, 0), t(5, 1), t(5, 4), t(3, 3)]
        respondidas = sum(x["answered"] for x in historico)
        certas = sum(x["correct"] for x in historico)
        media = certas / respondidas
        self.assertLess(media, POLITICA.improvement_accuracy)
        self.assertEqual(tendencia(historico), TENDENCIA_CONFIRMADA)


class UmaQuedaInterrompeASequencia(unittest.TestCase):
    def test_forte_forte_fraca_volta_a_persistente(self):
        historico = [t(5, 5), t(5, 4), t(5, 1)]
        self.assertEqual(tendencia(historico), TENDENCIA_PERSISTENTE)

    def test_forte_fraca_forte_e_recuperando_de_novo(self):
        historico = [t(5, 5), t(5, 1), t(5, 4)]
        self.assertEqual(tendencia(historico), TENDENCIA_RECUPERANDO)


class DificuldadePersistente(unittest.TestCase):
    def test_tudo_fraco(self):
        self.assertEqual(tendencia([t(3, 0), t(5, 1), t(5, 1)]),
                         TENDENCIA_PERSISTENTE)

    def test_a_ultima_e_que_manda(self):
        """Uma tentativa boa no meio nao torna persistente o que terminou mal."""
        self.assertEqual(tendencia([t(5, 5), t(5, 0)]), TENDENCIA_PERSISTENTE)


class FaixaDoMeioNaoEPersistenteNemRecuperando(unittest.TestCase):
    def test_tres_de_cinco_fica_indefinida(self):
        # 0,60 <= acerto < 0,80: a politica ja chama isso de intermediario, e
        # intermediario nao e nem lacuna nem dominio.
        self.assertEqual(tendencia([t(5, 1), t(5, 3)]), TENDENCIA_INDEFINIDA)


class AmostraPequenaNaoDecideNada(unittest.TestCase):
    """Abaixo do minimo da politica, a tentativa nao conclui - nem bem nem mal."""

    def test_duas_questoes_nao_confirmam(self):
        self.assertEqual(tendencia([t(5, 1), t(2, 2)]), TENDENCIA_PERSISTENTE,
                         "2 de 2 nao pode valer como tentativa forte")

    def test_duas_questoes_nao_derrubam_uma_sequencia(self):
        historico = [t(5, 4), t(3, 3), t(2, 0)]
        self.assertEqual(tendencia(historico), TENDENCIA_CONFIRMADA,
                         "uma amostra pequena nao apaga duas fortes")


class APoliticaEAMesmaDoResto(unittest.TestCase):
    """Nenhum numero novo: os cortes sao os da PerformanceThresholdPolicy."""

    def test_usa_o_corte_forte_da_politica(self):
        folgado = PerformanceThresholdPolicy(
            min_sample_size=3, strong_accuracy=0.5, improvement_accuracy=0.4)
        # 3 de 5 = 0,60: fraco pela politica padrao, forte pela folgada.
        self.assertEqual(tendencia([t(5, 3), t(5, 3)]), TENDENCIA_INDEFINIDA)
        self.assertEqual(tendencia([t(5, 3), t(5, 3)], thresholds=folgado),
                         TENDENCIA_CONFIRMADA)


class DosResultadosGravadosParaTentativas(unittest.TestCase):
    """A entrada vem do que ja esta gravado: um ActivityResult por tentativa."""

    def test_agrupa_por_resultado_em_ordem(self):
        linhas = [
            {"result_id": "r1", "corrected_at": "2026-10-01T10:00:00Z",
             "is_correct": False},
            {"result_id": "r1", "corrected_at": "2026-10-01T10:00:00Z",
             "is_correct": False},
            {"result_id": "r2", "corrected_at": "2026-10-02T10:00:00Z",
             "is_correct": True},
        ]
        self.assertEqual(
            tentativas_de_resultados(linhas),
            [{"answered": 2, "correct": 0}, {"answered": 1, "correct": 1}])

    def test_ordena_pelo_tempo_e_nao_pela_ordem_de_chegada(self):
        linhas = [
            {"result_id": "depois", "corrected_at": "2026-10-09T10:00:00Z",
             "is_correct": True},
            {"result_id": "antes", "corrected_at": "2026-10-01T10:00:00Z",
             "is_correct": False},
        ]
        self.assertEqual(
            tentativas_de_resultados(linhas),
            [{"answered": 1, "correct": 0}, {"answered": 1, "correct": 1}])

    def test_sem_linhas(self):
        self.assertEqual(tentativas_de_resultados([]), [])


if __name__ == "__main__":
    unittest.main()
