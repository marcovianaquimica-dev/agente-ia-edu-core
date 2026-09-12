"""PHASE 17 - student activity player backend tests.

TestClient + in-memory SQLite. Execution state only: ActivityAttempt (NOT_STARTED
/ IN_PROGRESS / COMPLETED) + ActivityAnswer (current choice per question, UNIQUE
per attempt+question). NO correction / score / percentage / ranking anywhere.

Covered (spec s20 + the s22 integration flow):
  1  start an activity -> IN_PROGRESS attempt created
  2  start is idempotent (same attempt, no duplicate)
  3  an unavailable activity cannot be started
  4  a non-recipient student is refused
  5  create an answer
  6  update an answer (A -> C -> E leaves only E)
  7  UNIQUE(attempt, question_version) - never two rows for one question
  8  answers are retrievable via GET state
  9  the attempt stays IN_PROGRESS while answering
  10 a fully-answered activity finalises -> COMPLETED
  11 an incomplete finalisation is rejected by the BACKEND (409 + pending)
  12 no change is accepted after COMPLETED
  13 autosave is idempotent (same PUT twice -> one row, same state)
  14 tenant / recipient isolation on every operation
  15 the frozen PHASE 16 snapshot (version + order + count + fingerprint) is preserved
  16 the official bank is not mutated
  17 (integration) the full s22 walk incl. reload-preserves-answers + final DB state
  18 the student state never exposes the answer key
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
from agente_ia_edu.db.models.assessments import ActivityAnswer, ActivityAttempt
from agente_ia_edu.identity import AuthenticatedUserContext

ANSWERS = {130: "C", 131: "A", 132: "E", 133: "B", 96: "D", 97: "A", 98: "B", 99: "C"}

_SCHOOL_UUIDS: dict[str, str] = {}


def _school_uuid(name: str | None) -> str | None:
    if name is None:
        return None
    return _SCHOOL_UUIDS.setdefault(
        name, str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"phase17-school-{name}")))


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


class Phase17Tests(unittest.TestCase):
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
        cls.vids = [cls.made[n]["question_version_id"] for n in (130, 131, 132, 133, 96)]

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    # -- helpers ------------------------------------------------------

    def _as(self, ctx):
        self.__class__._ctx = ctx
        self.app.dependency_overrides[get_current_authenticated_context] = lambda: ctx

    def _seed_link(self, uid, classroom, school="school-1", active=True):
        async def _do():
            async with self.factory() as s:
                s.add(UserSchoolLink(
                    external_user_id=uid, school_id=_uuid.UUID(_school_uuid(school)),
                    role="STUDENT", scope_type="CLASSROOM", scope_external_id=classroom, active=active))
                await s.commit()
        self.loop.run_until_complete(_do())

    def _distributed_activity(self, ids=None, *, school="school-1", owner="prof_a",
                              classroom="turma-A", **assign_kw):
        self._as(_ctx(owner, school))
        r = self.client.post("/api/v1/question-bank/lists", json={
            "question_version_ids": ids or self.vids, "title": "Atividade P17",
            "answer_key_presentation": "KEY_AT_END"})
        lid = r.json()["id"]
        fp = r.json()["selection_fingerprint"]
        self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        kw = {"available_from": "2000-01-01T00:00:00Z", **assign_kw}
        a = self.client.post(f"/api/v1/question-bank/lists/{lid}/assignments",
                             json={"target_type": "CLASS", "target_id": classroom, **kw})
        return lid, fp, a.json()["id"]

    def _student(self, uid="stu_a", school="school-1"):
        self._as(_ctx(uid, school, role="STUDENT"))

    def _start(self, aid):
        return self.client.post(f"/api/v1/student/activities/{aid}/attempt")

    def _state(self, aid):
        return self.client.get(f"/api/v1/student/activities/{aid}/attempt")

    def _answer(self, aid, qvid, key):
        return self.client.put(
            f"/api/v1/student/activities/{aid}/attempt/answers/{qvid}",
            json={"selected_option": key})

    def _complete(self, aid):
        return self.client.post(f"/api/v1/student/activities/{aid}/attempt/complete")

    def _opts_by_vid(self, state):
        return {q["question_version_id"]: [o["key"] for o in q["options"]] for q in state["questions"]}

    # -- 1 / 15 / 18 ------------------------------------------------
    def test_start_creates_in_progress_attempt_with_frozen_order(self):
        lid, fp, aid = self._distributed_activity(classroom="turma-1")
        self._seed_link("stu_start", "turma-1")
        self._student("stu_start")
        r = self._start(aid)
        self.assertEqual(r.status_code, 200, r.text)
        st = r.json()
        self.assertEqual(st["status"], "IN_PROGRESS")
        self.assertEqual(st["attempt"]["status"], "IN_PROGRESS")
        self.assertIsNotNone(st["attempt"]["started_at"])
        self.assertEqual(st["total_questions"], len(self.vids))
        self.assertEqual([q["question_version_id"] for q in st["questions"]], self.vids)  # frozen order
        self.assertEqual(st["activity"]["selection_fingerprint"], fp)
        self.assertEqual(st["activity"]["question_count"], len(self.vids))
        self.assertFalse(st["answer_key_visible"])
        for q in st["questions"]:
            for o in q["options"]:
                self.assertEqual(set(o), {"key", "position", "text"})  # NO is_valid_option

    # -- 2 --------------------------------------------------------
    def test_start_is_idempotent(self):
        lid, fp, aid = self._distributed_activity(classroom="turma-2")
        self._seed_link("stu_idem", "turma-2")
        self._student("stu_idem")
        a1 = self._start(aid).json()
        a2 = self._start(aid).json()
        self.assertEqual(a1["attempt"]["id"], a2["attempt"]["id"])

        async def _count():
            async with self.factory() as s:
                return int(await s.scalar(select(func.count()).select_from(ActivityAttempt)
                                          .where(ActivityAttempt.assignment_id == _uuid.UUID(aid))))
        self.assertEqual(self.loop.run_until_complete(_count()), 1)

    # -- 3 --------------------------------------------------------
    def test_unavailable_activity_cannot_start(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        older = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        lid, fp, aid = self._distributed_activity(classroom="turma-3",
                                                  available_from=older, due_at=past)
        self._seed_link("stu_closed", "turma-3")
        self._student("stu_closed")
        r = self._start(aid)
        self.assertEqual(r.status_code, 409)
        self.assertIn("availability", r.json()["detail"])

    # -- 4 / 14 -------------------------------------------------
    def test_non_recipient_is_refused_everywhere(self):
        lid, fp, aid = self._distributed_activity(classroom="turma-4")
        self._seed_link("stu_ok4", "turma-4")
        self._seed_link("stu_bad4", "turma-outra")
        self._student("stu_ok4")
        qv = self._start(aid).json()["questions"][0]["question_version_id"]
        # a student in another class
        self._student("stu_bad4")
        self.assertEqual(self._start(aid).status_code, 403)
        self.assertEqual(self._state(aid).status_code, 403)
        self.assertEqual(self._answer(aid, qv, "A").status_code, 403)
        self.assertEqual(self._complete(aid).status_code, 403)
        # a teacher from another tenant
        self._as(_ctx("prof_z", "school-Z"))
        self.assertIn(self._state(aid).status_code, (403, 404))

    # -- 5 / 6 / 7 / 13 --------------------------------------
    def test_answer_create_update_single_row(self):
        lid, fp, aid = self._distributed_activity(classroom="turma-5")
        self._seed_link("stu_ans", "turma-5")
        self._student("stu_ans")
        st = self._start(aid).json()
        qv = st["questions"][0]["question_version_id"]
        keys = self._opts_by_vid(st)[qv]

        r = self._answer(aid, qv, keys[0])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["answered_count"], 1)
        # A -> C -> E : only the last survives
        self._answer(aid, qv, keys[2])
        r = self._answer(aid, qv, keys[4])
        self.assertEqual(r.json()["selected_option"], keys[4])
        # idempotent repeat
        r2 = self._answer(aid, qv, keys[4])
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["answered_count"], 1)

        async def _rows():
            async with self.factory() as s:
                aid_uuid = _uuid.UUID(aid)
                att = (await s.execute(select(ActivityAttempt.id).where(
                    ActivityAttempt.assignment_id == aid_uuid))).scalar_one()
                rows = (await s.execute(select(ActivityAnswer).where(
                    ActivityAnswer.attempt_id == att))).scalars().all()
                return [(str(a.question_version_id), a.selected_option_key) for a in rows]
        rows = self.loop.run_until_complete(_rows())
        self.assertEqual(rows, [(qv, keys[4])])  # exactly one row, current choice

    # -- 8 / 9 --------------------------------------------------
    def test_state_returns_answers_and_stays_in_progress(self):
        lid, fp, aid = self._distributed_activity(classroom="turma-8")
        self._seed_link("stu_state", "turma-8")
        self._student("stu_state")
        st = self._start(aid).json()
        ov = self._opts_by_vid(st)
        self._answer(aid, self.vids[0], ov[self.vids[0]][1])
        self._answer(aid, self.vids[2], ov[self.vids[2]][3])
        got = self._state(aid).json()
        self.assertEqual(got["status"], "IN_PROGRESS")
        self.assertEqual(got["answered_count"], 2)
        self.assertEqual(got["pending_count"], len(self.vids) - 2)
        sel = {q["position"]: q["selected_option"] for q in got["questions"]}
        self.assertEqual(sel[1], ov[self.vids[0]][1])
        self.assertIsNone(sel[2])
        self.assertEqual(sel[3], ov[self.vids[2]][3])

    # -- 11 ----------------------------------------------------
    def test_incomplete_finalisation_rejected_by_backend(self):
        lid, fp, aid = self._distributed_activity(classroom="turma-11")
        self._seed_link("stu_inc", "turma-11")
        self._student("stu_inc")
        st = self._start(aid).json()
        ov = self._opts_by_vid(st)
        self._answer(aid, self.vids[0], ov[self.vids[0]][0])
        r = self._complete(aid)
        self.assertEqual(r.status_code, 409)
        d = r.json()["detail"]
        self.assertEqual(d["pending_count"], len(self.vids) - 1)
        self.assertEqual(d["pending_positions"], list(range(2, len(self.vids) + 1)))
        # still IN_PROGRESS, nothing completed
        self.assertEqual(self._state(aid).json()["status"], "IN_PROGRESS")

    # -- 10 / 12 --------------------------------------------
    def test_complete_then_immutable(self):
        lid, fp, aid = self._distributed_activity(classroom="turma-10")
        self._seed_link("stu_done", "turma-10")
        self._student("stu_done")
        st = self._start(aid).json()
        ov = self._opts_by_vid(st)
        for vid in self.vids:
            self._answer(aid, vid, ov[vid][0])
        r = self._complete(aid)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "COMPLETED")
        self.assertIsNotNone(r.json()["attempt"]["completed_at"])
        # no more edits
        self.assertEqual(self._answer(aid, self.vids[0], ov[self.vids[0]][2]).status_code, 409)
        # complete again is a no-op returning the final state
        self.assertEqual(self._complete(aid).json()["status"], "COMPLETED")
        # start again does not reopen / duplicate
        again = self._start(aid)
        self.assertEqual(again.json()["status"], "COMPLETED")

    # -- 16 --------------------------------------------------
    def test_official_bank_not_mutated(self):
        async def counts():
            async with self.factory() as s:
                return {k: await s.scalar(select(func.count()).select_from(m)) for k, m in {
                    "q": Question, "v": QuestionVersion, "o": QuestionOption,
                    "bq": BookletQuestion, "ake": AnswerKeyEntry,
                    "pc": PedagogicalClassification, "cn": CatalogNode}.items()}
        before = self.loop.run_until_complete(counts())
        lid, fp, aid = self._distributed_activity(classroom="turma-16")
        self._seed_link("stu_immut", "turma-16")
        self._student("stu_immut")
        st = self._start(aid).json()
        ov = self._opts_by_vid(st)
        for vid in self.vids:
            self._answer(aid, vid, ov[vid][1])
        self._complete(aid)
        self.assertEqual(before, self.loop.run_until_complete(counts()))

    # -- 17: the s22 integration walk + final DB state -----
    def test_full_integration_flow_with_reload(self):
        lid, fp, aid = self._distributed_activity(ids=self.vids, classroom="turma-flow")
        self._seed_link("stu_flow", "turma-flow")
        self._student("stu_flow")
        st = self._start(aid).json()
        v = self.vids
        ov = self._opts_by_vid(st)

        self._answer(aid, v[0], ov[v[0]][0])                 # Q1 -> A
        self._answer(aid, v[1], ov[v[1]][1])                 # Q2 -> B
        # skip Q3
        self._answer(aid, v[3], ov[v[3]][2])                 # Q4 -> C
        self._answer(aid, v[2], ov[v[2]][3])                 # back to Q3 -> D
        self._answer(aid, v[0], ov[v[0]][4])                 # change Q1 A -> E

        # "reload": a fresh state request must preserve every current choice
        reloaded = self._state(aid).json()
        sel = {q["position"]: q["selected_option"] for q in reloaded["questions"]}
        self.assertEqual(sel[1], ov[v[0]][4])
        self.assertEqual(sel[2], ov[v[1]][1])
        self.assertEqual(sel[3], ov[v[2]][3])
        self.assertEqual(sel[4], ov[v[3]][2])
        self.assertIsNone(sel[5])
        self.assertEqual(reloaded["answered_count"], 4)

        # finish the rest, finalise
        self._answer(aid, v[4], ov[v[4]][0])
        done = self._complete(aid).json()
        self.assertEqual(done["status"], "COMPLETED")

        async def _db():
            async with self.factory() as s:
                att = (await s.execute(select(ActivityAttempt).where(
                    ActivityAttempt.assignment_id == _uuid.UUID(aid)))).scalar_one()
                ans = (await s.execute(select(ActivityAnswer).where(
                    ActivityAnswer.attempt_id == att.id))).scalars().all()
                return att.status, att.completed_at, len(ans), sorted(a.selected_option_key for a in ans)
        status, completed_at, n_ans, keys = self.loop.run_until_complete(_db())
        self.assertEqual(status, "COMPLETED")
        self.assertIsNotNone(completed_at)
        self.assertEqual(n_ans, len(v))                       # one row per question, no duplicates
        self.assertNotIn(None, keys)

    # -- 18: no answer key path anywhere in the player payload
    def test_state_never_carries_answer_key(self):
        lid, fp, aid = self._distributed_activity(classroom="turma-key")
        self._seed_link("stu_key", "turma-key")
        self._student("stu_key")
        st = self._start(aid).json()
        # explicit safety flag is allowed; nothing that reveals the key is
        blob = repr(st).lower().replace("'answer_key_visible': false", "")
        for needle in ("is_valid_option", "correct_option", "correct_answer",
                       "answer_key", "gabarito", "frozen_correct"):
            self.assertNotIn(needle, blob)
        self.assertIs(st["answer_key_visible"], False)

    # -- performance: no N+1 on state for a bigger activity
    def test_no_n_plus_1_on_state(self):
        big = [self.made[n]["question_version_id"] for n in (130, 131, 132, 133, 96, 97, 98, 99)]
        lid, fp, aid = self._distributed_activity(ids=big, classroom="turma-n1")
        self._seed_link("stu_n1", "turma-n1")
        self._student("stu_n1")
        self._start(aid)

        async def run():
            from agente_ia_edu.services.activity_player_store import ActivityPlayerStore
            from agente_ia_edu.services.question_list_store import Requester
            n = {"c": 0}

            @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
            def _c(*_a):  # noqa: ANN001
                n["c"] += 1
            try:
                async with self.factory() as s:
                    store = ActivityPlayerStore(s)
                    req = Requester(external_user_id="stu_n1", school_id=_school_uuid("school-1"), role="STUDENT")
                    n["c"] = 0
                    await store.get_state(_uuid.UUID(aid), requester=req)
                    return n["c"]
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", _c)

        q = self.loop.run_until_complete(run())
        self.assertLessEqual(q, 12)  # bounded, independent of question count


if __name__ == "__main__":
    unittest.main()
