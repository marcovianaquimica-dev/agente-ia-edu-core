"""PHASE 11.19 - focused tests for the formal consensus & plausibility gate.

FakeProvider only. No OpenAI. No 332-question bank. Every ``run_classification_consensus``
call asserts ZERO PedagogicalClassification rows were written.
"""

import ast
import json
import pathlib
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import PedagogicalClassification, Question, QuestionVersion
from agente_ia_edu.providers.errors import ProviderUnavailableError
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.services.classification_consensus import (
    ConsensusPolicy,
    DEFAULT_CONSENSUS_POLICY,
    DEFAULT_N,
    DEFAULT_REQUIRED_CONFIDENCE,
    ProposalRun,
    classify_run,
    consolidate_consensus,
    run_classification_consensus,
)
from agente_ia_edu.services.curriculum_classification import ClassificationProposalService
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService

_VALID_RESPONSE = {
    "selected_candidate_rank": 1,
    "discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
    "content_code": "CHEMISTRY-SOLUTIONS", "subcontent_code": "CHEMISTRY-SOLUTIONS-DILUTION",
    "confidence": "HIGH", "evidence": [{"text": "Diluição", "reason": "Exige concentracao de solucoes."}],
    "candidate_classifications": [{"discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
                                   "content_code": "CHEMISTRY-SOLUTIONS", "subcontent_code": "CHEMISTRY-SOLUTIONS-DILUTION",
                                   "rank": 1, "rationale": "Caminho completo do catálogo."}],
    "complementary_contents": ["MATH-ALGEBRA-RATIO"], "catalog_gap": False, "gap_type": None,
    "taxonomy_coverage_evidence": [], "review_reason": None, "visual_dependency": False, "status": "PROPOSED",
}


class _FakeProvider:
    provider = "consensus-fake"

    def __init__(self, response=None, *, raise_error=None, raw_text=None):
        self.calls = 0
        self.response = response if response is not None else _VALID_RESPONSE
        self.raise_error = raise_error
        self.raw_text = raw_text

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        self.calls += 1
        if self.raise_error is not None:
            raise self.raise_error
        text = self.raw_text if self.raw_text is not None else json.dumps(self.response)
        return TextGenerationResult(text=text, provider=self.provider, model="fake-model")


def _run(outcome, content=None, confidence=None, **kw):
    return ProposalRun(outcome=outcome, content_code=content, confidence=confidence, **kw)


class ConsolidateConsensusPureTests(unittest.TestCase):
    def test_three_identical_classified_high_are_accepted(self):
        runs = [_run("CLASSIFIED", "MATH-ALGEBRA-PERCENTAGE", "HIGH")] * 3
        out = consolidate_consensus(runs)
        self.assertEqual(out.verdict, "CLASSIFIED")
        self.assertEqual(out.content_code, "MATH-ALGEBRA-PERCENTAGE")
        self.assertEqual(out.confidence, "HIGH")
        self.assertEqual(out.n, 3)

    def test_different_content_is_human_review(self):
        runs = [_run("CLASSIFIED", "A", "HIGH"), _run("CLASSIFIED", "B", "HIGH"), _run("CLASSIFIED", "A", "HIGH")]
        out = consolidate_consensus(runs)
        self.assertEqual(out.verdict, "HUMAN_REVIEW")
        self.assertIn("content disagreement", out.reason)

    def test_classified_plus_human_review_is_human_review(self):
        runs = [_run("CLASSIFIED", "A", "HIGH"), _run("HUMAN_REVIEW"), _run("CLASSIFIED", "A", "HIGH")]
        out = consolidate_consensus(runs)
        self.assertEqual(out.verdict, "HUMAN_REVIEW")
        self.assertIn("not CLASSIFIED", out.reason)

    def test_classified_plus_curriculum_gap_is_human_review(self):
        runs = [_run("CLASSIFIED", "A", "HIGH"), _run("CURRICULUM_GAP", "A", "MEDIUM"), _run("CLASSIFIED", "A", "HIGH")]
        out = consolidate_consensus(runs)
        self.assertEqual(out.verdict, "HUMAN_REVIEW")
        self.assertIn("CURRICULUM_GAP", out.reason)
        self.assertIsNone(out.content_code)  # never forced

    def test_provider_error_is_human_review(self):
        runs = [_run("CLASSIFIED", "A", "HIGH"), _run("PROVIDER_ERROR"), _run("CLASSIFIED", "A", "HIGH")]
        out = consolidate_consensus(runs)
        self.assertEqual(out.verdict, "HUMAN_REVIEW")
        self.assertIn("PROVIDER_ERROR", out.reason)

    def test_confidence_below_high_is_human_review(self):
        runs = [_run("CLASSIFIED", "A", "HIGH"), _run("CLASSIFIED", "A", "MEDIUM"), _run("CLASSIFIED", "A", "HIGH")]
        out = consolidate_consensus(runs)
        self.assertEqual(out.verdict, "HUMAN_REVIEW")
        self.assertIn("confidence below policy HIGH", out.reason)

    def test_wrong_number_of_runs_is_human_review(self):
        out = consolidate_consensus([_run("CLASSIFIED", "A", "HIGH")] * 2)  # policy.n == 3
        self.assertEqual(out.verdict, "HUMAN_REVIEW")
        self.assertIn("expected 3 runs", out.reason)

    def test_n_is_configurable(self):
        policy5 = ConsensusPolicy(n=5)
        self.assertEqual(consolidate_consensus([_run("CLASSIFIED", "A", "HIGH")] * 5, policy5).verdict, "CLASSIFIED")
        self.assertEqual(consolidate_consensus([_run("CLASSIFIED", "A", "HIGH")] * 4, policy5).verdict, "HUMAN_REVIEW")

    def test_policy_validation(self):
        with self.assertRaises(ValueError):
            ConsensusPolicy(n=0)
        with self.assertRaises(ValueError):
            ConsensusPolicy(required_confidence="VERY_HIGH")
        self.assertEqual(DEFAULT_CONSENSUS_POLICY.n, DEFAULT_N)
        self.assertEqual(DEFAULT_CONSENSUS_POLICY.required_confidence, DEFAULT_REQUIRED_CONFIDENCE)

    def test_classify_run_maps_record_shapes(self):
        classified = type("R", (), {"status": "CLASSIFIED", "metadata_": {
            "content_code": "X", "confidence_band": "HIGH", "discipline_code": "MATH", "proposal_status": "PROPOSED"}})()
        self.assertEqual(classify_run(classified).outcome, "CLASSIFIED")
        review = type("R", (), {"status": "NEEDS_REVIEW", "metadata_": {
            "content_code": "X", "confidence_band": "LOW", "review_reason": "LOW_CONFIDENCE",
            "proposal_status": "NEEDS_REVIEW"}})()
        self.assertEqual(classify_run(review).outcome, "HUMAN_REVIEW")
        gap = type("R", (), {"status": "NEEDS_REVIEW", "metadata_": {
            "content_code": "X", "confidence_band": "MEDIUM", "gap_type": "MISSING_SUBCONTENT",
            "review_reason": "TAXONOMY_GRANULARITY_GAP"}})()
        self.assertEqual(classify_run(gap).outcome, "CURRICULUM_GAP")

    def test_no_provider_specific_dependency(self):
        src = pathlib.Path(
            __file__).resolve().parents[1] / "src" / "agente_ia_edu" / "services" / "classification_consensus.py"
        tree = ast.parse(src.read_text())

        # (a) no import from a vendor adapter / SDK
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        for mod in imported:
            self.assertNotIn("adapters", mod, mod)
            self.assertNotIn("openai", mod.lower(), mod)
            self.assertNotIn("anthropic", mod.lower(), mod)

        # (b) no vendor token in executable code (docstrings excluded)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        code_strings = [
            n.value.lower() for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in docstrings
        ]
        for blob in code_strings:
            for marker in ("openai", "anthropic", "asyncopenai", "gpt-", "claude-", "api_key"):
                self.assertNotIn(marker, blob, f"vendor token {marker!r} in code string {blob!r}")


class RunClassificationConsensusIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        async with self.factory() as session:
            await CurriculumTaxonomyService(session).seed_reference_fixture()
            question = Question(validation_status="validated", origin_type="IMPORTED",
                                status="DRAFT", visibility_scope="PRIVATE")
            session.add(question)
            await session.flush()
            version = QuestionVersion(question_id=question.id, version_kind="official_original",
                                      canonical_text="Diluição de soluções exige concentração.",
                                      content_hash="consensus-fixture")
            session.add(version)
            await session.commit()
            self.qv_id = version.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _classification_count(self) -> int:
        async with self.factory() as session:
            rows = (await session.execute(select(PedagogicalClassification))).scalars().all()
            return len(rows)

    async def _consensus(self, provider, *, policy=DEFAULT_CONSENSUS_POLICY):
        out = await run_classification_consensus(
            question_version_id=self.qv_id, provider=provider, session_factory=self.factory,
            classifier_version="consensus-test-v1", taxonomy_version="reference-v1",
            prompt_version="v1", policy=policy)
        self.assertEqual(await self._classification_count(), 0, "consensus must not persist")
        return out

    async def test_three_identical_classified_high_accepted_zero_writes(self):
        provider = _FakeProvider(_VALID_RESPONSE)
        out = await self._consensus(provider)
        self.assertEqual(out.verdict, "CLASSIFIED")
        self.assertEqual(out.content_code, "CHEMISTRY-SOLUTIONS")
        self.assertEqual(out.confidence, "HIGH")
        self.assertEqual(provider.calls, 3)
        self.assertEqual(len(out.runs), 3)

    async def test_provider_error_every_run_is_human_review(self):
        provider = _FakeProvider(raise_error=ProviderUnavailableError("down"))
        out = await self._consensus(provider)
        self.assertEqual(out.verdict, "HUMAN_REVIEW")
        self.assertIn("PROVIDER_ERROR", out.reason)

    async def test_invalid_provider_json_is_human_review(self):
        provider = _FakeProvider(raw_text="not-json-at-all")
        out = await self._consensus(provider)
        self.assertEqual(out.verdict, "HUMAN_REVIEW")
        self.assertTrue(all(r.outcome == "HUMAN_REVIEW" for r in out.runs))

    async def test_invalid_candidate_is_human_review(self):
        bad = dict(_VALID_RESPONSE)
        bad["content_code"] = "NOT-A-REAL-CODE"
        bad["candidate_classifications"] = [dict(_VALID_RESPONSE["candidate_classifications"][0],
                                                 content_code="NOT-A-REAL-CODE")]
        out = await self._consensus(_FakeProvider(bad))
        self.assertEqual(out.verdict, "HUMAN_REVIEW")

    async def test_n1_preserves_single_run_behavior_zero_writes(self):
        out = await self._consensus(_FakeProvider(_VALID_RESPONSE), policy=ConsensusPolicy(n=1))
        self.assertEqual(out.verdict, "CLASSIFIED")
        self.assertEqual(out.n, 1)
        self.assertEqual(len(out.runs), 1)
        # the standalone single-run API is untouched and still persists directly
        async with self.factory() as session:
            rec = await ClassificationProposalService(session).propose_with_provider(
                self.qv_id, _FakeProvider(_VALID_RESPONSE),
                classifier_version="direct-v1", taxonomy_version="reference-v1", prompt_version="v1")
            self.assertEqual(rec.metadata_["proposal_status"], "PROPOSED")
            self.assertEqual(await self._classification_count(), 1)

    async def test_below_high_confidence_run_is_human_review(self):
        medium = dict(_VALID_RESPONSE)
        medium["confidence"] = "MEDIUM"
        medium["status"] = "NEEDS_REVIEW"  # LOW/MEDIUM-only paths still go through the decision core
        out = await self._consensus(_FakeProvider(medium))
        self.assertEqual(out.verdict, "HUMAN_REVIEW")


if __name__ == "__main__":
    unittest.main()
