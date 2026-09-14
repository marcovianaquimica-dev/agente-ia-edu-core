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

    def setUp(self):
        self._drop(); self._admin(f"CREATE DATABASE {self.database_name}"); command.upgrade(self._config(), "023_curriculum_taxonomy")

    def tearDown(self): self._drop()

    def test_upgrade_downgrade_reupgrade(self):
        command.downgrade(self._config(), "022_modification_proposals")
        command.upgrade(self._config(), "023_curriculum_taxonomy")

    def test_tree_codes_prerequisites_and_cycle_guard(self):
        async def run():
            engine = create_async_engine(self.database_url)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                service = CurriculumTaxonomyService(session)
                nodes = await service.seed_reference_fixture()
                self.assertEqual(service.created_nodes, 17)
                repeated = await service.seed_reference_fixture()
                self.assertEqual(service.created_nodes, 0)
                self.assertEqual(nodes["chemistry"].id, repeated["chemistry"].id)
                await service.add_prerequisite(nodes["chemistry_dilution"].id, nodes["chemistry_concentration"].id)
                with self.assertRaises(ValueError): await service.add_prerequisite(nodes["chemistry_concentration"].id, nodes["chemistry_dilution"].id)
                self.assertEqual(await session.scalar(select(CatalogNodePrerequisite.content_node_id)), nodes["chemistry_dilution"].id)
            await engine.dispose()
        asyncio.run(run())

    def test_classification_proposal_persists_without_question_mutation(self):
        # setUp only migrates to 023_curriculum_taxonomy (the revision this
        # class is scoped to), but ClassificationProposalService.propose has
        # since grown to persist PedagogicalClassification.lifecycle, added
        # only by the later 025_classification_lifecycle migration.
        #
        # That migration chain runs THROUGH 024_chemistry_kinetics, which is
        # a DATA migration, not just schema: it inserts a content node under
        # a REQUIRED, already-existing parent (catalog_node code
        # CHEMISTRY-PHYSICAL) and fails loudly if that parent is missing -
        # by design (migrations/versions/024_chemistry_kinetics.py's own
        # _validate_parent). In every real environment that parent was
        # seeded long before 024 was authored; nothing in this repo creates
        # it via a migration, so a from-scratch test database never has it.
        # Seed the same small reference catalog CurriculumTaxonomyService
        # itself uses (which creates CHEMISTRY-PHYSICAL, among others)
        # BEFORE advancing past 023, mirroring what every real deployment's
        # database already had at that point. seed_reference_fixture() is
        # idempotent by code, so the SAME call later in this test's own body
        # simply finds these nodes already present and reuses them.
        async def seed_reference_catalog():
            engine = create_async_engine(self.database_url)
            try:
                factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                async with factory() as session:
                    await CurriculumTaxonomyService(session).seed_reference_fixture()
            finally:
                await engine.dispose()

        asyncio.run(seed_reference_catalog())
        command.upgrade(self._config(), "025_classification_lifecycle")

        async def run():
            engine = create_async_engine(self.database_url)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                nodes = await CurriculumTaxonomyService(session).seed_reference_fixture()
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