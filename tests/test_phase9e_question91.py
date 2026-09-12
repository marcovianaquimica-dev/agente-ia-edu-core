import importlib.util
import io
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    CatalogNode,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    Question,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
)
from agente_ia_edu.providers.errors import (
    AllProvidersFailedError,
    ProviderAttempt,
    ProviderUnavailableError,
)


SCRIPT_PATH = Path(__file__).parent / "manual" / "phase9e_question91.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("phase9e_question91", SCRIPT_PATH)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
phase9e_question91 = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(phase9e_question91)


class Phase9EQuestion91Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    def test_loads_required_configuration_without_exposing_secrets(self):
        database_url = "postgresql+psycopg://user:secret@localhost/education"
        api_key = "sk-secret-value"
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "\n".join(
                    (
                        f"DATABASE_URL={database_url}",
                        f"OPENAI_API_KEY={api_key}",
                        "OPENAI_MODEL=gpt-5.6-luna",
                    )
                ),
                encoding="ascii",
            )
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), patch("sys.stdout", output):
                model = phase9e_question91.load_required_configuration(env_file)

        self.assertEqual(model, "gpt-5.6-luna")
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn(database_url, output.getvalue())
        self.assertNotIn(api_key, output.getvalue())

    def test_diagnostic_error_redacts_database_urls_and_api_keys(self):
        try:
            raise ValueError(
                "Failed for postgresql+psycopg://user:secret@host/db with sk-secret-value"
            )
        except ValueError as exc:
            diagnostic = phase9e_question91.format_diagnostic_error(exc)

        self.assertIn("TIPO: ValueError", diagnostic)
        self.assertIn("MENSAGEM:", diagnostic)
        self.assertIn("LOCAL:", diagnostic)
        self.assertNotIn("postgresql+psycopg://", diagnostic)
        self.assertNotIn("sk-secret-value", diagnostic)

    def test_diagnostic_error_displays_provider_details_when_present(self):
        error = AllProvidersFailedError(
            [
                ProviderAttempt(
                    provider="openai",
                    error_type="ProviderUnavailableError",
                    original_error_type="APIConnectionError",
                    diagnostic_message=(
                        "connection failed for sk-secret-value at "
                        "postgresql://user:secret@host/db"
                    ),
                ),
                ProviderAttempt(
                    provider="other",
                    error_type="ProviderTimeoutError",
                    original_error_type="TimeoutError",
                    diagnostic_message="must not be selected",
                ),
            ]
        )
        try:
            raise error
        except AllProvidersFailedError as exc:
            diagnostic = phase9e_question91.format_diagnostic_error(exc)

        self.assertIn("TIPO: AllProvidersFailedError", diagnostic)
        self.assertIn("MENSAGEM: All providers failed: openai: ProviderUnavailableError", diagnostic)
        self.assertIn("ORIGINAL_ERROR_TYPE: APIConnectionError", diagnostic)
        self.assertIn("DIAGNOSTIC_MESSAGE:", diagnostic)
        self.assertNotIn("must not be selected", diagnostic)
        self.assertNotIn("sk-secret-value", diagnostic)
        self.assertNotIn("postgresql://user:secret@host/db", diagnostic)

    def test_diagnostic_error_handles_empty_attempts(self):
        try:
            raise AllProvidersFailedError([])
        except AllProvidersFailedError as exc:
            diagnostic = phase9e_question91.format_diagnostic_error(exc)

        self.assertIn("ORIGINAL_ERROR_TYPE: N/A", diagnostic)
        self.assertIn("DIAGNOSTIC_MESSAGE: N/A", diagnostic)

    def test_diagnostic_error_handles_attempt_without_optional_fields(self):
        try:
            raise AllProvidersFailedError(
                [ProviderAttempt("openai", "ProviderUnavailableError")]
            )
        except AllProvidersFailedError as exc:
            diagnostic = phase9e_question91.format_diagnostic_error(exc)

        self.assertIn("ORIGINAL_ERROR_TYPE: None", diagnostic)
        self.assertIn("DIAGNOSTIC_MESSAGE: None", diagnostic)

    async def _seed_question91(self, session):
        institution = Institution(code="INEP", name="INEP")
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
        source_document = SourceDocument(
            exam_application_id=application.id,
            exam_booklet_id=booklet.id,
            document_type="ANSWER_KEY",
            source_url="https://example.test/gabarito.pdf",
            acquired_at=datetime.now(timezone.utc),
            content_hash="answer-key-source",
        )
        session.add(source_document)
        await session.flush()
        revision = AnswerKeyRevision(source_document_id=source_document.id, revision_number=1)
        session.add(revision)
        question = Question(
            validation_status="validated",
            origin_type="IMPORTED",
            status="PUBLISHED",
            visibility_scope="PUBLIC",
        )
        session.add(question)
        await session.flush()
        version = QuestionVersion(
            question_id=question.id,
            version_kind="official_original",
            canonical_text="Texto integral da questão oficial 91.",
            content_hash="question-91",
        )
        session.add(version)
        await session.flush()
        booklet_question = BookletQuestion(
            exam_booklet_id=booklet.id,
            question_version_id=version.id,
            position=91,
            official_number=91,
        )
        session.add(booklet_question)
        await session.flush()
        session.add_all(
            [
                QuestionOption(
                    question_version_id=version.id,
                    option_key=key,
                    position=position,
                    text=f"Alternativa {key}",
                )
                for position, key in enumerate("ABCDE", start=1)
            ]
        )
        session.add(
            AnswerKeyEntry(
                answer_key_revision_id=revision.id,
                booklet_question_id=booklet_question.id,
                official_answer_label="C",
            )
        )
        for position in range(17):
            session.add(
                CatalogNode(
                    code=f"NODE-{position:02d}",
                    name=f"Node {position}",
                    node_type="CONTENT",
                    position=position,
                )
            )
        await session.commit()
        return version

    async def test_builds_a_deterministic_payload_for_only_question_91(self):
        async with self.factory() as session:
            version = await self._seed_question91(session)
            payload = await phase9e_question91.build_question91_input(session)

        self.assertEqual(payload["question_version_id"], str(version.id))
        self.assertEqual(payload["official_number"], 91)
        self.assertEqual([item["key"] for item in payload["alternatives"]], list("ABCDE"))
        self.assertEqual(payload["official_answer"], "C")
        self.assertEqual(len(payload["authorized_catalog_nodes"]), 17)
        self.assertEqual(
            phase9e_question91.serialize_canonical_payload(payload),
            phase9e_question91.serialize_canonical_payload(payload),
        )

    async def test_rejects_any_non_91_question_from_the_payload(self):
        async with self.factory() as session:
            await self._seed_question91(session)
            booklet_question = await session.get(
                BookletQuestion,
                (await session.execute(select(BookletQuestion.id))).scalar_one(),
            )
            booklet_question.official_number = 90
            await session.commit()
            with self.assertRaisesRegex(RuntimeError, "exactly one official"):
                await phase9e_question91.build_question91_input(session)