import json
import unittest
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    CatalogNode,
    ContentQuestionLink,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    PedagogicalClassification,
    Question,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
)
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.services.curriculum_classification import ClassificationProposalService
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService


class Question91FakeProvider:
    provider = "phase9e1-fake"

    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.requests = []

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        self.calls += 1
        self.requests.append(request)
        return TextGenerationResult(
            text=json.dumps(self.response), provider=self.provider, model="fake-model"
        )


class Phase9E1Question91AcceptanceTests(unittest.IsolatedAsyncioTestCase):
    classifier_version = "phase9e1-taxonomy-coverage-v1"
    prompt_version = "phase9e1-classification-contract-v1"
    taxonomy_version = "reference-v1"

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.fixture_number = 0

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_question91(
        self,
        session,
        *,
        question_number=91,
        canonical_text="O fármaco é liberado em soluções aquosas cujo pH é próximo da neutralidade.",
    ):
        await CurriculumTaxonomyService(session).seed_reference_fixture()
        self.fixture_number += 1
        institution = Institution(code=f"INEP-{self.fixture_number}", name="INEP")
        session.add(institution)
        await session.flush()
        exam = Exam(institution_id=institution.id, code="ENEM", name="ENEM")
        session.add(exam)
        await session.flush()
        application = ExamApplication(
            exam_id=exam.id, year=2020, application_type="REGULAR", day=2
        )
        session.add(application)
        await session.flush()
        booklet = ExamBooklet(
            exam_application_id=application.id, code="D2_CD5", color="AMARELO"
        )
        session.add(booklet)
        await session.flush()
        source = SourceDocument(
            exam_application_id=application.id,
            exam_booklet_id=booklet.id,
            document_type="ANSWER_KEY",
            source_url="https://example.test/key.pdf",
            acquired_at=datetime.now(timezone.utc),
            content_hash="answer-key",
        )
        session.add(source)
        await session.flush()
        revision = AnswerKeyRevision(source_document_id=source.id, revision_number=1)
        question = Question(
            validation_status="validated", origin_type="IMPORTED",
            status="PUBLISHED", visibility_scope="PUBLIC",
        )
        session.add_all([revision, question])
        await session.flush()
        version = QuestionVersion(
            question_id=question.id,
            version_kind="official_original",
            canonical_text=canonical_text,
            content_hash=f"question-{question_number}-hash",
        )
        session.add(version)
        await session.flush()
        booklet_question = BookletQuestion(
            exam_booklet_id=booklet.id, question_version_id=version.id,
            position=question_number, official_number=question_number,
        )
        session.add(booklet_question)
        await session.flush()
        session.add_all([
            QuestionOption(question_version_id=version.id, option_key=key, position=index, text=f"Alternativa {key}")
            for index, key in enumerate("ABCDE", start=1)
        ])
        session.add(AnswerKeyEntry(
            answer_key_revision_id=revision.id, booklet_question_id=booklet_question.id,
            official_answer_label="C",
        ))
        await session.commit()
        return version, booklet_question

    @staticmethod
    def _response():
        return {
            "selected_candidate_rank": 1,
            "discipline_code": "CHEMISTRY",
            "area_code": "CHEMISTRY-PHYSICAL",
            "content_code": "CHEMISTRY-SOLUTIONS",
            "subcontent_code": None,
            "confidence": "LOW",
            "evidence": [{"text": "pH", "reason": "Relaciona a liberação do fármaco ao pH."}],
            "candidate_classifications": [{
                "discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
                "content_code": "CHEMISTRY-SOLUTIONS", "subcontent_code": None,
                "rank": 1, "rationale": "É o nível curricular superior mais próximo disponível.",
            }],
            "complementary_contents": [],
            "catalog_gap": True,
            "gap_type": "MISSING_SUBCONTENT",
            "taxonomy_coverage_evidence": ["Existe conteúdo de Soluções, mas não subconteúdo específico."],
            "review_reason": "TAXONOMY_GRANULARITY_GAP",
            "visual_dependency": False,
            "status": "NEEDS_REVIEW",
        }

    @staticmethod
    def _question92_response():
        return {
            "selected_candidate_rank": None,
            "discipline_code": None,
            "area_code": None,
            "content_code": None,
            "subcontent_code": None,
            "confidence": "LOW",
            "evidence": [{"text": "temperatura de ebulição", "reason": "Relaciona pressão e ebulição."}],
            "candidate_classifications": [],
            "complementary_contents": [],
            "catalog_gap": True,
            "gap_type": "NO_COMPATIBLE_NODE",
            "taxonomy_coverage_evidence": ["Não há termodinâmica, pressão ou ebulição no catálogo."],
            "review_reason": "CATALOG_GAP",
            "visual_dependency": False,
            "status": "NEEDS_REVIEW",
        }

    async def _propose(self, session, response):
        provider = Question91FakeProvider(response)
        record = await ClassificationProposalService(session).propose_with_provider(
            (await self._seed_question91(session))[0].id,
            provider,
            classifier_version=self.classifier_version,
            taxonomy_version=self.taxonomy_version,
            prompt_version=self.prompt_version,
        )
        return record, provider

    async def test_accepts_question91_partial_taxonomy_granularity_gap(self):
        async with self.factory() as session:
            version, booklet_question = await self._seed_question91(session)
            initial_version = (version.canonical_text, version.content_hash, version.recommended_difficulty)
            initial_options = [(option.option_key, option.text) for option in (await session.scalars(select(QuestionOption).where(QuestionOption.question_version_id == version.id).order_by(QuestionOption.position))).all()]
            initial_answer = await session.scalar(select(AnswerKeyEntry.official_answer_label).where(AnswerKeyEntry.booklet_question_id == booklet_question.id))
            provider = Question91FakeProvider(self._response())
            service = ClassificationProposalService(session)
            first = await service.propose_with_provider(version.id, provider, classifier_version=self.classifier_version, taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)
            second = await service.propose_with_provider(version.id, provider, classifier_version=self.classifier_version, taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)

            metadata = first.metadata_
            self.assertEqual(first.id, second.id)
            self.assertEqual(provider.calls, 1)
            self.assertEqual((metadata["discipline_code"], metadata["area_code"], metadata["content_code"], metadata["subcontent_code"]), ("CHEMISTRY", "CHEMISTRY-PHYSICAL", "CHEMISTRY-SOLUTIONS", None))
            self.assertEqual(str(first.classification_confidence), "0.4000")
            self.assertEqual(metadata["confidence_band"], "LOW")
            self.assertEqual(metadata["candidate_classifications"][0]["rank"], 1)
            self.assertEqual(len(metadata["candidate_classifications"]), 1)
            self.assertEqual(metadata["gap_type"], "MISSING_SUBCONTENT")
            self.assertEqual(metadata["review_reason"], "TAXONOMY_GRANULARITY_GAP")
            self.assertTrue(metadata["catalog_gap"])
            self.assertFalse(metadata["visual_dependency"])
            self.assertEqual(first.status, "NEEDS_REVIEW")
            self.assertEqual(metadata["evidence"][0]["text"], "pH")
            self.assertTrue(metadata["taxonomy_coverage_evidence"])
            self.assertTrue(metadata["input_hash"] and metadata["output_hash"])
            self.assertEqual(metadata["question_content_hash"], "question-91-hash")
            self.assertEqual(first.model_version, self.classifier_version)
            self.assertEqual(first.prompt_version, self.prompt_version)
            self.assertEqual(metadata["taxonomy_version"], self.taxonomy_version)
            self.assertEqual(initial_version, (version.canonical_text, version.content_hash, version.recommended_difficulty))
            self.assertEqual(initial_options, [(option.option_key, option.text) for option in (await session.scalars(select(QuestionOption).where(QuestionOption.question_version_id == version.id).order_by(QuestionOption.position))).all()])
            self.assertEqual(initial_answer, await session.scalar(select(AnswerKeyEntry.official_answer_label).where(AnswerKeyEntry.booklet_question_id == booklet_question.id)))
            self.assertEqual(await session.scalar(select(func.count()).select_from(CatalogNode)), 17)
            self.assertEqual(await session.scalar(select(func.count()).select_from(ContentQuestionLink)), 0)
            self.assertEqual(await session.scalar(select(func.count()).select_from(PedagogicalClassification)), 1)

    async def test_rejects_invalid_candidate_cases(self):
        cases = (
            ("missing candidate", {"discipline_code": "MISSING", "area_code": None, "content_code": None, "subcontent_code": None, "rank": 1, "rationale": "Inválido"}),
            ("incompatible parent", {"discipline_code": "CHEMISTRY", "area_code": "MATH-ALGEBRA", "content_code": None, "subcontent_code": None, "rank": 1, "rationale": "Inválido"}),
            ("duplicate rank", {"discipline_code": "MATH", "area_code": "MATH-ALGEBRA", "content_code": "MATH-ALGEBRA-FUNCTIONS", "subcontent_code": "MATH-ALGEBRA-RATIO", "rank": 1, "rationale": "Duplicado"}),
        )
        for label, candidate in cases:
            with self.subTest(label=label), self.assertRaises(ValueError):
                async with self.factory() as session:
                    response = self._response()
                    response["candidate_classifications"] = [response["candidate_classifications"][0], candidate]
                    await self._propose(session, response)

    async def test_rejects_incoherent_gap_review_and_evidence(self):
        cases = (
            ("gap/review", {"gap_type": "MISSING_SUBCONTENT", "review_reason": "LOW_CONFIDENCE"}),
            ("proposed/granularity", {"status": "PROPOSED"}),
            ("malformed evidence", {"evidence": [{"text": "pH"}]}),
        )
        for label, changes in cases:
            with self.subTest(label=label), self.assertRaises(ValueError):
                async with self.factory() as session:
                    response = self._response()
                    response.update(changes)
                    await self._propose(session, response)

    async def test_distinguishes_question91_granularity_from_question92_catalog_gap(self):
        async with self.factory() as session:
            version91, _ = await self._seed_question91(session)
            version92, _ = await self._seed_question91(
                session,
                question_number=92,
                canonical_text=(
                    "Panelas de pressão elevam a temperatura de ebulição da água "
                    "e evitam consumo de gás desnecessário."
                ),
            )
            service = ClassificationProposalService(session)
            candidates92 = service.retrieve_candidate_classifications(
                version92.canonical_text,
                list((await session.scalars(select(CatalogNode))).all()),
            )
            provider91 = Question91FakeProvider(self._response())
            provider92 = Question91FakeProvider(self._question92_response())
            record91 = await service.propose_with_provider(version91.id, provider91, classifier_version=self.classifier_version, taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)
            record92 = await service.propose_with_provider(version92.id, provider92, classifier_version=self.classifier_version, taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)

            self.assertEqual(candidates92, [])
            self.assertEqual(record91.metadata_["gap_type"], "MISSING_SUBCONTENT")
            self.assertEqual(record91.metadata_["review_reason"], "TAXONOMY_GRANULARITY_GAP")
            self.assertEqual(record92.metadata_["gap_type"], "NO_COMPATIBLE_NODE")
            self.assertEqual(record92.metadata_["review_reason"], "CATALOG_GAP")
            self.assertNotEqual(record91.metadata_["gap_type"], record92.metadata_["gap_type"])
            self.assertNotEqual(record91.metadata_["review_reason"], record92.metadata_["review_reason"])
            self.assertEqual(record92.metadata_["candidate_classifications"], [])
            self.assertEqual((record92.discipline, record92.content, record92.subcontent), ("", "", ""))
            self.assertEqual(record91.status, "NEEDS_REVIEW")
            self.assertEqual(record92.status, "NEEDS_REVIEW")
            self.assertEqual(await session.scalar(select(func.count()).select_from(CatalogNode)), 17)
            self.assertEqual(await session.scalar(select(func.count()).select_from(ContentQuestionLink)), 0)
            self.assertEqual(await session.scalar(select(func.count()).select_from(PedagogicalClassification)), 2)

    async def test_rejects_invented_thermodynamics_codes_without_creating_nodes(self):
        async with self.factory() as session:
            version, _ = await self._seed_question91(
                session,
                question_number=92,
                canonical_text="A pressão eleva a temperatura de ebulição da água.",
            )
            response = self._question92_response()
            response.update({
                "discipline_code": "PHYSICS",
                "area_code": "PHYSICS-THERMODYNAMICS",
                "content_code": "PHYSICS-THERMODYNAMICS-PRESSURE",
                "candidate_classifications": [{
                    "discipline_code": "PHYSICS",
                    "area_code": "PHYSICS-THERMODYNAMICS",
                    "content_code": "PHYSICS-THERMODYNAMICS-PRESSURE",
                    "subcontent_code": None,
                    "rank": 1,
                    "rationale": "Código inventado.",
                }],
            })
            with self.assertRaisesRegex(ValueError, "Primary curriculum codes must be null") as context:
                await ClassificationProposalService(session).propose_with_provider(version.id, Question91FakeProvider(response), classifier_version=self.classifier_version, taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)
            self.assertEqual(context.exception.diagnostic_output["candidate_count"], 1)
            self.assertEqual(context.exception.diagnostic_output["gap_type"], "NO_COMPATIBLE_NODE")
            self.assertEqual(await session.scalar(select(func.count()).select_from(CatalogNode)), 17)
            self.assertEqual(await session.scalar(select(func.count()).select_from(ContentQuestionLink)), 0)
            self.assertEqual(await session.scalar(select(func.count()).select_from(PedagogicalClassification)), 0)

    async def test_phase9f4_preflight_question92_catalog_gap_contract(self):
        async with self.factory() as session:
            version91, _ = await self._seed_question91(session)
            version92, _ = await self._seed_question91(
                session,
                question_number=92,
                canonical_text="A pressão eleva a temperatura de ebulição da água.",
            )
            service = ClassificationProposalService(session)
            catalog = list((await session.scalars(select(CatalogNode))).all())
            recovered91 = service.retrieve_candidate_classifications(version91.canonical_text, catalog)
            recovered92 = service.retrieve_candidate_classifications(version92.canonical_text, catalog)
            self.assertTrue(recovered91)
            self.assertEqual(recovered92, [])

            valid_provider = Question91FakeProvider(self._question92_response())
            record = await service.propose_with_provider(version92.id, valid_provider, classifier_version=self.classifier_version, taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)
            prompt = valid_provider.requests[0].prompt
            self.assertEqual(valid_provider.calls, 1)
            self.assertIn("RECOVERED_CANDIDATES: []", prompt)
            self.assertNotIn("CATALOG_AUTHORIZED:", prompt)
            for requirement in ("selected_candidate_rank must be null", "candidate_classifications must be []", "gap_type must be NO_COMPATIBLE_NODE", "review_reason must be CATALOG_GAP", "status must be NEEDS_REVIEW", "Do not use external knowledge"):
                self.assertIn(requirement, prompt)
            self.assertEqual(record.metadata_["recovered_candidates"], [])
            self.assertIsNone(record.metadata_["selected_candidate_rank"])
            self.assertEqual(record.metadata_["candidate_count"], 0)
            self.assertEqual(record.metadata_["review_reason"], "CATALOG_GAP")
            self.assertTrue(record.metadata_["input_hash"] and record.metadata_["output_hash"])

            for label, change, expected in (
                ("selected rank", {"selected_candidate_rank": 1}, "Selected candidate rank must be null"),
                ("primary code", {"discipline_code": "PHYSICS"}, "Primary curriculum codes must be null"),
                ("candidate", {"candidate_classifications": [self._response()["candidate_classifications"][0]]}, "Candidates must be empty"),
            ):
                with self.subTest(label=label):
                    alternate = await self._seed_question91(
                        session,
                        question_number=100 + len(label),
                        canonical_text="A pressão eleva a temperatura de ebulição da água.",
                    )
                    response = self._question92_response()
                    response.update(change)
                    with self.assertRaisesRegex(ValueError, expected):
                        await service.propose_with_provider(alternate[0].id, Question91FakeProvider(response), classifier_version=f"preflight-{label}", taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)

            self.assertNotEqual(
                service._hash({"question": version91.canonical_text, "candidates": recovered91}),
                service._hash({"question": version92.canonical_text, "candidates": recovered92}),
            )
            self.assertEqual(await session.scalar(select(func.count()).select_from(CatalogNode)), 17)
            self.assertEqual(await session.scalar(select(func.count()).select_from(ContentQuestionLink)), 0)

    async def test_question92_catalog_gap_allows_empty_concept_evidence_with_coverage_evidence(self):
        async with self.factory() as session:
            version, _ = await self._seed_question91(
                session,
                question_number=92,
                canonical_text="A pressão eleva a temperatura de ebulição da água.",
            )
            response = self._question92_response()
            response["evidence"] = []
            record = await ClassificationProposalService(session).propose_with_provider(version.id, Question91FakeProvider(response), classifier_version="evidence-v1", taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)
            self.assertEqual(record.status, "NEEDS_REVIEW")
            self.assertEqual(record.metadata_["review_reason"], "CATALOG_GAP")
            self.assertEqual(record.metadata_["evidence"], [])
            self.assertTrue(record.metadata_["taxonomy_coverage_evidence"])

    async def test_empty_evidence_remains_invalid_outside_complete_catalog_gap(self):
        async with self.factory() as session:
            version, _ = await self._seed_question91(session)
            response = self._response()
            response["evidence"] = []
            with self.assertRaisesRegex(ValueError, "Invalid classification evidence"):
                await ClassificationProposalService(session).propose_with_provider(version.id, Question91FakeProvider(response), classifier_version="evidence-v2", taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)
            gap_version, _ = await self._seed_question91(
                session,
                question_number=92,
                canonical_text="A pressão eleva a temperatura de ebulição da água.",
            )
            response = self._question92_response()
            response["evidence"] = []
            response["taxonomy_coverage_evidence"] = []
            with self.assertRaisesRegex(ValueError, "CATALOG_GAP requires taxonomy coverage evidence"):
                await ClassificationProposalService(session).propose_with_provider(gap_version.id, Question91FakeProvider(response), classifier_version="evidence-v3", taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)

    async def test_real_question92_response_requires_taxonomy_coverage_evidence(self):
        real_response = {
            "selected_candidate_rank": None,
            "discipline_code": None,
            "area_code": None,
            "content_code": None,
            "subcontent_code": None,
            "confidence": "LOW",
            "evidence": [],
            "candidate_classifications": [],
            "complementary_contents": [],
            "catalog_gap": True,
            "gap_type": "NO_COMPATIBLE_NODE",
            "taxonomy_coverage_evidence": [],
            "review_reason": "CATALOG_GAP",
            "visual_dependency": False,
            "status": "NEEDS_REVIEW",
        }
        async with self.factory() as session:
            rejected_version, _ = await self._seed_question91(
                session,
                question_number=92,
                canonical_text="A pressão eleva a temperatura de ebulição da água.",
            )
            with self.assertRaisesRegex(ValueError, "CATALOG_GAP requires taxonomy coverage evidence"):
                await ClassificationProposalService(session).propose_with_provider(rejected_version.id, Question91FakeProvider(real_response), classifier_version="phase9f5-real-response", taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)
            self.assertEqual(await session.scalar(select(func.count()).select_from(PedagogicalClassification)), 0)

            accepted_version, _ = await self._seed_question91(
                session,
                question_number=93,
                canonical_text="A pressão eleva a temperatura de ebulição da água.",
            )
            corrected_response = {
                **real_response,
                "taxonomy_coverage_evidence": [
                    "Nenhum candidato recuperado representa pressão e ebulição."
                ],
            }
            record = await ClassificationProposalService(session).propose_with_provider(accepted_version.id, Question91FakeProvider(corrected_response), classifier_version="phase9f5-real-response", taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)
            self.assertEqual(record.status, "NEEDS_REVIEW")
            self.assertEqual(record.metadata_["evidence"], [])
            self.assertEqual(record.metadata_["review_reason"], "CATALOG_GAP")
            self.assertTrue(record.metadata_["taxonomy_coverage_evidence"])

    async def test_catalog_gap_rejects_malformed_coverage_and_incomplete_structure(self):
        async with self.factory() as session:
            malformed_version, _ = await self._seed_question91(
                session,
                question_number=92,
                canonical_text="A pressão eleva a temperatura de ebulição da água.",
            )
            malformed = self._question92_response()
            malformed.update({"evidence": [], "taxonomy_coverage_evidence": "not-a-list"})
            with self.assertRaisesRegex(ValueError, "Invalid taxonomy coverage evidence"):
                await ClassificationProposalService(session).propose_with_provider(malformed_version.id, Question91FakeProvider(malformed), classifier_version="phase9f7-malformed", taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)

            incomplete_version, _ = await self._seed_question91(
                session,
                question_number=93,
                canonical_text="A pressão eleva a temperatura de ebulição da água.",
            )
            incomplete = self._question92_response()
            incomplete.update({"evidence": [], "review_reason": None})
            with self.assertRaisesRegex(ValueError, "Invalid classification evidence"):
                await ClassificationProposalService(session).propose_with_provider(incomplete_version.id, Question91FakeProvider(incomplete), classifier_version="phase9f7-incomplete", taxonomy_version=self.taxonomy_version, prompt_version=self.prompt_version)