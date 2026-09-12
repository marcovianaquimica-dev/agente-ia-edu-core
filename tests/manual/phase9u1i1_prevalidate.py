"""PHASE 9U.1-I.1 — operator-shell READ-ONLY pre-validation for the Q128 correction.

Run from the operator terminal (where DATABASE_URL is configured):

    cd /Users/marcoviana/agente-ia-edu-core
    .venv/bin/python /private/tmp/claude-501/-Users-marcoviana-agente-ia-edu-core/7b7ab38b-78be-4363-8830-2f94920c1e88/scratchpad/phase9u1i1_prevalidate.py

It performs ONLY:
  * one PostgreSQL session, `SET TRANSACTION READ ONLY` as the first statement
    (via the existing tests/manual/phase9u1e_q128_correction.py::capture_snapshot),
  * SELECTs to read Q128 / Q93 / totals / duplicates,
  * pure, in-memory evaluation of plan_correction() and the structural guards.

It NEVER writes, NEVER runs Alembic, NEVER calls OpenAI, NEVER runs the correction.
Exit code 0 == CORRECTION_APPROVED_FOR_EXECUTION, 1 == CORRECTION_NEEDS_REVIEW.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve()
# Fall back to the known checkout if this file is run from the scratchpad.
for candidate in (Path.cwd(), Path("/Users/marcoviana/agente-ia-edu-core")):
    if (candidate / "src" / "agente_ia_edu").is_dir():
        REPO_ROOT = candidate
        break
sys.path.insert(0, str(REPO_ROOT / "tests" / "manual"))
sys.path.insert(0, str(REPO_ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

import phase9u1e_q128_correction as routine  # noqa: E402
from agente_ia_edu.db.models import (  # noqa: E402
    BookletQuestion,
    PedagogicalClassification,
    QuestionVersion,
)
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    ClassificationProposalService,
)

REQUIRED_REVISION = routine.REQUIRED_ALEMBIC_REVISION            # 025_classification_lifecycle
KNOWN_QV = routine.KNOWN_QUESTION_VERSION_ID                     # 7709144c-...-340a
KNOWN_CLS = routine.KNOWN_EXISTING_CLASSIFICATION_ID            # 9685d51a-...-d931
WRONG_CC = routine.WRONG_CONTENT_CODE                           # CHEMISTRY-SOLUTIONS
GOOD_CC = routine.EXPECTED_CONTENT_CODE                         # CHEMISTRY-PHYSICAL-KINETICS
TAXO = routine.TAXONOMY_VERSION                                 # 024_chemistry_kinetics
ORIG_MODEL = routine.EXPECTED_ORIGINAL_CLASSIFIER_VERSION       # phase9u1-initial-v1
ORIG_PROMPT = routine.EXPECTED_ORIGINAL_PROMPT_VERSION          # phase9t3-kinetics-v1


def _flag(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _evaluate_structural_guards() -> dict[str, str]:
    main_src = inspect.getsource(routine.main)
    module_src = inspect.getsource(routine)
    supersede_src = inspect.getsource(
        ClassificationProposalService.supersede_initial_classification
    )

    # DRY-RUN gate: default true; only exact "false" enables writing.
    dry_run_guard = (
        routine.is_dry_run({}) is True
        and routine.is_dry_run({routine.DRY_RUN_ENV_VAR: "true"}) is True
        and routine.is_dry_run({routine.DRY_RUN_ENV_VAR: "false"}) is False
        and routine.is_dry_run({routine.DRY_RUN_ENV_VAR: "False"}) is False
    )

    # Approval gate: no token -> blocked; correct token -> allowed; wrong token -> blocked.
    approval_guard = (
        routine.approval_granted({}, interactive=False) is False
        and routine.approval_granted({routine.APPROVAL_ENV_VAR: routine.APPROVAL_TOKEN}) is True
        and routine.approval_granted({routine.APPROVAL_ENV_VAR: "not-the-token"}) is False
    )

    # No write may be reached before BOTH gates: the dry-run early-out and the
    # approval check both precede the only supersede call in main().
    order_ok = (
        "elif dry_run:" in main_src
        and "approval_granted()" in main_src
        and main_src.index("approval_granted()")
        < main_src.index("supersede_initial_classification(")
        and main_src.count("supersede_initial_classification(") == 1
    )

    # Single, scoped write path: only supersede_initial_classification(); no manual
    # DML, no session.add of a classification, no loop over questions.
    no_mass = (
        "session.add(" not in module_src
        and "INSERT INTO" not in module_src.upper()
        and "DELETE FROM" not in module_src.upper()
        and "UPDATE PEDAGOGICAL" not in module_src.upper()
        and module_src.count("KNOWN_OFFICIAL_NUMBER = 128") == 1
        and order_ok
    )

    # Atomic hand-off inside the one write method: flips old -> SUPERSEDED,
    # rolls back on any failure, commits exactly once.
    transactional = (
        'old.lifecycle = "SUPERSEDED"' in supersede_src
        and "await self.session.rollback()" in supersede_src
        and supersede_src.count("await self.session.commit()") == 1
        and "new.supersedes_id = old.id" in supersede_src
    )

    # Idempotency: a snapshot whose ACTIVE row is already KINETICS yields a NO-OP
    # plan (ALREADY_CORRECT), never a second supersession.
    already_correct_row = {
        "id": "0f0f0f0f-already-corrected-row-000000000000",
        "question_version_id": KNOWN_QV,
        "status": "CLASSIFIED",
        "lifecycle": "ACTIVE",
        "supersedes_id": KNOWN_CLS,
        "classifier_version": routine.CORRECTION_CLASSIFIER_VERSION,
        "prompt_version": routine.CORRECTION_PROMPT_VERSION,
        "content_code": GOOD_CC,
        "taxonomy_version": TAXO,
        "classification_mode": routine.CLASSIFICATION_MODE,
        "input_hash_prefix": None,
        "output_hash_prefix": None,
        "created_at": None,
    }
    synthetic = routine.CorrectionSnapshot(
        alembic_revision=REQUIRED_REVISION,
        question_found=True,
        question_version_id=KNOWN_QV,
        content_available=True,
        all_classifications=(already_correct_row,),
        binding_status="BOUND",
        binding_content_code=GOOD_CC,
        kinetics_candidate_recovered=True,
        other_rows_with_correction_tag=0,
    )
    try:
        idempotency = routine.plan_correction(synthetic).action == "ALREADY_CORRECT"
    except routine.CorrectionError:
        idempotency = False

    return {
        "DRY_RUN_GUARD": _flag(dry_run_guard),
        "APPROVAL_GUARD": _flag(approval_guard),
        "TRANSACTIONAL_SAFETY": _flag(transactional),
        "IDEMPOTENCY": _flag(idempotency),
        "NO_MASS_RECLASSIFICATION": _flag(no_mass),
    }


async def _read_state():
    load_dotenv(routine.ENV_FILE, override=False)
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                # First statement in the transaction is SET TRANSACTION READ ONLY.
                snapshot = await routine.capture_snapshot(session)

                total = await session.scalar(
                    select(func.count()).select_from(PedagogicalClassification)
                )
                q93_rows = list(
                    (
                        await session.scalars(
                            select(PedagogicalClassification)
                            .join(
                                QuestionVersion,
                                QuestionVersion.id
                                == PedagogicalClassification.question_version_id,
                            )
                            .join(
                                BookletQuestion,
                                BookletQuestion.question_version_id == QuestionVersion.id,
                            )
                            .where(BookletQuestion.official_number == 93)
                        )
                    ).all()
                )
                active_pairs = list(
                    (
                        await session.execute(
                            select(
                                PedagogicalClassification.question_version_id,
                                PedagogicalClassification.metadata_,
                            ).where(PedagogicalClassification.lifecycle == "ACTIVE")
                        )
                    ).all()
                )
    finally:
        await engine.dispose()

    pair_counts = Counter(
        (str(qv), (md or {}).get("taxonomy_version")) for qv, md in active_pairs
    )
    active_duplicates = sum(
        1 for (qv, taxo), n in pair_counts.items() if taxo is not None and n > 1
    )

    q93_ok = (
        len(q93_rows) == 1
        and q93_rows[0].lifecycle == "ACTIVE"
        and q93_rows[0].content == GOOD_CC
        and q93_rows[0].model_version == ORIG_MODEL
        and q93_rows[0].prompt_version == ORIG_PROMPT
        and q93_rows[0].status == "NEEDS_REVIEW"
        and (q93_rows[0].metadata_ or {}).get("taxonomy_version") == TAXO
        and (q93_rows[0].metadata_ or {}).get("classification_mode") == "INITIAL"
        and q93_rows[0].supersedes_id is None
    )
    return snapshot, int(total or 0), len(q93_rows), q93_ok, active_duplicates


def main() -> int:
    guards = _evaluate_structural_guards()
    try:
        snap, total, q93_count, q93_ok, active_duplicates = asyncio.run(_read_state())
    except Exception as exc:  # fail closed, never guess
        print("PHASE 9U.1-I.1 — OPERATOR PRE-VALIDATION")
        print(f"FATAL: {routine._scrub(type(exc).__name__ + ': ' + str(exc))}")
        print()
        print("FINAL_DECISION:")
        print("CORRECTION_NEEDS_REVIEW")
        return 1

    active_024 = [
        r
        for r in snap.all_classifications
        if r["lifecycle"] == "ACTIVE"
        and r["classification_mode"] == "INITIAL"
        and r["taxonomy_version"] == TAXO
    ]
    cur = active_024[0] if len(active_024) == 1 else None
    q128_superseded_rows = sum(1 for r in snap.all_classifications if r["supersedes_id"])

    plan = None
    plan_error = None
    try:
        plan = routine.plan_correction(snap)
    except routine.CorrectionError as exc:
        plan_error = routine._scrub(str(exc))

    if plan is None:
        plan_action = "BLOCKED"
    elif plan.action == "READY":
        plan_action = "SUPERSEDE_AND_RECLASSIFY"
    elif plan.action == "ALREADY_CORRECT":
        plan_action = "NO_OP_ALREADY_CORRECT"
    else:
        plan_action = plan.action

    binding_ok = (
        snap.binding_status == "BOUND"
        and snap.binding_content_code == GOOD_CC
        and snap.kinetics_candidate_recovered
    )
    qv_match = snap.question_version_id == KNOWN_QV
    cls_match = bool(cur) and cur["id"] == KNOWN_CLS

    guards_ok = all(v == "PASS" for v in guards.values())
    core_ok = (
        snap.alembic_revision == REQUIRED_REVISION
        and snap.question_found
        and qv_match
        and len(snap.all_classifications) == 1
        and len(active_024) == 1
        and cls_match
        and cur["content_code"] == WRONG_CC
        and cur["lifecycle"] == "ACTIVE"
        and cur["classification_mode"] == "INITIAL"
        and cur["taxonomy_version"] == TAXO
        and cur["classifier_version"] == ORIG_MODEL
        and cur["prompt_version"] == ORIG_PROMPT
        and cur["supersedes_id"] is None
        and q128_superseded_rows == 0
        and snap.other_rows_with_correction_tag == 0
        and binding_ok
        and plan is not None
        and plan.action == "READY"
        and total == 3
        and q93_count == 1
        and q93_ok
        and active_duplicates == 0
    )
    final = (
        "CORRECTION_APPROVED_FOR_EXECUTION"
        if (core_ok and guards_ok)
        else "CORRECTION_NEEDS_REVIEW"
    )

    def show(value):
        return "NOT_FOUND" if value is None else value

    print("PHASE 9U.1-I.1 — OPERATOR PRE-VALIDATION")
    print()
    print(
        f"ALEMBIC_REVISION: {show(snap.alembic_revision)} "
        f"({_flag(snap.alembic_revision == REQUIRED_REVISION)})"
    )
    print(f"Q128_FOUND: {snap.question_found}")
    print(f"Q128_VERSION_ID: {show(snap.question_version_id)}")
    print(f"Q128_VERSION_ID_MATCH: {_flag(qv_match)}")
    print(f"CURRENT_CLASSIFICATION_ID: {show(cur['id'] if cur else None)}")
    print(f"CURRENT_CLASSIFICATION_ID_MATCH: {_flag(cls_match)}")
    print(f"CURRENT_CONTENT_CODE: {show(cur['content_code'] if cur else None)} "
          f"({_flag(bool(cur) and cur['content_code'] == WRONG_CC)})")
    print(f"CURRENT_LIFECYCLE: {show(cur['lifecycle'] if cur else None)} "
          f"({_flag(bool(cur) and cur['lifecycle'] == 'ACTIVE')})")
    print(f"CURRENT_CLASSIFICATION_MODE: {show(cur['classification_mode'] if cur else None)} "
          f"({_flag(bool(cur) and cur['classification_mode'] == 'INITIAL')})")
    print(f"CURRENT_TAXONOMY_VERSION: {show(cur['taxonomy_version'] if cur else None)} "
          f"({_flag(bool(cur) and cur['taxonomy_version'] == TAXO)})")
    print(f"CONTROLLED_VOCABULARY_BINDING: {show(snap.binding_status)} "
          f"({_flag(snap.binding_status == 'BOUND')})")
    print(f"BOUND_CONTENT_CODE: {show(snap.binding_content_code)} "
          f"({_flag(snap.binding_content_code == GOOD_CC)})")
    print(f"SUPERSESSION_READY: {_flag(plan is not None and plan.action == 'READY')}")
    print(f"PLAN_ACTION: {plan_action}"
          + (f"  [blocked: {plan_error}]" if plan_error else ""))
    print(f"OTHER_ACTIVE_CLASSIFICATIONS: {max(len(active_024) - 1, 0)} "
          f"({_flag(len(active_024) == 1)})")
    print(f"OTHER_QUESTIONS_AFFECTED: 0 ({_flag(snap.other_rows_with_correction_tag == 0)})")
    print(f"Q93_UNCHANGED: {_flag(q93_ok)}")
    print(f"ACTIVE_DUPLICATES: {active_duplicates} ({_flag(active_duplicates == 0)})")
    print(f"DRY_RUN_GUARD: {guards['DRY_RUN_GUARD']}")
    print(f"APPROVAL_GUARD: {guards['APPROVAL_GUARD']}")
    print(f"TRANSACTIONAL_SAFETY: {guards['TRANSACTIONAL_SAFETY']}")
    print(f"IDEMPOTENCY: {guards['IDEMPOTENCY']}")
    print(f"NO_MASS_RECLASSIFICATION: {guards['NO_MASS_RECLASSIFICATION']}")
    print("OPENAI_CALLS: 0")
    print("POSTGRESQL_WRITES: 0")
    print("DATABASE_WRITES: 0")
    print()
    print(f"TOTAL_CLASSIFICATIONS: {total} ({_flag(total == 3)})")
    print(f"Q128_CLASSIFICATIONS: {len(snap.all_classifications)} "
          f"({_flag(len(snap.all_classifications) == 1)})")
    print(f"Q128_SUPERSEDED_ROWS: {q128_superseded_rows} ({_flag(q128_superseded_rows == 0)})")
    print()
    print("FINAL_DECISION:")
    print(final)
    return 0 if final == "CORRECTION_APPROVED_FOR_EXECUTION" else 1


if __name__ == "__main__":
    raise SystemExit(main())
