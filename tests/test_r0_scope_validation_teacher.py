# tests/test_r0_scope_validation_teacher.py
"""The second call site resolve_many's docstring names: a teacher's own
classroom list, currently built with no check that the code is real - and, as
with coordination_portal, no check that the school's hierarchy exists at all.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.db.models.academic import Class, GradeLevel, Segment, AcademicYear
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.teacher_portal import TeacherPortalService


class TeacherScopeValidationTests(unittest.IsolatedAsyncioTestCase):
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

    async def _school(self, session, code):
        school = School(id=uuid.uuid4(), code=f"SCH-{code}", name=f"school-{code}")
        session.add(school)
        await session.commit()
        return school

    async def _seed_hierarchy(self, session, school, code):
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="segment", external_id=f"SEG-{code}")
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
            grade_level_id=grade.id, name="class", external_id=f"TURMA-{code}",
        )
        session.add(klass)
        await session.commit()

    async def test_a_stale_classroom_code_is_dropped_once_hierarchy_exists(self):
        async with self.session_factory() as session:
            school = await self._school(session, "1")
            await self._seed_hierarchy(session, school, "1")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="teacher-real",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-1",
            )
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="teacher-real",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-FANTASMA",
            )

            portal = TeacherPortalService(session, None, None, None)
            classrooms = await portal.get_teacher_authorized_classrooms("teacher-real", school.id)

        self.assertIn("TURMA-1", classrooms)
        self.assertNotIn("TURMA-FANTASMA", classrooms)

    async def test_an_all_invalid_list_keeps_its_unfiltered_codes(self):
        """Dropping every code would return [], and [] does not mean "no
        classrooms" to every caller: coordination_portal reaches this function
        and _fetch_students_in_classrooms falls back to an unfiltered student
        query on an empty list. Narrowing is allowed; emptying is not. With no
        valid code left, the original list is kept - today's behaviour, which
        downstream already denies.
        """
        async with self.session_factory() as session:
            school = await self._school(session, "2")
            await self._seed_hierarchy(session, school, "2")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="teacher-stale-only",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-FANTASMA",
            )

            portal = TeacherPortalService(session, None, None, None)
            classrooms = await portal.get_teacher_authorized_classrooms(
                "teacher-stale-only", school.id
            )

        self.assertEqual(classrooms, ["TURMA-FANTASMA"])

    async def test_every_code_survives_when_the_school_has_no_hierarchy_yet(self):
        """This is exactly tests/test_teacher_portal.py's own prof_mendes
        fixture: a real link to scope_external_id="TURMA_3A", no Class row
        behind it. It must keep working."""
        async with self.session_factory() as session:
            school = await self._school(session, "unmigrated")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="teacher-legacy",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_3A",
            )

            portal = TeacherPortalService(session, None, None, None)
            classrooms = await portal.get_teacher_authorized_classrooms("teacher-legacy", school.id)

        self.assertIn("TURMA_3A", classrooms)

    async def test_an_unlinked_dev_shaped_identity_gets_no_classrooms(self):
        """teacher_id="teacher:ghost" (matches the removed dev/test prefix)
        has ZERO links to this school - it must get an empty list like any
        other unlinked identity, not every classroom in the school."""
        async with self.session_factory() as session:
            school = await self._school(session, "dev-fallback")
            portal = TeacherPortalService(session, None, None, None)
            classrooms = await portal.get_teacher_authorized_classrooms("teacher:ghost", school.id)

        self.assertEqual(classrooms, [])


if __name__ == "__main__":
    unittest.main()
