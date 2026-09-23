"""canonical_text on the student's own devolutiva - a gap found while
designing the visual-annotation overlay feature: the only place
canonical_text existed before was the POST/confirm response body, which
doesn't survive a page reload. Mirrors the same PENDING/APPROVED gating
every other field on this response already uses."""
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


class StudentCorrectionCanonicalTextTests(unittest.TestCase):
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

    def _seed(self, code: str, *, correction_status: str | None, anchor_mode: str = "TEXT_OFFSET",
               canonical_text: str | None = "Uma redacao digitada qualquer.") -> uuid.UUID:
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"CT-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(SchoolModule(
                    id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
                ))
                session.add(UserSchoolLink(
                    external_user_id=f"student_ct_{code}", school_id=school.id, role="STUDENT",
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
                    external_identity_provider="test", external_user_id=f"student_ct_{code}",
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
                submitted_at = datetime.now(timezone.utc)
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                    prompt_assignment_id=assignment.id, student_id=student.id,
                    mode="TYPED" if anchor_mode == "TEXT_OFFSET" else "PHOTO",
                    anchor_mode=anchor_mode, status="SUBMITTED", canonical_text=canonical_text,
                    # Same two paired CHECK constraints as Task 1's fixture (see its
                    # comment): canonical_text/normalized_text_hash null together,
                    # submitted_at required whenever status=SUBMITTED.
                    normalized_text_hash=("h" * 64) if canonical_text is not None else None,
                    submitted_at=submitted_at,
                )
                session.add(submission)
                await session.flush()
                if correction_status is not None:
                    session.add(EssayCorrection(
                        id=uuid.uuid4(), school_id=school.id, essay_submission_id=submission.id,
                        correction_key="k" * 64, rubric_version="ENEM_2025", model_version="stub",
                        prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                        # ai_output must be non-null for every status except
                        # NEEDS_REVIEW (ck_essay_corrections_non_failed_has_ai_output)
                        # - this fixture never exercises NEEDS_REVIEW, so it's set
                        # unconditionally here.
                        ai_output={"scores": None}, status=correction_status,
                        **({
                            "final_scores": {"total": 800, "per_competency": {}},
                            "final_feedback": {"strengths": [], "improvements": [], "next_essay_strategy": "x"},
                            "reviewed_at": submitted_at, "published_at": submitted_at,
                        } if correction_status == "APPROVED" else {}),
                    ))
                submission_id = submission.id
                await session.commit()
                return submission_id

        return self.loop.run_until_complete(_seed_async())

    def test_canonical_text_present_when_approved(self):
        submission_id = self._seed("1", correction_status="APPROVED")
        self._as("student_ct_1")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["canonical_text"], "Uma redacao digitada qualquer.")

    def test_canonical_text_null_when_pending(self):
        submission_id = self._seed("2", correction_status="PENDING_REVIEW")
        self._as("student_ct_2")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "PENDING")
        self.assertIsNone(body["canonical_text"])

    def test_canonical_text_null_for_image_region_submission(self):
        submission_id = self._seed(
            "3", correction_status="APPROVED", anchor_mode="IMAGE_REGION", canonical_text=None,
        )
        self._as("student_ct_3")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsNone(resp.json()["canonical_text"])


if __name__ == "__main__":
    unittest.main()
