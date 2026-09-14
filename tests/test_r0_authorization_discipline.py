# tests/test_r0_authorization_discipline.py
"""require_discipline, in the shape of the require_* methods already there."""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.catalog import CatalogNode
from agente_ia_edu.db.models.pedagogical_universe import (
    PedagogicalUniverse,
    PedagogicalUniverseCatalogScope,
)
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.authorization import AuthorizationService

SCHOOL = str(uuid.uuid4())


def _context(school_id):
    return AuthenticatedUserContext(
        user_id="u-1",
        external_identity_id="ext-1",
        role="TEACHER",
        school_id=school_id,
    )


class AuthorizationDisciplineTests(unittest.IsolatedAsyncioTestCase):
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

    async def _restrict_school_to(self, session, code):
        node = CatalogNode(
            id=uuid.uuid4(), code=code, name=code, node_type="DISCIPLINE", parent_id=None
        )
        session.add(node)
        universe = PedagogicalUniverse(
            id=uuid.uuid4(), external_id="u", slug="u", name="u",
            owner_type="SCHOOL", owner_external_id=SCHOOL, status="ACTIVE",
        )
        session.add(universe)
        await session.flush()
        session.add(
            PedagogicalUniverseCatalogScope(
                id=uuid.uuid4(), universe_id=universe.id,
                catalog_node_id=node.id, scope_kind="DISCIPLINE",
                include_descendants=True,
            )
        )
        await session.commit()
        return node

    async def test_school_without_universe_is_allowed(self):
        async with self.session_factory() as session:
            result = await AuthorizationService(session).require_discipline(
                _context(SCHOOL), uuid.uuid4()
            )
        self.assertTrue(result.allowed)
        self.assertIsNone(result.reason)

    async def test_independent_student_is_allowed(self):
        async with self.session_factory() as session:
            result = await AuthorizationService(session).require_discipline(
                _context(None), uuid.uuid4()
            )
        self.assertTrue(result.allowed)

    async def test_restricted_school_is_allowed_inside_its_scope(self):
        async with self.session_factory() as session:
            node = await self._restrict_school_to(session, "CHEMISTRY")
            result = await AuthorizationService(session).require_discipline(
                _context(SCHOOL), node.id
            )
        self.assertTrue(result.allowed)

    async def test_restricted_school_is_refused_outside_its_scope(self):
        async with self.session_factory() as session:
            await self._restrict_school_to(session, "CHEMISTRY")
            outsider = CatalogNode(
                id=uuid.uuid4(), code="HISTORY", name="HISTORY",
                node_type="DISCIPLINE", parent_id=None,
            )
            session.add(outsider)
            await session.commit()
            result = await AuthorizationService(session).require_discipline(
                _context(SCHOOL), outsider.id
            )
        self.assertFalse(result.allowed)
        self.assertIn("discipline", (result.reason or "").lower())


if __name__ == "__main__":
    unittest.main()
