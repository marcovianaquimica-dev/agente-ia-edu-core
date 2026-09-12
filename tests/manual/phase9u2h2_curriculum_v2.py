"""PHASE 9U.2-H2 — curriculum-v2 INITIAL classification executor.

Distinct from the kinetics Batch-0 executor (``phase9u2_batch0.py``):

  * Batch-0 targets ONE taxonomy/content — ``024_chemistry_kinetics`` /
    ``CHEMISTRY-PHYSICAL-KINETICS`` — for the whole inventory.
  * This executor targets ``curriculum-v2`` and a PER-QUESTION CONTENT node,
    taken from the single explicit map :data:`OFFICIAL_TO_CONTENT` (Layer A),
    which is the curated PHASE 9U.2-G0 assignment (``docs/phase9u2_g0_curation.md``),
    NOT an inference from the question number.

Both share exactly ONE decision precedence: ``_phase9u2_decision.decide_question_state``
(the D0..D10 core, extracted verbatim from Batch-0). This module only orchestrates
it against the map, then routes READY_FOR_INITIAL rows through the existing write
service. It creates NO catalog nodes, NO vocabularies, NO bindings; it never calls
OpenAI or an external provider; it never runs Alembic/migrations.

The map is a *catalog* mapping, never a bypass: every question is still put
through the real, deterministic controlled-vocabulary probe and the shared
decision core before any write. A question whose statement does not
deterministically bind stays NEEDS_REVIEW / OUT_OF_SCOPE and is not written.

Q107 (official number) has NO entry in the map: its content
``CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS`` (G0 code N05) is DEFERRED while Q107 is
HUMAN_REVIEW. Q107 is reported HUMAN_REVIEW_PENDING and never enters a read or
write for classification.

Layers
------
  A  configuration / map        (this file, module scope)
  B  decision / read-only       read_snapshot / decide  (SET TRANSACTION READ ONLY first)
  C  write (isolated)           apply_initial -> ClassificationProposalService
                                .classify_initial_with_provider(...)  ONLY
  D  post-audit (per question)  audit_question
  E  final audit                final_audit  (counts + protected fingerprints)

Safety gates
------------
  PHASE9U2_H2_DRY_RUN        default "true"; only the literal, case-insensitive
                            value "false" disables dry-run.
  PHASE9U2_H2_APPROVAL_TOKEN must equal exactly
                            "PHASE9U2-H2-INITIAL-CLASSIFICATION-REVIEWED".
  No token from an earlier phase is accepted (not PHASE9U1E, not
  PHASE9U2_G5_CATALOG_REVIEWED, not PHASE-9U2-BATCH0-INITIAL-REVIEWED).
  Without dry_run=False AND the exact token, no write is attempted.

Nothing runs at import time. ``main()`` requires DATABASE_URL and PostgreSQL.
This file is delivered for review; PHASE 9U.2-H2-BUILD does NOT execute it.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
from agente_ia_edu.providers.router import ProviderRouter  # noqa: E402
from agente_ia_edu.services._curriculum_v2_bindings import (  # noqa: E402
    DEFERRED_CONTENTS,
    NEW_AREAS,
    NEW_CONTENTS,
    TAXONOMY_VERSION as CURRICULUM_V2_TAXONOMY,
    build_curriculum_v2_bindings,
)
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    DeterministicInitialBinding,
    RetrievalVocabularyEntry,
    ClassificationProposalService,
    registered_initial_taxonomy_versions,
    resolve_registered_initial_binding,
)

# The shared decision core + value objects (verbatim D0..D10).
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

# Generic, target-agnostic IO primitives + the deterministic provider + the
# batch exception hierarchy — reused as-is from Batch-0 (no re-implementation).
from phase9u2_batch0 import (  # noqa: E402
    AuditFailure,
    DeterministicKineticsProvider as DeterministicInitialProvider,
    ReassertMismatch,
    UniqueCollision,
    WriteFailed,
    _begin_read_only,
    _classification_rows,
    _hp,
    _scrub,
    load_catalog,
    official_numbers,
)

# --------------------------------------------------------------------------- #
# LAYER A — configuration / map
# --------------------------------------------------------------------------- #

TARGET_TAXONOMY = CURRICULUM_V2_TAXONOMY  # "curriculum-v2"

# Verbatim from docs/phase9u2_g0_curation.md §15.4 (curated assignment). This is
# the ONLY place a question number maps to a CONTENT code. Q107 is intentionally
# absent (N05 CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS is DEFERRED).
OFFICIAL_TO_CONTENT: dict[int, str] = {
    92: "PHYSICS-THERMAL-PHASE-CHANGE",
    94: "PHYSICS-WAVES-PHENOMENA",
    95: "CHEMISTRY-PHYSICAL-EQUILIBRIUM",
    103: "CHEMISTRY-GENERAL-POLARITY-IMF",
    104: "CHEMISTRY-PHYSICAL-ACID-BASE",
    105: "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS",
    111: "CHEMISTRY-ENVIRONMENTAL-AIR-POLLUTION",
    112: "PHYSICS-EM-ELECTROSTATICS",
    113: "CHEMISTRY-GENERAL-MATTER-PROPERTIES",
    125: "PHYSICS-EM-ELECTROSTATICS",
    126: "CHEMISTRY-ORGANIC-POLYMERS",
    127: "BIOLOGY-ECOLOGY-POPULATIONS-CONSERVATION",
    129: "BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES",
    130: "PHYSICS-EM-INDUCTION",
    133: "PHYSICS-THERMAL-THERMODYNAMICS",
    134: "PHYSICS-MECHANICS-HYDROSTATICS",
    135: "PHYSICS-MECHANICS-PRESSURE-SCALE",
    152: "MATH-MEASUREMENT-PLANE-AREA",
    153: "MATH-COMBINATORICS-COUNTING",
    172: "MATH-STATISTICS-CENTRAL-TENDENCY",
}

# Protected official numbers — hard D0 block: never read for classification,
# never written, captured only for the run-level immutability fingerprint.
PROTECTED_OFFICIAL_NUMBERS = frozenset({91, 93, 107, 128})

# Q107 is blocked *and* has no map entry; report it explicitly.
HUMAN_REVIEW_OFFICIAL_NUMBERS = frozenset({107})

H2_CLASSIFIER_VERSION = "phase9u2h2-initial-v1"
H2_PROMPT_VERSION = "phase9u2h2-curriculum-v2-v1"

DRY_RUN_ENV_VAR = "PHASE9U2_H2_DRY_RUN"
APPROVAL_ENV_VAR = "PHASE9U2_H2_APPROVAL_TOKEN"
APPROVAL_TOKEN = "PHASE9U2-H2-INITIAL-CLASSIFICATION-REVIEWED"

# Tokens from earlier phases that must NEVER authorise a write here.
_REJECTED_TOKENS = frozenset(
    {
        "PHASE9U1E",
        "PHASE9U1E-Q128-CORRECTION-REVIEWED",
        "PHASE9U2_G5_CATALOG_REVIEWED",
        "PHASE-9U2-BATCH0-INITIAL-REVIEWED",
        "PHASE9U2-BATCH0-INITIAL-REVIEWED",
    }
)

MAX_SNAPSHOT_RETRIES = 2
_HASH_RE = "^[0-9a-f]{64}$"

# Expected inventory sizes (documentation + a cheap self-check).
EXPECTED_MAP_SIZE = 20
EXPECTED_CATALOG_NODE_COUNT = 48
# The pre-existing classification count *before this executor's first run*. It was
# 4 for PHASE 9U.2-H2-EXECUTE-13 (Q91/Q93/Q128 legacy rows). A later phase that
# runs this same machinery after H2 has already written (e.g. PHASE 9U.2-H3
# classifying one recovered question) legitimately sees a higher count, so
# `preflight()` / `run()` take `expected_initial_count` — the default keeps the
# H2 behaviour byte-identical; H3 passes 17.
EXPECTED_INITIAL_CLASSIFICATION_COUNT = 4
DEFERRED_CONTENT_CODE = "CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS"

# Content code -> AREA (parent) code, from _curriculum_v2_bindings.NEW_CONTENTS.
_CONTENT_TO_AREA: dict[str, str] = {code: area for code, _name, area in NEW_CONTENTS}
_AREA_TO_DISCIPLINE: dict[str, str] = {code: disc for code, _name, disc in NEW_AREAS}
# The registered curriculum-v2 bindings, keyed by canonical CONTENT code.
_V2_BINDINGS: dict[str, DeterministicInitialBinding] = {
    b.canonical_code: b
    for b in build_curriculum_v2_bindings(RetrievalVocabularyEntry, DeterministicInitialBinding)
}


def profile_for(content_code: str) -> TargetProfile:
    """The frozen TargetProfile the shared decision core needs for one question.

    ``protected_official_numbers`` is empty here on purpose: protection is
    enforced by the orchestrator (it never calls the core for a protected
    number); the core still receives a valid profile object.
    """
    return TargetProfile(
        taxonomy_version=TARGET_TAXONOMY,
        canonical_content_code=content_code,
        classifier_version=H2_CLASSIFIER_VERSION,
        prompt_version=H2_PROMPT_VERSION,
        protected_official_numbers=frozenset(),
    )


class MapError(RuntimeError):
    pass


def validate_map() -> None:
    """Layer-A self-consistency — pure, no I/O. Raises MapError on any defect."""
    if set(OFFICIAL_TO_CONTENT) & PROTECTED_OFFICIAL_NUMBERS:
        raise MapError("protected official number present in the write map")
    if 107 in OFFICIAL_TO_CONTENT:
        raise MapError("Q107 must not have a target content code")
    if len(OFFICIAL_TO_CONTENT) != EXPECTED_MAP_SIZE:
        raise MapError(f"map has {len(OFFICIAL_TO_CONTENT)} entries, expected {EXPECTED_MAP_SIZE}")
    registered = set(registered_initial_taxonomy_versions())
    if TARGET_TAXONOMY not in registered:
        raise MapError(f"{TARGET_TAXONOMY!r} is not a registered INITIAL taxonomy")
    for number, code in OFFICIAL_TO_CONTENT.items():
        if code == DEFERRED_CONTENT_CODE or code in DEFERRED_CONTENTS:
            raise MapError(f"#{number} maps to a DEFERRED content {code!r}")
        if code not in _V2_BINDINGS:
            raise MapError(f"#{number} -> {code!r} has no registered curriculum-v2 binding")
        binding = resolve_registered_initial_binding(TARGET_TAXONOMY, code)
        if binding is None or binding.canonical_code != code:
            raise MapError(f"#{number} -> {code!r} does not resolve to itself in the registry")
        if code not in _CONTENT_TO_AREA:
            raise MapError(f"#{number} -> {code!r} missing from NEW_CONTENTS")
        if binding.parent_code != _CONTENT_TO_AREA[code]:
            raise MapError(
                f"#{number} -> {code!r} parent {binding.parent_code!r} != "
                f"catalog area {_CONTENT_TO_AREA[code]!r}"
            )
        # The AREA may be pre-existing (e.g. CHEMISTRY-PHYSICAL) or one of the 11
        # G5 NEW_AREAS; if it is a NEW_AREA its discipline parent must be known.
        area = _CONTENT_TO_AREA[code]
        if area in _AREA_TO_DISCIPLINE and _AREA_TO_DISCIPLINE[area] not in {
            "CHEMISTRY",
            "PHYSICS",
            "BIOLOGY",
            "MATH",
        }:
            raise MapError(f"NEW_AREA {area!r} has an unexpected discipline parent")


# --------------------------------------------------------------------------- #
# LAYER B — decision / read-only
# --------------------------------------------------------------------------- #


def probe_binding(
    statement: str, catalog_nodes: list[CatalogNode], content_code: str
) -> BindingProbe:
    """Deterministic, provider-free. Uses the real Phase 9U.1-C / G4 helpers
    against ``curriculum-v2`` and the per-question CONTENT code. Identical shape
    to ``phase9u2_batch0.probe_binding`` but parametrised on the content code."""
    service = ClassificationProposalService(None)
    recovered = service.recover_candidates(statement, catalog_nodes)
    has_canonical = any(c.get("content_code") == content_code for c in recovered)
    binding = service.resolve_initial_controlled_vocabulary_binding(
        statement, TARGET_TAXONOMY, recovered, content_code=content_code
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
    content_code: str | None
    pre_rows: dict[str, tuple]
    pre_count: int


async def read_snapshot(
    session,
    number: int,
    catalog_nodes: list[CatalogNode],
    catalog_codes: frozenset[str],
) -> Snapshot:
    """Dialect-portable read (no SET TRANSACTION here — callers wrap with
    _begin_read_only so the RO guard is the first statement on PostgreSQL)."""
    content_code = OFFICIAL_TO_CONTENT.get(number)
    bqs = list(
        (
            await session.scalars(
                select(BookletQuestion).where(BookletQuestion.official_number == number)
            )
        ).all()
    )
    if not bqs:
        return Snapshot(False, None, None, content_code, {}, 0)
    qv_ids = {b.question_version_id for b in bqs}
    if len(qv_ids) != 1:
        return Snapshot(True, "AMBIGUOUS_OFFICIAL_NUMBER", None, content_code, {}, 0)

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
    if (
        content_code is not None
        and not row_views
        and qv_view is not None
        and qv_view.statement_text.strip()
    ):
        binding = probe_binding(qv_view.statement_text.strip(), catalog_nodes, content_code)

    vocab_registered = (
        content_code is not None
        and resolve_registered_initial_binding(TARGET_TAXONOMY, content_code) is not None
    )
    ctx = QuestionContext(
        official_number=number,
        question_version=qv_view,
        classifications=row_views,
        catalog_codes=catalog_codes,
        target_vocabulary_registered=vocab_registered,
        binding=binding,
    )
    return Snapshot(True, None, ctx, content_code, pre_rows, pre_count)


def decide(snapshot: Snapshot) -> Decision:
    """Apply the shared D0..D10 core with the per-question profile."""
    if snapshot.ctx is None:
        return Decision("ERROR", snapshot.hard_error or "NO_CONTEXT")
    if snapshot.content_code is None:
        # Only Q107 reaches here (no map entry) — but protection already caught it.
        return Decision("BLOCKED", "NO_TARGET_CONTENT_CODE")
    return _decide_question_state(snapshot.ctx, profile_for(snapshot.content_code))


# --------------------------------------------------------------------------- #
# LAYER C — write (isolated)
# --------------------------------------------------------------------------- #


async def do_write(session, planned: PlannedWrite, content_code: str) -> str:
    """The ONLY create path: ClassificationProposalService.classify_initial_with_provider.
    The service commits internally. IntegrityError (partial unique index) ->
    rollback + UniqueCollision; anything else -> rollback + WriteFailed."""
    provider = DeterministicInitialProvider(
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
            target_content_code=content_code,
        )
    except IntegrityError as exc:
        await session.rollback()
        raise UniqueCollision(_scrub(str(exc))) from exc
    except Exception as exc:
        await session.rollback()
        raise WriteFailed(_scrub(f"{type(exc).__name__}: {exc}")) from exc
    return str(new.id)


async def apply_initial(factory, snapshot: Snapshot, planned: PlannedWrite) -> str:
    """One question, one write transaction: re-read, re-assert the decision,
    write, commit (via the service). Never writes if the re-assert is not
    still READY_FOR_INITIAL or an ACTIVE curriculum-v2 row has appeared."""
    assert snapshot.ctx is not None and snapshot.content_code is not None
    async with factory() as session:
        rows = await _classification_rows(session, uuid.UUID(planned.question_version_id))
        row_views = tuple(RowView.from_row(r) for r in rows)
        recheck = Snapshot(
            exists=True,
            hard_error=None,
            ctx=QuestionContext(
                official_number=snapshot.ctx.official_number,
                question_version=snapshot.ctx.question_version,
                classifications=row_views,
                catalog_codes=snapshot.ctx.catalog_codes,
                target_vocabulary_registered=snapshot.ctx.target_vocabulary_registered,
                binding=snapshot.ctx.binding,
            ),
            content_code=snapshot.content_code,
            pre_rows=snapshot.pre_rows,
            pre_count=snapshot.pre_count,
        )
        decision = decide(recheck)
        if decision.state != "READY_FOR_INITIAL":
            raise ReassertMismatch(f"{decision.state}/{decision.reason_code}")
        active_v2_now = [
            rv
            for rv in row_views
            if rv.lifecycle == "ACTIVE" and rv.taxonomy_version == TARGET_TAXONOMY
        ]
        if active_v2_now:
            raise ReassertMismatch("ALREADY_CLASSIFIED/ACTIVE_TARGET_APPEARED")
        return await do_write(session, planned, snapshot.content_code)


# --------------------------------------------------------------------------- #
# LAYER D — post-audit (per question)
# --------------------------------------------------------------------------- #


async def audit_question(
    factory,
    content_code: str,
    pre_rows: dict[str, tuple],
    pre_count: int,
    question_version_id: str,
    new_id: str,
) -> None:
    async with factory() as session:
        await _begin_read_only(session)
        rows = await _classification_rows(session, uuid.UUID(question_version_id))
        by_id = {str(r.id): r for r in rows}

        new_ids = set(by_id) - set(pre_rows)
        if new_ids != {new_id}:
            raise AuditFailure(
                f"expected exactly new id {_hp(new_id)}, got {sorted(_hp(i) for i in new_ids)}"
            )
        for old_id, key in pre_rows.items():
            if old_id not in by_id:
                raise AuditFailure(f"historical row {_hp(old_id)} disappeared")
            if RowView.from_row(by_id[old_id]).immutable_key != key:
                raise AuditFailure(f"historical row {_hp(old_id)} changed")

        row = by_id[new_id]
        md = row.metadata_ or {}
        checks = {
            "lifecycle": row.lifecycle == "ACTIVE",
            "content": row.content == content_code,
            "taxonomy_version": md.get("taxonomy_version") == TARGET_TAXONOMY,
            "classification_mode": md.get("classification_mode") == "INITIAL",
            "model_version": row.model_version == H2_CLASSIFIER_VERSION,
            "prompt_version": row.prompt_version == H2_PROMPT_VERSION,
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


# --------------------------------------------------------------------------- #
# LAYER E — final audit  (counts + protected fingerprints)
# --------------------------------------------------------------------------- #


async def _protected_fingerprint(session) -> dict[int, tuple]:
    out: dict[int, tuple] = {}
    for number in sorted(PROTECTED_OFFICIAL_NUMBERS):
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


async def _integrity_counts(session) -> dict[str, int]:
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
    multi_active = int(
        await session.scalar(
            select(func.count()).select_from(
                select(PedagogicalClassification.question_version_id)
                .where(PedagogicalClassification.lifecycle == "ACTIVE")
                .group_by(PedagogicalClassification.question_version_id)
                .having(func.count() > 1)
                .subquery()
            )
        )
    )
    every_ids = set((await session.scalars(select(PedagogicalClassification.id))).all())
    orphan = 0
    deep = 0
    id_to_sup: dict[Any, Any] = {}
    for pc_id, sup in (
        await session.execute(
            select(PedagogicalClassification.id, PedagogicalClassification.supersedes_id)
        )
    ).all():
        id_to_sup[pc_id] = sup
    for pc_id, sup in id_to_sup.items():
        if sup is None:
            continue
        if sup not in every_ids:
            orphan += 1
        elif id_to_sup.get(sup) is not None:
            deep += 1
    return {
        "supersedes": supersedes,
        "active_duplicates": dups,
        "multiple_active_per_version": multi_active,
        "orphan_supersessions": orphan,
        "deep_supersession_chains": deep,
    }


async def _catalog_node_count(session) -> int:
    return int(await session.scalar(select(func.count()).select_from(CatalogNode)))


async def _n05_present(session) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(CatalogNode)
            .where(CatalogNode.code == DEFERRED_CONTENT_CODE)
        )
    )


# --------------------------------------------------------------------------- #
# ORCHESTRATOR
# --------------------------------------------------------------------------- #


@dataclass
class QResult:
    number: int
    state: str
    reason_code: str
    detail: str = ""
    qv: str | None = None
    content: str | None = None
    new_id: str | None = None


@dataclass
class RunReport:
    results: list[QResult] = field(default_factory=list)
    dry_run: bool = True
    approval_present: bool = False
    preflight_ok: bool = False
    preflight_detail: str = ""
    initial_count: int | None = None
    final_count: int | None = None
    writes_planned: int = 0
    writes_applied: int = 0
    stopped_on_error: int | None = None
    protected_unchanged: dict[int, bool] = field(default_factory=dict)
    integrity_end: dict[str, int] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        c = {s: 0 for s in STATES}
        c["HUMAN_REVIEW"] = 0
        c["SKIPPED"] = 0
        for r in self.results:
            c[r.state] = c.get(r.state, 0) + 1
        return c

    @property
    def protected_ok(self) -> bool:
        return bool(self.protected_unchanged) and all(self.protected_unchanged.values())

    @property
    def integrity_ok(self) -> bool:
        i = self.integrity_end
        return bool(i) and (
            i.get("active_duplicates", 1) == 0
            and i.get("multiple_active_per_version", 1) == 0
            and i.get("orphan_supersessions", 1) == 0
            and i.get("deep_supersession_chains", 1) == 0
        )

    @property
    def exit_code(self) -> int:
        ok = (
            self.preflight_ok
            and self.stopped_on_error is None
            and self.protected_ok
            and self.integrity_ok
        )
        return 0 if ok else 1


async def preflight(
    factory, *, expected_initial_count: int = EXPECTED_INITIAL_CLASSIFICATION_COUNT
) -> tuple[bool, str, int]:
    """Read-only. Returns (ok, detail, initial_classification_count).

    ``expected_initial_count`` defaults to the H2 value (4). A caller running this
    machinery after H2 has written (PHASE 9U.2-H3) passes the current legitimate
    count (17)."""
    try:
        validate_map()
    except MapError as exc:
        return False, f"MAP_INVALID: {exc}", 0

    async with factory() as session:
        await _begin_read_only(session)
        dialect = session.bind.dialect.name
        if dialect != "postgresql":
            return False, f"NOT_POSTGRESQL: {dialect}", 0
        node_count = await _catalog_node_count(session)
        n05 = await _n05_present(session)
        counts = await _integrity_counts(session)
        init_count = int(
            await session.scalar(select(func.count()).select_from(PedagogicalClassification))
        )
        catalog_nodes = await load_catalog(session)
        codes = {n.code for n in catalog_nodes}
        inventory = set(await official_numbers(session))

    problems: list[str] = []
    if node_count != EXPECTED_CATALOG_NODE_COUNT:
        problems.append(f"catalog_nodes={node_count}!={EXPECTED_CATALOG_NODE_COUNT}")
    if n05 != 0:
        problems.append("N05_PRESENT")
    missing_contents = [c for c in OFFICIAL_TO_CONTENT.values() if c not in codes]
    if missing_contents:
        problems.append(f"missing_contents={sorted(set(missing_contents))}")
    missing_areas = [a for a in _CONTENT_TO_AREA.values() if a not in codes]
    if missing_areas:
        problems.append(f"missing_areas={sorted(set(missing_areas))}")
    if counts["active_duplicates"]:
        problems.append("active_duplicates")
    if counts["multiple_active_per_version"]:
        problems.append("multiple_active_per_version")
    if counts["orphan_supersessions"]:
        problems.append("orphan_supersessions")
    if counts["deep_supersession_chains"]:
        problems.append("deep_supersession_chains")
    if init_count != expected_initial_count:
        problems.append(f"classification_count={init_count}!={expected_initial_count}")
    absent = [n for n in OFFICIAL_TO_CONTENT if n not in inventory]
    if absent:
        problems.append(f"map_numbers_absent_from_inventory={absent}")
    if 107 in OFFICIAL_TO_CONTENT:
        problems.append("Q107_in_write_map")

    if problems:
        return False, "; ".join(problems), init_count
    return True, "OK", init_count


async def run(
    factory,
    *,
    dry_run: bool,
    approval: bool,
    only: list[int] | None = None,
    expected_initial_count: int = EXPECTED_INITIAL_CLASSIFICATION_COUNT,
) -> RunReport:
    report = RunReport(dry_run=dry_run, approval_present=approval)

    ok, detail, init_count = await preflight(factory, expected_initial_count=expected_initial_count)
    report.preflight_ok = ok
    report.preflight_detail = detail
    report.initial_count = init_count
    if not ok:
        return report

    async with factory() as session:
        await _begin_read_only(session)
        catalog_nodes = await load_catalog(session)
        catalog_codes = frozenset(n.code for n in catalog_nodes)

    async with factory() as session:
        await _begin_read_only(session)
        protected_start = await _protected_fingerprint(session)

    targets = sorted(only) if only is not None else sorted(
        set(OFFICIAL_TO_CONTENT) | HUMAN_REVIEW_OFFICIAL_NUMBERS
    )

    for number in targets:
        if number in PROTECTED_OFFICIAL_NUMBERS:
            state = "HUMAN_REVIEW" if number in HUMAN_REVIEW_OFFICIAL_NUMBERS else "PROTECTED"
            reason = "HUMAN_REVIEW_PENDING" if state == "HUMAN_REVIEW" else "PROTECTED_QUESTION"
            report.results.append(QResult(number, state, reason))
            continue

        snapshot: Snapshot | None = None
        for attempt in range(MAX_SNAPSHOT_RETRIES + 1):
            try:
                async with factory() as session:
                    await _begin_read_only(session)
                    snapshot = await read_snapshot(session, number, catalog_nodes, catalog_codes)
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

        decision = decide(snapshot)
        qv = snapshot.ctx.question_version.id if snapshot.ctx and snapshot.ctx.question_version else None

        if decision.state == "ERROR":
            report.results.append(
                QResult(number, "ERROR", decision.reason_code, decision.detail, qv=qv,
                        content=snapshot.content_code)
            )
            report.stopped_on_error = number
            break

        if decision.state != "READY_FOR_INITIAL":
            report.results.append(
                QResult(number, decision.state, decision.reason_code, decision.detail, qv=qv,
                        content=snapshot.content_code)
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
                    content=snapshot.content_code,
                )
            )
            continue

        try:
            new_id = await apply_initial(factory, snapshot, decision.planned_write)
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

        try:
            await audit_question(
                factory,
                snapshot.content_code,
                snapshot.pre_rows,
                snapshot.pre_count,
                snapshot.ctx.question_version.id,
                new_id,
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
                content=snapshot.content_code,
                new_id=new_id,
            )
        )

    async with factory() as session:
        await _begin_read_only(session)
        protected_end = await _protected_fingerprint(session)
        report.integrity_end = await _integrity_counts(session)
        report.final_count = int(
            await session.scalar(select(func.count()).select_from(PedagogicalClassification))
        )
    report.protected_unchanged = {
        n: protected_start.get(n) == protected_end.get(n) for n in sorted(PROTECTED_OFFICIAL_NUMBERS)
    }
    return report


# --------------------------------------------------------------------------- #
# Environment gates
# --------------------------------------------------------------------------- #


def is_dry_run(environ: dict[str, str] | None = None) -> bool:
    import os

    source = os.environ if environ is None else environ
    return source.get(DRY_RUN_ENV_VAR, "true").strip().lower() != "false"


def approval_granted(environ: dict[str, str] | None = None) -> bool:
    import os

    source = os.environ if environ is None else environ
    value = source.get(APPROVAL_ENV_VAR)
    if value is None:
        return False
    if value in _REJECTED_TOKENS:
        return False
    return value == APPROVAL_TOKEN


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #


def render_report(report: RunReport) -> str:
    lines = ["PHASE 9U.2-H2 — INITIAL CLASSIFICATION REPORT", ""]
    lines.append(f"PREFLIGHT_STATUS: {'PASS' if report.preflight_ok else 'FAIL'}")
    if not report.preflight_ok:
        lines.append(f"  detail: {report.preflight_detail}")
    lines.append(f"INITIAL_CLASSIFICATION_COUNT: {report.initial_count}")
    lines.append(f"FINAL_CLASSIFICATION_COUNT: {report.final_count}")
    lines.append("")
    for r in report.results:
        parts = [f"#{r.number}", r.state, f"reason={r.reason_code}"]
        if r.content:
            parts.append(f"content={r.content}")
        if r.qv:
            parts.append(f"qv={_hp(r.qv)}")
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
        f"  HUMAN_REVIEW: {c['HUMAN_REVIEW']}",
        f"  ALREADY_CLASSIFIED: {c['ALREADY_CLASSIFIED']}",
        f"  READY_FOR_INITIAL: {c['READY_FOR_INITIAL']}",
        f"  NEEDS_REVIEW: {c['NEEDS_REVIEW']}",
        f"  OUT_OF_SCOPE: {c['OUT_OF_SCOPE']}",
        f"  SUPERSEDED_ONLY: {c['SUPERSEDED_ONLY']}",
        f"  BLOCKED: {c['BLOCKED']}",
        f"  SKIPPED: {c['SKIPPED']}",
        f"  ERROR: {c['ERROR']}",
        "",
        f"WRITES_PLANNED: {report.writes_planned}",
        f"WRITES_APPLIED: {report.writes_applied}",
        "",
        f"DRY_RUN: {report.dry_run}",
        f"APPROVAL_PRESENT: {report.approval_present}",
        "",
        "PROTECTED_FINGERPRINT_CHECK",
    ]
    for n in sorted(PROTECTED_OFFICIAL_NUMBERS):
        state = report.protected_unchanged.get(n)
        lines.append(f"  Q{n}: {'PASS' if state else 'FAIL' if state is not None else 'NOT_CHECKED'}")
    i = report.integrity_end
    lines += [
        "",
        "INTEGRITY_CHECK",
        f"  ACTIVE_DUPLICATES: {i.get('active_duplicates', 'NA')}",
        f"  MULTIPLE_ACTIVE_PER_VERSION: {i.get('multiple_active_per_version', 'NA')}",
        f"  ORPHAN_SUPERSESSIONS: {i.get('orphan_supersessions', 'NA')}",
        f"  DEEP_SUPERSESSION_CHAINS: {i.get('deep_supersession_chains', 'NA')}",
        "",
        f"STOPPED_ON_ERROR: {report.stopped_on_error if report.stopped_on_error is not None else 'NO'}",
        "",
        "SECURITY",
        f"  DATABASE_WRITES: {report.writes_applied}",
        "  OPENAI_CALLS: 0",
        "  ALEMBIC_EXECUTION: 0",
        "",
        "FINAL_DECISION:",
        "PHASE_9U2_H2_COMPLETE" if report.exit_code == 0 else "PHASE_9U2_H2_NEEDS_REVIEW",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main() — production entrypoint (NOT executed in PHASE 9U.2-H2-BUILD)
# --------------------------------------------------------------------------- #


def _parse_only(value: str | None) -> list[int] | None:
    if not value:
        return None
    return [int(part) for part in value.split(",") if part.strip()]


async def _amain(argv: list[str]) -> int:  # pragma: no cover - manual entrypoint
    parser = argparse.ArgumentParser(description="PHASE 9U.2-H2 curriculum-v2 INITIAL executor")
    parser.add_argument("--only", default=None, help="comma-separated official numbers")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_FILE, override=False)
    except Exception:
        pass

    from agente_ia_edu.db.session import create_engine, create_session_factory, get_database_url

    try:
        get_database_url()
    except Exception as exc:  # fail closed
        print(
            "PHASE 9U.2-H2 — INITIAL CLASSIFICATION REPORT\n"
            f"FATAL: {_scrub(str(exc))}\n\nFINAL_DECISION:\nPHASE_9U2_H2_NEEDS_REVIEW"
        )
        return 1

    dry_run = is_dry_run()
    approval = approval_granted()

    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    try:
        report = await run(factory, dry_run=dry_run, approval=approval, only=_parse_only(args.only))
    finally:
        await engine.dispose()

    print(render_report(report))
    return report.exit_code


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - manual entrypoint
    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
