"""verify_teacher_classroom_scope and verify_coordinator_scope both grant
access to any identity shaped like a dev/test subject (teacher_id starting
with "teacher:", or exactly "prof_mendes"; coordinator_id starting with
"coordinator:", or exactly "coord_1") even when that identity has ZERO real
links - no school_id or classroom_id check at all. Confirmed live: an
identity with no links read another school's data through both functions.

verify_coordinator_scope also let a DIRECTOR/COORDINATOR link registered at
one school pass for ANY OTHER school, as long as its scope_type was
PLATFORM. link_user_to_school never validates scope_type against role, so
such a link is creatable through the ordinary API. This is the exact clause
onda 2 already removed from verify_teacher_classroom_scope's sibling check -
verify_coordinator_scope never got the same fix.

All three removals leave no new logic behind: the existing "no links" raise,
and the existing per-link school_id comparison, already do the right thing
once the shortcut is gone.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.teaching_context import ScopeAuthorizationError, TeachingContextService


class DevFallbackRemovalTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_teacher_classroom_scope_denies_unlinked_dev_shaped_identity(self):
        """teacher_id="teacher:ghost" (matches the removed prefix) has ZERO
        links - it must be denied like any other unlinked identity, not
        silently let through."""
        async with self.session_factory() as session:
            school = await self._school(session, "T")
            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_teacher_classroom_scope(
                    teacher_id="teacher:ghost", school_id=school.id, classroom_id="QUALQUER"
                )

    async def test_coordinator_scope_denies_unlinked_dev_shaped_identity(self):
        """coordinator_id="coord_1" (the removed exact match) has ZERO
        links - same denial as any other unlinked identity."""
        async with self.session_factory() as session:
            school = await self._school(session, "C")
            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_coordinator_scope(coordinator_id="coord_1", school_id=school.id)

    async def test_coordinator_scope_denies_platform_scoped_link_at_the_wrong_school(self):
        """A COORDINATOR link registered at school A, with scope_type
        PLATFORM, must not authorize school B - only a real school_id match,
        or PLATFORM_ADMIN, does."""
        async with self.session_factory() as session:
            school_a = await self._school(session, "A")
            school_b = await self._school(session, "B")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-of-school-a",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.PLATFORM,
                school_id=school_a.id,
            )

            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_coordinator_scope(
                    coordinator_id="coord-of-school-a", school_id=school_b.id
                )


if __name__ == "__main__":
    unittest.main()
