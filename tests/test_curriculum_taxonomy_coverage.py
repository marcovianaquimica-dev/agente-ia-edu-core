"""Service-layer coverage for agente_ia_edu.services.curriculum_taxonomy.

Targets branches left uncovered by test_curriculum_taxonomy.py's fixture-
and-happy-path style tests: create_node's three validation branches
(unsupported node type, missing parent, incompatible parent/child type
pairing), link_question's existence check, add_prerequisite's self-reference
and missing-node checks, the diamond-shaped revisit ("already visited, skip")
branch inside the private cycle-detection BFS (_depends_on), and
seed_reference_fixture's "a node with this code already exists but with an
incompatible hierarchy" guard.
"""

from __future__ import annotations

import unittest
from uuid import uuid4

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, CatalogNodePrerequisite
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


class CurriculumTaxonomyCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_create_node_rejects_unsupported_node_type(self):
        async with self.factory() as session:
            taxonomy = CurriculumTaxonomyService(session)
            with self.assertRaises(ValueError) as ctx:
                await taxonomy.create_node("Bogus", "TOPIC", "BOGUS-1")
            self.assertIn("Unsupported curriculum node type", str(ctx.exception))

    async def test_create_node_rejects_missing_parent(self):
        async with self.factory() as session:
            taxonomy = CurriculumTaxonomyService(session)
            with self.assertRaises(ValueError) as ctx:
                await taxonomy.create_node("Ghost Area", "AREA", "GHOST-AREA", parent_id=uuid4())
            self.assertIn("Curriculum parent does not exist", str(ctx.exception))

    async def test_create_node_rejects_incompatible_parent_type(self):
        async with self.factory() as session:
            taxonomy = CurriculumTaxonomyService(session)
            discipline = await taxonomy.create_node("Quimica", "DISCIPLINE", "COV-CHEM")
            # CONTENT must parent under AREA, not directly under DISCIPLINE.
            with self.assertRaises(ValueError) as ctx:
                await taxonomy.create_node("Solucoes", "CONTENT", "COV-CHEM-SOL", parent_id=discipline.id)
            self.assertIn("Curriculum parent type is incompatible", str(ctx.exception))

    async def test_link_question_rejects_nonexistent_question_or_node(self):
        async with self.factory() as session:
            taxonomy = CurriculumTaxonomyService(session)
            discipline = await taxonomy.create_node("Fisica", "DISCIPLINE", "COV-PHYS")
            with self.assertRaises(ValueError) as ctx:
                await taxonomy.link_question(uuid4(), discipline.id, primary=False)
            self.assertIn("does not exist", str(ctx.exception))

    async def test_add_prerequisite_rejects_self_reference(self):
        async with self.factory() as session:
            taxonomy = CurriculumTaxonomyService(session)
            node = await taxonomy.create_node("Biologia", "DISCIPLINE", "COV-BIO")
            with self.assertRaises(ValueError) as ctx:
                await taxonomy.add_prerequisite(node.id, node.id)
            self.assertIn("cannot require itself", str(ctx.exception))

    async def test_add_prerequisite_rejects_nonexistent_node(self):
        async with self.factory() as session:
            taxonomy = CurriculumTaxonomyService(session)
            node = await taxonomy.create_node("Matematica", "DISCIPLINE", "COV-MATH")
            with self.assertRaises(ValueError) as ctx:
                await taxonomy.add_prerequisite(node.id, uuid4())
            self.assertIn("Curriculum node does not exist", str(ctx.exception))

    async def test_diamond_shaped_prerequisites_revisit_a_node_without_false_cycle(self):
        """Builds a diamond A -> {B, C} -> D (A requires both B and C, and
        both B and C require D), then adds an unrelated E -> A edge. The
        cycle-detection BFS walking from A necessarily reaches D twice (once
        via B, once via C) - the second time must hit the
        `if current in visited: continue` guard (not re-raise, not
        infinite-loop) and correctly conclude no cycle exists, since D has
        no further prerequisites and E is never actually reachable from A.
        """
        async with self.factory() as session:
            taxonomy = CurriculumTaxonomyService(session)
            a = await taxonomy.create_node("A", "DISCIPLINE", "COV-DIAMOND-A")
            b = await taxonomy.create_node("B", "DISCIPLINE", "COV-DIAMOND-B")
            c = await taxonomy.create_node("C", "DISCIPLINE", "COV-DIAMOND-C")
            d = await taxonomy.create_node("D", "DISCIPLINE", "COV-DIAMOND-D")
            e = await taxonomy.create_node("E", "DISCIPLINE", "COV-DIAMOND-E")

            await taxonomy.add_prerequisite(a.id, b.id)
            await taxonomy.add_prerequisite(a.id, c.id)
            await taxonomy.add_prerequisite(b.id, d.id)
            await taxonomy.add_prerequisite(c.id, d.id)

            # No cycle: E requiring A is fine even though A's dependency
            # graph revisits D through two different branches.
            relation = await taxonomy.add_prerequisite(e.id, a.id)
            self.assertEqual(relation.content_node_id, e.id)
            self.assertEqual(relation.prerequisite_node_id, a.id)

            row = await session.scalar(
                select(CatalogNodePrerequisite).where(
                    CatalogNodePrerequisite.content_node_id == e.id,
                    CatalogNodePrerequisite.prerequisite_node_id == a.id,
                )
            )
            self.assertIsNotNone(row)

    async def test_seed_reference_fixture_rejects_preexisting_incompatible_code(self):
        async with self.factory() as session:
            taxonomy = CurriculumTaxonomyService(session)
            # A node with the fixture's "CHEMISTRY" code already exists, but
            # as an AREA (not the expected root DISCIPLINE) - re-seeding must
            # refuse rather than silently reusing/renaming it.
            conflicting = CatalogNode(
                name="Quimica Pre-existente", node_type="AREA", code="CHEMISTRY",
                parent_id=None, root_id=None, position=1, active=True,
            )
            session.add(conflicting)
            await session.flush()

            with self.assertRaises(ValueError) as ctx:
                await taxonomy.seed_reference_fixture()
            self.assertIn("incompatible hierarchy", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
