import importlib.util
import json
import unittest
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, ContentQuestionLink, PedagogicalClassification, Question, QuestionVersion
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.services.curriculum_classification import ClassificationProposalService
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService


MIGRATION_PATH = Path(__file__).parents[1] / "migrations" / "versions" / "024_chemistry_kinetics.py"
SPEC = importlib.util.spec_from_file_location("kinetics_reclassification_migration", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


class ReclassificationFakeProvider:
    provider = "reclassification-fake"

    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        self.calls += 1
        return TextGenerationResult(json.dumps(self.response), self.provider, "fake-model")


class TaxonomyReclassificationWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        async with self.factory() as session:
            await CurriculumTaxonomyService(session).seed_reference_fixture()
        await self._apply_kinetics_migration()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _apply_kinetics_migration(self):
        def upgrade(connection):
            context = MigrationContext.configure(connection)
            original = migration.op
            migration.op = Operations(context)
            try:
                migration.upgrade()
            finally:
                migration.op = original
        async with self.engine.begin() as connection:
            await connection.run_sync(upgrade)

    async def _version(self, session, text, content_hash):
        question = Question(validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE")
        session.add(question)
        await session.flush()
        version = QuestionVersion(question_id=question.id, version_kind="official_original", canonical_text=text, content_hash=content_hash)
        session.add(version)
        await session.flush()
        return version

    async def _historical_proposal(self, session, version):
        record = PedagogicalClassification(
            question_version_id=version.id, discipline="", content="", subcontent="", difficulty="UNKNOWN",
            reasoning_type="UNSPECIFIED", status="NEEDS_REVIEW", source="ai",
            metadata_={"taxonomy_version": "023_curriculum_taxonomy", "input_hash": "old-input", "output_hash": "old-output"},
        )
        session.add(record)
        await session.commit()
        return record

    @staticmethod
    def _response(candidate, statement):
        return {
            "selected_candidate_rank": candidate["rank"],
            "discipline_code": candidate["discipline_code"],
            "area_code": candidate["area_code"],
            "content_code": candidate["content_code"],
            "subcontent_code": candidate["subcontent_code"],
            "confidence": "HIGH",
            "evidence": [{"text": "velocidade da reação" if "velocidade da reação" in statement else "estudo cinético", "reason": "Evidência de cinética química."}],
            "candidate_classifications": [{**candidate, "rationale": "Candidato controlado selecionado."}],
            "complementary_contents": [],
            "catalog_gap": False,
            "gap_type": None,
            "taxonomy_coverage_evidence": ["O candidato foi recuperado pelo vocabulário controlado."],
            "review_reason": None,
            "visual_dependency": False,
            "status": "PROPOSED",
        }

    async def test_reclassifies_93_without_modifying_historical_proposal(self):
        statement = "Nanomateriais catalíticos aumentam a velocidade da reação química."
        async with self.factory() as session:
            version = await self._version(session, statement, "q93")
            historical = await self._historical_proposal(session, version)
            historical_snapshot = (
                historical.id,
                historical.discipline,
                historical.content,
                historical.subcontent,
                historical.status,
                historical.created_at.replace(tzinfo=None),
                dict(historical.metadata_),
            )
            service = ClassificationProposalService(session)
            catalog = list((await session.scalars(select(CatalogNode))).all())
            candidate = next(item for item in service.recover_candidates(statement, catalog) if item["content_code"] == migration.NODE_CODE)
            provider = ReclassificationFakeProvider(self._response(candidate, statement))
            first = await service.reclassify_with_provider(version.id, provider, source_taxonomy_version="023_curriculum_taxonomy", target_taxonomy_version=migration.TAXONOMY_VERSION, classifier_version="reclassify-v1", prompt_version="contract-v1", reclassification_reason="taxonomy expansion")
            second = await service.reclassify_with_provider(version.id, provider, source_taxonomy_version="023_curriculum_taxonomy", target_taxonomy_version=migration.TAXONOMY_VERSION, classifier_version="reclassify-v1", prompt_version="contract-v1", reclassification_reason="taxonomy expansion")

            self.assertEqual(first.id, second.id)
            self.assertEqual(provider.calls, 1)
            self.assertEqual((first.discipline, first.content, first.subcontent), ("CHEMISTRY", migration.NODE_CODE, ""))
            await session.refresh(historical)
            self.assertEqual(
                (
                    historical.id,
                    historical.discipline,
                    historical.content,
                    historical.subcontent,
                    historical.status,
                    historical.created_at.replace(tzinfo=None),
                    historical.metadata_,
                ),
                historical_snapshot,
            )
            self.assertEqual(first.metadata_["taxonomy_version"], migration.TAXONOMY_VERSION)
            self.assertEqual(first.metadata_["reclassification"]["original_proposal_id"], str(historical.id))
            self.assertEqual(first.metadata_["reclassification"]["controlled_vocabulary_version"], "phase9t3-kinetics-v1")
            self.assertTrue(first.metadata_["input_hash"] and first.metadata_["output_hash"])

    async def test_reclassifies_128_with_kinetics_and_existing_candidates(self):
        statement = "Em um estudo cinético, a concentração de sacarose foi reduzida à metade."
        async with self.factory() as session:
            version = await self._version(session, statement, "q128")
            await self._historical_proposal(session, version)
            service = ClassificationProposalService(session)
            catalog = list((await session.scalars(select(CatalogNode))).all())
            candidates = service.recover_candidates(statement, catalog)
            kinetics = next(item for item in candidates if item["content_code"] == migration.NODE_CODE)
            self.assertTrue(any(item["subcontent_code"] == "CHEMISTRY-SOLUTIONS-CONCENTRATION" for item in candidates))
            record = await service.reclassify_with_provider(version.id, ReclassificationFakeProvider(self._response(kinetics, statement)), source_taxonomy_version="023_curriculum_taxonomy", target_taxonomy_version=migration.TAXONOMY_VERSION, classifier_version="reclassify-v1", prompt_version="contract-v1", reclassification_reason="taxonomy expansion")
            self.assertEqual(record.metadata_["content_code"], migration.NODE_CODE)
            self.assertGreaterEqual(len(record.metadata_["recovered_candidates"]), 2)

    async def test_controlled_vocabulary_does_not_leak_to_regressions_or_modify_catalog(self):
        texts = {
            "91": "Soluções aquosas com pH próximo da neutralidade.",
            "92": "Pressão e temperatura de ebulição da água.",
            "95": "Sal de cobalto muda de cor na presença de água.",
            "104": "O dióxido de carbono retorna ao ambiente.",
            "135": "A razão entre massa e área define a densidade.",
        }
        async with self.factory() as session:
            service = ClassificationProposalService(session)
            catalog = list((await session.scalars(select(CatalogNode))).all())
            for number, text in texts.items():
                with self.subTest(question=number):
                    self.assertFalse(any(item["content_code"] == migration.NODE_CODE for item in service.recover_candidates(text, catalog)))
            self.assertEqual(await session.scalar(select(func.count()).select_from(CatalogNode)), 18)
            self.assertEqual(await session.scalar(select(func.count()).select_from(ContentQuestionLink)), 0)

    async def test_reclassification_cannot_bypass_evidence_or_candidate_validation(self):
        statement = "O estudo cinético mede a velocidade da reação."
        async with self.factory() as session:
            version = await self._version(session, statement, "invalid-workflow")
            await self._historical_proposal(session, version)
            service = ClassificationProposalService(session)
            catalog = list((await session.scalars(select(CatalogNode))).all())
            candidate = next(item for item in service.recover_candidates(statement, catalog) if item["content_code"] == migration.NODE_CODE)
            invalid_evidence = self._response(candidate, statement)
            invalid_evidence["evidence"] = []
            with self.assertRaisesRegex(ValueError, "Invalid classification evidence"):
                await service.reclassify_with_provider(version.id, ReclassificationFakeProvider(invalid_evidence), source_taxonomy_version="023_curriculum_taxonomy", target_taxonomy_version=migration.TAXONOMY_VERSION, classifier_version="reclassify-invalid", prompt_version="contract-v1", reclassification_reason="taxonomy expansion")
            invalid_candidate = self._response(candidate, statement)
            invalid_candidate["content_code"] = "CHEMISTRY-SOLUTIONS"
            with self.assertRaisesRegex(ValueError, "Curriculum candidate was not recovered"):
                await service.reclassify_with_provider(version.id, ReclassificationFakeProvider(invalid_candidate), source_taxonomy_version="023_curriculum_taxonomy", target_taxonomy_version=migration.TAXONOMY_VERSION, classifier_version="reclassify-invalid", prompt_version="contract-v2", reclassification_reason="taxonomy expansion")
            self.assertEqual(await session.scalar(select(func.count()).select_from(PedagogicalClassification)), 1)

    async def test_target_taxonomy_missing_or_incompatible_fails_before_provider_call(self):
        statement = "O estudo cinético mede a velocidade da reação."
        async with self.factory() as session:
            version = await self._version(session, statement, "target-validation")
            service = ClassificationProposalService(session)
            catalog = list((await session.scalars(select(CatalogNode))).all())
            candidate = next(item for item in service.recover_candidates(statement, catalog) if item["content_code"] == migration.NODE_CODE)
            provider = ReclassificationFakeProvider(self._response(candidate, statement))
            with self.assertRaisesRegex(ValueError, "Target taxonomy version is unavailable"):
                await service.reclassify_with_provider(version.id, provider, source_taxonomy_version="023_curriculum_taxonomy", target_taxonomy_version="missing-version", classifier_version="target-v1", prompt_version="contract-v1", reclassification_reason="taxonomy expansion")
            self.assertEqual(provider.calls, 0)
            node = await session.scalar(select(CatalogNode).where(CatalogNode.code == migration.NODE_CODE))
            node.node_type = "SUBCONTENT"
            await session.commit()
            with self.assertRaisesRegex(ValueError, "Target taxonomy node is unavailable or incompatible"):
                await service.reclassify_with_provider(version.id, provider, source_taxonomy_version="023_curriculum_taxonomy", target_taxonomy_version=migration.TAXONOMY_VERSION, classifier_version="target-v1", prompt_version="contract-v1", reclassification_reason="taxonomy expansion")
            self.assertEqual(provider.calls, 0)

    async def test_reclassification_without_historical_proposal_fails_closed(self):
        statement = "O estudo cinético mede a velocidade da reação."
        async with self.factory() as session:
            version = await self._version(session, statement, "no-history")
            service = ClassificationProposalService(session)
            catalog = list((await session.scalars(select(CatalogNode))).all())
            candidate = next(item for item in service.recover_candidates(statement, catalog) if item["content_code"] == migration.NODE_CODE)
            provider = ReclassificationFakeProvider(self._response(candidate, statement))
            with self.assertRaisesRegex(ValueError, "Source taxonomy proposal is unavailable"):
                await service.reclassify_with_provider(version.id, provider, source_taxonomy_version="023_curriculum_taxonomy", target_taxonomy_version=migration.TAXONOMY_VERSION, classifier_version="no-history-v1", prompt_version="contract-v1", reclassification_reason="taxonomy expansion")
            self.assertEqual(provider.calls, 0)