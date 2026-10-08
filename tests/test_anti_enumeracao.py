"""Um aluno não pode descobrir o que existe perguntando.

O PROBLEMA
==========
Recurso inexistente respondia **404**. Recurso real de outro aluno respondia
**403** com *"this activity is not assigned to you"*. A diferença entre as
duas respostas é um oráculo: quem varre UUIDs aprende quais existem, e a
mensagem confirma que existe atividade naquele id.

O dano não é grande — é preciso adivinhar um UUID — mas é gratuito, e o custo
de fechar é um mapeamento de status.

A REGRA
=======
Nas rotas **do aluno**, o recurso é privado por definição: ele nunca tem
motivo legítimo para saber que existe uma atividade que não é dele. Então
"não existe" e "não é seu" respondem **igual**.

O QUE *NÃO* MUDA
================
As rotas de **gestão** (professor, coordenação) continuam 403. Lá o requester
pode listar as distribuições da escola, então saber que uma existe não é
vazamento — e um 404 ali esconderia um erro de permissão real de quem precisa
corrigi-lo.

E `AssignmentAuthError` continua sendo `AssignmentAuthError`. O que muda é
como a camada HTTP do aluno a traduz. Mexer na exceção mudaria o contrato de
prática, diagnóstico e gestão de uma vez.

COMO ESTE ARQUIVO MEDE
======================
Comparando pares. Não basta "deu 404": o par (inexistente, de outro) tem de
ser **indistinguível** — mesmo status, mesmo corpo.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_session_factory,
)
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, CatalogNode, Exam,
    ExamApplication, ExamBooklet, Institution, PedagogicalClassification,
    Question, QuestionOption, QuestionVersion, SourceDocument,
)
from agente_ia_edu.db.models.admin import UserSchoolLink
from agente_ia_edu.db.models.catalog import ContentQuestionLink
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.activity_assignment_store import ActivityAssignmentStore
from agente_ia_edu.services.curriculum_domain_map import ORIGIN_OFFICIAL_ACTIVITY
from agente_ia_edu.services.list_generator import ListConfiguration
from agente_ia_edu.services.question_list_store import QuestionListStore, Requester

KEYS = ["A", "B", "C", "D", "E"]
CONTEUDO = "QUIM-ESTEQUIOMETRIA"
ANA = "aluna_ana"          # dona da atividade da turma
BRUNO = "aluno_bruno"      # outra turma, sem acesso a nada da Ana
PROF = "professor_abc"
TURMA_A = "TURMA_DA_ANA"
TURMA_B = "TURMA_DO_BRUNO"
ESCOLA = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "anti-enumeracao-escola"))
INEXISTENTE = str(_uuid.uuid4())


def _ctx(user=ANA, role="STUDENT"):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user,
                                    role=role, school_id=ESCOLA, scope_type="SCHOOL")


async def _seed(factory) -> dict:
    async with factory() as s:
        inst = Institution(code="INEP", name="INEP"); s.add(inst); await s.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM"); s.add(exam); await s.flush()
        disc = CatalogNode(code="QUIM", name="Quimica", node_type="DISCIPLINE",
                           position=0, active=True)
        s.add(disc); await s.flush(); disc.root_id = disc.id; await s.flush()
        area = CatalogNode(code="QUIM-A", name="Area", node_type="AREA", position=0,
                           parent_id=disc.id, root_id=disc.id, active=True)
        s.add(area); await s.flush()
        no = CatalogNode(code=CONTEUDO, name="Estequiometria", node_type="CONTENT",
                         position=1, parent_id=area.id, root_id=disc.id, active=True)
        s.add(no); await s.flush()

        app_ = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=1)
        s.add(app_); await s.flush()
        bk = ExamBooklet(exam_application_id=app_.id, code="C24", color="AZUL")
        s.add(bk); await s.flush()
        sd = SourceDocument(exam_application_id=app_.id, exam_booklet_id=bk.id,
                            document_type="ANSWER_KEY", source_url="https://x/g.pdf",
                            acquired_at=datetime.now(timezone.utc), content_hash="g24")
        s.add(sd); await s.flush()
        rev = AnswerKeyRevision(source_document_id=sd.id, revision_number=1,
                                is_official=True)
        s.add(rev); await s.flush()

        gabarito: dict[str, str] = {}
        versoes: list[str] = []
        for numero in range(1, 7):
            correta = KEYS[numero % 5]
            q = Question(validation_status="validated", origin_type="IMPORTED",
                         status="PUBLISHED", visibility_scope="PUBLIC")
            s.add(q); await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=f"e{numero}", statement=f"enunciado {numero}",
                                content_hash=f"h{numero}", is_immutable=True)
            s.add(v); await s.flush()
            opcoes = {}
            for pos, key in enumerate(KEYS, start=1):
                o = QuestionOption(question_version_id=v.id, option_key=key,
                                   position=pos, text=f"Alt {key}",
                                   is_valid_option=(key == correta))
                s.add(o); await s.flush(); opcoes[key] = o
            bq = BookletQuestion(exam_booklet_id=bk.id, question_version_id=v.id,
                                 position=numero, official_number=numero, page_number=1)
            s.add(bq); await s.flush()
            s.add(AnswerKeyEntry(answer_key_revision_id=rev.id, booklet_question_id=bq.id,
                                 official_answer_label=correta,
                                 resolved_option_id=opcoes[correta].id, page_number=1))
            s.add(PedagogicalClassification(
                question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
                content=CONTEUDO, subcontent=CONTEUDO, difficulty="UNKNOWN",
                reasoning_type="U", prerequisites=[], keywords=[], competencies=[],
                skills=[], status="CLASSIFIED", source="rule", lifecycle="ACTIVE",
                model_version="fx", prompt_version="v1",
                metadata_={"taxonomy_version": "curriculum-v2",
                           "primary_content_code": CONTEUDO,
                           "visual_dependency": False}))
            s.add(ContentQuestionLink(content_node_id=no.id, question_version_id=v.id))
            gabarito[str(v.id)] = correta
            versoes.append(str(v.id))

        for quem, papel, escopo, alvo in (
                (ANA, "STUDENT", "CLASSROOM", TURMA_A),
                (BRUNO, "STUDENT", "CLASSROOM", TURMA_B),
                (PROF, "TEACHER", "SCHOOL", None)):
            s.add(UserSchoolLink(external_user_id=quem, school_id=_uuid.UUID(ESCOLA),
                                 role=papel, scope_type=escopo,
                                 scope_external_id=alvo, active=True))
        await s.commit()

    async def _distribuir(titulo, vids, tipo, alvo) -> str:
        async with factory() as s:
            prof = Requester(external_user_id=PROF, school_id=ESCOLA,
                             role="TEACHER", is_platform_admin=False)
            listas = QuestionListStore(s)
            resumo = await listas.create(
                configuration=ListConfiguration(title=titulo,
                                                answer_key_presentation="KEY_AT_END"),
                question_version_ids=[_uuid.UUID(v) for v in vids], requester=prof)
            lid = _uuid.UUID(str(resumo.id))
            await listas.finalize(lid, requester=prof)
            vista, _ = await ActivityAssignmentStore(s).create(
                lid, requester=prof, target_type=tipo, target_id=alvo,
                origin=ORIGIN_OFFICIAL_ACTIVITY)
            qual = str(vista.id)
            await s.commit()
            return qual

    da_ana = await _distribuir("Atividade da Ana", versoes[:3], "CLASS", TURMA_A)
    do_bruno = await _distribuir("Atividade do Bruno", versoes[3:6], "CLASS", TURMA_B)
    return {"gabarito": gabarito, "da_ana": da_ana, "do_bruno": do_bruno}


class AntiEnumeracaoTests(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(self.factory)

        self.fx = self.loop.run_until_complete(prep())
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app.dependency_overrides[get_current_authenticated_context] = _ctx
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.client.close()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _como(self, quem):
        self.app.dependency_overrides[get_current_authenticated_context] = \
            lambda: _ctx(user=quem)

    def _par_indistinguivel(self, chamar):
        """`chamar(id)` → resposta. Compara inexistente com o de outra turma."""
        a = chamar(INEXISTENTE)
        b = chamar(self.fx["do_bruno"])
        self.assertEqual(
            a.status_code, b.status_code,
            f"status distingue inexistente ({a.status_code}) de alheio "
            f"({b.status_code}) - isso revela o que existe")
        self.assertEqual(
            a.text, b.text,
            f"corpo distingue os dois casos:\n  inexistente: {a.text}\n"
            f"  alheio:      {b.text}")
        self.assertEqual(a.status_code, 404, a.text)
        return a

    # -- o par que importa -------------------------------------------------

    def test_abrir_atividade(self):
        self._par_indistinguivel(
            lambda i: self.client.post(f"/api/v1/student/activities/{i}/attempt"))

    def test_detalhe_da_atividade(self):
        self._par_indistinguivel(
            lambda i: self.client.get(f"/api/v1/student/activities/{i}"))

    def test_estado_da_tentativa(self):
        self._par_indistinguivel(
            lambda i: self.client.get(f"/api/v1/student/activities/{i}/attempt"))

    def test_resultado(self):
        self._par_indistinguivel(
            lambda i: self.client.get(
                f"/api/v1/student/activities/{i}/attempt/result"))

    def test_corrigir(self):
        self._par_indistinguivel(
            lambda i: self.client.post(
                f"/api/v1/student/activities/{i}/attempt/correct"))

    def test_finalizar(self):
        self._par_indistinguivel(
            lambda i: self.client.post(
                f"/api/v1/student/activities/{i}/attempt/complete"))

    def test_salvar_resposta(self):
        vid = next(iter(self.fx["gabarito"]))
        self._par_indistinguivel(
            lambda i: self.client.put(
                f"/api/v1/student/activities/{i}/attempt/answers/{vid}",
                json={"selected_option": "A"}))

    def test_posicao(self):
        self._par_indistinguivel(
            lambda i: self.client.put(
                f"/api/v1/student/activities/{i}/attempt/position?position=1"))

    def test_prontidao(self):
        self._par_indistinguivel(
            lambda i: self.client.get(
                f"/api/v1/student/activities/{i}/readiness"))

    def test_analise(self):
        self._par_indistinguivel(
            lambda i: self.client.get(
                f"/api/v1/student/activities/{i}/attempt/result/analysis"))

    # -- o dono continua entrando ------------------------------------------

    def test_a_dona_abre_a_propria_atividade(self):
        """A trava não pode virar bloqueio geral."""
        r = self.client.post(
            f"/api/v1/student/activities/{self.fx['da_ana']}/attempt")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "IN_PROGRESS")

    def test_a_dona_ve_a_propria_atividade_na_lista(self):
        itens = self.client.get("/api/v1/student/activities").json()["items"]
        self.assertIn(self.fx["da_ana"], {i["assignment_id"] for i in itens})
        self.assertNotIn(self.fx["do_bruno"], {i["assignment_id"] for i in itens})

    def test_o_bruno_abre_a_dele(self):
        self._como(BRUNO)
        r = self.client.post(
            f"/api/v1/student/activities/{self.fx['do_bruno']}/attempt")
        self.assertEqual(r.status_code, 200, r.text)

    def test_o_bruno_nao_abre_a_da_ana(self):
        self._como(BRUNO)
        r = self.client.post(
            f"/api/v1/student/activities/{self.fx['da_ana']}/attempt")
        self.assertEqual(r.status_code, 404, r.text)

    # -- a gestão continua com 403 -----------------------------------------

    def test_a_rota_de_gestao_continua_403(self):
        """Professor e coordenação PODEM listar as distribuições da escola:
        esconder a existência deles não protege nada e esconde um erro de
        permissão de quem precisa corrigi-lo."""
        self._como("professor_sem_vinculo")
        self.app.dependency_overrides[get_current_authenticated_context] = \
            lambda: _ctx(user="professor_sem_vinculo", role="TEACHER")
        r = self.client.get(
            f"/api/v1/question-bank/assignments/{self.fx['da_ana']}")
        self.assertIn(r.status_code, (403, 404),
                      f"a rota de gestão mudou de contrato: {r.status_code}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
