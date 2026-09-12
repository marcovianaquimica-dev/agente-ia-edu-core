"""PHASE 9U.2-H4 — STEP 2: the HUMAN REVIEW decision executor.

Applies ONLY the six curator-approved decisions for the residual questions
95, 104, 105, 112, 129, 134. The pedagogical decision was already made by a
human; this executor does NOT re-derive it — it validates the decision file,
checks the production state, and persists one ``pedagogical_classifications``
row per confirmed question, idempotently and auditably.

It does NOT:
  * use the lexical matcher / ``recover_candidates`` /
    ``resolve_initial_controlled_vocabulary_binding`` / the deterministic
    decision core to decide content (the human decided);
  * call ``classify_initial_with_provider`` / ``propose_with_provider``
    (those force ``classification_mode="INITIAL"`` and enforce a statement
    evidence gate these six deliberately cannot pass);
  * call OpenAI or any provider, run Alembic, create catalog nodes / AREAs,
    modify any vocabulary or the normalizer, touch ``question_versions`` /
    ``booklet_questions``, or write any question outside the six.

Persistence
-----------
There is no service/repository wrapper for a human-decided classification, and
every existing creator (``_persist_provider_output``, the curriculum ``finalize``
path, ``ContentAuthoringService.suggest_classification``) builds the row via the
ORM and ``session.add(...)`` + ``session.commit()``. This executor uses the same
ORM path — NOT raw SQL — mirroring ``_persist_provider_output``'s column shape,
with:
  source                       = "human"        (String(20), no CHECK; 'human' is an
                                                  established value in the sibling
                                                  question_classifications table)
  status                       = "CLASSIFIED"   (in the status CHECK allow-list)
  lifecycle                    = "ACTIVE"
  supersedes_id                = NULL
  metadata.taxonomy_version    = "curriculum-v2"
  metadata.classification_mode = "HUMAN_REVIEW"  (free JSONB string, like "INITIAL")
  metadata.decision_source     = "HUMAN_REVIEW"
  metadata.review_phase        = "PHASE_9U2_H4"
  metadata.content_code / candidate_content_code / curator_id / evidence_note /
  question_content_hash
No migration is required (``classification_mode`` and ``source`` are unconstrained).

Gates
-----
  PHASE9U2_H4_DRY_RUN        default "true"; only literal, case-insensitive "false" disables.
  PHASE9U2_H4_APPROVAL_TOKEN must equal exactly "PHASE9U2-H4-HUMAN-DECISIONS-APPROVED";
                            no H1/H2/H3/G5/Batch-0 token is accepted.
  A write needs dry_run=False AND the exact token AND a passing pre-flight.

Nothing runs at import time. ``main()`` requires DATABASE_URL + PostgreSQL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

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

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from agente_ia_edu.db.models import (  # noqa: E402
    BookletQuestion,
    CatalogNode,
    PedagogicalClassification,
    QuestionVersion,
)
from agente_ia_edu.services._curriculum_v2_bindings import (  # noqa: E402
    DEFERRED_CONTENTS,
    NEW_AREAS,
    NEW_CONTENTS,
    TAXONOMY_VERSION,
)
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    resolve_registered_initial_binding,
)

from phase9u2_batch0 import _begin_read_only, _hp, _scrub  # noqa: E402

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

PHASE = "PHASE_9U2_H4"
TAXONOMY = TAXONOMY_VERSION  # "curriculum-v2"
CLASSIFICATION_MODE = "HUMAN_REVIEW"
DECISION_SOURCE = "HUMAN_REVIEW"
ROW_SOURCE = "human"
ROW_STATUS = "CLASSIFIED"
ROW_LIFECYCLE = "ACTIVE"
ROW_REASONING_TYPE = "HUMAN_REVIEW"
ROW_DIFFICULTY = "UNKNOWN"

H4_REVIEW_OFFICIAL_NUMBERS: tuple[int, ...] = (95, 104, 105, 112, 129, 134)

# The curator-approved decisions this executor is allowed to apply. The decision
# file MUST match this exactly (decision == CONFIRM, content_code == mapped value).
H4_EXPECTED_DECISIONS: dict[int, str] = {
    95: "CHEMISTRY-PHYSICAL-EQUILIBRIUM",
    104: "CHEMISTRY-PHYSICAL-ACID-BASE",
    105: "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS",
    112: "PHYSICS-EM-ELECTROSTATICS",
    129: "BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES",
    134: "PHYSICS-MECHANICS-HYDROSTATICS",
}

PROTECTED_OFFICIAL_NUMBERS = frozenset({91, 93, 107, 128})
PROTECTED_FINGERPRINT_NUMBERS = frozenset({91, 93, 107, 128, 133})
ALREADY_CLASSIFIED_OFFICIAL_NUMBERS = frozenset(
    {92, 94, 103, 111, 113, 125, 126, 127, 130, 133, 135, 152, 153, 172}
)
DEFERRED_CONTENT_CODE = "CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS"

EXPECTED_CATALOG_NODE_COUNT = 48
EXPECTED_COUNT_BEFORE = 18
EXPECTED_COUNT_AFTER = 24
EXPECTED_V2_ACTIVE_BEFORE = 14
EXPECTED_V2_ACTIVE_AFTER = 20

DECISION_FILE = _HERE / "phase9u2h4_decisions.json"

DRY_RUN_ENV_VAR = "PHASE9U2_H4_DRY_RUN"
APPROVAL_ENV_VAR = "PHASE9U2_H4_APPROVAL_TOKEN"
APPROVAL_TOKEN = "PHASE9U2-H4-HUMAN-DECISIONS-APPROVED"
_REJECTED_TOKENS = frozenset(
    {
        "PHASE9U1E",
        "PHASE9U1E-Q128-CORRECTION-REVIEWED",
        "PHASE9U2_G5_CATALOG_REVIEWED",
        "PHASE-9U2-BATCH0-INITIAL-REVIEWED",
        "PHASE9U2-BATCH0-INITIAL-REVIEWED",
        "PHASE9U2-H2-INITIAL-CLASSIFICATION-REVIEWED",
        "PHASE9U2-H3-VOCABULARY-REVIEWED",
    }
)

_HASH_RE = "^[0-9a-f]{16,64}$"

_CONTENT_TO_AREA = {code: area for code, _n, area in NEW_CONTENTS}
_CONTENT_TO_NAME = {code: name for code, name, _a in NEW_CONTENTS}
_AREA_TO_DISCIPLINE = {code: disc for code, _n, disc in NEW_AREAS}
# pre-existing AREAs (not in NEW_AREAS) used by two of the six contents
_PREEXISTING_AREA_DISCIPLINE = {"CHEMISTRY-PHYSICAL": "CHEMISTRY", "PHYSICS-MECHANICS": "PHYSICS"}


def _discipline_for(content_code: str) -> str:
    area = _CONTENT_TO_AREA.get(content_code, "")
    return _AREA_TO_DISCIPLINE.get(area) or _PREEXISTING_AREA_DISCIPLINE.get(area, "")


class H4Error(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Decision file
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Decision:
    official_number: int
    content_code: str
    curator_id: str
    evidence_note: str


def load_decisions(path: Path | str = DECISION_FILE) -> list[Decision]:
    """Read + validate the curator decision file. Raises H4Error on ANY deviation
    from the six approved CONFIRM decisions — never auto-corrects, never rewrites."""
    p = Path(path)
    if not p.exists():
        raise H4Error(f"decision file not found: {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise H4Error(f"decision file is not valid JSON: {exc}") from exc

    if not isinstance(doc, dict) or doc.get("taxonomy_version") != TAXONOMY:
        raise H4Error(f"decision file taxonomy_version must be {TAXONOMY!r}")
    raw = doc.get("decisions")
    if not isinstance(raw, list):
        raise H4Error("decision file has no 'decisions' list")

    by_number: dict[int, dict] = {}
    for entry in raw:
        if not isinstance(entry, dict) or "official_number" not in entry:
            raise H4Error(f"malformed decision entry: {entry!r}")
        n = entry["official_number"]
        if n in by_number:
            raise H4Error(f"duplicate decision entry for #{n}")
        by_number[n] = entry

    got = set(by_number)
    expected = set(H4_EXPECTED_DECISIONS)
    if got != expected:
        raise H4Error(
            f"decision file official_numbers {sorted(got)} != required {sorted(expected)}"
        )

    decisions: list[Decision] = []
    for number in H4_REVIEW_OFFICIAL_NUMBERS:
        e = by_number[number]
        decision = e.get("decision")
        code = e.get("content_code")
        if decision != "CONFIRM":
            raise H4Error(
                f"#{number}: decision is {decision!r}; this executor is CONFIRM-only "
                f"(PENDING/REJECT are not applied here)"
            )
        if code != H4_EXPECTED_DECISIONS[number]:
            raise H4Error(
                f"#{number}: content_code {code!r} != approved {H4_EXPECTED_DECISIONS[number]!r}"
            )
        cand = e.get("candidate_content_code")
        if cand is not None and cand != code:
            raise H4Error(f"#{number}: content_code {code!r} != candidate_content_code {cand!r}")
        decisions.append(
            Decision(
                official_number=number,
                content_code=code,
                curator_id=str(e.get("curator_id") or ""),
                evidence_note=str(e.get("evidence_note") or ""),
            )
        )
    return decisions


def validate_decision_semantics(decisions: list[Decision]) -> None:
    """Pure structural guards on the decision set (no DB)."""
    numbers = [d.official_number for d in decisions]
    if sorted(numbers) != list(H4_REVIEW_OFFICIAL_NUMBERS):
        raise H4Error(f"decision set {sorted(numbers)} != {list(H4_REVIEW_OFFICIAL_NUMBERS)}")
    if set(numbers) & PROTECTED_OFFICIAL_NUMBERS:
        raise H4Error("a protected official number is in the decision set")
    if set(numbers) & ALREADY_CLASSIFIED_OFFICIAL_NUMBERS:
        raise H4Error("an already-classified official number is in the decision set")
    if 133 in numbers:
        raise H4Error("Q133 must not be in the decision set")
    for d in decisions:
        if d.content_code == DEFERRED_CONTENT_CODE or d.content_code in DEFERRED_CONTENTS:
            raise H4Error(f"#{d.official_number}: DEFERRED content {d.content_code}")
        binding = resolve_registered_initial_binding(TAXONOMY, d.content_code)
        if binding is None or binding.canonical_code != d.content_code:
            raise H4Error(f"#{d.official_number}: {d.content_code!r} not a registered {TAXONOMY} content")
        if d.content_code not in _CONTENT_TO_AREA:
            raise H4Error(f"#{d.official_number}: {d.content_code!r} not in NEW_CONTENTS")


# --------------------------------------------------------------------------- #
# Read-only state / pre-flight
# --------------------------------------------------------------------------- #


@dataclass
class QState:
    official_number: int
    question_version_id: str | None
    content_code: str
    v2_active_rows: list[dict]  # {content, mode} for ACTIVE curriculum-v2 rows
    catalog_node_active: bool

    @property
    def v2_active_count(self) -> int:
        return len(self.v2_active_rows)


@dataclass
class PreflightResult:
    ok: bool
    detail: str
    classification_count: int
    v2_active_count: int
    catalog_node_count: int
    n05_present: int
    integrity: dict[str, int]
    q_states: dict[int, QState]
    protected_fingerprint: dict[int, tuple]
    to_write: list[Decision]
    already_classified: list[int]


async def _integrity_counts(session) -> dict[str, int]:
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
    multi = int(
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
    id_to_sup: dict = {
        r[0]: r[1]
        for r in (
            await session.execute(
                select(PedagogicalClassification.id, PedagogicalClassification.supersedes_id)
            )
        ).all()
    }
    ids = set(id_to_sup)
    orphan = sum(1 for s in id_to_sup.values() if s is not None and s not in ids)
    deep = sum(
        1
        for pid, s in id_to_sup.items()
        if s is not None and id_to_sup.get(s) is not None
    )
    return {
        "active_duplicates": dups,
        "multiple_active_per_version": multi,
        "orphan_supersessions": orphan,
        "deep_supersession_chains": deep,
    }


async def _classification_rows(session, qv_id):
    return list(
        (
            await session.scalars(
                select(PedagogicalClassification)
                .where(PedagogicalClassification.question_version_id == qv_id)
                .order_by(PedagogicalClassification.created_at)
            )
        ).all()
    )


def _row_key(row: PedagogicalClassification) -> tuple:
    md = row.metadata_ or {}
    return (
        str(row.id),
        row.lifecycle,
        row.content,
        row.status,
        md.get("taxonomy_version"),
        md.get("classification_mode"),
        str(row.supersedes_id) if row.supersedes_id else None,
    )


async def _protected_fingerprint(session) -> dict[int, tuple]:
    out: dict[int, tuple] = {}
    for number in sorted(PROTECTED_FINGERPRINT_NUMBERS):
        bqs = list(
            (
                await session.scalars(
                    select(BookletQuestion).where(BookletQuestion.official_number == number)
                )
            ).all()
        )
        keys: list[tuple] = []
        for qv_id in sorted({b.question_version_id for b in bqs}, key=str):
            for r in await _classification_rows(session, qv_id):
                keys.append(_row_key(r))
        out[number] = tuple(sorted(keys))
    return out


async def _q_state(session, number: int, content_code: str, active_catalog: set[str]) -> QState:
    bqs = list(
        (
            await session.scalars(
                select(BookletQuestion).where(BookletQuestion.official_number == number)
            )
        ).all()
    )
    qv_ids = {b.question_version_id for b in bqs}
    qv_id = next(iter(qv_ids)) if len(qv_ids) == 1 else None
    rows: list[dict] = []
    if qv_id is not None:
        for r in await _classification_rows(session, qv_id):
            md = r.metadata_ or {}
            if r.lifecycle == "ACTIVE" and md.get("taxonomy_version") == TAXONOMY:
                rows.append(
                    {"content": r.content, "mode": md.get("classification_mode"), "id": str(r.id)}
                )
    return QState(
        official_number=number,
        question_version_id=str(qv_id) if qv_id is not None else None,
        content_code=content_code,
        v2_active_rows=rows,
        catalog_node_active=content_code in active_catalog,
    )


async def preflight(
    factory, decisions: list[Decision], *, require_postgresql: bool = True
) -> PreflightResult:
    validate_decision_semantics(decisions)
    async with factory() as session:
        await _begin_read_only(session)
        dialect = session.bind.dialect.name
        node_count = int(await session.scalar(select(func.count()).select_from(CatalogNode)))
        n05 = int(
            await session.scalar(
                select(func.count())
                .select_from(CatalogNode)
                .where(CatalogNode.code == DEFERRED_CONTENT_CODE)
            )
        )
        active_catalog = set(
            (
                await session.scalars(
                    select(CatalogNode.code).where(CatalogNode.active.is_(True))
                )
            ).all()
        )
        total = int(
            await session.scalar(select(func.count()).select_from(PedagogicalClassification))
        )
        v2_active = int(
            await session.scalar(
                select(func.count())
                .select_from(PedagogicalClassification)
                .where(
                    PedagogicalClassification.lifecycle == "ACTIVE",
                    PedagogicalClassification.metadata_["taxonomy_version"].as_string() == TAXONOMY,
                )
            )
        )
        integrity = await _integrity_counts(session)
        q_states = {
            d.official_number: await _q_state(session, d.official_number, d.content_code, active_catalog)
            for d in decisions
        }
        fp = await _protected_fingerprint(session)

    problems: list[str] = []
    if require_postgresql and dialect != "postgresql":
        problems.append(f"NOT_POSTGRESQL:{dialect}")
    if node_count != EXPECTED_CATALOG_NODE_COUNT:
        problems.append(f"catalog_nodes={node_count}!={EXPECTED_CATALOG_NODE_COUNT}")
    if n05 != 0:
        problems.append("N05_PRESENT")
    for k, v in integrity.items():
        if v:
            problems.append(f"{k}={v}")

    to_write: list[Decision] = []
    already: list[int] = []
    by_number = {d.official_number: d for d in decisions}
    done = 0
    for number, st in q_states.items():
        d = by_number[number]
        if number in PROTECTED_FINGERPRINT_NUMBERS:
            problems.append(f"#{number}_is_protected_in_write_set")
        if st.question_version_id is None:
            problems.append(f"#{number}_question_version_unresolved")
            continue
        if not st.catalog_node_active:
            problems.append(f"#{number}_content_{st.content_code}_node_not_active")
        if st.v2_active_count > 1:
            problems.append(f"#{number}_has_{st.v2_active_count}_ACTIVE_{TAXONOMY}_rows")
            continue
        if st.v2_active_count == 1:
            row = st.v2_active_rows[0]
            if row["content"] != d.content_code:
                problems.append(
                    f"#{number}_ACTIVE_{TAXONOMY}_content_{row['content']}!={d.content_code}"
                    "_HUMAN_REVIEW_REQUIRED"
                )
            elif row["mode"] != CLASSIFICATION_MODE:
                problems.append(
                    f"#{number}_ACTIVE_{TAXONOMY}_mode_{row['mode']}!={CLASSIFICATION_MODE}"
                )
            else:
                already.append(number)
                done += 1
        else:
            to_write.append(d)

    # count consistency: 18 + (already-done) expected before this run
    expected_total = EXPECTED_COUNT_BEFORE + done
    if total != expected_total:
        problems.append(
            f"classification_count={total}!={expected_total}(=18+{done}_already_done)"
        )
    expected_v2 = EXPECTED_V2_ACTIVE_BEFORE + done
    if v2_active != expected_v2:
        problems.append(f"{TAXONOMY}_active={v2_active}!={expected_v2}")

    ok = not problems
    return PreflightResult(
        ok=ok,
        detail="OK" if ok else "; ".join(problems),
        classification_count=total,
        v2_active_count=v2_active,
        catalog_node_count=node_count,
        n05_present=n05,
        integrity=integrity,
        q_states=q_states,
        protected_fingerprint=fp,
        to_write=to_write,
        already_classified=sorted(already),
    )


# --------------------------------------------------------------------------- #
# Write (ORM only — mirrors _persist_provider_output)
# --------------------------------------------------------------------------- #


def _build_row(session_qv_id: str, d: Decision, question_content_hash: str | None) -> PedagogicalClassification:
    return PedagogicalClassification(
        question_version_id=uuid.UUID(session_qv_id),
        discipline=_discipline_for(d.content_code) or "",
        content=d.content_code,
        subcontent="",
        difficulty=ROW_DIFFICULTY,
        classification_confidence=None,
        difficulty_confidence=None,
        reasoning_type=ROW_REASONING_TYPE,
        prerequisites=[],
        keywords=[],
        competencies=[],
        skills=[],
        model_name=None,
        model_version=None,
        prompt_version=None,
        provider_name=None,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        status=ROW_STATUS,
        source=ROW_SOURCE,
        lifecycle=ROW_LIFECYCLE,
        supersedes_id=None,
        metadata_={
            "taxonomy_version": TAXONOMY,
            "classification_mode": CLASSIFICATION_MODE,
            "decision_source": DECISION_SOURCE,
            "review_phase": PHASE,
            "content_code": d.content_code,
            "candidate_content_code": d.content_code,
            "curator_id": d.curator_id,
            "evidence_note": d.evidence_note,
            "question_content_hash": question_content_hash,
        },
    )


class UniqueCollision(H4Error):
    pass


async def write_one(session, d: Decision) -> str:
    """One human classification, one commit. Re-checks 0 ACTIVE curriculum-v2 for
    the question, then adds + commits via the ORM (no raw SQL). IntegrityError on
    the partial unique index -> rollback + UniqueCollision."""
    bqs = list(
        (
            await session.scalars(
                select(BookletQuestion).where(
                    BookletQuestion.official_number == d.official_number
                )
            )
        ).all()
    )
    qv_ids = {b.question_version_id for b in bqs}
    if len(qv_ids) != 1:
        raise H4Error(f"#{d.official_number}: question_version not uniquely resolvable")
    qv_id = next(iter(qv_ids))
    version = await session.get(QuestionVersion, qv_id)

    existing = [
        r
        for r in await _classification_rows(session, qv_id)
        if r.lifecycle == "ACTIVE" and (r.metadata_ or {}).get("taxonomy_version") == TAXONOMY
    ]
    if existing:
        raise H4Error(
            f"#{d.official_number}: an ACTIVE {TAXONOMY} row already exists — not overwriting"
        )

    row = _build_row(str(qv_id), d, getattr(version, "content_hash", None))
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise UniqueCollision(_scrub(str(exc))) from exc
    await session.refresh(row)
    return str(row.id)


async def audit_one(factory, d: Decision, new_id: str, pre_rows: dict[str, tuple]) -> None:
    async with factory() as session:
        await _begin_read_only(session)
        bqs = list(
            (
                await session.scalars(
                    select(BookletQuestion).where(
                        BookletQuestion.official_number == d.official_number
                    )
                )
            ).all()
        )
        qv_id = next(iter({b.question_version_id for b in bqs}))
        rows = await _classification_rows(session, qv_id)
        by_id = {str(r.id): r for r in rows}

        new_ids = set(by_id) - set(pre_rows)
        if new_ids != {new_id}:
            raise H4Error(
                f"#{d.official_number}: expected exactly new id {_hp(new_id)}, got "
                f"{sorted(_hp(i) for i in new_ids)}"
            )
        for old_id, key in pre_rows.items():
            if old_id not in by_id or _row_key(by_id[old_id]) != key:
                raise H4Error(f"#{d.official_number}: historical row {_hp(old_id)} changed/vanished")

        row = by_id[new_id]
        md = row.metadata_ or {}
        checks = {
            "lifecycle": row.lifecycle == "ACTIVE",
            "status": row.status == "CLASSIFIED",
            "content": row.content == d.content_code,
            "taxonomy_version": md.get("taxonomy_version") == TAXONOMY,
            "classification_mode": md.get("classification_mode") == CLASSIFICATION_MODE,
            "decision_source": md.get("decision_source") == DECISION_SOURCE,
            "review_phase": md.get("review_phase") == PHASE,
            "source": row.source == ROW_SOURCE,
            "supersedes_id": row.supersedes_id is None,
        }
        bad = [k for k, v in checks.items() if not v]
        if bad:
            raise H4Error(f"#{d.official_number}: new row failed {bad}")

        active_v2 = [
            r
            for r in rows
            if r.lifecycle == "ACTIVE" and (r.metadata_ or {}).get("taxonomy_version") == TAXONOMY
        ]
        if len(active_v2) != 1:
            raise H4Error(f"#{d.official_number}: {len(active_v2)} ACTIVE {TAXONOMY} rows after write")


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


@dataclass
class QResult:
    official_number: int
    state: str
    content_code: str
    question_version_id: str | None = None
    new_id: str | None = None
    detail: str = ""


@dataclass
class RunReport:
    dry_run: bool = True
    approval_present: bool = False
    decision_file_valid: bool = False
    decision_file_detail: str = ""
    preflight: PreflightResult | None = None
    planned_writes: int = 0
    writes_applied: int = 0
    results: list[QResult] = field(default_factory=list)
    stopped_on_error: int | None = None
    final_count: int | None = None
    final_v2_active: int | None = None
    final_integrity: dict[str, int] = field(default_factory=dict)
    protected_unchanged: dict[int, bool] = field(default_factory=dict)

    @property
    def protected_ok(self) -> bool:
        return bool(self.protected_unchanged) and all(self.protected_unchanged.values())

    @property
    def integrity_ok(self) -> bool:
        i = self.final_integrity or (self.preflight.integrity if self.preflight else {})
        return bool(i) and all(v == 0 for v in i.values())

    @property
    def ok(self) -> bool:
        return (
            self.decision_file_valid
            and self.preflight is not None
            and self.preflight.ok
            and self.stopped_on_error is None
            and self.integrity_ok
            and (self.dry_run or not self.approval_present or self.protected_ok)
        )


def is_dry_run(environ: dict[str, str] | None = None) -> bool:
    src = os.environ if environ is None else environ
    return src.get(DRY_RUN_ENV_VAR, "true").strip().lower() != "false"


def approval_granted(environ: dict[str, str] | None = None) -> bool:
    src = os.environ if environ is None else environ
    val = src.get(APPROVAL_ENV_VAR)
    if val is None or val in _REJECTED_TOKENS:
        return False
    return val == APPROVAL_TOKEN


async def run(
    factory,
    *,
    dry_run: bool,
    approval: bool,
    decision_path: Path | str = DECISION_FILE,
    require_postgresql: bool = True,
) -> RunReport:
    report = RunReport(dry_run=dry_run, approval_present=approval)

    try:
        decisions = load_decisions(decision_path)
        report.decision_file_valid = True
        report.decision_file_detail = "OK"
    except H4Error as exc:
        report.decision_file_valid = False
        report.decision_file_detail = _scrub(str(exc))
        return report

    try:
        pf = await preflight(factory, decisions, require_postgresql=require_postgresql)
    except H4Error as exc:
        report.decision_file_valid = True
        report.decision_file_detail = f"OK; preflight raised: {_scrub(str(exc))}"
        return report
    report.preflight = pf
    for d in decisions:
        st = pf.q_states[d.official_number]
        base = QResult(
            official_number=d.official_number,
            state="?",
            content_code=d.content_code,
            question_version_id=st.question_version_id,
        )
        if d.official_number in pf.already_classified:
            base.state = "ALREADY_CLASSIFIED"
        elif any(x.official_number == d.official_number for x in pf.to_write):
            base.state = "PLANNED_WRITE"
        else:
            base.state = "BLOCKED"
        report.results.append(base)
    report.planned_writes = len(pf.to_write)

    if not pf.ok:
        return report
    if dry_run or not approval:
        return report

    # ---- WRITE (only reached with dry_run=False + valid token + ok pre-flight) ----
    pre_rows_by_number: dict[int, dict[str, tuple]] = {}
    async with factory() as session:
        await _begin_read_only(session)
        for d in pf.to_write:
            st = pf.q_states[d.official_number]
            rows = await _classification_rows(session, uuid.UUID(st.question_version_id))
            pre_rows_by_number[d.official_number] = {str(r.id): _row_key(r) for r in rows}

    for d in pf.to_write:
        try:
            async with factory() as session:
                new_id = await write_one(session, d)
        except H4Error as exc:
            _mark(report, d.official_number, "ERROR", _scrub(str(exc)))
            report.stopped_on_error = d.official_number
            break
        try:
            await audit_one(factory, d, new_id, pre_rows_by_number[d.official_number])
        except H4Error as exc:
            _mark(report, d.official_number, "ERROR", _scrub(str(exc)), new_id=new_id)
            report.stopped_on_error = d.official_number
            break
        report.writes_applied += 1
        _mark(report, d.official_number, "CLASSIFIED", "", new_id=new_id)

    async with factory() as session:
        await _begin_read_only(session)
        report.final_count = int(
            await session.scalar(select(func.count()).select_from(PedagogicalClassification))
        )
        report.final_v2_active = int(
            await session.scalar(
                select(func.count())
                .select_from(PedagogicalClassification)
                .where(
                    PedagogicalClassification.lifecycle == "ACTIVE",
                    PedagogicalClassification.metadata_["taxonomy_version"].as_string() == TAXONOMY,
                )
            )
        )
        report.final_integrity = await _integrity_counts(session)
        fp_after = await _protected_fingerprint(session)
    report.protected_unchanged = {
        n: pf.protected_fingerprint.get(n) == fp_after.get(n)
        for n in sorted(PROTECTED_FINGERPRINT_NUMBERS)
    }
    return report


def _mark(report: RunReport, number: int, state: str, detail: str, *, new_id: str | None = None) -> None:
    for r in report.results:
        if r.official_number == number:
            r.state = state
            r.detail = detail
            if new_id:
                r.new_id = new_id
            return


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def render_report(report: RunReport) -> str:
    pf = report.preflight
    lines = ["PHASE 9U.2-H4 — DECISION EXECUTOR REPORT", ""]
    lines.append(f"DECISION_FILE:\n  {'VALID' if report.decision_file_valid else 'INVALID'}")
    if report.decision_file_detail and report.decision_file_detail != "OK":
        lines.append(f"  detail: {report.decision_file_detail}")
    lines.append("")
    lines.append("DECISIONS:")
    if report.results:
        for r in report.results:
            extra = f" new_id={_hp(r.new_id)}" if r.new_id else ""
            extra += f" ({r.detail})" if r.detail else ""
            lines.append(
                f"  Q{r.official_number} = CONFIRM -> {r.content_code}  [{r.state}]"
                f" qv={_hp(r.question_version_id)}{extra}"
            )
    else:
        for n, code in H4_EXPECTED_DECISIONS.items():
            lines.append(f"  Q{n} = CONFIRM -> {code}  [not evaluated]")
    lines.append("")
    lines.append(f"VALID_CONTENT_CODES:\n  {'PASS' if report.decision_file_valid else 'FAIL'}")
    lines.append("")
    lines.append(f"PRE_FLIGHT:\n  {'PASS' if (pf and pf.ok) else 'FAIL'}")
    if pf and not pf.ok:
        lines.append(f"  detail: {pf.detail}")
    lines += [
        "",
        f"INITIAL_CLASSIFICATION_COUNT:\n  {pf.classification_count if pf else 'NOT_READ'}",
        f"CURRICULUM_V2_ACTIVE:\n  {pf.v2_active_count if pf else 'NOT_READ'}",
        f"CATALOG_NODE_COUNT:\n  {pf.catalog_node_count if pf else 'NOT_READ'}",
        f"N05_PRESENT:\n  {pf.n05_present if pf else 'NOT_READ'}",
        "",
        f"ALREADY_CLASSIFIED:\n  {pf.already_classified if pf else '[]'}",
        f"PLANNED_WRITES:\n  {report.planned_writes}",
        f"WRITES_APPLIED:\n  {report.writes_applied}",
        "",
        f"DRY_RUN:\n  {str(report.dry_run).upper()}",
        f"APPROVAL_PRESENT:\n  {report.approval_present}",
    ]
    if not report.dry_run and report.approval_present and pf and pf.ok:
        lines += [
            "",
            f"POST_WRITE_CLASSIFICATION_COUNT:\n  {report.final_count}",
            f"POST_WRITE_CURRICULUM_V2_ACTIVE:\n  {report.final_v2_active}",
        ]
    lines += ["", "PROTECTED:"]
    if report.protected_unchanged:
        for n in sorted(PROTECTED_FINGERPRINT_NUMBERS):
            st = report.protected_unchanged.get(n)
            lines.append(f"  Q{n} = {'PASS' if st else 'FAIL'}")
    else:
        for n in sorted(PROTECTED_FINGERPRINT_NUMBERS):
            lines.append(f"  Q{n} = {'CAPTURED' if pf else 'NOT_CHECKED'}")
    integ = report.final_integrity or (pf.integrity if pf else {})
    lines += [
        "",
        "INTEGRITY:",
        f"  ACTIVE_DUPLICATES = {integ.get('active_duplicates', 'NA')}",
        f"  MULTIPLE_ACTIVE_PER_VERSION = {integ.get('multiple_active_per_version', 'NA')}",
        f"  ORPHAN_SUPERSESSIONS = {integ.get('orphan_supersessions', 'NA')}",
        f"  DEEP_SUPERSESSION_CHAINS = {integ.get('deep_supersession_chains', 'NA')}",
        "",
        "OPENAI_CALLS:\n  0",
        "ALEMBIC_EXECUTION:\n  0",
        f"DATABASE_WRITES:\n  {report.writes_applied}",
        "",
        f"STOPPED_ON_ERROR:\n  {report.stopped_on_error if report.stopped_on_error is not None else 'NO'}",
        "",
        "FINAL_DECISION:",
        (
            "PHASE_9U2_H4_DECISION_EXECUTOR_DRY_RUN_COMPLETE"
            if report.dry_run
            else (
                "PHASE_9U2_H4_DECISION_EXECUTOR_WRITE_COMPLETE"
                if report.ok and report.approval_present
                else "PHASE_9U2_H4_DECISION_EXECUTOR_NEEDS_REVIEW"
            )
        ),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


async def _amain(argv: list[str]) -> int:  # pragma: no cover - manual entrypoint
    parser = argparse.ArgumentParser(description="PHASE 9U.2-H4 decision executor")
    parser.add_argument("--decisions", default=str(DECISION_FILE))
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_FILE, override=False)
    except Exception:
        pass

    from agente_ia_edu.db.session import create_engine, create_session_factory, get_database_url

    try:
        get_database_url()
    except Exception as exc:
        print(
            "PHASE 9U.2-H4 — DECISION EXECUTOR REPORT\n"
            f"FATAL: {_scrub(str(exc))}\n\nFINAL_DECISION:\nPHASE_9U2_H4_DECISION_EXECUTOR_NEEDS_REVIEW"
        )
        return 1

    dry_run = is_dry_run()
    approval = approval_granted()
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    try:
        report = await run(factory, dry_run=dry_run, approval=approval, decision_path=args.decisions)
    finally:
        await engine.dispose()

    print(render_report(report))
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - manual entrypoint
    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
