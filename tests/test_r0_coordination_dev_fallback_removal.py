"""get_coordinator_authorized_scopes grants global access ("is_global": True,
with a fabricated classroom list) to any identity with ZERO real links, in
TWO independent places: an explicit dev/test shortcut at the top of the
function (coordinator_id shaped like "coordinator:*", "director:*", or one
of "coord_1"/"coord_a"/"admin:master"), and an unconditional `or not links`
a few lines below it that does not even check the id's shape - ANY unlinked
identity that reaches that point gets the same fabricated result.

In today's codebase, every caller of get_coordinator_authorized_scopes
reaches it only after verify_coordinator_access's own gate has already run -
so the unconditional branch is not independently reachable from outside this
file today. It is still fixed here: a function whose own docstring promises
"authorized scope filters" should not silently grant everything to a future
caller that skips the gate, and this is the same "empty set reads as
unrestricted" anti-pattern onda 1 of this phase already fixed for this
exact function's neighbours (see _resolved's docstring in this same file).

verify_coordinator_access has the matching dev/test shortcut in its own
"no links" branch - remove that too, and reaching
get_coordinator_authorized_scopes with an unlinked identity becomes
impossible again through the only path that exists today.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.services.coordination_portal import CoordinationPortalService
from agente_ia_edu.services.teaching_context import ScopeAuthorizationError


class CoordinationDevFallbackRemovalTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_authorized_scopes_denies_unlinked_dev_shaped_identity(self):
        """coordinator_id="coordinator:ghost" (matches the removed prefix)
        has ZERO links - must NOT get is_global=True with a fabricated
        classroom set."""
        async with self.session_factory() as session:
            school = await self._school(session, "1")
            portal = CoordinationPortalService(session, None, None, None, None)
            scopes = await portal.get_coordinator_authorized_scopes(
                "coordinator:ghost", school.id
            )

        self.assertFalse(scopes["is_global"])
        self.assertEqual(scopes["allowed_classrooms"], set())

    async def test_authorized_scopes_denies_any_unlinked_identity_regardless_of_shape(self):
        """The unconditional `or not links` clause: an identity whose name
        does NOT match any dev/test shape must ALSO get is_global=False, not
        just the dev-shaped ones - confirms the second, independent branch
        was fixed too, not only the top shortcut."""
        async with self.session_factory() as session:
            school = await self._school(session, "2")
            portal = CoordinationPortalService(session, None, None, None, None)
            scopes = await portal.get_coordinator_authorized_scopes(
                "nobody-at-all", school.id
            )

        self.assertFalse(scopes["is_global"])
        self.assertEqual(scopes["allowed_classrooms"], set())

    async def test_verify_coordinator_access_denies_unlinked_dev_shaped_identity(self):
        """coordinator_id="director:ghost" (matches the removed prefix) has
        ZERO links - verify_coordinator_access must raise, not pass
        through to get_coordinator_authorized_scopes at all."""
        async with self.session_factory() as session:
            school = await self._school(session, "3")
            portal = CoordinationPortalService(session, None, None, None, None)
            with self.assertRaises(ScopeAuthorizationError):
                await portal.verify_coordinator_access(
                    coordinator_id="director:ghost", school_id=school.id
                )


if __name__ == "__main__":
    unittest.main()
