"""PHASE 16 - activity publication & assignment backend tests.

TestClient + in-memory SQLite. A distribution (``activity_assignments``) turns a
PUBLISHED question list (PHASE 15 Assessment/AssessmentVersion/AssessmentItem)
into an activity targeted at a STUDENT or a CLASS. It is pure distribution: no
Attempt / StudentResponse / answer / grade is created here (that is PHASE 17).

Covered (spec s20):
  1  create a distribution from a published list
  2  only a PUBLISHED list is distributable (draft -> 409)
  3  only an authorised manager (owner / same-school privileged) may distribute
  4  cross-tenant distribution is blocked
  5  a targeted STUDENT sees the activity
  6  a non-targeted student does not
  7  a CLASS target resolves recipients via user_school_links
  8  only a CURRENT (active) class link counts - a deactivated link is ignored
  9  available_from in the future -> AGUARDANDO, cannot start
  10 due_at in the past -> ENCERRADA
  11 status ACTIVE -> CLOSED via PATCH
  12 cancellation (DELETE) -> CANCELLED, hidden from the student
  13 duplicate control: same version + target + ACTIVE -> idempotent replay
  14 the distribution preserves assessment_id + assessment_version_id
  15 selection_fingerprint + question_count are frozen snapshots
  16 no official question table is mutated
  17 the official gabarito is not mutated
  18 no N+1 in the student activities query
  19 multi-tenant isolation on GET / PATCH / DELETE by id
  20 direct-by-id access respects the existing authorisation rule
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import datetime, timedelta, timezone

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
from agente_ia_edu.db.models.admin import UserSchoolLink
from agente_ia_edu.db.models.assessments import ActivityAssignment
from agente_ia_edu.identity import AuthenticatedUserContext

ANSWERS = {130: "C", 131: "A", 132: "E", 133: "B", 96: "D", 97: "A", 98: "B"}

_SCHOOL_UUIDS: dict[str, str] = {}


def _school_uuid(name: str | None) -> str | None:
    if name is None:
        return None
    return _SCHOOL_UUIDS.setdefault(
        name, str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"phase16-school-{name}")))


def _ctx(user="prof_a", school="school-1", role="TEACHER"):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user, role=role,
                                    school_id=_school_uuid(school), scope_type="SCHOOL")


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
            made[num] = {"question_id": str(q.id), "question_version_id": str(v.id), "correct": correct}
        await s.commit()
        return made


class Phase16Tests(unittest.TestCase):
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

    # -- helpers ------------------------------------------------------

    def _as(self, ctx):
        self.__class__._ctx = ctx
        self.app.dependency_overrides[get_current_authenticated_context] = lambda: ctx

    def _published_list(self, ids=None, title="Lista P16", school="school-1", owner="prof_a", **kw):
        self._as(_ctx(owner, school))
        body = {"question_version_ids": ids or self.vids, "title": title,
                "answer_key_presentation": kw.pop("answer_key_presentation", "KEY_AT_END"), **kw}
        r = self.client.post("/api/v1/question-bank/lists", json=body)
        self.assertEqual(r.status_code, 201, r.text)
        lid = r.json()["id"]
        f = self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        self.assertEqual(f.status_code, 200, f.text)
        return lid, r.json()["selection_fingerprint"]

    def _assign(self, lid, target_type, target_id, **kw):
        return self.client.post(f"/api/v1/question-bank/lists/{lid}/assignments",
                                json={"target_type": target_type, "target_id": target_id, **kw})

    def _seed_student_link(self, external_user_id, classroom, school="school-1", active=True):
        async def _do():
            async with self.factory() as s:
                s.add(UserSchoolLink(
                    external_user_id=external_user_id, school_id=_uuid.UUID(_school_uuid(school)),
                    role="STUDENT", scope_type="CLASSROOM", scope_external_id=classroom, active=active))
                await s.commit()
        self.loop.run_until_complete(_do())

    # -- 1 / 14 / 15  create + canonical identity + frozen snapshot --
    def test_create_from_published_preserves_identity_and_snapshot(self):
        lid, fp = self._published_list(title="Identidade")
        self._as(_ctx("prof_a", "school-1"))
        r = self._assign(lid, "CLASS", "turma-ident")
        self.assertEqual(r.status_code, 201, r.text)
        a = r.json()
        self.assertEqual(a["status"], "ACTIVE")
        self.assertEqual(a["list_id"], lid)
        self.assertEqual(a["assessment_id"], lid)
        self.assertTrue(a["assessment_version_id"])
        self.assertEqual(a["selection_fingerprint"], fp)          # frozen
        self.assertEqual(a["question_count"], len(self.vids))     # frozen
        self.assertEqual(a["answer_key_presentation"], "KEY_AT_END")
        # the row really references the published version
        async def _check():
            async with self.factory() as s:
                row = (await s.execute(select(ActivityAssignment).where(
                    ActivityAssignment.id == _uuid.UUID(a["id"])))).scalar_one()
                self.assertEqual(str(row.assessment_id), lid)
                self.assertEqual(str(row.assessment_version_id), a["assessment_version_id"])
                self.assertEqual(row.question_count, len(self.vids))
        self.loop.run_until_complete(_check())

    # -- 2  only a PUBLISHED list is distributable --
    def test_draft_list_cannot_be_distributed(self):
        self._as(_ctx("prof_draft", "school-1"))
        lid = self.client.post("/api/v1/question-bank/lists", json={
            "question_version_ids": self.vids, "title": "Rascunho"}).json()["id"]
        r = self._assign(lid, "STUDENT", "aluno_x")
        self.assertEqual(r.status_code, 409)
        # empty list guard also holds after finalize is impossible on empty -> n/a

    # -- 3  authorised manager only --
    def test_only_authorised_manager_distributes(self):
        lid, _ = self._published_list(title="Autor", school="school-3", owner="prof_owner3")
        # another teacher in the same school is NOT the owner and not privileged
        self._as(_ctx("prof_other3", "school-3", role="TEACHER"))
        self.assertIn(self._assign(lid, "STUDENT", "z").status_code, (403, 404))
        # a coordinator in the same school MAY distribute (spec s10, reused auth)
        self._as(_ctx("coord3", "school-3", role="COORDINATION"))
        self.assertEqual(self._assign(lid, "CLASS", "turma-coord").status_code, 201)
        # the owner can too
        self._as(_ctx("prof_owner3", "school-3"))
        self.assertEqual(self._assign(lid, "STUDENT", "aluno3").status_code, 201)

    # -- 4 / 19  cross-tenant blocked --
    def test_cross_tenant_blocked(self):
        lid, _ = self._published_list(title="Tenant", school="school-A", owner="prof_A")
        self._as(_ctx("prof_A", "school-A"))
        aid = self._assign(lid, "CLASS", "turma-A").json()["id"]
        # a teacher in another school cannot distribute, list, get, patch or delete
        self._as(_ctx("prof_B", "school-B"))
        self.assertIn(self._assign(lid, "STUDENT", "x").status_code, (403, 404))
        self.assertIn(self.client.get(f"/api/v1/question-bank/lists/{lid}/assignments").status_code, (403, 404))
        self.assertIn(self.client.get(f"/api/v1/question-bank/assignments/{aid}").status_code, (403, 404))
        self.assertIn(self.client.patch(f"/api/v1/question-bank/assignments/{aid}",
                                        json={"status": "CLOSED"}).status_code, (403, 404))
        self.assertIn(self.client.delete(f"/api/v1/question-bank/assignments/{aid}").status_code, (403, 404))

    # -- 5 / 6  student visibility (STUDENT target) --
    def test_targeted_student_sees_non_targeted_does_not(self):
        lid, _ = self._published_list(title="Visib", school="school-1", owner="prof_a")
        self._as(_ctx("prof_a", "school-1"))
        self._assign(lid, "STUDENT", "stu_target")
        self._seed_student_link("stu_target", "turma-none", school="school-1")
        self._seed_student_link("stu_bystander", "turma-none", school="school-1")

        self._as(_ctx("stu_target", "school-1", role="STUDENT"))
        items = self.client.get("/api/v1/student/activities").json()["items"]
        self.assertIn(lid, [i["list_id"] for i in items])

        self._as(_ctx("stu_bystander", "school-1", role="STUDENT"))
        items = self.client.get("/api/v1/student/activities").json()["items"]
        self.assertNotIn(lid, [i["list_id"] for i in items])

    # -- 7 / 8  CLASS target resolves via user_school_links; inactive link ignored --
    def test_class_target_membership_and_currency(self):
        lid, _ = self._published_list(title="Turma", school="school-1", owner="prof_a")
        self._as(_ctx("prof_a", "school-1"))
        self._assign(lid, "CLASS", "turma-9A")
        self._seed_student_link("stu_member", "turma-9A", school="school-1", active=True)
        self._seed_student_link("stu_left", "turma-9A", school="school-1", active=False)

        self._as(_ctx("stu_member", "school-1", role="STUDENT"))
        self.assertIn(lid, [i["list_id"] for i in self.client.get("/api/v1/student/activities").json()["items"]])

        self._as(_ctx("stu_left", "school-1", role="STUDENT"))
        self.assertNotIn(lid, [i["list_id"] for i in self.client.get("/api/v1/student/activities").json()["items"]])

    # -- 9  available_from in the future --
    def test_available_from_future_is_waiting(self):
        lid, _ = self._published_list(title="Futuro", school="school-1", owner="prof_a")
        self._as(_ctx("prof_a", "school-1"))
        future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        a = self._assign(lid, "STUDENT", "stu_future", available_from=future).json()
        self.assertEqual(a["availability"], "AGUARDANDO")
        self._seed_student_link("stu_future", "t", school="school-1")
        self._as(_ctx("stu_future", "school-1", role="STUDENT"))
        d = self.client.get(f"/api/v1/student/activities/{a['id']}").json()
        self.assertEqual(d["availability"], "AGUARDANDO")
        self.assertFalse(d["can_start"])
        self.assertNotIn("attempt", d)  # PHASE 17 not anticipated

    # -- 10  due_at in the past --
    def test_due_at_past_is_closed_for_new_execution(self):
        lid, _ = self._published_list(title="Prazo", school="school-1", owner="prof_a")
        self._as(_ctx("prof_a", "school-1"))
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        older = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        a = self._assign(lid, "STUDENT", "stu_past", available_from=older, due_at=past).json()
        self.assertEqual(a["availability"], "ENCERRADA")

    # -- 11  status ACTIVE -> CLOSED via PATCH --
    def test_patch_status_active_to_closed(self):
        lid, _ = self._published_list(title="Status", school="school-1", owner="prof_a")
        self._as(_ctx("prof_a", "school-1"))
        aid = self._assign(lid, "CLASS", "turma-status").json()["id"]
        r = self.client.patch(f"/api/v1/question-bank/assignments/{aid}", json={"status": "CLOSED"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "CLOSED")
        self.assertEqual(r.json()["availability"], "ENCERRADA")
        # an invalid status value is rejected
        self.assertEqual(self.client.patch(f"/api/v1/question-bank/assignments/{aid}",
                                           json={"status": "COMPLETED"}).status_code, 422)

    # -- 12  cancellation hides it from the student --
    def test_cancel_hides_from_student(self):
        lid, _ = self._published_list(title="Cancela", school="school-1", owner="prof_a")
        self._as(_ctx("prof_a", "school-1"))
        aid = self._assign(lid, "STUDENT", "stu_cancel").json()["id"]
        self._seed_student_link("stu_cancel", "t", school="school-1")
        self._as(_ctx("stu_cancel", "school-1", role="STUDENT"))
        self.assertIn(lid, [i["list_id"] for i in self.client.get("/api/v1/student/activities").json()["items"]])

        self._as(_ctx("prof_a", "school-1"))
        d = self.client.delete(f"/api/v1/question-bank/assignments/{aid}")
        self.assertEqual(d.status_code, 200)
        self.assertEqual(d.json()["status"], "CANCELLED")

        self._as(_ctx("stu_cancel", "school-1", role="STUDENT"))
        self.assertNotIn(lid, [i["list_id"] for i in self.client.get("/api/v1/student/activities").json()["items"]])
        self.assertEqual(self.client.get(f"/api/v1/student/activities/{aid}").status_code, 404)

    # -- 13  duplicate control / idempotency --
    def test_duplicate_control_idempotent(self):
        lid, _ = self._published_list(title="Dup", school="school-1", owner="prof_a")
        self._as(_ctx("prof_a", "school-1"))
        first = self._assign(lid, "CLASS", "turma-dup")
        self.assertEqual(first.status_code, 201)
        again = self._assign(lid, "CLASS", "turma-dup")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.headers.get("x-idempotent-replay"), "true")
        self.assertEqual(again.json()["id"], first.json()["id"])
        # after cancelling, the same target can be distributed again
        self.client.delete(f"/api/v1/question-bank/assignments/{first.json()['id']}")
        third = self._assign(lid, "CLASS", "turma-dup")
        self.assertEqual(third.status_code, 201)
        self.assertNotEqual(third.json()["id"], first.json()["id"])

    # -- 16 / 17  no official data / gabarito mutation --
    def test_no_mutation_of_official_data(self):
        async def counts():
            async with self.factory() as s:
                return {k: await s.scalar(select(func.count()).select_from(m)) for k, m in {
                    "q": Question, "v": QuestionVersion, "o": QuestionOption,
                    "ake": AnswerKeyEntry, "akr": AnswerKeyRevision,
                    "pc": PedagogicalClassification, "cn": CatalogNode, "bq": BookletQuestion}.items()}
        before = self.loop.run_until_complete(counts())
        lid, _ = self._published_list(title="Immut", school="school-1", owner="prof_a")
        self._as(_ctx("prof_a", "school-1"))
        aid = self._assign(lid, "CLASS", "turma-immut").json()["id"]
        self.client.patch(f"/api/v1/question-bank/assignments/{aid}", json={"status": "CLOSED"})
        self.client.delete(f"/api/v1/question-bank/assignments/{aid}")
        self.assertEqual(before, self.loop.run_until_complete(counts()))

    # -- 18  no N+1 in the student activities query --
    def test_no_n_plus_1_student_activities(self):
        # distribute three different lists to the same class
        lids = []
        for i in range(3):
            lid, _ = self._published_list(title=f"N1-{i}", school="school-1", owner="prof_a")
            self._as(_ctx("prof_a", "school-1"))
            self._assign(lid, "CLASS", "turma-n1")
            lids.append(lid)
        self._seed_student_link("stu_n1", "turma-n1", school="school-1")

        async def run():
            from agente_ia_edu.services.activity_assignment_store import ActivityAssignmentStore
            from agente_ia_edu.services.question_list_store import Requester
            n = {"c": 0}

            @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
            def _c(*_a):  # noqa: ANN001
                n["c"] += 1
            try:
                async with self.factory() as s:
                    st = ActivityAssignmentStore(s)
                    req = Requester(external_user_id="stu_n1", school_id=_school_uuid("school-1"), role="STUDENT")
                    n["c"] = 0
                    rows = await st.student_activities(requester=req)
                    return len(rows), n["c"]
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", _c)

        count, queries = self.loop.run_until_complete(run())
        self.assertGreaterEqual(count, 3)
        self.assertLessEqual(queries, 5)   # scope + assignments + assessments batch, constant

    # -- 20  direct-by-id respects the existing authorisation rule --
    def test_direct_by_id_requires_authorisation(self):
        lid, _ = self._published_list(title="ById", school="school-1", owner="prof_a")
        self._as(_ctx("prof_a", "school-1"))
        aid = self._assign(lid, "CLASS", "turma-byid").json()["id"]
        # a user with no relationship to the list cannot fetch it by id
        self._as(_ctx("nobody", "school-Z", role="TEACHER"))
        self.assertIn(self.client.get(f"/api/v1/question-bank/assignments/{aid}").status_code, (403, 404))
        # an unknown id is 404
        self._as(_ctx("prof_a", "school-1"))
        self.assertEqual(self.client.get(
            f"/api/v1/question-bank/assignments/{_uuid.uuid4()}").status_code, 404)

    # -- GRADE distribution is explicitly refused, not silently improvised --
    def test_grade_target_refused(self):
        lid, _ = self._published_list(title="Serie", school="school-1", owner="prof_a")
        self._as(_ctx("prof_a", "school-1"))
        r = self._assign(lid, "GRADE", "9ano")
        self.assertEqual(r.status_code, 422)
        self.assertIn("série", r.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
