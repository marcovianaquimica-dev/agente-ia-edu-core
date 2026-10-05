"""A MAQUINA DE DECISAO do Assessor - o que ele faz depois de cada evento.

O QUE O TESTE HUMANO ENCONTROU (2026-10-05)
============================================
Reproduzido pelo caminho real da API, no banco de desenvolvimento:

  ACHADO 1  diagnostico 0/3 -> ENSINO -> guiada -> pratica 1/5 -> ENSINO de
            novo -> pratica 1/5 -> ENSINO de novo... o mesmo material e o
            mesmo lote de cinco questoes, sem fim. `escalate` ligava no ciclo
            4 e ninguem consumia.

  ACHADO 2  depois de 4/5 e de 5/5 - nove acertos nas ultimas dez - o passo
            continuou ENSINO, porque a media acumulada seguia em 0,556.

Estes testes descrevem a decisao correta em cada um desses pontos. Eles falham
contra o codigo anterior: foi assim que foram escritos.

O QUE ESTA MAQUINA NAO FAZ
===========================
Nao conhece threshold proprio, nao sabe o que e Estequiometria e nao inventa
numero nenhum. Recebe a banda agregada (ja passada pela politica), a
trajetoria recente, o ciclo e o que existe para oferecer.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.assessor_pedagogico import (
    ACAO_ENSINAR,
    ACAO_ESCALAR,
    ACAO_GUIADA,
    ACAO_PRATICAR,
    ACAO_VERIFICAR,
    LIMITE_DE_CICLOS,
    decidir_intervencao,
)
from agente_ia_edu.services.pedagogical_analysis import (
    BAND_IMPROVEMENT,
    BAND_INSUFFICIENT,
    BAND_STRONG,
)

LACUNA = {"por_habilidade": {"CONSERVACAO_DE_ATOMOS": {
    "band": BAND_IMPROVEMENT, "accuracy": 0.1,
    "name": "a conservação dos átomos"}}}
SEM_LACUNA = {"por_habilidade": {}}


def t(respondidas: int, certas: int) -> dict:
    return {"answered": respondidas, "correct": certas}


def decidir(**kwargs):
    base = dict(
        habilidades=LACUNA,
        banda_do_conteudo=BAND_IMPROVEMENT,
        ja_ensinado=False,
        praticas_concluidas=0,
        ha_material=True,
        ha_guiada_pendente=False,
        tentativas=(),
        objetivo_nome="Estequiometria e cálculos químicos",
        conteudo_nome="Reações químicas e balanceamento",
    )
    base.update(kwargs)
    return decidir_intervencao(**base)


class PrimeiraLacuna(unittest.TestCase):
    def test_quem_nunca_estudou_recebe_a_explicacao(self):
        self.assertEqual(decidir()["action"], ACAO_ENSINAR)

    def test_sem_material_nao_promete_explicacao(self):
        self.assertEqual(decidir(ha_material=False)["action"], ACAO_PRATICAR)

    def test_depois_de_estudar_vem_a_guiada(self):
        d = decidir(ja_ensinado=True, ha_guiada_pendente=True)
        self.assertEqual(d["action"], ACAO_GUIADA)

    def test_guiada_ja_feita_leva_a_pratica(self):
        d = decidir(ja_ensinado=True, ha_guiada_pendente=False)
        self.assertEqual(d["action"], ACAO_PRATICAR)


class NaoInterromperQuemJaSabe(unittest.TestCase):
    def test_banda_forte_sem_lacuna_nao_recebe_intervencao(self):
        d = decidir(habilidades=SEM_LACUNA, banda_do_conteudo=BAND_STRONG)
        self.assertIsNone(d["action"])

    def test_sem_amostra_e_assunto_do_diagnostico(self):
        d = decidir(habilidades=SEM_LACUNA, banda_do_conteudo=BAND_INSUFFICIENT)
        self.assertIsNone(d["action"])


class AchadoUmNaoEUmBancoDeQuestoes(unittest.TestCase):
    """Errar nao pode gerar sempre o mesmo lote de cinco questoes.

    DUAS GARANTIAS, E NENHUMA E "LIMITAR O REENSINO"
    ================================================
    Escrevi primeiro um limite de dois reensinos, e ele piorou o problema: no
    terceiro ciclo, com a guiada daquela habilidade ja concluida, so sobrava
    PRATICA - e entao vinham duas praticas seguidas, exatamente o que o limite
    existia para evitar. Medido em `test_ciclo_adaptativo_e2e`.

    As garantias que ficaram sao outras: entre duas praticas sempre ha uma
    intervencao (quem consome `ja_ensinado` o envelhece a cada tentativa), e o
    ciclo ACABA em ESCALAR em vez de recomecar.
    """

    def test_entre_duas_praticas_entra_uma_intervencao(self):
        """`ja_ensinado` volta a ser falso depois da tentativa, e o reensino
        entra - nunca dois lotes de questoes seguidos."""
        d = decidir(ja_ensinado=False, praticas_concluidas=2,
                    tentativas=[t(3, 0), t(5, 1), t(5, 1)])
        self.assertNotEqual(d["action"], ACAO_PRATICAR)

    def test_a_guiada_entra_quando_o_material_ja_valeu(self):
        d = decidir(ja_ensinado=True, praticas_concluidas=2,
                    ha_guiada_pendente=True,
                    tentativas=[t(3, 0), t(5, 1), t(5, 1)])
        self.assertEqual(d["action"], ACAO_GUIADA)

    def test_o_ciclo_dois_ainda_pode_reensinar(self):
        """Uma vez e legitimo: pode ser que ele nao tenha lido de verdade."""
        d = decidir(ja_ensinado=False, praticas_concluidas=1,
                    tentativas=[t(3, 0), t(5, 1)])
        self.assertEqual(d["action"], ACAO_ENSINAR)

    def test_a_pratica_nao_se_repete_indefinidamente(self):
        """Depois do teto, o passo deixa de ser mais questoes."""
        d = decidir(ja_ensinado=True, praticas_concluidas=LIMITE_DE_CICLOS,
                    tentativas=[t(3, 0), t(5, 1), t(5, 1), t(5, 1)])
        self.assertEqual(d["action"], ACAO_ESCALAR)
        self.assertTrue(d["escalate"])

    def test_quatro_praticas_ruins_nao_viram_a_quinta(self):
        for n in range(LIMITE_DE_CICLOS, LIMITE_DE_CICLOS + 3):
            with self.subTest(praticas=n):
                d = decidir(ja_ensinado=True, praticas_concluidas=n,
                            tentativas=[t(5, 1)] * n)
                self.assertEqual(d["action"], ACAO_ESCALAR)


class AchadoDoisARecuperacaoEReconhecida(unittest.TestCase):
    """Nove acertos nas ultimas dez nao podem passar despercebidos."""

    def test_quatro_de_cinco_leva_a_verificacao(self):
        d = decidir(ja_ensinado=True, praticas_concluidas=2,
                    tentativas=[t(3, 0), t(5, 1), t(5, 4)])
        self.assertEqual(d["action"], ACAO_VERIFICAR)

    def test_quatro_de_cinco_nao_libera_sozinho(self):
        d = decidir(ja_ensinado=True, praticas_concluidas=2,
                    tentativas=[t(3, 0), t(5, 1), t(5, 4)])
        self.assertIsNotNone(d["action"],
                             "uma tentativa boa nao pode apagar o historico")

    def test_a_verificacao_confirmada_libera(self):
        d = decidir(ja_ensinado=True, praticas_concluidas=3,
                    tentativas=[t(3, 0), t(5, 1), t(5, 4), t(3, 3)])
        self.assertIsNone(d["action"], "duas fortes seguidas: ele aprendeu")

    def test_a_verificacao_falha_volta_para_a_intervencao(self):
        d = decidir(ja_ensinado=True, praticas_concluidas=3,
                    ha_guiada_pendente=True,
                    tentativas=[t(3, 0), t(5, 1), t(5, 4), t(3, 0)])
        self.assertIn(d["action"], (ACAO_ENSINAR, ACAO_GUIADA, ACAO_PRATICAR,
                                    ACAO_ESCALAR))
        self.assertNotEqual(d["action"], ACAO_VERIFICAR)

    def test_recuperar_vence_o_teto_de_ciclos(self):
        """Quem acabou de ir bem nao e escalado por causa do passado."""
        d = decidir(ja_ensinado=True, praticas_concluidas=LIMITE_DE_CICLOS + 2,
                    tentativas=[t(5, 1), t(5, 1), t(5, 1), t(5, 5)])
        self.assertEqual(d["action"], ACAO_VERIFICAR)


class OQueASaidaPrecisaCarregar(unittest.TestCase):
    def test_a_habilidade_que_trava_e_nomeada(self):
        d = decidir()
        self.assertEqual(d["skill"], "CONSERVACAO_DE_ATOMOS")
        self.assertIn("conservação dos átomos", d["reason"])

    def test_a_tendencia_e_dita_para_quem_consome(self):
        d = decidir(tentativas=[t(3, 0), t(5, 1), t(5, 4)], ja_ensinado=True,
                    praticas_concluidas=2)
        self.assertEqual(d["trend"], "RECUPERANDO")

    def test_nenhuma_frase_acusa_o_aluno(self):
        for kwargs in ({}, {"ja_ensinado": True},
                       {"ja_ensinado": True, "praticas_concluidas": 9},
                       {"ja_ensinado": True, "praticas_concluidas": 2,
                        "tentativas": [t(3, 0), t(5, 4)]}):
            with self.subTest(**kwargs):
                texto = " ".join(str(v) for v in decidir(**kwargs).values()
                                 if isinstance(v, str))
                for palavra in ("errado", "errou", "falhou", "fraco",
                                "deficien", "incapaz", "ruim"):
                    self.assertNotIn(palavra, texto.lower())


class SemTrajetoriaOComportamentoAntigoSeMantem(unittest.TestCase):
    """Compatibilidade: quem chama sem `tentativas` continua funcionando."""

    def test_primeira_lacuna_sem_tentativas(self):
        d = decidir_intervencao(
            habilidades=LACUNA, banda_do_conteudo=BAND_IMPROVEMENT,
            ja_ensinado=False, praticas_concluidas=0, ha_material=True)
        self.assertEqual(d["action"], ACAO_ENSINAR)


if __name__ == "__main__":
    unittest.main()
