import unittest
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayPrompt,
    EssaySubmission,
    GradeLevel,
    PromptAssignment,
    School,
    Segment,
    Student,
)
from agente_ia_edu.services.essay_submission import EssaySubmissionService
from agente_ia_edu.services.material_storage import MaterialStorage


def _make_test_png(path: Path, width: int, height: int) -> None:
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.clear_with(255)
    path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(path))


class PageDimensionsOnUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.tmp_dir = Path("/tmp/r3_page_dimensions_test")
        self.storage = MaterialStorage(root=self.tmp_dir / "storage")

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _pending_submission(self, session) -> EssaySubmission:
        school = School(id=uuid.uuid4(), code="DIM-1", name="school-dim")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-DIM")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="grade", external_id="GRADE-DIM",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-DIM")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="turma", external_id="TURMA-DIM",
        )
        session.add(klass)
        student = Student(id=uuid.uuid4(), school_id=school.id, person_id=uuid.uuid4(), student_code="ST-DIM")
        session.add(student)
        await session.flush()
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
            mode="PHOTO", anchor_mode="IMAGE_REGION", status="PENDING_TRANSCRIPTION",
        )
        session.add(submission)
        await session.commit()
        return submission

    async def test_upload_page_records_real_pixel_dimensions(self):
        async with self.session_factory() as session:
            submission = await self._pending_submission(session)
            source = self.tmp_dir / "source" / "page1.png"
            _make_test_png(source, 800, 600)

            service = EssaySubmissionService(session, storage=self.storage)
            page = await service.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=source,
            )

            self.assertEqual(page.width, 800.0)
            self.assertEqual(page.height, 600.0)

    async def test_re_upload_overwrites_previous_dimensions(self):
        async with self.session_factory() as session:
            submission = await self._pending_submission(session)
            first = self.tmp_dir / "source" / "first.png"
            second = self.tmp_dir / "source" / "second.png"
            _make_test_png(first, 800, 600)
            _make_test_png(second, 400, 300)

            service = EssaySubmissionService(session, storage=self.storage)
            await service.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=first,
            )
            page = await service.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=second,
            )

            self.assertEqual(page.width, 400.0)
            self.assertEqual(page.height, 300.0)


if __name__ == "__main__":
    unittest.main()
