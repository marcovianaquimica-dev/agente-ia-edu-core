import unittest

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    GradeLevel,
    School,
    SchoolUnit,
    Segment,
)


class TestHierarchyModels(unittest.IsolatedAsyncioTestCase):
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

    async def _base(self, session):
        school = School(code="ESCOLA_R0", name="Escola R0")
        session.add(school)
        await session.flush()
        segment = Segment(school_id=school.id, name="Ensino Médio", ordinal=3)
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            school_id=school.id, segment_id=segment.id, name="1ª série", ordinal=1
        )
        session.add(grade)
        await session.flush()
        return school, segment, grade

    async def test_the_same_class_name_in_two_years_is_two_entities(self):
        """Spec §4.2, quoting REDAÇÃO §13: "1ª Série A - 2026" and
        "1ª Série A - 2027" are different entities. Without this, a student's
        history is a pile of rows with no context."""
        async with self.session_factory() as session:
            school, _, grade = await self._base(session)
            year_2026 = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
            year_2027 = AcademicYear(school_id=school.id, year=2027, status="PLANNED")
            session.add_all([year_2026, year_2027])
            await session.flush()

            first = Class(
                school_id=school.id,
                academic_year_id=year_2026.id,
                grade_level_id=grade.id,
                name="A",
            )
            second = Class(
                school_id=school.id,
                academic_year_id=year_2027.id,
                grade_level_id=grade.id,
                name="A",
            )
            session.add_all([first, second])
            await session.flush()

            self.assertNotEqual(first.id, second.id)

    async def test_a_class_name_repeats_within_one_year_is_rejected(self):
        async with self.session_factory() as session:
            school, _, grade = await self._base(session)
            year = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
            session.add(year)
            await session.flush()

            session.add(Class(
                school_id=school.id,
                academic_year_id=year.id,
                grade_level_id=grade.id,
                name="A",
            ))
            await session.flush()
            session.add(Class(
                school_id=school.id,
                academic_year_id=year.id,
                grade_level_id=grade.id,
                name="A",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_academic_year_is_unique_per_school(self):
        async with self.session_factory() as session:
            school, _, _ = await self._base(session)
            session.add(AcademicYear(school_id=school.id, year=2026, status="ACTIVE"))
            await session.flush()
            session.add(AcademicYear(school_id=school.id, year=2026, status="PLANNED"))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_academic_year_status_is_constrained(self):
        async with self.session_factory() as session:
            school, _, _ = await self._base(session)
            session.add(AcademicYear(school_id=school.id, year=2028, status="NAO_EXISTE"))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_a_class_may_name_a_school_unit(self):
        async with self.session_factory() as session:
            school, _, grade = await self._base(session)
            unit = SchoolUnit(school_id=school.id, name="Unidade Centro")
            year = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
            session.add_all([unit, year])
            await session.flush()

            klass = Class(
                school_id=school.id,
                academic_year_id=year.id,
                grade_level_id=grade.id,
                school_unit_id=unit.id,
                name="A",
            )
            session.add(klass)
            await session.flush()
            self.assertEqual(klass.school_unit_id, unit.id)

    async def test_grade_level_belongs_to_a_segment(self):
        async with self.session_factory() as session:
            _, segment, grade = await self._base(session)
            self.assertEqual(grade.segment_id, segment.id)
