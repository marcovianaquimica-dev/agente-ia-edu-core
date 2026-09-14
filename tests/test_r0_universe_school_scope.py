# tests/test_r0_universe_school_scope.py
"""Covers the two public helpers the discipline gate is built on.

They live on the universe service because knowing what a school's universe is,
and how to walk the catalog tree, is that module's knowledge - not the gate's.
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
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService

SCHOOL_A = str(uuid.uuid4())
SCHOOL_B = str(uuid.uuid4())


class UniverseSchoolScopeTests(unittest.IsolatedAsyncioTestCase):
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

    async def _node(self, session, code, parent_id=None):
        node = CatalogNode(
            id=uuid.uuid4(), code=code, name=code, node_type="CONTENT", parent_id=parent_id
        )
        session.add(node)
        await session.flush()
        return node

    async def _universe(self, session, *, slug, owner_type, owner_external_id, status):
        universe = PedagogicalUniverse(
            id=uuid.uuid4(),
            external_id=slug,
            slug=slug,
            name=slug,
            owner_type=owner_type,
            owner_external_id=owner_external_id,
            status=status,
        )
        session.add(universe)
        await session.flush()
        return universe

    async def test_active_school_universe_ids_only_returns_this_schools_active_ones(self):
        async with self.session_factory() as session:
            mine = await self._universe(
                session, slug="mine", owner_type="SCHOOL",
                owner_external_id=SCHOOL_A, status="ACTIVE",
            )
            await self._universe(
                session, slug="draft", owner_type="SCHOOL",
                owner_external_id=SCHOOL_A, status="DRAFT",
            )
            await self._universe(
                session, slug="other-school", owner_type="SCHOOL",
                owner_external_id=SCHOOL_B, status="ACTIVE",
            )
            await self._universe(
                session, slug="platform", owner_type="PLATFORM",
                owner_external_id=None, status="ACTIVE",
            )
            await session.commit()

            service = PedagogicalUniverseService(session)
            found = await service.active_school_universe_ids(SCHOOL_A)

        self.assertEqual(found, [mine.id])

    async def test_expand_catalog_scope_nodes_includes_descendants_when_asked(self):
        async with self.session_factory() as session:
            root = await self._node(session, "DISC")
            child = await self._node(session, "DISC-AREA", parent_id=root.id)
            grandchild = await self._node(session, "DISC-AREA-CONTENT", parent_id=child.id)
            unrelated = await self._node(session, "OTHER")

            universe = await self._universe(
                session, slug="u", owner_type="SCHOOL",
                owner_external_id=SCHOOL_A, status="ACTIVE",
            )
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=root.id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                )
            )
            await session.commit()

            service = PedagogicalUniverseService(session)
            nodes = await service.expand_catalog_scope_nodes([universe.id])

        self.assertEqual(nodes, frozenset({root.id, child.id, grandchild.id}))
        self.assertNotIn(unrelated.id, nodes)

    async def test_expand_catalog_scope_nodes_stops_at_the_node_when_not_asked(self):
        async with self.session_factory() as session:
            root = await self._node(session, "DISC")
            child = await self._node(session, "DISC-AREA", parent_id=root.id)

            universe = await self._universe(
                session, slug="u", owner_type="SCHOOL",
                owner_external_id=SCHOOL_A, status="ACTIVE",
            )
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=root.id, scope_kind="DISCIPLINE",
                    include_descendants=False,
                )
            )
            await session.commit()

            service = PedagogicalUniverseService(session)
            nodes = await service.expand_catalog_scope_nodes([universe.id])

        self.assertEqual(nodes, frozenset({root.id}))
        self.assertNotIn(child.id, nodes)

    async def test_expand_catalog_scope_nodes_of_nothing_is_empty(self):
        async with self.session_factory() as session:
            service = PedagogicalUniverseService(session)
            self.assertEqual(await service.expand_catalog_scope_nodes([]), frozenset())

    async def test_catalog_codes_for_translates_ids_to_codes(self):
        """The question bank stores the content CODE, not a foreign key, so the
        gate has to speak both."""
        async with self.session_factory() as session:
            root = await self._node(session, "CHEMISTRY")
            child = await self._node(session, "CHEMISTRY-KINETICS", parent_id=root.id)
            await session.commit()

            service = PedagogicalUniverseService(session)
            codes = await service.catalog_codes_for([root.id, child.id])

        self.assertEqual(codes, frozenset({"CHEMISTRY", "CHEMISTRY-KINETICS"}))

    async def test_catalog_codes_for_drops_nodes_without_a_usable_code(self):
        """``catalog_nodes.code`` is nullable, and the return type says
        ``frozenset[str]``. A ``None`` slipping through made the question
        bank's ``sorted(allowed_codes)`` raise ``TypeError`` - a 500 on a
        security path - so it is dropped here, at the one place that knows
        the column is nullable. A node with no code matches no classification
        anyway, so nothing a consumer could have used is lost."""
        async with self.session_factory() as session:
            root = await self._node(session, "CHEMISTRY")
            codeless = CatalogNode(
                id=uuid.uuid4(), code=None, name="sem codigo",
                node_type="CONTENT", parent_id=root.id,
            )
            blank = CatalogNode(
                id=uuid.uuid4(), code="   ", name="em branco",
                node_type="CONTENT", parent_id=root.id,
            )
            session.add_all([codeless, blank])
            await session.commit()

            service = PedagogicalUniverseService(session)
            codes = await service.catalog_codes_for([root.id, codeless.id, blank.id])

        self.assertEqual(codes, frozenset({"CHEMISTRY"}))
        self.assertNotIn(None, codes)

    async def test_catalog_codes_for_nothing_is_empty(self):
        async with self.session_factory() as session:
            service = PedagogicalUniverseService(session)
            self.assertEqual(await service.catalog_codes_for([]), frozenset())


if __name__ == "__main__":
    unittest.main()
