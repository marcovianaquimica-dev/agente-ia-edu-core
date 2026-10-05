"""O que o aluno LE depois de uma pratica - e por que ele continua na preparacao.

O ACHADO 3 DO TESTE HUMANO (2026-10-05)
========================================
Depois de uma pratica ruim a tela dizia:

    "Você acertou 1 de 5.
     Isso entra no seu progresso e ajusta o próximo passo."

Duas frases que descrevem o SISTEMA. Nenhuma assessora o aluno: ele nao fica
sabendo o que foi observado, o que vai acontecer agora, nem por que esse
proximo passo ajuda. E o texto morava no JavaScript - regra pedagogica onde o
console do navegador alcanca, o mesmo erro que o feedback do diagnostico ja
teve de corrigir uma vez.

Aqui o texto vem da DECISAO. Se a decisao muda, a frase muda junto; nao ha
como uma dizer uma coisa e a outra fazer outra.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.assessor_pedagogico import (
    ACAO_ENSINAR,
    ACAO_ESCALAR,
    ACAO_GUIADA,
    ACAO_PRATICAR,
    ACAO_VERIFICAR,
)
from agente_ia_edu.services.feedback_pedagogico import (
    TOM_BOM,
    TOM_NEUTRO,
    TOM_REVISAR,
    feedback_do_passo,
)
from agente_ia_edu.services.trajetoria_do_aluno import (
    TENDENCIA_CONFIRMADA,
    TENDENCIA_PERSISTENTE,
    TENDENCIA_RECUPERANDO,
)

HABILIDADE = "a conservação dos átomos"
CONTEUDO = "Reações químicas e balanceamento"
OBJETIVO = "Estequiometria e cálculos químicos"


def f(**kwargs):
    base = dict(action=ACAO_ENSINAR, trend=TENDENCIA_PERSISTENTE, cycle=1,
                skill_name=HABILIDADE, content_name=CONTEUDO,
                objective_name=OBJETIVO)
    base.update(kwargs)
    return feedback_do_passo(**base)


def texto(d: dict) -> str:
    return f"{d['titulo']} {d['detalhe']}"


class CadaEstadoTemASuaFrase(unittest.TestCase):
    def test_sao_todas_diferentes(self):
        vistos = {
            texto(f(action=None, trend=TENDENCIA_CONFIRMADA)),
            texto(f(action=ACAO_ENSINAR, cycle=1)),
            texto(f(action=ACAO_ENSINAR, cycle=3)),
            texto(f(action=ACAO_GUIADA, cycle=2)),
            texto(f(action=ACAO_PRATICAR, cycle=2)),
            texto(f(action=ACAO_VERIFICAR, trend=TENDENCIA_RECUPERANDO)),
            texto(f(action=ACAO_ESCALAR, cycle=4)),
        }
        self.assertEqual(len(vistos), 7, "dois estados dizem a mesma coisa")

    def test_nenhuma_fica_vazia(self):
        for acao in (None, ACAO_ENSINAR, ACAO_GUIADA, ACAO_PRATICAR,
                     ACAO_VERIFICAR, ACAO_ESCALAR):
            with self.subTest(acao=acao):
                d = f(action=acao)
                self.assertTrue(d["titulo"].strip())
                self.assertTrue(d["detalhe"].strip())
                self.assertIn(d["tom"], (TOM_BOM, TOM_REVISAR, TOM_NEUTRO))


class ADominioConfirmado(unittest.TestCase):
    def test_quem_confirmou_ouve_que_pode_avancar(self):
        d = f(action=None, trend=TENDENCIA_CONFIRMADA)
        self.assertEqual(d["tom"], TOM_BOM)
        self.assertIn(OBJETIVO, texto(d))


class BDificuldadeInicial(unittest.TestCase):
    def test_o_primeiro_ciclo_nomeia_a_habilidade_e_o_que_vem(self):
        d = f(action=ACAO_ENSINAR, cycle=1)
        self.assertIn(HABILIDADE, texto(d))


class CDificuldadePersistente(unittest.TestCase):
    def test_o_terceiro_ciclo_nao_repete_a_frase_do_primeiro(self):
        self.assertNotEqual(texto(f(action=ACAO_ENSINAR, cycle=1)),
                            texto(f(action=ACAO_ENSINAR, cycle=3)))

    def test_e_reconhece_que_ja_se_tentou_antes(self):
        d = f(action=ACAO_ENSINAR, cycle=3)
        self.assertRegex(texto(d).lower(), r"de novo|outra vez|ainda|mais uma")


class DERecuperacaoEVerificacao(unittest.TestCase):
    def test_quem_melhorou_ouve_que_melhorou(self):
        d = f(action=ACAO_VERIFICAR, trend=TENDENCIA_RECUPERANDO)
        self.assertEqual(d["tom"], TOM_BOM)

    def test_e_entende_que_falta_confirmar(self):
        d = f(action=ACAO_VERIFICAR, trend=TENDENCIA_RECUPERANDO)
        self.assertRegex(texto(d).lower(), r"confirm|verific")

    def test_nao_promete_que_ja_passou(self):
        d = f(action=ACAO_VERIFICAR, trend=TENDENCIA_RECUPERANDO)
        self.assertNotRegex(texto(d).lower(), r"voc[eê] domina|est[aá] liberad")


class FEscalonamento(unittest.TestCase):
    def test_diz_que_o_proximo_passo_e_outra_pessoa(self):
        d = f(action=ACAO_ESCALAR, cycle=4)
        self.assertRegex(texto(d).lower(), r"professor")

    def test_NAO_afirma_que_alguem_foi_avisado(self):
        """Nao ha infraestrutura para o professor receber isso. Dizer que foi
        avisado seria mentir para o aluno sobre algo que ele vai esperar."""
        d = f(action=ACAO_ESCALAR, cycle=4)
        for mentira in ("avisei", "notifiquei", "enviei", "já informei",
                        "seu professor foi"):
            self.assertNotIn(mentira, texto(d).lower())


class NenhumaFraseAcusa(unittest.TestCase):
    def test_sem_palavra_punitiva_em_nenhum_estado(self):
        for acao in (None, ACAO_ENSINAR, ACAO_GUIADA, ACAO_PRATICAR,
                     ACAO_VERIFICAR, ACAO_ESCALAR):
            for ciclo in (1, 3, 5):
                with self.subTest(acao=acao, ciclo=ciclo):
                    t = texto(f(action=acao, cycle=ciclo)).lower()
                    for palavra in ("errado", "errou", "falhou", "fraco",
                                    "deficien", "incapaz", "ruim", "burro",
                                    "não consegue"):
                        self.assertNotIn(palavra, t)

    def test_sem_codigo_interno_na_tela(self):
        for acao in (None, ACAO_ENSINAR, ACAO_GUIADA, ACAO_PRATICAR,
                     ACAO_VERIFICAR, ACAO_ESCALAR):
            with self.subTest(acao=acao):
                t = texto(f(action=acao))
                for codigo in ("PONTO_MELHORIA", "VERIFY", "ESCALATE",
                               "readiness", "band", "RECUPERANDO", "TEACH"):
                    self.assertNotIn(codigo, t)


class SemNomesOTextoAindaFuncion(unittest.TestCase):
    def test_sem_habilidade_nem_objetivo(self):
        for acao in (None, ACAO_ENSINAR, ACAO_VERIFICAR, ACAO_ESCALAR):
            with self.subTest(acao=acao):
                d = feedback_do_passo(action=acao, trend=TENDENCIA_PERSISTENTE,
                                      cycle=1, skill_name=None,
                                      content_name=None, objective_name=None)
                self.assertTrue(d["titulo"].strip())
                self.assertTrue(d["detalhe"].strip())
                self.assertNotIn("None", texto(d))


if __name__ == "__main__":
    unittest.main()
