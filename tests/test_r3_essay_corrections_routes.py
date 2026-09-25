import asyncio
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    Person,
    PromptAssignment,
    School,
    Student,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


async def _fake_retry(self, essay_correction_id):
    """Stands in for EssayCorrectionService.retry's real AI call - Task 5/6
    already covers the correction engine's own retry logic exhaustively
    (tests/test_r3_essay_correction_service.py), so this test proves the
    ROUTE wiring only. Without this patch, the route's plain
    ``EssayCorrectionService(session)`` would build a real provider via
    ``build_text_provider()`` and raise ``ProviderConfigurationError`` for
    missing OPENAI_API_KEY/OPENAI_MODEL - same reasoning, and the same
    patch target shape, as ``_fake_correct`` in
    tests/test_r3_essay_submission_confirm_triggers_correction.py."""
    correction = await self.session.get(EssayCorrection, essay_correction_id)
    correction.status = "PENDING_REVIEW"
    correction.correction_key = correction.correction_key or "k" * 64
    correction.model_version = correction.model_version or "gpt-test"
    correction.ai_output = {"scores": {"per_competency": {}, "total": 600}}
    correction.final_scores = {
        "per_competency": {
            "C1": {"points": 120, "confidence": 0.8}, "C2": {"points": 120, "confidence": 0.8},
            "C3": {"points": 120, "confidence": 0.8}, "C4": {"points": 120, "confidence": 0.8},
            "C5": {"points": 120, "confidence": 0.8},
        },
        "total": 600,
    }
    correction.final_feedback = {"strengths": [], "improvements": [], "next_essay_strategy": "..."}
    correction.failure_reason = None
    await self.session.flush()
    return correction


class EssayCorrectionsRoutesTests(unittest.TestCase):
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
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed_pending_correction(
        self, code: str, *, school_id: uuid.UUID | None = None, status: str = "PENDING_REVIEW",
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Seeds one correction (PENDING_REVIEW by default). Pass an existing
        ``school_id`` to add a second correction to a school an earlier call
        already created (needed for tests that must prove two corrections in
        the SAME school behave independently, as opposed to one being
        rejected merely for belonging to a different school). Pass
        ``status="NEEDS_REVIEW"`` to seed a failed correction for the
        /retry tests - a NEEDS_REVIEW row has no ai_output/correction_key/
        model_version yet (same shape _run_ai's failure_fields produces),
        which every relevant CHECK constraint on essay_corrections already
        permits (ck_essay_corrections_non_failed_has_ai_output only applies
        to non-NEEDS_REVIEW rows)."""
        async def _seed():
            async with self.factory() as session:
                target_school_id = school_id
                if target_school_id is None:
                    school = School(id=uuid.uuid4(), code=f"REVR-{code}", name=f"school-{code}")
                    session.add(school)
                    await session.flush()
                    session.add(UserSchoolLink(
                        external_user_id=f"teacher_{code}", school_id=school.id, role="TEACHER",
                        scope_type="SCHOOL", active=True,
                    ))
                    target_school_id = school.id
                # A real Person row is required here (not just a random
                # person_id): list_essay_corrections (Task 4) now INNER
                # JOINs EssayCorrection -> EssaySubmission -> Student ->
                # Person to enrich the response with student_name, so a
                # Student pointing at a nonexistent Person would silently
                # drop out of the list results.
                person = Person(id=uuid.uuid4(), school_id=target_school_id, full_name=f"Aluno {code}")
                session.add(person)
                await session.flush()
                student = Student(
                    id=uuid.uuid4(), school_id=target_school_id, person_id=person.id,
                    student_code=f"ST-{code}",
                )
                session.add(student)
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=target_school_id, title="Tema", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=target_school_id, essay_prompt_id=prompt.id,
                    class_id=uuid.uuid4(), assigned_by_external_identity="teacher:t",
                )
                session.add(assignment)
                await session.flush()
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=target_school_id,
                    prompt_assignment_id=assignment.id, student_id=student.id,
                    mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                    canonical_text="Redacao.", normalized_text_hash="a" * 64,
                    # NOTE (fixture gap fixed here): the brief's own Step-1 code
                    # omitted submitted_at. ck_essay_submissions_submitted_at_presence
                    # requires it whenever status='SUBMITTED' - mirrored from the
                    # real sibling fixtures in test_r3_essay_correction_model.py /
                    # test_r3_essay_correction_service.py, which already seed it.
                    submitted_at=datetime.now(timezone.utc),
                )
                session.add(submission)
                await session.flush()
                if status == "NEEDS_REVIEW":
                    correction = EssayCorrection(
                        id=uuid.uuid4(), school_id=target_school_id, essay_submission_id=submission.id,
                        correction_key=None, rubric_version="ENEM_2025", model_version=None,
                        prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                        ai_output=None, final_scores=None, final_feedback=None,
                        failure_reason="ProviderConfigurationError: stub seed failure",
                        status="NEEDS_REVIEW",
                    )
                else:
                    # Terminal statuses (APPROVED/REJECTED) have two CHECK
                    # constraints not exercised by any pre-existing caller of
                    # this helper (which only ever passed NEEDS_REVIEW or the
                    # PENDING_REVIEW default): ck_essay_corrections_terminal_
                    # has_reviewed_at requires reviewed_at whenever status is
                    # APPROVED or REJECTED, and ck_essay_corrections_approved_
                    # has_published_at requires published_at specifically for
                    # APPROVED. Both are set here so status="APPROVED"/
                    # "REJECTED" (as the docstring above already promises)
                    # actually inserts instead of raising IntegrityError.
                    now = datetime.now(timezone.utc)
                    is_terminal = status in ("APPROVED", "REJECTED")
                    correction = EssayCorrection(
                        id=uuid.uuid4(), school_id=target_school_id, essay_submission_id=submission.id,
                        correction_key="k" * 64, rubric_version="ENEM_2025", model_version="gpt-test",
                        prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                        ai_output={"scores": {"per_competency": {}, "total": 600}},
                        final_scores={
                            "per_competency": {
                                "C1": {"points": 120, "confidence": 0.8}, "C2": {"points": 120, "confidence": 0.8},
                                "C3": {"points": 120, "confidence": 0.8}, "C4": {"points": 120, "confidence": 0.8},
                                "C5": {"points": 120, "confidence": 0.8},
                            },
                            "total": 600,
                        },
                        final_feedback={"strengths": [], "improvements": [], "next_essay_strategy": "..."},
                        status=status,
                        reviewed_by_external_identity=f"teacher:{code}" if is_terminal else None,
                        reviewed_at=now if is_terminal else None,
                        published_at=now if status == "APPROVED" else None,
                    )
                session.add(correction)
                await session.commit()
                return correction.id, target_school_id

        return self.loop.run_until_complete(_seed())

    def test_list_returns_pending_reviews_for_own_school(self):
        correction_id, _school_id = self._seed_pending_correction("1")
        self._as("teacher_1")
        resp = self.client.get("/api/v1/teacher/essay-corrections")
        self.assertEqual(resp.status_code, 200, resp.text)
        ids = [row["id"] for row in resp.json()]
        self.assertEqual(ids, [str(correction_id)])

    def test_approve_publishes(self):
        correction_id, _school_id = self._seed_pending_correction("2")
        self._as("teacher_2")
        resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/approve", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "APPROVED")
        self.assertEqual(body["reviewed_by_external_identity"], "teacher_2")

    def test_approve_with_score_edit(self):
        correction_id, _school_id = self._seed_pending_correction("3")
        self._as("teacher_3")
        new_scores = {
            "per_competency": {
                "C1": {"points": 200, "confidence": 1.0}, "C2": {"points": 200, "confidence": 1.0},
                "C3": {"points": 200, "confidence": 1.0}, "C4": {"points": 200, "confidence": 1.0},
                "C5": {"points": 200, "confidence": 1.0},
            },
            "total": 1000,
        }
        resp = self.client.post(
            f"/api/v1/teacher/essay-corrections/{correction_id}/approve",
            json={"final_scores": new_scores},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["final_scores"]["total"], 1000)

    def test_edit_approved_updates_final_scores(self):
        correction_id, _school_id = self._seed_pending_correction("30", status="APPROVED")
        self._as("teacher_30")
        new_scores = {
            "per_competency": {
                "C1": {"points": 200, "confidence": 1.0}, "C2": {"points": 200, "confidence": 1.0},
                "C3": {"points": 200, "confidence": 1.0}, "C4": {"points": 200, "confidence": 1.0},
                "C5": {"points": 200, "confidence": 1.0},
            },
            "total": 1000,
        }
        resp = self.client.post(
            f"/api/v1/teacher/essay-corrections/{correction_id}/edit",
            json={"final_scores": new_scores},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["final_scores"]["total"], 1000)
        self.assertEqual(body["status"], "APPROVED")
        self.assertEqual(body["reviewed_by_external_identity"], "teacher_30")

        list_resp = self.client.get("/api/v1/teacher/essay-corrections?status=APPROVED")
        self.assertEqual(list_resp.status_code, 200, list_resp.text)
        rows = [row for row in list_resp.json() if row["id"] == str(correction_id)]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["final_scores"]["total"], 1000)

    def test_edit_pending_review_is_409(self):
        correction_id, _school_id = self._seed_pending_correction("31")
        self._as("teacher_31")
        resp = self.client.post(
            f"/api/v1/teacher/essay-corrections/{correction_id}/edit",
            json={"final_feedback": {"next_essay_strategy": "novo"}},
        )
        self.assertEqual(resp.status_code, 409)

    def test_edit_does_not_change_status_or_published_at(self):
        correction_id, _school_id = self._seed_pending_correction("32", status="APPROVED")

        async def _get_published_at():
            async with self.factory() as session:
                correction = await session.get(EssayCorrection, correction_id)
                return correction.status, correction.published_at, correction.reviewed_at

        status_before, published_at_before, reviewed_at_before = self.loop.run_until_complete(
            _get_published_at()
        )

        self._as("teacher_32")
        resp = self.client.post(
            f"/api/v1/teacher/essay-corrections/{correction_id}/edit",
            json={"final_feedback": {"next_essay_strategy": "atualizado"}},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        status_after, published_at_after, reviewed_at_after = self.loop.run_until_complete(
            _get_published_at()
        )
        self.assertEqual(status_after, status_before)
        self.assertEqual(published_at_after, published_at_before)
        self.assertEqual(reviewed_at_after, reviewed_at_before)

    def test_edit_from_another_school_is_403(self):
        correction_id, _school_id = self._seed_pending_correction("33", status="APPROVED")
        self._seed_pending_correction("34")
        self._as("teacher_34")
        resp = self.client.post(
            f"/api/v1/teacher/essay-corrections/{correction_id}/edit",
            json={"final_feedback": {"next_essay_strategy": "novo"}},
        )
        self.assertEqual(resp.status_code, 403)

    def test_edit_with_malformed_final_scores_is_422_and_keeps_old_values(self):
        correction_id, _school_id = self._seed_pending_correction("35", status="APPROVED")

        async def _get_final_scores():
            async with self.factory() as session:
                correction = await session.get(EssayCorrection, correction_id)
                return correction.final_scores

        scores_before = self.loop.run_until_complete(_get_final_scores())

        self._as("teacher_35")
        # Missing C5 (only 4 of the 5 required competencies) - Scores'
        # model_validator requires all of COMPETENCY_CODES, so this must be
        # rejected by the same shape check /approve already applies.
        malformed_scores = {
            "per_competency": {
                "C1": {"points": 200, "confidence": 1.0}, "C2": {"points": 200, "confidence": 1.0},
                "C3": {"points": 200, "confidence": 1.0}, "C4": {"points": 200, "confidence": 1.0},
            },
            "total": 800,
        }
        resp = self.client.post(
            f"/api/v1/teacher/essay-corrections/{correction_id}/edit",
            json={"final_scores": malformed_scores},
        )
        self.assertEqual(resp.status_code, 422, resp.text)

        scores_after = self.loop.run_until_complete(_get_final_scores())
        self.assertEqual(scores_after, scores_before)
        self.assertNotEqual(scores_after, malformed_scores)

    def test_edit_with_final_scores_bad_points_scale_is_422(self):
        correction_id, _school_id = self._seed_pending_correction("36", status="APPROVED")
        self._as("teacher_36")
        # 50 is not one of the official level points (0/40/80/120/160/200).
        bad_points_scores = {
            "per_competency": {
                "C1": {"points": 50, "confidence": 1.0}, "C2": {"points": 200, "confidence": 1.0},
                "C3": {"points": 200, "confidence": 1.0}, "C4": {"points": 200, "confidence": 1.0},
                "C5": {"points": 200, "confidence": 1.0},
            },
            "total": 850,
        }
        resp = self.client.post(
            f"/api/v1/teacher/essay-corrections/{correction_id}/edit",
            json={"final_scores": bad_points_scores},
        )
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_edit_with_final_scores_inconsistent_total_is_422(self):
        correction_id, _school_id = self._seed_pending_correction("37", status="APPROVED")
        self._as("teacher_37")
        # total (999) does not match the sum of the five competencies (1000).
        inconsistent_total_scores = {
            "per_competency": {
                "C1": {"points": 200, "confidence": 1.0}, "C2": {"points": 200, "confidence": 1.0},
                "C3": {"points": 200, "confidence": 1.0}, "C4": {"points": 200, "confidence": 1.0},
                "C5": {"points": 200, "confidence": 1.0},
            },
            "total": 999,
        }
        resp = self.client.post(
            f"/api/v1/teacher/essay-corrections/{correction_id}/edit",
            json={"final_scores": inconsistent_total_scores},
        )
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_edit_with_malformed_final_feedback_is_422_and_keeps_old_values(self):
        correction_id, _school_id = self._seed_pending_correction("38", status="APPROVED")

        async def _get_final_feedback():
            async with self.factory() as session:
                correction = await session.get(EssayCorrection, correction_id)
                return correction.final_feedback

        feedback_before = self.loop.run_until_complete(_get_final_feedback())

        self._as("teacher_38")
        # next_essay_strategy has min_length=1 and is required by Feedback.
        malformed_feedback = {"strengths": [], "improvements": [], "next_essay_strategy": ""}
        resp = self.client.post(
            f"/api/v1/teacher/essay-corrections/{correction_id}/edit",
            json={"final_feedback": malformed_feedback},
        )
        self.assertEqual(resp.status_code, 422, resp.text)

        feedback_after = self.loop.run_until_complete(_get_final_feedback())
        self.assertEqual(feedback_after, feedback_before)

    def test_reject(self):
        correction_id, _school_id = self._seed_pending_correction("4")
        self._as("teacher_4")
        resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/reject")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "REJECTED")

    def test_a_correction_from_another_school_is_403(self):
        correction_id, _school_id = self._seed_pending_correction("5")
        self._seed_pending_correction("6")
        self._as("teacher_6")
        resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/reject")
        self.assertEqual(resp.status_code, 403)

    def test_a_student_cannot_approve(self):
        correction_id, school_id = self._seed_pending_correction("7")

        async def _add_student_link():
            async with self.factory() as session:
                session.add(UserSchoolLink(
                    external_user_id="student_7", school_id=school_id, role="STUDENT",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()

        self.loop.run_until_complete(_add_student_link())
        self._as("student_7")
        resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/approve", json={})
        self.assertEqual(resp.status_code, 403)

    def test_bulk_approve_is_best_effort(self):
        ok_id, school_id = self._seed_pending_correction("8")
        already_rejected_id, _same_school_id = self._seed_pending_correction("9", school_id=school_id)

        async def _reject_one():
            async with self.factory() as session:
                from agente_ia_edu.services.essay_correction import EssayCorrectionService
                await EssayCorrectionService(session).reject(
                    already_rejected_id, reviewed_by_external_identity="teacher:other"
                )
                await session.commit()

        self.loop.run_until_complete(_reject_one())
        self._as("teacher_8")
        resp = self.client.post(
            "/api/v1/teacher/essay-corrections/bulk-approve",
            json={"essay_correction_ids": [str(ok_id), str(already_rejected_id)]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual([row["id"] for row in body["approved"]], [str(ok_id)])
        self.assertIn(str(already_rejected_id), body["failures"])

    def test_bulk_approve_rejects_an_id_from_another_school(self):
        ok_id, school_id = self._seed_pending_correction("13")
        other_id, _other_school_id = self._seed_pending_correction("14")
        self._as("teacher_13")
        resp = self.client.post(
            "/api/v1/teacher/essay-corrections/bulk-approve",
            json={"essay_correction_ids": [str(ok_id), str(other_id)]},
        )
        self.assertEqual(resp.status_code, 403)

    def test_retry_succeeds_for_own_school(self):
        correction_id, _school_id = self._seed_pending_correction("10", status="NEEDS_REVIEW")
        self._as("teacher_10")
        with patch(
            "agente_ia_edu.services.essay_correction.EssayCorrectionService.retry",
            new=_fake_retry,
        ):
            resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/retry")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "PENDING_REVIEW")

    def test_retry_from_another_school_is_403(self):
        correction_id, _school_id = self._seed_pending_correction("11", status="NEEDS_REVIEW")
        self._seed_pending_correction("12", status="NEEDS_REVIEW")
        self._as("teacher_12")
        # No patch needed: the 403 guard runs before EssayCorrectionService.retry
        # is ever called, so this never reaches (and never needs to stub) the
        # real provider - that's the entire point of the "403, never 404/422,
        # for not yours" rule being checked BEFORE the service sees the id.
        resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/retry")
        self.assertEqual(resp.status_code, 403)

    def test_export_pdf_approved_returns_pdf_bytes(self):
        correction_id, _school_id = self._seed_pending_correction("20", status="APPROVED")
        self._as("teacher_20")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/export.pdf")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.headers["content-type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF-"))

    def test_export_pdf_pending_review_returns_pdf_bytes(self):
        """Unlike the student route, PENDING_REVIEW is exportable for the teacher."""
        correction_id, _school_id = self._seed_pending_correction("21")
        self._as("teacher_21")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/export.pdf")
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_export_pdf_needs_review_is_404(self):
        correction_id, _school_id = self._seed_pending_correction("22", status="NEEDS_REVIEW")
        self._as("teacher_22")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/export.pdf")
        self.assertEqual(resp.status_code, 404)

    def test_export_pdf_rejected_is_404(self):
        correction_id, _school_id = self._seed_pending_correction("23", status="REJECTED")
        self._as("teacher_23")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/export.pdf")
        self.assertEqual(resp.status_code, 404)

    def test_export_pdf_from_another_school_is_403(self):
        correction_id, _school_id = self._seed_pending_correction("24", status="APPROVED")
        self._seed_pending_correction("25")
        self._as("teacher_25")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/export.pdf")
        self.assertEqual(resp.status_code, 403)

    def test_export_pdf_503_when_pymupdf_unavailable(self):
        correction_id, _school_id = self._seed_pending_correction("26", status="APPROVED")
        self._as("teacher_26")
        with patch("agente_ia_edu.api.routes.essay_corrections.pdf_available", return_value=False):
            resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/export.pdf")
        self.assertEqual(resp.status_code, 503)


class EssayEvolutionTeacherRoutesTests(unittest.TestCase):
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
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed_school_with_teacher(self, code: str):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"EVT-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id=f"teacher_{code}", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()
                return school.id

        return self.loop.run_until_complete(_seed())

    def _seed_student_with_correction(self, code: str, school_id, *, student_name: str, total):
        async def _seed():
            async with self.factory() as session:
                person = Person(id=uuid.uuid4(), school_id=school_id, full_name=student_name)
                session.add(person)
                await session.flush()
                student = Student(id=uuid.uuid4(), school_id=school_id, person_id=person.id, student_code=f"ST-{code}")
                session.add(student)
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school_id, title="Tema", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school_id, essay_prompt_id=prompt.id,
                    class_id=uuid.uuid4(), assigned_by_external_identity="teacher:t",
                )
                session.add(assignment)
                await session.flush()
                now = datetime.now(timezone.utc)
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school_id,
                    prompt_assignment_id=assignment.id, student_id=student.id,
                    mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                    canonical_text="Redacao.", normalized_text_hash="a" * 64,
                    submitted_at=now,
                )
                session.add(submission)
                await session.flush()
                session.add(EssayCorrection(
                    id=uuid.uuid4(), school_id=school_id, essay_submission_id=submission.id,
                    correction_key="k" * 64, rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output={"annotations": [], "rewrites": [], "intervention": {}, "alerts": []},
                    final_scores={
                        "total": total,
                        "per_competency": {
                            c: {"points": total // 5, "confidence": 1.0} for c in ("C1", "C2", "C3", "C4", "C5")
                        },
                    },
                    final_feedback={}, status="APPROVED", reviewed_at=now, published_at=now,
                ))
                await session.commit()
                return student.id

        return self.loop.run_until_complete(_seed())

    def test_evolution_for_student_in_own_school(self):
        school_id = self._seed_school_with_teacher("1")
        student_id = self._seed_student_with_correction("1", school_id, student_name="Ana", total=700)
        self._as("teacher_1")
        resp = self.client.get(f"/api/v1/teacher/essay-evolution?student_id={student_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body["entries"]), 1)
        self.assertEqual(body["entries"][0]["total"], 700)

    def test_evolution_for_student_in_another_school_returns_empty(self):
        school_id = self._seed_school_with_teacher("2")
        other_school_id = self._seed_school_with_teacher("3")
        other_student_id = self._seed_student_with_correction(
            "3", other_school_id, student_name="Bia", total=700,
        )
        self._as("teacher_2")
        resp = self.client.get(f"/api/v1/teacher/essay-evolution?student_id={other_student_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        # school_id scoping in build_evolution() means a student from a
        # DIFFERENT school simply has no matching rows, not a 403 - same
        # non-restrictive-by-classroom rule list_essay_corrections already
        # uses (dashboard spec §4).
        self.assertEqual(resp.json()["entries"], [])

    def test_list_students_filters_by_school_and_name(self):
        school_id = self._seed_school_with_teacher("4")
        self._seed_student_with_correction("4a", school_id, student_name="Carla Souza", total=700)
        self._seed_student_with_correction("4b", school_id, student_name="Daniel Reis", total=650)
        self._as("teacher_4")
        resp = self.client.get("/api/v1/teacher/essay-evolution/students")
        self.assertEqual(resp.status_code, 200, resp.text)
        names = sorted(row["student_name"] for row in resp.json())
        self.assertEqual(names, ["Carla Souza", "Daniel Reis"])

        resp = self.client.get("/api/v1/teacher/essay-evolution/students?q=carla")
        self.assertEqual(resp.status_code, 200, resp.text)
        names = [row["student_name"] for row in resp.json()]
        self.assertEqual(names, ["Carla Souza"])

    def test_list_students_excludes_students_without_approved_corrections(self):
        school_id = self._seed_school_with_teacher("5")
        # A student who exists but has no APPROVED correction yet must not
        # show up in the selector (dashboard spec §2: "lists students ...
        # with >= 1 approved correction").
        async def _seed_unapproved():
            async with self.factory() as session:
                person = Person(id=uuid.uuid4(), school_id=school_id, full_name="Sem Correção")
                session.add(person)
                await session.flush()
                session.add(Student(id=uuid.uuid4(), school_id=school_id, person_id=person.id, student_code="ST-5x"))
                await session.commit()

        self.loop.run_until_complete(_seed_unapproved())
        self._as("teacher_5")
        resp = self.client.get("/api/v1/teacher/essay-evolution/students")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), [])

    def test_list_students_excludes_students_with_only_pending_review_correction(self):
        school_id = self._seed_school_with_teacher("6")
        # A student whose only correction is PENDING_REVIEW (not yet
        # approved) must not show up in the selector either (dashboard
        # spec §2: "lists students ... with >= 1 approved correction").
        async def _seed_pending():
            async with self.factory() as session:
                person = Person(id=uuid.uuid4(), school_id=school_id, full_name="Pendente Review")
                session.add(person)
                await session.flush()
                student = Student(id=uuid.uuid4(), school_id=school_id, person_id=person.id, student_code="ST-6p")
                session.add(student)
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school_id, title="Tema", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school_id, essay_prompt_id=prompt.id,
                    class_id=uuid.uuid4(), assigned_by_external_identity="teacher:t",
                )
                session.add(assignment)
                await session.flush()
                now = datetime.now(timezone.utc)
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school_id,
                    prompt_assignment_id=assignment.id, student_id=student.id,
                    mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                    canonical_text="Redacao.", normalized_text_hash="c" * 64,
                    submitted_at=now,
                )
                session.add(submission)
                await session.flush()
                session.add(EssayCorrection(
                    id=uuid.uuid4(), school_id=school_id, essay_submission_id=submission.id,
                    correction_key="q" * 64, rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output={"annotations": [], "rewrites": [], "intervention": {}, "alerts": []},
                    final_scores={
                        "total": 700,
                        "per_competency": {
                            c: {"points": 140, "confidence": 1.0} for c in ("C1", "C2", "C3", "C4", "C5")
                        },
                    },
                    final_feedback={}, status="PENDING_REVIEW",
                ))
                await session.commit()

        self.loop.run_until_complete(_seed_pending())
        self._as("teacher_6")
        resp = self.client.get("/api/v1/teacher/essay-evolution/students")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), [])


if __name__ == "__main__":
    unittest.main()
