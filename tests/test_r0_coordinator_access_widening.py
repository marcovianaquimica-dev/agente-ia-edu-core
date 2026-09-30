# tests/test_r0_coordinator_access_widening.py
"""The exact widening bug the Fase 3B final review found and executed live.

A coordinator whose links never include a CLASSROOM-level scope never
populates allowed_classrooms; the set arrives empty at verify_coordinator_access
without ever passing through the resolver. Before this fix, the guard
`if classroom_id and scopes["allowed_classrooms"] and classroom_id not in ...`
treats that empty set the same as "nothing to check" and grants access to
anything. After it, an empty set of a coordinator who is not global correctly
denies everything specific.
"""

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School, TeachingLesson
from agente_ia_edu.db.models.academic import AcademicYear, Class, GradeLevel, Segment
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.coordination_portal import CoordinationPortalService
from agente_ia_edu.services.teaching_context import ScopeAuthorizationError


class CoordinatorAccessWideningTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_a_coordinator_with_only_a_segment_link_is_denied_any_classroom(self):
        """The reported shape exactly: no CLASSROOM-scoped link at all, so
        allowed_classrooms is empty from the start - not from filtering."""
        async with self.session_factory() as session:
            school = await self._school(session, "1")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-segment-only",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SEGMENT,
                school_id=school.id,
                scope_external_id="SEG-1",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            with self.assertRaises(ScopeAuthorizationError):
                await portal.verify_coordinator_access(
                    coordinator_id="coord-segment-only",
                    school_id=school.id,
                    classroom_id="QUALQUER-TURMA",
                )

    async def test_the_same_coordinator_is_denied_any_grade_and_any_unit_too(self):
        """The bug repeats identically for grade_level and unit_id - same guard
        shape, same fix, in the same function."""
        async with self.session_factory() as session:
            school = await self._school(session, "2")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-segment-only-2",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SEGMENT,
                school_id=school.id,
                scope_external_id="SEG-2",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            with self.assertRaises(ScopeAuthorizationError):
                await portal.verify_coordinator_access(
                    coordinator_id="coord-segment-only-2",
                    school_id=school.id,
                    grade_level="QUALQUER-SERIE",
                )
            with self.assertRaises(ScopeAuthorizationError):
                await portal.verify_coordinator_access(
                    coordinator_id="coord-segment-only-2",
                    school_id=school.id,
                    unit_id="QUALQUER-UNIDADE",
                )

    async def test_a_coordinator_with_a_real_classroom_scope_still_works(self):
        """The fix must not deny what the coordinator actually has - only what
        they never declared."""
        async with self.session_factory() as session:
            school = await self._school(session, "3")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-with-classroom",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-REAL",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            allowed = await portal.verify_coordinator_access(
                coordinator_id="coord-with-classroom",
                school_id=school.id,
                classroom_id="TURMA-REAL",
            )
        self.assertTrue(allowed)

    async def test_a_global_coordinator_is_unaffected(self):
        """is_global short-circuits before either guard runs - unchanged."""
        async with self.session_factory() as session:
            school = await self._school(session, "4")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-global",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school.id,
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            allowed = await portal.verify_coordinator_access(
                coordinator_id="coord-global",
                school_id=school.id,
                classroom_id="QUALQUER-TURMA",
            )
        self.assertTrue(allowed)

    async def test_a_coordinator_with_only_a_segment_link_sees_no_classrooms(self):
        """The read-side twin of Task 1's fix: instead of raising, this one
        returns a list - and an empty, specific scope must return an empty
        list, not every classroom in the school plus the TURMA_3A scaffold."""
        async with self.session_factory() as session:
            school = await self._school(session, "5")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-segment-only-3",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SEGMENT,
                school_id=school.id,
                scope_external_id="SEG-3",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            classrooms = await portal._resolve_scope_classrooms(
                "coord-segment-only-3", school.id
            )
        self.assertEqual(classrooms, [])

    async def test_a_grade_level_only_coordinator_sees_no_classrooms(self):
        """Same shape as the SEGMENT case above, for GRADE_LEVEL specifically -
        reachable in production today (a platform admin can link a COORDINATOR
        with scope_type=GRADE_LEVEL and no CLASSROOM link via the admin API),
        and _resolve_scope_classrooms's own docstring flags this exact
        combination as untested: deriving classroom membership from a grade
        level is deferred, new logic - the fail-closed [] here is correct and
        must stay [], not silently widen to every classroom in the school."""
        async with self.session_factory() as session:
            school = await self._school(session, "7")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-grade-only",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.GRADE_LEVEL,
                school_id=school.id,
                scope_external_id="GRADE-3",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            classrooms = await portal._resolve_scope_classrooms(
                "coord-grade-only", school.id
            )
        self.assertEqual(classrooms, [])

    async def test_a_unit_only_coordinator_sees_no_classrooms(self):
        """Same as above, for UNIT."""
        async with self.session_factory() as session:
            school = await self._school(session, "8")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-unit-only",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.UNIT,
                school_id=school.id,
                scope_external_id="UNIT-3",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            classrooms = await portal._resolve_scope_classrooms(
                "coord-unit-only", school.id
            )
        self.assertEqual(classrooms, [])

    async def test_a_global_coordinator_still_sees_every_classroom(self):
        """is_global must keep reaching the TeachingLesson query - that half
        of the function is correct and stays. It must return the classroom
        actually taught (from TeachingLesson), not an invented name."""
        async with self.session_factory() as session:
            school = await self._school(session, "6")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-global-2",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school.id,
            )
            session.add(TeachingLesson(
                id=uuid.uuid4(),
                school_id=school.id,
                classroom_id="TAUGHT-REAL",
                teacher_id="teacher-x",
                content_node_id=uuid.uuid4(),
                lesson_date=datetime.now(timezone.utc),
            ))
            await session.commit()

            portal = CoordinationPortalService(session, None, None, None, None)
            classrooms = await portal._resolve_scope_classrooms(
                "coord-global-2", school.id
            )
        self.assertEqual(classrooms, ["TAUGHT-REAL"])
        self.assertNotIn("TURMA_3A", classrooms)
        self.assertNotIn("TURMA_3B", classrooms)

    async def test_a_global_coordinator_sees_the_schools_real_class_rows(self):
        """The exact reported production bug: a coordinator with SCHOOL-wide
        access, no TeachingLesson history yet, and no CLASSROOM-scoped
        UserSchoolLink either - but the school DOES have real Class rows
        (the R0 academic hierarchy). The coordinator must see those real
        classrooms, never the ["TURMA_3A", "TURMA_3B"] demo placeholder."""
        async with self.session_factory() as session:
            school = await self._school(session, "9")
            segment = Segment(id=uuid.uuid4(), school_id=school.id, name="segment", external_id="SEG-9")
            session.add(segment)
            await session.flush()
            grade = GradeLevel(
                id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                name="grade", external_id="GRADE-9",
            )
            year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-9")
            session.add_all([grade, year])
            await session.flush()
            turma_a = Class(
                id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                grade_level_id=grade.id, name="Turma A", external_id="1EM_A",
            )
            turma_b = Class(
                id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                grade_level_id=grade.id, name="Turma B", external_id="1EM_B",
            )
            session.add_all([turma_a, turma_b])
            await session.commit()

            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-real-classes",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school.id,
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            classrooms = await portal._resolve_scope_classrooms(
                "coord-real-classes", school.id
            )
        self.assertEqual(set(classrooms), {"1EM_A", "1EM_B"})
        self.assertNotIn("TURMA_3A", classrooms)
        self.assertNotIn("TURMA_3B", classrooms)

    async def test_a_global_coordinator_in_a_genuinely_empty_school_sees_no_classrooms(self):
        """No TeachingLesson, no CLASSROOM-scoped link, no real Class row at
        all: the honest answer is an empty list, never a fabricated one."""
        async with self.session_factory() as session:
            school = await self._school(session, "10")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-empty-school",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school.id,
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            classrooms = await portal._resolve_scope_classrooms(
                "coord-empty-school", school.id
            )
        self.assertEqual(classrooms, [])


if __name__ == "__main__":
    unittest.main()
