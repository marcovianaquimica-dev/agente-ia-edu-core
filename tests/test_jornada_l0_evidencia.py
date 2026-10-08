"""A JORNADA ATÉ L0 — e o que chega ao Evidence Engine, pelo caminho real.

O QUE ESTE ARQUIVO EXISTE PARA GUARDAR
=======================================
O percurso do §1: diagnóstico → investigação → ensino → guiada → **item
novo sem ajuda** → evidência → próxima decisão.

Os outros arquivos deste bloco provam as peças: que os itens de verificação
existem e conferem, que a seleção não devolve o item da sondagem, que a
conversa sobrevive. Este prova a COLA — que a micro-habilidade decidida pelo
assessor chega à criação da prática, e que a resposta L0 vira evidência pelo
mesmo caminho de sempre.

AS DUAS INVARIANTES, E ELAS APONTAM EM DIREÇÕES OPOSTAS
========================================================
1. O acerto no degrau GUIADO **não** produz evidência. Ele mora em
   `guided_practice_items`, que o mapa de domínio não lê.
2. O acerto no degrau AUTÔNOMO **produz** evidência — e produz só isso:
   `PerformanceThresholdPolicy` exige `min_sample_size` respostas antes de
   falar em banda, e um acerto isolado continua sendo amostra insuficiente.

Um teste que só verificasse a segunda passaria com o produto medindo ajuda
como aprendizagem. Um que só verificasse a primeira passaria com o produto
não medindo nada.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import unittest
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, Question, QuestionVersion
from agente_ia_edu.services.adaptive_practice import (
    PROPOSITO_VERIFICACAO,
    AdaptivePracticeService,
)
from agente_ia_edu.services.grafo_estequiometria import CONTEUDO, MASSA_MOLAR
from agente_ia_edu.services.verificacao_da_habilidade import (
    itens_para_verificar,
    selecao_para_pratica,
)
from agente_ia_edu.services.verificacao_estequiometria import ITENS as ITENS_VER

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
ALUNO = "aluno_qa_l0"


def _carregar(nome: str, caminho: str):
    spec = importlib.util.spec_from_file_location(nome, _RAIZ / caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pub_sondagem = _carregar("pub_sond_l0",
                         "scripts/publicar_sondagem_estequiometria.py")
pub_verificacao = _carregar("pub_ver_l0",
                            "scripts/publicar_verificacao_estequiometria.py")


def _requester():
    """O `Requester` REAL do produto, nao um dublê.

    `create_practice` le `role` e `school_id` dele; um objeto falso com tres
    atributos passava nos outros servicos e estourava aqui. Usar o tipo de
    verdade e o que faz este teste exercitar o caminho real.
    """
    from agente_ia_edu.services.question_list_store import Requester

    return Requester(external_user_id=ALUNO, school_id=None, role="STUDENT",
                     is_platform_admin=False)


class _Base(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)
        self.req = _requester()

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with self.factory() as s:
                disc = CatalogNode(id=_uuid.uuid4(), code="CHEMISTRY",
                                   name="Quimica", node_type="DISCIPLINE",
                                   position=0, active=True)
                s.add(disc)
                await s.flush()
                disc.root_id = disc.id
                s.add(CatalogNode(id=_uuid.uuid4(), code=CONTEUDO,
                                  name="Estequiometria", node_type="CONTENT",
                                  position=0, parent_id=disc.id,
                                  root_id=disc.id, active=True))
                await s.commit()
            async with self.factory() as s:
                await pub_sondagem.publicar(s)
            async with self.factory() as s:
                await pub_verificacao.publicar(s)

        self.loop.run_until_complete(prep())

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _rodar(self, corrotina_de):
        async def run():
            async with self.factory() as s:
                return await corrotina_de(s)
        return self.loop.run_until_complete(run())

    def _versoes_por_chave(self) -> dict[str, str]:
        async def run():
            async with self.factory() as s:
                saida = {}
                for q in (await s.execute(select(Question))).scalars().all():
                    md = q.metadata_ or {}
                    chave = md.get("verificacao_key") or md.get("sondagem_key")
                    v = (await s.execute(select(QuestionVersion).where(
                        QuestionVersion.question_id == q.id))).scalars().first()
                    saida[chave] = str(v.id)
                return saida
        return self.loop.run_until_complete(run())


class APRATICAL0SERVEOSITENSDAHABILIDADE(_Base):
    """A cola: a seleção por micro-habilidade chega a `create_practice`."""

    def _criar_com_ids(self, ids):
        async def run(s):
            return await AdaptivePracticeService(s).create_practice(
                ALUNO, requester=self.req, content_code=CONTEUDO,
                question_count=len(ids), purpose=PROPOSITO_VERIFICACAO,
                question_version_ids=ids)
        return self._rodar(run)

    def test_create_practice_aceita_a_selecao_por_habilidade(self):
        chaves = self._versoes_por_chave()
        ids = [chaves[i.key] for i in ITENS_VER]
        saida = self._criar_com_ids(ids)
        self.assertEqual(len(ITENS_VER), saida["question_count"])

    def test_e_serve_EXATAMENTE_esses_itens(self):
        """Lido no BANCO, nao no retorno.

        `create_practice` devolve contagens e `selection_mode`, nao os ids.
        Conferir só a contagem deixaria passar o pior caso - três questões
        servidas, nenhuma delas a pedida -, então a asserção vai aos itens
        da avaliação montada.
        """
        from agente_ia_edu.db.models.assessments import AssessmentItem

        chaves = self._versoes_por_chave()
        ids = [chaves[i.key] for i in ITENS_VER]
        self._criar_com_ids(ids)

        async def ler(s):
            linhas = (await s.execute(
                select(AssessmentItem.question_version_id,
                       AssessmentItem.position)
                .order_by(AssessmentItem.position))).all()
            return [str(vid) for vid, _ in linhas]

        self.assertEqual(ids, self._rodar(ler))

    def test_e_o_relatorio_declara_que_a_selecao_veio_de_fora(self):
        """§16 de novo: a origem da seleção é dado, não suposição."""
        chaves = self._versoes_por_chave()
        ids = [chaves[i.key] for i in ITENS_VER]
        saida = self._criar_com_ids(ids)
        self.assertEqual("CALLER_SUPPLIED",
                         saida["selection"]["selection_mode"])

    def test_sem_selecao_de_fora_o_modo_volta_a_ser_por_conteudo(self):
        async def run(s):
            return await AdaptivePracticeService(s).create_practice(
                ALUNO, requester=self.req, content_code=CONTEUDO,
                question_count=3, purpose=PROPOSITO_VERIFICACAO)
        saida = self._rodar(run)
        self.assertEqual("CONTENT_RANKED",
                         saida["selection"]["selection_mode"])

    def test_o_item_da_sondagem_NAO_entra_no_lote_de_verificacao(self):
        chaves = self._versoes_por_chave()
        ids = [chaves[i.key] for i in ITENS_VER]
        self.assertNotIn(chaves["SOND-EST-MASSA-MOLAR-1"], ids)


class ACOLACHAMAOASSESSOR(_Base):
    """A habilidade vem do servidor — e sem lacuna medida não há seleção."""

    def test_sem_historico_nenhum_nao_ha_habilidade_que_trave(self):
        """Quem nunca respondeu nada não tem lacuna medida.

        E então a seleção por micro-habilidade devolve vazio e a prática cai
        na seleção por conteúdo — exatamente o comportamento anterior. Sem
        isto, um aluno novo receberia a "verificação" de algo que ninguém
        mediu.
        """
        async def run(s):
            return await selecao_para_pratica(
                s, aluno=ALUNO, conteudo=CONTEUDO, quantas=3,
                requester=self.req)
        self.assertEqual({}, self._rodar(run))

    def test_com_a_habilidade_dada_a_selecao_devolve_os_itens(self):
        """O caminho de dentro, com a habilidade já decidida."""
        async def run(s):
            return await itens_para_verificar(
                s, aluno=ALUNO, conteudo=CONTEUDO, habilidade=MASSA_MOLAR,
                quantas=3)
        self.assertEqual(3, len(self._rodar(run)))


class OGUIADONAOPRODUZEVIDENCIA(_Base):
    """A invariante herdada, medida de novo depois das mudanças deste bloco."""

    def test_a_jornada_guiada_inteira_nao_escreve_em_activity(self):
        from agente_ia_edu.db.models.guided_practice import GuidedPracticeItem
        from agente_ia_edu.services.pratica_guiada import PraticaGuiadaService
        from agente_ia_edu.services.itens_guiados import item_para

        item = item_para(CONTEUDO, MASSA_MOLAR)
        self.assertIsNotNone(item, "sem item guiado nao ha o que medir aqui")

        async def jogar(s):
            svc = PraticaGuiadaService(s)
            await svc.abrir(ALUNO, CONTEUDO, MASSA_MOLAR,
                            requester=self.req)
            # Pede todas as dicas e acerta: o caso mais favoravel possivel.
            for _ in range(4):
                try:
                    await svc.pedir_ajuda(ALUNO, item["key"],
                                          requester=self.req)
                except Exception:  # noqa: BLE001 - acabaram os niveis
                    break
            return await svc.responder(ALUNO, item["key"], item["correta"],
                                       requester=self.req)

        self._rodar(jogar)

        async def contar(s):
            from agente_ia_edu.db.models import (
                ActivityResult,
                ActivityResultItem,
                DomainContentMastery,
            )
            saida = {}
            for modelo in (ActivityResult, ActivityResultItem,
                           DomainContentMastery):
                saida[modelo.__tablename__] = len(
                    (await s.execute(select(modelo))).scalars().all())
            saida["guided_practice_items"] = len(
                (await s.execute(select(GuidedPracticeItem))).scalars().all())
            return saida

        contagem = self._rodar(contar)
        self.assertEqual(0, contagem["activity_results"])
        self.assertEqual(0, contagem["activity_result_items"])
        self.assertEqual(0, contagem["domain_content_mastery"])
        self.assertGreater(contagem["guided_practice_items"], 0)

    def test_e_acertar_com_as_quatro_dicas_nao_vira_solved_unaided(self):
        from agente_ia_edu.db.models.guided_practice import GuidedPracticeItem
        from agente_ia_edu.services.pratica_guiada import PraticaGuiadaService
        from agente_ia_edu.services.itens_guiados import item_para

        item = item_para(CONTEUDO, MASSA_MOLAR)

        async def jogar(s):
            svc = PraticaGuiadaService(s)
            await svc.abrir(ALUNO, CONTEUDO, MASSA_MOLAR,
                            requester=self.req)
            for _ in range(4):
                try:
                    await svc.pedir_ajuda(ALUNO, item["key"],
                                          requester=self.req)
                except Exception:  # noqa: BLE001
                    break
            await svc.responder(ALUNO, item["key"], item["correta"],
                                requester=self.req)

        self._rodar(jogar)

        async def ler(s):
            return (await s.execute(select(GuidedPracticeItem).where(
                GuidedPracticeItem.item_key == item["key"]))
            ).scalar_one_or_none()

        linha = self._rodar(ler)
        self.assertTrue(linha.completed)
        self.assertFalse(linha.solved_unaided)


if __name__ == "__main__":
    unittest.main()
