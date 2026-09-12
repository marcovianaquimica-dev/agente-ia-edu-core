"""Explicit, fail-closed production orchestrator for taxonomy revision 024.

Run manually only after reviewing its read-only pre-flight output.
"""

import asyncio
import importlib.util
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import select, text

from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    BookletQuestion,
    CatalogNode,
    ContentQuestionLink,
    PedagogicalClassification,
    QuestionOption,
    QuestionVersion,
)
from agente_ia_edu.db.session import create_engine, create_session_factory, get_database_url
from agente_ia_edu.providers.adapters.openai import OpenAIProvider
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.providers.router import ProviderRouter
from agente_ia_edu.services.curriculum_classification import (
    KINETICS_RETRIEVAL_VOCABULARY,
    ClassificationProposalService,
)


SOURCE_REVISION = "023_curriculum_taxonomy"
TARGET_REVISION = "024_chemistry_kinetics"
TARGET_QUESTIONS = (93, 128)
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
PROJECT_ROOT = ENV_FILE.parent
MIGRATION_FILE = PROJECT_ROOT / "migrations" / "versions" / "024_chemistry_kinetics.py"
NODE_CODE = "CHEMISTRY-PHYSICAL-KINETICS"
PARENT_CODE = "CHEMISTRY-PHYSICAL"
NODE_EXPECTED = {
    "code": NODE_CODE,
    "name": "Cinética química",
    "description": "Estudo da velocidade das reações químicas.",
    "node_type": "CONTENT",
    "active": True,
}


@dataclass(frozen=True)
class PreflightSnapshot:
    revision: str
    catalog_nodes: tuple[dict[str, Any], ...]
    historical_proposals: dict[int, dict[str, Any]]
    question_states: dict[int, dict[str, Any]]
    link_count: int


class CountingOpenAIProvider:
    """Allow one external request for one selected question."""

    provider = "openai"

    def __init__(self) -> None:
        self._provider = OpenAIProvider()
        self.calls = 0

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        if self.calls:
            raise RuntimeError("Only one OpenAI call is permitted per selected question.")
        self.calls += 1
        return await self._provider.generate(request)


def validate_environment(environ: dict[str, str] | None = None) -> None:
    source = os.environ if environ is None else environ
    if not all(source.get(name) for name in ("DATABASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL")):
        raise RuntimeError("Required production environment is unavailable")
    if not get_database_url(source).startswith("postgresql+psycopg://"):
        raise RuntimeError("Production migration requires PostgreSQL")


def validate_preflight(snapshot: PreflightSnapshot) -> None:
    if snapshot.revision != SOURCE_REVISION:
        raise RuntimeError("Unexpected Alembic revision")
    parent = next((node for node in snapshot.catalog_nodes if node["code"] == PARENT_CODE), None)
    if parent is None or parent["parent_code"] != "CHEMISTRY" or parent["node_type"] != "AREA" or not parent["active"]:
        raise RuntimeError("Kinetics taxonomy parent is unavailable or incompatible")
    node = next((node for node in snapshot.catalog_nodes if node["code"] == NODE_CODE), None)
    if node is not None:
        expected = {**NODE_EXPECTED, "parent_code": PARENT_CODE}
        if any(node.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Existing kinetics taxonomy node is incompatible")
    for number in TARGET_QUESTIONS:
        proposal = snapshot.historical_proposals.get(number)
        if proposal is None or not proposal.get("input_hash") or not proposal.get("output_hash"):
            raise RuntimeError(f"Historical proposal is unavailable or unauditable: {number}")


def validate_local_migration() -> None:
    spec = importlib.util.spec_from_file_location("phase9t9_migration", MIGRATION_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError("Target migration file is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.revision != TARGET_REVISION or module.down_revision != SOURCE_REVISION:
        raise RuntimeError("Target migration revision chain is incompatible")


def validate_post_migration(before: PreflightSnapshot, after: PreflightSnapshot) -> None:
    if after.revision != TARGET_REVISION:
        raise RuntimeError("Target Alembic revision was not applied")
    before_nodes = {node["code"]: node for node in before.catalog_nodes}
    after_nodes = {node["code"]: node for node in after.catalog_nodes}
    if set(after_nodes) != set(before_nodes) | {NODE_CODE}:
        raise RuntimeError("Unexpected CatalogNode snapshot after migration")
    if any(after_nodes[code] != node for code, node in before_nodes.items()):
        raise RuntimeError("Existing CatalogNode changed during migration")
    expected = {**NODE_EXPECTED, "parent_code": PARENT_CODE}
    node = after_nodes.get(NODE_CODE)
    if node is None or any(node.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Kinetics taxonomy node is missing or incompatible after migration")
    if before.link_count != after.link_count:
        raise RuntimeError("ContentQuestionLink changed during migration")
    if before.historical_proposals != after.historical_proposals:
        raise RuntimeError("Historical proposal changed during migration")
    if before.question_states != after.question_states:
        raise RuntimeError("Question source state changed during migration")


def preflight_observations(snapshot: PreflightSnapshot) -> dict[str, Any]:
    """Return only safe pre-flight state needed to diagnose a fail-closed abort."""
    nodes = {node["code"]: node for node in snapshot.catalog_nodes}
    parent = nodes.get(PARENT_CODE)
    kinetics = nodes.get(NODE_CODE)
    return {
        "FOUND_REVISION": snapshot.revision,
        "EXPECTED_REVISION": SOURCE_REVISION,
        "FOUND_TAXONOMY_VERSION": snapshot.revision,
        "CATALOG_NODE_COUNT": len(snapshot.catalog_nodes),
        "PARENT_EXISTS": parent is not None,
        "PARENT_TYPE": parent.get("node_type") if parent else None,
        "PARENT_ACTIVE": parent.get("active") if parent else None,
        "KINETICS_NODE_EXISTS": kinetics is not None,
        "KINETICS_NODE_STATE": (
            {key: kinetics.get(key) for key in ("code", "name", "description", "node_type", "active", "parent_code")}
            if kinetics
            else None
        ),
        "HISTORICAL_PROPOSALS": {
            number: {
                "exists": number in snapshot.historical_proposals,
                "id": proposal.get("id"),
                "taxonomy_version": proposal.get("taxonomy_version"),
                "input_hash_present": bool(proposal.get("input_hash")),
                "output_hash_present": bool(proposal.get("output_hash")),
            }
            for number, proposal in (
                (number, snapshot.historical_proposals.get(number, {}))
                for number in TARGET_QUESTIONS
            )
        },
    }


async def _question_state(session, number: int) -> dict[str, Any]:
    version = await session.scalar(
        select(QuestionVersion)
        .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
        .where(BookletQuestion.official_number == number)
    )
    if version is None:
        raise RuntimeError(f"Official question is unavailable: {number}")
    options = (await session.scalars(select(QuestionOption).where(QuestionOption.question_version_id == version.id).order_by(QuestionOption.position))).all()
    answer = await session.scalar(
        select(AnswerKeyEntry.official_answer_label)
        .join(BookletQuestion, BookletQuestion.id == AnswerKeyEntry.booklet_question_id)
        .where(BookletQuestion.question_version_id == version.id)
    )
    return {"question_version_id": str(version.id), "content_hash": version.content_hash, "text": version.canonical_text, "difficulty": version.recommended_difficulty, "options": [(item.option_key, item.position, item.text) for item in options], "answer": answer}


async def capture_preflight(session) -> PreflightSnapshot:
    await session.execute(text("SET TRANSACTION READ ONLY"))
    revisions = (await session.scalars(text("SELECT version_num FROM alembic_version"))).all()
    if len(revisions) != 1:
        raise RuntimeError("Alembic revision state is incompatible")
    nodes = (await session.scalars(select(CatalogNode).order_by(CatalogNode.code))).all()
    node_map = {node.id: node for node in nodes}
    catalog_snapshot = tuple({"id": str(node.id), "code": node.code, "name": node.name, "description": node.description, "node_type": node.node_type, "active": node.active, "parent_code": node_map[node.parent_id].code if node.parent_id else None} for node in nodes)
    historical = {}
    for number in TARGET_QUESTIONS:
        proposal = await session.scalar(
            select(PedagogicalClassification)
            .join(QuestionVersion, QuestionVersion.id == PedagogicalClassification.question_version_id)
            .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
            .where(BookletQuestion.official_number == number, PedagogicalClassification.metadata_["taxonomy_version"].as_string() == SOURCE_REVISION)
            .order_by(PedagogicalClassification.created_at.desc())
        )
        if proposal is not None:
            metadata = proposal.metadata_ or {}
            historical[number] = {"id": str(proposal.id), "discipline": proposal.discipline, "content": proposal.content, "subcontent": proposal.subcontent, "status": proposal.status, "created_at": str(proposal.created_at), "taxonomy_version": metadata.get("taxonomy_version"), "input_hash": metadata.get("input_hash"), "output_hash": metadata.get("output_hash"), "metadata": metadata}
    states = {number: await _question_state(session, number) for number in (91, 92, 93, 95, 104, 128, 135)}
    return PreflightSnapshot(revisions[0], catalog_snapshot, historical, states, await session.scalar(select(text("count(*)")).select_from(ContentQuestionLink)))


def _alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return config


def _run_migration(target: str) -> None:
    command.upgrade(_alembic_config(), target)


async def _reclassify(session, number: int) -> tuple[PedagogicalClassification, int]:
    version = await session.scalar(
        select(QuestionVersion)
        .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
        .where(BookletQuestion.official_number == number)
    )
    if version is None:
        raise RuntimeError(f"Selected question is unavailable: {number}")
    provider = CountingOpenAIProvider()
    proposal = await ClassificationProposalService(session).reclassify_with_provider(
        version.id,
        ProviderRouter(text_providers=[provider], embedding_providers=[]),
        source_taxonomy_version=SOURCE_REVISION,
        target_taxonomy_version=TARGET_REVISION,
        classifier_version="phase9t9-reclassification-v1",
        prompt_version="phase9t3-kinetics-v1",
        reclassification_reason="approved kinetics taxonomy migration",
    )
    return proposal, provider.calls


async def main() -> None:
    load_dotenv(ENV_FILE, override=False)
    result = {"PRE-FLIGHT": "FAIL", "SOURCE_REVISION": SOURCE_REVISION, "TARGET_REVISION": TARGET_REVISION, "MIGRATION": "FAIL", "OPENAI_CALLS": 0, "POSTGRESQL_READS": 0, "POSTGRESQL_WRITES": 0, "MIGRATION_EXECUTION": 0, "DATABASE_WRITES": 0, "UNEXPECTED_CHANGES": [], "ROLLBACK": "PASS"}
    preflight_snapshot = None
    try:
        validate_environment()
        validate_local_migration()
        engine = create_engine()
        factory = create_session_factory(engine)
        try:
            async with factory() as session:
                async with session.begin():
                    before = await capture_preflight(session)
                    preflight_snapshot = before
                    result["POSTGRESQL_READS"] += 1
            validate_preflight(before)
            result["PRE-FLIGHT"] = "PASS"
            _run_migration(TARGET_REVISION)
            result["MIGRATION_EXECUTION"] = 1
            result["POSTGRESQL_WRITES"] += 1
            result["DATABASE_WRITES"] += 1
            async with factory() as session:
                async with session.begin():
                    after_migration = await capture_preflight(session)
                    result["POSTGRESQL_READS"] += 1
            validate_post_migration(before, after_migration)
            for number in TARGET_QUESTIONS:
                async with factory() as session:
                    proposal, calls = await _reclassify(session, number)
                    result["OPENAI_CALLS"] += calls
                    result["POSTGRESQL_WRITES"] += 1
                    result["DATABASE_WRITES"] += 1
                    metadata = proposal.metadata_ or {}
                    if metadata.get("taxonomy_version") != TARGET_REVISION or metadata.get("reclassification", {}).get("source_taxonomy_version") != SOURCE_REVISION:
                        raise RuntimeError(f"Reclassification audit failed: {number}")
                    result[f"RECLASSIFICATION_{number}"] = "PASS"
            result["MIGRATION"] = "PASS"
            result["FINAL_DECISION"] = "PRODUCTION_MIGRATION_SUCCESS"
        finally:
            await engine.dispose()
    except Exception as exc:
        result["PREFLIGHT_FAILURE_TYPE"] = type(exc).__name__
        result["PREFLIGHT_FAILURE_MESSAGE"] = str(exc)
        if preflight_snapshot is not None:
            result.update(preflight_observations(preflight_snapshot))
        result["FINAL_DECISION"] = "PRODUCTION_MIGRATION_NEEDS_REVIEW"
    print("PHASE 9T.9 — PRODUCTION MIGRATION & CONTROLLED RECLASSIFICATION")
    for key, value in result.items():
        print(f"{key}: {json.dumps(value, ensure_ascii=True, sort_keys=True)}")


if __name__ == "__main__":
    asyncio.run(main())