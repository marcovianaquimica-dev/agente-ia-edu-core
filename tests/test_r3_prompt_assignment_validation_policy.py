import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AcademicYear, Class, EssayPrompt, GradeLevel, School, Segment
from agente_ia_edu.services.essay_proposal import EssayProposalService
from agente_ia_edu.services.institution_settings import InstitutionSettingsService


class PromptAssignmentValidationPolicyTests(unittest.IsolatedAsyncioTestCase):
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

    async def _prompt_and_class(self, session, code):
        school = School(id=uuid.uuid4(), code=f"POL-{code}", name=f"school-{code}")
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
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, status="DRAFT", created_by_external_identity="teacher:t",
        )
        session.add(prompt)
        await session.commit()
        return school.id, prompt.id, klass.id

    async def test_disabling_validation_is_rejected_when_school_forbids_it(self):
        async with self.session_factory() as session:
            school_id, prompt_id, class_id = await self._prompt_and_class(session, "1")
            await InstitutionSettingsService(session).configure(
                school_id, performed_by_external_id="admin:a",
                correction_mode="AVALIATIVO", validation_teacher_can_disable=False,
            )
            service = EssayProposalService(session)
            with self.assertRaises(ValueError):
                await service.create_assignment(
                    school_id=school_id, essay_prompt_id=prompt_id, class_id=class_id,
                    assigned_by_external_identity="teacher:t", validation_enabled=False,
                )

    async def test_disabling_validation_is_allowed_when_school_permits_it(self):
        async with self.session_factory() as session:
            school_id, prompt_id, class_id = await self._prompt_and_class(session, "2")
            await InstitutionSettingsService(session).configure(
                school_id, performed_by_external_id="admin:a",
                correction_mode="AVALIATIVO", validation_teacher_can_disable=True,
            )
            service = EssayProposalService(session)
            assignment = await service.create_assignment(
                school_id=school_id, essay_prompt_id=prompt_id, class_id=class_id,
                assigned_by_external_identity="teacher:t", validation_enabled=False,
            )
            self.assertFalse(assignment.validation_enabled)

    async def test_leaving_validation_enabled_is_always_allowed(self):
        async with self.session_factory() as session:
            school_id, prompt_id, class_id = await self._prompt_and_class(session, "3")
            await InstitutionSettingsService(session).configure(
                school_id, performed_by_external_id="admin:a",
                correction_mode="AVALIATIVO", validation_teacher_can_disable=False,
            )
            service = EssayProposalService(session)
            assignment = await service.create_assignment(
                school_id=school_id, essay_prompt_id=prompt_id, class_id=class_id,
                assigned_by_external_identity="teacher:t", validation_enabled=True,
            )
            self.assertTrue(assignment.validation_enabled)


if __name__ == "__main__":
    unittest.main()
