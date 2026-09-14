# tests/test_r0_discipline_gate.py
"""The gate, and above all the three shapes of absence.

Absence of a universe means access to everything. Restriction exists only
where somebody declared it (spec section 6). Every school in production today
is in that state, so a gate that gets this wrong locks out the entire base on
deploy day. Three of these tests are that rule.
"""

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
from agente_ia_edu.services.discipline_gate import DisciplineGate, DisciplineScope

SCHOOL = str(uuid.uuid4())


class DisciplineGateTests(unittest.IsolatedAsyncioTestCase):
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

    async def _node(self, session, code):
        node = CatalogNode(
            id=uuid.uuid4(), code=code, name=code, node_type="DISCIPLINE", parent_id=None
        )
        session.add(node)
        await session.flush()
        return node

    async def _active_universe(self, session):
        universe = PedagogicalUniverse(
            id=uuid.uuid4(), external_id="u", slug="u", name="u",
            owner_type="SCHOOL", owner_external_id=SCHOOL, status="ACTIVE",
        )
        session.add(universe)
        await session.flush()
        return universe

    async def test_absence_1_no_school_means_unrestricted(self):
        async with self.session_factory() as session:
            scope = await DisciplineGate(session).scope_for_school(None)
        self.assertTrue(scope.unrestricted)
        self.assertTrue(scope.permits(uuid.uuid4()))

    async def test_absence_2_school_without_universe_means_unrestricted(self):
        async with self.session_factory() as session:
            scope = await DisciplineGate(session).scope_for_school(SCHOOL)
        self.assertTrue(scope.unrestricted)
        self.assertTrue(scope.permits(uuid.uuid4()))

    async def test_absence_3_universe_without_any_scope_means_unrestricted(self):
        async with self.session_factory() as session:
            await self._active_universe(session)
            await session.commit()
            scope = await DisciplineGate(session).scope_for_school(SCHOOL)
        self.assertTrue(
            scope.unrestricted,
            "a universe that declares no catalog scope restricts nothing - "
            "intersecting with an empty set would block everything",
        )
        self.assertTrue(scope.permits(uuid.uuid4()))

    async def test_a_declared_scope_restricts_to_it(self):
        async with self.session_factory() as session:
            allowed = await self._node(session, "CHEMISTRY")
            denied = await self._node(session, "HISTORY")
            universe = await self._active_universe(session)
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=allowed.id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                )
            )
            await session.commit()
            scope = await DisciplineGate(session).scope_for_school(SCHOOL)

        self.assertFalse(scope.unrestricted)
        self.assertTrue(scope.permits(allowed.id))
        self.assertFalse(scope.permits(denied.id))
        # The same rule by code, which is how the question bank sees it.
        self.assertTrue(scope.permits_code("CHEMISTRY"))
        self.assertFalse(scope.permits_code("HISTORY"))
        self.assertTrue(scope.permits_code(None), "unclassified stays visible")
        self.assertTrue(
            scope.permits_code("   "),
            "whitespace-only is absence too, same as None or empty string",
        )

    async def test_an_area_scope_restricts_too(self):
        """The gate deliberately honours AREA, DISCIPLINE and CONTENT alike:
        restricting the query to scope_kind == "DISCIPLINE" would leave a
        school scoped only to an AREA silently unrestricted. This must fail
        if that filter is ever added back."""
        async with self.session_factory() as session:
            allowed = await self._node(session, "EXACT_SCIENCES")
            denied = await self._node(session, "HUMANITIES")
            universe = await self._active_universe(session)
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=allowed.id, scope_kind="AREA",
                    include_descendants=True,
                )
            )
            await session.commit()
            scope = await DisciplineGate(session).scope_for_school(SCHOOL)

        self.assertFalse(scope.unrestricted)
        self.assertTrue(scope.permits(allowed.id))
        self.assertFalse(scope.permits(denied.id))

    async def test_a_draft_universe_does_not_restrict(self):
        async with self.session_factory() as session:
            allowed = await self._node(session, "CHEMISTRY")
            universe = PedagogicalUniverse(
                id=uuid.uuid4(), external_id="d", slug="d", name="d",
                owner_type="SCHOOL", owner_external_id=SCHOOL, status="DRAFT",
            )
            session.add(universe)
            await session.flush()
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=allowed.id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                )
            )
            await session.commit()
            scope = await DisciplineGate(session).scope_for_school(SCHOOL)

        self.assertTrue(scope.unrestricted)

    async def test_an_unclassified_item_is_permitted_under_restriction(self):
        """A question with no catalog node at all is not evidence of another
        discipline, and blocking it would hide unclassified content from the
        only people able to classify it."""
        async with self.session_factory() as session:
            allowed = await self._node(session, "CHEMISTRY")
            universe = await self._active_universe(session)
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=allowed.id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                )
            )
            await session.commit()
            scope = await DisciplineGate(session).scope_for_school(SCHOOL)

        self.assertFalse(scope.unrestricted)
        self.assertTrue(scope.permits(None))

    def test_unrestricted_scope_permits_anything(self):
        scope = DisciplineScope.unrestricted_scope()
        self.assertTrue(scope.permits(uuid.uuid4()))
        self.assertTrue(scope.permits(None))


if __name__ == "__main__":
    unittest.main()
