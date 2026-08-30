import asyncio
import unittest
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    IngestionDocument,
    IngestionQuestion,
    PedagogicalClassification,
    Question,
    QuestionVersion,
    Taxonomy,
    TaxonomyNode,
)
from agente_ia_edu.services.ingestion import IngestionService
from agente_ia_edu.services.ingestion_classifier import (
    DifficultyPolicy,
    IngestionClassificationService,
)
from agente_ia_edu.services.pedagogical_classifier import MockPedagogicalClassifierProvider


class TestIngestionClassifierIntegration(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.pilot_material = Path(
            "/Users/marcoviana/agente-ia-edu-core/tests/fixtures/ingestion_materials/"
            "T 01 PRINCÍPIOS ELEMENTARES DA MATÉRIA3.docx"
        )
        assert cls.pilot_material.exists(), f"Pilot material not found at {cls.pilot_material}"

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=False,
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _ingest_pilot(self, session: AsyncSession) -> IngestionDocument:
        service = IngestionService()
        doc, _ = await service.ingest_document(session, self.pilot_material)
        return doc

    async def test_01_document_with_15_questions(self):
        """1. documento com 15 questões"""
        async with self.session_factory() as session:
            doc = await self._ingest_pilot(session)
            stmt = select(IngestionQuestion).where(IngestionQuestion.document_id == doc.id)
            res = await session.execute(stmt)
            questions = res.scalars().all()
            self.assertEqual(len(questions), 15)

    async def test_02_all_15_questions_classified(self):
        """2. todas as 15 questões classificadas"""
        async with self.session_factory() as session:
            doc = await self._ingest_pilot(session)
            service = IngestionClassificationService(session)
            provider = MockPedagogicalClassifierProvider()
            classifications = await service.classify_document_questions(
                doc.id,
                provider,
                model_name="gpt-4o",
                model_version="2024-05-13",
                prompt_version="v1.0",
            )
            self.assertEqual(len(classifications), 15)
            for c in classifications:
                self.assertEqual(c.status, "CLASSIFIED")
                self.assertEqual(c.discipline, "Ciências")

    async def test_03_preservation_of_original_content(self):
        """3. preservação do conteúdo original"""
        async with self.session_factory() as session:
            doc = await self._ingest_pilot(session)
            stmt = select(IngestionQuestion).where(IngestionQuestion.document_id == doc.id)
            res = await session.execute(stmt)
            q_before = {q.id: (q.statement_text, q.alternatives_text, q.correct_answer) for q in res.scalars().all()}

            service = IngestionClassificationService(session)
            await service.classify_document_questions(doc.id, MockPedagogicalClassifierProvider())

            res_after = await session.execute(stmt)
            q_after = {q.id: (q.statement_text, q.alternatives_text, q.correct_answer) for q in res_after.scalars().all()}

            self.assertEqual(q_before, q_after)

    async def test_04_traceability(self):
        """4. rastreabilidade bidirecional"""
        async with self.session_factory() as session:
            doc = await self._ingest_pilot(session)
            service = IngestionClassificationService(session)
            classifications = await service.classify_document_questions(doc.id, MockPedagogicalClassifierProvider())

            # doc -> question -> version -> classification
            doc_trace = await service.get_document_traceability(doc.id)
            self.assertEqual(len(doc_trace), 15)
            self.assertEqual(doc_trace[0]["document"]["filename"], "T 01 PRINCÍPIOS ELEMENTARES DA MATÉRIA3.docx")
            self.assertIsNotNone(doc_trace[0]["classification"]["id"])

            # classification -> question -> document -> section
            cid = classifications[0].id
            class_trace = await service.get_classification_traceability(cid)
            self.assertIsNotNone(class_trace)
            self.assertEqual(class_trace["document"]["id"], str(doc.id))
            self.assertEqual(class_trace["document"]["filename"], "T 01 PRINCÍPIOS ELEMENTARES DA MATÉRIA3.docx")

    async def test_05_duplicate_classification_prevention(self):
        """5. classificação duplicada evitada se reprocess=False"""
        async with self.session_factory() as session:
            doc = await self._ingest_pilot(session)
            service = IngestionClassificationService(session)
            provider = MockPedagogicalClassifierProvider()

            first_run = await service.classify_document_questions(doc.id, provider, reprocess=False)
            second_run = await service.classify_document_questions(doc.id, provider, reprocess=False)

            self.assertEqual(len(first_run), 15)
            self.assertEqual(len(second_run), 15)
            self.assertEqual([c.id for c in first_run], [c.id for c in second_run])

            stmt = select(PedagogicalClassification)
            res = await session.execute(stmt)
            total_db_records = res.scalars().all()
            self.assertEqual(len(total_db_records), 15)

    async def test_06_reprocessing(self):
        """6. reprocessamento gera novas versões quando reprocess=True"""
        async with self.session_factory() as session:
            doc = await self._ingest_pilot(session)
            service = IngestionClassificationService(session)
            provider = MockPedagogicalClassifierProvider()

            first_run = await service.classify_document_questions(doc.id, provider, reprocess=False)
            second_run = await service.classify_document_questions(doc.id, provider, reprocess=True)

            self.assertEqual(len(first_run), 15)
            self.assertEqual(len(second_run), 15)
            self.assertNotEqual([c.id for c in first_run], [c.id for c in second_run])

            stmt = select(PedagogicalClassification)
            res = await session.execute(stmt)
            total_db_records = res.scalars().all()
            self.assertEqual(len(total_db_records), 30)

    async def test_07_single_question_failure_does_not_fail_batch(self):
        """7. falha de uma questão sem perder as demais"""
        async with self.session_factory() as session:
            doc = await self._ingest_pilot(session)
            service = IngestionClassificationService(session)
            provider = MockPedagogicalClassifierProvider(failing_question_numbers=[3])

            classifications = await service.classify_document_questions(doc.id, provider)
            # Question 3 failed, 14 succeeded
            self.assertEqual(len(classifications), 14)

    async def test_08_classification_needs_review_status(self):
        """8. classificação NEEDS_REVIEW"""
        async with self.session_factory() as session:
            doc = await self._ingest_pilot(session)
            service = IngestionClassificationService(session)
            provider = MockPedagogicalClassifierProvider(default_status="NEEDS_REVIEW")

            classifications = await service.classify_document_questions(doc.id, provider)
            for c in classifications:
                self.assertEqual(c.status, "NEEDS_REVIEW")

    async def test_09_confidence_below_threshold(self):
        """9. confidence abaixo do limite torna status NEEDS_REVIEW"""
        async with self.session_factory() as session:
            doc = await self._ingest_pilot(session)
            service = IngestionClassificationService(session, confidence_threshold=Decimal("0.80"))
            provider = MockPedagogicalClassifierProvider(default_confidence=0.65)

            classifications = await service.classify_document_questions(doc.id, provider)
            for c in classifications:
                self.assertEqual(c.status, "NEEDS_REVIEW")

    async def test_10_difficulty_ai_to_learning_level(self):
        """10. difficulty AI -> learning level policy"""
        self.assertEqual(DifficultyPolicy.to_learning_level("VERY_EASY"), "EASY")
        self.assertEqual(DifficultyPolicy.to_learning_level("EASY"), "EASY")
        self.assertEqual(DifficultyPolicy.to_learning_level("MEDIUM"), "MEDIUM")
        self.assertEqual(DifficultyPolicy.to_learning_level("HARD"), "HARD")
        self.assertEqual(DifficultyPolicy.to_learning_level("VERY_HARD"), "HARD")

        async with self.session_factory() as session:
            doc = await self._ingest_pilot(session)
            service = IngestionClassificationService(session)
            provider = MockPedagogicalClassifierProvider(default_difficulty="VERY_HARD")

            classifications = await service.classify_document_questions(doc.id, provider)
            self.assertEqual(classifications[0].difficulty, "VERY_HARD")
            self.assertEqual(classifications[0].metadata_["difficulty_learning_level"], "HARD")

            qv = await session.get(QuestionVersion, classifications[0].question_version_id)
            self.assertEqual(qv.recommended_difficulty, "HARD")

    async def test_11_tokens_tracking(self):
        """11. tokens"""
        async with self.session_factory() as session:
            doc = await self._ingest_pilot(session)
            service = IngestionClassificationService(session)
            classifications = await service.classify_document_questions(doc.id, MockPedagogicalClassifierProvider())

            for c in classifications:
                self.assertEqual(c.input_tokens, 10)
                self.assertEqual(c.output_tokens, 8)
                self.assertEqual(c.total_tokens, 18)

    async def test_12_provider_info(self):
        """12. provider"""
        async with self.session_factory() as session:
            doc = await self._ingest_pilot(session)
            service = IngestionClassificationService(session)
            classifications = await service.classify_document_questions(
                doc.id,
                MockPedagogicalClassifierProvider(),
                model_name="claude-3-5-sonnet",
                model_version="20241022",
                prompt_version="ped-v2",
            )
            self.assertEqual(classifications[0].model_name, "claude-3-5-sonnet")
            self.assertEqual(classifications[0].model_version, "20241022")
            self.assertEqual(classifications[0].prompt_version, "ped-v2")
            self.assertEqual(classifications[0].provider_name, "mock")

    async def test_13_isolation_between_documents(self):
        """13. isolamento entre documentos"""
        async with self.session_factory() as session:
            doc1 = await self._ingest_pilot(session)
            doc2 = await self._ingest_pilot(session)
            self.assertNotEqual(doc1.id, doc2.id)

            service = IngestionClassificationService(session)
            class1 = await service.classify_document_questions(doc1.id, MockPedagogicalClassifierProvider())
            class2 = await service.classify_document_questions(doc2.id, MockPedagogicalClassifierProvider())

            q_ids1 = {c.question_version_id for c in class1}
            q_ids2 = {c.question_version_id for c in class2}
            self.assertTrue(q_ids1.isdisjoint(q_ids2))

    async def test_14_isolation_between_question_versions(self):
        """14. isolamento entre versões de questão"""
        async with self.session_factory() as session:
            q1 = Question(validation_status="approved")
            q2 = Question(validation_status="approved")
            session.add_all([q1, q2])
            await session.flush()

            v1 = QuestionVersion(question_id=q1.id, version_kind="official_original", canonical_text="v1 text", content_hash="h1")
            v2 = QuestionVersion(question_id=q2.id, version_kind="official_original", canonical_text="v2 text", content_hash="h2")
            session.add_all([v1, v2])
            await session.commit()

            c1 = PedagogicalClassification(
                question_version_id=v1.id,
                discipline="D",
                content="C",
                subcontent="S",
                difficulty="EASY",
                reasoning_type="R",
                status="CLASSIFIED",
                source="ai",
            )
            c2 = PedagogicalClassification(
                question_version_id=v2.id,
                discipline="D",
                content="C",
                subcontent="S",
                difficulty="HARD",
                reasoning_type="R",
                status="CLASSIFIED",
                source="ai",
            )
            session.add_all([c1, c2])
            await session.commit()

            res1 = await session.execute(
                select(PedagogicalClassification).where(PedagogicalClassification.question_version_id == v1.id)
            )
            res2 = await session.execute(
                select(PedagogicalClassification).where(PedagogicalClassification.question_version_id == v2.id)
            )
            self.assertEqual(res1.scalar_one().difficulty, "EASY")
            self.assertEqual(res2.scalar_one().difficulty, "HARD")

    async def test_15_mock_provider_determinism(self):
        """15. determinismo do MockProvider"""
        provider = MockPedagogicalClassifierProvider()
        res1 = await provider.classify(question_text="Texto", question_number=1)
        res2 = await provider.classify(question_text="Texto", question_number=1)
        self.assertEqual(res1, res2)


if __name__ == "__main__":
    unittest.main()
