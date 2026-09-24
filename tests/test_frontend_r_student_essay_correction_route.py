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
    AcademicYear,
    Class,
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    GradeLevel,
    Person,
    PromptAssignment,
    School,
    SchoolModule,
    Segment,
    Student,
    StudentEnrollment,
    User,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.institution_settings import InstitutionSettingsService


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class StudentEssayCorrectionRouteTests(unittest.TestCase):
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

    def _seed_submission(self, code: str):
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"SEC-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(SchoolModule(
                    id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
                ))
                session.add(UserSchoolLink(
                    external_user_id=f"student_{code}", school_id=school.id, role="STUDENT",
                    scope_type="SCHOOL", active=True,
                ))
                segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEG-{code}")
                session.add(segment)
                await session.flush()
                grade = GradeLevel(
                    id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                    name="grade", external_id=f"GRADE-{code}",
                )
                year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEAR-{code}")
                session.add_all([grade, year])
                await session.flush()
                klass = Class(
                    id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                    grade_level_id=grade.id, name="turma", external_id=f"TURMA-{code}",
                )
                session.add(klass)
                person = Person(id=uuid.uuid4(), school_id=school.id, full_name=f"Aluno {code}")
                session.add(person)
                await session.flush()
                session.add(User(
                    id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                    external_identity_provider="test", external_user_id=f"student_{code}",
                ))
                student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code=f"ST-{code}")
                session.add(student)
                await session.flush()
                session.add(StudentEnrollment(
                    id=uuid.uuid4(), school_id=school.id, student_id=student.id, class_id=klass.id,
                    status="ACTIVE",
                ))
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                    class_id=klass.id, assigned_by_external_identity="teacher:t",
                )
                session.add(assignment)
                await session.flush()
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                    prompt_assignment_id=assignment.id, student_id=student.id,
                    mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                    canonical_text="Redacao.", normalized_text_hash="a" * 64,
                    submitted_at=datetime.now(timezone.utc),
                )
                session.add(submission)
                await session.commit()
                return submission.id

        return self.loop.run_until_complete(_seed_async())

    def _add_correction(self, submission_id, *, status: str, with_content: bool):
        async def _add_async():
            async with self.factory() as session:
                submission = await session.get(EssaySubmission, submission_id)
                ai_output = None
                final_scores = None
                final_feedback = None
                if with_content:
                    ai_output = {
                        "annotations": [{"letter": "A", "short_comment": "ok"}],
                        "rewrites": [], "intervention": {"respeita_direitos_humanos": True}, "alerts": [],
                    }
                    final_scores = {"total": 800}
                    final_feedback = {"next_essay_strategy": "Revisar conectivos."}
                elif status != "NEEDS_REVIEW":
                    # Non-NEEDS_REVIEW statuses require ai_output per model constraint
                    ai_output = {"annotations": [], "rewrites": [], "intervention": {}, "alerts": []}
                session.add(EssayCorrection(
                    id=uuid.uuid4(), school_id=submission.school_id,
                    essay_submission_id=submission_id, correction_key="k" * 64,
                    rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output=ai_output, final_scores=final_scores, final_feedback=final_feedback,
                    status=status,
                    reviewed_at=datetime.now(timezone.utc) if status in ("APPROVED", "REJECTED") else None,
                    published_at=datetime.now(timezone.utc) if status == "APPROVED" else None,
                ))
                await session.commit()

        self.loop.run_until_complete(_add_async())

    def _submission_row(self, submission_id):
        """Fetch fields the resubmission POST needs (prompt_assignment_id,
        essay_id) and fields assertions check afterwards (status), without
        every test having to hand-roll its own session/get boilerplate."""
        async def _fetch():
            async with self.factory() as session:
                submission = await session.get(EssaySubmission, submission_id)
                return {
                    "essay_id": submission.essay_id,
                    "prompt_assignment_id": submission.prompt_assignment_id,
                    "school_id": submission.school_id,
                    "status": submission.status,
                }

        return self.loop.run_until_complete(_fetch())

    def test_needs_review_collapses_to_pending_with_no_content(self):
        submission_id = self._seed_submission("1")
        self._add_correction(submission_id, status="NEEDS_REVIEW", with_content=False)
        self._as("student_1")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "PENDING")
        self.assertIsNone(body["final_scores"])
        self.assertIsNone(body["annotations"])

    def test_pending_review_also_collapses_to_pending(self):
        submission_id = self._seed_submission("2")
        self._add_correction(submission_id, status="PENDING_REVIEW", with_content=False)
        self._as("student_2")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "PENDING")

    def test_no_correction_yet_is_pending(self):
        submission_id = self._seed_submission("3")
        self._as("student_3")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "PENDING")

    def test_approved_exposes_full_content(self):
        submission_id = self._seed_submission("4")
        self._add_correction(submission_id, status="APPROVED", with_content=True)
        self._as("student_4")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "APPROVED")
        self.assertEqual(body["final_scores"]["total"], 800)
        self.assertEqual(len(body["annotations"]), 1)
        self.assertTrue(body["intervention"]["respeita_direitos_humanos"])

    def test_approved_exposes_the_new_devolutiva_fields(self):
        submission_id = self._seed_submission("10")

        async def _add():
            async with self.factory() as session:
                submission = await session.get(EssaySubmission, submission_id)
                session.add(EssayCorrection(
                    id=uuid.uuid4(), school_id=submission.school_id,
                    essay_submission_id=submission_id, correction_key="k" * 64,
                    rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v2", engine_version="r3_correction_engine_v1",
                    ai_output={
                        "annotations": [], "rewrites": [],
                        "intervention": {"respeita_direitos_humanos": True}, "alerts": [],
                        "rationales": [{
                            "competency_code": "C1", "summary": "ok",
                            "strengths": "boa norma", "growth_area": "revisar crase",
                            "signal_keys": [],
                        }],
                        "intro_message": "Ola!", "closing_message": "Continue assim!",
                        "mechanical_review": [{
                            "category": "CRASE", "excerpt": "a ela",
                            "suggested_form": "à ela", "rule_explanation": "fusao de a+a",
                        }],
                    },
                    final_scores={"total": 800}, final_feedback={"next_essay_strategy": "Revisar conectivos."},
                    status="APPROVED",
                    reviewed_at=datetime.now(timezone.utc), published_at=datetime.now(timezone.utc),
                ))
                await session.commit()

        self.loop.run_until_complete(_add())
        self._as("student_10")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["rationales"][0]["strengths"], "boa norma")
        self.assertEqual(body["intro_message"], "Ola!")
        self.assertEqual(body["closing_message"], "Continue assim!")
        self.assertEqual(body["mechanical_review"][0]["category"], "CRASE")

    def test_approved_with_old_ai_output_has_none_for_new_fields(self):
        # ai_output WITHOUT the new keys - simulates a correction approved
        # before this leva. Must not error: the four new fields come back
        # as null, same as annotations/rewrites already do in with_content=False.
        submission_id = self._seed_submission("11")

        async def _add():
            async with self.factory() as session:
                submission = await session.get(EssaySubmission, submission_id)
                session.add(EssayCorrection(
                    id=uuid.uuid4(), school_id=submission.school_id,
                    essay_submission_id=submission_id, correction_key="k" * 64,
                    rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output={
                        "annotations": [], "rewrites": [],
                        "intervention": {"respeita_direitos_humanos": True}, "alerts": [],
                        "rationales": [{"competency_code": "C1", "summary": "ok", "signal_keys": []}],
                    },
                    final_scores={"total": 800}, final_feedback={"next_essay_strategy": "Revisar conectivos."},
                    status="APPROVED",
                    reviewed_at=datetime.now(timezone.utc), published_at=datetime.now(timezone.utc),
                ))
                await session.commit()

        self.loop.run_until_complete(_add())
        self._as("student_11")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIsNone(body["intro_message"])
        self.assertIsNone(body["closing_message"])
        self.assertIsNone(body["mechanical_review"])
        self.assertEqual(body["rationales"][0]["summary"], "ok")

    def test_approved_with_malformed_rationales_shape_degrades_to_none_not_500(self):
        # A demo/seed-script-created row can store rationales as a dict
        # instead of the contract's list. That must degrade to None (same
        # as a missing key), not 500 the student's own devolutiva.
        submission_id = self._seed_submission("12")

        async def _add():
            async with self.factory() as session:
                submission = await session.get(EssaySubmission, submission_id)
                session.add(EssayCorrection(
                    id=uuid.uuid4(), school_id=submission.school_id,
                    essay_submission_id=submission_id, correction_key="k" * 64,
                    rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v2", engine_version="r3_correction_engine_v1",
                    ai_output={
                        "annotations": [], "rewrites": [],
                        "intervention": {"respeita_direitos_humanos": True}, "alerts": [],
                        "rationales": {"not": "a list"},
                    },
                    final_scores={"total": 800}, final_feedback={"next_essay_strategy": "Revisar conectivos."},
                    status="APPROVED",
                    reviewed_at=datetime.now(timezone.utc), published_at=datetime.now(timezone.utc),
                ))
                await session.commit()

        self.loop.run_until_complete(_add())
        self._as("student_12")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsNone(resp.json()["rationales"])

    def test_another_students_submission_is_403(self):
        submission_id = self._seed_submission("5")
        self._add_correction(submission_id, status="APPROVED", with_content=True)
        self._seed_submission("6")
        self._as("student_6")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 403)

    def test_rejected_exposes_status_but_no_content(self):
        submission_id = self._seed_submission("7")
        # with_content=True prova que a rota ATIVAMENTE esconde o conteúdo em
        # REJECTED, não que o conteúdo simplesmente não existia no banco.
        self._add_correction(submission_id, status="REJECTED", with_content=True)
        self._as("student_7")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "REJECTED")
        self.assertIsNone(body["final_scores"])
        self.assertIsNone(body["final_feedback"])
        self.assertIsNone(body["annotations"])
        # canonical_text is the STUDENT'S OWN WRITING, not the AI's
        # unpublished output the assertions above withhold - REJECTED means
        # the teacher discarded the AI's correction, not the essay itself, so
        # the student (who already wrote this text) gets it back to build a
        # resubmission from instead of retyping it from memory.
        self.assertEqual(body["canonical_text"], "Redacao.")
        self.assertTrue(body["resubmission_allowed"])

    def test_rejected_resubmission_supersedes_and_reuses_essay_id(self):
        submission_id = self._seed_submission("8")
        self._add_correction(submission_id, status="REJECTED", with_content=True)
        original = self._submission_row(submission_id)
        self._as("student_8")

        resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={
                "prompt_assignment_id": str(original["prompt_assignment_id"]),
                "mode": "TYPED",
                "text": "Versao reenviada apos rejeicao.",
                "resubmit_essay_id": str(original["essay_id"]),
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertEqual(body["essay_id"], str(original["essay_id"]))
        self.assertNotEqual(body["id"], str(submission_id))

        refreshed = self._submission_row(submission_id)
        self.assertEqual(refreshed["status"], "SUPERSEDED")

    def test_rejected_resubmission_blocked_in_avaliativo_mode(self):
        submission_id = self._seed_submission("9")
        self._add_correction(submission_id, status="REJECTED", with_content=True)
        original = self._submission_row(submission_id)

        async def _configure_avaliativo():
            async with self.factory() as session:
                await InstitutionSettingsService(session).configure(
                    original["school_id"],
                    performed_by_external_id="admin:test",
                    correction_mode="AVALIATIVO",
                )
                await session.commit()

        self.loop.run_until_complete(_configure_avaliativo())
        self._as("student_9")

        resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={
                "prompt_assignment_id": str(original["prompt_assignment_id"]),
                "mode": "TYPED",
                "text": "Tentativa de reenvio negada.",
                "resubmit_essay_id": str(original["essay_id"]),
            },
        )
        self.assertEqual(resp.status_code, 409, resp.text)

    def test_export_pdf_approved_returns_pdf_bytes(self):
        submission_id = self._seed_submission("13")
        self._add_correction(submission_id, status="APPROVED", with_content=True)
        self._as("student_13")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction/export.pdf")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.headers["content-type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF-"))

    def test_export_pdf_pending_review_is_404(self):
        submission_id = self._seed_submission("14")
        self._add_correction(submission_id, status="PENDING_REVIEW", with_content=False)
        self._as("student_14")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction/export.pdf")
        self.assertEqual(resp.status_code, 404)

    def test_export_pdf_rejected_is_404(self):
        submission_id = self._seed_submission("15")
        self._add_correction(submission_id, status="REJECTED", with_content=True)
        self._as("student_15")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction/export.pdf")
        self.assertEqual(resp.status_code, 404)

    def test_export_pdf_another_students_submission_is_403(self):
        submission_id = self._seed_submission("16")
        self._add_correction(submission_id, status="APPROVED", with_content=True)
        self._seed_submission("17")
        self._as("student_17")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction/export.pdf")
        self.assertEqual(resp.status_code, 403)

    def test_export_pdf_503_when_pymupdf_unavailable(self):
        submission_id = self._seed_submission("18")
        self._add_correction(submission_id, status="APPROVED", with_content=True)
        self._as("student_18")
        with patch("agente_ia_edu.api.routes.essay_submissions.pdf_available", return_value=False):
            resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction/export.pdf")
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
