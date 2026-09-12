import importlib.util
import json
import unittest
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, ContentQuestionLink, PedagogicalClassification, Question, QuestionVersion
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.services.curriculum_classification import ClassificationProposalService
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService


MIGRATION_PATH = Path(__file__).parents[1] / "migrations" / "versions" / "024_chemistry_kinetics.py"
SPEC = importlib.util.spec_from_file_location("initial_kinetics_migration", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


class InitialFakeProvider:
    provider = "initial-fake"

    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        self.calls += 1
        return TextGenerationResult(json.dumps(self.response), self.provider, "fake-model")


class InitialTaxonomyClassificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.factory() as session:
            await CurriculumTaxonomyService(session).seed_reference_fixture()
        await self._upgrade()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _upgrade(self):
        def operation(connection):
            context = MigrationContext.configure(connection)
            previous = migration.op
            migration.op = Operations(context)
            try:
                migration.upgrade()
            finally:
                migration.op = previous
        async with self.engine.begin() as connection:
            await connection.run_sync(operation)

    async def _version(self, session, text, content_hash):
        question = Question(validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE")
        session.add(question)
        await session.flush()
        version = QuestionVersion(question_id=question.id, version_kind="official_original", canonical_text=text, content_hash=content_hash)
        session.add(version)
        await session.flush()
        return version

    @staticmethod
    def _response(candidate, evidence):
        return {
            "selected_candidate_rank": candidate["rank"],
            "discipline_code": candidate["discipline_code"],
            "area_code": candidate["area_code"],
            "content_code": candidate["content_code"],
            "subcontent_code": candidate["subcontent_code"],
            "confidence": "HIGH",
            "evidence": [{"text": evidence, "reason": "Evidência de cinética."}],
            "candidate_classifications": [{**candidate, "rationale": "Candidato controlado."}],
            "complementary_contents": [], "catalog_gap": False, "gap_type": None,
            "taxonomy_coverage_evidence": ["Vocabulário controlado recuperou o candidato."],
            "review_reason": None, "visual_dependency": False, "status": "PROPOSED",
        }

    async def _initial(self, session, text, content_hash, evidence):
        version = await self._version(session, text, content_hash)
        service = ClassificationProposalService(session)
        catalog = list((await session.scalars(select(CatalogNode))).all())
        candidates = service.recover_candidates(text, catalog)
        kinetics = next(item for item in candidates if item["content_code"] == migration.NODE_CODE)
        provider = InitialFakeProvider(self._response(kinetics, evidence))
        record = await service.classify_initial_with_provider(version.id, provider, target_taxonomy_version=migration.TAXONOMY_VERSION, classifier_version="initial-v1", prompt_version="contract-v1")
        return version, record, provider, candidates

    async def test_initial_classification_93_does_not_require_or_create_history(self):
        text = "Nanomateriais catalíticos aumentam a velocidade da reação química."
        async with self.factory() as session:
            version, first, provider, _ = await self._initial(session, text, "q93", "velocidade da reação")
            second = await ClassificationProposalService(session).classify_initial_with_provider(version.id, provider, target_taxonomy_version=migration.TAXONOMY_VERSION, classifier_version="initial-v1", prompt_version="contract-v1")
            self.assertEqual(first.id, second.id)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(first.metadata_["classification_mode"], "INITIAL")
            self.assertIsNone(first.metadata_["reclassification"])
            self.assertEqual(first.metadata_["taxonomy_version"], migration.TAXONOMY_VERSION)
            self.assertEqual(first.metadata_["content_code"], migration.NODE_CODE)
            self.assertTrue(first.metadata_["input_hash"] and first.metadata_["output_hash"])
            self.assertEqual(await session.scalar(select(func.count()).select_from(PedagogicalClassification).where(PedagogicalClassification.question_version_id == version.id)), 1)

    async def test_initial_classification_128_preserves_concentration_candidate(self):
        text = "Em um estudo cinético, a concentração de sacarose foi reduzida à metade."
        async with self.factory() as session:
            _, record, _, candidates = await self._initial(session, text, "q128", "estudo cinético")
            self.assertTrue(any(item["subcontent_code"] == "CHEMISTRY-SOLUTIONS-CONCENTRATION" for item in candidates))
            self.assertTrue(any(item["content_code"] == migration.NODE_CODE for item in record.metadata_["recovered_candidates"]))
            self.assertEqual(record.metadata_["classification_mode"], "INITIAL")

    async def test_initial_blocks_invalid_target_candidate_and_evidence_without_history(self):
        async with self.factory() as session:
            version = await self._version(session, "O estudo cinético mede a velocidade da reação.", "invalid")
            provider = InitialFakeProvider({})
            service = ClassificationProposalService(session)
            with self.assertRaisesRegex(ValueError, "Target taxonomy version is unavailable"):
                await service.classify_initial_with_provider(version.id, provider, target_taxonomy_version="missing", classifier_version="initial-v1", prompt_version="contract-v1")
            self.assertEqual(provider.calls, 0)
            catalog = list((await session.scalars(select(CatalogNode))).all())
            candidate = next(item for item in service.recover_candidates(version.canonical_text, catalog) if item["content_code"] == migration.NODE_CODE)
            invalid = self._response(candidate, "estudo cinético")
            invalid["evidence"] = []
            with self.assertRaisesRegex(ValueError, "Invalid classification evidence"):
                await service.classify_initial_with_provider(version.id, InitialFakeProvider(invalid), target_taxonomy_version=migration.TAXONOMY_VERSION, classifier_version="initial-v2", prompt_version="contract-v1")
            self.assertEqual(await session.scalar(select(func.count()).select_from(PedagogicalClassification)), 0)

    async def test_initial_vocabulary_isolated_from_regressions(self):
        texts = ("Soluções aquosas com pH.", "Pressão e ebulição.", "Sal de cobalto.", "Dióxido de carbono.", "Razão entre massa e área.")
        async with self.factory() as session:
            service = ClassificationProposalService(session)
            catalog = list((await session.scalars(select(CatalogNode))).all())
            for text in texts:
                self.assertFalse(any(item["content_code"] == migration.NODE_CODE for item in service.recover_candidates(text, catalog)))
            self.assertEqual(await session.scalar(select(func.count()).select_from(ContentQuestionLink)), 0)