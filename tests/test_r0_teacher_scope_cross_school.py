# tests/test_r0_teacher_scope_cross_school.py
"""verify_teacher_classroom_scope never checked school_id for DIRECTOR/COORDINATOR
links, so a director of one school passed for a classroom of any other. The
sibling function two methods below, verify_coordinator_scope, already gets this
right - this fix makes verify_teacher_classroom_scope match it.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.teaching_context import ScopeAuthorizationError, TeachingContextService


class TeacherScopeCrossSchoolTests(unittest.IsolatedAsyncioTestCase):
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

    async def _two_schools(self, session):
        school_a = School(id=uuid.uuid4(), code="SCH-A", name="school-a")
        school_b = School(id=uuid.uuid4(), code="SCH-B", name="school-b")
        session.add_all([school_a, school_b])
        await session.commit()
        return school_a, school_b

    async def test_a_director_of_one_school_is_denied_another_schools_classroom(self):
        """The reported shape exactly, run against the real service."""
        async with self.session_factory() as session:
            school_a, school_b = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="director-a",
                role=AdminRole.DIRECTOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )

            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_teacher_classroom_scope(
                    teacher_id="director-a",
                    school_id=school_b.id,
                    classroom_id="QUALQUER-TURMA-DE-B",
                )

    async def test_a_coordinator_of_one_school_is_denied_another_schools_classroom(self):
        """Same bug, same fix, the other of the two affected roles."""
        async with self.session_factory() as session:
            school_a, school_b = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-a",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )

            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_teacher_classroom_scope(
                    teacher_id="coord-a",
                    school_id=school_b.id,
                    classroom_id="QUALQUER-TURMA-DE-B",
                )

    async def test_a_director_still_accesses_their_own_school(self):
        """The fix must not deny what was always legitimate - only what was
        never declared."""
        async with self.session_factory() as session:
            school_a, _ = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="director-own",
                role=AdminRole.DIRECTOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )

            svc = TeachingContextService(session)
            allowed = await svc.verify_teacher_classroom_scope(
                teacher_id="director-own",
                school_id=school_a.id,
                classroom_id="QUALQUER-TURMA-DA-PROPRIA-ESCOLA",
            )
        self.assertTrue(allowed)

    async def test_a_platform_admin_is_unaffected(self):
        """PLATFORM_ADMIN is the one role that genuinely has no school - the
        only role link_user_to_school lets through without one."""
        async with self.session_factory() as session:
            _, school_b = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="platform-admin-1",
                role=AdminRole.PLATFORM_ADMIN,
                scope_type=AdminScopeType.PLATFORM,
            )

            svc = TeachingContextService(session)
            allowed = await svc.verify_teacher_classroom_scope(
                teacher_id="platform-admin-1",
                school_id=school_b.id,
                classroom_id="QUALQUER-TURMA",
            )
        self.assertTrue(allowed)

    async def test_a_teacher_in_their_own_classroom_is_unaffected(self):
        """The TEACHER branch below this fix already checked school_id and
        must keep behaving exactly as it did."""
        async with self.session_factory() as session:
            school_a, _ = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="teacher-a",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school_a.id,
                scope_external_id="TURMA-A1",
            )

            svc = TeachingContextService(session)
            allowed = await svc.verify_teacher_classroom_scope(
                teacher_id="teacher-a", school_id=school_a.id, classroom_id="TURMA-A1"
            )
        self.assertTrue(allowed)

        async with self.session_factory() as session:
            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_teacher_classroom_scope(
                    teacher_id="teacher-not-linked-at-all",
                    school_id=uuid.uuid4(),
                    classroom_id="QUALQUER",
                )


if __name__ == "__main__":
    unittest.main()
