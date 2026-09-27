import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayPrompt,
    GradeLevel,
    PromptAssignment,
    School,
    Segment,
)
from agente_ia_edu.services.essay_submission import EssaySubmissionService


class FreeThemeSubmissionTests(unittest.IsolatedAsyncioTestCase):
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

    async def _assignment(self, session, code, *, is_free_theme):
        school = School(id=uuid.uuid4(), code=f"FT-{code}", name=f"school-{code}")
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
        await session.flush()
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="teacher:p", is_free_theme=is_free_theme,
        )
        session.add(prompt)
        await session.flush()
        assignment = PromptAssignment(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, assigned_by_external_identity="teacher:p",
        )
        session.add(assignment)
        await session.commit()
        return school, assignment

    async def test_typed_submission_requires_declared_theme_when_free_theme(self):
        async with self.session_factory() as session:
            school, assignment = await self._assignment(session, "1", is_free_theme=True)
            svc = EssaySubmissionService(session)

            with self.assertRaises(ValueError):
                await svc.start_typed_submission(
                    school_id=school.id, prompt_assignment_id=assignment.id,
                    student_id=uuid.uuid4(), text="Minha redacao.",
                )

    async def test_typed_submission_stores_declared_theme_when_free_theme(self):
        async with self.session_factory() as session:
            school, assignment = await self._assignment(session, "2", is_free_theme=True)
            svc = EssaySubmissionService(session)

            submission = await svc.start_typed_submission(
                school_id=school.id, prompt_assignment_id=assignment.id,
                student_id=uuid.uuid4(), text="Minha redacao.",
                student_declared_theme="  O impacto da tecnologia na educacao  ",
            )

            self.assertEqual(submission.student_declared_theme, "O impacto da tecnologia na educacao")

    async def test_typed_submission_ignores_declared_theme_when_not_free_theme(self):
        async with self.session_factory() as session:
            school, assignment = await self._assignment(session, "3", is_free_theme=False)
            svc = EssaySubmissionService(session)

            submission = await svc.start_typed_submission(
                school_id=school.id, prompt_assignment_id=assignment.id,
                student_id=uuid.uuid4(), text="Minha redacao.",
                student_declared_theme="Um tema que eu inventei",
            )

            self.assertIsNone(submission.student_declared_theme)

    async def test_photo_submission_requires_declared_theme_when_free_theme(self):
        async with self.session_factory() as session:
            school, assignment = await self._assignment(session, "4", is_free_theme=True)
            svc = EssaySubmissionService(session)

            with self.assertRaises(ValueError):
                await svc.start_photo_submission(
                    school_id=school.id, prompt_assignment_id=assignment.id,
                    student_id=uuid.uuid4(), mode="PHOTO", transcription_enabled=True,
                )

    async def test_photo_submission_stores_declared_theme_when_free_theme(self):
        async with self.session_factory() as session:
            school, assignment = await self._assignment(session, "5", is_free_theme=True)
            svc = EssaySubmissionService(session)

            submission = await svc.start_photo_submission(
                school_id=school.id, prompt_assignment_id=assignment.id,
                student_id=uuid.uuid4(), mode="PHOTO", transcription_enabled=True,
                student_declared_theme="Envelhecimento populacional",
            )

            self.assertEqual(submission.student_declared_theme, "Envelhecimento populacional")

    async def test_resolve_missing_assignment_is_treated_as_not_free_theme(self):
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session)
            submission = await svc.start_typed_submission(
                school_id=uuid.uuid4(), prompt_assignment_id=uuid.uuid4(),
                student_id=uuid.uuid4(), text="Minha redacao.",
            )
            self.assertIsNone(submission.student_declared_theme)


if __name__ == "__main__":
    unittest.main()
