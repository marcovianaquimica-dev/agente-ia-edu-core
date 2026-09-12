"""Phase 9U.1-E - fail-closed, DRY-RUN-first correction executor for the one
known-incorrect INITIAL/024 PedagogicalClassification of ENEM question 128.

Run manually only, and only from the user's own Terminal with a real
``DATABASE_URL`` / ``OPENAI_API_KEY`` / ``OPENAI_MODEL`` exported. Even then,
by default this script only performs a read-only pre-flight and DRY-RUN plan:
no write ever happens without ``PHASE9U1E_DRY_RUN=false`` *and* a separate,
explicit human approval token - being merely "correct" is never sufficient by
itself to trigger a write.

Guarantees enforced by construction:

* Read-only pre-flight, exactly like phase9u1_production.py and
  phase9u1_audit.py: ``SET TRANSACTION READ ONLY`` before any query.
* Unambiguous target identification: question 128 is resolved both by its
  official number *and* cross-checked against a hardcoded known
  question_version_id; the one classification being touched is cross-checked
  against a hardcoded known classification id. Any mismatch aborts - nothing
  is ever inferred or guessed.
* Exactly one ACTIVE INITIAL/024 classification must exist for that question;
  zero or more than one both abort. Its classification_mode, taxonomy_version,
  lifecycle and originating classifier_version (the Phase 9U.1 tag) are all
  checked explicitly - never assumed.
* The correction is fully deterministic: it uses the same
  ``resolve_initial_controlled_vocabulary_binding`` mechanism validated in
  Phase 9U.1-C, read from the question's real statement at pre-flight time.
  OpenAI is never called by this script - the deterministic local provider
  below only ever echoes the already-computed, already-enforced binding, so
  the persisted outcome does not depend on it.
* The only write path is ``ClassificationProposalService.supersede_initial_
  classification()`` (Phase 9U.1-D infrastructure) - there is no manual
  INSERT/UPDATE/DELETE anywhere in this module.
* Nothing is written unless every pre-flight condition passes AND
  ``dry_run=False`` AND a human approval token has been supplied. A DRY-RUN
  performs every check and reports the plan without writing anything.
* Scope is hardcoded to question 128 alone; there is no code path that can
  iterate, batch, or touch any other question.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import select, text

from agente_ia_edu.db.models import BookletQuestion, CatalogNode, PedagogicalClassification, QuestionVersion
from agente_ia_edu.db.session import create_engine, create_session_factory, get_database_url
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.providers.router import ProviderRouter
from agente_ia_edu.services.curriculum_classification import (
    ClassificationProposalService,
    KINETICS_TAXONOMY_VERSION,
)

# --------------------------------------------------------------------------- #
# Known, unambiguous identifiers (never inferred, never guessed)
# --------------------------------------------------------------------------- #

KNOWN_OFFICIAL_NUMBER = 128
KNOWN_QUESTION_VERSION_ID = "7709144c-a9fc-4211-a126-575faf2b340a"
KNOWN_EXISTING_CLASSIFICATION_ID = "9685d51a-bafe-4391-9a34-fac769d6c931"

WRONG_CONTENT_CODE = "CHEMISTRY-SOLUTIONS"
EXPECTED_CONTENT_CODE = "CHEMISTRY-PHYSICAL-KINETICS"
TAXONOMY_VERSION = KINETICS_TAXONOMY_VERSION  # "024_chemistry_kinetics"
CLASSIFICATION_MODE = "INITIAL"
REQUIRED_ALEMBIC_REVISION = "025_classification_lifecycle"

# The classifier_version originally used by the Phase 9U.1 INITIAL executor
# (tests/manual/phase9u1_production.py). The classification being superseded
# must carry this tag - i.e. it must genuinely belong to that run - or this
# routine refuses to touch it.
EXPECTED_ORIGINAL_CLASSIFIER_VERSION = "phase9u1-initial-v1"
EXPECTED_ORIGINAL_PROMPT_VERSION = "phase9t3-kinetics-v1"

CORRECTION_CLASSIFIER_VERSION = "phase9u1e-correction-v1"
CORRECTION_PROMPT_VERSION = "phase9t3-kinetics-v1"

APPROVAL_ENV_VAR = "PHASE9U1E_APPROVAL_TOKEN"
APPROVAL_TOKEN = "PHASE-9U1E-Q128-CORRECTION-REVIEWED"
DRY_RUN_ENV_VAR = "PHASE9U1E_DRY_RUN"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

READY_DECISION = "Q128_CORRECTION_READY_FOR_PRODUCTION_REVIEW"
ALREADY_CORRECT_DECISION = "Q128_ALREADY_CORRECT_NO_ACTION_NEEDED"
NEEDS_REVIEW_DECISION = "Q128_CORRECTION_NEEDS_REVIEW"
DRY_RUN_DECISION = "Q128_CORRECTION_DRY_RUN_COMPLETE"
SUCCESS_DECISION = "Q128_CORRECTION_APPLIED"


class CorrectionError(RuntimeError):
    """Base class for every deliberate fail-closed abort in this routine."""


class PreFlightError(CorrectionError):
    pass


class ApprovalError(CorrectionError):
    pass


# --------------------------------------------------------------------------- #
# Sanitisation helpers (no full hashes, no statement/canonical_text, no secrets)
# --------------------------------------------------------------------------- #


def _short_hash(value: Any) -> str | None:
    if not value:
        return None
    text_value = str(value)
    return text_value[:12] + "…" if len(text_value) > 12 else text_value


def _sanitize_classification(record: PedagogicalClassification) -> dict[str, Any]:
    metadata = record.metadata_ or {}
    return {
        "id": str(record.id),
        "question_version_id": str(record.question_version_id),
        "status": record.status,
        "lifecycle": record.lifecycle,
        "supersedes_id": str(record.supersedes_id) if record.supersedes_id else None,
        "classifier_version": record.model_version,
        "prompt_version": record.prompt_version,
        "content_code": record.content,
        "taxonomy_version": metadata.get("taxonomy_version"),
        "classification_mode": metadata.get("classification_mode"),
        "input_hash_prefix": _short_hash(metadata.get("input_hash")),
        "output_hash_prefix": _short_hash(metadata.get("output_hash")),
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


def _scrub(message: str) -> str:
    scrubbed = message
    for secret in (os.getenv("DATABASE_URL"), os.getenv("OPENAI_API_KEY")):
        if secret:
            scrubbed = scrubbed.replace(secret, "[REDACTED]")
    return scrubbed


# --------------------------------------------------------------------------- #
# Snapshot (single READ ONLY transaction)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CorrectionSnapshot:
    alembic_revision: str | None
    question_found: bool
    question_version_id: str | None
    content_available: bool
    all_classifications: tuple[dict[str, Any], ...]
    binding_status: str | None  # "BOUND" | "NEEDS_REVIEW" | None (no vocabulary match)
    binding_content_code: str | None
    kinetics_candidate_recovered: bool
    other_rows_with_correction_tag: int  # must be 0 pre-correction: the tag is new


async def _read_alembic_revision(session: Any) -> str | None:
    rows = list(
        (await session.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    )
    if len(rows) != 1:
        raise PreFlightError(f"Alembic revision state is not singular: {rows}")
    return rows[0]


async def capture_snapshot(session: Any) -> CorrectionSnapshot:
    """Explicitly read-only transaction for the real (PostgreSQL) pre-flight."""
    await session.execute(text("SET TRANSACTION READ ONLY"))
    return await _read_snapshot(session)


async def _read_snapshot(session: Any) -> CorrectionSnapshot:
    """Pure read. Reads only what is needed to validate and plan the
    correction; never writes. Dialect-portable (no ``SET TRANSACTION``), so
    local tests can exercise it directly against SQLite."""
    revision = await _read_alembic_revision(session)

    anchor = await session.scalar(
        select(QuestionVersion)
        .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
        .where(BookletQuestion.official_number == KNOWN_OFFICIAL_NUMBER)
    )
    if anchor is None:
        return CorrectionSnapshot(
            alembic_revision=revision, question_found=False, question_version_id=None,
            content_available=False, all_classifications=(), binding_status=None,
            binding_content_code=None, kinetics_candidate_recovered=False,
            other_rows_with_correction_tag=0,
        )

    statement = anchor.statement or anchor.canonical_text or ""
    content_available = bool(statement.strip())

    records = list(
        (
            await session.scalars(
                select(PedagogicalClassification)
                .where(PedagogicalClassification.question_version_id == anchor.id)
                .order_by(PedagogicalClassification.created_at)
            )
        ).all()
    )
    sanitized = tuple(_sanitize_classification(r) for r in records)

    binding_status: str | None = None
    binding_content_code: str | None = None
    kinetics_recovered = False
    if content_available:
        catalog = list(
            (await session.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)))).all()
        )
        service = ClassificationProposalService(session)
        recovered = service.recover_candidates(statement, catalog)
        kinetics_recovered = any(c.get("content_code") == EXPECTED_CONTENT_CODE for c in recovered)
        binding = service.resolve_initial_controlled_vocabulary_binding(
            statement, TAXONOMY_VERSION, recovered
        )
        if binding is not None:
            binding_status = binding.status
            binding_content_code = (
                binding.bound_candidate.get("content_code") if binding.bound_candidate else None
            )

    other_tagged = int(
        await session.scalar(
            select(PedagogicalClassification.id)
            .where(PedagogicalClassification.model_version == CORRECTION_CLASSIFIER_VERSION)
            .limit(1)
        )
        is not None
    )

    return CorrectionSnapshot(
        alembic_revision=revision,
        question_found=True,
        question_version_id=str(anchor.id),
        content_available=content_available,
        all_classifications=sanitized,
        binding_status=binding_status,
        binding_content_code=binding_content_code,
        kinetics_candidate_recovered=kinetics_recovered,
        other_rows_with_correction_tag=other_tagged,
    )


# --------------------------------------------------------------------------- #
# Pure pre-flight plan (every fail-closed condition lives here, testable
# without a database)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CorrectionPlan:
    action: str  # "READY" | "ALREADY_CORRECT"
    superseded_id: str | None
    reason: str


def plan_correction(snapshot: CorrectionSnapshot) -> CorrectionPlan:
    """Validate every precondition and decide what (if anything) to do.

    Raises PreFlightError on the first violated condition; never guesses.
    """
    if snapshot.alembic_revision != REQUIRED_ALEMBIC_REVISION:
        raise PreFlightError(
            "Lifecycle infrastructure (migration 025) is not the current revision: "
            f"found {snapshot.alembic_revision!r}"
        )
    if not snapshot.question_found:
        raise PreFlightError(f"Official question {KNOWN_OFFICIAL_NUMBER} was not found")
    if snapshot.question_version_id != KNOWN_QUESTION_VERSION_ID:
        raise PreFlightError(
            "question_version_id does not match the known Q128 id; refusing to guess: "
            f"found {snapshot.question_version_id!r}"
        )
    if not snapshot.content_available:
        raise PreFlightError("Question 128 has no available content")

    active_initial_024 = [
        row
        for row in snapshot.all_classifications
        if row["lifecycle"] == "ACTIVE"
        and row["classification_mode"] == CLASSIFICATION_MODE
        and row["taxonomy_version"] == TAXONOMY_VERSION
    ]
    if len(active_initial_024) == 0:
        raise PreFlightError("No ACTIVE INITIAL/024 classification found for question 128")
    if len(active_initial_024) > 1:
        raise PreFlightError(
            f"Multiple ({len(active_initial_024)}) ACTIVE INITIAL/024 classifications found "
            "for question 128; ambiguous, refusing to guess which to supersede"
        )
    existing = active_initial_024[0]

    # "Already correct" is recognised by content, independent of id: once a
    # correction has been applied, the new ACTIVE row necessarily carries a
    # *different* id than the original wrong one (that is the whole point of
    # supersession) - demanding the original id here would make a second run
    # fail closed instead of recognising success. Unambiguous-id enforcement
    # below applies only while the row is still the known-wrong one.
    if existing["content_code"] == EXPECTED_CONTENT_CODE:
        return CorrectionPlan(
            action="ALREADY_CORRECT",
            superseded_id=existing["id"],
            reason="content_code is already CHEMISTRY-PHYSICAL-KINETICS; no correction needed",
        )

    if existing["id"] != KNOWN_EXISTING_CLASSIFICATION_ID:
        raise PreFlightError(
            "The ACTIVE classification id does not match the known Q128 classification id; "
            f"refusing to guess: found {existing['id']!r}"
        )
    if existing["classifier_version"] != EXPECTED_ORIGINAL_CLASSIFIER_VERSION:
        raise PreFlightError(
            "The classification does not carry the Phase 9U.1 executor's tag "
            f"({EXPECTED_ORIGINAL_CLASSIFIER_VERSION!r}); refusing to supersede a classification "
            "that may not belong to that flow"
        )
    if existing["prompt_version"] != EXPECTED_ORIGINAL_PROMPT_VERSION:
        raise PreFlightError("The classification's prompt_version does not match the expected Phase 9U.1 tag")

    if existing["content_code"] != WRONG_CONTENT_CODE:
        raise PreFlightError(
            "The classification's content_code matches neither the known-incorrect "
            f"({WRONG_CONTENT_CODE!r}) nor the corrected ({EXPECTED_CONTENT_CODE!r}) value; "
            f"refusing to guess: found {existing['content_code']!r}"
        )

    if not snapshot.kinetics_candidate_recovered:
        raise PreFlightError(
            "CHEMISTRY-PHYSICAL-KINETICS is not among the recovered candidates for the current "
            "statement/catalog; the deterministic correction is unavailable"
        )
    if snapshot.binding_status != "BOUND" or snapshot.binding_content_code != EXPECTED_CONTENT_CODE:
        raise PreFlightError(
            "The controlled vocabulary binding is not deterministically BOUND to "
            f"{EXPECTED_CONTENT_CODE!r} (status={snapshot.binding_status!r}, "
            f"content_code={snapshot.binding_content_code!r}); refusing to proceed without "
            "a human-reviewed path"
        )
    if snapshot.other_rows_with_correction_tag:
        raise PreFlightError(
            f"A classification already carries the correction tag {CORRECTION_CLASSIFIER_VERSION!r}; "
            "a prior partial run must be reviewed before proceeding"
        )

    return CorrectionPlan(
        action="READY",
        superseded_id=existing["id"],
        reason="deterministic controlled vocabulary binding confirms CHEMISTRY-PHYSICAL-KINETICS",
    )


# --------------------------------------------------------------------------- #
# Deterministic, network-free provider
# --------------------------------------------------------------------------- #


class DeterministicKineticsProvider:
    """Never calls OpenAI or any network service.

    Safe specifically because Phase 9U.1-C's controlled-vocabulary binding
    enforcement makes the persisted outcome independent of what a provider
    proposes, as long as it proposes the bound candidate with valid, literal
    evidence. This provider supplies exactly that, deterministically, from
    data already read from the database in this same pre-flight - it invents
    nothing. Allows exactly one call.
    """

    provider = "deterministic-controlled-vocabulary"

    def __init__(self, *, candidate: dict[str, Any], evidence_text: str) -> None:
        self._candidate = candidate
        self._evidence_text = evidence_text
        self.calls = 0

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        if self.calls:
            raise RuntimeError("Only one deterministic call is permitted per correction")
        self.calls += 1
        response = {
            "selected_candidate_rank": self._candidate["rank"],
            "discipline_code": self._candidate["discipline_code"],
            "area_code": self._candidate["area_code"],
            "content_code": self._candidate["content_code"],
            "subcontent_code": self._candidate["subcontent_code"],
            "confidence": "HIGH",
            "evidence": [
                {
                    "text": self._evidence_text,
                    "reason": "Controlled vocabulary phase9t3-kinetics-v1 deterministic match "
                    "(Phase 9U.1-C).",
                }
            ],
            "candidate_classifications": [
                {**self._candidate, "rationale": "Controlled vocabulary binding (Phase 9U.1-C)."}
            ],
            "complementary_contents": [],
            "catalog_gap": False,
            "gap_type": None,
            "taxonomy_coverage_evidence": ["Deterministic controlled vocabulary binding."],
            "review_reason": None,
            "visual_dependency": False,
            "status": "PROPOSED",
        }
        return TextGenerationResult(json.dumps(response), self.provider, "deterministic-v1")


async def _build_deterministic_provider(session: Any) -> DeterministicKineticsProvider:
    anchor = await session.scalar(
        select(QuestionVersion)
        .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
        .where(BookletQuestion.official_number == KNOWN_OFFICIAL_NUMBER)
    )
    if anchor is None or str(anchor.id) != KNOWN_QUESTION_VERSION_ID:
        raise PreFlightError("Question 128 could not be re-resolved for classification")
    statement = anchor.statement or anchor.canonical_text or ""
    catalog = list(
        (await session.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)))).all()
    )
    service = ClassificationProposalService(session)
    recovered = service.recover_candidates(statement, catalog)
    binding = service.resolve_initial_controlled_vocabulary_binding(statement, TAXONOMY_VERSION, recovered)
    if binding is None or binding.status != "BOUND":
        raise PreFlightError("Controlled vocabulary binding is unavailable at classification time")
    matched = binding.matched_terms
    if not matched:
        raise PreFlightError("Controlled vocabulary binding has no matched terms to use as evidence")
    evidence_text = next((term for term in matched if term in statement), None)
    if evidence_text is None:
        raise PreFlightError("No matched term is a literal excerpt of the statement")
    return DeterministicKineticsProvider(candidate=binding.bound_candidate, evidence_text=evidence_text)


# --------------------------------------------------------------------------- #
# Sanitised report
# --------------------------------------------------------------------------- #


def build_report(
    snapshot: CorrectionSnapshot,
    plan: CorrectionPlan | None,
    *,
    dry_run: bool,
    counters: dict[str, int],
) -> dict[str, Any]:
    existing = next(
        (
            row
            for row in snapshot.all_classifications
            if row["id"] == KNOWN_EXISTING_CLASSIFICATION_ID
        ),
        None,
    )
    active_rows = [row for row in snapshot.all_classifications if row["lifecycle"] == "ACTIVE"]
    return {
        "PHASE": "9U.1-E",
        "CURRENT_ALEMBIC_REVISION": snapshot.alembic_revision,
        "Q128_FOUND": snapshot.question_found,
        "Q128_VERSION_ID": snapshot.question_version_id,
        "EXISTING_CLASSIFICATION_ID": existing["id"] if existing else None,
        "EXISTING_CONTENT_CODE": existing["content_code"] if existing else None,
        "EXISTING_LIFECYCLE": existing["lifecycle"] if existing else None,
        "EXISTING_CLASSIFICATION_MODE": existing["classification_mode"] if existing else None,
        "EXISTING_TAXONOMY_VERSION": existing["taxonomy_version"] if existing else None,
        "EXPECTED_CONTENT_CODE": EXPECTED_CONTENT_CODE,
        "EXPECTED_TAXONOMY_VERSION": TAXONOMY_VERSION,
        "SUPERSESSION_READY": plan.action == "READY" if plan else False,
        "PLAN_ACTION": plan.action if plan else "NOT_DETERMINED",
        "OTHER_ACTIVE_CLASSIFICATIONS": len(active_rows) - (1 if existing and existing["lifecycle"] == "ACTIVE" else 0),
        "OTHER_QUESTIONS_AFFECTED": 0,
        "DRY_RUN": dry_run,
        "OPENAI_CALLS": counters.get("OPENAI_CALLS", 0),
        "POSTGRESQL_READS": counters.get("POSTGRESQL_READS", 0),
        "POSTGRESQL_WRITES": counters.get("POSTGRESQL_WRITES", 0),
        "DATABASE_WRITES": counters.get("DATABASE_WRITES", 0),
        "MIGRATION_EXECUTION": 0,
        "UNEXPECTED_CHANGES": [],
    }


def emit_report(report: dict[str, Any]) -> None:
    print("PHASE 9U.1-E — Q128 CORRECTION PRE-FLIGHT REPORT")
    for key, value in report.items():
        print(f"{key}: {json.dumps(value, ensure_ascii=True, sort_keys=True)}")
    sys.stdout.flush()


# --------------------------------------------------------------------------- #
# Human-safe approval (only consulted when dry_run is False)
# --------------------------------------------------------------------------- #


def approval_granted(
    environ: dict[str, str] | None = None,
    *,
    interactive: bool | None = None,
    prompt=input,
) -> bool:
    source = os.environ if environ is None else environ
    token = source.get(APPROVAL_ENV_VAR)
    if token is not None:
        return token == APPROVAL_TOKEN
    is_interactive = sys.stdin.isatty() if interactive is None else interactive
    if not is_interactive:
        return False
    typed = prompt(f"Type '{APPROVAL_TOKEN}' to approve the Q128 correction write: ").strip()
    return typed == APPROVAL_TOKEN


def is_dry_run(environ: dict[str, str] | None = None) -> bool:
    """Fail-safe default: DRY-RUN unless explicitly disabled."""
    source = os.environ if environ is None else environ
    return source.get(DRY_RUN_ENV_VAR, "true").strip().lower() != "false"


# --------------------------------------------------------------------------- #
# Orchestration (real production run only; never invoked by the test-suite)
# --------------------------------------------------------------------------- #


async def _require_postgresql() -> None:
    try:
        get_database_url()
    except Exception as exc:
        raise PreFlightError(f"PostgreSQL is not configured: {_scrub(str(exc))}") from exc


async def main() -> int:
    load_dotenv(ENV_FILE, override=False)
    dry_run = is_dry_run()
    counters = {"OPENAI_CALLS": 0, "POSTGRESQL_READS": 0, "POSTGRESQL_WRITES": 0, "DATABASE_WRITES": 0}
    snapshot: CorrectionSnapshot | None = None
    plan: CorrectionPlan | None = None
    final_decision = NEEDS_REVIEW_DECISION
    try:
        await _require_postgresql()
        engine = create_engine()
        factory = create_session_factory(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                async with session.begin():
                    snapshot = await capture_snapshot(session)
                    counters["POSTGRESQL_READS"] += 1

            plan = plan_correction(snapshot)

            if plan.action == "ALREADY_CORRECT":
                final_decision = ALREADY_CORRECT_DECISION
            elif dry_run:
                final_decision = DRY_RUN_DECISION
            else:
                if not approval_granted():
                    raise ApprovalError("Human approval was not granted; no write was performed.")
                async with factory() as session:
                    provider = await _build_deterministic_provider(session)
                    router = ProviderRouter(text_providers=[provider], embedding_providers=[])
                    await ClassificationProposalService(session).supersede_initial_classification(
                        superseded_id=uuid.UUID(plan.superseded_id),
                        question_version_id=uuid.UUID(KNOWN_QUESTION_VERSION_ID),
                        provider=router,
                        target_taxonomy_version=TAXONOMY_VERSION,
                        classifier_version=CORRECTION_CLASSIFIER_VERSION,
                        prompt_version=CORRECTION_PROMPT_VERSION,
                    )
                    counters["OPENAI_CALLS"] += provider.calls  # always 0: deterministic, no network
                    counters["POSTGRESQL_WRITES"] += 1
                    counters["DATABASE_WRITES"] += 1
                final_decision = SUCCESS_DECISION
        finally:
            await engine.dispose()
    except CorrectionError as exc:
        final_decision = NEEDS_REVIEW_DECISION
        report = build_report(
            snapshot or CorrectionSnapshot(None, False, None, False, (), None, None, False, 0),
            plan, dry_run=dry_run, counters=counters,
        )
        report["UNEXPECTED_CHANGES"] = [f"{type(exc).__name__}: {_scrub(str(exc))}"]
        report["FINAL_DECISION"] = final_decision
        emit_report(report)
        return 1
    except Exception as exc:  # unknown, still fail closed
        report = {"FINAL_DECISION": NEEDS_REVIEW_DECISION, "FAILURE_TYPE": type(exc).__name__,
                   "FAILURE_MESSAGE": _scrub(str(exc))}
        emit_report(report)
        return 1

    report = build_report(snapshot, plan, dry_run=dry_run, counters=counters)
    report["FINAL_DECISION"] = final_decision
    emit_report(report)
    return 0 if final_decision in (SUCCESS_DECISION, ALREADY_CORRECT_DECISION, DRY_RUN_DECISION) else 1


if __name__ == "__main__":  # pragma: no cover - manual entrypoint only
    raise SystemExit(asyncio.run(main()))
