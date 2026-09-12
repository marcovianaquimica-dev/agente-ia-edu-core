"""Controlled Phase 9E.3 pilot for only ENEM 2020 D2_CD5 question 92."""

import asyncio
import json
import os
import re
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


TARGET_QUESTION_NUMBER = 92
MAX_OPENAI_CALLS = 1
CLASSIFIER_VERSION = "phase9e1-taxonomy-coverage-v1"
PROMPT_VERSION = "phase9e1-classification-contract-v1"
TAXONOMY_VERSION = "023_curriculum_taxonomy"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_required_configuration() -> None:
    load_dotenv(dotenv_path=ENV_FILE, override=False)
    if not all(os.getenv(name) for name in ("DATABASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL")):
        raise RuntimeError("Required Phase 9E configuration is unavailable.")


class SingleCallOpenAIProvider:
    provider = "openai"

    def __init__(self) -> None:
        self._provider = OpenAIProvider()
        self.calls = 0

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        if self.calls >= MAX_OPENAI_CALLS:
            raise RuntimeError("The Phase 9E.3 pilot permits exactly one OpenAI call.")
        self.calls += 1
        return await self._provider.generate(request)


async def load_question92(session) -> tuple[QuestionVersion, BookletQuestion]:
    rows = (await session.execute(
        select(QuestionVersion, BookletQuestion)
        .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
        .join(ExamBooklet, ExamBooklet.id == BookletQuestion.exam_booklet_id)
        .join(ExamApplication, ExamApplication.id == ExamBooklet.exam_application_id)
        .join(Exam, Exam.id == ExamApplication.exam_id)
        .where(
            Exam.code == "ENEM", ExamApplication.year == 2020,
            ExamApplication.day == 2, ExamBooklet.code == "D2_CD5",
            ExamBooklet.color == "AMARELO",
            BookletQuestion.official_number == TARGET_QUESTION_NUMBER,
            QuestionVersion.version_kind == "official_original",
        )
    )).all()
    if len(rows) != 1:
        raise RuntimeError("Expected exactly one official ENEM 2020 question 92.")
    version, booklet_question = rows[0]
    options = (await session.scalars(
        select(QuestionOption).where(QuestionOption.question_version_id == version.id)
        .order_by(QuestionOption.position)
    )).all()
    answer_entries = (await session.scalars(
        select(AnswerKeyEntry).where(AnswerKeyEntry.booklet_question_id == booklet_question.id)
    )).all()
    if not version.canonical_text or [option.option_key for option in options] != list("ABCDE") or len(answer_entries) != 1:
        raise RuntimeError("Question 92 is not a complete objective official record.")
    return version, booklet_question


async def source_state(session, version: QuestionVersion, booklet_question: BookletQuestion) -> dict[str, Any]:
    options = (await session.scalars(
        select(QuestionOption).where(QuestionOption.question_version_id == version.id)
        .order_by(QuestionOption.position)
    )).all()
    answer = await session.scalar(
        select(AnswerKeyEntry.official_answer_label).where(
            AnswerKeyEntry.booklet_question_id == booklet_question.id
        )
    )
    return {
        "canonical_text": version.canonical_text,
        "statement": version.statement,
        "content_hash": version.content_hash,
        "recommended_difficulty": version.recommended_difficulty,
        "options": [(option.option_key, option.position, option.text) for option in options],
        "answer": answer,
    }


def _sanitize(value: str) -> str:
    value = re.sub(r"postgres(?:ql)?[^\s]*", "[REDACTED]", value, flags=re.I)
    return re.sub(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+", "[REDACTED]", value)


def format_error(exc: Exception) -> str:
    source = exc.attempts[0] if isinstance(exc, AllProvidersFailedError) and exc.attempts else exc
    lines = [
        f"TYPE: {type(exc).__name__}",
        f"MESSAGE: {_sanitize(str(exc))}",
        f"ORIGINAL_ERROR_TYPE: {getattr(source, 'original_error_type', 'N/A')}",
        f"DIAGNOSTIC_MESSAGE: {_sanitize(str(getattr(source, 'diagnostic_message', 'N/A')))}",
        f"LOW_LEVEL_ERROR_TYPE: {getattr(source, 'low_level_error_type', 'N/A')}",
        f"LOW_LEVEL_DIAGNOSTIC_MESSAGE: {_sanitize(str(getattr(source, 'low_level_diagnostic_message', 'N/A')))}",
    ]
    diagnostic_output = getattr(exc, "diagnostic_output", None)
    if diagnostic_output is not None:
        lines.append(
            "VALIDATION_DIAGNOSTIC: "
            + json.dumps(diagnostic_output, ensure_ascii=True, sort_keys=True)
        )
    return "\n".join(lines)


async def main() -> None:
    try:
        load_required_configuration()
    except RuntimeError:
        print("BLOQUEADO\nCONFIGURATION")
        return

    engine = create_engine()
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            version, booklet_question = await load_question92(session)
            initial = await source_state(session, version, booklet_question)
            catalog_before = await session.scalar(select(func.count()).select_from(CatalogNode))
            links_before = await session.scalar(select(func.count()).select_from(ContentQuestionLink))
            existing = await session.scalar(select(func.count()).select_from(PedagogicalClassification).where(PedagogicalClassification.question_version_id == version.id))
            if catalog_before != 17 or links_before != 0 or existing != 0:
                raise RuntimeError("Phase 9E.3 preconditions are not satisfied.")

            service = ClassificationProposalService(session)
            catalog = (await session.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)))).all()
            recovered = service.retrieve_candidate_classifications(version.canonical_text, catalog)
            if recovered:
                raise RuntimeError("Question 92 recovered catalog candidates; manual review is required.")

            provider = SingleCallOpenAIProvider()
            try:
                proposal = await service.propose_with_provider(
                    version.id,
                    ProviderRouter(text_providers=[provider], embedding_providers=[]),
                    classifier_version=CLASSIFIER_VERSION,
                    taxonomy_version=TAXONOMY_VERSION,
                    prompt_version=PROMPT_VERSION,
                )
            except Exception as exc:
                await session.rollback()
                print(f"BLOQUEADO\n{format_error(exc)}\nOPENAI_CALLS: {provider.calls}")
                return

            final = await source_state(session, version, booklet_question)
            catalog_after = await session.scalar(select(func.count()).select_from(CatalogNode))
            links_after = await session.scalar(select(func.count()).select_from(ContentQuestionLink))
            question92_records = await session.scalar(select(func.count()).select_from(PedagogicalClassification).where(PedagogicalClassification.question_version_id == version.id))
            classified_numbers = (await session.scalars(
                select(BookletQuestion.official_number)
                .join(QuestionVersion, QuestionVersion.id == BookletQuestion.question_version_id)
                .join(PedagogicalClassification, PedagogicalClassification.question_version_id == QuestionVersion.id)
                .distinct()
            )).all()
            if provider.calls != MAX_OPENAI_CALLS or final != initial or catalog_after != catalog_before or links_after != links_before or question92_records != 1 or any(number not in {91, TARGET_QUESTION_NUMBER} for number in classified_numbers):
                raise RuntimeError("Phase 9E.3 integrity audit failed.")

        metadata = proposal.metadata_ or {}
        print("PHASE 9E.3 — RESULTADO QUESTÃO 92")
        for label, key in (("Status", "proposal_status"), ("Discipline", "discipline_code"), ("Area", "area_code"), ("Content", "content_code"), ("Subcontent", "subcontent_code"), ("Confidence", "confidence_band"), ("Candidates", "candidate_classifications"), ("Catalog gap", "catalog_gap"), ("Gap type", "gap_type"), ("Review reason", "review_reason"), ("Visual dependency", "visual_dependency"), ("Evidence", "evidence"), ("Taxonomy coverage evidence", "taxonomy_coverage_evidence"), ("Input hash", "input_hash"), ("Output hash", "output_hash")):
            print(f"{label}: {json.dumps(metadata.get(key), ensure_ascii=True)}")
        print(f"Provider: {proposal.provider_name}")
        print(f"Model: {proposal.model_name}")
        print("AUDITORIA")
        print(f"OpenAI calls: {provider.calls}")
        print(f"CatalogNodes before/after: {catalog_before}/{catalog_after}")
        print(f"ContentQuestionLinks before/after: {links_before}/{links_after}")
        print(f"Other classifications: {len([number for number in classified_numbers if number != TARGET_QUESTION_NUMBER])}")
        print(f"Question 92 classifications: {question92_records}")
        print(f"Question unchanged: {final == initial}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())