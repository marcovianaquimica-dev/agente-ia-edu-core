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


class TestPedagogicalClassifierUncoveredBranches(unittest.IsolatedAsyncioTestCase):
    """Closes the remaining coverage gaps in services/pedagogical_classifier.py:
    MockPedagogicalClassifierProvider's custom_responses branch (only
    reachable when a caller passes question_number - the service's own
    classify_question never does, but ingestion_classifier.py's
    classify_document_questions does), the canonical_text-blank/statement
    fallback, _coerce_payload's JSON-string and invalid-type branches, and
    _validate_payload's empty-string-field and string-confidence-coercion
    branches.

    Unlike the sibling TestPedagogicalClassifier class above (which opts out
    with expire_on_commit=False), this class deliberately leaves
    expire_on_commit at SQLAlchemy's default (True) - the same default
    production's create_session_factory() uses with no override (see
    db/session.py) - so a MissingGreenlet-shaped bug in post-commit
    attribute access would actually surface here instead of being masked.
    """

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=False,
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _create_question(self, canonical_text: str = "", statement: str | None = None):
        async with self.session_factory() as session:
            question = Question(validation_status="approved")
            session.add(question)
            await session.flush()
            version = QuestionVersion(
                question_id=question.id,
                version_kind="official_original",
                canonical_text=canonical_text,
                statement=statement,
                content_hash=f"hash-{uuid4()}".replace("-", "")[:128],
                recommended_difficulty=None,
                metadata_={"source": "pilot"},
            )
            session.add(version)
            await session.flush()
            # Captured before commit: with expire_on_commit at its default
            # (True, matching production - see this class's docstring),
            # `version.id` would be expired post-commit, and reading it back
            # outside the greenlet-bridged awaited context raises
            # MissingGreenlet. flush() already assigns the PK, so there's no
            # need to touch the attribute again after commit.
            version_id = version.id
            await session.commit()
            return version_id

    # --- MockPedagogicalClassifierProvider.custom_responses branch ---

    async def test_mock_provider_returns_custom_response_for_matching_question_number(self):
        provider = MockPedagogicalClassifierProvider(custom_responses={
            7: {
                "discipline": "Física", "content": "Cinemática", "subcontent": "MRU",
                "difficulty": "EASY", "difficulty_confidence": 0.5, "reasoning_type": "aplicacao",
                "classification_confidence": 0.6, "status": "CLASSIFIED",
            }
        })
        result = await provider.classify(question_text="x", question_number=7)
        self.assertEqual(result["discipline"], "Física")
        # setdefault-filled bookkeeping fields, since the custom payload above
        # doesn't specify them.
        self.assertEqual(result["provider"], "mock")
        self.assertEqual(result["model_name"], "mock-model")
        self.assertEqual(result["total_tokens"], 18)

    async def test_mock_provider_custom_response_setdefault_keeps_explicit_overrides(self):
        provider = MockPedagogicalClassifierProvider(custom_responses={
            9: {
                "discipline": "Biologia", "content": "Genética", "subcontent": "Mendel",
                "difficulty": "HARD", "difficulty_confidence": 0.9, "reasoning_type": "aplicacao",
                "classification_confidence": 0.95, "status": "NEEDS_REVIEW",
                "provider": "custom-provider", "total_tokens": 999,
            }
        })
        result = await provider.classify(question_text="x", question_number=9, model_name="real-model")
        # Keys already present in the custom payload are NOT overwritten...
        self.assertEqual(result["provider"], "custom-provider")
        self.assertEqual(result["total_tokens"], 999)
        # ...but a key absent from it still falls back to the call's own
        # model_name argument (setdefault semantics).
        self.assertEqual(result["model_name"], "real-model")

    async def test_mock_provider_custom_response_ignored_for_unmatched_question_number(self):
        provider = MockPedagogicalClassifierProvider(custom_responses={7: {"discipline": "Física"}})
        result = await provider.classify(question_text="x", question_number=99)
        self.assertEqual(result["discipline"], "Ciências")  # falls through to the default response

    # --- canonical_text blank -> statement fallback ---

    async def test_classify_question_falls_back_to_statement_when_canonical_text_is_blank(self):
        version_id = await self._create_question(canonical_text="   ", statement="Qual a fórmula da água?")
        async with self.session_factory() as session:
            service = PedagogicalClassificationService(session)
            result = await service.classify_question(
                question_version_id=version_id,
                provider=MockPedagogicalClassifierProvider(),
                model_name="mock-model",
                model_version="v1",
                prompt_version="prompt-v1",
            )
            self.assertEqual(result.status, "CLASSIFIED")
            self.assertEqual(result.discipline, "Ciências")

    # --- _coerce_payload: JSON-string and invalid-type branches ---

    def test_coerce_payload_parses_json_string(self):
        payload = PedagogicalClassificationService._coerce_payload('{"discipline": "Química"}')
        self.assertEqual(payload, {"discipline": "Química"})

    def test_coerce_payload_rejects_invalid_json_string(self):
        with self.assertRaises(ValueError):
            PedagogicalClassificationService._coerce_payload("not json at all {")

    def test_coerce_payload_rejects_json_string_that_is_not_an_object(self):
        with self.assertRaises(ValueError):
            PedagogicalClassificationService._coerce_payload("[1, 2, 3]")

    def test_coerce_payload_rejects_non_str_non_dict_payload(self):
        with self.assertRaises(ValueError):
            PedagogicalClassificationService._coerce_payload(12345)

    # --- _validate_payload: empty/blank required text field ---

    def test_validate_payload_rejects_blank_required_text_field(self):
        with self.assertRaises(ValueError) as ctx:
            PedagogicalClassificationService._validate_payload({
                "discipline": "   ",
                "content": "Matéria",
                "subcontent": "Átomos",
                "difficulty": "MEDIUM",
                "difficulty_confidence": 0.7,
                "reasoning_type": "conceitual",
                "classification_confidence": 0.8,
                "status": "CLASSIFIED",
            })
        self.assertIn("discipline", str(ctx.exception))

    # --- _validate_payload: confidence provided as a numeric string ---

    def test_validate_payload_coerces_string_confidence_values(self):
        result = PedagogicalClassificationService._validate_payload({
            "discipline": "Química", "content": "Matéria", "subcontent": "Átomos",
            "difficulty": "medium", "difficulty_confidence": "0.7",
            "reasoning_type": "conceitual", "classification_confidence": "0.8",
            "status": "classified",
        })
        self.assertEqual(result["difficulty_confidence"], 0.7)
        self.assertEqual(result["classification_confidence"], 0.8)
        self.assertEqual(result["difficulty"], "MEDIUM")
        self.assertEqual(result["status"], "CLASSIFIED")

    def test_validate_payload_rejects_non_numeric_string_confidence(self):
        with self.assertRaises(ValueError):
            PedagogicalClassificationService._validate_payload({
                "discipline": "Química", "content": "Matéria", "subcontent": "Átomos",
                "difficulty": "MEDIUM", "difficulty_confidence": "not-a-number",
                "reasoning_type": "conceitual", "classification_confidence": 0.8,
                "status": "CLASSIFIED",
            })


if __name__ == "__main__":
    unittest.main()
