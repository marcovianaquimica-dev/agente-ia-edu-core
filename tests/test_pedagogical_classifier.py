import asyncio
import unittest
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    PedagogicalClassification,
    Question,
    QuestionVersion,
)
from agente_ia_edu.services.pedagogical_classifier import (
    MockPedagogicalClassifierProvider,
    PedagogicalClassificationService,
)


class TestPedagogicalClassifier(unittest.IsolatedAsyncioTestCase):
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

    async def _create_question(self, text: str = "Qual é a unidade fundamental da matéria?") -> QuestionVersion:
        async with self.session_factory() as session:
            question = Question(validation_status="approved")
            session.add(question)
            await session.flush()
            version = QuestionVersion(
                question_id=question.id,
                version_kind="official_original",
                canonical_text=text,
                statement=text,
                content_hash=f"hash-{uuid4()}".replace("-", "")[:128],
                recommended_difficulty=None,
                metadata_={"source": "pilot"},
            )
            session.add(version)
            await session.flush()
            await session.commit()
            return version

    async def test_valid_classification(self):
        question_version = await self._create_question()
        async with self.session_factory() as session:
            provider = MockPedagogicalClassifierProvider()
            service = PedagogicalClassificationService(session)
            result = await service.classify_question(
                question_version_id=question_version.id,
                provider=provider,
                model_name="mock-model",
                model_version="v1",
                prompt_version="prompt-v1",
            )
            self.assertEqual(result.status, "CLASSIFIED")
            self.assertEqual(result.difficulty, "MEDIUM")
            self.assertGreaterEqual(float(result.classification_confidence), 0.0)
            self.assertLessEqual(float(result.classification_confidence), 1.0)

    async def test_invalid_json_response(self):
        async with self.session_factory() as session:
            question_version = await self._create_question()
            class BadProvider:
                async def classify(self, *args, **kwargs):
                    return {"discipline": 123}

            service = PedagogicalClassificationService(session)
            with self.assertRaises(ValueError):
                await service.classify_question(
                    question_version_id=question_version.id,
                    provider=BadProvider(),
                    model_name="bad-provider",
                    model_version="v1",
                    prompt_version="prompt-v1",
                )

    async def test_invalid_difficulty(self):
        async with self.session_factory() as session:
            question_version = await self._create_question()
            provider = MockPedagogicalClassifierProvider()
            service = PedagogicalClassificationService(session)
            with self.assertRaises(ValueError):
                service._validate_payload({
                    "discipline": "Química",
                    "content": "Matéria",
                    "subcontent": "Átomos",
                    "difficulty": "EXTREME",
                    "difficulty_confidence": 0.7,
                    "reasoning_type": "conceitual",
                    "prerequisites": [],
                    "keywords": ["matéria"],
                    "competencies": [],
                    "skills": [],
                    "classification_confidence": 0.8,
                    "status": "CLASSIFIED",
                })

    async def test_confidence_out_of_range(self):
        async with self.session_factory() as session:
            question_version = await self._create_question()
            provider = MockPedagogicalClassifierProvider()
            service = PedagogicalClassificationService(session)
            with self.assertRaises(ValueError):
                service._validate_payload({
                    "discipline": "Química",
                    "content": "Matéria",
                    "subcontent": "Átomos",
                    "difficulty": "MEDIUM",
                    "difficulty_confidence": 1.2,
                    "reasoning_type": "conceitual",
                    "prerequisites": [],
                    "keywords": ["matéria"],
                    "competencies": [],
                    "skills": [],
                    "classification_confidence": 0.8,
                    "status": "CLASSIFIED",
                })

    async def test_invalid_status(self):
        async with self.session_factory() as session:
            question_version = await self._create_question()
            service = PedagogicalClassificationService(session)
            with self.assertRaises(ValueError):
                service._validate_payload({
                    "discipline": "Química",
                    "content": "Matéria",
                    "subcontent": "Átomos",
                    "difficulty": "MEDIUM",
                    "difficulty_confidence": 0.7,
                    "reasoning_type": "conceitual",
                    "prerequisites": [],
                    "keywords": ["matéria"],
                    "competencies": [],
                    "skills": [],
                    "classification_confidence": 0.8,
                    "status": "INVALID",
                })

    async def test_question_not_found(self):
        async with self.session_factory() as session:
            service = PedagogicalClassificationService(session)
            with self.assertRaises(ValueError):
                await service.classify_question(
                    question_version_id=uuid4(),
                    provider=MockPedagogicalClassifierProvider(),
                    model_name="mock-model",
                    model_version="v1",
                    prompt_version="prompt-v1",
                )

    async def test_question_without_content(self):
        async with self.session_factory() as session:
            question = Question(validation_status="approved")
            session.add(question)
            await session.flush()
            version = QuestionVersion(
                question_id=question.id,
                version_kind="official_original",
                canonical_text="",
                statement=None,
                content_hash=f"hash-{uuid4()}".replace("-", "")[:128],
                recommended_difficulty=None,
            )
            session.add(version)
            await session.flush()
            await session.commit()

            with self.assertRaises(ValueError):
                await PedagogicalClassificationService(session).classify_question(
                    question_version_id=version.id,
                    provider=MockPedagogicalClassifierProvider(),
                    model_name="mock-model",
                    model_version="v1",
                    prompt_version="prompt-v1",
                )

    async def test_needs_review_status(self):
        async with self.session_factory() as session:
            question_version = await self._create_question()
            provider = MockPedagogicalClassifierProvider()
            service = PedagogicalClassificationService(session)
            result = await service.classify_question(
                question_version_id=question_version.id,
                provider=provider,
                model_name="mock-model",
                model_version="v1",
                prompt_version="prompt-v1",
                force_review=True,
            )
            self.assertEqual(result.status, "NEEDS_REVIEW")

    async def test_reprocessing_and_versioning(self):
        async with self.session_factory() as session:
            question_version = await self._create_question()
            service = PedagogicalClassificationService(session)
            first = await service.classify_question(
                question_version_id=question_version.id,
                provider=MockPedagogicalClassifierProvider(),
                model_name="mock-model",
                model_version="v1",
                prompt_version="prompt-v1",
            )
            second = await service.classify_question(
                question_version_id=question_version.id,
                provider=MockPedagogicalClassifierProvider(),
                model_name="mock-model",
                model_version="v1",
                prompt_version="prompt-v1",
            )
            self.assertEqual(first.question_version_id, second.question_version_id)
            self.assertNotEqual(first.id, second.id)

    async def test_preserves_original_question(self):
        async with self.session_factory() as session:
            question_version = await self._create_question(text="Original text remains intact")
            before = question_version.statement
            service = PedagogicalClassificationService(session)
            await service.classify_question(
                question_version_id=question_version.id,
                provider=MockPedagogicalClassifierProvider(),
                model_name="mock-model",
                model_version="v1",
                prompt_version="prompt-v1",
            )
            refreshed = await session.get(QuestionVersion, question_version.id)
            self.assertEqual(refreshed.statement, before)

    async def test_provider_mock_is_used(self):
        async with self.session_factory() as session:
            question_version = await self._create_question()
            provider = MockPedagogicalClassifierProvider()
            service = PedagogicalClassificationService(session)
            result = await service.classify_question(
                question_version_id=question_version.id,
                provider=provider,
                model_name="mock-model",
                model_version="v1",
                prompt_version="prompt-v1",
            )
            self.assertEqual(result.model_name, "mock-model")
            self.assertEqual(result.model_version, "v1")
            self.assertEqual(result.total_tokens, 18)

    async def test_token_recording(self):
        async with self.session_factory() as session:
            question_version = await self._create_question()
            provider = MockPedagogicalClassifierProvider()
            service = PedagogicalClassificationService(session)
            result = await service.classify_question(
                question_version_id=question_version.id,
                provider=provider,
                model_name="mock-model",
                model_version="v1",
                prompt_version="prompt-v1",
            )
            self.assertGreaterEqual(result.input_tokens, 0)
            self.assertGreaterEqual(result.output_tokens, 0)
            self.assertGreaterEqual(result.total_tokens, 0)

    async def test_no_duplicate_records_without_need(self):
        async with self.session_factory() as session:
            question_version = await self._create_question()
            service = PedagogicalClassificationService(session)
            await service.classify_question(
                question_version_id=question_version.id,
                provider=MockPedagogicalClassifierProvider(),
                model_name="mock-model",
                model_version="v1",
                prompt_version="prompt-v1",
            )
            classifications = await service.list_classifications(question_version.id)
            self.assertEqual(len(classifications), 1)

    async def test_isolation_between_question_versions(self):
        async with self.session_factory() as session:
            q1 = await self._create_question("Primeira questão")
            q2 = await self._create_question("Segunda questão")
            service = PedagogicalClassificationService(session)
            await service.classify_question(q1.id, provider=MockPedagogicalClassifierProvider(), model_name="mock-model", model_version="v1", prompt_version="prompt-v1")
            await service.classify_question(q2.id, provider=MockPedagogicalClassifierProvider(), model_name="mock-model", model_version="v1", prompt_version="prompt-v1")
            classifications_q1 = await service.list_classifications(q1.id)
            classifications_q2 = await service.list_classifications(q2.id)
            self.assertEqual(len(classifications_q1), 1)
            self.assertEqual(len(classifications_q2), 1)


if __name__ == "__main__":
    unittest.main()
