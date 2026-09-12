import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, CatalogNodePrerequisite, ContentQuestionLink, Question, QuestionVersion
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService


class CurriculumTaxonomyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_controlled_fixture_has_stable_tree_and_complementary_question_links(self):
        async with self.factory() as session:
            taxonomy = CurriculumTaxonomyService(session)
            nodes = await taxonomy.seed_reference_fixture()
            self.assertEqual(taxonomy.created_nodes, 17)
            again = await taxonomy.seed_reference_fixture()
            self.assertEqual(taxonomy.created_nodes, 0)
            self.assertEqual(nodes["chemistry"].id, again["chemistry"].id)
            dilution = nodes["chemistry_dilution"]
            path = await taxonomy.ancestors(dilution.id)
            self.assertEqual([node.code for node in path], ["CHEMISTRY", "CHEMISTRY-PHYSICAL", "CHEMISTRY-SOLUTIONS", "CHEMISTRY-SOLUTIONS-DILUTION"])
            children = await taxonomy.children(nodes["chemistry_solutions"].id)
            self.assertEqual([node.code for node in children], ["CHEMISTRY-SOLUTIONS-CONCENTRATION", "CHEMISTRY-SOLUTIONS-DILUTION"])
            question = Question(validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE")
            session.add(question)
            await session.flush()
            version = QuestionVersion(question_id=question.id, version_kind="official_original", canonical_text="Question", content_hash="taxonomy-fixture")
            session.add(version)
            await session.flush()
            await taxonomy.link_question(version.id, dilution.id, primary=True)
            await taxonomy.link_question(version.id, nodes["math_ratio"].id, primary=False)
            links = list((await session.scalars(select(ContentQuestionLink).where(ContentQuestionLink.question_version_id == version.id))).all())
            self.assertEqual(len(links), 2)
            self.assertEqual((await session.get(QuestionVersion, version.id)).metadata_["primary_content_node_id"], str(dilution.id))

    async def test_prerequisites_are_explicit_and_cannot_cycle(self):
        async with self.factory() as session:
            taxonomy = CurriculumTaxonomyService(session)
            nodes = await taxonomy.seed_reference_fixture()
            await taxonomy.add_prerequisite(nodes["chemistry_dilution"].id, nodes["chemistry_concentration"].id)
            row = await session.scalar(select(CatalogNodePrerequisite))
            self.assertEqual(row.content_node_id, nodes["chemistry_dilution"].id)
            with self.assertRaises(ValueError):
                await taxonomy.add_prerequisite(nodes["chemistry_concentration"].id, nodes["chemistry_dilution"].id)

    async def test_node_code_is_stable_and_tree_creation_rejects_invalid_parent_type(self):
        async with self.factory() as session:
            taxonomy = CurriculumTaxonomyService(session)
            await taxonomy.seed_reference_fixture()
            with self.assertRaises(ValueError):
                await taxonomy.create_node("Duplicate", "DISCIPLINE", "CHEMISTRY")
            with self.assertRaises(ValueError):
                await taxonomy.create_node("Invalid", "SUBCONTENT", "INVALID", parent_id=None)