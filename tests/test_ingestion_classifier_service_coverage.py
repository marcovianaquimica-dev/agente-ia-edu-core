"""Service-layer coverage for agente_ia_edu.services.ingestion_classifier.

Targets branches left uncovered by test_ingestion_classifier.py (the docx
pilot-material integration suite) and test_ingestion_classifier_n1.py (the
query-count regression suite): DifficultyPolicy's unknown-value error, the
missing-document and no-questions early returns, the taxonomy_code
validation path (building the node map AND all three of its NEEDS_REVIEW
branches), the `_ensure_question_version` session.get() fallback for a
version not present in the caller's preloaded_versions map, the
non-"x) text" alternative-line fallback, and both early-return branches of
get_classification_traceability.

Uses the same lightweight direct-ORM seeding style as
test_ingestion_classifier_n1.py (IngestionDocument/IngestionQuestion built
by hand) rather than the docx pilot material, since these scenarios need
precise control over taxonomy rows, alternative-line formatting and
question-version linkage that a real document doesn't exercise.
"""

from __future__ import annotations

import unittest
import uuid as _uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    IngestionDocument,
    IngestionQuestion,
    PedagogicalClassification,
    Question,
    QuestionOption,
    QuestionVersion,
    Taxonomy,
    TaxonomyNode,
)
from agente_ia_edu.services.ingestion_classifier import (
    DifficultyPolicy,
    IngestionClassificationService,
)
from agente_ia_edu.services.pedagogical_classifier import MockPedagogicalClassifierProvider


class TestDifficultyPolicyUnknownValue(unittest.TestCase):
    def test_unrecognized_ai_difficulty_raises(self):
        with self.assertRaises(ValueError):
            DifficultyPolicy.to_learning_level("SUPER_HARD")


class IngestionClassifierServiceCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_document(self, session: AsyncSession, tag: str) -> IngestionDocument:
        doc = IngestionDocument(
            filename=f"{tag}.pdf", document_type="PDF", document_hash=f"hash-{tag}-{_uuid.uuid4().hex}",
            storage_uri=f"var/{tag}.pdf", file_size_bytes=1, status="processed",
            ingested_by_external_identity="tester",
        )
        session.add(doc)
        await session.flush()
        return doc

    def _add_question(self, session: AsyncSession, doc: IngestionDocument, *, number: int,
                       statement: str = "Enunciado", alternatives: str | None = None) -> IngestionQuestion:
        iq = IngestionQuestion(
            document_id=doc.id, question_number=number, question_type="MULTIPLE_CHOICE",
            statement_text=f"{statement} {_uuid.uuid4().hex}",
            alternatives_text=alternatives,
            position=number, page_start=1, page_end=1, status="extracted", metadata_={},
        )
        session.add(iq)
        return iq

    # -- 101: document not found -------------------------------------------

    async def test_classify_document_questions_raises_for_unknown_document(self):
        async with self.factory() as session:
            svc = IngestionClassificationService(session)
            with self.assertRaises(ValueError):
                await svc.classify_document_questions(_uuid.uuid4(), MockPedagogicalClassifierProvider())

    # -- 105: document with zero questions ----------------------------------

    async def test_classify_document_questions_returns_empty_list_when_no_questions(self):
        async with self.factory() as session:
            doc = await self._seed_document(session, "empty")
            await session.commit()

            svc = IngestionClassificationService(session)
            result = await svc.classify_document_questions(doc.id, MockPedagogicalClassifierProvider())
            self.assertEqual(result, [])

    # -- 109-116 & 202-209: taxonomy_code validation -------------------------

    async def _seed_taxonomy(self, session: AsyncSession, *, code: str, node_code: str) -> None:
        taxonomy = Taxonomy(code=code, name="Test Taxonomy", version="v1", active=True)
        session.add(taxonomy)
        await session.flush()
        node = TaxonomyNode(taxonomy_id=taxonomy.id, code=node_code, name="Node", node_type="skill")
        session.add(node)
        await session.flush()

    async def test_taxonomy_code_with_matching_skill_builds_node_map_and_stays_classified(self):
        async with self.factory() as session:
            doc = await self._seed_document(session, "tax-match")
            self._add_question(session, doc, number=1)
            await self._seed_taxonomy(session, code="ENEM", node_code="H01")
            await session.commit()

            svc = IngestionClassificationService(session)
            provider = MockPedagogicalClassifierProvider(custom_responses={
                1: {
                    "discipline": "Ciências", "content": "Matéria", "subcontent": "Estrutura",
                    "difficulty": "MEDIUM", "difficulty_confidence": 0.9,
                    "reasoning_type": "conceitual", "classification_confidence": 0.9,
                    "status": "CLASSIFIED", "skills": ["h01"],
                },
            })
            results = await svc.classify_document_questions(doc.id, provider, taxonomy_code="ENEM")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "CLASSIFIED")

    async def test_taxonomy_code_forces_needs_review_when_no_skills_returned(self):
        async with self.factory() as session:
            doc = await self._seed_document(session, "tax-noskills")
            self._add_question(session, doc, number=1)
            await self._seed_taxonomy(session, code="ENEM2", node_code="H01")
            await session.commit()

            svc = IngestionClassificationService(session)
            provider = MockPedagogicalClassifierProvider(custom_responses={
                1: {
                    "discipline": "Ciências", "content": "Matéria", "subcontent": "Estrutura",
                    "difficulty": "MEDIUM", "difficulty_confidence": 0.9,
                    "reasoning_type": "conceitual", "classification_confidence": 0.9,
                    "status": "CLASSIFIED", "skills": [],
                },
            })
            results = await svc.classify_document_questions(doc.id, provider, taxonomy_code="ENEM2")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "NEEDS_REVIEW")

    async def test_taxonomy_code_forces_needs_review_when_skill_not_in_taxonomy(self):
        async with self.factory() as session:
            doc = await self._seed_document(session, "tax-mismatch")
            self._add_question(session, doc, number=1)
            await self._seed_taxonomy(session, code="ENEM3", node_code="H01")
            await session.commit()

            svc = IngestionClassificationService(session)
            provider = MockPedagogicalClassifierProvider(custom_responses={
                1: {
                    "discipline": "Ciências", "content": "Matéria", "subcontent": "Estrutura",
                    "difficulty": "MEDIUM", "difficulty_confidence": 0.9,
                    "reasoning_type": "conceitual", "classification_confidence": 0.9,
                    "status": "CLASSIFIED", "skills": ["H99-DOES-NOT-EXIST"],
                },
            })
            results = await svc.classify_document_questions(doc.id, provider, taxonomy_code="ENEM3")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "NEEDS_REVIEW")

    # -- 306: _ensure_question_version's session.get() fallback -------------

    async def test_ensure_question_version_falls_back_to_session_get_when_not_preloaded(self):
        """classify_document_questions always pre-populates preloaded_versions
        for every question_version_id it's about to touch, so this fallback
        never fires through the public entrypoint - calling the (module-
        private but not name-mangled) helper directly, the same way this
        method documents its own `preloaded_versions=None` standalone-call
        contract, is the only way to exercise it."""
        async with self.factory() as session:
            doc = await self._seed_document(session, "fallback")
            question = Question(validation_status="extracted")
            session.add(question)
            await session.flush()
            version = QuestionVersion(
                question_id=question.id, version_kind="official_original",
                canonical_text="Texto existente", content_hash="fallback-hash",
            )
            session.add(version)
            await session.flush()
            iq = self._add_question(session, doc, number=1)
            iq.question_version_id = version.id
            await session.commit()

            svc = IngestionClassificationService(session)
            # Empty map: forces the `qv is None` branch to hit session.get().
            result = await svc._ensure_question_version(iq, doc, preloaded_versions={})
            self.assertEqual(result.id, version.id)

    # -- 340-341: alternative line without a leading "x) " ------------------

    async def test_alternatives_without_letter_prefix_fall_back_to_positional_key(self):
        async with self.factory() as session:
            doc = await self._seed_document(session, "alt-fallback")
            iq = self._add_question(
                session, doc, number=1,
                alternatives=" primeira alternativa sem prefixo\nb) segunda com prefixo",
            )
            await session.commit()

            svc = IngestionClassificationService(session)
            results = await svc.classify_document_questions(doc.id, MockPedagogicalClassifierProvider())
            self.assertEqual(len(results), 1)

            version_id = results[0].question_version_id
            from sqlalchemy import select
            opts = (await session.execute(
                select(QuestionOption).where(QuestionOption.question_version_id == version_id)
                .order_by(QuestionOption.position)
            )).scalars().all()
            self.assertEqual(len(opts), 2)
            # No "x) " prefix matched -> key falls back to the 1-based position.
            self.assertEqual(opts[0].option_key, "1")
            self.assertEqual(opts[0].text, "primeira alternativa sem prefixo")
            # A properly "b) " prefixed line still parses normally.
            self.assertEqual(opts[1].option_key, "B")
            self.assertEqual(opts[1].text, "segunda com prefixo")

    # -- 437: get_classification_traceability, classification not found ----

    async def test_get_classification_traceability_returns_none_for_unknown_id(self):
        async with self.factory() as session:
            svc = IngestionClassificationService(session)
            result = await svc.get_classification_traceability(_uuid.uuid4())
            self.assertIsNone(result)

    # -- 445-448: fallback lookup of IngestionQuestion by question_version_id

    async def test_get_classification_traceability_falls_back_to_version_lookup(self):
        """When a PedagogicalClassification's metadata_ carries no
        ingestion_question_id (e.g. a classification created outside the
        classify_document_questions flow), get_classification_traceability
        falls back to finding the IngestionQuestion by question_version_id
        instead of returning a traceability record with no ingestion_question
        at all."""
        async with self.factory() as session:
            doc = await self._seed_document(session, "fallback-trace")
            question = Question(validation_status="extracted")
            session.add(question)
            await session.flush()
            version = QuestionVersion(
                question_id=question.id, version_kind="official_original",
                canonical_text="Texto", content_hash="trace-hash",
            )
            session.add(version)
            await session.flush()
            iq = self._add_question(session, doc, number=1)
            iq.question_version_id = version.id

            classification = PedagogicalClassification(
                question_version_id=version.id, discipline="D", content="C", subcontent="S",
                difficulty="EASY", reasoning_type="R", status="CLASSIFIED", source="ai",
                metadata_={},  # deliberately no ingestion_question_id
            )
            session.add(classification)
            await session.commit()

            svc = IngestionClassificationService(session)
            trace = await svc.get_classification_traceability(classification.id)
            self.assertIsNotNone(trace)
            self.assertEqual(trace["ingestion_question"]["id"], str(iq.id))
            self.assertEqual(trace["document"]["id"], str(doc.id))


if __name__ == "__main__":
    unittest.main()
