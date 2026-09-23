"""Teacher-facing routes exposing a submission's own content (canonical
text or page images), for the visual-annotation overlay feature. Mirrors
the 403-not-404 pattern every other route in essay_corrections.py already
uses - a correction from another school is 403, never 404/422."""
import asyncio
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

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
    EssaySubmissionPage,
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


class TeacherSubmissionContentRouteTests(unittest.TestCase):
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
        cls.tmp_dir = Path("/tmp/r_submission_content_fixtures")
        cls.tmp_dir.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed(self, code: str, *, anchor_mode: str, canonical_text: str | None,
               pages: list[str] | None = None) -> tuple[uuid.UUID, uuid.UUID]:
        """Returns (school_id, essay_correction_id)."""
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"SC-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(SchoolModule(
                    id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
                ))
                session.add(UserSchoolLink(
                    external_user_id=f"teacher_{code}", school_id=school.id, role="TEACHER",
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
                    mode="TYPED" if anchor_mode == "TEXT_OFFSET" else "PHOTO",
                    anchor_mode=anchor_mode, status="SUBMITTED", canonical_text=canonical_text,
                    # Two CHECK constraints on essay_submissions (db/models/essay_proposal.py)
                    # require these paired with the fields above: canonical_text and
                    # normalized_text_hash must be null/non-null together, and
                    # submitted_at must be set whenever status is SUBMITTED/SUPERSEDED.
                    normalized_text_hash=("h" * 64) if canonical_text is not None else None,
                    submitted_at=datetime.now(timezone.utc),
                )
                session.add(submission)
                await session.flush()
                for i, uri in enumerate(pages or [], start=1):
                    session.add(EssaySubmissionPage(
                        id=uuid.uuid4(), essay_submission_id=submission.id,
                        page_number=i, storage_uri=uri,
                    ))
                correction = EssayCorrection(
                    id=uuid.uuid4(), school_id=school.id, essay_submission_id=submission.id,
                    correction_key="k" * 64, rubric_version="ENEM_2025", model_version="stub",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output={"scores": None}, status="PENDING_REVIEW",
                )
                session.add(correction)
                await session.commit()
                return school.id, correction.id

        return self.loop.run_until_complete(_seed_async())

    def test_text_offset_submission_returns_canonical_text_and_no_pages(self):
        _, correction_id = self._seed("1", anchor_mode="TEXT_OFFSET", canonical_text="Uma redacao qualquer.")
        self._as("teacher_1")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/submission-content")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["anchor_mode"], "TEXT_OFFSET")
        self.assertEqual(body["canonical_text"], "Uma redacao qualquer.")
        self.assertIsNone(body["pages"])

    def test_image_region_submission_returns_pages_and_no_text(self):
        source = self.tmp_dir / "page1.png"
        import pymupdf
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
        pix.clear_with(200)
        pix.save(str(source))
        _, correction_id = self._seed(
            "2", anchor_mode="IMAGE_REGION", canonical_text=None, pages=[str(source)],
        )
        self._as("teacher_2")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/submission-content")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["anchor_mode"], "IMAGE_REGION")
        self.assertIsNone(body["canonical_text"])
        self.assertEqual(body["pages"], [{"page_number": 1}])

    def test_submission_content_from_another_school_is_403(self):
        _, correction_id = self._seed("3", anchor_mode="TEXT_OFFSET", canonical_text="x")
        self._seed("4", anchor_mode="TEXT_OFFSET", canonical_text="y")
        self._as("teacher_4")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/submission-content")
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_page_image_returns_bytes_for_owner_school(self):
        source = self.tmp_dir / "page_bytes.png"
        import pymupdf
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
        pix.clear_with(100)
        pix.save(str(source))
        _, correction_id = self._seed(
            "5", anchor_mode="IMAGE_REGION", canonical_text=None, pages=[str(source)],
        )
        self._as("teacher_5")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/pages/1/image")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, source.read_bytes())

    def test_page_image_from_another_school_is_403(self):
        source = self.tmp_dir / "page_other.png"
        import pymupdf
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
        pix.clear_with(50)
        pix.save(str(source))
        _, correction_id = self._seed(
            "6", anchor_mode="IMAGE_REGION", canonical_text=None, pages=[str(source)],
        )
        self._seed("7", anchor_mode="TEXT_OFFSET", canonical_text="z")
        self._as("teacher_7")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/pages/1/image")
        self.assertEqual(resp.status_code, 403)

    def test_page_image_missing_page_number_is_404(self):
        _, correction_id = self._seed("8", anchor_mode="IMAGE_REGION", canonical_text=None, pages=[])
        self._as("teacher_8")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/pages/1/image")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
