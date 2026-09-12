"""PHASE 11.23 - focused integration tests for the AI-agnostic classification entrypoint.

FakeProvider only. In-memory sqlite only. No OpenAI. No 332-question bank.
Every propose_and_audit call asserts ZERO persisted rows.
"""

import ast
import json
import pathlib
import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import PedagogicalClassification, Question, QuestionVersion
from agente_ia_edu.providers.errors import ProviderUnavailableError
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.providers.router import ProviderRouter
from agente_ia_edu.services.ai_classification_service import AiAgnosticClassificationService
from agente_ia_edu.services.classification_consensus import ConsensusOutcome, ConsensusPolicy
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
    provider = "entrypoint-fake"

    def __init__(self, response=None, *, raise_error=None):
        self.calls = 0
        self.prompts = []
        self.response = response if response is not None else _VALID_RESPONSE
        self.raise_error = raise_error

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        self.calls += 1
        self.prompts.append(request.prompt)
        if self.raise_error is not None:
            raise self.raise_error
        return TextGenerationResult(text=json.dumps(self.response), provider=self.provider, model="fake-model")


class AiAgnosticClassificationServiceTests(unittest.IsolatedAsyncioTestCase):
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
                                      content_hash="entrypoint-fixture")
            session.add(version)
            await session.commit()
            self.qv_id = version.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _count(self):
        async with self.factory() as session:
            return len((await session.execute(select(PedagogicalClassification))).scalars().all())

    async def _audit(self, provider, **kw):
        svc = AiAgnosticClassificationService(self.factory, provider=provider)
        out = await svc.propose_and_audit(
            self.qv_id, classifier_version="entrypoint-v1", taxonomy_version="reference-v1", **kw)
        self.assertEqual(await self._count(), 0, "propose_and_audit must not persist")
        return svc, out

    # 1 - factory provider reaches the service (when provider is not injected)
    async def test_factory_provider_reaches_the_service(self):
        fake = _FakeProvider()
        router = ProviderRouter([fake], [])
        with patch("agente_ia_edu.services.ai_classification_service.build_text_provider", return_value=router) as bt:
            svc = AiAgnosticClassificationService(self.factory)  # provider=None -> factory
            out = await svc.propose_and_audit(
                self.qv_id, classifier_version="entrypoint-v1", taxonomy_version="reference-v1")
        bt.assert_called()  # the service obtained its provider through the factory
        self.assertEqual(fake.calls, 3)
        self.assertEqual(out.verdict, "CLASSIFIED")
        self.assertEqual(await self._count(), 0)

    # 2 - the versioned prompt artifact reaches the pipeline
    async def test_versioned_prompt_reaches_the_pipeline(self):
        fake = _FakeProvider()
        svc, out = await self._audit(fake)
        self.assertEqual(svc.classification_prompt_version, "v1")
        self.assertEqual(len(fake.prompts), 3)
        for prompt in fake.prompts:
            self.assertIn("RESPONSE_SCHEMA: ", prompt)
            self.assertIn("RULES: RECOVERED_CANDIDATES is the complete authority", prompt)
            self.assertIn("QUESTION_DATA: ", prompt)

    # 3 - the consensus result reaches the service
    async def test_consensus_result_reaches_the_service(self):
        _, out = await self._audit(_FakeProvider())
        self.assertIsInstance(out, ConsensusOutcome)
        self.assertEqual(out.verdict, "CLASSIFIED")
        self.assertEqual(out.content_code, "CHEMISTRY-SOLUTIONS")
        self.assertEqual(out.n, 3)

    # 4 - a successful consensus does NOT persist by default
    async def test_successful_consensus_does_not_persist_by_default(self):
        _, out = await self._audit(_FakeProvider())
        self.assertEqual(out.verdict, "CLASSIFIED")
        self.assertEqual(await self._count(), 0)

    # 5a - explicit persistence DELEGATES to ClassificationProposalService.propose
    async def test_explicit_persistence_delegates_to_deterministic_propose(self):
        svc, out = await self._audit(_FakeProvider())

        seen = {}

        async def _capture(self_svc, qv_id, proposal, *, classifier_version, taxonomy_version,
                           provider, model, prompt_version):
            seen.update(qv_id=qv_id, primary=proposal.primary_content_code,
                        evidence_text=[e["text"] for e in proposal.evidence],
                        classifier_version=classifier_version, taxonomy_version=taxonomy_version,
                        provider=provider, model=model, prompt_version=prompt_version)
            return object()

        with patch.object(ClassificationProposalService, "propose", new=_capture):
            async with self.factory() as session:
                await svc.persist_classified_consensus(
                    session, out, self.qv_id, evidence=[{"text": "Diluição", "reason": "consensus"}],
                    classifier_version="entrypoint-persist-v1", taxonomy_version="reference-v1",
                    provider_label="phase-11-23", model_label="deterministic-v1", confirm=True)
        self.assertEqual(seen["primary"], "CHEMISTRY-SOLUTIONS")
        self.assertEqual(seen["evidence_text"], ["Diluição"])
        self.assertEqual(seen["taxonomy_version"], "reference-v1")
        self.assertEqual(seen["provider"], "phase-11-23")
        self.assertEqual(seen["prompt_version"], "v1")  # the versioned artifact
        self.assertEqual(await self._count(), 0)  # the mock did not write

    # 5b - a real explicit persist creates exactly one deterministic ('rule') ACTIVE row
    async def test_explicit_persistence_creates_one_deterministic_row(self):
        svc, out = await self._audit(_FakeProvider())
        async with self.factory() as session:
            record = await svc.persist_classified_consensus(
                session, out, self.qv_id, evidence=[{"text": "Diluição", "reason": "consensus"}],
                classifier_version="entrypoint-persist-v1", taxonomy_version="reference-v1",
                provider_label="phase-11-23", model_label="deterministic-v1", confirm=True)
        self.assertEqual(record.status, "CLASSIFIED")
        self.assertEqual(record.source, "rule")  # the deterministic ClassificationProposalService.propose path
        self.assertEqual((record.metadata_ or {})["taxonomy_version"], "reference-v1")
        self.assertEqual((record.metadata_ or {})["primary_content_code"], "CHEMISTRY-SOLUTIONS")
        self.assertEqual(await self._count(), 1)

    # 6 - persistence refuses without explicit confirm
    async def test_persistence_requires_explicit_confirm(self):
        svc, out = await self._audit(_FakeProvider())
        async with self.factory() as session:
            with self.assertRaises(ValueError):
                await svc.persist_classified_consensus(
                    session, out, self.qv_id, evidence=[{"text": "Diluição"}],
                    classifier_version="x", taxonomy_version="reference-v1",
                    provider_label="p", model_label="m")  # confirm defaults to False
        self.assertEqual(await self._count(), 0)

    # 7 - HUMAN_REVIEW does not persist
    async def test_human_review_outcome_does_not_persist(self):
        hr = ConsensusOutcome(verdict="HUMAN_REVIEW", content_code=None, confidence=None,
                              n=3, reason="test", runs=())
        svc = AiAgnosticClassificationService(self.factory, provider=_FakeProvider())
        async with self.factory() as session:
            with self.assertRaises(ValueError):
                await svc.persist_classified_consensus(
                    session, hr, self.qv_id, evidence=[{"text": "x"}],
                    classifier_version="x", taxonomy_version="reference-v1",
                    provider_label="p", model_label="m", confirm=True)
        self.assertEqual(await self._count(), 0)

    # 8 - a provider error yields HUMAN_REVIEW and never persists
    async def test_provider_error_yields_human_review_and_no_persist(self):
        _, out = await self._audit(_FakeProvider(raise_error=ProviderUnavailableError("down")))
        self.assertEqual(out.verdict, "HUMAN_REVIEW")
        self.assertIn("PROVIDER_ERROR", out.reason)
        self.assertEqual(await self._count(), 0)

    # 9 - N is configurable via policy
    async def test_policy_is_configurable(self):
        fake = _FakeProvider()
        svc = AiAgnosticClassificationService(self.factory, provider=fake, policy=ConsensusPolicy(n=1))
        out = await svc.propose_and_audit(
            self.qv_id, classifier_version="entrypoint-v1", taxonomy_version="reference-v1")
        self.assertEqual(out.n, 1)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(await self._count(), 0)

    # 10 - the existing single-run API is untouched and still persists directly
    async def test_existing_single_run_api_still_works(self):
        async with self.factory() as session:
            rec = await ClassificationProposalService(session).propose_with_provider(
                self.qv_id, ProviderRouter([_FakeProvider()], []),
                classifier_version="direct-v1", taxonomy_version="reference-v1", prompt_version="v1")
            self.assertEqual((rec.metadata_ or {})["proposal_status"], "PROPOSED")
        self.assertEqual(await self._count(), 1)


class AiAgnosticEntrypointStaticTests(unittest.TestCase):
    _MODULE = (pathlib.Path(__file__).resolve().parents[1]
               / "src" / "agente_ia_edu" / "services" / "ai_classification_service.py")

    def test_no_provider_specific_imports_or_construction(self):
        src = self._MODULE.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn("adapters", node.module or "")
                self.assertNotIn("openai", (node.module or "").lower())
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("openai", alias.name.lower())
        # docstrings excluded from the code-token scan
        docstrings = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        code_strings = [n.value for n in ast.walk(tree)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in docstrings]
        joined = " ".join(code_strings).lower()
        for marker in ("openaiprovider(", "asyncopenai", "import openai", "gpt-", "response_format", "api_key"):
            self.assertNotIn(marker, joined)
        self.assertNotIn("OpenAIProvider(", src.split('"""', 2)[-1])  # no construction outside the docstring

    def test_obtains_provider_via_factory(self):
        src = self._MODULE.read_text()
        self.assertIn("from agente_ia_edu.providers.factory import build_text_provider", src)
        self.assertIn("build_text_provider()", src)


if __name__ == "__main__":
    unittest.main()
