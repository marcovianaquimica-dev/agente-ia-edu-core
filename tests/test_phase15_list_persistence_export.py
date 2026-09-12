"""PHASE 15 - list persistence, history & export backend tests.

TestClient + in-memory SQLite. The stored list REUSES the existing
assessments/assessment_versions/assessment_items tables (no migration). Covers
create / retrieve / edit-draft / block-edit-finalized / delete-rules / order /
duplicate / unknown version / fingerprint / ownership / multi-tenant isolation /
answer-key snapshot / no mutation of official data / no N+1 / PDF+DOCX export.
"""

from __future__ import annotations

import asyncio
import io
import unittest
import zipfile
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_authenticated_context, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    CatalogNode,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    PedagogicalClassification,
    Question,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
)
from agente_ia_edu.db.models.assessments import Assessment, AssessmentItem, AssessmentVersion
from agente_ia_edu.identity import AuthenticatedUserContext

ANSWERS = {130: "C", 131: "A", 132: "E", 133: "B", 96: "D", 97: "A", 98: "B"}


async def _seed(factory) -> dict:
    async with factory() as s:
        inst = Institution(code="INEP", name="INEP")
        s.add(inst)
        await s.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM")
        s.add(exam)
        await s.flush()
        math = CatalogNode(code="MATH", name="M", node_type="DISCIPLINE", active=True)
        s.add(math)
        await s.flush()
        math.root_id = math.id
        area = CatalogNode(code="MATH-ALGEBRA", name="A", node_type="AREA", parent_id=math.id, root_id=math.id, active=True)
        s.add(area)
        await s.flush()
        content = CatalogNode(code="MATH-ALGEBRA-FUNCTIONS", name="F", node_type="CONTENT", parent_id=area.id, root_id=math.id, active=True)
        s.add(content)
        await s.flush()
        app = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=2)
        s.add(app)
        await s.flush()
        bk = ExamBooklet(exam_application_id=app.id, code="D2_CD5", color="AMARELO")
        s.add(bk)
        await s.flush()
        sd = SourceDocument(exam_application_id=app.id, exam_booklet_id=bk.id, document_type="ANSWER_KEY",
                            source_url="https://x/g.pdf", acquired_at=datetime.now(timezone.utc), content_hash="g")
        s.add(sd)
        await s.flush()
        rev = AnswerKeyRevision(source_document_id=sd.id, revision_number=1, is_official=True)
        s.add(rev)
        await s.flush()
        made = {}
        for num, correct in ANSWERS.items():
            q = Question(validation_status="validated", origin_type="IMPORTED", status="PUBLISHED", visibility_scope="PUBLIC")
            s.add(q)
            await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=f"Enunciado oficial 2024 Q{num}.",
                                statement=f"Enunciado oficial 2024 Q{num}.", content_hash=f"h{num}", is_immutable=True)
            s.add(v)
            await s.flush()
            opts = {}
            for pos, key in enumerate("ABCDE", start=1):
                o = QuestionOption(question_version_id=v.id, option_key=key, position=pos,
                                   text=f"Alt {key}", is_valid_option=(key == correct))
                s.add(o)
                await s.flush()
                opts[key] = o
            bq = BookletQuestion(exam_booklet_id=bk.id, question_version_id=v.id, position=num, official_number=num, page_number=1)
            s.add(bq)
            await s.flush()
            s.add(AnswerKeyEntry(answer_key_revision_id=rev.id, booklet_question_id=bq.id,
                                 official_answer_label=correct, resolved_option_id=opts[correct].id, page_number=1))
            if num == 130:
                s.add(PedagogicalClassification(
                    question_version_id=v.id, discipline="CURRICULUM_PROPOSAL", content="MATH-ALGEBRA-FUNCTIONS",
                    subcontent="MATH-ALGEBRA-FUNCTIONS", difficulty="UNKNOWN", reasoning_type="U",
                    prerequisites=[], keywords=[], competencies=[], skills=[], status="CLASSIFIED",
                    source="rule", lifecycle="ACTIVE", model_version="fx", prompt_version="v1",
                    metadata_={"taxonomy_version": "curriculum-v2", "primary_content_code": "MATH-ALGEBRA-FUNCTIONS", "evidence": []}))
            made[num] = {"question_id": str(q.id), "question_version_id": str(v.id), "correct": correct}
        await s.commit()
        return made


import uuid as _uuid

_SCHOOL_UUIDS: dict[str, str] = {}


def _school_uuid(name: str | None) -> str | None:
    if name is None:
        return None
    return _SCHOOL_UUIDS.setdefault(
        name, str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"phase15-school-{name}")))


def _ctx(user="prof_a", school="school-1", role="TEACHER"):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user, role=role,
                                    school_id=_school_uuid(school), scope_type="SCHOOL")


class Phase15Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(cls.factory)

        cls.made = cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls._ctx = _ctx()
        cls.app.dependency_overrides[get_current_authenticated_context] = lambda: cls._ctx
        cls.client = TestClient(cls.app)
        cls.vids = [cls.made[n]["question_version_id"] for n in (130, 131, 132, 133, 96, 97, 98)]

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, ctx):
        self.__class__._ctx = ctx
        self.app.dependency_overrides[get_current_authenticated_context] = lambda: ctx

    def _create(self, ids=None, title="Lista A", **kw):
        body = {"question_version_ids": ids or self.vids, "title": title,
                "answer_key_presentation": kw.pop("answer_key_presentation", "KEY_AT_END"), **kw}
        return self.client.post("/api/v1/question-bank/lists", json=body)

    # -- create + retrieve + order + fingerprint --
    def test_create_retrieve_preserves_order_and_fingerprint(self):
        self._as(_ctx("prof_order"))
        order = self.vids[::-1]
        r = self._create(order, title="Ordenada")
        self.assertEqual(r.status_code, 201)
        s = r.json()
        self.assertEqual(s["status"], "draft")
        self.assertEqual(s["question_count"], len(order))
        lid = s["id"]
        d = self.client.get(f"/api/v1/question-bank/lists/{lid}").json()
        self.assertEqual(d["question_version_ids"], order)
        self.assertEqual([i["question_version_id"] for i in d["items"]], order)
        self.assertEqual([i["position"] for i in d["items"]], list(range(1, len(order) + 1)))
        self.assertTrue(d["persisted"]["fingerprint_matches"])
        self.assertEqual(d["persisted"]["stored_fingerprint"], s["selection_fingerprint"])
        self.assertTrue(d["persisted"]["editable"])

    # -- persists on the reused Assessment tables, one version, ordered items --
    def test_persisted_on_assessment_tables(self):
        self._as(_ctx("prof_tables"))
        lid = self._create(title="Tab").json()["id"]

        from sqlalchemy.orm import selectinload

        async def _check():
            async with self.factory() as s:
                a = (await s.execute(
                    select(Assessment).where(Assessment.id == _uuid.UUID(lid))
                    .options(selectinload(Assessment.versions)))).scalar_one()
                self.assertEqual(a.material_type, "EXERCISE_LIST")
                v = a.versions[0]
                items = (await s.execute(select(AssessmentItem).where(
                    AssessmentItem.assessment_version_id == v.id).order_by(AssessmentItem.position))).scalars().all()
                self.assertEqual([str(it.question_version_id) for it in items], self.vids)
                self.assertEqual([it.position for it in items], list(range(1, len(self.vids) + 1)))
                # official answer key snapshotted per item
                self.assertTrue(all(it.frozen_correct_option_id is not None for it in items))
                self.assertTrue(all(it.answer_key_revision_id is not None for it in items))
        self.loop.run_until_complete(_check())

    # -- rejections --
    def test_validation_rules(self):
        self._as(_ctx("prof_val"))
        self.assertEqual(self._create([self.vids[0], self.vids[0]]).status_code, 422)
        self.assertEqual(self._create(["00000000-0000-0000-0000-000000000000"]).status_code, 422)
        self.assertEqual(self._create(title="   ").status_code, 422)
        self.assertEqual(self._create(activity_mode="PROVA").status_code, 422)

    # -- edit draft, block edit / delete on finalized --
    def test_lifecycle_draft_then_immutable(self):
        self._as(_ctx("prof_life"))
        lid = self._create(title="Ciclo").json()["id"]
        e = self.client.patch(f"/api/v1/question-bank/lists/{lid}",
                              json={"title": "Ciclo v2", "question_version_ids": self.vids[:3]})
        self.assertEqual(e.status_code, 200)
        self.assertEqual(e.json()["title"], "Ciclo v2")
        self.assertEqual(e.json()["question_count"], 3)
        f = self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        self.assertEqual(f.status_code, 200)
        self.assertEqual(f.json()["status"], "published")
        self.assertIsNotNone(f.json()["finalized_at"])
        # finalize is idempotent
        self.assertEqual(self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize").json()["status"], "published")
        self.assertEqual(self.client.patch(f"/api/v1/question-bank/lists/{lid}", json={"title": "x"}).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/v1/question-bank/lists/{lid}").status_code, 409)
        # retrieved order still exact after finalize
        d = self.client.get(f"/api/v1/question-bank/lists/{lid}").json()
        self.assertEqual(d["question_version_ids"], self.vids[:3])
        self.assertFalse(d["persisted"]["editable"])

    def test_delete_draft_allowed(self):
        self._as(_ctx("prof_del"))
        lid = self._create(title="Apagar").json()["id"]
        self.assertEqual(self.client.delete(f"/api/v1/question-bank/lists/{lid}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/v1/question-bank/lists/{lid}").status_code, 404)

    # -- ownership + multi-tenant isolation --
    def test_ownership_and_tenant_isolation(self):
        self._as(_ctx("prof_owner", "school-A"))
        lid = self._create(title="Minha").json()["id"]
        # a different teacher in a different school cannot see it
        self._as(_ctx("prof_intruder", "school-B"))
        self.assertEqual(self.client.get(f"/api/v1/question-bank/lists/{lid}").status_code, 403)
        self.assertEqual(self.client.patch(f"/api/v1/question-bank/lists/{lid}", json={"title": "hack"}).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v1/question-bank/lists/{lid}").status_code, 403)
        self.assertNotIn(lid, [i["id"] for i in self.client.get("/api/v1/question-bank/lists").json()["items"]])
        # a coordinator in the SAME school may view but not manage
        self._as(_ctx("coord_1", "school-A", role="COORDINATION"))
        self.assertEqual(self.client.get(f"/api/v1/question-bank/lists/{lid}").status_code, 200)
        self.assertIn(lid, [i["id"] for i in self.client.get("/api/v1/question-bank/lists").json()["items"]])
        self.assertEqual(self.client.patch(f"/api/v1/question-bank/lists/{lid}", json={"title": "no"}).status_code, 403)

    def test_my_lists_scoped_search_sort(self):
        self._as(_ctx("prof_list", "school-L"))
        for t in ("Alpha lista", "Beta lista", "Gamma"):
            self._create(title=t)
        page = self.client.get("/api/v1/question-bank/lists", params={"q": "lista", "order_by": "title", "order_direction": "asc"}).json()
        titles = [i["title"] for i in page["items"]]
        self.assertEqual(titles, ["Alpha lista", "Beta lista"])
        self.assertEqual(page["pagination"]["total"], 2)
        drafts = self.client.get("/api/v1/question-bank/lists", params={"status": "draft"}).json()
        self.assertEqual(drafts["pagination"]["total"], 3)

    # -- answer key comes from the official source, unchanged by list order --
    def test_answer_key_integrity_in_stored_list(self):
        self._as(_ctx("prof_ak"))
        lid = self._create(self.vids[::-1], title="AK").json()["id"]
        d = self.client.get(f"/api/v1/question-bank/lists/{lid}").json()
        for it in d["items"]:
            self.assertEqual(it["answer_key"]["correct_option_key"], ANSWERS[it["official_number"]])
            self.assertEqual(it["answer_key"]["source"], "official_answer_key")

    # -- no mutation of official data --
    def test_no_mutation_of_official_data(self):
        async def counts():
            async with self.factory() as s:
                return {k: await s.scalar(select(func.count()).select_from(m)) for k, m in {
                    "q": Question, "v": QuestionVersion, "o": QuestionOption,
                    "ake": AnswerKeyEntry, "pc": PedagogicalClassification, "cn": CatalogNode}.items()}
        before = self.loop.run_until_complete(counts())
        self._as(_ctx("prof_immut"))
        lid = self._create(title="Immut").json()["id"]
        self.client.patch(f"/api/v1/question-bank/lists/{lid}", json={"title": "Immut2"})
        self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        self.client.get(f"/api/v1/question-bank/lists/{lid}/export.pdf")
        self.client.get(f"/api/v1/question-bank/lists/{lid}/export.docx")
        self.assertEqual(before, self.loop.run_until_complete(counts()))

    # -- bounded retrieval (no per-question query) --
    def test_no_n_plus_1_on_retrieval(self):
        self._as(_ctx("prof_n1"))
        small = self._create(self.vids[:2], title="s").json()["id"]
        big = self._create(self.vids, title="b").json()["id"]

        async def run():
            n = {"c": 0}

            @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
            def _c(*_a):  # noqa: ANN001
                n["c"] += 1
            try:
                from agente_ia_edu.services.question_list_store import QuestionListStore, Requester
                async with self.factory() as s:
                    st = QuestionListStore(s)
                    req = Requester(external_user_id="prof_n1", school_id=_school_uuid("school-1"), role="TEACHER")
                    n["c"] = 0
                    await st.get_definition(__import__("uuid").UUID(small), requester=req)
                    q2 = n["c"]
                    n["c"] = 0
                    await st.get_definition(__import__("uuid").UUID(big), requester=req)
                    q7 = n["c"]
                return q2, q7
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", _c)

        q2, q7 = self.loop.run_until_complete(run())
        self.assertLessEqual(q7, q2 + 1)   # constant, not proportional to N
        self.assertLessEqual(q7, 10)

    # -- PDF + DOCX from the SAME definition --
    def test_pdf_and_docx_export_same_list(self):
        self._as(_ctx("prof_exp"))
        lid = self._create(self.vids[:4], title="Prova X", instructions="Sem consulta.",
                           answer_key_presentation="KEY_AND_RESOLUTION_AT_END", resolution_style="STEP_BY_STEP").json()["id"]
        self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        pdf = self.client.get(f"/api/v1/question-bank/lists/{lid}/export.pdf")
        docx = self.client.get(f"/api/v1/question-bank/lists/{lid}/export.docx")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-"))
        self.assertGreater(len(pdf.content), 1000)
        self.assertEqual(docx.status_code, 200)
        self.assertIn("wordprocessingml", docx.headers["content-type"])
        self.assertTrue(docx.content.startswith(b"PK"))
        # DOCX carries the title, an official statement and the gabarito heading
        with zipfile.ZipFile(io.BytesIO(docx.content)) as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8", "ignore")
        self.assertIn("Prova X", doc_xml)
        self.assertIn("Enunciado oficial 2024 Q130", doc_xml)
        self.assertIn("Gabarito", doc_xml)
        # resolution has no official source -> the "unavailable" string, never invented text
        self.assertIn("Resolução não disponível.", doc_xml)

    def test_export_no_answer_key_when_mode_none(self):
        self._as(_ctx("prof_nokey"))
        lid = self._create(self.vids[:3], title="Sem gabarito", answer_key_presentation="NONE").json()["id"]
        with zipfile.ZipFile(io.BytesIO(self.client.get(f"/api/v1/question-bank/lists/{lid}/export.docx").content)) as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8", "ignore")
        self.assertNotIn("Gabarito", doc_xml)


if __name__ == "__main__":
    unittest.main()
