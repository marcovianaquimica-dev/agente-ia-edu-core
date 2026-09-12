"""PHASE 9U.2 — Batch-0 INITIAL classification executor for the official inventory.

Implements the PHASE 9U.2-B specification. Three strictly separated layers:

  A. DECISION CORE   decide_question_state(ctx) -> Decision
        Totally deterministic and provider-free. No I/O. Consumes an in-memory
        QuestionContext (identity + classification rows + catalog codes + an
        already-computed controlled-vocabulary BindingProbe). The probe is
        produced by the IO shell using the real, deterministic
        ClassificationProposalService.recover_candidates /
        .resolve_initial_controlled_vocabulary_binding (Phase 9U.1-C) — those
        remain the binding authority; this core only applies the decision
        precedence to their result. This split keeps the core unit-testable
        without a database; it is a documented refinement of spec section 7,
        not a behavioural change.

  B. IO SHELL        read_snapshot / apply_initial / audit_question / audit_run
        One PostgreSQL session per call. Every read transaction issues
        ``SET TRANSACTION READ ONLY`` as its first statement (skipped on
        SQLite, which rejects the syntax — same accommodation as Phase 9U.1-E).
        The ONLY create path is
        ClassificationProposalService.classify_initial_with_provider(...).
        No session.add, no manual INSERT/UPDATE/DELETE, no supersede_*.

  C. ORCHESTRATOR    run(factory, *, dry_run, approval, only, limit)
        One official question per transaction sequence. Stops on the first
        ERROR. DRY-RUN by default; a write needs dry_run=False AND a valid
        PHASE9U2_APPROVAL_TOKEN. Never touches migrations, Alembic, OpenAI, a
        real provider, or official numbers 93/128.

Nothing in this module runs at import time. ``main()`` requires DATABASE_URL
but this file is delivered for review only — no production execution.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
for _cand in (Path.cwd(), _HERE.parents[1]):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        _ENV_FILE = _cand / ".env"
        break
else:  # pragma: no cover
    _ENV_FILE = Path(".env")

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.exc import IntegrityError, OperationalError  # noqa: E402

from agente_ia_edu.db.models import (  # noqa: E402
    BookletQuestion,
    CatalogNode,
    PedagogicalClassification,
    QuestionVersion,
)
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult  # noqa: E402
from agente_ia_edu.providers.router import ProviderRouter  # noqa: E402
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    _INITIAL_CONTROLLED_VOCABULARIES,
    ClassificationProposalService,
    KINETICS_TAXONOMY_VERSION,
)

# PHASE 9U.2-H2-BUILD — the D0..D10 decision core and its value objects were
# extracted VERBATIM into _phase9u2_decision so the curriculum-v2 executor can
# reuse exactly one decision precedence. This module keeps the kinetics
# constants and delegates decide_question_state() through a frozen profile;
# behaviour is unchanged (tests/test_phase9u2_batch0.py is the regression gate).
from _phase9u2_decision import (  # noqa: E402
    STATES,
    BindingProbe,
    Decision,
    PlannedWrite,
    QuestionContext,
    QVView,
    RowView,
    TargetProfile,
    decide_question_state as _decide_question_state,
)

# --------------------------------------------------------------------------- #
# Constants (spec section 2 — verbatim)
# --------------------------------------------------------------------------- #

TARGET_TAXONOMY = "024_chemistry_kinetics"
CANONICAL_CONTENT_CODE = "CHEMISTRY-PHYSICAL-KINETICS"
PROTECTED_OFFICIAL_NUMBERS = frozenset({93, 128})
BATCH0_CLASSIFIER_VERSION = "phase9u2-initial-v1"
BATCH0_PROMPT_VERSION = "phase9t3-kinetics-v1"
DRY_RUN_ENV_VAR = "PHASE9U2_DRY_RUN"
APPROVAL_ENV_VAR = "PHASE9U2_APPROVAL_TOKEN"
APPROVAL_TOKEN = "PHASE-9U2-BATCH0-INITIAL-REVIEWED"
MAX_SNAPSHOT_RETRIES = 2

assert TARGET_TAXONOMY == KINETICS_TAXONOMY_VERSION  # invariant

# STATES + the value objects (RowView/QVView/BindingProbe/QuestionContext/
# PlannedWrite/Decision, incl. RowView.from_row) now live in _phase9u2_decision
# and are imported above. This module keeps the kinetics constants and delegates
# decide_question_state() through the frozen profile below.
_KINETICS_PROFILE = TargetProfile(
    taxonomy_version=TARGET_TAXONOMY,
    canonical_content_code=CANONICAL_CONTENT_CODE,
    classifier_version=BATCH0_CLASSIFIER_VERSION,
    prompt_version=BATCH0_PROMPT_VERSION,
    protected_official_numbers=PROTECTED_OFFICIAL_NUMBERS,
)


# --------------------------------------------------------------------------- #
# A. DECISION CORE  (pure, deterministic, provider-free, no I/O)
# --------------------------------------------------------------------------- #


def decide_question_state(ctx: QuestionContext) -> Decision:
    """Kinetics Batch-0 decision. Thin delegation to the shared D0..D10 core
    (_phase9u2_decision.decide_question_state) with the frozen kinetics profile.
    Public 1-arg signature preserved for tests/test_phase9u2_batch0.py."""
    return _decide_question_state(ctx, _KINETICS_PROFILE)


# --------------------------------------------------------------------------- #
# Deterministic, network-free provider (same pattern as Phase 9U.1-E)
# --------------------------------------------------------------------------- #


class DeterministicKineticsProvider:
    """Never touches a network. Echoes the already-bound controlled-vocabulary
    candidate with a literal matched term as evidence, so the persisted outcome
    is fully determined by Phase 9U.1-C's binding enforcement. One call only."""

    provider = "deterministic-controlled-vocabulary"

    def __init__(self, *, candidate: dict[str, Any], evidence_text: str) -> None:
        self._candidate = candidate
        self._evidence_text = evidence_text
        self.calls = 0

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        if self.calls:
            raise RuntimeError("Only one deterministic call is permitted per classification")
        self.calls += 1
        payload = {
            "selected_candidate_rank": self._candidate["rank"],
            "discipline_code": self._candidate["discipline_code"],
            "area_code": self._candidate["area_code"],
            "content_code": self._candidate["content_code"],
            "subcontent_code": self._candidate["subcontent_code"],
            "confidence": "HIGH",
            "evidence": [
                {
                    "text": self._evidence_text,
                    "reason": "Controlled vocabulary phase9t3-kinetics-v1 deterministic match (Phase 9U.1-C).",
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
        return TextGenerationResult(json.dumps(payload), self.provider, "deterministic-v1")


# --------------------------------------------------------------------------- #
# B. IO SHELL
# --------------------------------------------------------------------------- #


class BatchError(RuntimeError):
    pass


class UniqueCollision(BatchError):
    pass


class WriteFailed(BatchError):
    pass


class ReassertMismatch(BatchError):
    pass


class AuditFailure(BatchError):
    pass


def _scrub(message: str) -> str:
    out = message
    for key in ("DATABASE_URL", "OPENAI_API_KEY"):
        secret = os.getenv(key)
        if secret:
            out = out.replace(secret, "[REDACTED]")
    return out


def _hp(value: Any) -> str:
    if value is None:
        return "None"
    s = str(value)
    return (s[:12] + "…") if len(s) > 12 else s


async def _begin_read_only(session) -> None:
    """First statement of a read transaction. SQLite rejects the syntax."""
    if session.bind.dialect.name == "postgresql":
        await session.execute(text("SET TRANSACTION READ ONLY"))


async def load_catalog(session) -> list[CatalogNode]:
    return list(
        (await session.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)))).all()
    )


async def official_numbers(session) -> list[int]:
    rows = (
        await session.scalars(
            select(BookletQuestion.official_number).where(BookletQuestion.official_number.is_not(None))
        )
    ).all()
    return sorted({int(n) for n in rows})


async def _classification_rows(session, question_version_id) -> list[PedagogicalClassification]:
    return list(
        (
            await session.scalars(
                select(PedagogicalClassification)
                .where(PedagogicalClassification.question_version_id == question_version_id)
                .order_by(PedagogicalClassification.created_at)
            )
        ).all()
    )


def probe_binding(statement: str, catalog_nodes: list[CatalogNode]) -> BindingProbe:
    """Deterministic, provider-free. Uses the real Phase 9U.1-C helpers."""
    service = ClassificationProposalService(None)
    recovered = service.recover_candidates(statement, catalog_nodes)
    has_canonical = any(c.get("content_code") == CANONICAL_CONTENT_CODE for c in recovered)
    binding = service.resolve_initial_controlled_vocabulary_binding(
        statement, TARGET_TAXONOMY, recovered, content_code=CANONICAL_CONTENT_CODE
    )
    if binding is None:
        return BindingProbe(has_canonical, None, None, None)
    term = None
    for candidate_term in binding.matched_terms or ():
        if candidate_term and candidate_term in statement:
            term = candidate_term
            break
    return BindingProbe(
        recovered_has_canonical=has_canonical,
        binding_status=binding.status,
        bound_candidate=dict(binding.bound_candidate) if binding.bound_candidate else None,
        literal_evidence_term=term,
    )


@dataclass
class Snapshot:
    exists: bool
    hard_error: str | None
    ctx: QuestionContext | None
    pre_rows: dict[str, tuple]
    pre_count: int


async def read_snapshot_core(
    session, number: int, catalog_nodes: list[CatalogNode], catalog_codes: frozenset[str]
) -> Snapshot:
    """Dialect-portable read (no SET TRANSACTION). Callers wrap with
    _begin_read_only() so the RO guard is the first statement in PostgreSQL."""
    bqs = list(
        (
            await session.scalars(
                select(BookletQuestion).where(BookletQuestion.official_number == number)
            )
        ).all()
    )
    if not bqs:
        return Snapshot(False, None, None, {}, 0)
    qv_ids = {b.question_version_id for b in bqs}
    if len(qv_ids) != 1:
        return Snapshot(True, "AMBIGUOUS_OFFICIAL_NUMBER", None, {}, 0)

    qv_id = next(iter(qv_ids))
    version = await session.get(QuestionVersion, qv_id)
    qv_view = None
    if version is not None:
        qv_view = QVView(
            id=str(version.id),
            statement_text=(version.statement or version.canonical_text or ""),
        )

    rows = await _classification_rows(session, qv_id)
    row_views = tuple(RowView.from_row(r) for r in rows)
    pre_rows = {rv.id: rv.immutable_key for rv in row_views}
    pre_count = int(await session.scalar(select(func.count()).select_from(PedagogicalClassification)))

    binding: BindingProbe | None = None
    if not row_views and qv_view is not None and qv_view.statement_text.strip():
        binding = probe_binding(qv_view.statement_text.strip(), catalog_nodes)

    ctx = QuestionContext(
        official_number=number,
        question_version=qv_view,
        classifications=row_views,
        catalog_codes=catalog_codes,
        target_vocabulary_registered=TARGET_TAXONOMY in _INITIAL_CONTROLLED_VOCABULARIES,
        binding=binding,
    )
    return Snapshot(True, None, ctx, pre_rows, pre_count)


async def do_write(session, planned: PlannedWrite) -> tuple[str, DeterministicKineticsProvider]:
    """The ONLY create path. classify_initial_with_provider commits internally.
    IntegrityError (partial unique index) -> rollback + UniqueCollision."""
    provider = DeterministicKineticsProvider(
        candidate=planned.bound_candidate, evidence_text=planned.evidence_term
    )
    router = ProviderRouter(text_providers=[provider], embedding_providers=[])
    try:
        new = await ClassificationProposalService(session).classify_initial_with_provider(
            question_version_id=uuid.UUID(planned.question_version_id),
            provider=router,
            target_taxonomy_version=TARGET_TAXONOMY,
            classifier_version=planned.classifier_version,
            prompt_version=planned.prompt_version,
            target_content_code=CANONICAL_CONTENT_CODE,
        )
    except IntegrityError as exc:
        await session.rollback()
        raise UniqueCollision(_scrub(str(exc))) from exc
    except Exception as exc:  # provider failure, validation error, etc.
        await session.rollback()
        raise WriteFailed(_scrub(f"{type(exc).__name__}: {exc}")) from exc
    return str(new.id), provider


async def apply_initial(
    factory, ctx: QuestionContext, planned: PlannedWrite, catalog_nodes: list[CatalogNode]
) -> str:
    """One question, one write transaction: re-read, re-assert, write, commit."""
    async with factory() as session:
        rows = await _classification_rows(session, uuid.UUID(planned.question_version_id))
        row_views = tuple(RowView.from_row(r) for r in rows)
        recheck = replace(ctx, classifications=row_views)
        decision = decide_question_state(recheck)
        if decision.state != "READY_FOR_INITIAL":
            raise ReassertMismatch(f"{decision.state}/{decision.reason_code}")
        active_target_now = [
            rv for rv in row_views if rv.lifecycle == "ACTIVE" and rv.taxonomy_version == TARGET_TAXONOMY
        ]
        if active_target_now:
            raise ReassertMismatch("ALREADY_CLASSIFIED/ACTIVE_TARGET_APPEARED")
        new_id, _provider = await do_write(session, planned)
        return new_id


_HASH_RE = "^[0-9a-f]{64}$"


async def audit_question(
    factory, number: int, pre_rows: dict[str, tuple], pre_count: int, question_version_id: str, new_id: str
) -> None:
    import re

    async with factory() as session:
        await _begin_read_only(session)
        rows = await _classification_rows(session, uuid.UUID(question_version_id))
        by_id = {str(r.id): r for r in rows}

        new_ids = set(by_id) - set(pre_rows)
        if new_ids != {new_id}:
            raise AuditFailure(f"expected exactly new id {_hp(new_id)}, got {sorted(_hp(i) for i in new_ids)}")

        for old_id, key in pre_rows.items():
            if old_id not in by_id:
                raise AuditFailure(f"historical row {_hp(old_id)} disappeared")
            if RowView.from_row(by_id[old_id]).immutable_key != key:
                raise AuditFailure(f"historical row {_hp(old_id)} changed")

        row = by_id[new_id]
        md = row.metadata_ or {}
        checks = {
            "lifecycle": row.lifecycle == "ACTIVE",
            "content": row.content == CANONICAL_CONTENT_CODE,
            "taxonomy_version": md.get("taxonomy_version") == TARGET_TAXONOMY,
            "classification_mode": md.get("classification_mode") == "INITIAL",
            "model_version": row.model_version == BATCH0_CLASSIFIER_VERSION,
            "prompt_version": row.prompt_version == BATCH0_PROMPT_VERSION,
            "supersedes_id": row.supersedes_id is None,
            "input_hash": bool(re.match(_HASH_RE, str(md.get("input_hash", "")))),
            "output_hash": bool(re.match(_HASH_RE, str(md.get("output_hash", "")))),
            "question_content_hash": bool(re.match(_HASH_RE, str(md.get("question_content_hash", "")))),
        }
        bad = [k for k, ok in checks.items() if not ok]
        if bad:
            raise AuditFailure("new row failed: " + ",".join(bad))

        total = int(await session.scalar(select(func.count()).select_from(PedagogicalClassification)))
        if total != pre_count + 1:
            raise AuditFailure(f"global count {total} != {pre_count} + 1")

        dup = await session.scalar(
            select(func.count()).select_from(
                select(PedagogicalClassification.question_version_id)
                .where(PedagogicalClassification.lifecycle == "ACTIVE")
                .group_by(
                    PedagogicalClassification.question_version_id,
                    PedagogicalClassification.metadata_["taxonomy_version"].as_string(),
                )
                .having(func.count() > 1)
                .subquery()
            )
        )
        if dup:
            raise AuditFailure(f"{dup} ACTIVE duplicate (qv, taxonomy_version) pair(s)")


async def _protected_snapshot(session) -> dict[int, tuple]:
    out: dict[int, tuple] = {}
    for number in sorted(PROTECTED_OFFICIAL_NUMBERS | {91}):
        bqs = list(
            (
                await session.scalars(
                    select(BookletQuestion).where(BookletQuestion.official_number == number)
                )
            ).all()
        )
        qv_ids = {b.question_version_id for b in bqs}
        keys: list[tuple] = []
        for qv_id in sorted(qv_ids, key=str):
            for r in await _classification_rows(session, qv_id):
                keys.append(RowView.from_row(r).immutable_key)
        out[number] = tuple(sorted(keys))
    return out


async def _run_level_counts(session) -> dict[str, int]:
    supersedes = int(
        await session.scalar(
            select(func.count())
            .select_from(PedagogicalClassification)
            .where(PedagogicalClassification.supersedes_id.is_not(None))
        )
    )
    dups = int(
        await session.scalar(
            select(func.count()).select_from(
                select(PedagogicalClassification.question_version_id)
                .where(PedagogicalClassification.lifecycle == "ACTIVE")
                .group_by(
                    PedagogicalClassification.question_version_id,
                    PedagogicalClassification.metadata_["taxonomy_version"].as_string(),
                )
                .having(func.count() > 1)
                .subquery()
            )
        )
    )
    every_ids = set((await session.scalars(select(PedagogicalClassification.id))).all())
    orphan = 0
    for pc_id, sup in (
        await session.execute(
            select(PedagogicalClassification.id, PedagogicalClassification.supersedes_id).where(
                PedagogicalClassification.supersedes_id.is_not(None)
            )
        )
    ).all():
        if sup not in every_ids:
            orphan += 1
    return {"supersedes": supersedes, "active_duplicates": dups, "orphan_supersessions": orphan}


# --------------------------------------------------------------------------- #
# C. ORCHESTRATOR
# --------------------------------------------------------------------------- #


@dataclass
class QResult:
    number: int
    state: str
    reason_code: str
    detail: str = ""
    qv: str | None = None
    taxo: str | None = None
    new_id: str | None = None


@dataclass
class RunReport:
    results: list[QResult] = field(default_factory=list)
    dry_run: bool = True
    approval_present: bool = False
    writes_planned: int = 0
    writes_applied: int = 0
    stopped_on_error: int | None = None
    q91_unchanged: bool = True
    q93_unchanged: bool = True
    q128_unchanged: bool = True
    supersedes_unchanged: bool = True
    active_duplicates: int = 0
    orphan_supersessions: int = 0

    @property
    def counts(self) -> dict[str, int]:
        c = {s: 0 for s in STATES}
        for r in self.results:
            if r.state in c:
                c[r.state] += 1
        return c

    @property
    def has_pending(self) -> bool:
        c = self.counts
        return any(c[s] for s in ("NEEDS_REVIEW", "OUT_OF_SCOPE", "SUPERSEDED_ONLY", "BLOCKED"))

    @property
    def run_level_ok(self) -> bool:
        """Run-level immutability / integrity invariants — a hard gate."""
        return (
            self.q91_unchanged
            and self.q93_unchanged
            and self.q128_unchanged
            and self.supersedes_unchanged
            and self.active_duplicates == 0
            and self.orphan_supersessions == 0
        )

    @property
    def exit_code(self) -> int:
        return 1 if (self.stopped_on_error is not None or not self.run_level_ok) else 0


async def run(
    factory,
    *,
    dry_run: bool,
    approval: bool,
    only: list[int] | None = None,
    limit: int | None = None,
) -> RunReport:
    report = RunReport(dry_run=dry_run, approval_present=approval)

    async with factory() as session:
        await _begin_read_only(session)
        catalog_nodes = await load_catalog(session)
        catalog_codes = frozenset(n.code for n in catalog_nodes)

    async with factory() as session:
        await _begin_read_only(session)
        inventory = await official_numbers(session)
        protected_start = await _protected_snapshot(session)
        counts_start = await _run_level_counts(session)

    targets = list(only) if only is not None else inventory
    if limit is not None:
        targets = targets[:limit]

    for number in targets:
        # D0 first: protected numbers never trigger a per-question classification
        # read (spec section 6). Their rows are still captured for the run-level
        # immutability audit in _protected_snapshot().
        if number in PROTECTED_OFFICIAL_NUMBERS:
            report.results.append(QResult(number, "PROTECTED", "PROTECTED_QUESTION"))
            continue

        # Tx-R1 — read-only snapshot + decision (with bounded retry)
        snapshot: Snapshot | None = None
        for attempt in range(MAX_SNAPSHOT_RETRIES + 1):
            try:
                async with factory() as session:
                    await _begin_read_only(session)
                    snapshot = await read_snapshot_core(session, number, catalog_nodes, catalog_codes)
                break
            except OperationalError as exc:
                if attempt == MAX_SNAPSHOT_RETRIES:
                    report.results.append(
                        QResult(number, "ERROR", "SNAPSHOT_READ_FAILED", _scrub(str(exc)))
                    )
                    report.stopped_on_error = number
                    break
                await asyncio.sleep(0.2 * (attempt + 1))
        if report.stopped_on_error is not None:
            break
        assert snapshot is not None

        if not snapshot.exists:
            report.results.append(QResult(number, "SKIPPED", "NOT_IN_OFFICIAL_INVENTORY"))
            continue
        if snapshot.hard_error:
            report.results.append(QResult(number, "ERROR", snapshot.hard_error))
            report.stopped_on_error = number
            break

        ctx = snapshot.ctx
        decision = decide_question_state(ctx)
        qv = ctx.question_version.id if ctx.question_version else None

        if decision.state == "ERROR":
            report.results.append(
                QResult(number, "ERROR", decision.reason_code, decision.detail, qv=qv)
            )
            report.stopped_on_error = number
            break

        if decision.state != "READY_FOR_INITIAL":
            taxo = None
            if decision.reason_code == "ACTIVE_CLASSIFICATION_IN_OTHER_TAXONOMY":
                taxo = decision.detail
            report.results.append(
                QResult(number, decision.state, decision.reason_code, decision.detail, qv=qv, taxo=taxo)
            )
            continue

        report.writes_planned += 1

        if dry_run or not approval:
            report.results.append(
                QResult(
                    number,
                    "READY_FOR_INITIAL",
                    "PLANNED_DRY_RUN" if dry_run else "PLANNED_PENDING_APPROVAL",
                    decision.detail,
                    qv=qv,
                )
            )
            continue

        # Tx-W — write
        try:
            new_id = await apply_initial(factory, ctx, decision.planned_write, catalog_nodes)
        except UniqueCollision as exc:
            report.results.append(
                QResult(number, "ERROR", "UNIQUE_INDEX_COLLISION", _scrub(str(exc)), qv=qv)
            )
            report.stopped_on_error = number
            break
        except ReassertMismatch as exc:
            report.results.append(
                QResult(number, "ERROR", "REASSERT_MISMATCH", _scrub(str(exc)), qv=qv)
            )
            report.stopped_on_error = number
            break
        except Exception as exc:
            report.results.append(
                QResult(number, "ERROR", "WRITE_FAILED", _scrub(f"{type(exc).__name__}: {exc}"), qv=qv)
            )
            report.stopped_on_error = number
            break

        # Tx-A — per-question audit
        try:
            await audit_question(
                factory, number, snapshot.pre_rows, snapshot.pre_count, ctx.question_version.id, new_id
            )
        except AuditFailure as exc:
            report.results.append(
                QResult(number, "ERROR", "POST_WRITE_AUDIT_FAILED", _scrub(str(exc)), qv=qv, new_id=new_id)
            )
            report.stopped_on_error = number
            break

        report.writes_applied += 1
        report.results.append(
            QResult(
                number,
                "READY_FOR_INITIAL",
                "INITIAL_CLASSIFICATION_CREATED",
                decision.detail,
                qv=qv,
                new_id=new_id,
            )
        )

    # Run-level audit (always)
    async with factory() as session:
        await _begin_read_only(session)
        protected_end = await _protected_snapshot(session)
        counts_end = await _run_level_counts(session)

    report.q91_unchanged = protected_start.get(91) == protected_end.get(91)
    report.q93_unchanged = protected_start.get(93) == protected_end.get(93)
    report.q128_unchanged = protected_start.get(128) == protected_end.get(128)
    report.supersedes_unchanged = counts_start["supersedes"] == counts_end["supersedes"]
    report.active_duplicates = counts_end["active_duplicates"]
    report.orphan_supersessions = counts_end["orphan_supersessions"]
    return report


# --------------------------------------------------------------------------- #
# Environment gates (spec sections 12/13)
# --------------------------------------------------------------------------- #


def is_dry_run(environ: dict[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return source.get(DRY_RUN_ENV_VAR, "true").strip().lower() != "false"


def approval_granted(environ: dict[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return source.get(APPROVAL_ENV_VAR) == APPROVAL_TOKEN


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #


def render_report(report: RunReport) -> str:
    lines = ["PHASE 9U.2 — BATCH-0 RUN REPORT", ""]
    for r in report.results:
        parts = [f"#{r.number}", r.state, f"reason={r.reason_code}"]
        if r.qv:
            parts.append(f"qv={_hp(r.qv)}")
        if r.taxo:
            parts.append(f"taxo={r.taxo}")
        if r.new_id:
            parts.append(f"new_id={_hp(r.new_id)}")
        if r.detail:
            parts.append(f"detail={r.detail}")
        lines.append("  " + " ".join(parts))
    c = report.counts
    lines += [
        "",
        "COUNTS",
        f"  PROTECTED: {c['PROTECTED']}",
        f"  ALREADY_CLASSIFIED: {c['ALREADY_CLASSIFIED']}",
        f"  READY_FOR_INITIAL: {c['READY_FOR_INITIAL']}",
        f"  NEEDS_REVIEW: {c['NEEDS_REVIEW']}",
        f"  OUT_OF_SCOPE: {c['OUT_OF_SCOPE']}",
        f"  SUPERSEDED_ONLY: {c['SUPERSEDED_ONLY']}",
        f"  BLOCKED: {c['BLOCKED']}",
        f"  ERROR: {c['ERROR']}",
        "",
        f"WRITES_PLANNED: {report.writes_planned}",
        f"WRITES_APPLIED: {report.writes_applied}",
        "",
        f"DRY_RUN: {report.dry_run}",
        f"APPROVAL_PRESENT: {report.approval_present}",
        "",
        f"Q91_UNCHANGED: {'PASS' if report.q91_unchanged else 'FAIL'}",
        f"Q93_UNCHANGED: {'PASS' if report.q93_unchanged else 'FAIL'}",
        f"Q128_UNCHANGED: {'PASS' if report.q128_unchanged else 'FAIL'}",
        f"SUPERSESSIONS_UNCHANGED: {'PASS' if report.supersedes_unchanged else 'FAIL'}",
        "",
        f"ACTIVE_DUPLICATES: {report.active_duplicates}",
        f"ORPHAN_SUPERSESSIONS: {report.orphan_supersessions}",
        "",
        f"RUN_LEVEL_AUDIT: {'PASS' if report.run_level_ok else 'FAIL'}",
        f"PHASE_9U2_BATCH0_HAS_PENDING: {'YES' if report.has_pending else 'NO'}",
        f"STOPPED_ON_ERROR: {report.stopped_on_error if report.stopped_on_error is not None else 'NO'}",
        "",
        "FINAL_DECISION:",
        (
            "PHASE_9U2_BATCH0_RUN_COMPLETE"
            if report.exit_code == 0
            else (
                "PHASE_9U2_BATCH0_RUN_STOPPED_ON_ERROR"
                if report.stopped_on_error is not None
                else "PHASE_9U2_BATCH0_RUN_LEVEL_AUDIT_FAILED"
            )
        ),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main() — production entrypoint (NOT executed in Phase 9U.2-C)
# --------------------------------------------------------------------------- #


def _parse_only(value: str | None) -> list[int] | None:
    if not value:
        return None
    return [int(part) for part in value.split(",") if part.strip()]


async def _amain(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Phase 9U.2 Batch-0 INITIAL classification executor")
    parser.add_argument("--only", default=None, help="comma-separated official numbers")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_FILE, override=False)
    except Exception:  # pragma: no cover
        pass

    from agente_ia_edu.db.session import create_engine, create_session_factory, get_database_url

    try:
        get_database_url()
    except Exception as exc:  # fail closed
        print(f"PHASE 9U.2 — BATCH-0 RUN REPORT\nFATAL: {_scrub(str(exc))}\n\nFINAL_DECISION:\nPHASE_9U2_BATCH0_RUN_STOPPED_ON_ERROR")
        return 1

    dry_run = is_dry_run()
    approval = approval_granted()

    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    try:
        report = await run(
            factory,
            dry_run=dry_run,
            approval=approval,
            only=_parse_only(args.only),
            limit=args.limit,
        )
    finally:
        await engine.dispose()

    print(render_report(report))
    print()
    print(f"DATABASE_WRITES: {report.writes_applied}")
    print("OPENAI_CALLS: 0")
    print("ALEMBIC_EXECUTION: 0")
    return report.exit_code


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - manual entrypoint
    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
