"""
HTTP-level route-handler coverage for src/agente_ia_edu/api/routes/student.py.

Wave 2 of the overnight HTTP-layer coverage campaign. student.py has dozens
of thin routes that each: resolve a store/service, call it inside a
try/except, and map typed domain exceptions to HTTP status codes. Existing
phase-numbered test files exercise the SERVICE layer (and a handful of happy
paths + auth-mismatch paths) extensively, but two entire routes
(GET /evolution, GET /learning-path) were never hit via HTTP at all, and most
of the "except Exception as exc: raise _map_XXX_error(exc)" branches across
the activities/domain/study-path/practice/study-session/materials endpoints
were never triggered - i.e. every one of those endpoints was only ever
exercised on its happy path.

This file targets the REACHABLE gaps only. Several of the missing branches
identified by coverage (e.g. the generic `except (FooError, ValueError)`
catch-alls on purely self-scoped endpoints with no path parameter, such as
POST /domain/rebuild or GET /study-path) are structurally unreachable via any
legitimate HTTP call: those endpoints always pass the requester's own id as
both the "subject" and the "requester", so their internal `_authz_self`
check can never fail, and they take no other input that could raise. Per the
campaign's "don't invent hypothetical edge cases with no plausible real
trigger" rule, those are left uncovered and are called out in the report
instead of being faked with contrived state.
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
    get_current_identity,
    get_session_factory,
)
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
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext


def _school_uuid(name: str) -> str:
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"student-cov-school-{name}"))


def _ctx(user, role="STUDENT", school="school-cov", scope_type="SCHOOL", scope_external_id=None):
    return AuthenticatedUserContext(
        user_id=user, external_identity_id=user, role=role,
        school_id=_school_uuid(school), scope_type=scope_type,
        scope_external_id=scope_external_id,
    )


class StudentRouteCoverageHTTP(unittest.TestCase):
    """Shared full app (create_app()) + in-memory SQLite, mirroring the
    pattern in tests/test_phase17_student_activity_player.py."""

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls._ctx = _ctx("stu-default")
        cls.app.dependency_overrides[get_current_authenticated_context] = lambda: cls._ctx
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def setUp(self):
        # Every request also passes through router-level reject_reception_only_role,
        # which depends on get_current_identity - a "Bearer student:<id>" header
        # satisfies that (role != SECRETARY) without needing a second override,
        # matching the default TestExternalIdentityProvider header format.
        self.headers = {"Authorization": "Bearer student:stu-default"}
        self._as(_ctx("stu-default"))

    def _as(self, ctx):
        type(self).app.dependency_overrides[get_current_authenticated_context] = lambda: ctx

    def _seed_link(self, uid, school="school-cov", scope_external_id="turma-cov", active=True):
        async def _do():
            async with self.factory() as s:
                s.add(UserSchoolLink(
                    external_user_id=uid, school_id=_uuid.UUID(_school_uuid(school)),
                    role="STUDENT", scope_type="SCHOOL", scope_external_id=scope_external_id,
                    active=active,
                ))
                await s.commit()

        self.loop.run_until_complete(_do())

    def _distributed_activity(self, *, owner="prof-cov", school="school-cov",
                              target_type="STUDENT", target_id="stu-default", **assign_kw):
        """Create + finalize a question-bank list and distribute it, returning
        the assignment id - the minimal real ActivityAssignment fixture the
        PHASE 16/17/18 player/correction endpoints operate on."""
        self._as(_ctx(owner, role="TEACHER", school=school))
        r = self.client.post(
            "/api/v1/question-bank/lists",
            json={
                "question_version_ids": self._seed_question_bank(),
                "title": f"Atividade cov {_uuid.uuid4().hex[:6]}",
                "answer_key_presentation": "KEY_AT_END",
            },
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 201, r.text)
        list_id = r.json()["id"]
        finalize = self.client.post(
            f"/api/v1/question-bank/lists/{list_id}/finalize", headers=self.headers
        )
        self.assertEqual(finalize.status_code, 200, finalize.text)
        kw = {"available_from": "2000-01-01T00:00:00Z", **assign_kw}
        assign = self.client.post(
            f"/api/v1/question-bank/lists/{list_id}/assignments",
            json={"target_type": target_type, "target_id": target_id, **kw},
            headers=self.headers,
        )
        self.assertEqual(assign.status_code, 201, assign.text)
        return assign.json()["id"]

    def _seed_question_bank(self, n=2):
        async def _do():
            async with self.factory() as s:
                inst = Institution(code=f"I-{_uuid.uuid4().hex[:8]}", name="Institution")
                s.add(inst)
                await s.flush()
                exam = Exam(institution_id=inst.id, code=f"E-{_uuid.uuid4().hex[:8]}", name="Exam")
                s.add(exam)
                await s.flush()
                application = ExamApplication(exam_id=exam.id, year=2026, application_type="regular")
                s.add(application)
                await s.flush()
                booklet = ExamBooklet(exam_application_id=application.id, code=f"B-{_uuid.uuid4().hex[:8]}")
                source = SourceDocument(
                    exam_application_id=application.id, document_type="proof",
                    source_url="https://example.test/cov.pdf",
                    acquired_at=datetime.now(timezone.utc), content_hash=_uuid.uuid4().hex,
                )
                s.add_all([booklet, source])
                await s.flush()
                revision = AnswerKeyRevision(source_document_id=source.id, revision_number=1, is_official=True)
                s.add(revision)
                await s.flush()
                vids = []
                for i in range(n):
                    q = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
                    s.add(q)
                    await s.flush()
                    v = QuestionVersion(
                        question_id=q.id, version_kind="official_original",
                        canonical_text=f"Enunciado cov {i}", content_hash=_uuid.uuid4().hex,
                        recommended_difficulty="EASY",
                    )
                    s.add(v)
                    await s.flush()
                    correct = QuestionOption(question_version_id=v.id, option_key="A", position=1,
                                             text="Correta", is_valid_option=True)
                    wrong = QuestionOption(question_version_id=v.id, option_key="B", position=2,
                                           text="Errada", is_valid_option=True)
                    s.add_all([correct, wrong])
                    await s.flush()
                    bq = BookletQuestion(exam_booklet_id=booklet.id, question_version_id=v.id, position=i + 1)
                    s.add(bq)
                    await s.flush()
                    s.add(AnswerKeyEntry(
                        answer_key_revision_id=revision.id, booklet_question_id=bq.id,
                        official_answer_label="A", resolved_option_id=correct.id,
                    ))
                    vids.append(str(v.id))
                await s.commit()
                return vids

        return self.loop.run_until_complete(_do())

    # ------------------------------------------------------------------
    # /evolution and /learning-path - never hit via HTTP at all before this
    # (student.py ~65-126). Uses get_current_identity, not
    # get_current_authenticated_context, so we point that dependency at a
    # brand-new student with zero history - the "empty state" contract
    # already proven at the service level by test_student_experience.py.
    # ------------------------------------------------------------------

    def test_get_student_evolution_new_student_empty_state(self):
        response = self.client.get(
            "/api/v1/student/evolution",
            headers={"Authorization": "Bearer student:stu-newbie-evo"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["has_data"])
        self.assertEqual(body["accuracy_percentage"], 0.0)

    def test_get_student_learning_path_new_student_empty_state(self):
        response = self.client.get(
            "/api/v1/student/learning-path",
            headers={"Authorization": "Bearer student:stu-newbie-path"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsNotNone(body["steps"])

    def test_get_student_evolution_respects_time_period_query_param(self):
        response = self.client.get(
            "/api/v1/student/evolution?time_period=last_30_days",
            headers={"Authorization": "Bearer student:stu-newbie-evo2"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    # ------------------------------------------------------------------
    # get_student_activity - AssignmentAuthError branch (228-229): a real
    # assignment exists, but the caller is not its recipient.
    # ------------------------------------------------------------------

    def test_get_student_activity_wrong_student_forbidden(self):
        assignment_id = self._distributed_activity(target_id="stu-owner")
        self._as(_ctx("stu-intruder"))
        response = self.client.get(
            f"/api/v1/student/activities/{assignment_id}", headers=self.headers
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_get_student_activity_not_found(self):
        response = self.client.get(
            f"/api/v1/student/activities/{_uuid.uuid4()}", headers=self.headers
        )
        self.assertEqual(response.status_code, 404, response.text)

    # ------------------------------------------------------------------
    # activity player - PlayerNotFound (252) and the generic
    # PlayerError/ValueError branch (258-259), plus set_activity_position
    # (322-328), never exercised via HTTP before.
    # ------------------------------------------------------------------

    def test_get_activity_attempt_not_found(self):
        self._as(_ctx("stu-player-404"))
        response = self.client.get(
            f"/api/v1/student/activities/{_uuid.uuid4()}/attempt", headers=self.headers
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_set_activity_position_unknown_assignment_not_found(self):
        self._as(_ctx("stu-position-404"))
        response = self.client.put(
            f"/api/v1/student/activities/{_uuid.uuid4()}/attempt/position?position=1",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_save_activity_answer_invalid_option_key_returns_422(self):
        assignment_id = self._distributed_activity(target_id="stu-badanswer")
        self._seed_link("stu-badanswer")
        self._as(_ctx("stu-badanswer"))
        started = self.client.post(
            f"/api/v1/student/activities/{assignment_id}/attempt", headers=self.headers
        )
        self.assertEqual(started.status_code, 200, started.text)
        question_version_id = started.json()["questions"][0]["question_version_id"]
        response = self.client.put(
            f"/api/v1/student/activities/{assignment_id}/attempt/answers/{question_version_id}",
            json={"selected_option": "Z"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 422, response.text)

    # ------------------------------------------------------------------
    # activity correction - CorrectionNotFound (363), never triggered via a
    # literally nonexistent assignment_id before (existing tests always use
    # a real assignment with a role/state mismatch, hitting 403/409 instead).
    # ------------------------------------------------------------------

    def test_get_activity_result_unknown_assignment_not_found(self):
        self._as(_ctx("stu-result-404"))
        response = self.client.get(
            f"/api/v1/student/activities/{_uuid.uuid4()}/attempt/result", headers=self.headers
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_correct_activity_attempt_unknown_assignment_not_found(self):
        self._as(_ctx("stu-correct-404"))
        response = self.client.post(
            f"/api/v1/student/activities/{_uuid.uuid4()}/attempt/correct", headers=self.headers
        )
        self.assertEqual(response.status_code, 404, response.text)

    # ------------------------------------------------------------------
    # curriculum domain map - get_discipline's DomainMapNotFound (527-528)
    # for an unknown discipline_code.
    # ------------------------------------------------------------------

    def test_get_curriculum_domain_discipline_unknown_code_not_found(self):
        self._as(_ctx("stu-domain-404"))
        response = self.client.get(
            "/api/v1/student/domain/discipline/NOPE-DISC", headers=self.headers
        )
        self.assertEqual(response.status_code, 404, response.text)

    # ------------------------------------------------------------------
    # adaptive learning path - get_content_recommendation's
    # LearningPathNotFound (562-563 in the shared _map_path_error, and
    # 616-617 in the endpoint's own except block) for an unknown content_code.
    # ------------------------------------------------------------------

    def test_get_student_study_path_content_unknown_code_not_found(self):
        self._as(_ctx("stu-path-404"))
        response = self.client.get(
            "/api/v1/student/study-path/content/NOPE-CONTENT", headers=self.headers
        )
        self.assertEqual(response.status_code, 404, response.text)

    # ------------------------------------------------------------------
    # adaptive practice - PracticeNotFound (644) for a practice id that does
    # not exist at all (existing test_phase22 coverage only exercises the
    # PracticeAuthError branch, via another student's real practice).
    # ------------------------------------------------------------------

    def test_get_student_practice_unknown_id_not_found(self):
        self._as(_ctx("stu-practice-404"))
        response = self.client.get(
            f"/api/v1/student/practice/{_uuid.uuid4()}", headers=self.headers
        )
        self.assertEqual(response.status_code, 404, response.text)

    # ------------------------------------------------------------------
    # study session - StudySessionNotFound (723, 758-759) for every
    # session_id-keyed endpoint, and the StudySessionError/ValueError branch
    # (726-727, 741-742) for a creation request that satisfies the schema but
    # violates the service's own validation rule (neither a timed nor an
    # explicitly untimed session was requested).
    # ------------------------------------------------------------------

    def test_get_student_study_session_unknown_id_not_found(self):
        self._as(_ctx("stu-session-404"))
        response = self.client.get(
            f"/api/v1/student/study-session/{_uuid.uuid4()}", headers=self.headers
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_create_student_study_session_missing_time_choice_returns_422(self):
        self._as(_ctx("stu-session-422"))
        response = self.client.post(
            "/api/v1/student/study-session", json={}, headers=self.headers
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_start_student_study_session_block_unknown_session_not_found(self):
        self._as(_ctx("stu-block-404"))
        response = self.client.post(
            f"/api/v1/student/study-session/{_uuid.uuid4()}/blocks/0/start", headers=self.headers
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_complete_student_study_session_block_unknown_session_not_found(self):
        self._as(_ctx("stu-block-complete-404"))
        response = self.client.post(
            f"/api/v1/student/study-session/{_uuid.uuid4()}/blocks/0/complete",
            json={"skipped": False},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_complete_student_study_session_unknown_id_not_found(self):
        self._as(_ctx("stu-session-complete-404"))
        response = self.client.post(
            f"/api/v1/student/study-session/{_uuid.uuid4()}/complete", headers=self.headers
        )
        self.assertEqual(response.status_code, 404, response.text)

    # ------------------------------------------------------------------
    # materials - get_sections (908-909) and save_progress (923-924) for a
    # material id that does not exist.
    # ------------------------------------------------------------------

    def test_get_student_material_sections_unknown_id_not_found(self):
        self._as(_ctx("stu-material-404"))
        response = self.client.get(
            f"/api/v1/student/materials/{_uuid.uuid4()}/sections", headers=self.headers
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_get_student_material_progress_unknown_id_not_found(self):
        self._as(_ctx("stu-material-get-progress-404"))
        response = self.client.get(
            f"/api/v1/student/materials/{_uuid.uuid4()}/progress", headers=self.headers
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_save_student_material_progress_unknown_id_not_found(self):
        self._as(_ctx("stu-material-progress-404"))
        response = self.client.put(
            f"/api/v1/student/materials/{_uuid.uuid4()}/progress",
            json={"completed": False},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 404, response.text)


if __name__ == "__main__":
    unittest.main()
