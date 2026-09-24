"""PHASE 17 - targeted branch coverage for ActivityPlayerStore.

Overnight service-layer coverage campaign - this file's zone is EXACTLY
``src/agente_ia_edu/services/activity_player_store.py``. It does not touch
``tests/test_phase17_student_activity_player.py`` (shared file, other
sessions may be editing it concurrently) - fixture patterns (``create_app()``,
TestClient, ``get_current_authenticated_context`` / ``get_session_factory``
overrides, the official-bank seed shape) are copied/adapted here so this file
is self-contained.

Uses ``expire_on_commit=True`` (the async_sessionmaker default, and what
``create_session_factory()`` uses in production) so any MissingGreenlet /
stale-attribute bug on post-commit reads would actually surface here.

Covers (see activity_player_store.py):
  1  start(): resume an IN_PROGRESS attempt whose started_at is None
  2  start(): the "status != IN_PROGRESS" defensive branch (documented, see
     test docstring - the DB CHECK constraint allows a NOT_STARTED row even
     though the store itself never persists one)
  3  start(): a version with zero items -> 409
  4  start(): a genuine UNIQUE(assignment_id, student) race -> IntegrityError
     -> rollback, no duplicate row
  5  _state(): current_position fallback computed from pending_positions
     when there is no attempt yet (NOT_STARTED)
  6  save_answer(): no attempt yet -> 409
  7  save_answer(): availability window closed after start -> 409
  8  save_answer(): question_version_id not in the frozen item list -> 422
  9  save_answer(): a genuine UNIQUE(attempt, question_version) race ->
     IntegrityError -> rollback -> retry as UPDATE
  10 set_current_position(): success / not-in-progress / out-of-range
  11 complete(): no attempt yet -> 409
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
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
    Question,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
)
from agente_ia_edu.db.models.admin import UserSchoolLink
from agente_ia_edu.db.models.assessments import (
    ActivityAnswer,
    ActivityAssignment,
    ActivityAttempt,
    Assessment,
    AssessmentVersion,
)
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.activity_player_store import (
    ActivityPlayerStore,
    PlayerError,
    PlayerStateError,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_NOT_STARTED,
)
from agente_ia_edu.services.question_list_store import LIST_MATERIAL_TYPE, Requester

ANSWERS = {130: "C", 131: "A", 132: "E", 133: "B", 96: "D"}

_SCHOOL_UUIDS: dict[str, str] = {}


def _school_uuid(name: str | None) -> str | None:
    if name is None:
        return None
    return _SCHOOL_UUIDS.setdefault(
        name, str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"apsc-school-{name}")))


def _ctx(user="prof_a", school="school-1", role="TEACHER"):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user, role=role,
                                    school_id=_school_uuid(school), scope_type="SCHOOL")


def _requester(user="stu_a", school="school-1", role="STUDENT"):
    return Requester(external_user_id=user, school_id=_school_uuid(school), role=role)


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


class ActivityPlayerStoreCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        # expire_on_commit defaults to True here - mirrors create_session_factory()
        # in production (api/dependencies.py calls it with no options).
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession)

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

    # -- helpers (same shape as test_phase17_student_activity_player.py) --

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
            "question_version_ids": ids or self.vids, "title": "Atividade cobertura"})
        lid = r.json()["id"]
        self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        kw = {"available_from": "2000-01-01T00:00:00Z", **assign_kw}
        a = self.client.post(f"/api/v1/question-bank/lists/{lid}/assignments",
                             json={"target_type": "CLASS", "target_id": classroom, **kw})
        self.assertEqual(a.status_code, 201, a.text)
        return lid, a.json()["id"]

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

    def _set_position(self, aid, position):
        return self.client.put(f"/api/v1/student/activities/{aid}/attempt/position",
                               params={"position": position})

    def _new_student_activity(self, classroom, *, uid):
        """One fully-set-up (assignment, student-link) pair, ready to start."""
        lid, aid = self._distributed_activity(classroom=classroom)
        self._seed_link(uid, classroom)
        return aid

    def _attempt_row(self, aid):
        async def _get():
            async with self.factory() as s:
                return (await s.execute(select(ActivityAttempt).where(
                    ActivityAttempt.assignment_id == _uuid.UUID(aid)))).scalar_one()
        return self.loop.run_until_complete(_get())

    def _touch_attempt(self, aid, **fields):
        async def _do():
            async with self.factory() as s:
                att = (await s.execute(select(ActivityAttempt).where(
                    ActivityAttempt.assignment_id == _uuid.UUID(aid)))).scalar_one()
                for k, v in fields.items():
                    setattr(att, k, v)
                await s.commit()
        self.loop.run_until_complete(_do())

    def _touch_assignment(self, aid, **fields):
        async def _do():
            async with self.factory() as s:
                row = await s.get(ActivityAssignment, _uuid.UUID(aid))
                for k, v in fields.items():
                    setattr(row, k, v)
                await s.commit()
        self.loop.run_until_complete(_do())

    # -- 1: resume an IN_PROGRESS attempt whose started_at is None --------
    def test_start_resumes_attempt_with_null_started_at(self):
        aid = self._new_student_activity("turma-null-started", uid="stu_null_start")
        self._student("stu_null_start")
        first = self._start(aid).json()
        self.assertIsNotNone(first["attempt"]["started_at"])

        # simulate a row that somehow has started_at NULL while IN_PROGRESS
        # (e.g. a future data-migration path) and confirm start() repairs it.
        self._touch_attempt(aid, started_at=None)
        row = self._attempt_row(aid)
        self.assertIsNone(row.started_at)

        second = self._start(aid)
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        self.assertEqual(body["attempt"]["status"], STATUS_IN_PROGRESS)
        self.assertIsNotNone(body["attempt"]["started_at"])
        # still the SAME attempt row - no duplicate created
        self.assertEqual(body["attempt"]["id"], first["attempt"]["id"])

    # -- 2: the "status != IN_PROGRESS" defensive branch ------------------
    def test_start_repairs_a_non_in_progress_attempt_row(self):
        """The DB CHECK constraint on activity_attempts.status allows
        'NOT_STARTED' (ck_activity_attempts_status), but the store itself
        never persists that value - only IN_PROGRESS (on creation/resume) and
        COMPLETED (which short-circuits earlier in start()) are ever written.
        So `if attempt.status != STATUS_IN_PROGRESS: attempt.status = ...`
        is defensive code with no real caller path today given the store's
        own write set. This test deliberately writes a NOT_STARTED row out of
        band (a value the schema itself allows) to prove the defensive branch
        does the right thing IF such a row ever existed, rather than leaving
        it silently unverified. It is not evidence of a live bug or a
        reachable path through any current API caller.
        """
        aid = self._new_student_activity("turma-repair-status", uid="stu_repair")
        self._student("stu_repair")
        self._start(aid)
        self._touch_attempt(aid, status=STATUS_NOT_STARTED)
        row = self._attempt_row(aid)
        self.assertEqual(row.status, STATUS_NOT_STARTED)

        r = self._start(aid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["attempt"]["status"], STATUS_IN_PROGRESS)
        row = self._attempt_row(aid)
        self.assertEqual(row.status, STATUS_IN_PROGRESS)

    # -- 3: a version with zero items -> 409 -------------------------------
    def test_start_on_activity_with_no_questions_is_rejected(self):
        async def _make():
            async with self.factory() as s:
                assessment = Assessment(title="Vazia", material_type=LIST_MATERIAL_TYPE,
                                        status="published", owner_external_id="prof_empty")
                s.add(assessment)
                await s.flush()
                version = AssessmentVersion(assessment_id=assessment.id, version_number=1,
                                            title="Vazia", status="published")
                s.add(version)
                await s.flush()
                # deliberately NO AssessmentItem rows
                assignment = ActivityAssignment(
                    assessment_id=assessment.id, assessment_version_id=version.id,
                    target_type="STUDENT", target_id="stu_empty", status="ACTIVE",
                    question_count=0)
                s.add(assignment)
                await s.flush()
                assignment_id = assignment.id
                await s.commit()
                return assignment_id
        assignment_id = self.loop.run_until_complete(_make())

        async def _call():
            async with self.factory() as s:
                store = ActivityPlayerStore(s)
                req = _requester("stu_empty", school=None)
                with self.assertRaises(PlayerStateError) as ctx:
                    await store.start(assignment_id, requester=req)
                return str(ctx.exception)
        msg = self.loop.run_until_complete(_call())
        self.assertIn("não possui questões", msg)

    # -- 4: a genuine UNIQUE(assignment_id, student) race -----------------
    def test_start_handles_concurrent_unique_violation(self):
        aid = self._new_student_activity("turma-race-start", uid="stu_race")
        assignment_id = _uuid.UUID(aid)

        # a competing attempt "wins the race" and is committed first
        async def _insert_competitor():
            async with self.factory() as s:
                competitor = ActivityAttempt(
                    assignment_id=assignment_id, student_external_id="stu_race",
                    status=STATUS_IN_PROGRESS, current_position=1)
                s.add(competitor)
                await s.flush()
                competitor_id = competitor.id
                await s.commit()
                return competitor_id
        competitor_id = self.loop.run_until_complete(_insert_competitor())

        # the store's own _load_attempt is forced to (stale-)report None once,
        # as it would under a real race where the competing INSERT lands
        # between this store's SELECT and its own INSERT+COMMIT.
        async def _call():
            async with self.factory() as s:
                store = ActivityPlayerStore(s)
                orig_load_attempt = store._load_attempt
                calls = {"n": 0}

                async def flaky_load_attempt(assignment_id_, student_id_):
                    calls["n"] += 1
                    if calls["n"] == 1:
                        return None
                    return await orig_load_attempt(assignment_id_, student_id_)

                store._load_attempt = flaky_load_attempt
                req = _requester("stu_race", school="school-1")
                return await store.start(assignment_id, requester=req)
        result = self.loop.run_until_complete(_call())

        # the race is absorbed: the response reflects the row that actually
        # won, no crash bubbles up
        self.assertEqual(result["attempt"]["id"], str(competitor_id))

        async def _count():
            async with self.factory() as s:
                rows = (await s.execute(select(ActivityAttempt).where(
                    ActivityAttempt.assignment_id == assignment_id))).scalars().all()
                return len(rows)
        self.assertEqual(self.loop.run_until_complete(_count()), 1)  # no duplicate

    # -- 5: current_position fallback before any start() ------------------
    def test_state_before_start_computes_current_position_from_pending(self):
        aid = self._new_student_activity("turma-pre-start", uid="stu_pre")
        self._student("stu_pre")
        r = self._state(aid)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], STATUS_NOT_STARTED)
        self.assertIsNone(body["attempt"]["id"])
        self.assertEqual(body["pending_positions"][0], 1)
        self.assertEqual(body["current_position"], 1)

    # -- extra: _resolve() maps an unknown assignment_id to PlayerNotFound --
    def test_unknown_assignment_id_is_not_found(self):
        self._student("stu_unknown_assignment")
        r = self._state(str(_uuid.uuid4()))
        self.assertEqual(r.status_code, 404)

    # -- extra: save_answer with an option key that isn't A-E for the
    #    question -> PlayerError (422) --------------------------------------
    def test_save_answer_invalid_option_key_is_rejected(self):
        aid = self._new_student_activity("turma-bad-option", uid="stu_bad_opt")
        self._student("stu_bad_opt")
        self._start(aid)
        r = self._answer(aid, self.vids[0], "Z")
        self.assertEqual(r.status_code, 422)
        self.assertIn("alternativa inválida", r.json()["detail"])

    # -- 6: save_answer without ever starting -> 409 -----------------------
    def test_save_answer_without_start_is_rejected(self):
        aid = self._new_student_activity("turma-no-start-answer", uid="stu_nostart")
        self._student("stu_nostart")
        r = self._answer(aid, self.vids[0], "A")
        self.assertEqual(r.status_code, 409)
        self.assertIn("inicie a atividade", r.json()["detail"]["message"])

    # -- 7: availability window closes after start -------------------------
    def test_save_answer_after_window_closes_is_rejected(self):
        aid = self._new_student_activity("turma-window-closes", uid="stu_window")
        self._student("stu_window")
        r = self._start(aid)
        self.assertEqual(r.status_code, 200, r.text)

        past = datetime.now(timezone.utc) - timedelta(days=1)
        self._touch_assignment(aid, due_at=past)

        r2 = self._answer(aid, self.vids[0], "A")
        self.assertEqual(r2.status_code, 409)
        self.assertIn("não está disponível", r2.json()["detail"]["message"])

    # -- 8: question not in the frozen item list -> 422 --------------------
    def test_save_answer_for_foreign_question_is_rejected(self):
        aid = self._new_student_activity("turma-foreign-q", uid="stu_foreign")
        self._student("stu_foreign")
        self._start(aid)
        foreign_vid = str(_uuid.uuid4())
        r = self._answer(aid, foreign_vid, "A")
        self.assertEqual(r.status_code, 422)
        self.assertIn("não pertence a esta atividade", r.json()["detail"])

    # -- 9: a genuine UNIQUE(attempt, question_version) race ---------------
    def test_save_answer_handles_concurrent_unique_violation(self):
        aid = self._new_student_activity("turma-race-answer", uid="stu_ans_race")
        self._student("stu_ans_race")
        self._start(aid)
        row = self._attempt_row(aid)
        target_vid = _uuid.UUID(self.vids[0])

        # a competing answer already exists in the DB (committed by "someone
        # else") before this call even runs its own pre-insert check
        async def _insert_competitor():
            async with self.factory() as s:
                competitor = ActivityAnswer(
                    attempt_id=row.id, question_version_id=target_vid,
                    selected_option_id=None, selected_option_key="A",
                    answered_at=datetime.now(timezone.utc))
                s.add(competitor)
                await s.commit()
        self.loop.run_until_complete(_insert_competitor())

        async def _call():
            async with self.factory() as s:
                store = ActivityPlayerStore(s)
                orig_execute = s.execute
                state = {"intercepted": False}

                async def flaky_execute(stmt, *a, **kw):
                    text_stmt = str(stmt).lower()
                    if (not state["intercepted"] and "activity_answers" in text_stmt
                            and "question_version_id" in text_stmt):
                        state["intercepted"] = True

                        class _FakeResult:
                            def scalar_one_or_none(self_inner):
                                return None
                        return _FakeResult()
                    return await orig_execute(stmt, *a, **kw)

                s.execute = flaky_execute
                req = _requester("stu_ans_race", school="school-1")
                return await store.save_answer(
                    _uuid.UUID(aid), target_vid, requester=req, selected_option="B")
        result = self.loop.run_until_complete(_call())

        self.assertTrue(result["saved"])
        self.assertEqual(result["selected_option"], "B")

        async def _rows():
            async with self.factory() as s:
                rows = (await s.execute(select(ActivityAnswer).where(
                    ActivityAnswer.attempt_id == row.id,
                    ActivityAnswer.question_version_id == target_vid))).scalars().all()
                return rows
        rows = self.loop.run_until_complete(_rows())
        self.assertEqual(len(rows), 1)  # retried as UPDATE, no duplicate
        self.assertEqual(rows[0].selected_option_key, "B")

    # -- 10: set_current_position ------------------------------------------
    def test_set_current_position_success(self):
        aid = self._new_student_activity("turma-position-ok", uid="stu_pos_ok")
        self._student("stu_pos_ok")
        self._start(aid)
        r = self._set_position(aid, 3)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["current_position"], 3)
        st = self._state(aid).json()
        self.assertEqual(st["current_position"], 3)

    def test_set_current_position_without_attempt_is_rejected(self):
        aid = self._new_student_activity("turma-position-nostart", uid="stu_pos_none")
        self._student("stu_pos_none")
        r = self._set_position(aid, 2)
        self.assertEqual(r.status_code, 409)
        self.assertIn("não está em andamento", r.json()["detail"]["message"])

    def test_set_current_position_after_completion_is_rejected(self):
        aid = self._new_student_activity("turma-position-done", uid="stu_pos_done")
        self._student("stu_pos_done")
        st = self._start(aid).json()
        for q in st["questions"]:
            self._answer(aid, q["question_version_id"], "A")
        done = self._complete(aid)
        self.assertEqual(done.status_code, 200, done.text)

        r = self._set_position(aid, 2)
        self.assertEqual(r.status_code, 409)
        self.assertIn("não está em andamento", r.json()["detail"]["message"])

    def test_set_current_position_out_of_range_is_rejected(self):
        aid = self._new_student_activity("turma-position-range", uid="stu_pos_range")
        self._student("stu_pos_range")
        self._start(aid)
        total = len(self.vids)

        r_low = self._set_position(aid, 0)
        self.assertEqual(r_low.status_code, 422)
        self.assertIn("fora do intervalo", r_low.json()["detail"])

        r_high = self._set_position(aid, total + 1)
        self.assertEqual(r_high.status_code, 422)
        self.assertIn("fora do intervalo", r_high.json()["detail"])

    # -- 11: complete() without ever starting -> 409 ------------------------
    def test_complete_without_start_is_rejected(self):
        aid = self._new_student_activity("turma-no-start-complete", uid="stu_nostart_c")
        self._student("stu_nostart_c")
        r = self._complete(aid)
        self.assertEqual(r.status_code, 409)
        self.assertIn("inicie a atividade", r.json()["detail"]["message"])


if __name__ == "__main__":
    unittest.main()
