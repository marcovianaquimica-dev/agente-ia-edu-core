"""Phase 9U.1-C — deterministic INITIAL classifier fix.

Local only: SQLite + the repo's real INEP pilot PDF for Q93/Q128 statements, and
fake providers. No PostgreSQL, no Alembic-on-a-real-db, no OpenAI.
"""

from __future__ import annotations

import importlib.util
import json
import random
import unittest
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, PedagogicalClassification, Question, QuestionVersion
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.providers.router import ProviderRouter
from agente_ia_edu.services.curriculum_classification import (
    KINETICS_RETRIEVAL_VOCABULARY,
    KINETICS_TAXONOMY_VERSION,
    ClassificationProposalService,
    ControlledVocabularyBinding,
)
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService
from agente_ia_edu.services.ingestion_parser import PdfParser


ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "var" / "inep-pilot" / "2020_PV_impresso_D2_CD5.pdf"
MIGRATION_PATH = ROOT / "migrations" / "versions" / "024_chemistry_kinetics.py"

_spec = importlib.util.spec_from_file_location("phase9u1c_migration", MIGRATION_PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

_parsed = {item.question_number: item for item in PdfParser.parse_file(PDF).questions}
STATEMENT_93 = _parsed[93].statement_text
STATEMENT_128 = _parsed[128].statement_text
NODE = "CHEMISTRY-PHYSICAL-KINETICS"
SOLUTIONS = "CHEMISTRY-SOLUTIONS"


class FakeProvider:
    provider = "fake"

    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        self.calls += 1
        return TextGenerationResult(json.dumps(self.response), self.provider, "fake-model")


def _router(provider):
    return ProviderRouter(text_providers=[provider], embedding_providers=[])


def _response(candidate, evidence, *, status="PROPOSED"):
    return {
        "selected_candidate_rank": candidate["rank"],
        "discipline_code": candidate["discipline_code"],
        "area_code": candidate["area_code"],
        "content_code": candidate["content_code"],
        "subcontent_code": candidate["subcontent_code"],
        "confidence": "HIGH",
        "evidence": [{"text": evidence, "reason": "Evidência textual."}],
        "candidate_classifications": [{**candidate, "rationale": "Candidato recuperado."}],
        "complementary_contents": [],
        "catalog_gap": False,
        "gap_type": None,
        "taxonomy_coverage_evidence": ["Candidato recuperado deterministicamente."],
        "review_reason": None,
        "visual_dependency": False,
        "status": status,
    }


async def _make_db(*, apply_migration=True):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await CurriculumTaxonomyService(session).seed_reference_fixture()
    if apply_migration:
        def _upgrade(connection):
            context = MigrationContext.configure(connection)
            prev = migration.op
            migration.op = Operations(context)
            try:
                migration.upgrade()
            finally:
                migration.op = prev
        async with engine.begin() as connection:
            await connection.run_sync(_upgrade)
    return engine, factory


async def _version(session, text, content_hash):
    question = Question(
        validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE"
    )
    session.add(question)
    await session.flush()
    version = QuestionVersion(
        question_id=question.id, version_kind="official_original", canonical_text=text, content_hash=content_hash
    )
    session.add(version)
    await session.flush()
    return version


async def _catalog(session):
    return list((await session.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)).order_by(CatalogNode.code))).all())


# --------------------------------------------------------------------------- #
# Fix 1 — deduplication keeps the higher-score / controlled-vocabulary candidate
# --------------------------------------------------------------------------- #


class DedupFixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_controlled_vocabulary_score_survives_dedup(self):
        async with self.factory() as session:
            catalog = await _catalog(session)
            svc = ClassificationProposalService(session)
            candidates = svc.recover_candidates(STATEMENT_128, catalog)
        kin = next(c for c in candidates if c["content_code"] == NODE)
        self.assertEqual(kin["candidate_type"], "CONTROLLED_VOCABULARY")
        self.assertEqual(kin["retrieval_vocabulary_version"], "phase9t3-kinetics-v1")
        self.assertEqual(kin["score"], 60)  # controlled-vocab score, not the weak lexical 21
        # Fix 6: the vocabulary does NOT globally win — the more specific SUBCONTENT
        # lexical candidate still outranks it on raw score.
        self.assertTrue(any(c["content_code"] == SOLUTIONS and c["score"] > kin["score"] for c in candidates))

    async def test_recover_candidates_is_order_independent_and_deterministic(self):
        async with self.factory() as session:
            catalog = await _catalog(session)
            svc = ClassificationProposalService(session)

            def signature(cat):
                rows = svc.recover_candidates(STATEMENT_128, list(cat))
                return [
                    (r["rank"], r["discipline_code"], r["area_code"], r["content_code"], r["subcontent_code"], r["score"])
                    for r in rows
                ]

            base = signature(catalog)
            self.assertEqual(base, signature(catalog))  # deterministic
            for seed in (1, 2, 3, 4, 5):
                shuffled = list(catalog)
                random.Random(seed).shuffle(shuffled)
                self.assertEqual(base, signature(shuffled), f"order-dependent for seed {seed}")

    def test_candidate_merge_priority_orders_by_score_then_vocabulary(self):
        priority = ClassificationProposalService._candidate_merge_priority
        weak = {"score": 21, "candidate_type": "ANCESTOR_CANDIDATE"}
        strong = {"score": 60, "candidate_type": "CONTROLLED_VOCABULARY", "retrieval_vocabulary_version": "phase9t3-kinetics-v1"}
        self.assertGreater(priority(strong), priority(weak))
        tie_vocab = {"score": 21, "candidate_type": "CONTROLLED_VOCABULARY", "retrieval_vocabulary_version": "v1"}
        self.assertGreater(priority(tie_vocab), priority(weak))


# --------------------------------------------------------------------------- #
# Fix 2 — resolve_initial_controlled_vocabulary_binding
# --------------------------------------------------------------------------- #


class BindingResolverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()
        async with self.factory() as session:
            self.catalog = await _catalog(session)
            self.svc = ClassificationProposalService(session)
            self.cands_93 = self.svc.recover_candidates(STATEMENT_93, self.catalog)
            self.cands_128 = self.svc.recover_candidates(STATEMENT_128, self.catalog)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def test_q93_binds_to_kinetics(self):
        binding = ClassificationProposalService.resolve_initial_controlled_vocabulary_binding(
            STATEMENT_93, KINETICS_TAXONOMY_VERSION, self.cands_93
        )
        self.assertIsInstance(binding, ControlledVocabularyBinding)
        self.assertEqual(binding.status, "BOUND")
        self.assertEqual(binding.canonical_code, NODE)
        self.assertEqual(binding.bound_candidate["content_code"], NODE)
        self.assertEqual(binding.controlled_vocabulary_version, "phase9t3-kinetics-v1")

    def test_q128_binds_to_kinetics_even_though_solutions_outranks(self):
        binding = ClassificationProposalService.resolve_initial_controlled_vocabulary_binding(
            STATEMENT_128, KINETICS_TAXONOMY_VERSION, self.cands_128
        )
        self.assertEqual(binding.status, "BOUND")
        self.assertEqual(binding.bound_candidate["content_code"], NODE)
        self.assertIn("estudo cinético", binding.matched_terms)
        self.assertTrue(any(c["content_code"] == SOLUTIONS for c in self.cands_128))

    def test_needs_review_when_kinetics_candidate_not_recovered(self):
        only_solutions = [c for c in self.cands_128 if c["content_code"] == SOLUTIONS]
        binding = ClassificationProposalService.resolve_initial_controlled_vocabulary_binding(
            STATEMENT_128, KINETICS_TAXONOMY_VERSION, only_solutions
        )
        self.assertEqual(binding.status, "NEEDS_REVIEW")
        self.assertIsNone(binding.bound_candidate)

    def test_none_when_no_vocabulary_match(self):
        binding = ClassificationProposalService.resolve_initial_controlled_vocabulary_binding(
            "A densidade é a razão entre massa e volume.", KINETICS_TAXONOMY_VERSION, []
        )
        self.assertIsNone(binding)

    def test_none_for_other_taxonomy_versions(self):
        binding = ClassificationProposalService.resolve_initial_controlled_vocabulary_binding(
            STATEMENT_128, "023_curriculum_taxonomy", self.cands_128
        )
        self.assertIsNone(binding)

    def test_apply_binding_conflict_and_no_candidate(self):
        bound = next(c for c in self.cands_128 if c["content_code"] == NODE)
        sol = next(c for c in self.cands_128 if c["content_code"] == SOLUTIONS)
        binding = ControlledVocabularyBinding(
            "phase9t3-kinetics-v1", NODE, KINETICS_TAXONOMY_VERSION, "BOUND", bound, ("estudo cinético",), "r"
        )
        base = _response(sol, "x")
        # provider agrees -> no conflict, codes forced to kinetics
        out_ok, sel_ok, conflict_ok = ClassificationProposalService.apply_controlled_vocabulary_binding(
            _response(bound, "x"), bound, binding
        )
        self.assertFalse(conflict_ok)
        self.assertEqual(out_ok["content_code"], NODE)
        # provider disagrees -> conflict
        out_bad, sel_bad, conflict_bad = ClassificationProposalService.apply_controlled_vocabulary_binding(
            base, sol, binding
        )
        self.assertTrue(conflict_bad)
        self.assertEqual(out_bad["content_code"], NODE)  # never SOLUTIONS
        # binding needs review -> conflict, codes cleared, no invented candidate
        nr = ControlledVocabularyBinding(
            "phase9t3-kinetics-v1", NODE, KINETICS_TAXONOMY_VERSION, "NEEDS_REVIEW", None, ("estudo cinético",), "r"
        )
        out_nr, sel_nr, conflict_nr = ClassificationProposalService.apply_controlled_vocabulary_binding(base, sol, nr)
        self.assertTrue(conflict_nr)
        self.assertIsNone(sel_nr)
        self.assertIsNone(out_nr["content_code"])


# --------------------------------------------------------------------------- #
# Fix 3/4/5 — end to end through classify_initial_with_provider (fake provider)
# --------------------------------------------------------------------------- #


class InitialBindingEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _classify(self, statement, content_hash, response):
        async with self.factory() as session:
            version = await _version(session, statement, content_hash)
            provider = FakeProvider(response)
            record = await ClassificationProposalService(session).classify_initial_with_provider(
                version.id,
                _router(provider),
                target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                classifier_version="phase9u1c-v1",
                prompt_version="phase9t3-kinetics-v1",
            )
            return record, provider, version

    async def _kinetics_candidate(self, statement):
        async with self.factory() as session:
            catalog = await _catalog(session)
            cands = ClassificationProposalService(session).recover_candidates(statement, catalog)
        return next(c for c in cands if c["content_code"] == NODE)

    async def _solutions_candidate(self, statement):
        async with self.factory() as session:
            catalog = await _catalog(session)
            cands = ClassificationProposalService(session).recover_candidates(statement, catalog)
        return next(c for c in cands if c["content_code"] == SOLUTIONS)

    async def test_q93_deterministic_kinetics(self):
        kin = await self._kinetics_candidate(STATEMENT_93)
        record, provider, _ = await self._classify(STATEMENT_93, "q93", _response(kin, "velocidade da reação"))
        self.assertEqual(record.metadata_["content_code"], NODE)
        self.assertEqual(record.metadata_["classification_mode"], "INITIAL")
        self.assertEqual(record.metadata_["taxonomy_version"], KINETICS_TAXONOMY_VERSION)
        self.assertEqual(record.status, "CLASSIFIED")
        self.assertEqual(provider.calls, 1)

    async def test_q128_deterministic_kinetics_when_provider_agrees(self):
        kin = await self._kinetics_candidate(STATEMENT_128)
        record, _, _ = await self._classify(STATEMENT_128, "q128", _response(kin, "estudo cinético"))
        self.assertEqual(record.metadata_["content_code"], NODE)
        self.assertEqual(record.metadata_["classification_mode"], "INITIAL")
        self.assertEqual(record.status, "CLASSIFIED")

    async def test_q128_provider_wrong_selection_is_rejected(self):
        sol = await self._solutions_candidate(STATEMENT_128)
        with self.assertRaisesRegex(ValueError, "Controlled vocabulary binding was not honoured"):
            await self._classify(STATEMENT_128, "q128", _response(sol, "a concentração de sacarose foi reduzida à metade"))
        async with self.factory() as session:
            total = await session.scalar(select(func.count()).select_from(PedagogicalClassification))
        self.assertEqual(total, 0)  # existing/incorrect prod row is never touched; nothing new persisted

    async def test_no_controlled_vocabulary_match_preserves_prior_behaviour(self):
        # A pure concentration statement (no kinetics vocabulary term) -> normal flow,
        # provider's CHEMISTRY-SOLUTIONS selection is accepted as before.
        statement = "Foram dissolvidos 171 g de sacarose em 500 mL de água; qual a concentração da solução?"
        sol = await self._solutions_candidate(statement)
        record, _, _ = await self._classify(statement, "plain", _response(sol, "qual a concentração da solução"))
        self.assertEqual(record.metadata_["content_code"], SOLUTIONS)
        self.assertEqual(record.metadata_["classification_mode"], "INITIAL")

    async def test_binding_needs_review_path_rejects_via_persist(self):
        # White-box: statement vocab-matches but the recovered set lacks the kinetics
        # candidate -> _persist_provider_output must fail closed.
        async with self.factory() as session:
            version = await _version(session, STATEMENT_128, "wb")
            catalog = await _catalog(session)
            svc = ClassificationProposalService(session)
            sol = next(c for c in svc.recover_candidates(STATEMENT_128, catalog) if c["content_code"] == SOLUTIONS)
            with self.assertRaisesRegex(ValueError, "Controlled vocabulary binding was not honoured"):
                await svc._persist_provider_output(
                    version,
                    _response(sol, "a concentração de sacarose foi reduzida à metade"),
                    catalog,
                    [sol],  # recovered_candidates WITHOUT kinetics
                    "deadbeef",
                    "phase9u1c-v1",
                    KINETICS_TAXONOMY_VERSION,
                    "phase9t3-kinetics-v1",
                    "fake",
                    "fake-model",
                    classification_mode="INITIAL",
                )
            total = await session.scalar(select(func.count()).select_from(PedagogicalClassification))
        self.assertEqual(total, 0)


# --------------------------------------------------------------------------- #
# Impact — RECLASSIFICATION is unaffected by the INITIAL-only binding
# --------------------------------------------------------------------------- #


class ReclassificationImpactTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_reclassification_mode_does_not_apply_the_binding(self):
        # In RECLASSIFICATION mode a provider choosing the SOLUTIONS candidate for a
        # kinetics-worded statement is still accepted (pre-fix behaviour), because the
        # binding is gated on classification_mode == "INITIAL".
        async with self.factory() as session:
            version = await _version(session, STATEMENT_128, "recl")
            catalog = await _catalog(session)
            svc = ClassificationProposalService(session)
            sol = next(c for c in svc.recover_candidates(STATEMENT_128, catalog) if c["content_code"] == SOLUTIONS)
            record = await svc._persist_provider_output(
                version,
                _response(sol, "a concentração de sacarose foi reduzida à metade"),
                catalog,
                svc.recover_candidates(STATEMENT_128, catalog),
                "cafe",
                "reclassify-v1",
                KINETICS_TAXONOMY_VERSION,
                "phase9t3-kinetics-v1",
                "fake",
                "fake-model",
                classification_mode="RECLASSIFICATION",
            )
        self.assertEqual(record.metadata_["content_code"], SOLUTIONS)
        self.assertEqual(record.metadata_["classification_mode"], "RECLASSIFICATION")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
