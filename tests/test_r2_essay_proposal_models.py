import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayPrompt,
    EssaySubmission,
    EssaySubmissionPage,
    GradeLevel,
    PromptAssignment,
    PromptMaterial,
    School,
    Segment,
    Student,
)


class EssayProposalModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _class_and_student(self, session, code):
        school = School(id=uuid.uuid4(), code=f"EP-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
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
        student = Student(id=uuid.uuid4(), school_id=school.id, person_id=uuid.uuid4(), student_code=f"ST-{code}")
        session.add(student)
        await session.commit()
        return school, klass, student

    async def test_full_chain_round_trips(self):
        async with self.session_factory() as session:
            school, klass, student = await self._class_and_student(session, "1")

            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema X",
                statement="Disserte sobre X.", year=2026,
                created_by_external_identity="teacher:prof1",
            )
            session.add(prompt)
            await session.flush()
            self.assertEqual(prompt.status, "DRAFT")

            material = PromptMaterial(
                id=uuid.uuid4(), essay_prompt_id=prompt.id, material_type="TEXT",
                content="Texto motivador.", position=0,
            )
            session.add(material)

            assignment = PromptAssignment(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id, assigned_by_external_identity="teacher:prof1",
            )
            session.add(assignment)
            await session.flush()
            self.assertEqual(assignment.status, "OPEN")
            self.assertTrue(assignment.validation_enabled)

            submission = EssaySubmission(
                id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                prompt_assignment_id=assignment.id, student_id=student.id,
                mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                canonical_text="Redacao completa.", normalized_text_hash="a" * 64,
                submitted_at=datetime.now(timezone.utc),
            )
            session.add(submission)
            await session.commit()

            fetched = await session.get(EssaySubmission, submission.id)
            self.assertEqual(fetched.mode, "TYPED")
            self.assertEqual(fetched.status, "SUBMITTED")

    async def test_essay_submission_page_requires_positive_page_number(self):
        async with self.session_factory() as session:
            school, klass, student = await self._class_and_student(session, "2")
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="T", statement="S", year=2026,
                created_by_external_identity="teacher:p",
            )
            session.add(prompt)
            await session.flush()
            assignment = PromptAssignment(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id, assigned_by_external_identity="teacher:p",
            )
            session.add(assignment)
            await session.flush()
            submission = EssaySubmission(
                id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                prompt_assignment_id=assignment.id, student_id=student.id,
                mode="PHOTO", anchor_mode="IMAGE_REGION", status="PENDING_TRANSCRIPTION",
            )
            session.add(submission)
            await session.flush()

            page = EssaySubmissionPage(
                id=uuid.uuid4(), essay_submission_id=submission.id,
                page_number=0, storage_uri="var/x.png",
            )
            session.add(page)
            with self.assertRaises(Exception):
                await session.flush()


if __name__ == "__main__":
    unittest.main()
