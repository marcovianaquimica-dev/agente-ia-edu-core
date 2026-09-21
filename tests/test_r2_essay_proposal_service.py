import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AcademicYear, Class, GradeLevel, School, Segment
from agente_ia_edu.services.essay_proposal import EssayProposalService


class EssayProposalServiceTests(unittest.IsolatedAsyncioTestCase):
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

    async def _school_and_class(self, session, code):
        school = School(id=uuid.uuid4(), code=f"PR-{code}", name=f"school-{code}")
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
        await session.commit()
        return school, klass

    async def test_create_prompt_starts_as_draft(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "1")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p1",
            )
            self.assertEqual(prompt.status, "DRAFT")

    async def test_add_material_requires_draft(self):
        async with self.session_factory() as session:
            school, klass = await self._school_and_class(session, "2")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p2",
            )
            material = await svc.add_material(
                essay_prompt_id=prompt.id, material_type="TEXT", content="Apoio.", position=0,
            )
            self.assertEqual(material.material_type, "TEXT")

            await svc.create_assignment(
                school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                assigned_by_external_identity="teacher:p2",
            )
            with self.assertRaises(ValueError):
                await svc.add_material(
                    essay_prompt_id=prompt.id, material_type="TEXT", content="Tarde demais.", position=1,
                )

    async def test_add_material_rejects_type_content_mismatch(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "3")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p3",
            )
            with self.assertRaises(ValueError):
                await svc.add_material(
                    essay_prompt_id=prompt.id, material_type="TEXT", content=None, position=0,
                )
            with self.assertRaises(ValueError):
                await svc.add_material(
                    essay_prompt_id=prompt.id, material_type="IMAGE", storage_uri=None, position=0,
                )

    async def test_create_assignment_activates_prompt_and_rejects_foreign_class(self):
        async with self.session_factory() as session:
            school_a, class_a = await self._school_and_class(session, "4a")
            school_b, class_b = await self._school_and_class(session, "4b")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school_a.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p4",
            )

            with self.assertRaises(ValueError):
                await svc.create_assignment(
                    school_id=school_a.id, essay_prompt_id=prompt.id, class_id=class_b.id,
                    assigned_by_external_identity="teacher:p4",
                )

            assignment = await svc.create_assignment(
                school_id=school_a.id, essay_prompt_id=prompt.id, class_id=class_a.id,
                assigned_by_external_identity="teacher:p4",
            )
            self.assertEqual(assignment.status, "OPEN")

            refreshed_prompt = await session.get(type(prompt), prompt.id)
            self.assertEqual(refreshed_prompt.status, "ACTIVE")

            with self.assertRaises(ValueError):
                await svc.create_assignment(
                    school_id=school_a.id, essay_prompt_id=prompt.id, class_id=class_a.id,
                    assigned_by_external_identity="teacher:p4",
                )


if __name__ == "__main__":
    unittest.main()
