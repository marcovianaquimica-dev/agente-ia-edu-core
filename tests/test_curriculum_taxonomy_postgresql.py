import asyncio
import os
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.models import CatalogNode, CatalogNodePrerequisite, PedagogicalClassification, Question, QuestionVersion
from agente_ia_edu.services.curriculum_classification import ClassificationProposal, ClassificationProposalService
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService


class CurriculumTaxonomyPostgreSQLTests(unittest.TestCase):
    database_name = "agente_ia_edu_taxonomy_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres"
    database_url = f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}"

    @classmethod
    def setUpClass(cls):
        try: cls._admin("SELECT 1")
        except Exception as exc: raise unittest.SkipTest("PostgreSQL unavailable") from exc

    @classmethod
    def _admin(cls, statement):
        engine = create_engine(cls.admin_url, connect_args={"autocommit": True}, execution_options={"isolation_level": "AUTOCOMMIT"})
        try:
            with engine.connect() as connection: connection.execute(text(statement))
        finally: engine.dispose()

    @classmethod
    def _drop(cls):
        try:
            cls._admin(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{cls.database_name}' AND pid <> pg_backend_pid()")
            cls._admin(f"DROP DATABASE IF EXISTS {cls.database_name}")
        except Exception: pass

    def _config(self):
        config = Config("alembic.ini"); config.set_main_option("sqlalchemy.url", self.database_url); return config

    def _seed_reference_catalog(self):
        """Seed the reference catalog and return how many nodes were created."""
        async def seed():
            engine = create_async_engine(self.database_url)
            try:
                factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                async with factory() as session:
                    service = CurriculumTaxonomyService(session)
                    await service.seed_reference_fixture()
                    return service.created_nodes
            finally:
                await engine.dispose()

        return asyncio.run(seed())

    def _upgrade_to_head(self):
        """Bring the database to head through the real Alembic chain.

        The tests below exercise today's models, so the schema has to be the
        one a deployment actually has: the whole chain, not a revision this
        class pins itself to and not ``Base.metadata.create_all`` (which
        proves only what the models claim, never that a migration wrote it).

        The chain stops once on the way up. 024_chemistry_kinetics is a DATA
        migration, not just schema: it inserts a content node under a
        REQUIRED, already-existing parent (catalog_node code
        CHEMISTRY-PHYSICAL) and fails loudly if that parent is missing - by
        design, see migrations/versions/024_chemistry_kinetics.py's own
        _validate_parent. Nothing in this repository creates that node in a
        migration; it only ever comes from
        CurriculumTaxonomyService.seed_reference_fixture(). In every real
        environment it was seeded long before 024 was authored, so seed it
        here at exactly that point - after the chain reaches 023, the last
        revision before 024 reads the catalog, and before it advances past it.
        """
        config = self._config()
        command.upgrade(config, "023_curriculum_taxonomy")
        self.seeded_nodes = self._seed_reference_catalog()
        command.upgrade(config, "head")

    def setUp(self):
        self._drop(); self._admin(f"CREATE DATABASE {self.database_name}"); self._upgrade_to_head()

    def tearDown(self): self._drop()

    def test_upgrade_downgrade_reupgrade(self):
        # Migration-subject test. setUp already proved the chain replays from
        # an empty database all the way up to head; what is left to prove is
        # that it reverses across the revision this class is named after and
        # replays from there.
        command.downgrade(self._config(), "022_modification_proposals")
        self._upgrade_to_head()
        # catalog_nodes itself predates 023, so the round trip leaves the
        # reference catalog in place and the re-seed creates nothing - which
        # is also what lets 024_chemistry_kinetics find its parent on the way
        # back up, exactly as it does in a real deployment.
        self.assertEqual(self.seeded_nodes, 0)

    def test_tree_codes_prerequisites_and_cycle_guard(self):
        # setUp seeds the reference catalog on an empty database, so the 17
        # creations this test used to count happen there; seeding again has
        # to create nothing and return the very same rows.
        self.assertEqual(self.seeded_nodes, 17)

        async def run():
            engine = create_async_engine(self.database_url)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                service = CurriculumTaxonomyService(session)
                nodes = await service.seed_reference_fixture()
                self.assertEqual(service.created_nodes, 0)
                repeated = await service.seed_reference_fixture()
                self.assertEqual(service.created_nodes, 0)
                self.assertEqual(nodes["chemistry"].id, repeated["chemistry"].id)
                # 024_chemistry_kinetics is part of the chain setUp now runs, so its row is here.
                self.assertIsNotNone(await session.scalar(select(CatalogNode.id).where(CatalogNode.code == "CHEMISTRY-PHYSICAL-KINETICS")))
                await service.add_prerequisite(nodes["chemistry_dilution"].id, nodes["chemistry_concentration"].id)
                with self.assertRaises(ValueError): await service.add_prerequisite(nodes["chemistry_concentration"].id, nodes["chemistry_dilution"].id)
                self.assertEqual(await session.scalar(select(CatalogNodePrerequisite.content_node_id)), nodes["chemistry_dilution"].id)
            await engine.dispose()
        asyncio.run(run())

    def test_classification_proposal_persists_without_question_mutation(self):
        # PedagogicalClassification.lifecycle, which propose() persists, is
        # added by 025_classification_lifecycle - reached by setUp, which now
        # migrates all the way to head.
        async def run():
            engine = create_async_engine(self.database_url)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                question = Question(validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE")
                session.add(question); await session.flush()
                version = QuestionVersion(question_id=question.id, version_kind="official_original", canonical_text="Diluição requer concentração.", content_hash="postgres-classification")
                session.add(version); await session.commit()
                proposal = ClassificationProposal("CHEMISTRY-SOLUTIONS-DILUTION", [], ["concentracao"], ["CHEMISTRY-SOLUTIONS-CONCENTRATION"], ["APPLY"], "", "UNKNOWN", 0.9, [{"text":"Diluição", "content_code":"CHEMISTRY-SOLUTIONS-DILUTION"}])
                record = await ClassificationProposalService(session).propose(version.id, proposal, classifier_version="v1", taxonomy_version="reference-v1", provider="rule", model="deterministic", prompt_version="p1")
                self.assertEqual(record.status, "CLASSIFIED")
                self.assertEqual((await session.get(QuestionVersion, version.id)).recommended_difficulty, None)
                self.assertEqual(await session.scalar(select(PedagogicalClassification.question_version_id)), version.id)
            await engine.dispose()
        asyncio.run(run())
