# tests/test_r0_scope_validation_coordination.py
"""A stale or cross-school scope_external_id must stop granting access - but
only once the school's hierarchy has been populated at that level.

get_coordinator_authorized_scopes builds its allow-sets directly from
UserSchoolLink.scope_external_id, a free-text field nothing validates at
write time. This is the first of the two call sites resolve_many's own
docstring names as the reason it exists (external_id_resolution.py:180-183).

The naive version of this file - validate everything, unconditionally - broke
tests/test_teacher_portal.py::test_16_list_teacher_classrooms and two others,
because their fixtures link a real user to a real scope_external_id with no
matching Class/GradeLevel/... row, exactly the state of every school today
before its hierarchy is backfilled. The tests below cover both states on
purpose: populated (validate) and unpopulated (pass through unchanged).
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.db.models.academic import Class, GradeLevel, Segment, SchoolUnit, AcademicYear
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.coordination_portal import CoordinationPortalService


class CoordinatorScopeValidationTests(unittest.IsolatedAsyncioTestCase):
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
        unit = SchoolUnit(id=uuid.uuid4(), school_id=school.id, name="unit", external_id=f"UNIT-{code}")
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="segment", external_id=f"SEG-{code}")
        session.add_all([unit, segment])
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
                external_user_id="coord-real",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-1",
            )
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-real",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-QUE-NAO-EXISTE-MAIS",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            scopes = await portal.get_coordinator_authorized_scopes("coord-real", school.id)

        self.assertIn("TURMA-1", scopes["allowed_classrooms"])
        self.assertNotIn("TURMA-QUE-NAO-EXISTE-MAIS", scopes["allowed_classrooms"])

    async def test_every_code_survives_when_the_school_has_no_hierarchy_yet(self):
        """The exact scenario that broke the naive design: a real link, a
        school whose hierarchy tables are still empty. Nothing is dropped."""
        async with self.session_factory() as session:
            school = await self._school(session, "unmigrated")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-legacy",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_3A",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            scopes = await portal.get_coordinator_authorized_scopes("coord-legacy", school.id)

        self.assertIn("TURMA_3A", scopes["allowed_classrooms"])

    async def test_an_all_invalid_level_keeps_its_unfiltered_set(self):
        """Filtering may narrow an allow-set; it may never empty one.

        One call up, an empty set does not mean "restricted to nothing" - it
        means "not restricted": before Fase 3C onda 1, verify_coordinator_access
        skipped its check on a falsy set, and _resolve_scope_classrooms fell
        through to every classroom in the school. So a coordinator whose only
        code is stale would go from denied-everywhere to allowed-everywhere.
        When every code at a level is invalid, the unfiltered set is kept
        instead: exactly today's behaviour, which never grants more than this
        phase found on entry. Those two callers are authorization logic, and
        Fase 3C onda 1 has since fixed both of them (commits c06b198 and
        b6d6a27) to key off is_global instead of allow-set truthiness.
        """
        async with self.session_factory() as session:
            school = await self._school(session, "3")
            await self._seed_hierarchy(session, school, "3")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-stale-only",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-VELHA-DE-2025",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            scopes = await portal.get_coordinator_authorized_scopes(
                "coord-stale-only", school.id
            )

        self.assertEqual(scopes["allowed_classrooms"], {"TURMA-VELHA-DE-2025"})

    async def test_a_valid_code_of_every_level_survives(self):
        """The four levels - unit, segment, grade, classroom - each go through
        their own has_any_entities + resolve_many call. One test per level
        would be four files of the same shape; this proves all four at once,
        in a school whose hierarchy is fully populated."""
        async with self.session_factory() as session:
            school = await self._school(session, "2")
            await self._seed_hierarchy(session, school, "2")
            admin = PlatformAdminService(session)
            for scope_type, code in (
                (AdminScopeType.UNIT, "UNIT-2"),
                (AdminScopeType.SEGMENT, "SEG-2"),
                (AdminScopeType.GRADE_LEVEL, "GRADE-2"),
                (AdminScopeType.CLASSROOM, "TURMA-2"),
            ):
                await admin.link_user_to_school(
                    performed_by_external_id="setup",
                    external_user_id="coord-multi",
                    role=AdminRole.COORDINATOR,
                    scope_type=scope_type,
                    school_id=school.id,
                    scope_external_id=code,
                )

            portal = CoordinationPortalService(session, None, None, None, None)
            scopes = await portal.get_coordinator_authorized_scopes("coord-multi", school.id)

        self.assertEqual(scopes["allowed_units"], {"UNIT-2"})
        self.assertEqual(scopes["allowed_segments"], {"SEG-2"})
        self.assertEqual(scopes["allowed_grades"], {"GRADE-2"})
        self.assertEqual(scopes["allowed_classrooms"], {"TURMA-2"})


if __name__ == "__main__":
    unittest.main()
