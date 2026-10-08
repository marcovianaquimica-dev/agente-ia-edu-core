"""A INVESTIGAÇÃO PERSISTIDA — sem tabela nova, e sem virar domínio.

ONDE ELA MORA, E POR QUÊ
=========================
Em `guided_practice_items`, uma linha por ETAPA. A tabela já existe, já tem a
semântica certa — interação assistida — e, o que mais importa, o mapa de
domínio já não a lê, por projeto. Pôr a investigação ali herda essa garantia
em vez de reconstruí-la.

Uma linha por etapa, e não uma por investigação, porque o estado que precisa
sobreviver é "qual etapa ainda não está de pé". Com uma linha só, errar e
acertar ficariam indistinguíveis no meio da cadeia.

O QUE ESTE ARQUIVO TRAVA
=========================
1. errar NÃO avança a etapa, e a resposta certa não viaja antes da hora;
2. acertar avança, e só então o que era aparece;
3. voltar retoma de onde parou — a investigação é idempotente;
4. nada daqui escreve no mapa de domínio;
5. a investigação de outra pessoa não é legível.
"""

from __future__ import annotations

import asyncio
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.guided_practice import GuidedPracticeItem
from agente_ia_edu.services.grafo_estequiometria import CONTEUDO, MASSA_MOLAR, PROPORCAO
from agente_ia_edu.services.investigacao_do_erro import investigacao_para
from agente_ia_edu.services.servico_de_investigacao import (
    InvestigacaoService,
    SemInvestigacao,
)


class _Requester:
    def __init__(self, quem: str, admin: bool = False):
        self.external_user_id = quem
        self.is_platform_admin = admin
        self.school_id = None


ALUNO = "aluno_qa_investigacao"


class _Base(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        # expire_on_commit=True de proposito: e o que a fabrica da aplicacao
        # usa, e e com ele que o MissingGreenlet aparece. Com False o bug nao
        # aparece no teste e aparece no navegador - ja aconteceu.
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=True)

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        self.loop.run_until_complete(prep())
        self.req = _Requester(ALUNO)
        self.inv = investigacao_para(CONTEUDO, PROPORCAO)

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _run(self, corrotina_de):
        async def run():
            async with self.factory() as s:
                return await corrotina_de(InvestigacaoService(s))
        return self.loop.run_until_complete(run())

    def abrir(self, skill=PROPORCAO, quem=None):
        req = quem or self.req
        return self._run(lambda sv: sv.abrir(req.external_user_id, CONTEUDO,
                                             skill, requester=req))

    def responder(self, ordem, escolha, quem=None):
        req = quem or self.req
        return self._run(lambda sv: sv.responder(
            req.external_user_id, self.inv.key, ordem, escolha, requester=req))

    def certa(self, ordem):
        return next(e.correta for e in self.inv.etapas if e.ordem == ordem)

    def errada(self, ordem):
        e = next(x for x in self.inv.etapas if x.ordem == ordem)
        return next(k for k in e.alternativas if k != e.correta)


class AbrirAInvestigacao(_Base):

    def test_abre_na_primeira_etapa(self):
        v = self.abrir()
        self.assertEqual(1, v["etapa"]["ordem"])
        self.assertFalse(v["completed"])

    def test_a_hipotese_vem_junto(self):
        self.assertIn("sugere", self.abrir()["hipotese"].lower())

    def test_conteudo_sem_cadeia_para_a_lacuna_recusa(self):
        with self.assertRaises(SemInvestigacao):
            self.abrir(skill="HABILIDADE_QUE_NAO_EXISTE")

    def test_abrir_duas_vezes_nao_muda_nada(self):
        self.assertEqual(self.abrir()["etapa"], self.abrir()["etapa"])


class ERRARNAOAVANCA(_Base):

    def test_a_etapa_continua_a_mesma(self):
        self.responder(1, self.errada(1))
        self.assertEqual(1, self.abrir()["etapa"]["ordem"])

    def test_a_resposta_diz_que_errou_sem_dizer_qual_era(self):
        r = self.responder(1, self.errada(1))
        self.assertFalse(r["correct"])
        self.assertNotIn("correct_option", r)

    def test_o_retorno_ENSINA_a_etapa(self):
        r = self.responder(1, self.errada(1))
        self.assertIn("massa molar", r["retorno"]["comentario"].lower())

    def test_o_gargalo_fica_localizado_na_habilidade_da_etapa(self):
        from agente_ia_edu.services.grafo_estequiometria import MASSA_MOL
        r = self.responder(1, self.errada(1))
        self.assertEqual(MASSA_MOL, r["bottleneck_skill"])

    def test_errar_duas_vezes_conta_duas_tentativas(self):
        self.responder(1, self.errada(1))
        self.responder(1, self.errada(1))

        async def contar():
            async with self.factory() as s:
                linha = (await s.execute(select(GuidedPracticeItem).where(
                    GuidedPracticeItem.item_key == f"{self.inv.key}#1"))
                ).scalar_one()
                return linha.attempts, linha.completed, linha.solved_unaided

        self.assertEqual((2, False, False), self.loop.run_until_complete(contar()))


class ACERTARAVANCA(_Base):

    def test_a_proxima_etapa_e_a_seguinte(self):
        r = self.responder(1, self.certa(1))
        self.assertTrue(r["correct"])
        self.assertEqual(2, r["etapa"]["ordem"])

    def test_a_etapa_resolvida_entra_nas_concluidas(self):
        r = self.responder(1, self.certa(1))
        self.assertEqual([1], [c["ordem"] for c in r["concluidas"]])

    def test_so_depois_de_acertar_a_letra_aparece(self):
        r = self.responder(1, self.certa(1))
        self.assertEqual(self.certa(1), r["concluidas"][0]["correct_option"])

    def test_acertar_de_primeira_marca_sem_ajuda(self):
        self.responder(1, self.certa(1))

        async def ler():
            async with self.factory() as s:
                return (await s.execute(select(GuidedPracticeItem).where(
                    GuidedPracticeItem.item_key == f"{self.inv.key}#1"))
                ).scalar_one().solved_unaided

        self.assertTrue(self.loop.run_until_complete(ler()))

    def test_acertar_depois_de_errar_nao_marca_sem_ajuda(self):
        self.responder(1, self.errada(1))
        self.responder(1, self.certa(1))

        async def ler():
            async with self.factory() as s:
                return (await s.execute(select(GuidedPracticeItem).where(
                    GuidedPracticeItem.item_key == f"{self.inv.key}#1"))
                ).scalar_one().solved_unaided

        self.assertFalse(self.loop.run_until_complete(ler()))

    def test_a_cadeia_inteira_conclui(self):
        for e in self.inv.etapas:
            r = self.responder(e.ordem, e.correta)
        self.assertTrue(r["completed"])
        self.assertIsNone(r["etapa"])
        self.assertIsNone(r["bottleneck_skill"])

    def test_concluida_ela_se_mantem_concluida_ao_reabrir(self):
        for e in self.inv.etapas:
            self.responder(e.ordem, e.correta)
        self.assertTrue(self.abrir()["completed"])


class RESPONDERFORADAVEZ(_Base):
    """A conferência é do servidor. O cliente não escolhe a etapa."""

    def test_responder_a_etapa_3_sem_as_anteriores_nao_a_resolve(self):
        self.responder(3, self.certa(3))
        self.assertEqual(1, self.abrir()["etapa"]["ordem"])

    def test_e_a_etapa_3_nao_fica_marcada_como_concluida(self):
        self.responder(3, self.certa(3))
        self.assertEqual([], self.abrir()["concluidas"])

    def test_etapa_inexistente_recusa(self):
        with self.assertRaises(SemInvestigacao):
            self.responder(99, "A")


class ANOTACAOEPORMICROHABILIDADE(_Base):
    """A linha grava QUAL micro-habilidade a etapa isolou - dado melhor que o
    que existia antes, porque é por etapa e não por questão inteira."""

    def test_cada_linha_carrega_a_habilidade_da_etapa(self):
        for e in self.inv.etapas:
            self.responder(e.ordem, e.correta)

        async def ler():
            async with self.factory() as s:
                linhas = (await s.execute(select(GuidedPracticeItem).where(
                    GuidedPracticeItem.student_external_id == ALUNO)
                    .order_by(GuidedPracticeItem.item_key))).scalars().all()
                return [(x.item_key, x.skill) for x in linhas]

        esperado = [(f"{self.inv.key}#{e.ordem}", e.habilidade)
                    for e in self.inv.etapas]
        self.assertEqual(esperado, self.loop.run_until_complete(ler()))


class AINVESTIGACAOEDAPESSOA(_Base):

    def test_a_de_outro_aluno_nao_e_legivel(self):
        self.responder(1, self.certa(1))
        outro = _Requester("outro_aluno")
        with self.assertRaises(PermissionError):
            self._run(lambda sv: sv.abrir(ALUNO, CONTEUDO, PROPORCAO,
                                          requester=outro))

    def test_e_nao_e_respondivel_por_outro(self):
        outro = _Requester("outro_aluno")
        with self.assertRaises(PermissionError):
            self._run(lambda sv: sv.responder(ALUNO, self.inv.key, 1, "B",
                                              requester=outro))


class NADADAQUIESCREVEDOMINIO(_Base):
    """Teste D do bloco, medido no banco e não na intenção."""

    def test_so_guided_practice_items_recebe_linha(self):
        for e in self.inv.etapas:
            self.responder(e.ordem, e.correta)

        async def contar():
            async with self.factory() as s:
                saida = {}
                for tabela in Base.metadata.sorted_tables:
                    n = (await s.execute(
                        select(tabela).limit(1))).first()
                    if n is not None:
                        saida[tabela.name] = True
                return set(saida)

        self.assertEqual({"guided_practice_items"},
                         self.loop.run_until_complete(contar()))

    def test_a_investigacao_concluida_nao_e_pergunta_de_dominio(self):
        """`concluida` responde "o apoio pode diminuir", nunca "ele domina"."""
        for e in self.inv.etapas:
            self.responder(e.ordem, e.correta)
        v = self.abrir()
        for proibido in ("mastery", "domina", "dominio", "score"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, {k.lower() for k in v})


class ACONCLUSAOALIMENTAAESCADA(_Base):

    def test_concluida_responde_que_a_investigacao_nao_esta_pendente(self):
        self.assertTrue(self._run(lambda sv: sv.pendente(
            ALUNO, CONTEUDO, PROPORCAO, requester=self.req)))
        for e in self.inv.etapas:
            self.responder(e.ordem, e.correta)
        self.assertFalse(self._run(lambda sv: sv.pendente(
            ALUNO, CONTEUDO, PROPORCAO, requester=self.req)))

    def test_lacuna_sem_cadeia_nunca_esta_pendente(self):
        self.assertFalse(self._run(lambda sv: sv.pendente(
            ALUNO, CONTEUDO, "NAO_EXISTE", requester=self.req)))

    def test_a_conclusao_de_uma_lacuna_nao_conclui_a_de_outra(self):
        for e in self.inv.etapas:
            self.responder(e.ordem, e.correta)
        self.assertTrue(self._run(lambda sv: sv.pendente(
            ALUNO, CONTEUDO, MASSA_MOLAR, requester=self.req)))


if __name__ == "__main__":
    unittest.main()
