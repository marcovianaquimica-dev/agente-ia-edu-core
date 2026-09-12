"""Build the Phase 9E canonical input for the ENEM 2020 question 91 pilot.

This script deliberately does not call OpenAI or persist classifications.
"""

import asyncio
import json
import os
import re
import traceback
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import func, select

from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    BookletQuestion,
    CatalogNode,
    ContentQuestionLink,
    Exam,
    ExamApplication,
    ExamBooklet,
    PedagogicalClassification,
    QuestionOption,
    QuestionVersion,
)
from agente_ia_edu.db.session import create_engine, create_session_factory
from agente_ia_edu.providers.adapters.openai import OpenAIProvider
from agente_ia_edu.providers.errors import AllProvidersFailedError
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.providers.router import ProviderRouter
from agente_ia_edu.services.curriculum_classification import ClassificationProposalService


TARGET_QUESTION_NUMBER = 91
MAX_OPENAI_CALLS = 1
CLASSIFIER_VERSION = "phase9e-question91-v1"
PROMPT_VERSION = "phase9e-question91-prompt-v1"
TAXONOMY_VERSION = "023_curriculum_taxonomy"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_required_configuration(env_file: Path = ENV_FILE) -> str:
    """Load local configuration without exposing any secret values."""
    load_dotenv(dotenv_path=env_file, override=False)
    database_configured = bool(os.getenv("DATABASE_URL"))
    api_key_configured = bool(os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL")
    if not database_configured or not api_key_configured or not model:
        raise RuntimeError("Required Phase 9E configuration is unavailable.")
    return model


def format_diagnostic_error(exc: Exception) -> str:
    """Format a useful error location while redacting likely secret values."""
    message = _sanitize_diagnostic_text(str(exc))
    diagnostic_source = exc
    if isinstance(exc, AllProvidersFailedError) and exc.attempts:
        diagnostic_source = exc.attempts[0]
    original_error_type = str(getattr(diagnostic_source, "original_error_type", "N/A"))
    provider_message = _sanitize_diagnostic_text(
        str(getattr(diagnostic_source, "diagnostic_message", "N/A"))
    )
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return (
            f"TIPO: {type(exc).__name__}\n"
            f"MENSAGEM: {message}\n"
            f"ORIGINAL_ERROR_TYPE: {original_error_type}\n"
            f"DIAGNOSTIC_MESSAGE: {provider_message}"
        )
    frame = frames[-1]
    return (
        f"TIPO: {type(exc).__name__}\n"
        f"MENSAGEM: {message}\n"
        f"ORIGINAL_ERROR_TYPE: {original_error_type}\n"
        f"DIAGNOSTIC_MESSAGE: {provider_message}\n"
        f"LOCAL: {frame.filename}:{frame.lineno} ({frame.name})"
    )


def _sanitize_diagnostic_text(value: str) -> str:
    value = re.sub(r"postgres(?:ql)?[^\s]*", "[REDACTED]", value, flags=re.I)
    return re.sub(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+", "[REDACTED]", value)


class SingleCallOpenAIProvider:
    """Permit exactly one provider invocation for this manual pilot."""

    provider = "openai"

    def __init__(self) -> None:
        self._provider = OpenAIProvider()
        self.calls = 0

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        if self.calls >= MAX_OPENAI_CALLS:
            raise RuntimeError("The Phase 9E pilot permits exactly one OpenAI call.")
        self.calls += 1
        return await self._provider.generate(request)


async def build_question91_input(session) -> dict[str, Any]:
    """Load only the approved pilot question and construct its canonical input."""
    statement = (
        select(QuestionVersion, BookletQuestion, Exam, ExamApplication, ExamBooklet)
        .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
        .join(ExamBooklet, ExamBooklet.id == BookletQuestion.exam_booklet_id)
        .join(ExamApplication, ExamApplication.id == ExamBooklet.exam_application_id)
        .join(Exam, Exam.id == ExamApplication.exam_id)
        .where(
            Exam.code == "ENEM",
            ExamApplication.year == 2020,
            ExamApplication.day == 2,
            ExamBooklet.code == "D2_CD5",
            ExamBooklet.color == "AMARELO",
            BookletQuestion.official_number == TARGET_QUESTION_NUMBER,
            QuestionVersion.version_kind == "official_original",
        )
    )
    rows = (await session.execute(statement)).all()
    if len(rows) != 1:
        raise RuntimeError("Expected exactly one official ENEM 2020 question 91.")

    version, booklet_question, exam, application, booklet = rows[0]
    if booklet_question.official_number != TARGET_QUESTION_NUMBER:
        raise RuntimeError("Only question 91 may enter the Phase 9E pilot payload.")

    options = (
        await session.scalars(
            select(QuestionOption)
            .where(QuestionOption.question_version_id == version.id)
            .order_by(QuestionOption.position)
        )
    ).all()
    if [option.option_key for option in options] != list("ABCDE"):
        raise RuntimeError("Question 91 must have exactly the A-E alternatives.")

    answer_entries = (
        await session.scalars(
            select(AnswerKeyEntry).where(
                AnswerKeyEntry.booklet_question_id == booklet_question.id
            )
        )
    ).all()
    if len(answer_entries) != 1 or answer_entries[0].official_answer_label != "C":
        raise RuntimeError("Question 91 must have official answer C.")

    catalog_nodes = (
        await session.scalars(select(CatalogNode).order_by(CatalogNode.code))
    ).all()
    if len(catalog_nodes) != 17:
        raise RuntimeError("Phase 9E requires exactly 17 authorized CatalogNodes.")

    return {
        "question_version_id": str(version.id),
        "exam": exam.code,
        "year": application.year,
        "day": application.day,
        "booklet": {"code": booklet.code, "color": booklet.color},
        "official_number": booklet_question.official_number,
        "text": version.canonical_text,
        "alternatives": [
            {"key": option.option_key, "text": option.text} for option in options
        ],
        "official_answer": answer_entries[0].official_answer_label,
        "authorized_catalog_nodes": [
            {
                "id": str(node.id),
                "code": node.code,
                "name": node.name,
                "node_type": node.node_type,
            }
            for node in catalog_nodes
        ],
        "classifier_version": CLASSIFIER_VERSION,
        "prompt_version": PROMPT_VERSION,
    }


def serialize_canonical_payload(payload: dict[str, Any]) -> str:
    """Return a stable representation suitable for a later SHA-256 calculation."""
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


async def classify_question91(session, payload: dict[str, Any]):
    """Submit only question 91 as an auditable curriculum proposal."""
    if payload.get("official_number") != TARGET_QUESTION_NUMBER:
        raise RuntimeError("Only question 91 may be classified by this pilot.")
    provider = SingleCallOpenAIProvider()
    router = ProviderRouter(text_providers=[provider], embedding_providers=[])
    record = await ClassificationProposalService(session).propose_with_provider(
        UUID(payload["question_version_id"]),
        router,
        classifier_version=CLASSIFIER_VERSION,
        taxonomy_version=TAXONOMY_VERSION,
        prompt_version=PROMPT_VERSION,
    )
    if provider.calls != MAX_OPENAI_CALLS:
        raise RuntimeError("The Phase 9E pilot did not make exactly one OpenAI call.")
    return record, provider.calls


async def question91_state(session, question_version_id: UUID) -> dict[str, Any]:
    """Capture the immutable target state used by the post-call audit."""
    version = await session.get(QuestionVersion, question_version_id)
    if version is None:
        raise RuntimeError("Question version 91 no longer exists.")
    options = (
        await session.scalars(
            select(QuestionOption)
            .where(QuestionOption.question_version_id == question_version_id)
            .order_by(QuestionOption.position)
        )
    ).all()
    booklet_question = await session.scalar(
        select(BookletQuestion).where(
            BookletQuestion.question_version_id == question_version_id,
            BookletQuestion.official_number == TARGET_QUESTION_NUMBER,
        )
    )
    if booklet_question is None:
        raise RuntimeError("Question version is not the official question 91.")
    answer = await session.scalar(
        select(AnswerKeyEntry).where(
            AnswerKeyEntry.booklet_question_id == booklet_question.id
        )
    )
    return {
        "version_kind": version.version_kind,
        "canonical_text": version.canonical_text,
        "statement": version.statement,
        "content_hash": version.content_hash,
        "recommended_difficulty": version.recommended_difficulty,
        "options": [(option.option_key, option.position, option.text) for option in options],
        "answer": None if answer is None else answer.official_answer_label,
    }


async def audit_integrity(session, question_version_id: UUID, initial_state: dict[str, Any]):
    """Read back only the pilot state and proposal invariants."""
    final_state = await question91_state(session, question_version_id)
    classifications = (
        await session.scalars(
            select(PedagogicalClassification).where(
                PedagogicalClassification.question_version_id == question_version_id
            )
        )
    ).all()
    other_classifications = await session.scalar(
        select(func.count())
        .select_from(PedagogicalClassification)
        .where(PedagogicalClassification.question_version_id != question_version_id)
    )
    catalog_count = await session.scalar(select(func.count()).select_from(CatalogNode))
    link_count = await session.scalar(select(func.count()).select_from(ContentQuestionLink))
    if final_state != initial_state:
        raise RuntimeError("The question 91 source state changed during the pilot.")
    if len(classifications) != 1 or other_classifications != 0:
        raise RuntimeError("The pilot persisted a proposal outside question 91.")
    if catalog_count != 17 or link_count != 0:
        raise RuntimeError("CatalogNodes or ContentQuestionLinks changed during the pilot.")
    record = classifications[0]
    metadata = record.metadata_ or {}
    required_metadata = {"input_hash", "output_hash", "evidence", "confidence_band"}
    if not required_metadata.issubset(metadata) or not metadata["evidence"]:
        raise RuntimeError("The persisted proposal lacks required audit metadata.")
    return record, {
        "question_unchanged": final_state == initial_state,
        "catalog_nodes": catalog_count,
        "content_question_links": link_count,
        "question91_classifications": len(classifications),
        "other_question_classifications": other_classifications,
    }


async def main() -> None:
    try:
        load_required_configuration()
    except RuntimeError:
        print("BLOQUEADO")
        return
    engine = create_engine()
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            payload = await build_question91_input(session)
            question_version_id = UUID(payload["question_version_id"])
            initial_state = await question91_state(session, question_version_id)
            existing_count = await session.scalar(
                select(func.count())
                .select_from(PedagogicalClassification)
                .where(PedagogicalClassification.question_version_id == question_version_id)
            )
            if existing_count != 0:
                raise RuntimeError("Question 91 already has a PedagogicalClassification.")
            try:
                proposal, call_count = await classify_question91(session, payload)
            except Exception as exc:
                await session.rollback()
                print(f"BLOQUEADO\n{format_diagnostic_error(exc)}")
                return
            proposal, audit = await audit_integrity(session, question_version_id, initial_state)
        metadata = proposal.metadata_ or {}
        print("RESULTADO DA QUESTÃO 91: PROPOSTA PERSISTIDA")
        print(f"CLASSIFICAÇÃO PROPOSTA: {metadata.get('primary_content_code')}")
        print(f"CONFIDENCE: {metadata.get('confidence_band')}")
        print(f"EVIDÊNCIAS: {json.dumps(metadata.get('evidence'), ensure_ascii=True)}")
        print(f"STATUS: {proposal.status}")
        print(f"INPUT_HASH: {metadata.get('input_hash')}")
        print(f"OUTPUT_HASH: {metadata.get('output_hash')}")
        print(f"PROVIDER/MODEL: {proposal.provider_name}/{proposal.model_name}")
        print(f"AUDITORIA DE INTEGRIDADE: {json.dumps(audit, sort_keys=True)}")
        print(f"QUANTIDADE DE CHAMADAS OPENAI: {call_count}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())