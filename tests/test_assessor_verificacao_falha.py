"""DEPOIS DE UMA VERIFICAÇÃO QUE FALHOU, O ASSESSOR VOLTA A ENSINAR.

O QUE FOI MEDIDO, EM 2026-10-06
================================
Chamando `decidir_intervencao` pelo caminho real, com a lacuna medida e a
trajetória do aluno:

    1/5 → 5/5 → VERIFY 0/3, SEM material publicado
    → action=PRACTICE
    → tela: "Vamos tentar de novo, sozinho."

O aluno acabou de falhar a verificação e o sistema ofereceu mais questões
sozinho. Uma verificação forte negativa não diz "tente de novo": diz que a
INTERVENÇÃO ANTERIOR NÃO BASTOU. Repetir o mesmo lote é responder a essa
informação ignorando-a.

E o conteúdo da demonstração é justamente um sem material publicado: das 3
linhas de `theory_materials`, só `CHEMISTRY-GENERAL-BALANCING` está PUBLIC.
Para Estequiometria o ramo de ensino nunca acendia.

O QUE ESTE TESTE TRAVA
=======================
Depois de uma verificação que falhou, a próxima ação NUNCA é praticar sozinho.
É ensinar de novo - por outro caminho - ou, quando não há mais estratégia a
oferecer, escalar. Nunca o mesmo lote de questões.

O sinal de que a última tentativa era uma verificação não é adivinhado: ele é
gravado no `purpose` da própria prática, que já tem metadados.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.assessor_pedagogico import (
    ACAO_ENSINAR,
    ACAO_ESCALAR,
    ACAO_GUIADA,
    ACAO_PRATICAR,
    decidir_intervencao,
)
from agente_ia_edu.services.pedagogical_analysis import BAND_IMPROVEMENT

LACUNA = {"por_habilidade": {
    "CONSERVACAO_DE_ATOMOS": {"band": BAND_IMPROVEMENT, "accuracy": 0.2,
                              "name": "conservação de átomos"}}}

# 1/5, depois 5/5, depois a verificação 0/3: a trajetória que o achado tinha.
TRAJETO_COM_VERIFICACAO_FALHA = [
    {"answered": 5, "correct": 1},
    {"answered": 5, "correct": 5},
    {"answered": 3, "correct": 0},
]


def decidir(**kw):
    base = dict(
        habilidades=LACUNA,
        banda_do_conteudo=BAND_IMPROVEMENT,
        ja_ensinado=False,
        praticas_concluidas=2,
        ha_material=False,
        ha_guiada_pendente=False,
        tentativas=TRAJETO_COM_VERIFICACAO_FALHA,
        conteudo_nome="Estequiometria",
        ultima_foi_verificacao=True,
    )
    base.update(kw)
    return decidir_intervencao(**base)


class VerificacaoFalhaNaoMandaPraticarSozinho(unittest.TestCase):
    """O caso medido, nas quatro combinações que o produto tem."""

    def test_sem_material_e_sem_guiada_nao_e_pratica(self):
        self.assertNotEqual(ACAO_PRATICAR, decidir()["action"],
                            "verificação falhou e o sistema ofereceu mais "
                            "questões sozinho")

    def test_sem_nada_a_oferecer_escala_em_vez_de_repetir(self):
        """Sem material e sem guiada, acabaram as estratégias deste sistema."""
        self.assertEqual(ACAO_ESCALAR, decidir()["action"])

    def test_com_material_volta_a_ensinar(self):
        self.assertEqual(ACAO_ENSINAR,
                         decidir(ha_material=True, ja_ensinado=False)["action"])

    def test_com_guiada_pendente_tenta_com_ajuda(self):
        """Tentar COM ajuda é mudar de estratégia; tentar sozinho não é."""
        self.assertEqual(ACAO_GUIADA, decidir(ha_guiada_pendente=True)["action"])


class OEnsinoDepoisDaVerificacaoEntraPorOutroCaminho(unittest.TestCase):
    """Reabrir o mesmo texto do mesmo ponto não é uma segunda tentativa."""

    def test_a_abordagem_nao_e_a_primeira(self):
        d = decidir(ha_material=True, ja_ensinado=False)
        self.assertEqual(ACAO_ENSINAR, d["action"])
        self.assertNotEqual("CONCEITO", d["approach"],
                            "a segunda explicação entra pelo mesmo lugar da "
                            "primeira")


class SemVerificacaoNadaMuda(unittest.TestCase):
    """A regra é sobre verificação falha, não sobre ir mal em geral.

    Uma prática fraca comum continua podendo levar a praticar: entre as duas
    houve a explicação do erro, que é a intervenção.
    """

    def test_pratica_fraca_comum_ainda_pode_praticar(self):
        d = decidir(ultima_foi_verificacao=False,
                    tentativas=[{"answered": 5, "correct": 1}],
                    praticas_concluidas=1)
        self.assertEqual(ACAO_PRATICAR, d["action"])

    def test_o_padrao_e_nao_ser_verificacao(self):
        """Quem não informa não é punido: o parâmetro tem padrão seguro."""
        d = decidir_intervencao(
            habilidades=LACUNA, banda_do_conteudo=BAND_IMPROVEMENT,
            ja_ensinado=False, praticas_concluidas=1, ha_material=False,
            tentativas=[{"answered": 5, "correct": 1}])
        self.assertEqual(ACAO_PRATICAR, d["action"])


class RecuperacaoAindaVenceTudo(unittest.TestCase):
    """Quem está melhorando não pode ser escalado pela regra nova."""

    def test_uma_forte_depois_de_fraca_continua_indo_para_verificar(self):
        d = decidir(tentativas=[{"answered": 5, "correct": 1},
                                {"answered": 5, "correct": 5}],
                    ultima_foi_verificacao=False)
        self.assertEqual("VERIFY", d["action"])


if __name__ == "__main__":
    unittest.main()
