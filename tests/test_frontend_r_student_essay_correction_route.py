import asyncio
import unittest
import uuid
from datetime import datetime, timezone

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

    def test_another_students_submission_is_403(self):
        submission_id = self._seed_submission("5")
        self._add_correction(submission_id, status="APPROVED", with_content=True)
        self._seed_submission("6")
        self._as("student_6")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
