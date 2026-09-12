"""Read-only reconciliation of curriculum history for ENEM questions 93 and 128."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import select, text

from agente_ia_edu.db.models import (
    BookletQuestion,
    ModificationProposal,
    PedagogicalClassification,
    Question,
    QuestionVersion,
)
from agente_ia_edu.db.session import create_engine, create_session_factory, get_database_url


SOURCE_TAXONOMY_VERSION = "023_curriculum_taxonomy"
TARGET_QUESTIONS = (93, 128)
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def validate_environment(environ: dict[str, str] | None = None) -> None:
    source = os.environ if environ is None else environ
    if not source.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is unavailable")
    if not get_database_url(source).startswith("postgresql+psycopg://"):
        raise RuntimeError("Historical reconciliation requires PostgreSQL")


def _classification_record(record: PedagogicalClassification) -> dict[str, Any]:
    metadata = record.metadata_ or {}
    return {
        "id": str(record.id),
        "question_version_id": str(record.question_version_id),
        "taxonomy_version": metadata.get("taxonomy_version"),
        "classifier_version": record.model_version,
        "prompt_version": record.prompt_version,
        "input_hash_present": bool(metadata.get("input_hash")),
        "output_hash_present": bool(metadata.get("output_hash")),
        "created_at": str(record.created_at),
        "status": record.status,
        "primary_codes": {
            "discipline": record.discipline,
            "area": metadata.get("area_code"),
            "content": record.content,
            "subcontent": record.subcontent,
        },
        "candidate_count": len(metadata.get("candidate_classifications", [])),
        "selected_candidate_rank": metadata.get("selected_candidate_rank"),
        "recovered_candidate_count": len(metadata.get("recovered_candidates", [])),
        "has_reclassification_metadata": bool(metadata.get("reclassification")),
    }


def _modification_record(record: ModificationProposal) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "original_question_version_id": str(record.original_question_version_id),
        "created_at": str(record.created_at),
        "updated_at": str(record.updated_at),
        "status": record.status,
        "provider": record.provider,
        "model": record.model,
    }


async def inspect_question(session, number: int) -> dict[str, Any]:
    rows = (await session.execute(
        select(Question, QuestionVersion)
        .join(QuestionVersion, QuestionVersion.question_id == Question.id)
        .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
        .where(BookletQuestion.official_number == number)
        .order_by(QuestionVersion.created_at)
    )).all()
    versions = [version for _, version in rows]
    classifications = []
    modifications = []
    for version in versions:
        classifications.extend((await session.scalars(
            select(PedagogicalClassification).where(
                PedagogicalClassification.question_version_id == version.id
            ).order_by(PedagogicalClassification.created_at)
        )).all())
        modifications.extend((await session.scalars(
            select(ModificationProposal).where(
                ModificationProposal.original_question_version_id == version.id
            ).order_by(ModificationProposal.created_at)
        )).all())
    historical = [record for record in classifications if (record.metadata_ or {}).get("taxonomy_version") == SOURCE_TAXONOMY_VERSION]
    return {
        "question_found": bool(rows),
        "question_id": str(rows[0][0].id) if rows else None,
        "question_version_ids": [str(version.id) for version in versions],
        "question_version_count": len(versions),
        "classifications": [_classification_record(record) for record in classifications],
        "historical_classifications": [_classification_record(record) for record in historical],
        "modification_proposals": [_modification_record(record) for record in modifications],
    }


async def main() -> None:
    load_dotenv(ENV_FILE, override=False)
    environment = {
        "DATABASE_URL": bool(os.getenv("DATABASE_URL")),
        "OPENAI_API_KEY": bool(os.getenv("OPENAI_API_KEY")),
        "OPENAI_MODEL": "PRESENT" if os.getenv("OPENAI_MODEL") else "ABSENT",
    }
    result = {
        "PROCESS_ENVIRONMENT": environment,
        "POSTGRESQL_READS": 0,
        "POSTGRESQL_WRITES": 0,
        "DATABASE_WRITES": 0,
        "MIGRATION_EXECUTION": 0,
        "OPENAI_CALLS": 0,
    }
    try:
        validate_environment()
        engine = create_engine()
        factory = create_session_factory(engine)
        try:
            async with factory() as session:
                async with session.begin():
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                    result["QUESTION_93"] = await inspect_question(session, 93)
                    result["QUESTION_128"] = await inspect_question(session, 128)
                    sample = (await session.scalars(
                        select(PedagogicalClassification)
                        .where(PedagogicalClassification.metadata_["taxonomy_version"].as_string() == SOURCE_TAXONOMY_VERSION)
                        .order_by(PedagogicalClassification.created_at)
                        .limit(5)
                    )).all()
                    result["OTHER_023_HISTORICAL_DATA"] = [_classification_record(record) for record in sample]
                    result["POSTGRESQL_READS"] = 1
        finally:
            await engine.dispose()
        histories = [result[f"QUESTION_{number}"]["historical_classifications"] for number in TARGET_QUESTIONS]
        result["PREFLIGHT_QUERY"] = "VALID"
        result["ROOT_CAUSE"] = "HISTORICAL_DATA_ABSENT" if not any(histories) else "NEEDS_RELATION_REVIEW"
        result["FINAL_DECISION"] = "NO-GO" if not all(histories) else "NEEDS_REVIEW"
    except Exception as exc:
        result["ERROR_TYPE"] = type(exc).__name__
        result["ERROR_MESSAGE"] = str(exc)
        result["ROOT_CAUSE"] = "OTHER"
        result["FINAL_DECISION"] = "NO-GO"
    print("PHASE 9T.9A — HISTORICAL DATA RECONCILIATION")
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())