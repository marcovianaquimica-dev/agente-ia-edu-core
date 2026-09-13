import unittest

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    GradeLevel,
    Person,
    School,
    Segment,
    User,
    UserSchoolLink,
)


class TestUserSchoolLinkBridge(unittest.IsolatedAsyncioTestCase):
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

    async def test_an_existing_style_link_still_works_with_no_entity(self):
        """The bridge is additive: a link written the old way, with only a
        scope string, must keep working untouched. Every row in production
        today looks like this."""
        async with self.session_factory() as session:
            school = School(code="ESCOLA_R0", name="Escola R0")
            session.add(school)
            await session.flush()

            link = UserSchoolLink(
                external_user_id="prof_mendes",
                school_id=school.id,
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
            )
            session.add(link)
            await session.flush()

            self.assertIsNone(link.class_id)
            self.assertIsNone(link.user_id)
            self.assertEqual(link.scope_external_id, "TURMA_3A")

    async def test_a_link_may_point_at_a_real_class(self):
        async with self.session_factory() as session:
            school = School(code="ESCOLA_R0", name="Escola R0")
            session.add(school)
            await session.flush()
            segment = Segment(school_id=school.id, name="Ensino Médio", ordinal=3)
            year = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
            person = Person(school_id=school.id, full_name="Prof. Mendes")
            session.add_all([segment, year, person])
            await session.flush()
            grade = GradeLevel(segment_id=segment.id, name="3ª série", ordinal=3)
            user = User(
                person_id=person.id,
                external_identity_provider="host",
                external_user_id="prof_mendes",
            )
            session.add_all([grade, user])
            await session.flush()
            klass = Class(academic_year_id=year.id, grade_level_id=grade.id, name="A")
            session.add(klass)
            await session.flush()

            link = UserSchoolLink(
                external_user_id="prof_mendes",
                school_id=school.id,
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
                user_id=user.id,
                class_id=klass.id,
            )
            session.add(link)
            await session.flush()

            self.assertEqual(link.class_id, klass.id)
            self.assertEqual(link.scope_external_id, "TURMA_3A")
