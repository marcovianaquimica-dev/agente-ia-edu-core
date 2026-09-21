"""N+1 query regression tests for pedagogical_universe.py.

Three separate N+1s lived here, all confirmed with a real in-process query
counter (SQLAlchemy `before_cursor_execute`, the same technique used live
against Postgres) before being fixed:

1. `catalog.py`'s cascading `/nodes` listing (and `initial_diagnostic.py`'s
   analogous call) tested each candidate `CatalogNode` against
   `contains_catalog_node()` one at a time - re-fetching the SAME universe
   scopes every call (the universe never changes across the loop) and
   re-`session.get`-ing a node the caller already had in hand, on top of
   `_is_descendant` walking the ancestor chain one query per level. Fixed
   with a batched `contains_catalog_nodes()` that resolves the whole
   candidate list with two queries total.

2. `contains_question_version()` did the same thing internally: one
   `contains_catalog_node()` call per linked content node. Fixed by routing
   it through the same batched primitive.

3. `_collect_descendants()` (used by `expand_catalog_scope_nodes()`, which
   backs `DisciplineGate` on every discipline-gated request) walked a BFS
   queue one node at a time, issuing one query per descendant. Fixed to
   batch each tree LEVEL with a single `parent_id IN (...)` query, so the
   query count is bounded by the subtree's DEPTH, not its SIZE.

4. `contains_catalog_node()` (the SINGLE-node check used directly by
   teacher_materials.py / learning_path.py, not just looped over) scaled
   with the universe's SCOPE count on its own: it looped over every
   `PedagogicalUniverseCatalogScope` and, for each `include_descendants`
   scope, walked the node's ancestor chain one `session.get()` per level.
   Measured against a fresh session (no identity-map caching from setup):
   5 scopes -> 6 queries, 50 scopes -> 43 queries. Fixed by delegating to
   `contains_catalog_nodes([node])`, the same batched primitive as #1-#2.

The signature of a real N+1 fix is that the query count for the SAME
operation stays constant (or grows only with genuinely distinct lookups,
never with the size of the collection/subtree being processed) as that
collection grows. That is exactly what each test below asserts: build a
"small" scenario and a "large" one and require the query count NOT to grow
proportionally with size.
"""

import unittest
import uuid

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, ContentQuestionLink, Question, QuestionVersion
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService


class QueryCounter:
    """Counts SQL statements executed against `engine` for the duration of
    a `with` block, via SQLAlchemy's `before_cursor_execute` event - the
    same instrumentation technique used to measure query counts live
    against the real dev Postgres, reused here so the (fast, disposable)
    sqlite-backed unit tests can assert exact counts."""

    def __init__(self, engine):
        self.engine = engine
        self.count = 0
        self._listener = None

    def __enter__(self):
        def _count(conn, cursor, statement, parameters, context, executemany):
            self.count += 1

        self._listener = _count
        event.listen(self.engine.sync_engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine.sync_engine, "before_cursor_execute", self._listener)


class PedagogicalUniverseN1Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    # -- scenario builders --------------------------------------------

    async def _universe_scoped_to_area(self, session, num_contents: int):
        """AREA -> DISCIPLINE -> `num_contents` CONTENT children, with a
        universe whose only catalog scope is the AREA (include_descendants).
        Returns (universe, content_nodes)."""
        area = CatalogNode(node_type="AREA", name="Ciências da Natureza", active=True)
        session.add(area)
        await session.flush()
        area.root_id = area.id
        discipline = CatalogNode(parent_id=area.id, root_id=area.id, node_type="DISCIPLINE", name="Química", active=True)
        session.add(discipline)
        await session.flush()
        contents = [
            CatalogNode(parent_id=discipline.id, root_id=area.id, node_type="CONTENT", name=f"Conteúdo {i}", active=True)
            for i in range(num_contents)
        ]
        session.add_all(contents)
        await session.commit()

        service = PedagogicalUniverseService(session)
        universe = await service.create_universe(
            external_id=f"U_{uuid.uuid4().hex[:8]}", slug=f"u-{uuid.uuid4().hex[:8]}", name="Universo",
            owner_type="PLATFORM", owner_external_id=None, performed_by_external_id="admin", status="ACTIVE",
        )
        await service.add_catalog_scope(universe_id=universe.id, catalog_node_id=area.id, scope_kind="AREA")
        return universe, contents

    # -- 1) contains_catalog_node looped vs contains_catalog_nodes batched --

    async def test_looping_contains_catalog_node_grows_with_candidate_count(self):
        """RED-style proof: this is exactly what catalog.py's cascading
        listing did before the fix - one contains_catalog_node() call per
        candidate node. Query count must grow with the candidate count."""
        async with self.session_factory() as session:
            universe, small_contents = await self._universe_scoped_to_area(session, 3)
            service = PedagogicalUniverseService(session)
            with QueryCounter(self.engine) as counter:
                for node in small_contents:
                    self.assertTrue(await service.contains_catalog_node(universe.id, node.id))
            small_count = counter.count

        async with self.session_factory() as session:
            universe, large_contents = await self._universe_scoped_to_area(session, 30)
            service = PedagogicalUniverseService(session)
            with QueryCounter(self.engine) as counter:
                for node in large_contents:
                    self.assertTrue(await service.contains_catalog_node(universe.id, node.id))
            large_count = counter.count

        self.assertGreater(
            large_count, small_count * 5,
            "contains_catalog_node() called in a loop (the old catalog.py "
            f"pattern) should scale with candidate count: 3 nodes -> {small_count} "
            f"queries, 30 nodes -> {large_count} queries.",
        )

    async def test_contains_catalog_nodes_batched_does_not_grow_with_candidate_count(self):
        """GREEN: the batched replacement stays flat."""
        async with self.session_factory() as session:
            universe, small_contents = await self._universe_scoped_to_area(session, 3)
            service = PedagogicalUniverseService(session)
            with QueryCounter(self.engine) as counter:
                matched = await service.contains_catalog_nodes(universe.id, small_contents)
            self.assertEqual(matched, {node.id for node in small_contents})
            small_count = counter.count

        async with self.session_factory() as session:
            universe, large_contents = await self._universe_scoped_to_area(session, 30)
            service = PedagogicalUniverseService(session)
            with QueryCounter(self.engine) as counter:
                matched = await service.contains_catalog_nodes(universe.id, large_contents)
            self.assertEqual(matched, {node.id for node in large_contents})
            large_count = counter.count

        self.assertEqual(
            small_count, large_count,
            "contains_catalog_nodes() must issue the SAME number of queries "
            f"regardless of candidate count: 3 nodes -> {small_count} queries, "
            f"30 nodes -> {large_count} queries.",
        )
        self.assertLessEqual(large_count, 3, f"expected ~2 queries total, got {large_count}")

    async def test_contains_catalog_nodes_excludes_nodes_outside_scope(self):
        """Batched membership must match the single-node method exactly,
        including the negative case."""
        async with self.session_factory() as session:
            universe, contents = await self._universe_scoped_to_area(session, 2)
            outside = CatalogNode(node_type="DISCIPLINE", name="Matemática", active=True)
            session.add(outside)
            await session.commit()

            service = PedagogicalUniverseService(session)
            matched = await service.contains_catalog_nodes(universe.id, contents + [outside])
            self.assertEqual(matched, {node.id for node in contents})
            self.assertFalse(await service.contains_catalog_node(universe.id, outside.id))

    async def test_contains_catalog_nodes_empty_input_short_circuits(self):
        async with self.session_factory() as session:
            universe, _ = await self._universe_scoped_to_area(session, 1)
            service = PedagogicalUniverseService(session)
            with QueryCounter(self.engine) as counter:
                matched = await service.contains_catalog_nodes(universe.id, [])
            self.assertEqual(matched, set())
            self.assertEqual(counter.count, 0)

    # -- 2) contains_question_version routed through the batched primitive --

    async def _question_version_linked_to(self, session, content_nodes):
        question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
        session.add(question)
        await session.flush()
        version = QuestionVersion(
            question_id=question.id, version_kind="official_original", canonical_text="Q",
            content_hash=f"hash-{uuid.uuid4().hex}", recommended_difficulty="EASY",
        )
        session.add(version)
        await session.flush()
        session.add_all([
            ContentQuestionLink(content_node_id=node.id, question_version_id=version.id)
            for node in content_nodes
        ])
        await session.commit()
        return version

    async def _universe_and_out_of_scope_contents(self, session, num_contents: int):
        """A universe scoped to one AREA, plus `num_contents` CONTENT nodes
        that live entirely OUTSIDE that scope (a different, unscoped
        DISCIPLINE). Used to exercise contains_question_version's WORST
        case: every linked node is a miss, so a per-node loop can never
        short-circuit on an early True and must check every single one -
        unlike a scenario where every node matches and the very first
        check returns True, which hides the N+1 behind an early exit."""
        universe, _ = await self._universe_scoped_to_area(session, 1)
        unscoped_discipline = CatalogNode(node_type="DISCIPLINE", name="História", active=True)
        session.add(unscoped_discipline)
        await session.flush()
        unscoped_discipline.root_id = unscoped_discipline.id
        contents = [
            CatalogNode(parent_id=unscoped_discipline.id, root_id=unscoped_discipline.id, node_type="CONTENT", name=f"Fora {i}", active=True)
            for i in range(num_contents)
        ]
        session.add_all(contents)
        await session.commit()
        return universe, contents

    async def test_contains_question_version_does_not_grow_with_linked_node_count(self):
        """Worst case: none of the linked nodes are in scope, so a per-node
        loop (the old implementation) cannot short-circuit early and must
        check every single linked node - exactly where the old N+1 showed up."""
        async with self.session_factory() as session:
            universe, small_contents = await self._universe_and_out_of_scope_contents(session, 2)
            version = await self._question_version_linked_to(session, small_contents)
            service = PedagogicalUniverseService(session)
            with QueryCounter(self.engine) as counter:
                self.assertFalse(await service.contains_question_version(universe.id, version.id))
            small_count = counter.count

        async with self.session_factory() as session:
            universe, large_contents = await self._universe_and_out_of_scope_contents(session, 25)
            version = await self._question_version_linked_to(session, large_contents)
            service = PedagogicalUniverseService(session)
            with QueryCounter(self.engine) as counter:
                self.assertFalse(await service.contains_question_version(universe.id, version.id))
            large_count = counter.count

        self.assertEqual(
            small_count, large_count,
            "contains_question_version() must not re-run contains_catalog_node "
            f"per linked node: 2 links -> {small_count} queries, 25 links -> "
            f"{large_count} queries.",
        )

    # -- 3) _collect_descendants / expand_catalog_scope_nodes batched by level --

    async def _universe_with_wide_discipline(self, session, num_children: int):
        """DISCIPLINE scope with `num_children` direct CONTENT children (a
        single extra tree level) - old code queried once per child, new
        code queries once per LEVEL (so twice, regardless of num_children)."""
        discipline = CatalogNode(node_type="DISCIPLINE", name="Física", active=True)
        session.add(discipline)
        await session.flush()
        discipline.root_id = discipline.id
        children = [
            CatalogNode(parent_id=discipline.id, root_id=discipline.id, node_type="CONTENT", name=f"C{i}", active=True)
            for i in range(num_children)
        ]
        session.add_all(children)
        await session.commit()

        service = PedagogicalUniverseService(session)
        universe = await service.create_universe(
            external_id=f"W_{uuid.uuid4().hex[:8]}", slug=f"w-{uuid.uuid4().hex[:8]}", name="Universo Largo",
            owner_type="PLATFORM", owner_external_id=None, performed_by_external_id="admin", status="ACTIVE",
        )
        await service.add_catalog_scope(universe_id=universe.id, catalog_node_id=discipline.id, scope_kind="DISCIPLINE")
        return universe, discipline, children

    async def test_expand_catalog_scope_nodes_query_count_bounded_by_depth_not_size(self):
        async with self.session_factory() as session:
            universe, discipline, small_children = await self._universe_with_wide_discipline(session, 3)
            service = PedagogicalUniverseService(session)
            with QueryCounter(self.engine) as counter:
                reachable = await service.expand_catalog_scope_nodes([universe.id])
            self.assertEqual(reachable, {discipline.id} | {c.id for c in small_children})
            small_count = counter.count

        async with self.session_factory() as session:
            universe, discipline, large_children = await self._universe_with_wide_discipline(session, 60)
            service = PedagogicalUniverseService(session)
            with QueryCounter(self.engine) as counter:
                reachable = await service.expand_catalog_scope_nodes([universe.id])
            self.assertEqual(reachable, {discipline.id} | {c.id for c in large_children})
            large_count = counter.count

        self.assertEqual(
            small_count, large_count,
            "expand_catalog_scope_nodes() must not issue one query PER "
            f"descendant: 3 children -> {small_count} queries, 60 children -> "
            f"{large_count} queries.",
        )

    # -- 4) contains_catalog_node's own query count vs the universe's scope count --

    async def _universe_with_many_scopes(self, session, num_scopes: int):
        """`num_scopes` separately-scoped DISCIPLINE/CONTENT pairs
        (include_descendants=True). Target is the CONTENT child of the
        LAST scope added - the worst case for a per-scope loop, since every
        earlier scope must be checked (and fail) before reaching the one
        that matches."""
        service = PedagogicalUniverseService(session)
        universe = await service.create_universe(
            external_id=f"MS_{uuid.uuid4().hex[:8]}", slug=f"ms-{uuid.uuid4().hex[:8]}", name="Universo Multi-Escopo",
            owner_type="PLATFORM", owner_external_id=None, performed_by_external_id="admin", status="ACTIVE",
        )
        target_content_id = None
        for i in range(num_scopes):
            discipline = CatalogNode(node_type="DISCIPLINE", name=f"Disciplina {i}", active=True)
            session.add(discipline)
            await session.flush()
            discipline.root_id = discipline.id
            content = CatalogNode(parent_id=discipline.id, root_id=discipline.id, node_type="CONTENT", name=f"Conteúdo {i}", active=True)
            session.add(content)
            await session.flush()
            await session.commit()
            await service.add_catalog_scope(universe_id=universe.id, catalog_node_id=discipline.id, scope_kind="DISCIPLINE", include_descendants=True)
            if i == num_scopes - 1:
                target_content_id = content.id
        return universe.id, target_content_id

    async def test_contains_catalog_node_does_not_grow_with_universe_scope_count(self):
        """A FRESH session per measurement is essential here: within the
        SAME session used to build the scenario, every node is already in
        the SQLAlchemy identity map, so session.get() never touches the
        database and would hide this N+1 entirely."""
        async with self.session_factory() as session:
            universe_id, target_id = await self._universe_with_many_scopes(session, 5)
        async with self.session_factory() as session:  # fresh, empty identity map
            service = PedagogicalUniverseService(session)
            with QueryCounter(self.engine) as counter:
                self.assertTrue(await service.contains_catalog_node(universe_id, target_id))
            small_count = counter.count

        async with self.session_factory() as session:
            universe_id, target_id = await self._universe_with_many_scopes(session, 50)
        async with self.session_factory() as session:  # fresh, empty identity map
            service = PedagogicalUniverseService(session)
            with QueryCounter(self.engine) as counter:
                self.assertTrue(await service.contains_catalog_node(universe_id, target_id))
            large_count = counter.count

        self.assertEqual(
            small_count, large_count,
            "contains_catalog_node() must not issue one query PER universe "
            f"scope: 5 scopes -> {small_count} queries, 50 scopes -> "
            f"{large_count} queries.",
        )


if __name__ == "__main__":
    unittest.main()
