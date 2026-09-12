"""Read-only final post-production audit of the Phase 9U.1 INITIAL classification.

This module NEVER writes: it opens a single explicitly READ ONLY PostgreSQL
session, reads state through the real SQLAlchemy models, and produces a
sanitised report. It runs no migration, no Alembic command, no OpenAI call, does
not invoke the INITIAL or RECLASSIFICATION service entrypoints, and never runs
the ``phase9u1_production`` executor.

Column vs metadata provenance is resolved from the ORM models themselves
(``sqlalchemy.inspect``) and reported explicitly, so no audit check assumes a
column name that does not exist. On ``PedagogicalClassification`` the real
columns include ``model_version`` (the classifier version), ``prompt_version``,
``status`` and ``created_at``; the taxonomy version, classification mode,
``*_code`` values and the hashes live inside the ``metadata`` JSON blob
(Python attribute ``metadata_``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import func, inspect as sa_inspect, select, text

from agente_ia_edu.db.models import (
    BookletQuestion,
    CatalogNode,
    ContentQuestionLink,
    ModificationProposal,
    PedagogicalClassification,
    QuestionVersion,
)
from agente_ia_edu.db.session import get_database_url

# --------------------------------------------------------------------------- #
# Constants (kept local so the audit does not depend on the executor module)
# --------------------------------------------------------------------------- #

SOURCE_REVISION = "023_curriculum_taxonomy"
TARGET_REVISION = "024_chemistry_kinetics"

TARGET_QUESTIONS: tuple[int, ...] = (93, 128)
ALLOWED_QUESTION_NUMBERS = frozenset(TARGET_QUESTIONS)

PARENT_PARENT_CODE = "CHEMISTRY"
PARENT_CODE = "CHEMISTRY-PHYSICAL"
NODE_CODE = "CHEMISTRY-PHYSICAL-KINETICS"
NODE_NAME = "Cinética química"
NODE_DESCRIPTION = "Estudo da velocidade das reações químicas."
NODE_TYPE = "CONTENT"

EXPECTED_CLASSIFIER_VERSION = "phase9u1-initial-v1"
EXPECTED_PROMPT_VERSION = "phase9t3-kinetics-v1"
EXPECTED_CLASSIFICATION_MODE = "INITIAL"

SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
BASELINE_ENV_VAR = "PHASE9U1_AUDIT_BASELINE"

AUDIT_PASS = "PRODUCTION_AUDIT_PASS"
AUDIT_REVIEW = "PRODUCTION_AUDIT_NEEDS_REVIEW"

PROVABLE_FIELDS = (
    "ALEMBIC_REVISION",
    "TAXONOMY_NODE",
    "TAXONOMY_PARENT",
    "QUESTION_93",
    "QUESTION_128",
    "INITIAL_CLASSIFICATION_93",
    "INITIAL_CLASSIFICATION_128",
    "CLASSIFICATION_DUPLICATION",
    "HASH_INTEGRITY",
)
TRISTATE_FIELDS = (
    "HISTORICAL_INTEGRITY",
    "MODIFICATION_PROPOSAL_INTEGRITY",
    "CONTENT_LINK_INTEGRITY",
    "QUESTION_SCOPE",
)


class AuditError(RuntimeError):
    """Deliberate fail-closed abort of the audit."""


def enforce_question_scope(numbers) -> None:
    invalid = sorted({int(n) for n in numbers} - ALLOWED_QUESTION_NUMBERS)
    if invalid:
        raise AuditError(f"Audit scope is limited to 93/128; refused: {invalid}")


# --------------------------------------------------------------------------- #
# Sanitisation helpers
# --------------------------------------------------------------------------- #


def _short_hash(value: Any) -> str | None:
    if not value:
        return None
    text_value = str(value)
    return text_value[:12] + "…" if len(text_value) > 12 else text_value


def _is_sha256(value: Any) -> bool:
    return bool(value) and bool(SHA256_RE.match(str(value)))


def _scrub(message: str) -> str:
    """Remove any DSN / known secret from a free-text message."""
    scrubbed = message
    for secret in (os.getenv("DATABASE_URL"), os.getenv("OPENAI_API_KEY")):
        if secret:
            scrubbed = scrubbed.replace(secret, "[REDACTED]")
    scrubbed = re.sub(
        r"(?:postgres(?:ql)?|https?)(?:\+[a-z0-9]+)?://[^\s]*",
        "[REDACTED_URL]",
        scrubbed,
        flags=re.IGNORECASE,
    )
    return scrubbed


# --------------------------------------------------------------------------- #
# Model provenance (column vs metadata key) — resolved from the ORM itself
# --------------------------------------------------------------------------- #


def classification_column_keys() -> set[str]:
    return {column.key for column in sa_inspect(PedagogicalClassification).columns}


def resolve_field_sources() -> dict[str, str]:
    """Map each audited field to 'column:<name>' or 'metadata_key:<name>'."""
    columns = classification_column_keys()

    def column_or_meta(column_name: str, meta_key: str) -> str:
        return f"column:{column_name}" if column_name in columns else f"metadata_key:{meta_key}"

    return {
        "classification_id": column_or_meta("id", "id"),
        "question_version_id": column_or_meta("question_version_id", "question_version_id"),
        "status": column_or_meta("status", "status"),
        "created_at": column_or_meta("created_at", "created_at"),
        "classifier_version": column_or_meta("model_version", "model_version"),
        "prompt_version": column_or_meta("prompt_version", "prompt_version"),
        "taxonomy_version": column_or_meta("taxonomy_version", "taxonomy_version"),
        "classification_mode": column_or_meta("classification_mode", "classification_mode"),
        "primary_discipline_code": column_or_meta("discipline_code", "discipline_code"),
        "primary_area_code": column_or_meta("area_code", "area_code"),
        "primary_content_code": column_or_meta("content_code", "content_code"),
        "primary_subcontent_code": column_or_meta("subcontent_code", "subcontent_code"),
        "input_hash": column_or_meta("input_hash", "input_hash"),
        "output_hash": column_or_meta("output_hash", "output_hash"),
        "question_content_hash": column_or_meta("question_content_hash", "question_content_hash"),
        "metadata_blob_column": "column:metadata" if "metadata" in columns else "MISSING",
        "legacy_discipline_column": column_or_meta("discipline", "discipline_code"),
        "legacy_content_column": column_or_meta("content", "content_code"),
        "legacy_subcontent_column": column_or_meta("subcontent", "subcontent_code"),
    }


# --------------------------------------------------------------------------- #
# Sanitised row shapes
# --------------------------------------------------------------------------- #


def sanitize_classification(record: PedagogicalClassification) -> dict[str, Any]:
    metadata = record.metadata_ or {}
    input_hash = metadata.get("input_hash")
    output_hash = metadata.get("output_hash")
    content_hash = metadata.get("question_content_hash")
    key_parts = [
        str(record.question_version_id),
        metadata.get("taxonomy_version"),
        record.model_version,
        record.prompt_version,
        input_hash,
    ]
    fingerprint = _digest16(key_parts)
    return {
        "classification_id": str(record.id),
        "question_version_id": str(record.question_version_id),
        "status": record.status,
        "source": record.source,
        "classifier_version": record.model_version,  # column model_version
        "prompt_version": record.prompt_version,  # column prompt_version
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "taxonomy_version": metadata.get("taxonomy_version"),  # metadata
        "classification_mode": metadata.get("classification_mode"),  # metadata
        "primary_discipline_code": metadata.get("discipline_code"),  # metadata
        "primary_area_code": metadata.get("area_code"),  # metadata
        "primary_content_code": metadata.get("content_code"),  # metadata
        "primary_subcontent_code": metadata.get("subcontent_code"),  # metadata
        "legacy_discipline_column": record.discipline,
        "legacy_content_column": record.content,
        "legacy_subcontent_column": record.subcontent,
        "metadata_keys": sorted(metadata.keys()),
        "has_input_hash": bool(input_hash),
        "has_output_hash": bool(output_hash),
        "has_question_content_hash": bool(content_hash),
        "input_hash_prefix": _short_hash(input_hash),
        "output_hash_prefix": _short_hash(output_hash),
        "question_content_hash_prefix": _short_hash(content_hash),
        "input_hash_is_sha256": _is_sha256(input_hash),
        "output_hash_is_sha256": _is_sha256(output_hash),
        "question_content_hash_is_sha256": _is_sha256(content_hash),
        "dedup_fingerprint": fingerprint,
        "has_reclassification_audit": bool(metadata.get("reclassification")),
    }


def _digest16(parts: list[Any]) -> str:
    import hashlib

    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def sanitize_modification_proposal(record: ModificationProposal) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "original_question_version_id": str(record.original_question_version_id),
        "status": record.status,
        "modification_type": record.modification_type,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "cancelled_at": record.cancelled_at.isoformat() if record.cancelled_at else None,
        "accepted_at": record.accepted_at.isoformat() if record.accepted_at else None,
        "updated_after_create": bool(
            record.updated_at and record.created_at and record.updated_at != record.created_at
        ),
    }


# --------------------------------------------------------------------------- #
# Audit state (only this is filled from the database)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AuditState:
    alembic_revision_rows: tuple[str, ...]
    parent_node: dict[str, Any] | None
    kinetics_node: dict[str, Any] | None
    kinetics_code_count: int
    questions: dict[int, dict[str, Any]]
    classifications: dict[int, tuple[dict[str, Any], ...]]
    tagged_9u1_rows: tuple[dict[str, Any], ...]
    modification_proposals: dict[int, tuple[dict[str, Any], ...]]
    content_link_counts: dict[str, int]
    field_sources: dict[str, str] = field(default_factory=resolve_field_sources)


# --------------------------------------------------------------------------- #
# Database collection (single READ ONLY transaction)
# --------------------------------------------------------------------------- #


async def _node_view(session: Any, code: str) -> dict[str, Any] | None:
    node = await session.scalar(select(CatalogNode).where(CatalogNode.code == code))
    if node is None:
        return None
    parent_code = None
    if node.parent_id is not None:
        parent = await session.get(CatalogNode, node.parent_id)
        parent_code = parent.code if parent else None
    return {
        "code": node.code,
        "node_type": node.node_type,
        "active": bool(node.active),
        "name": node.name,
        "description": node.description,
        "position": node.position,
        "parent_code": parent_code,
    }


async def _resolve_official_numbers(session: Any, question_id: Any) -> set[int]:
    numbers = (
        await session.scalars(
            select(BookletQuestion.official_number)
            .join(QuestionVersion, QuestionVersion.id == BookletQuestion.question_version_id)
            .where(
                QuestionVersion.question_id == question_id,
                BookletQuestion.official_number.is_not(None),
            )
        )
    ).all()
    return {int(number) for number in numbers}


async def _question_view(session: Any, number: int) -> dict[str, Any]:
    enforce_question_scope([number])
    anchor = await session.scalar(
        select(QuestionVersion)
        .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
        .where(BookletQuestion.official_number == number)
    )
    if anchor is None:
        return {"present": False}
    versions = list(
        (
            await session.scalars(
                select(QuestionVersion).where(QuestionVersion.question_id == anchor.question_id)
            )
        ).all()
    )
    return {
        "present": True,
        "question_id": str(anchor.question_id),
        "anchor_question_version_id": str(anchor.id),
        "version_count": len(versions),
        "version_ids": [str(item.id) for item in versions],
        "content_available": bool((anchor.canonical_text or anchor.statement or "").strip()),
        "content_hash_present": bool(anchor.content_hash),
        "content_hash_prefix": _short_hash(anchor.content_hash),
        "content_hash_is_sha256": _is_sha256(anchor.content_hash),
    }


async def _classifications_for(session: Any, version_ids: list[str]) -> list[dict[str, Any]]:
    if not version_ids:
        return []
    records = list(
        (
            await session.scalars(
                select(PedagogicalClassification)
                .where(PedagogicalClassification.question_version_id.in_(version_ids))
                .order_by(PedagogicalClassification.created_at)
            )
        ).all()
    )
    return [sanitize_classification(record) for record in records]


async def _modification_proposals_for(
    session: Any, version_ids: list[str]
) -> list[dict[str, Any]]:
    if not version_ids:
        return []
    records = list(
        (
            await session.scalars(
                select(ModificationProposal)
                .where(ModificationProposal.original_question_version_id.in_(version_ids))
                .order_by(ModificationProposal.created_at)
            )
        ).all()
    )
    return [sanitize_modification_proposal(record) for record in records]


async def collect_state(session: Any) -> AuditState:
    """Populate the audit state from a single READ ONLY transaction."""
    await session.execute(text("SET TRANSACTION READ ONLY"))

    revision_rows = tuple(
        (await session.execute(text("SELECT version_num FROM alembic_version")))
        .scalars()
        .all()
    )

    parent_node = await _node_view(session, PARENT_CODE)
    kinetics_node = await _node_view(session, NODE_CODE)
    kinetics_code_count = int(
        await session.scalar(
            select(func.count()).select_from(CatalogNode).where(CatalogNode.code == NODE_CODE)
        )
        or 0
    )

    questions: dict[int, dict[str, Any]] = {}
    classifications: dict[int, tuple[dict[str, Any], ...]] = {}
    modification_proposals: dict[int, tuple[dict[str, Any], ...]] = {}
    target_version_ids: list[str] = []
    for number in TARGET_QUESTIONS:
        view = await _question_view(session, number)
        questions[number] = view
        version_ids = view.get("version_ids", []) if view.get("present") else []
        target_version_ids.extend(version_ids)
        classifications[number] = tuple(await _classifications_for(session, version_ids))
        modification_proposals[number] = tuple(
            await _modification_proposals_for(session, version_ids)
        )

    tagged_records = list(
        (
            await session.scalars(
                select(PedagogicalClassification).where(
                    PedagogicalClassification.model_version == EXPECTED_CLASSIFIER_VERSION
                )
            )
        ).all()
    )
    tagged_rows: list[dict[str, Any]] = []
    for record in tagged_records:
        version = await session.get(QuestionVersion, record.question_version_id)
        numbers = (
            await _resolve_official_numbers(session, version.question_id)
            if version is not None
            else set()
        )
        row = sanitize_classification(record)
        row["resolved_official_numbers"] = sorted(numbers)
        tagged_rows.append(row)

    kinetics_id = await session.scalar(
        select(CatalogNode.id).where(CatalogNode.code == NODE_CODE)
    )
    links_to_kinetics = int(
        await session.scalar(
            select(func.count())
            .select_from(ContentQuestionLink)
            .where(ContentQuestionLink.content_node_id == kinetics_id)
        )
        or 0
    ) if kinetics_id is not None else 0
    links_to_targets = int(
        await session.scalar(
            select(func.count())
            .select_from(ContentQuestionLink)
            .where(ContentQuestionLink.question_version_id.in_(target_version_ids))
        )
        or 0
    ) if target_version_ids else 0
    links_total = int(
        await session.scalar(select(func.count()).select_from(ContentQuestionLink)) or 0
    )

    return AuditState(
        alembic_revision_rows=revision_rows,
        parent_node=parent_node,
        kinetics_node=kinetics_node,
        kinetics_code_count=kinetics_code_count,
        questions=questions,
        classifications=classifications,
        tagged_9u1_rows=tuple(tagged_rows),
        modification_proposals=modification_proposals,
        content_link_counts={
            "referencing_kinetics_node": links_to_kinetics,
            "referencing_target_versions": links_to_targets,
            "total": links_total,
        },
    )


# --------------------------------------------------------------------------- #
# Pure audit logic
# --------------------------------------------------------------------------- #


def _new_initial_rows(rows: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get("classifier_version") == EXPECTED_CLASSIFIER_VERSION
        and row.get("prompt_version") == EXPECTED_PROMPT_VERSION
        and row.get("taxonomy_version") == TARGET_REVISION
    ]


def audit_alembic(state: AuditState) -> dict[str, Any]:
    rows = state.alembic_revision_rows
    ok = len(rows) == 1 and rows[0] == TARGET_REVISION
    return {
        "verdict": "PASS" if ok else "FAIL",
        "found": list(rows),
        "expected": TARGET_REVISION,
    }


def audit_taxonomy_parent(state: AuditState) -> dict[str, Any]:
    node = state.parent_node
    checks = {
        "exists": node is not None,
        "node_type_is_AREA": bool(node and node["node_type"] == "AREA"),
        "active": bool(node and node["active"]),
        "under_chemistry": bool(node and node["parent_code"] == PARENT_PARENT_CODE),
    }
    return {
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "node": node,
        "structural_mutation_check": "NOT_PROVABLE_FROM_CURRENT_STATE",
    }


def audit_taxonomy_node(state: AuditState) -> dict[str, Any]:
    node = state.kinetics_node
    checks = {
        "exists": node is not None,
        "exactly_one_by_code": state.kinetics_code_count == 1,
        "node_type_is_CONTENT": bool(node and node["node_type"] == NODE_TYPE),
        "active": bool(node and node["active"]),
        "parent_is_chemistry_physical": bool(node and node["parent_code"] == PARENT_CODE),
        "name_matches": bool(node and node["name"] == NODE_NAME),
        "description_matches": bool(node and node["description"] == NODE_DESCRIPTION),
        "code_matches": bool(node and node["code"] == NODE_CODE),
    }
    return {
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "node": node,
        "code_count": state.kinetics_code_count,
    }


def audit_question(state: AuditState, number: int) -> dict[str, Any]:
    view = state.questions.get(number) or {"present": False}
    checks = {
        "question_present": bool(view.get("present")),
        "has_version": view.get("version_count", 0) >= 1,
        "content_available": bool(view.get("content_available")),
    }
    return {
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "version_count": view.get("version_count", 0),
        "version_ids": view.get("version_ids", []),
        "content_hash_present": view.get("content_hash_present", False),
        "content_hash_prefix": view.get("content_hash_prefix"),
        "existing_classification_count": len(state.classifications.get(number, ())),
    }


def audit_initial_classification(state: AuditState, number: int) -> dict[str, Any]:
    rows = state.classifications.get(number, ())
    new_rows = _new_initial_rows(rows)
    rows_024 = [row for row in rows if row.get("taxonomy_version") == TARGET_REVISION]
    checks: dict[str, Any] = {
        "has_024_classification": len(rows_024) >= 1,
        "exactly_one_9u1_initial_row": len(new_rows) == 1,
        "no_9u1_duplicate": len({row["dedup_fingerprint"] for row in new_rows}) == len(new_rows),
    }
    detail: dict[str, Any] = {
        "count_total": len(rows),
        "count_024": len(rows_024),
        "count_9u1_initial": len(new_rows),
    }
    if new_rows:
        row = new_rows[0]
        mode = row.get("classification_mode")
        checks["classification_mode_is_INITIAL"] = (
            mode == EXPECTED_CLASSIFICATION_MODE if mode is not None else True
        )
        detail["classification_mode_present"] = mode is not None
        detail["classification_mode"] = mode
        # primary_content_code is persisted in metadata under key "content_code";
        # metadata key "primary_content_code" holds the subcontent (legacy quirk),
        # so the kinetics binding is verified via "content_code".
        checks["primary_content_code_is_kinetics"] = (
            row.get("primary_content_code") == NODE_CODE
        )
        detail["primary_content_code_field"] = state.field_sources.get("primary_content_code")
        detail["primary_content_code"] = row.get("primary_content_code")
        detail["status"] = row.get("status")
        detail["created_at"] = row.get("created_at")
    return {
        "verdict": "PASS" if all(bool(v) for v in checks.values()) else "FAIL",
        "checks": checks,
        "detail": detail,
    }


def audit_duplication(state: AuditState) -> dict[str, Any]:
    per_question: dict[str, Any] = {}
    duplicates: list[dict[str, Any]] = []
    for number in TARGET_QUESTIONS:
        rows = state.classifications.get(number, ())
        by_fp: dict[str, int] = {}
        for row in rows:
            by_fp[row["dedup_fingerprint"]] = by_fp.get(row["dedup_fingerprint"], 0) + 1
        collided = {fp: count for fp, count in by_fp.items() if count >= 2}
        per_question[str(number)] = {
            "total": len(rows),
            "count_024": len([r for r in rows if r.get("taxonomy_version") == TARGET_REVISION]),
            "distinct_dedup_keys": len(by_fp),
            "colliding_dedup_fingerprints": sorted(collided),
        }
        for fp, count in collided.items():
            duplicates.append({"question": number, "dedup_fingerprint": fp, "count": count})
    return {
        "verdict": "FAIL" if duplicates else "PASS",
        "per_question": per_question,
        "duplicates": duplicates,
        "dedup_key_definition": (
            "question_version_id + taxonomy_version(metadata) + model_version(column) "
            "+ prompt_version(column) + input_hash(metadata)"
        ),
    }


def audit_hash_integrity(state: AuditState) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for number in TARGET_QUESTIONS:
        rows.extend(_new_initial_rows(state.classifications.get(number, ())))
    if not rows:
        return {
            "verdict": "FAIL",
            "reason": "no new INITIAL classification found to check",
            "rows_checked": 0,
        }
    checks_per_row = []
    ok = True
    for row in rows:
        row_ok = (
            row["has_input_hash"]
            and row["has_output_hash"]
            and row["has_question_content_hash"]
            and row["input_hash_is_sha256"]
            and row["output_hash_is_sha256"]
            and row["question_content_hash_is_sha256"]
            and row.get("taxonomy_version") == TARGET_REVISION
        )
        ok = ok and row_ok
        checks_per_row.append(
            {
                "classification_id": row["classification_id"],
                "input_hash_present": row["has_input_hash"],
                "output_hash_present": row["has_output_hash"],
                "question_content_hash_present": row["has_question_content_hash"],
                "input_hash_sha256_shaped": row["input_hash_is_sha256"],
                "output_hash_sha256_shaped": row["output_hash_is_sha256"],
                "question_content_hash_sha256_shaped": row["question_content_hash_is_sha256"],
                "taxonomy_version_present": row.get("taxonomy_version") == TARGET_REVISION,
            }
        )
    return {"verdict": "PASS" if ok else "FAIL", "rows_checked": len(rows), "rows": checks_per_row}


def audit_historical_integrity(
    state: AuditState, *, recorded_baseline: dict[str, Any] | None
) -> dict[str, Any]:
    rows_023: dict[str, int] = {}
    for number in TARGET_QUESTIONS:
        rows_023[str(number)] = len(
            [
                row
                for row in state.classifications.get(number, ())
                if row.get("taxonomy_version") == SOURCE_REVISION
            ]
        )
    no_023_now = all(count == 0 for count in rows_023.values())

    baseline_ok = None
    if recorded_baseline is not None:
        try:
            hist = recorded_baseline["HISTORICAL_INTEGRITY_STATUS"]["historical_023_present"]
            pre_23 = not bool(hist.get("93")) and not bool(hist.get("128"))
            base_total = recorded_baseline["HISTORICAL_INTEGRITY_STATUS"][
                "total_classification_count"
            ]
            new_initial_total = sum(
                len(_new_initial_rows(state.classifications.get(number, ())))
                for number in TARGET_QUESTIONS
            )
            baseline_ok = {
                "pre_execution_no_023_history": pre_23,
                "new_initial_rows": new_initial_total,
                "baseline_total_classifications": base_total,
            }
        except (KeyError, TypeError):
            baseline_ok = {"error": "baseline payload not in the expected shape"}

    checks = {
        "no_023_classification_present_now": no_023_now,
        "no_historical_update_detectable": "NOT_PROVABLE_FROM_CURRENT_STATE "
        "(PedagogicalClassification has no updated_at column / audit trail)",
        "no_historical_delete_detectable": "NOT_PROVABLE_FROM_CURRENT_STATE",
    }
    if not no_023_now:
        verdict = "FAIL"
    elif recorded_baseline is not None and isinstance(baseline_ok, dict) and baseline_ok.get(
        "pre_execution_no_023_history"
    ):
        verdict = "PASS"
    else:
        verdict = "NOT_PROVABLE"
    return {
        "verdict": verdict,
        "current_023_counts": rows_023,
        "checks": checks,
        "baseline": baseline_ok,
    }


def audit_modification_proposal_integrity(state: AuditState) -> dict[str, Any]:
    all_rows: list[dict[str, Any]] = []
    for number in TARGET_QUESTIONS:
        all_rows.extend(state.modification_proposals.get(number, ()))
    any_updated = any(row["updated_after_create"] for row in all_rows)
    if not all_rows:
        verdict = "PASS"
    elif not any_updated:
        verdict = "PASS"
    else:
        verdict = "NOT_PROVABLE"
    return {
        "verdict": verdict,
        "rows_for_targets": len(all_rows),
        "any_updated_after_create": any_updated,
        "note": "attribution of any update to the 9U.1 run is NOT_PROVABLE_FROM_CURRENT_STATE",
    }


def audit_content_link_integrity(state: AuditState) -> dict[str, Any]:
    counts = state.content_link_counts
    added_by_execution = counts["referencing_kinetics_node"] > 0
    verdict = "FAIL" if added_by_execution else "PASS"
    return {
        "verdict": verdict,
        "counts": counts,
        "note": (
            "INITIAL flow writes only PedagogicalClassification; a link to the "
            "kinetics node would be unexpected. Non-mutation of pre-existing links "
            "is NOT_PROVABLE_FROM_CURRENT_STATE."
        ),
    }


def audit_question_scope(state: AuditState) -> dict[str, Any]:
    out_of_scope: list[dict[str, Any]] = []
    unresolved = 0
    for row in state.tagged_9u1_rows:
        numbers = set(row.get("resolved_official_numbers", []))
        if not numbers:
            unresolved += 1
            continue
        if numbers - ALLOWED_QUESTION_NUMBERS:
            out_of_scope.append(
                {
                    "classification_id": row["classification_id"],
                    "resolved_official_numbers": sorted(numbers),
                }
            )
    if out_of_scope:
        verdict = "FAIL"
    elif unresolved:
        verdict = "NOT_PROVABLE"
    else:
        verdict = "PASS"
    return {
        "verdict": verdict,
        "tagged_9u1_row_count": len(state.tagged_9u1_rows),
        "out_of_scope": out_of_scope,
        "unresolved_official_number": unresolved,
        "classifier_tag": EXPECTED_CLASSIFIER_VERSION,
    }


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #


def run_audits(
    state: AuditState, *, recorded_baseline: dict[str, Any] | None = None
) -> dict[str, Any]:
    alembic = audit_alembic(state)
    node = audit_taxonomy_node(state)
    parent = audit_taxonomy_parent(state)
    q93 = audit_question(state, 93)
    q128 = audit_question(state, 128)
    init93 = audit_initial_classification(state, 93)
    init128 = audit_initial_classification(state, 128)
    dup = audit_duplication(state)
    hashes = audit_hash_integrity(state)
    historical = audit_historical_integrity(state, recorded_baseline=recorded_baseline)
    modprop = audit_modification_proposal_integrity(state)
    contentlink = audit_content_link_integrity(state)
    scope = audit_question_scope(state)

    fields = {
        "ALEMBIC_REVISION": alembic["verdict"],
        "TAXONOMY_NODE": node["verdict"],
        "TAXONOMY_PARENT": parent["verdict"],
        "QUESTION_93": q93["verdict"],
        "QUESTION_128": q128["verdict"],
        "INITIAL_CLASSIFICATION_93": init93["verdict"],
        "INITIAL_CLASSIFICATION_128": init128["verdict"],
        "CLASSIFICATION_DUPLICATION": dup["verdict"],
        "HASH_INTEGRITY": hashes["verdict"],
        "HISTORICAL_INTEGRITY": historical["verdict"],
        "MODIFICATION_PROPOSAL_INTEGRITY": modprop["verdict"],
        "CONTENT_LINK_INTEGRITY": contentlink["verdict"],
        "QUESTION_SCOPE": scope["verdict"],
    }

    unexpected: list[str] = []
    for name in PROVABLE_FIELDS:
        if fields[name] != "PASS":
            unexpected.append(f"{name}={fields[name]}")
    for name in TRISTATE_FIELDS:
        if fields[name] == "FAIL":
            unexpected.append(f"{name}=FAIL")
    if node["code_count"] != 1:
        unexpected.append(f"kinetics_code_count={node['code_count']}")

    provable_pass = all(fields[name] == "PASS" for name in PROVABLE_FIELDS)
    no_fail = all(value != "FAIL" for value in fields.values())
    tristate_all_pass = all(fields[name] == "PASS" for name in TRISTATE_FIELDS)
    final = (
        AUDIT_PASS if (provable_pass and no_fail and tristate_all_pass) else AUDIT_REVIEW
    )

    return {
        "PHASE": "9U.1",
        "AUDIT": "FINAL_POST_PRODUCTION",
        **fields,
        "UNEXPECTED_CURRENT_STATE": unexpected,
        "POSTGRESQL_READS": 1,
        "POSTGRESQL_WRITES": 0,
        "DATABASE_WRITES": 0,
        "OPENAI_CALLS": 0,
        "MIGRATION_EXECUTION": 0,
        "FIELD_SOURCES": state.field_sources,
        "DETAIL": {
            "ALEMBIC_REVISION": alembic,
            "TAXONOMY_NODE": node,
            "TAXONOMY_PARENT": parent,
            "QUESTION_93": q93,
            "QUESTION_128": q128,
            "INITIAL_CLASSIFICATION_93": init93,
            "INITIAL_CLASSIFICATION_128": init128,
            "CLASSIFICATION_DUPLICATION": dup,
            "HASH_INTEGRITY": hashes,
            "HISTORICAL_INTEGRITY": historical,
            "MODIFICATION_PROPOSAL_INTEGRITY": modprop,
            "CONTENT_LINK_INTEGRITY": contentlink,
            "QUESTION_SCOPE": scope,
        },
        "FINAL_DECISION": final,
    }


def emit_audit_report(report: dict[str, Any]) -> None:
    print("PHASE 9U.1 — FINAL POST-PRODUCTION AUDIT")
    ordered = [
        "ALEMBIC_REVISION",
        "TAXONOMY_NODE",
        "TAXONOMY_PARENT",
        "QUESTION_93",
        "QUESTION_128",
        "INITIAL_CLASSIFICATION_93",
        "INITIAL_CLASSIFICATION_128",
        "CLASSIFICATION_DUPLICATION",
        "HASH_INTEGRITY",
        "HISTORICAL_INTEGRITY",
        "MODIFICATION_PROPOSAL_INTEGRITY",
        "CONTENT_LINK_INTEGRITY",
        "QUESTION_SCOPE",
        "UNEXPECTED_CURRENT_STATE",
        "POSTGRESQL_READS",
        "POSTGRESQL_WRITES",
        "DATABASE_WRITES",
        "OPENAI_CALLS",
        "MIGRATION_EXECUTION",
        "FINAL_DECISION",
    ]
    for key in ordered:
        print(f"{key}: {json.dumps(report.get(key), ensure_ascii=True, sort_keys=True)}")
    print("---")
    print(f"FIELD_SOURCES: {json.dumps(report.get('FIELD_SOURCES'), ensure_ascii=True, sort_keys=True)}")
    print(f"DETAIL: {json.dumps(report.get('DETAIL'), ensure_ascii=True, sort_keys=True)}")
    sys.stdout.flush()


def failure_report(exc: BaseException) -> dict[str, Any]:
    fields = {name: "FAIL" for name in PROVABLE_FIELDS}
    fields.update({name: "NOT_PROVABLE" for name in TRISTATE_FIELDS})
    return {
        "PHASE": "9U.1",
        "AUDIT": "FINAL_POST_PRODUCTION",
        **fields,
        "UNEXPECTED_CURRENT_STATE": [f"AUDIT_ABORTED:{type(exc).__name__}"],
        "POSTGRESQL_READS": 0,
        "POSTGRESQL_WRITES": 0,
        "DATABASE_WRITES": 0,
        "OPENAI_CALLS": 0,
        "MIGRATION_EXECUTION": 0,
        "FIELD_SOURCES": {},
        "DETAIL": {},
        "FAILURE_TYPE": type(exc).__name__,
        "FAILURE_MESSAGE": _scrub(str(exc)),
        "FINAL_DECISION": AUDIT_REVIEW,
    }


def _load_baseline() -> dict[str, Any] | None:
    path = os.getenv(BASELINE_ENV_VAR)
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - operator-supplied file
        return None


def _require_postgresql() -> None:
    try:
        get_database_url()
    except Exception as exc:  # DatabaseConfigurationError
        raise AuditError(f"PostgreSQL is not configured for the audit: {_scrub(str(exc))}") from exc


async def main() -> int:
    load_dotenv(ENV_FILE, override=False)
    try:
        _require_postgresql()
        from agente_ia_edu.db.session import create_engine, create_session_factory

        engine = create_engine()
        factory = create_session_factory(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                async with session.begin():
                    state = await collect_state(session)
        finally:
            await engine.dispose()
        report = run_audits(state, recorded_baseline=_load_baseline())
    except Exception as exc:  # fail closed
        report = failure_report(exc)
    emit_audit_report(report)
    return 0 if report["FINAL_DECISION"] == AUDIT_PASS else 1


if __name__ == "__main__":  # pragma: no cover - manual entrypoint only
    raise SystemExit(asyncio.run(main()))
