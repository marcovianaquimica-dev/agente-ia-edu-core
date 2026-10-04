"""PERFIL ALUNO - a cadeia atividade -> conteudo -> dominio -> prontidao.

O elo que faltava era o primeiro: ``QBStudentActivity`` nunca disse QUAIS
conteudos uma atividade exige, entao o cliente nao conseguia cruzar a tarefa
da escola com o mapa de dominio do aluno. Sem isso, o requisito 6 do perfil
Aluno ("a tarefa e o objetivo, nao necessariamente o primeiro passo")
funcionava so com mock.

A informacao ja existia inteira, espalhada por tabelas que nao se falavam:

    ActivityAssignment.assessment_version_id
      -> assessment_items        (quais questoes a lista tem)
      -> content_question_links  (a classificacao de cada questao)
      -> catalog_nodes.code      (o codigo de conteudo)

``catalog_nodes.code`` e o MESMO vocabulario que ``/learning-path`` e
``domain_content_mastery`` usam - por isso agregar fecha a cadeia sem
traducao no meio. Nenhuma tabela nova, nenhuma coluna nova: so leitura.

Primeiro teste verifica que estamos exercitando o codigo DESTE worktree, nao
o do checkout principal - o editable install do .venv aponta para la, e um
teste que roda contra a arvore errada passa sem provar nada.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.assessments import (
    Assessment,
    AssessmentItem,
    AssessmentVersion,
)
from agente_ia_edu.db.models.catalog import CatalogNode, ContentQuestionLink
from agente_ia_edu.services.activity_assignment_store import ActivityAssignmentStore


class ArvoreCorretaTests(unittest.TestCase):
    """Guarda contra o erro silencioso descrito na docstring."""

    def test_o_codigo_sob_teste_e_o_deste_worktree(self):
        import agente_ia_edu

        caminho = agente_ia_edu.__file__
        self.assertIn(
            "worktrees/cerebro-fase1", caminho,
            f"pytest carregou o pacote de {caminho} - os testes abaixo nao "
            "estariam provando nada sobre o codigo deste worktree",
        )

    def test_o_metodo_novo_existe_na_arvore_carregada(self):
        self.assertTrue(
            hasattr(ActivityAssignmentStore, "content_codes_by_version"),
            "a agregacao nao esta na arvore que o teste carregou",
        )


def _uuid4() -> _uuid.UUID:
    return _uuid.uuid4()


class CadeiaDeConteudosTests(unittest.TestCase):
    """SQLite em memoria, mesmo padrao de tests/test_phase16_*."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        self.loop.run_until_complete(self._criar_schema())

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    async def _criar_schema(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # -- montagem -----------------------------------------------------------

    async def _lista_com_conteudos(self, *, codigos_por_questao: list[str | None]):
        """Cria uma versao de lista cujas questoes estao classificadas nos
        codigos dados (None = questao sem classificacao)."""
        assessment_id, version_id = _uuid4(), _uuid4()
        async with self.factory() as s:
            s.add(Assessment(id=assessment_id, title="Lista", school_id=None,
                             created_by_external_identity="prof"))
            s.add(AssessmentVersion(id=version_id, assessment_id=assessment_id,
                                    version_number=1, title="Lista v1"))
            nos: dict[str, _uuid.UUID] = {}
            for posicao, codigo in enumerate(codigos_por_questao):
                qv_id = _uuid4()
                s.add(AssessmentItem(id=_uuid4(), assessment_version_id=version_id,
                                     question_version_id=qv_id, position=posicao, points=1))
                if codigo is None:
                    continue
                if codigo not in nos:
                    no_id = _uuid4()
                    nos[codigo] = no_id
                    s.add(CatalogNode(id=no_id, node_type="CONTENT", code=codigo,
                                      name=codigo.title(), position=0, active=True))
                s.add(ContentQuestionLink(id=_uuid4(), content_node_id=nos[codigo],
                                          question_version_id=qv_id))
            await s.commit()
        return assessment_id, version_id

    async def _agregar(self, version_ids):
        async with self.factory() as s:
            return await ActivityAssignmentStore(s).content_codes_by_version(version_ids)

    # -- os casos -----------------------------------------------------------

    def test_agrega_os_conteudos_das_questoes_da_atividade(self):
        async def run():
            _, v = await self._lista_com_conteudos(
                codigos_por_questao=["QUIMICA-ESTEQUIOMETRIA", "QUIMICA-SOLUCOES"])
            return await self._agregar([v]), v

        mapa, v = self.loop.run_until_complete(run())
        self.assertEqual(sorted(mapa[v]), ["QUIMICA-ESTEQUIOMETRIA", "QUIMICA-SOLUCOES"])

    def test_nao_repete_conteudo_quando_varias_questoes_tratam_dele(self):
        # O caso normal: uma lista de 12 questoes sobre UM assunto nao deve
        # devolver o mesmo codigo 12 vezes.
        async def run():
            _, v = await self._lista_com_conteudos(
                codigos_por_questao=["QUIMICA-ESTEQUIOMETRIA"] * 12)
            return await self._agregar([v]), v

        mapa, v = self.loop.run_until_complete(run())
        self.assertEqual(mapa[v], ["QUIMICA-ESTEQUIOMETRIA"])

    def test_preserva_a_ordem_de_primeira_aparicao_na_lista(self):
        # O conteudo que ABRE a atividade e o que ela trata primeiro; e ele
        # que o planejador deve considerar como assunto principal.
        async def run():
            _, v = await self._lista_com_conteudos(
                codigos_por_questao=["Z-ULTIMO", "A-PRIMEIRO", "Z-ULTIMO"])
            return await self._agregar([v]), v

        mapa, v = self.loop.run_until_complete(run())
        self.assertEqual(mapa[v], ["Z-ULTIMO", "A-PRIMEIRO"],
                         "ordenou alfabeticamente em vez de por posicao na lista")

    def test_questao_sem_classificacao_nao_vira_conteudo_fantasma(self):
        async def run():
            _, v = await self._lista_com_conteudos(
                codigos_por_questao=[None, "QUIMICA-SOLUCOES", None])
            return await self._agregar([v]), v

        mapa, v = self.loop.run_until_complete(run())
        self.assertEqual(mapa[v], ["QUIMICA-SOLUCOES"])

    def test_lista_inteiramente_nao_classificada_devolve_vazio_e_nao_erro(self):
        # Hoje a maioria do acervo esta assim. Vazio e um FATO ("nao sabemos o
        # que esta atividade exige"), nao uma falha - e o cliente trata como
        # evidencia ausente, nao como atividade sem conteudo.
        async def run():
            _, v = await self._lista_com_conteudos(codigos_por_questao=[None, None])
            return await self._agregar([v]), v

        mapa, v = self.loop.run_until_complete(run())
        self.assertEqual(mapa[v], [])

    def test_no_de_catalogo_inativo_nao_entra(self):
        async def run():
            _, v = await self._lista_com_conteudos(codigos_por_questao=["QUIMICA-ANTIGO"])
            async with self.factory() as s:
                no = (await s.execute(
                    CatalogNode.__table__.select().where(
                        CatalogNode.code == "QUIMICA-ANTIGO"))).first()
                await s.execute(CatalogNode.__table__.update()
                                .where(CatalogNode.id == no.id).values(active=False))
                await s.commit()
            return await self._agregar([v]), v

        mapa, v = self.loop.run_until_complete(run())
        self.assertEqual(mapa[v], [])

    def test_versoes_diferentes_nao_vazam_conteudo_uma_para_a_outra(self):
        async def run():
            _, v1 = await self._lista_com_conteudos(codigos_por_questao=["A-UM"])
            _, v2 = await self._lista_com_conteudos(codigos_por_questao=["B-DOIS"])
            return await self._agregar([v1, v2]), v1, v2

        mapa, v1, v2 = self.loop.run_until_complete(run())
        self.assertEqual(mapa[v1], ["A-UM"])
        self.assertEqual(mapa[v2], ["B-DOIS"])

    def test_uma_consulta_so_para_N_atividades(self):
        # Esta agregacao roda na tela INICIAL do aluno, a requisicao mais
        # quente do perfil. Um N+1 aqui apareceria direto para ele.
        async def run():
            ids = []
            for i in range(8):
                _, v = await self._lista_com_conteudos(codigos_por_questao=[f"C-{i}"])
                ids.append(v)
            contador = {"n": 0}

            @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
            def _conta(*_a):  # noqa: ANN001
                contador["n"] += 1

            try:
                async with self.factory() as s:
                    st = ActivityAssignmentStore(s)
                    contador["n"] = 0
                    mapa = await st.content_codes_by_version(ids)
                    return len(mapa), contador["n"]
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", _conta)

        quantas, consultas = self.loop.run_until_complete(run())
        self.assertEqual(quantas, 8)
        self.assertLessEqual(consultas, 1,
                             f"{consultas} consultas para 8 atividades - voltou o N+1")

    def test_sem_atividades_nao_consulta_o_banco(self):
        async def run():
            contador = {"n": 0}

            @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
            def _conta(*_a):  # noqa: ANN001
                contador["n"] += 1

            try:
                async with self.factory() as s:
                    mapa = await ActivityAssignmentStore(s).content_codes_by_version([])
                    return mapa, contador["n"]
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", _conta)

        mapa, consultas = self.loop.run_until_complete(run())
        self.assertEqual(mapa, {})
        self.assertEqual(consultas, 0)

    def test_ids_repetidos_nao_multiplicam_a_consulta(self):
        async def run():
            _, v = await self._lista_com_conteudos(codigos_por_questao=["X-UM"])
            return await self._agregar([v, v, v]), v

        mapa, v = self.loop.run_until_complete(run())
        self.assertEqual(mapa[v], ["X-UM"])
        self.assertEqual(len(mapa), 1)


class ListagemDoAlunoTests(unittest.TestCase):
    """A agregacao tem de chegar na listagem que o aluno realmente consome.

    Testar `content_codes_by_version` isolado prova que o SQL funciona; nao
    prova que alguem o chama. Este teste vai pelo caminho de verdade -
    `student_activities()`, o metodo por tras de GET /student/activities.
    """

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)

        async def criar():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        self.loop.run_until_complete(criar())

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def test_a_listagem_do_aluno_traz_os_conteudos_exigidos(self):
        from agente_ia_edu.db.models.admin import UserSchoolLink
        from agente_ia_edu.db.models.assessments import ActivityAssignment
        from agente_ia_edu.services.question_list_store import Requester

        escola = _uuid4()
        assessment_id, version_id = _uuid4(), _uuid4()
        no_id, qv_id = _uuid4(), _uuid4()

        async def run():
            async with self.factory() as s:
                s.add(Assessment(id=assessment_id, title="Atividade de Estequiometria",
                                 school_id=escola, created_by_external_identity="prof"))
                s.add(AssessmentVersion(id=version_id, assessment_id=assessment_id,
                                        version_number=1, title="v1"))
                s.add(AssessmentItem(id=_uuid4(), assessment_version_id=version_id,
                                     question_version_id=qv_id, position=0, points=1))
                s.add(CatalogNode(id=no_id, node_type="CONTENT",
                                  code="CHEMISTRY-PHYSICAL-STOICHIOMETRY",
                                  name="Estequiometria", position=0, active=True))
                s.add(ContentQuestionLink(id=_uuid4(), content_node_id=no_id,
                                          question_version_id=qv_id))
                s.add(ActivityAssignment(
                    id=_uuid4(), assessment_id=assessment_id,
                    assessment_version_id=version_id, school_id=escola,
                    target_type="STUDENT", target_id="stu-1", status="ACTIVE",
                    question_count=1, created_by_external_id="prof"))
                s.add(UserSchoolLink(external_user_id="stu-1", school_id=escola,
                                     role="STUDENT", scope_type="CLASSROOM",
                                     scope_external_id="turma-1", active=True))
                await s.commit()

            async with self.factory() as s:
                return await ActivityAssignmentStore(s).student_activities(
                    requester=Requester(external_user_id="stu-1", school_id=escola,
                                        role="STUDENT"))

        linhas = self.loop.run_until_complete(run())
        self.assertEqual(len(linhas), 1, "a atividade nao chegou ao aluno")
        self.assertEqual(linhas[0]["content_codes"],
                         ["CHEMISTRY-PHYSICAL-STOICHIOMETRY"],
                         "o campo existe no schema mas ninguem o preenche")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
