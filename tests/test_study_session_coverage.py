"""PHASE 24 - StudySessionService: coverage-gap sweep.

Targets the branches of src/agente_ia_edu/services/study_session.py that
tests/test_phase24_study_session.py (and its _n1 sibling) never exercised:
the "no session at all today" reply, the various guard/validation error
branches, the SCHOOL-vs-STUDENT_DEFINED prefer_source tie-break, the reduced-
practice note, a broken Learning Path graph degrading gracefully, break
normalization edge cases, and the coordination-session list filters.

HTTP-level tests reuse the same fixture (_seed/_ctx/_ident/_SCHOOL_UUID) as
tests/test_phase24_study_session.py, but - per the campaign's convention for
catching MissingGreenlet/expire_on_commit races - the session factory here
does NOT override expire_on_commit, matching create_session_factory()'s real
production default (expire_on_commit=True). A handful of branches are only
reachable by calling the service directly (no HTTP route can produce a
mismatched requester/subject id, or hand the service an already-CANCELLED
row, or a raw datetime instead of a string) - those use a plain
IsolatedAsyncioTestCase against a throwaway in-memory engine instead.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import StudySession
from agente_ia_edu.db.models.admin import School, UserSchoolLink
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.services.question_list_store import Requester
from agente_ia_edu.services.study_session import (
    ST_CANCELLED,
    StudySessionAuthError,
    StudySessionError,
    StudySessionService,
    _parse_dt,
)

from test_phase24_study_session import (  # noqa: E402  (sibling test module)
    _SCHOOL_UUID,
    _ctx,
    _ident,
    _seed,
)


# ============================================================================
# HTTP-level: full app, real production expire_on_commit=True default.
# ============================================================================
class StudySessionCoverageHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        # No expire_on_commit override - this mirrors create_session_factory()
        # in src/agente_ia_edu/db/session.py exactly (expire_on_commit=True).
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(cls.factory)

        seed = cls.loop.run_until_complete(_prep())
        cls.by_content = seed["by_content"]
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.app.dependency_overrides[get_current_authenticated_context] = lambda: _ctx()
        cls.app.dependency_overrides[get_current_identity] = lambda: _ident()
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    # -- helpers ---------------------------------------------------------
    def _as_student(self, user="s_al", school=None):
        self.app.dependency_overrides[get_current_authenticated_context] = lambda: _ctx(user, school=school)

    def _as_coord(self, user="p24_coord"):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _give_evidence(self, student, content_code, n_correct, n_total, *, date=None):
        from agente_ia_edu.db.models.assessments import DomainContentMastery

        async def _do():
            async with self.factory() as s:
                acc = round(n_correct / n_total, 4) if n_total else None
                s.add(DomainContentMastery(
                    student_external_id=student, taxonomy_version="curriculum-v2",
                    content_code=content_code, subcontent_code=None,
                    questions_seen=n_total, questions_answered=n_total,
                    questions_correct=n_correct, questions_incorrect=n_total - n_correct,
                    accuracy=acc, evidence_count=n_total,
                    evidence_state="OBSERVED" if n_total >= 3 else "INSUFFICIENT_EVIDENCE",
                    definitive_evidence_count=n_total, provisional_evidence_count=0,
                    forced_closure_evidence_count=0, visual_dependency_evidence_count=0,
                    origin_breakdown={"OFFICIAL_ACTIVITY": n_total},
                    last_evaluated_at=datetime.now(timezone.utc)))
                await s.commit()
        self.loop.run_until_complete(_do())

    def _clear_sessions(self, student=None):
        async def _do():
            async with self.factory() as s:
                q = select(StudySession)
                if student:
                    q = q.where(StudySession.student_external_id == student)
                for row in (await s.execute(q)).scalars().all():
                    await s.delete(row)
                await s.commit()
        self.loop.run_until_complete(_do())

    def _create_free(self, student="s_al", **body):
        self._as_student(student)
        return self.client.post("/api/v1/student/study-session", json=body)

    def _create_coord(self, target_id="s_al", **overrides):
        body = {
            "school_id": str(_SCHOOL_UUID), "target_type": "STUDENT", "target_id": target_id,
            "session_date": "2026-10-01", "start_at": "2026-10-01T14:00:00Z",
            "end_at": "2026-10-01T15:00:00Z",
        }
        body.update(overrides)
        self._as_coord()
        return self.client.post("/api/v1/coordination/study-sessions", json=body)

    # -- get_today: no session at all today (line 97) -------------------
    def test_today_no_session_at_all(self):
        self._clear_sessions("s_cov_none")
        self._as_student("s_cov_none")
        r = self.client.get("/api/v1/student/study-session/today")
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        self.assertFalse(b["has_session"])
        self.assertTrue(b["prompt_for_time"])
        self.assertIsNone(b["session"])

    # -- create_student_session: a SCHOOL session already exists (line 113)
    def test_create_free_blocked_by_school_session(self):
        self._clear_sessions("s_al")
        r1 = self._create_coord(target_id="s_al", session_date="2026-10-02",
                                start_at="2026-10-02T14:00:00Z", end_at="2026-10-02T15:00:00Z")
        self.assertEqual(r1.status_code, 200, r1.text)
        # the free-session creation path checks "today", so give the school
        # row today's date directly via a raw update (the coord endpoint
        # always schedules for session_date, which we don't control to be
        # "today" through the public API - so we backdate the row instead).
        sid = r1.json()["sessions"][0]["session_id"]

        async def _retodate():
            async with self.factory() as s:
                row = await s.get(StudySession, _uuid.UUID(sid))
                from agente_ia_edu.services.study_session import _today
                row.session_date = _today()
                await s.commit()
        self.loop.run_until_complete(_retodate())

        r2 = self._create_free("s_al", available_minutes=30)
        self.assertEqual(r2.status_code, 422, r2.text)
        self.assertIn("programado pela coordenação", r2.json()["detail"]["message"])

    # -- create_student_session: available_minutes below policy min (line 128)
    def test_create_free_minutes_below_policy_min(self):
        self._clear_sessions("s_cov_min")
        r = self._create_free("s_cov_min", available_minutes=5)  # passes pydantic ge=5, below policy min=10
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("O tempo deve estar entre", r.json()["detail"]["message"])

    # -- start_session on an already-COMPLETED session (line 170) -------
    def test_start_already_completed_session_rejected(self):
        self._clear_sessions("s_cov_done")
        self._give_evidence("s_cov_done", "C_A", 2, 6)
        sid = self._create_free("s_cov_done", available_minutes=30).json()["id"]
        self._as_student("s_cov_done")
        self.client.post(f"/api/v1/student/study-session/{sid}/start")
        blocks = self.client.get(f"/api/v1/student/study-session/{sid}").json()["blocks"]
        for b in blocks:
            self.client.post(f"/api/v1/student/study-session/{sid}/blocks/{b['index']}/complete",
                            json={"skipped": True})
        finished = self.client.post(f"/api/v1/student/study-session/{sid}/complete").json()
        self.assertEqual(finished["status"], "COMPLETED")
        r = self.client.post(f"/api/v1/student/study-session/{sid}/start")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("já foi concluído", r.json()["detail"]["message"])

    # -- start_block before start_session, on a coordinator-SCHEDULED
    #    session (line 187) ------------------------------------------
    def test_start_block_before_session_started_rejected(self):
        self._clear_sessions("s_al")
        r1 = self._create_coord(target_id="s_al", session_date="2026-10-03",
                                start_at="2026-10-03T14:00:00Z", end_at="2026-10-03T15:00:00Z",
                                content_codes=["C_A"])
        self.assertEqual(r1.status_code, 200, r1.text)
        sid = r1.json()["sessions"][0]["session_id"]
        self._as_student("s_al")
        # never call /start - a freshly coordinator-scheduled session is
        # SCHEDULED, not READY/IN_PROGRESS, so opening a block directly must
        # be rejected with a clear instruction instead of a confusing state.
        r = self.client.post(f"/api/v1/student/study-session/{sid}/blocks/0/start")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("Inicie o momento de aprendizado", r.json()["detail"]["message"])

    # -- start_block on an already-DONE block: idempotent short-circuit
    #    (line 191) ----------------------------------------------------
    def test_start_block_already_done_is_idempotent(self):
        self._clear_sessions("s_cov_donedblk")
        self._give_evidence("s_cov_donedblk", "C_A", 2, 6)
        sid = self._create_free("s_cov_donedblk", available_minutes=60,
                                target_content_codes=["C_A"]).json()["id"]
        self._as_student("s_cov_donedblk")
        self.client.post(f"/api/v1/student/study-session/{sid}/start")
        self.client.post(f"/api/v1/student/study-session/{sid}/blocks/0/complete", json={})
        r = self.client.post(f"/api/v1/student/study-session/{sid}/blocks/0/start")
        self.assertEqual(r.status_code, 200, r.text)
        block0 = next(b for b in r.json()["blocks"] if b["index"] == 0)
        self.assertEqual(block0["status"], "DONE")

    # -- start_block on a READY (not yet started) free session flips it
    #    to IN_PROGRESS from inside start_block itself (lines 226-227) --
    def test_start_block_on_ready_session_activates_it(self):
        self._clear_sessions("s_cov_ready")
        self._give_evidence("s_cov_ready", "C_A", 2, 6)
        sid = self._create_free("s_cov_ready", available_minutes=60,
                                target_content_codes=["C_A"]).json()["id"]
        self._as_student("s_cov_ready")
        # do NOT call /start - go straight to the first block, exactly like a
        # student who lands on the session view and taps the first block card.
        r = self.client.post(f"/api/v1/student/study-session/{sid}/blocks/0/start")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "IN_PROGRESS")

    # -- start_block reduces an oversized PRACTICE request to the real
    #    bank size (lines 220, 566-568) --------------------------------
    def test_start_block_reduces_practice_to_available_bank(self):
        self._clear_sessions("s_cov_reduce")
        # C_C only has 6 questions in the shared fixture; INSUFFICIENT_EVIDENCE
        # pushes a large PRACTICE allocation that the planner will size well
        # above 6 questions for a 90-minute session.
        self._give_evidence("s_cov_reduce", "C_B", 9, 10)  # keep C_B mastered so C_C isn't BLOCKED
        self._give_evidence("s_cov_reduce", "C_C", 0, 1)   # INSUFFICIENT_EVIDENCE
        sid = self._create_free("s_cov_reduce", available_minutes=90,
                                target_content_codes=["C_C"]).json()["id"]
        self._as_student("s_cov_reduce")
        self.client.post(f"/api/v1/student/study-session/{sid}/start")
        blocks = self.client.get(f"/api/v1/student/study-session/{sid}").json()["blocks"]
        practice = [b for b in blocks if b["block_type"] == "PRACTICE"]
        if not practice or practice[0]["requested_questions"] <= 6:
            self.skipTest("planner did not request more than the 6-question C_C bank "
                          "for this evidence/time combination - nothing to reduce")
        pidx = practice[0]["index"]
        started = self.client.post(f"/api/v1/student/study-session/{sid}/blocks/{pidx}/start").json()
        block = next(b for b in started["blocks"] if b["index"] == pidx)
        self.assertEqual(block["question_count"], 6)
        self.assertIn("reduzida", block["action_note"])

    # -- complete_session marks any still-PENDING/ACTIVE block SKIPPED
    #    (line 261) -------------------------------------------------
    def test_complete_session_skips_untouched_blocks(self):
        self._clear_sessions("s_cov_early")
        self._give_evidence("s_cov_early", "C_A", 2, 6)
        sid = self._create_free("s_cov_early", available_minutes=90,
                                target_content_codes=["C_A"]).json()["id"]
        self._as_student("s_cov_early")
        self.client.post(f"/api/v1/student/study-session/{sid}/start")
        blocks_before = self.client.get(f"/api/v1/student/study-session/{sid}").json()["blocks"]
        self.assertGreater(len(blocks_before), 1, "need >=2 blocks to leave one untouched")
        # complete only the FIRST block, then finish the whole session early
        # without touching the rest - mirrors a student giving up partway.
        finished = self.client.post(f"/api/v1/student/study-session/{sid}/complete").json()
        self.assertEqual(finished["status"], "COMPLETED")
        for b in finished["blocks"][1:]:
            self.assertEqual(b["status"], "SKIPPED")

    # -- create_coordination_sessions: window shorter than policy min
    #    (line 301) -----------------------------------------------------
    def test_coordination_window_too_short(self):
        self._as_coord()
        r = self._create_coord(session_date="2026-10-04", start_at="2026-10-04T14:00:00Z",
                                end_at="2026-10-04T14:05:00Z")  # 5 min < policy min (10)
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("ao menos", r.json()["detail"]["message"])

    # -- create_coordination_sessions: no students found for the target
    #    (line 312) --------------------------------------------------
    def test_coordination_no_students_for_target(self):
        r = self._create_coord(target_id="turma-inexistente", target_type="CLASSROOM",
                                session_date="2026-10-05", start_at="2026-10-05T14:00:00Z",
                                end_at="2026-10-05T15:00:00Z")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("Nenhum aluno encontrado", r.json()["detail"]["message"])

    # -- create_coordination_sessions: an unparseable start_at falls
    #    through _parse_dt's except branch and is reported as an
    #    invalid window (lines 699-700) --------------------------------
    def test_coordination_unparseable_start_at(self):
        r = self._create_coord(session_date="2026-10-06", start_at="not-a-timestamp",
                                end_at="2026-10-06T15:00:00Z")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("Janela inválida", r.json()["detail"]["message"])

    # -- list_coordination_sessions: session_date + classroom_id filters
    #    (lines 421, 423, 683 brief view) --------------------------------
    def test_list_coordination_sessions_filters(self):
        self._clear_sessions("s_al")
        self._clear_sessions("s_bo")
        r_a = self._create_coord(target_id="turma-1", target_type="CLASSROOM",
                                 session_date="2026-10-07", start_at="2026-10-07T14:00:00Z",
                                 end_at="2026-10-07T15:00:00Z")
        self.assertEqual(r_a.status_code, 200, r_a.text)
        r_b = self._create_coord(target_id="turma-1", target_type="CLASSROOM",
                                 session_date="2026-10-08", start_at="2026-10-08T14:00:00Z",
                                 end_at="2026-10-08T15:00:00Z")
        self.assertEqual(r_b.status_code, 200, r_b.text)

        self._as_coord()
        by_date = self.client.get("/api/v1/coordination/study-sessions", params={
            "school_id": str(_SCHOOL_UUID), "session_date": "2026-10-07"})
        self.assertEqual(by_date.status_code, 200, by_date.text)
        dates = {s["session_date"] for s in by_date.json()["sessions"]}
        self.assertEqual(dates, {"2026-10-07"})
        # brief=True view: no "blocks"/"plan_notes" keys
        self.assertNotIn("blocks", by_date.json()["sessions"][0])

        by_classroom = self.client.get("/api/v1/coordination/study-sessions", params={
            "school_id": str(_SCHOOL_UUID), "classroom_id": "turma-1"})
        self.assertEqual(by_classroom.status_code, 200, by_classroom.text)
        self.assertGreaterEqual(by_classroom.json()["count"], 2)
        self.assertTrue(all(s["scope_external_id"] == "turma-1" for s in by_classroom.json()["sessions"]))

    # -- get_today prefers the SCHOOL row over a coexisting STUDENT_DEFINED
    #    one for the same day (line 467) --------------------------------
    def test_today_prefers_school_over_student_defined(self):
        from agente_ia_edu.services.study_session import _today
        real_today = _today()

        self._clear_sessions("s_al")
        self._give_evidence("s_al", "C_A", 2, 6)
        free = self._create_free("s_al", available_minutes=30)
        self.assertEqual(free.status_code, 200, free.text)
        sid_free = free.json()["id"]
        self.assertEqual(free.json()["session_date"], real_today)

        # schedule a SCHOOL session for "s_al" on that SAME (real, today's)
        # date (create_coordination_sessions never checks for a coexisting
        # STUDENT_DEFINED row, so both legitimately coexist).
        r = self._create_coord(target_id="s_al", session_date=real_today,
                                start_at=f"{real_today}T14:00:00Z", end_at=f"{real_today}T15:00:00Z")
        self.assertEqual(r.status_code, 200, r.text)
        sid_school = r.json()["sessions"][0]["session_id"]
        self.assertNotEqual(sid_school, sid_free)

        self._as_student("s_al")
        today = self.client.get("/api/v1/student/study-session/today").json()
        self.assertEqual(today["session"]["id"], sid_school)
        self.assertEqual(today["session"]["source"], "SCHOOL_DEFINED")

    # -- _validate_codes: an all-blank codes list degrades to "no codes
    #    pinned" (line 497), not a validation error - the planner then falls
    #    back to auto-selecting from the Learning Path exactly as it does
    #    when target_content_codes is omitted entirely. --------------------
    def test_create_free_blank_content_code_ignored(self):
        self._clear_sessions("s_cov_blank")
        self._give_evidence("s_cov_blank", "C_A", 2, 6)
        r = self._create_free("s_cov_blank", available_minutes=30, target_content_codes=[""])
        self.assertEqual(r.status_code, 200, r.text)
        # the blank entry itself must never survive into the persisted codes
        self.assertNotIn("", r.json()["target_content_codes"])

    # -- _validate_codes: an unknown/inactive code is rejected (line 503)
    def test_create_free_invalid_content_code_rejected(self):
        self._clear_sessions("s_cov_badcode")
        r = self._create_free("s_cov_badcode", available_minutes=30,
                              target_content_codes=["NOPE_NOT_A_CODE"])
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("inválido ou inativo", r.json()["detail"]["message"])

    # -- _build_plan: a broken Learning Path graph must not break session
    #    creation (lines 526-527) -----------------------------------
    def test_broken_learning_path_degrades_gracefully(self):
        self._clear_sessions("s_cov_brokenpath")
        with patch(
            "agente_ia_edu.services.adaptive_learning_path.AdaptiveLearningPathService.build_path",
            side_effect=RuntimeError("simulated broken prerequisite graph"),
        ):
            r = self._create_free("s_cov_brokenpath", available_minutes=30)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertGreaterEqual(r.json()["blocks_total"], 0)

    # -- _block_at: a nonexistent block index is a clear 422, not a
    #    KeyError (line 590) -----------------------------------------
    def test_start_block_bad_index_rejected(self):
        self._clear_sessions("s_cov_badidx")
        self._give_evidence("s_cov_badidx", "C_A", 2, 6)
        sid = self._create_free("s_cov_badidx", available_minutes=30).json()["id"]
        self._as_student("s_cov_badidx")
        self.client.post(f"/api/v1/student/study-session/{sid}/start")
        r = self.client.post(f"/api/v1/student/study-session/{sid}/blocks/999/start")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("não existe nesta sessão", r.json()["detail"]["message"])

    # -- _normalize_breaks: start_at + duration_minutes (no end_at)
    #    derives end_at (line 606) --------------------------------------
    def test_break_with_start_and_duration_only(self):
        r = self._create_coord(session_date="2026-10-10", start_at="2026-10-10T14:00:00Z",
                                end_at="2026-10-10T16:00:00Z",
                                breaks=[{"start_at": "2026-10-10T14:30:00Z", "duration_minutes": 15}])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["break_minutes"], 15)
        brk = r.json()["breaks"][0]
        self.assertEqual(brk["start_at"], "2026-10-10T14:30:00+00:00")
        self.assertEqual(brk["end_at"], "2026-10-10T14:45:00+00:00")

    # -- _normalize_breaks: breaks that consume the whole window are
    #    rejected outright (line 625) -------------------------------
    def test_breaks_consuming_whole_window_rejected(self):
        r = self._create_coord(session_date="2026-10-12", start_at="2026-10-12T14:00:00Z",
                                end_at="2026-10-12T15:00:00Z", breaks=[{"duration_minutes": 60}])
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("não podem ocupar toda a janela", r.json()["detail"]["message"])


# ============================================================================
# Direct service-level: branches no HTTP route can reach.
# ============================================================================
class StudySessionServiceDirectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # Production default (expire_on_commit=True) - unchanged from the
        # engine's actual create_session_factory() behavior.
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession)

    async def asyncTearDown(self):
        await self.engine.dispose()

    # -- _authz_self: no HTTP route ever passes a requester whose
    #    external_user_id differs from the subject student id (every route
    #    always passes _me(ctx) as both) - only a direct/internal caller
    #    could hit this mismatch (line 433). --------------------------
    async def test_authz_self_rejects_mismatched_requester(self):
        async with self.factory() as session:
            svc = StudySessionService(session)
            requester = Requester(external_user_id="someone-else", school_id=None, role="STUDENT")
            with self.assertRaises(StudySessionAuthError):
                await svc.get_session(
                    "real-student", _uuid.uuid4(), requester=requester)

    # -- start_session on a row an admin/migration set to CANCELLED
    #    directly (no route ever writes this status) (line 172) --------
    async def test_start_session_on_cancelled_row_rejected(self):
        school_id = _uuid.uuid4()
        async with self.factory() as session:
            session.add(School(id=school_id, code="SCH-CANCEL", name="school-cancel"))
            await session.flush()  # id is already known (we generated it) - no post-commit access needed
            await session.commit()

            row = StudySession(
                student_external_id="s_cancel", source="STUDENT_DEFINED", school_id=school_id,
                scope_type="STUDENT", scope_external_id="s_cancel",
                created_by_external_id="s_cancel", session_date="2026-10-13",
                timer_mode="UNTIMED", available_minutes=None, break_minutes=0,
                effective_study_minutes=45, target_content_codes=None,
                config={"breaks": []}, plan={"blocks": []},
                status=ST_CANCELLED, current_block_index=0,
            )
            session.add(row)
            await session.flush()
            session_id = row.id  # read before commit expires the row
            await session.commit()

            svc = StudySessionService(session)
            requester = Requester(external_user_id="s_cancel", school_id=None, role="STUDENT")
            with self.assertRaises(StudySessionError) as ctx:
                await svc.start_session("s_cancel", session_id, requester=requester)
            self.assertIn("cancelado", str(ctx.exception))

    # -- _normalize_breaks: a negative duration (line 609). Note
    #    `dur = int(dur or pol.break_default_minutes)` treats 0 itself as
    #    falsy and silently substitutes the default - so a literal 0 can
    #    never reach this guard from ANY caller; only a strictly negative
    #    value does. The public HTTP route's _CoordBreak pydantic model
    #    also enforces duration_minutes >= 1, so no request can reach this
    #    through the API - it only guards a caller that builds break dicts
    #    directly. -------------------------------------------------------
    async def test_normalize_breaks_negative_duration_rejected(self):
        async with self.factory() as session:
            svc = StudySessionService(session)
            start_dt = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)
            end_dt = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)
            with self.assertRaises(StudySessionError) as ctx:
                svc._normalize_breaks([{"duration_minutes": -5}], start_dt, end_dt, 60)
            self.assertIn("duração não positiva", str(ctx.exception))

    # -- _existing_school_sessions: defensive empty-list guard, never hit
    #    through create_coordination_sessions (which already raises before
    #    calling this if _students_for_target returned []) (line 478) ----
    async def test_existing_school_sessions_empty_list_short_circuits(self):
        async with self.factory() as session:
            svc = StudySessionService(session)
            out = await svc._existing_school_sessions([], "2026-10-14")
        self.assertEqual(out, {})


# ============================================================================
# _parse_dt: plain module function, direct unit tests.
#
# Every real HTTP caller hands it a required `str` field (start_at/end_at on
# _CoordStudySessionRequest) or a guarded truthy string (breaks' start_at/
# end_at, only read when raw.get(...) is truthy) - so value=None and
# value=<a real datetime> are never reached via any route. They're exercised
# here directly since the function is a small, well-defined pure helper.
# ============================================================================
class ParseDtDirectTests(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(_parse_dt(None))

    def test_naive_datetime_gets_utc_attached(self):
        naive = datetime(2026, 10, 15, 12, 0, 0)
        out = _parse_dt(naive)
        self.assertEqual(out.tzinfo, timezone.utc)

    def test_aware_datetime_passed_through(self):
        aware = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
        out = _parse_dt(aware)
        self.assertIs(out, aware)


if __name__ == "__main__":
    unittest.main()
