"""Fail-closed production executor for the INITIAL curriculum classification of
ENEM questions 93 and 128 under taxonomy revision ``024_chemistry_kinetics``.

Run manually only, and only from the user's own Terminal with a real
``DATABASE_URL`` / ``OPENAI_API_KEY`` / ``OPENAI_MODEL`` exported.

Guarantees enforced by construction:

* Nothing writes to the database, runs Alembic, or calls OpenAI until every
  pre-flight condition has passed AND a human approval token has been supplied.
* The PostgreSQL pre-flight runs inside an explicit ``SET TRANSACTION READ ONLY``.
* Only questions 93 and 128 can ever be processed (``enforce_question_scope``).
* The INITIAL flow is used exclusively via ``classify_initial_with_provider``;
  the reclassification entrypoint is never invoked or referenced by this module.
* If a post-migration validation fails, a real, dependency-checked downgrade is
  attempted; an unsafe rollback is never masked.
* A partial failure (93 ok, 128 fails) stops immediately and is reported as-is.

The local migration chain now continues past this executor's own target
(023 -> 024 -> 025, where 025 is Phase 9U.1-D's lifecycle/supersession
infrastructure). This executor's own scope is unchanged - it still upgrades
to and classifies against 024_chemistry_kinetics only - but its chain-
integrity check recognises 025 as the chain's current single head, verified
by exact revision id and down_revision linkage, never "any" head.

This module is intentionally factored so that every decision function is pure and
unit-testable against SQLite / plain objects, with no PostgreSQL, Alembic or
OpenAI dependency.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import load_dotenv
from sqlalchemy import func, select, text

from agente_ia_edu.db.models import (
    BookletQuestion,
    CatalogNode,
    ContentQuestionLink,
    PedagogicalClassification,
    QuestionVersion,
)
from agente_ia_edu.db.session import (
    create_engine,
    create_session_factory,
    get_database_url,
)
from agente_ia_edu.providers.adapters.openai import OpenAIProvider
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.providers.router import ProviderRouter
from agente_ia_edu.services.curriculum_classification import (
    KINETICS_RETRIEVAL_VOCABULARY,
    KINETICS_TAXONOMY_VERSION,
    ClassificationProposalService,
)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

SOURCE_REVISION = "023_curriculum_taxonomy"
TARGET_REVISION = "024_chemistry_kinetics"
# The local migration chain has grown a further link (025, Phase 9U.1-D's
# lifecycle/supersession infrastructure) since this executor was written. Its
# own job is unchanged - upgrade to and classify against TARGET_REVISION
# (024) - but the chain-integrity check below must recognise the chain's true
# current single head, or it would spuriously fail forever. It never accepts
# "any" head: 025 is checked by exact id and by exact down_revision linkage
# back through 024 to 023, the same way 024 is checked back to 023.
LIFECYCLE_REVISION = "025_classification_lifecycle"
EXPECTED_SINGLE_HEAD = LIFECYCLE_REVISION

TARGET_QUESTIONS: tuple[int, ...] = (93, 128)
ALLOWED_QUESTION_NUMBERS = frozenset(TARGET_QUESTIONS)

CONTROLLED_VOCABULARY_VERSION = "phase9t3-kinetics-v1"
CLASSIFIER_VERSION = "phase9u1-initial-v1"
PROMPT_VERSION = "phase9t3-kinetics-v1"
CLASSIFICATION_MODE = "INITIAL"

APPROVAL_ENV_VAR = "PHASE9U1_APPROVAL_TOKEN"
APPROVAL_TOKEN = "PHASE-9U1-PREFLIGHT-REVIEWED"

PARENT_PARENT_CODE = "CHEMISTRY"
PARENT_CODE = "CHEMISTRY-PHYSICAL"
NODE_CODE = "CHEMISTRY-PHYSICAL-KINETICS"
NODE_EXPECTED: dict[str, Any] = {
    "code": NODE_CODE,
    "name": "Cinética química",
    "description": "Estudo da velocidade das reações químicas.",
    "node_type": "CONTENT",
    "active": True,
}

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
MIGRATION_023_FILE = MIGRATIONS_DIR / "versions" / "023_curriculum_taxonomy.py"
MIGRATION_024_FILE = MIGRATIONS_DIR / "versions" / "024_chemistry_kinetics.py"
MIGRATION_025_FILE = (
    MIGRATIONS_DIR / "versions" / "025_pedagogical_classification_lifecycle.py"
)

# Approved shape of migration 025 (Phase 9U.1-D), checked the same way 024's
# approved node configuration is checked below - by exact value, not by trust.
LIFECYCLE_TABLE = "pedagogical_classifications"
LIFECYCLE_ACTIVE_UNIQUE_INDEX = "uq_pedagogical_classifications_active_taxonomy"
LIFECYCLE_CHECK_CONSTRAINT = "ck_pedagogical_classifications_lifecycle"
LIFECYCLE_SUPERSEDES_FK = "fk_pedagogical_classifications_supersedes_id"

SUCCESS_DECISION = "PRODUCTION_INITIAL_CLASSIFICATION_SUCCESS"
INITIAL_REVIEW_DECISION = "PRODUCTION_INITIAL_CLASSIFICATION_NEEDS_REVIEW"
MIGRATION_REVIEW_DECISION = "PRODUCTION_MIGRATION_NEEDS_REVIEW"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ExecutorError(RuntimeError):
    """Base class for every deliberate fail-closed abort."""


class PreFlightError(ExecutorError):
    """A pre-flight / environment / snapshot condition was not as expected."""


class ApprovalError(ExecutorError):
    """The human approval gate was not satisfied."""


class PostMigrationError(ExecutorError):
    """A post-migration validation failed (triggers the rollback path)."""


class RollbackError(ExecutorError):
    """A downgrade was attempted but did not restore a safe state."""


class QuestionScopeError(ExecutorError):
    """An attempt was made to touch a question other than 93 or 128."""


# --------------------------------------------------------------------------- #
# Scope guard
# --------------------------------------------------------------------------- #


def enforce_question_scope(numbers: Iterable[int]) -> None:
    """Reject any question number outside the permitted 93/128 scope."""
    invalid = sorted({int(n) for n in numbers} - ALLOWED_QUESTION_NUMBERS)
    if invalid:
        raise QuestionScopeError(
            f"Questions outside the permitted 93/128 scope are forbidden: {invalid}"
        )


# --------------------------------------------------------------------------- #
# 1. Environment
# --------------------------------------------------------------------------- #


def validate_environment(environ: dict[str, str] | None = None) -> None:
    """Fail closed unless DATABASE_URL (PostgreSQL) + OpenAI vars are present."""
    source = os.environ if environ is None else environ
    missing = [
        name
        for name in ("DATABASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL")
        if not source.get(name)
    ]
    if missing:
        raise PreFlightError(
            "Required production environment is unavailable: " + ",".join(missing)
        )
    database_url = source.get("DATABASE_URL", "")
    if not database_url.startswith("postgresql+psycopg://"):
        raise PreFlightError(
            "Production execution requires a postgresql+psycopg:// DATABASE_URL"
        )


# --------------------------------------------------------------------------- #
# 2. Local migration chain validation (no database, no Alembic execution)
# --------------------------------------------------------------------------- #


def _alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def _load_migration_module(path: Path, name: str) -> Any:
    if not path.exists():
        raise PreFlightError(f"Migration file is unavailable: {path.name}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PreFlightError(f"Migration file cannot be imported: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def validate_local_migration_chain() -> dict[str, Any]:
    """Static assertions on 023/024 and the Alembic revision graph.

    Never opens a database connection and never runs a migration.
    """
    if KINETICS_TAXONOMY_VERSION != TARGET_REVISION:
        raise PreFlightError(
            "Service KINETICS_TAXONOMY_VERSION is not aligned with the target revision"
        )
    if KINETICS_RETRIEVAL_VOCABULARY.version != CONTROLLED_VOCABULARY_VERSION:
        raise PreFlightError("Controlled vocabulary version is not phase9t3-kinetics-v1")
    if KINETICS_RETRIEVAL_VOCABULARY.canonical_code != NODE_CODE:
        raise PreFlightError(
            "Controlled vocabulary is not bound to CHEMISTRY-PHYSICAL-KINETICS"
        )

    module_023 = _load_migration_module(MIGRATION_023_FILE, "phase9u1_migration_023")
    module_024 = _load_migration_module(MIGRATION_024_FILE, "phase9u1_migration_024")
    module_025 = _load_migration_module(MIGRATION_025_FILE, "phase9u1_migration_025")

    if getattr(module_023, "revision", None) != SOURCE_REVISION:
        raise PreFlightError("023 migration revision id is not 023_curriculum_taxonomy")
    if getattr(module_024, "revision", None) != TARGET_REVISION:
        raise PreFlightError("024 migration revision id is not 024_chemistry_kinetics")
    if getattr(module_024, "down_revision", None) != SOURCE_REVISION:
        raise PreFlightError("024 migration does not declare 023 as its down_revision")
    if getattr(module_025, "revision", None) != LIFECYCLE_REVISION:
        raise PreFlightError(
            f"025 migration revision id is not {LIFECYCLE_REVISION}"
        )
    if getattr(module_025, "down_revision", None) != TARGET_REVISION:
        raise PreFlightError("025 migration does not declare 024 as its down_revision")

    lifecycle_approved = {
        "revision": LIFECYCLE_REVISION,
        "down_revision": TARGET_REVISION,
        "TABLE": LIFECYCLE_TABLE,
        "ACTIVE_UNIQUE_INDEX": LIFECYCLE_ACTIVE_UNIQUE_INDEX,
        "LIFECYCLE_CHECK": LIFECYCLE_CHECK_CONSTRAINT,
        "SUPERSEDES_FK": LIFECYCLE_SUPERSEDES_FK,
    }
    lifecycle_actual = {
        "revision": module_025.revision,
        "down_revision": module_025.down_revision,
        "TABLE": getattr(module_025, "TABLE", None),
        "ACTIVE_UNIQUE_INDEX": getattr(module_025, "ACTIVE_UNIQUE_INDEX", None),
        "LIFECYCLE_CHECK": getattr(module_025, "LIFECYCLE_CHECK", None),
        "SUPERSEDES_FK": getattr(module_025, "SUPERSEDES_FK", None),
    }
    if lifecycle_actual != lifecycle_approved:
        raise PreFlightError(f"Migration 025 is not the approved configuration: {lifecycle_actual}")

    approved = {
        "revision": TARGET_REVISION,
        "down_revision": SOURCE_REVISION,
        "PARENT_CODE": PARENT_CODE,
        "NODE_CODE": NODE_CODE,
        "NODE_NAME": NODE_EXPECTED["name"],
        "NODE_DESCRIPTION": NODE_EXPECTED["description"],
        "NODE_TYPE": NODE_EXPECTED["node_type"],
    }
    actual = {
        "revision": module_024.revision,
        "down_revision": module_024.down_revision,
        "PARENT_CODE": getattr(module_024, "PARENT_CODE", None),
        "NODE_CODE": getattr(module_024, "NODE_CODE", None),
        "NODE_NAME": getattr(module_024, "NODE_NAME", None),
        "NODE_DESCRIPTION": getattr(module_024, "NODE_DESCRIPTION", None),
        "NODE_TYPE": getattr(module_024, "NODE_TYPE", None),
    }
    if actual != approved:
        raise PreFlightError(f"Migration 024 is not the approved configuration: {actual}")

    script = ScriptDirectory.from_config(_alembic_config())
    heads = tuple(script.get_heads())
    if heads != (EXPECTED_SINGLE_HEAD,):
        raise PreFlightError(f"Alembic chain must have a single expected head, found {heads}")
    target = script.get_revision(TARGET_REVISION)
    if target.down_revision != SOURCE_REVISION:
        raise PreFlightError("Alembic ScriptDirectory does not chain 024 onto 023")
    lifecycle_revision = script.get_revision(LIFECYCLE_REVISION)
    if lifecycle_revision.down_revision != TARGET_REVISION:
        raise PreFlightError("Alembic ScriptDirectory does not chain 025 onto 024")

    return {
        "heads": heads,
        "target_down_revision": target.down_revision,
        "lifecycle_down_revision": lifecycle_revision.down_revision,
    }


# --------------------------------------------------------------------------- #
# 3 + 5. Read-only snapshot
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Snapshot:
    """Sanitised, secret-free view of the state relevant to this executor."""

    revision: str | None
    catalog_nodes: tuple[dict[str, Any], ...]
    questions: dict[int, dict[str, Any]]
    classification_count: int
    content_question_link_count: int


def _catalog_index(snapshot: Snapshot) -> dict[str, dict[str, Any]]:
    return {node["code"]: node for node in snapshot.catalog_nodes}


def _sanitize_node(node: CatalogNode, by_id: dict[Any, CatalogNode]) -> dict[str, Any]:
    return {
        "code": node.code,
        "name": node.name,
        "description": node.description,
        "node_type": node.node_type,
        "active": bool(node.active),
        "position": node.position,
        "parent_code": by_id[node.parent_id].code if node.parent_id in by_id else None,
        "root_code": by_id[node.root_id].code if node.root_id in by_id else None,
    }


def _sanitize_classification(record: PedagogicalClassification) -> dict[str, Any]:
    metadata = record.metadata_ or {}
    return {
        "id": str(record.id),
        "question_version_id": str(record.question_version_id),
        "status": record.status,
        "source": record.source,
        "discipline": record.discipline,
        "content": record.content,
        "subcontent": record.subcontent,
        "model_version": record.model_version,
        "prompt_version": record.prompt_version,
        "taxonomy_version": metadata.get("taxonomy_version"),
        "classification_mode": metadata.get("classification_mode"),
        "input_hash": metadata.get("input_hash"),
        "output_hash": metadata.get("output_hash"),
        "selected_candidate_rank": metadata.get("selected_candidate_rank"),
        "recovered_candidate_count": len(metadata.get("recovered_candidates") or []),
        "has_reclassification_audit": bool(metadata.get("reclassification")),
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


async def _resolve_question_version(session: Any, number: int) -> QuestionVersion | None:
    enforce_question_scope([number])
    return await session.scalar(
        select(QuestionVersion)
        .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
        .where(BookletQuestion.official_number == number)
    )


async def _read_alembic_revision(session: Any) -> str | None:
    rows = list(
        (await session.execute(text("SELECT version_num FROM alembic_version")))
        .scalars()
        .all()
    )
    if len(rows) != 1:
        raise PreFlightError(f"Alembic revision state is not singular: {rows}")
    return rows[0]


async def read_snapshot(session: Any, *, revision: str | None) -> Snapshot:
    """Pure read. Callers pass the already-read Alembic revision explicitly."""
    nodes = list(
        (await session.scalars(select(CatalogNode).order_by(CatalogNode.code))).all()
    )
    by_id = {node.id: node for node in nodes}
    catalog = tuple(_sanitize_node(node, by_id) for node in nodes)

    questions: dict[int, dict[str, Any]] = {}
    for number in TARGET_QUESTIONS:
        version = await _resolve_question_version(session, number)
        entry: dict[str, Any] = {"present": version is not None}
        if version is not None:
            sibling_versions = list(
                (
                    await session.scalars(
                        select(QuestionVersion).where(
                            QuestionVersion.question_id == version.question_id
                        )
                    )
                ).all()
            )
            classifications: list[PedagogicalClassification] = []
            for sibling in sibling_versions:
                classifications.extend(
                    (
                        await session.scalars(
                            select(PedagogicalClassification)
                            .where(
                                PedagogicalClassification.question_version_id == sibling.id
                            )
                            .order_by(PedagogicalClassification.created_at)
                        )
                    ).all()
                )
            sanitized = [_sanitize_classification(item) for item in classifications]
            entry.update(
                {
                    "question_id": str(version.question_id),
                    "question_version_id": str(version.id),
                    "version_count": len(sibling_versions),
                    "content_hash": version.content_hash,
                    "content_available": bool(
                        (version.canonical_text or version.statement or "").strip()
                    ),
                    "classifications": sanitized,
                    "historical_023": [
                        row for row in sanitized if row["taxonomy_version"] == SOURCE_REVISION
                    ],
                    "target_024": [
                        row for row in sanitized if row["taxonomy_version"] == TARGET_REVISION
                    ],
                }
            )
        questions[number] = entry

    classification_count = int(
        await session.scalar(select(func.count()).select_from(PedagogicalClassification))
        or 0
    )
    link_count = int(
        await session.scalar(select(func.count()).select_from(ContentQuestionLink)) or 0
    )
    return Snapshot(revision, catalog, questions, classification_count, link_count)


async def capture_preflight_snapshot(session: Any) -> Snapshot:
    """Explicitly read-only snapshot for the pre-flight phase."""
    await session.execute(text("SET TRANSACTION READ ONLY"))
    revision = await _read_alembic_revision(session)
    return await read_snapshot(session, revision=revision)


async def read_state(session: Any) -> Snapshot:
    revision = await _read_alembic_revision(session)
    return await read_snapshot(session, revision=revision)


def preflight_observations(snapshot: Snapshot) -> dict[str, Any]:
    nodes = _catalog_index(snapshot)
    parent = nodes.get(PARENT_CODE)
    kinetics = nodes.get(NODE_CODE)
    return {
        "FOUND_REVISION": snapshot.revision,
        "EXPECTED_REVISION": SOURCE_REVISION,
        "CATALOG_NODE_COUNT": len(snapshot.catalog_nodes),
        "PARENT_EXISTS": parent is not None,
        "PARENT_TYPE": parent.get("node_type") if parent else None,
        "PARENT_ACTIVE": parent.get("active") if parent else None,
        "PARENT_UNDER": parent.get("parent_code") if parent else None,
        "KINETICS_NODE_EXISTS": kinetics is not None,
        "KINETICS_NODE_STATE": kinetics,
        "QUESTIONS": {
            number: {
                "present": (snapshot.questions.get(number) or {}).get("present", False),
                "question_version_id": (snapshot.questions.get(number) or {}).get(
                    "question_version_id"
                ),
                "content_available": (snapshot.questions.get(number) or {}).get(
                    "content_available"
                ),
                "historical_023_count": len(
                    (snapshot.questions.get(number) or {}).get("historical_023", [])
                ),
                "target_024_count": len(
                    (snapshot.questions.get(number) or {}).get("target_024", [])
                ),
            }
            for number in TARGET_QUESTIONS
        },
        "CLASSIFICATION_COUNT": snapshot.classification_count,
        "CONTENT_QUESTION_LINK_COUNT": snapshot.content_question_link_count,
    }


# --------------------------------------------------------------------------- #
# Pre-flight / post-migration / rollback validators (pure)
# --------------------------------------------------------------------------- #


def validate_preflight(snapshot: Snapshot) -> None:
    if snapshot.revision != SOURCE_REVISION:
        raise PreFlightError(f"Unexpected Alembic revision: {snapshot.revision!r}")

    nodes = _catalog_index(snapshot)
    parent = nodes.get(PARENT_CODE)
    if parent is None:
        raise PreFlightError("Taxonomy parent CHEMISTRY-PHYSICAL is missing")
    if parent["node_type"] != "AREA":
        raise PreFlightError("Taxonomy parent CHEMISTRY-PHYSICAL is not an AREA")
    if not parent["active"]:
        raise PreFlightError("Taxonomy parent CHEMISTRY-PHYSICAL is inactive")
    if parent["parent_code"] != PARENT_PARENT_CODE:
        raise PreFlightError("Taxonomy parent CHEMISTRY-PHYSICAL is not under CHEMISTRY")

    kinetics = nodes.get(NODE_CODE)
    if kinetics is not None:
        expected = {**NODE_EXPECTED, "parent_code": PARENT_CODE}
        if any(kinetics.get(key) != value for key, value in expected.items()):
            raise PreFlightError(
                "Pre-existing CHEMISTRY-PHYSICAL-KINETICS node is incompatible with the "
                "approved state"
            )

    for number in TARGET_QUESTIONS:
        question = snapshot.questions.get(number) or {}
        if not question.get("present"):
            raise PreFlightError(f"Official question is unavailable: {number}")
        if not question.get("question_version_id"):
            raise PreFlightError(f"QuestionVersion is unavailable: {number}")
        if not question.get("content_available"):
            raise PreFlightError(f"Question content is unavailable: {number}")
        if question.get("target_024"):
            raise PreFlightError(
                f"An INITIAL/024 proposal already exists for {number}; a partial prior "
                "run must be reviewed before proceeding"
            )
        if question.get("historical_023"):
            raise PreFlightError(
                f"Question {number} has a historical 023 proposal; the RECLASSIFICATION "
                "flow (not INITIAL) applies and this must be reviewed"
            )
    # INITIAL never *requires* a historical proposal: its absence is the expected
    # condition and is deliberately not treated as an error here.


def _assert_preexisting_questions_unchanged(before: Snapshot, after: Snapshot) -> None:
    for number in TARGET_QUESTIONS:
        before_rows = {
            row["id"]: row
            for row in (before.questions.get(number) or {}).get("classifications", [])
        }
        after_rows = {
            row["id"]: row
            for row in (after.questions.get(number) or {}).get("classifications", [])
        }
        for row_id, row in before_rows.items():
            if row_id not in after_rows:
                raise PostMigrationError(
                    f"A pre-existing classification vanished for question {number}: {row_id}"
                )
            if after_rows[row_id] != row:
                raise PostMigrationError(
                    f"A pre-existing classification changed for question {number}: {row_id}"
                )


def validate_post_migration(before: Snapshot, after: Snapshot) -> None:
    if after.revision != TARGET_REVISION:
        raise PostMigrationError("Alembic revision was not advanced to 024")

    before_nodes = _catalog_index(before)
    after_nodes = _catalog_index(after)
    if set(after_nodes) != set(before_nodes) | {NODE_CODE}:
        raise PostMigrationError(
            "Catalog node set changed beyond the single expected addition"
        )
    for code, node in before_nodes.items():
        if after_nodes.get(code) != node:
            raise PostMigrationError(
                f"Pre-existing CatalogNode was altered during migration: {code}"
            )
    new_node = after_nodes.get(NODE_CODE)
    expected = {**NODE_EXPECTED, "parent_code": PARENT_CODE}
    if new_node is None or any(
        new_node.get(key) != value for key, value in expected.items()
    ):
        raise PostMigrationError(
            "The new CHEMISTRY-PHYSICAL-KINETICS node is missing or incompatible"
        )
    if before.content_question_link_count != after.content_question_link_count:
        raise PostMigrationError("ContentQuestionLink count changed during migration")
    if before.classification_count != after.classification_count:
        raise PostMigrationError(
            "PedagogicalClassification count changed during migration"
        )
    _assert_preexisting_questions_unchanged(before, after)


def kinetics_has_dependencies(snapshot: Snapshot) -> bool:
    """A freshly created kinetics node only gains dependents via ContentQuestionLink.

    Any positive link count relative to the pre-migration baseline is treated as a
    dependency that makes a blind downgrade unsafe; the migration's own downgrade
    guard is the second line of defence.
    """
    return snapshot.content_question_link_count > 0


def validate_rollback(before: Snapshot, reverted: Snapshot) -> None:
    if reverted.revision != SOURCE_REVISION:
        raise RollbackError("Alembic revision did not return to 023 after downgrade")
    reverted_nodes = _catalog_index(reverted)
    if NODE_CODE in reverted_nodes:
        raise RollbackError("CHEMISTRY-PHYSICAL-KINETICS still present after downgrade")
    before_nodes = _catalog_index(before)
    if set(reverted_nodes) != set(before_nodes):
        raise RollbackError("Catalog node set differs from the pre-migration state")
    for code, node in before_nodes.items():
        if reverted_nodes.get(code) != node:
            raise RollbackError(f"Pre-existing CatalogNode altered by downgrade: {code}")
    if reverted.content_question_link_count != before.content_question_link_count:
        raise RollbackError("ContentQuestionLink count changed by downgrade")
    if reverted.classification_count != before.classification_count:
        raise RollbackError("PedagogicalClassification count changed by downgrade")
    _assert_preexisting_questions_unchanged(before, reverted)


@dataclass(frozen=True)
class RollbackReport:
    attempted: bool
    status: str  # NOT_APPLIED | COMPLETED | BLOCKED_DEPENDENCIES | UNSAFE
    reason: str


async def perform_rollback(
    *,
    migration_applied_by_executor: bool,
    before: Snapshot,
    run_downgrade: Callable[[], None],
    snapshot_reader: Callable[[], Awaitable[Snapshot]],
) -> RollbackReport:
    """Attempt a real downgrade only when this executor applied the migration."""
    if not migration_applied_by_executor:
        return RollbackReport(
            False,
            "NOT_APPLIED",
            "Migration was not applied by this executor; no rollback attempted.",
        )
    pre_rollback = await snapshot_reader()
    if kinetics_has_dependencies(pre_rollback):
        return RollbackReport(
            True,
            "BLOCKED_DEPENDENCIES",
            "Kinetics node has dependent references; a downgrade is unsafe.",
        )
    try:
        run_downgrade()
    except Exception as exc:  # includes the migration's own "with dependencies" guard
        return RollbackReport(
            True,
            "BLOCKED_DEPENDENCIES",
            f"Downgrade was refused: {type(exc).__name__}: {exc}",
        )
    reverted = await snapshot_reader()
    try:
        validate_rollback(before, reverted)
    except RollbackError as exc:
        return RollbackReport(True, "UNSAFE", str(exc))
    return RollbackReport(
        True, "COMPLETED", "Migration reverted to 023 with no residual changes."
    )


# --------------------------------------------------------------------------- #
# 6. Migration execution helpers (real run only)
# --------------------------------------------------------------------------- #


def _alembic_config_with_db() -> Config:
    config = _alembic_config()
    config.set_main_option("sqlalchemy.url", get_database_url())
    return config


def run_upgrade(target: str = TARGET_REVISION) -> None:
    from alembic import command

    command.upgrade(_alembic_config_with_db(), target)


def run_downgrade(target: str = SOURCE_REVISION) -> None:
    from alembic import command

    command.downgrade(_alembic_config_with_db(), target)


# --------------------------------------------------------------------------- #
# 4 (gate) Human-safe approval
# --------------------------------------------------------------------------- #


def approval_granted(
    environ: dict[str, str] | None = None,
    *,
    interactive: bool | None = None,
    prompt: Callable[[str], str] = input,
) -> bool:
    """Fail closed: only an exact token (env or typed) grants approval."""
    source = os.environ if environ is None else environ
    token = source.get(APPROVAL_ENV_VAR)
    if token is not None:
        return token == APPROVAL_TOKEN
    is_interactive = sys.stdin.isatty() if interactive is None else interactive
    if not is_interactive:
        return False
    typed = prompt(
        f"Type '{APPROVAL_TOKEN}' to approve migration 024 + INITIAL classification: "
    ).strip()
    return typed == APPROVAL_TOKEN


# --------------------------------------------------------------------------- #
# 7 + 8. INITIAL classification with idempotency verification
# --------------------------------------------------------------------------- #


class SingleCallOpenAIProvider:
    """Delegates to the real OpenAI provider, allowing exactly one call."""

    provider = "openai"

    def __init__(self) -> None:
        self._delegate = OpenAIProvider()
        self.calls = 0

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        if self.calls:
            raise RuntimeError(
                "Only one OpenAI call is permitted per question in this executor"
            )
        self.calls += 1
        return await self._delegate.generate(request)


def _assert_initial_record(record: PedagogicalClassification, number: int) -> None:
    metadata = record.metadata_ or {}
    if metadata.get("classification_mode") != CLASSIFICATION_MODE:
        raise PreFlightError(f"Classification for {number} is not in INITIAL mode")
    if metadata.get("taxonomy_version") != TARGET_REVISION:
        raise PreFlightError(f"Classification for {number} is not bound to taxonomy 024")
    if metadata.get("reclassification") is not None:
        raise PreFlightError(
            f"Classification for {number} carries a reclassification audit; INITIAL must not"
        )
    if not metadata.get("input_hash") or not metadata.get("output_hash"):
        raise PreFlightError(f"Classification for {number} is missing deterministic hashes")


async def _existing_initial_proposal(
    session: Any, version_id: Any
) -> PedagogicalClassification | None:
    return await session.scalar(
        select(PedagogicalClassification)
        .where(
            PedagogicalClassification.question_version_id == version_id,
            PedagogicalClassification.model_version == CLASSIFIER_VERSION,
            PedagogicalClassification.prompt_version == PROMPT_VERSION,
        )
        .order_by(PedagogicalClassification.created_at.desc())
    )


ProviderFactory = Callable[[], Any]


async def classify_initial(
    session: Any,
    number: int,
    *,
    provider_factory: ProviderFactory = SingleCallOpenAIProvider,
) -> dict[str, Any]:
    """Run the INITIAL flow for one permitted question and verify idempotency.

    Uses ``classify_initial_with_provider`` exclusively. The service is invoked a
    second time with a fresh provider; a duplicate row, a second provider call, or
    any hash/metadata drift is a hard failure.
    """
    enforce_question_scope([number])
    version = await _resolve_question_version(session, number)
    if version is None:
        raise PreFlightError(
            f"Official question is unavailable at classification time: {number}"
        )

    service = ClassificationProposalService(session)
    already_existed = await _existing_initial_proposal(session, version.id) is not None

    provider_one = provider_factory()
    first = await service.classify_initial_with_provider(
        version.id,
        ProviderRouter(text_providers=[provider_one], embedding_providers=[]),
        target_taxonomy_version=TARGET_REVISION,
        classifier_version=CLASSIFIER_VERSION,
        prompt_version=PROMPT_VERSION,
    )
    _assert_initial_record(first, number)

    provider_two = provider_factory()
    second = await service.classify_initial_with_provider(
        version.id,
        ProviderRouter(text_providers=[provider_two], embedding_providers=[]),
        target_taxonomy_version=TARGET_REVISION,
        classifier_version=CLASSIFIER_VERSION,
        prompt_version=PROMPT_VERSION,
    )
    first_md = first.metadata_ or {}
    second_md = second.metadata_ or {}
    if str(second.id) != str(first.id):
        raise PreFlightError(f"Idempotency violated for {number}: a second row was created")
    if getattr(provider_two, "calls", 0) != 0:
        raise PreFlightError(
            f"Idempotency violated for {number}: the provider was called again"
        )
    if first_md.get("input_hash") != second_md.get("input_hash"):
        raise PreFlightError(f"Idempotency violated for {number}: input_hash changed")
    if first_md.get("output_hash") != second_md.get("output_hash"):
        raise PreFlightError(f"Idempotency violated for {number}: output_hash changed")
    if first_md.get("selected_candidate_rank") != second_md.get("selected_candidate_rank"):
        raise PreFlightError(f"Idempotency violated for {number}: selection changed")

    return {
        "question_number": number,
        "classification_id": str(first.id),
        "already_existed": already_existed,
        "provider_calls": getattr(provider_one, "calls", 0),
        "status": first.status,
        "content_code": first_md.get("content_code"),
        "selected_candidate_rank": first_md.get("selected_candidate_rank"),
        "input_hash": first_md.get("input_hash"),
        "output_hash": first_md.get("output_hash"),
        "taxonomy_version": first_md.get("taxonomy_version"),
        "classification_mode": first_md.get("classification_mode"),
    }


def validate_historical_integrity(before: Snapshot, after: Snapshot) -> None:
    """Only additive INITIAL/024 rows may appear; nothing historical may change."""
    for number in TARGET_QUESTIONS:
        before_rows = {
            row["id"]: row
            for row in (before.questions.get(number) or {}).get("classifications", [])
        }
        after_rows = {
            row["id"]: row
            for row in (after.questions.get(number) or {}).get("classifications", [])
        }
        for row_id, row in before_rows.items():
            if row_id not in after_rows:
                raise PreFlightError(
                    f"A pre-existing classification was removed for question {number}: {row_id}"
                )
            if after_rows[row_id] != row:
                raise PreFlightError(
                    f"A pre-existing classification was modified for question {number}: {row_id}"
                )
        for row_id in after_rows.keys() - before_rows.keys():
            added = after_rows[row_id]
            if (
                added["taxonomy_version"] != TARGET_REVISION
                or added["classification_mode"] != CLASSIFICATION_MODE
            ):
                raise PreFlightError(
                    f"A non-INITIAL classification was added for question {number}: {row_id}"
                )
        before_hist = {
            row["id"]: row
            for row in (before.questions.get(number) or {}).get("historical_023", [])
        }
        after_hist = {
            row["id"]: row
            for row in (after.questions.get(number) or {}).get("historical_023", [])
        }
        if before_hist != after_hist:
            raise PreFlightError(
                f"A historical 023 classification changed for question {number}"
            )
    if after.content_question_link_count != before.content_question_link_count:
        raise PreFlightError(
            "ContentQuestionLink count changed during INITIAL classification"
        )


# --------------------------------------------------------------------------- #
# Operator-facing sanitised PRE-FLIGHT report (shown before the approval gate)
# --------------------------------------------------------------------------- #


def _short_hash(value: Any) -> str | None:
    """Never expose a full hash: keep only a short, non-reversible prefix."""
    if not value:
        return None
    text_value = str(value)
    return text_value[:12] + "…" if len(text_value) > 12 else text_value


def _report_classification_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "status": row.get("status"),
        "source": row.get("source"),
        "discipline": row.get("discipline"),
        "content": row.get("content"),
        "subcontent": row.get("subcontent"),
        "taxonomy_version": row.get("taxonomy_version"),
        "classification_mode": row.get("classification_mode"),
        "model_version": row.get("model_version"),
        "prompt_version": row.get("prompt_version"),
        "selected_candidate_rank": row.get("selected_candidate_rank"),
        "input_hash_prefix": _short_hash(row.get("input_hash")),
        "output_hash_prefix": _short_hash(row.get("output_hash")),
        "has_reclassification_audit": row.get("has_reclassification_audit"),
        "created_at": row.get("created_at"),
    }


def build_preflight_report(
    snapshot: Snapshot,
    *,
    status: str,
    chain_info: dict[str, Any] | None,
    postgresql_reads: int,
) -> dict[str, Any]:
    """Build the sanitised operator report for the pre-flight phase.

    Contains only structural catalogue/question state, counts and revision ids.
    It never carries DATABASE_URL, credentials, the OpenAI key, full hashes, or
    question text (``canonical_text`` / ``statement``).
    """
    nodes = _catalog_index(snapshot)
    parent = nodes.get(PARENT_CODE)
    kinetics = nodes.get(NODE_CODE)

    def _question(number: int) -> dict[str, Any]:
        entry = snapshot.questions.get(number) or {}
        return {
            "present": entry.get("present", False),
            "question_version_id": entry.get("question_version_id"),
            "content_available": entry.get("content_available"),
            "content_hash_prefix": _short_hash(entry.get("content_hash")),
            "version_count": entry.get("version_count", 0),
            "existing_classification_count": len(entry.get("classifications", [])),
            "historical_023_count": len(entry.get("historical_023", [])),
            "target_024_count": len(entry.get("target_024", [])),
        }

    chain = {
        "status": "PASS" if chain_info else "UNKNOWN",
        "source_revision": SOURCE_REVISION,
        "target_revision": TARGET_REVISION,
        "chain_head": LIFECYCLE_REVISION,
        "heads": list(chain_info.get("heads", ())) if chain_info else [],
        "target_down_revision": (
            chain_info.get("target_down_revision") if chain_info else None
        ),
        "lifecycle_down_revision": (
            chain_info.get("lifecycle_down_revision") if chain_info else None
        ),
    }

    return {
        "PRE-FLIGHT": status,
        "SOURCE_REVISION": SOURCE_REVISION,
        "TARGET_REVISION": TARGET_REVISION,
        "CATALOG_NODE_COUNT": len(snapshot.catalog_nodes),
        "PARENT_EXISTS": parent is not None,
        "PARENT_CODE": PARENT_CODE,
        "PARENT_TYPE": parent.get("node_type") if parent else None,
        "PARENT_ACTIVE": parent.get("active") if parent else None,
        "KINETICS_NODE_EXISTS": kinetics is not None,
        "KINETICS_NODE_STATE": kinetics,
        "QUESTION_93": _question(93),
        "QUESTION_128": _question(128),
        "QUESTION_VERSION_COUNTS": {
            "93": (snapshot.questions.get(93) or {}).get("version_count", 0),
            "128": (snapshot.questions.get(128) or {}).get("version_count", 0),
        },
        "EXISTING_CLASSIFICATIONS": {
            "93": [
                _report_classification_row(row)
                for row in (snapshot.questions.get(93) or {}).get("classifications", [])
            ],
            "128": [
                _report_classification_row(row)
                for row in (snapshot.questions.get(128) or {}).get("classifications", [])
            ],
        },
        "HISTORICAL_INTEGRITY_STATUS": {
            "state": "BASELINE_RECORDED",
            "detail": (
                "Historical rows recorded now; integrity is enforced after "
                "classification by validate_historical_integrity."
            ),
            "historical_023_present": {
                "93": bool((snapshot.questions.get(93) or {}).get("historical_023")),
                "128": bool((snapshot.questions.get(128) or {}).get("historical_023")),
            },
            "target_024_present": {
                "93": bool((snapshot.questions.get(93) or {}).get("target_024")),
                "128": bool((snapshot.questions.get(128) or {}).get("target_024")),
            },
            "content_question_link_count": snapshot.content_question_link_count,
            "total_classification_count": snapshot.classification_count,
        },
        "MIGRATION_LOCAL_CHAIN": chain,
        "OPENAI_CALLS": 0,
        "POSTGRESQL_READS": postgresql_reads,
        "POSTGRESQL_WRITES": 0,
        "MIGRATION_EXECUTION": 0,
        "DATABASE_WRITES": 0,
        "UNEXPECTED_CHANGES": [],
    }


def emit_preflight_report(report: dict[str, Any]) -> None:
    """Print the sanitised PRE-FLIGHT report to stdout before the approval gate."""
    print("PHASE 9U.1 — PRODUCTION INITIAL CLASSIFICATION PRE-FLIGHT REPORT")
    for key, value in report.items():
        print(f"{key}: {json.dumps(value, ensure_ascii=True, sort_keys=True)}")
    sys.stdout.flush()


# --------------------------------------------------------------------------- #
# Orchestration (real production run only; never invoked by the test-suite)
# --------------------------------------------------------------------------- #


@dataclass
class RunResult:
    data: dict[str, Any] = field(default_factory=dict)

    def emit(self) -> None:
        print("PHASE 9U.1 — PRODUCTION INITIAL CLASSIFICATION EXECUTOR")
        for key, value in self.data.items():
            print(f"{key}: {json.dumps(value, ensure_ascii=True, sort_keys=True)}")


async def main() -> int:
    load_dotenv(ENV_FILE, override=False)
    result: dict[str, Any] = {
        "PHASE": "9U.1",
        "SOURCE_REVISION": SOURCE_REVISION,
        "TARGET_REVISION": TARGET_REVISION,
        "PRE_FLIGHT": "FAIL",
        "APPROVAL": "NOT_GRANTED",
        "MIGRATION": "NOT_RUN",
        "POST_MIGRATION": "NOT_RUN",
        "ROLLBACK": "NOT_NEEDED",
        "INITIAL_93": "NOT_RUN",
        "INITIAL_128": "NOT_RUN",
        "OPENAI_CALLS": 0,
        "POSTGRESQL_READS": 0,
        "POSTGRESQL_WRITES": 0,
        "MIGRATION_EXECUTION": 0,
        "DATABASE_WRITES": 0,
        "UNEXPECTED_CHANGES": [],
        "FINAL_DECISION": INITIAL_REVIEW_DECISION,
    }
    before: Snapshot | None = None
    migration_applied_by_executor = False
    try:
        validate_environment()
        chain_info = validate_local_migration_chain()

        engine = create_engine()
        factory = create_session_factory(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                async with session.begin():
                    before = await capture_preflight_snapshot(session)
                    result["POSTGRESQL_READS"] += 1

            # Validate, then ALWAYS show the operator a sanitised PRE-FLIGHT
            # report before the approval gate is reached.
            preflight_status = "FAIL"
            try:
                validate_preflight(before)
                preflight_status = "PASS"
            finally:
                emit_preflight_report(
                    build_preflight_report(
                        before,
                        status=preflight_status,
                        chain_info=chain_info,
                        postgresql_reads=result["POSTGRESQL_READS"],
                    )
                )
            result["PRE_FLIGHT"] = preflight_status
            if preflight_status != "PASS":
                raise PreFlightError(
                    "Pre-flight did not complete with PASS; the approval gate is not offered."
                )

            # The approval token is requested only now, after a PRE-FLIGHT: PASS
            # report has been printed above.
            if not approval_granted():
                raise ApprovalError(
                    "Human approval was not granted; no migration or write was performed."
                )
            result["APPROVAL"] = "GRANTED"

            run_upgrade(TARGET_REVISION)
            migration_applied_by_executor = True
            result["MIGRATION"] = "APPLIED"
            result["MIGRATION_EXECUTION"] = 1
            result["POSTGRESQL_WRITES"] += 1
            result["DATABASE_WRITES"] += 1

            async with factory() as session:
                async with session.begin():
                    after_migration = await read_state(session)
                    result["POSTGRESQL_READS"] += 1

            try:
                validate_post_migration(before, after_migration)
                result["POST_MIGRATION"] = "PASS"
            except PostMigrationError as exc:
                result["POST_MIGRATION"] = "FAIL"
                result["UNEXPECTED_CHANGES"].append(str(exc))

                async def _reader() -> Snapshot:
                    async with factory() as rollback_session:
                        async with rollback_session.begin():
                            return await read_state(rollback_session)

                report = await perform_rollback(
                    migration_applied_by_executor=migration_applied_by_executor,
                    before=before,
                    run_downgrade=lambda: run_downgrade(SOURCE_REVISION),
                    snapshot_reader=_reader,
                )
                result["ROLLBACK"] = report.status
                result["ROLLBACK_REASON"] = report.reason
                if report.status == "COMPLETED":
                    result["POSTGRESQL_WRITES"] += 1
                    result["DATABASE_WRITES"] += 1
                result["FINAL_DECISION"] = MIGRATION_REVIEW_DECISION
                raise PostMigrationError(f"{exc} (rollback: {report.status})")

            outcomes: dict[int, dict[str, Any] | None] = {}
            for number in TARGET_QUESTIONS:
                try:
                    async with factory() as session:
                        summary = await classify_initial(session, number)
                    result[f"INITIAL_{number}"] = "PASS"
                    result["OPENAI_CALLS"] += summary["provider_calls"]
                    result["POSTGRESQL_WRITES"] += 1
                    result["DATABASE_WRITES"] += 1
                    outcomes[number] = summary
                except Exception as exc:  # partial-failure guard: stop, do not mask
                    result[f"INITIAL_{number}"] = "FAIL"
                    result[f"INITIAL_{number}_ERROR"] = f"{type(exc).__name__}: {exc}"
                    outcomes[number] = None
                    break

            if len(outcomes) < len(TARGET_QUESTIONS) or any(
                value is None for value in outcomes.values()
            ):
                result["FINAL_DECISION"] = INITIAL_REVIEW_DECISION
            else:
                async with factory() as session:
                    async with session.begin():
                        final_snapshot = await read_state(session)
                        result["POSTGRESQL_READS"] += 1
                validate_historical_integrity(before, final_snapshot)
                result["HISTORICAL_INTEGRITY"] = "PASS"
                result["FINAL_DECISION"] = SUCCESS_DECISION
        finally:
            await engine.dispose()
    except ExecutorError as exc:
        result["FAILURE_TYPE"] = type(exc).__name__
        result["FAILURE_MESSAGE"] = str(exc)
        if before is not None:
            result["PREFLIGHT_OBSERVATIONS"] = preflight_observations(before)
        result.setdefault("FINAL_DECISION", INITIAL_REVIEW_DECISION)
    except Exception as exc:  # unknown, still fail closed
        result["FAILURE_TYPE"] = type(exc).__name__
        result["FAILURE_MESSAGE"] = str(exc)
        result["FINAL_DECISION"] = INITIAL_REVIEW_DECISION

    RunResult(result).emit()
    return 0 if result["FINAL_DECISION"] == SUCCESS_DECISION else 1


if __name__ == "__main__":  # pragma: no cover - manual entrypoint only
    raise SystemExit(asyncio.run(main()))
