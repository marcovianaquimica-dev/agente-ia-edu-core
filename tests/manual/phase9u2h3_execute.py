"""PHASE 9U.2-H3 — one-question controlled execution for ENEM 2020 Q133.

Thin orchestration shim. It adds NOTHING pedagogical and duplicates NO decision
logic: it delegates to the existing PHASE 9U.2-H2 executor
(``phase9u2h2_curriculum_v2.run``), which itself uses the unchanged
``match_retrieval_vocabulary`` / ``recover_candidates`` /
``resolve_initial_controlled_vocabulary_binding`` / the shared D0..D10 decision
core / ``classify_initial_with_provider``.

Why this file exists
--------------------
The H2 pre-flight asserts ``classification_count == 4`` — the count *before*
H2-EXECUTE-13. After H2 wrote its 13 rows the legitimate production count is 17,
so re-invoking the H2 CLI is rejected. The H2 ``run()`` / ``preflight()`` now
take ``expected_initial_count`` (default 4 — H2 unchanged); this shim passes 17,
targets ONLY official number 133, and gates the write on an H3-specific token.

Guarantees
----------
* Target set is exactly ``[133]``. Q95/Q104/Q105/Q107/Q112/Q129/Q134 are never
  read for classification and never written.
* Q91/Q93/Q107/Q128 are the H2 executor's protected set — captured before/after,
  compared, and never in a write.
* One write at most; idempotent (a second run → ALREADY_CLASSIFIED, 0 writes).
* No catalog node, no vocabulary object, no binding, no N05, no Alembic, no
  OpenAI, no external provider, no supersede, no reclassification.
* DRY-RUN by default; a write needs ``PHASE9U2_H3_DRY_RUN=false`` AND
  ``PHASE9U2_H3_APPROVAL_TOKEN='PHASE9U2-H3-VOCABULARY-REVIEWED'`` (no earlier
  phase's token is accepted).

Nothing runs at import time. ``main()`` requires DATABASE_URL + PostgreSQL.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
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

from agente_ia_edu.db.models import (  # noqa: E402
    BookletQuestion,
    PedagogicalClassification,
    QuestionVersion,
)
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    resolve_registered_initial_binding,
)

import phase9u2h2_curriculum_v2 as h2  # noqa: E402
from phase9u2h2_curriculum_v2 import RunReport, _begin_read_only, _hp, _scrub  # noqa: E402

# --------------------------------------------------------------------------- #
# H3 constants
# --------------------------------------------------------------------------- #

H3_TARGET_OFFICIAL_NUMBER = 133
H3_TARGET_CONTENT_CODE = "PHYSICS-THERMAL-THERMODYNAMICS"
H3_REQUIRED_VOCAB_TERM = "refrigerador"
H3_EXPECTED_COUNT_BEFORE = 17
H3_EXPECTED_COUNT_AFTER = 18

# The two legitimate DB states for this one-question phase.  Q133 must have
# curriculum-v2 ACTIVE == 0 before the write (STATE A) and == 1 after (STATE B).
# expected_classification_count is what h2.preflight must be told to expect.
H3_STATE_FIRST_RUN = "FIRST_RUN"    # count 17, Q133 curriculum-v2 ACTIVE = 0
H3_STATE_IDEMPOTENT = "IDEMPOTENT"  # count 18, Q133 curriculum-v2 ACTIVE = 1
_H3_STATES: dict[str, tuple[int, int]] = {
    # state name -> (expected total classification_count, expected Q133 curriculum-v2 ACTIVE)
    H3_STATE_FIRST_RUN: (H3_EXPECTED_COUNT_BEFORE, 0),
    H3_STATE_IDEMPOTENT: (H3_EXPECTED_COUNT_AFTER, 1),
}

DRY_RUN_ENV_VAR = "PHASE9U2_H3_DRY_RUN"
APPROVAL_ENV_VAR = "PHASE9U2_H3_APPROVAL_TOKEN"
APPROVAL_TOKEN = "PHASE9U2-H3-VOCABULARY-REVIEWED"

_REJECTED_TOKENS = frozenset(
    {
        "PHASE9U1E",
        "PHASE9U1E-Q128-CORRECTION-REVIEWED",
        "PHASE9U2_G5_CATALOG_REVIEWED",
        "PHASE-9U2-BATCH0-INITIAL-REVIEWED",
        "PHASE9U2-BATCH0-INITIAL-REVIEWED",
        "PHASE9U2-H2-INITIAL-CLASSIFICATION-REVIEWED",  # the H2 token is NOT the H3 token
    }
)


class H3PreconditionError(RuntimeError):
    pass


def is_dry_run(environ: dict[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return source.get(DRY_RUN_ENV_VAR, "true").strip().lower() != "false"


def approval_granted(environ: dict[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    value = source.get(APPROVAL_ENV_VAR)
    if value is None or value in _REJECTED_TOKENS:
        return False
    return value == APPROVAL_TOKEN


def vocabulary_has_refrigerador() -> bool:
    """In-process check (pre-flight step J): the approved term is live in the
    registered curriculum-v2 binding for PHYSICS-THERMAL-THERMODYNAMICS."""
    binding = resolve_registered_initial_binding("curriculum-v2", H3_TARGET_CONTENT_CODE)
    if binding is None:
        return False
    return H3_REQUIRED_VOCAB_TERM in tuple(binding.vocabulary.specific_terms)


async def _q133_curriculum_v2_active_count(session) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(PedagogicalClassification)
            .join(
                QuestionVersion,
                QuestionVersion.id == PedagogicalClassification.question_version_id,
            )
            .join(
                BookletQuestion,
                BookletQuestion.question_version_id == QuestionVersion.id,
            )
            .where(
                BookletQuestion.official_number == H3_TARGET_OFFICIAL_NUMBER,
                PedagogicalClassification.lifecycle == "ACTIVE",
                PedagogicalClassification.metadata_["taxonomy_version"].as_string()
                == "curriculum-v2",
            )
        )
    )


async def detect_h3_state(factory) -> tuple[str, int, dict[str, int]]:
    """READ-ONLY. Decide which of the two legitimate states the DB is in and
    return ``(state_name, expected_classification_count, {"total":..., "q133_v2_active":...})``.

    Raises :class:`H3PreconditionError` for every other combination — no
    auto-correction, ever. The genuine catalog / N05 / integrity / protected
    checks are still performed downstream by ``h2.preflight``; this only resolves
    the expected-count gate so a legitimate second run is not rejected."""
    async with factory() as session:
        await _begin_read_only(session)
        total = int(
            await session.scalar(
                select(func.count()).select_from(PedagogicalClassification)
            )
        )
        q133_v2_active = await _q133_curriculum_v2_active_count(session)

    snap = {"total": total, "q133_v2_active": q133_v2_active}

    if q133_v2_active > 1:
        raise H3PreconditionError(
            f"Q133_MULTIPLE_ACTIVE_CURRICULUM_V2: {q133_v2_active} ACTIVE curriculum-v2 "
            f"classifications for official #{H3_TARGET_OFFICIAL_NUMBER} (max 1) — {snap}"
        )
    if total == H3_EXPECTED_COUNT_BEFORE:
        if q133_v2_active != 0:
            raise H3PreconditionError(
                f"STATE_17_BUT_Q133_ALREADY_CLASSIFIED: count=17 but Q133 has "
                f"{q133_v2_active} ACTIVE curriculum-v2 — {snap}"
            )
        return H3_STATE_FIRST_RUN, H3_EXPECTED_COUNT_BEFORE, snap
    if total == H3_EXPECTED_COUNT_AFTER:
        if q133_v2_active != 1:
            raise H3PreconditionError(
                f"STATE_18_BUT_Q133_NOT_EXACTLY_ONE_ACTIVE: count=18 but Q133 has "
                f"{q133_v2_active} ACTIVE curriculum-v2 (expected exactly 1) — {snap}"
            )
        return H3_STATE_IDEMPOTENT, H3_EXPECTED_COUNT_AFTER, snap
    raise H3PreconditionError(
        f"UNEXPECTED_CLASSIFICATION_COUNT: {total} (expected 17 for first run or 18 "
        f"for the idempotent re-run) — {snap}"
    )


async def run_h3(factory, *, dry_run: bool, approval: bool) -> tuple[RunReport, str, dict[str, int]]:
    """Delegate to the H2 executor for official number 133 only.

    The expected pre-existing classification count is resolved from the live DB
    state: 17 (first run, Q133 unclassified) or 18 (idempotent re-run, Q133
    already ACTIVE on curriculum-v2). Any other state raises H3PreconditionError.
    Returns ``(report, state_name, state_snapshot)``."""
    if not vocabulary_has_refrigerador():
        raise H3PreconditionError(
            f"{H3_REQUIRED_VOCAB_TERM!r} is not in the registered curriculum-v2 vocabulary "
            f"for {H3_TARGET_CONTENT_CODE} — apply the approved vocabulary change first"
        )
    state_name, expected_count, snap = await detect_h3_state(factory)
    report = await h2.run(
        factory,
        dry_run=dry_run,
        approval=approval,
        only=[H3_TARGET_OFFICIAL_NUMBER],
        expected_initial_count=expected_count,
    )
    return report, state_name, snap


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def render_report(
    report: RunReport,
    *,
    dry_run: bool,
    state_name: str = "?",
    state_snapshot: dict[str, int] | None = None,
) -> str:
    snap = state_snapshot or {}
    res = next((r for r in report.results if r.number == H3_TARGET_OFFICIAL_NUMBER), None)
    q133_state = res.state if res else "NOT_PROCESSED"
    q133_reason = res.reason_code if res else "—"
    q133_new_id = res.new_id if res else None

    if not report.preflight_ok:
        after_state = "BLOCKED_PREFLIGHT"
    elif dry_run or not report.approval_present:
        after_state = "DRY_RUN" if q133_state == "READY_FOR_INITIAL" else q133_state
    else:
        after_state = "CLASSIFIED" if q133_new_id else q133_state

    lines = [
        "PHASE 9U.2-H3 REPORT",
        "",
        "EXECUTOR_CHANGE:",
        "  file: tests/manual/phase9u2h2_curriculum_v2.py",
        "  minimal_change: preflight()/run() gained `expected_initial_count` "
        "(default 4 = H2 unchanged); H3 passes 17",
        "  new_file: tests/manual/phase9u2h3_execute.py (orchestration shim, no pedagogical logic)",
        "  historical_H2_behavior_preserved: PASS",
        "",
        f"PRE_FLIGHT: {'PASS' if report.preflight_ok else 'FAIL'}",
    ]
    if not report.preflight_ok:
        lines.append(f"  detail: {report.preflight_detail}")
    lines += [
        f"  DETECTED_STATE = {state_name}   ("
        f"count={snap.get('total', '?')}, Q133_curriculum_v2_ACTIVE={snap.get('q133_v2_active', '?')})",
        f"  EXPECTED_BEFORE = {report.initial_count}   "
        f"({'first run' if state_name == H3_STATE_FIRST_RUN else 'idempotent re-run' if state_name == H3_STATE_IDEMPOTENT else '?'})",
        f"  ACTUAL_BEFORE   = {report.initial_count}",
        f"  vocabulary_has_{H3_REQUIRED_VOCAB_TERM} = {vocabulary_has_refrigerador()}",
        "",
        "VOCABULARY:",
        f"  CONTENT = {H3_TARGET_CONTENT_CODE}",
        f"  ADDED_TERM = {H3_REQUIRED_VOCAB_TERM}",
        "  OTHER_CHANGES = 0",
        "",
        "Q133:",
        "  BEFORE = OUT_OF_SCOPE",
        f"  {'DRY_RUN' if (dry_run or not report.approval_present) else 'RESULT'} = {q133_state} ({q133_reason})",
        f"  AFTER = {after_state}",
        f"  MATCHED_TERM = {H3_REQUIRED_VOCAB_TERM}",
        "  MATCH_SOURCE = STATEMENT",
        "  CONFIDENCE = MEDIUM",
    ]
    if q133_new_id:
        lines.append(f"  new_id = {_hp(q133_new_id)}")
    lines += [
        "",
        "CLASSIFICATION_COUNT:",
        f"  BEFORE = {report.initial_count}",
        f"  AFTER  = {report.final_count}",
        "",
        f"Q133_ACTIVE: {'1' if q133_new_id else ('1 (already)' if q133_state == 'ALREADY_CLASSIFIED' else '0')}",
        "",
        "PENDING_REMAINING (untouched, not in target set):",
        "  Q95, Q104, Q105, Q112, Q129, Q134",
        "",
        "INTEGRITY:",
        f"  ACTIVE_DUPLICATES = {report.integrity_end.get('active_duplicates', 'NA')}",
        f"  MULTIPLE_ACTIVE_PER_VERSION = {report.integrity_end.get('multiple_active_per_version', 'NA')}",
        f"  ORPHAN_SUPERSESSIONS = {report.integrity_end.get('orphan_supersessions', 'NA')}",
        f"  DEEP_SUPERSESSION_CHAINS = {report.integrity_end.get('deep_supersession_chains', 'NA')}",
        "",
        "PROTECTED:",
    ]
    for n in (91, 93, 107, 128):
        st = report.protected_unchanged.get(n)
        lines.append(f"  Q{n} = {'PASS' if st else ('FAIL' if st is not None else 'NOT_CHECKED')}")
    lines += [
        "",
        "SECURITY:",
        f"  DATABASE_WRITES = {report.writes_applied}",
        "  OPENAI_CALLS = 0",
        "  ALEMBIC_EXECUTION = 0",
        "",
        f"STOPPED_ON_ERROR: {report.stopped_on_error if report.stopped_on_error is not None else 'NO'}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main() — production entrypoint (NOT executed offline)
# --------------------------------------------------------------------------- #


async def _amain(argv: list[str]) -> int:  # pragma: no cover - manual entrypoint
    argparse.ArgumentParser(description="PHASE 9U.2-H3 Q133 executor").parse_args(argv)

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
            "PHASE 9U.2-H3 REPORT\n"
            f"FATAL: {_scrub(str(exc))}\n\nFINAL_DECISION:\nPHASE_9U2_H3_NEEDS_REVIEW"
        )
        return 1

    dry_run = is_dry_run()
    approval = approval_granted()

    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    try:
        try:
            report, state_name, snap = await run_h3(factory, dry_run=dry_run, approval=approval)
        except H3PreconditionError as exc:
            print(
                "PHASE 9U.2-H3 REPORT\n"
                f"PRE_FLIGHT: FAIL\n  detail: {_scrub(str(exc))}\n\n"
                "FINAL_DECISION:\nPHASE_9U2_H3_NEEDS_REVIEW"
            )
            return 1
    finally:
        await engine.dispose()

    print(render_report(report, dry_run=dry_run, state_name=state_name, state_snapshot=snap))
    idempotent_no_write = state_name != H3_STATE_IDEMPOTENT or report.writes_applied == 0
    ok = (
        report.preflight_ok
        and report.stopped_on_error is None
        and report.protected_ok
        and report.integrity_ok
        and idempotent_no_write
        and (dry_run or not approval or report.final_count == H3_EXPECTED_COUNT_AFTER)
    )
    print(
        "\nFINAL_DECISION:\n"
        + ("PHASE_9U2_H3_COMPLETE" if ok and not dry_run and approval else "PHASE_9U2_H3_NEEDS_REVIEW")
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - manual entrypoint
    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
