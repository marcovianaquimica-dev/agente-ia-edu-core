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

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
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

    async def test_a_global_coordinator_still_sees_every_classroom(self):
        """is_global must keep reaching the TeachingLesson query and its
        TURMA_3A/3B fallback - that half of the function is correct and stays."""
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

            portal = CoordinationPortalService(session, None, None, None, None)
            classrooms = await portal._resolve_scope_classrooms(
                "coord-global-2", school.id
            )
        self.assertEqual(classrooms, ["TURMA_3A", "TURMA_3B"])


if __name__ == "__main__":
    unittest.main()
