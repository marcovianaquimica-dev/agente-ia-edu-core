"""PHASE 9U.2-E — production READ-ONLY pre-flight for Batch-0.

Run from the operator terminal, where DATABASE_URL is the production database:

    cd /Users/marcoviana/agente-ia-edu-core
    PYTHONPATH=src .venv/bin/python tests/manual/phase9u2e_preflight.py

OBSERVATION ONLY. Guarantees by construction:
  * a single PostgreSQL session / transaction; `SET TRANSACTION READ ONLY` is
    the first statement (via phase9u2_batch0._begin_read_only); SELECT only.
  * the decision for every official question is computed by the *reviewed*
    Batch-0 pure core `phase9u2_batch0.decide_question_state` fed by
    `phase9u2_batch0.read_snapshot_core` — so this pre-flight's table is
    exactly what Batch-0 would decide.
  * no INSERT/UPDATE/DELETE, no DDL, no Alembic, no provider, no OpenAI.
    `phase9u2_batch0.do_write` / `apply_initial` are never referenced.
  * nothing is modified; nothing is "fixed".

Exit 0 == PHASE_9U2_PRODUCTION_PREFLIGHT_PASS
Exit 1 == PHASE_9U2_PRODUCTION_PREFLIGHT_NEEDS_REVIEW  (fail closed)
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _cand in (Path.cwd(), _HERE.parents[1]):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        _ENV_FILE = _cand / ".env"
        break
else:  # pragma: no cover
    _ENV_FILE = Path(".env")
sys.path.insert(0, str(_HERE))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402

from agente_ia_edu.db.models import (  # noqa: E402
    BookletQuestion,
    PedagogicalClassification,
    QuestionVersion,
)
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    _INITIAL_CONTROLLED_VOCABULARIES,
    KINETICS_RETRIEVAL_VOCABULARY,
)

import phase9u2_batch0 as b0  # noqa: E402

REQUIRED_REVISION = "025_classification_lifecycle"
TARGET = b0.TARGET_TAXONOMY
CANON = b0.CANONICAL_CONTENT_CODE
PROTECTED = sorted(b0.PROTECTED_OFFICIAL_NUMBERS | {91})


def _hp(v) -> str:
    if v is None:
        return "None"
    s = str(v)
    return (s[:12] + "…") if len(s) > 12 else s


def _md(row) -> dict:
    return row.metadata_ or {}


def _fingerprint(rows) -> tuple:
    return tuple(sorted(b0.RowView.from_row(r).immutable_key for r in rows))


async def _run() -> int:
    load_dotenv(_ENV_FILE, override=False)
    problems: list[str] = []
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                await b0._begin_read_only(session)  # SET TRANSACTION READ ONLY (first stmt)

                # 1. ALEMBIC
                alembic_rev = (
                    await session.execute(text("SELECT version_num FROM alembic_version"))
                ).scalars().first()

                # catalog (once, read-only)
                catalog_nodes = await b0.load_catalog(session)
                catalog_codes = frozenset(n.code for n in catalog_nodes)

                # 2. OFFICIAL INVENTORY
                inventory = await b0.official_numbers(session)
                total_qv = int(await session.scalar(select(func.count()).select_from(QuestionVersion)))
                total_bq = int(await session.scalar(select(func.count()).select_from(BookletQuestion)))
                total_bq_official = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(BookletQuestion)
                        .where(BookletQuestion.official_number.is_not(None))
                    )
                )
                bq_rows = list(
                    (
                        await session.execute(
                            select(
                                BookletQuestion.official_number,
                                BookletQuestion.exam_booklet_id,
                                BookletQuestion.question_version_id,
                            ).where(BookletQuestion.official_number.is_not(None))
                        )
                    ).all()
                )
                per_number_qv: dict[int, set] = {}
                per_booklet_number = Counter()
                bad_number = []
                for num, booklet, qv in bq_rows:
                    per_number_qv.setdefault(num, set()).add(qv)
                    per_booklet_number[(booklet, num)] += 1
                    if num is None or num <= 0:
                        bad_number.append(num)
                dup_in_booklet = [k for k, c in per_booklet_number.items() if c > 1]
                ambiguous_numbers = sorted(n for n, s in per_number_qv.items() if len(s) != 1)

                qv_ids_official = {qv for s in per_number_qv.values() for qv in s}
                versions = {
                    v.id: v
                    for v in (
                        await session.scalars(
                            select(QuestionVersion).where(QuestionVersion.id.in_(qv_ids_official))
                        )
                    ).all()
                }
                missing_version = sorted(
                    n for n, s in per_number_qv.items() if any(qv not in versions for qv in s)
                )
                missing_text = sorted(
                    n
                    for n, s in per_number_qv.items()
                    for qv in s
                    if qv in versions
                    and not ((versions[qv].statement or versions[qv].canonical_text or "").strip())
                )
                inventory_blockers = (
                    bool(dup_in_booklet)
                    or bool(ambiguous_numbers)
                    or bool(bad_number)
                    or bool(missing_version)
                    or bool(missing_text)
                )

                # 3. CLASSIFICATION INVENTORY
                every_pc = list((await session.scalars(select(PedagogicalClassification))).all())
                every_ids = {pc.id for pc in every_pc}
                id_to_pc = {pc.id: pc for pc in every_pc}
                cls_buckets = Counter()
                cls_unexpected: list[str] = []
                for pc in every_pc:
                    lc, md = pc.lifecycle, _md(pc)
                    mode, taxo = md.get("classification_mode"), md.get("taxonomy_version")
                    if lc == "SUPERSEDED":
                        cls_buckets["SUPERSEDED"] += 1
                    elif lc == "ACTIVE" and taxo == TARGET and mode == "INITIAL":
                        cls_buckets["ACTIVE_INITIAL"] += 1
                    elif lc == "ACTIVE" and taxo == TARGET:
                        cls_buckets["ACTIVE_OTHER_MODE"] += 1
                    elif lc == "ACTIVE":
                        cls_buckets["ACTIVE_OTHER_TAXONOMY"] += 1
                    else:
                        cls_buckets["UNEXPECTED"] += 1
                        cls_unexpected.append(f"{_hp(pc.id)} lifecycle={lc!r}")
                    if pc.status == "NEEDS_REVIEW":
                        cls_buckets["STATUS_NEEDS_REVIEW"] += 1

                # 4. TARGET TAXONOMY / controlled vocabulary
                registered = sorted(_INITIAL_CONTROLLED_VOCABULARIES)
                vocab_ok = registered == [TARGET] and KINETICS_RETRIEVAL_VOCABULARY.canonical_code == CANON
                canon_node_present = CANON in catalog_codes

                # 5/8/9. PROTECTED QUESTION DETAIL + fingerprints
                protected_detail: dict[int, dict] = {}
                fingerprints: dict[int, tuple] = {}
                for number in PROTECTED:
                    bqs = list(
                        (
                            await session.scalars(
                                select(BookletQuestion).where(BookletQuestion.official_number == number)
                            )
                        ).all()
                    )
                    qv_ids = {b.question_version_id for b in bqs}
                    rows: list[PedagogicalClassification] = []
                    for qv_id in sorted(qv_ids, key=str):
                        rows += await b0._classification_rows(session, qv_id)
                    fingerprints[number] = _fingerprint(rows)
                    protected_detail[number] = {
                        "question_version_id": sorted(_hp(x) for x in qv_ids),
                        "count": len(rows),
                        "active": sum(1 for r in rows if r.lifecycle == "ACTIVE"),
                        "superseded": sum(1 for r in rows if r.lifecycle == "SUPERSEDED"),
                        "rows": [
                            {
                                "id": _hp(r.id),
                                "lifecycle": r.lifecycle,
                                "taxonomy_version": _md(r).get("taxonomy_version"),
                                "classification_mode": _md(r).get("classification_mode"),
                                "content": r.content,
                                "model_version": r.model_version,
                                "prompt_version": r.prompt_version,
                                "status": r.status,
                                "supersedes_id": _hp(r.supersedes_id),
                            }
                            for r in rows
                        ],
                    }

                # 7 + 11. ACTIVE-row conflict / duplicates
                active_pairs = Counter()
                active_any_by_q: dict[str, list] = {}
                for pc in every_pc:
                    if pc.lifecycle == "ACTIVE":
                        active_pairs[(str(pc.question_version_id), _md(pc).get("taxonomy_version"))] += 1
                        active_any_by_q.setdefault(str(pc.question_version_id), []).append(pc)
                active_duplicate_pairs = sorted(
                    f"{_hp(qv)}|{tx}" for (qv, tx), c in active_pairs.items() if c > 1
                )
                multi_active_any = sorted(
                    _hp(qv) for qv, lst in active_any_by_q.items() if len(lst) > 1
                )
                active_null_mode = [
                    _hp(pc.id)
                    for pc in every_pc
                    if pc.lifecycle == "ACTIVE" and _md(pc).get("classification_mode") is None
                ]

                # 10. SUPERSESSION INTEGRITY
                counts = await b0._run_level_counts(session)
                orphan = counts["orphan_supersessions"]
                deep_chains = sum(
                    1
                    for pc in every_pc
                    if pc.supersedes_id in id_to_pc
                    and id_to_pc[pc.supersedes_id].supersedes_id is not None
                )
                active_with_supersedes = [
                    _hp(pc.id) for pc in every_pc if pc.lifecycle == "ACTIVE" and pc.supersedes_id is not None
                ]
                # Q128 supersession count
                q128_qv = None
                if 128 in protected_detail and protected_detail[128]["question_version_id"]:
                    q128_bqs = list(
                        (
                            await session.scalars(
                                select(BookletQuestion).where(BookletQuestion.official_number == 128)
                            )
                        ).all()
                    )
                    q128_qv = next(iter({b.question_version_id for b in q128_bqs}), None)
                q128_supersessions = sum(
                    1
                    for pc in every_pc
                    if q128_qv is not None
                    and pc.question_version_id == q128_qv
                    and pc.supersedes_id is not None
                )
                other_protected_supersessions = sum(
                    1
                    for number in PROTECTED
                    if number != 128
                    for r in protected_detail.get(number, {}).get("rows", [])
                    if r["supersedes_id"] != "None"
                )

                # 6 + 12 + 13. DECISION TABLE (via the reviewed Batch-0 core)
                table: list[dict] = []
                binding_records: list[dict] = []
                state_counts = Counter()
                for number in inventory:
                    snap = await b0.read_snapshot_core(session, number, catalog_nodes, catalog_codes)
                    if not snap.exists:
                        table.append(dict(n=number, qv=None, state="SKIPPED",
                                          reason="NOT_IN_OFFICIAL_INVENTORY", current="n/a"))
                        continue
                    if snap.hard_error:
                        table.append(dict(n=number, qv=None, state="ERROR",
                                          reason=snap.hard_error, current="malformed"))
                        state_counts["ERROR"] += 1
                        continue
                    ctx = snap.ctx
                    d = b0.decide_question_state(ctx)
                    state_counts[d.state] += 1
                    current = _current_state(ctx)
                    table.append(
                        dict(
                            n=number,
                            qv=_hp(ctx.question_version.id) if ctx.question_version else None,
                            state=d.state,
                            reason=d.reason_code,
                            current=current,
                            detail=d.detail,
                        )
                    )
                    if ctx.binding is not None:
                        binding_records.append(
                            dict(
                                n=number,
                                status=ctx.binding.binding_status,
                                bound=(ctx.binding.bound_candidate or {}).get("content_code"),
                                canonical_recovered=ctx.binding.recovered_has_canonical,
                                literal_evidence=bool(ctx.binding.literal_evidence_term),
                            )
                        )

                # 15. BLAST-RADIUS BASELINE
                total_classifications = len(every_pc)
                active_total = sum(1 for pc in every_pc if pc.lifecycle == "ACTIVE")
                superseded_total = sum(1 for pc in every_pc if pc.lifecycle == "SUPERSEDED")
                needs_review_total = sum(1 for pc in every_pc if pc.status == "NEEDS_REVIEW")
                global_supersessions = counts["supersedes"]
                active_dup_count = counts["active_duplicates"]
    finally:
        await engine.dispose()

    # ---------------- verdicts ----------------
    v_alembic = alembic_rev == REQUIRED_REVISION
    v_inventory = not inventory_blockers
    v_cls = cls_buckets["UNEXPECTED"] == 0
    v_target = vocab_ok and canon_node_present
    q91 = protected_detail.get(91, {})
    q93 = protected_detail.get(93, {})
    q128 = protected_detail.get(128, {})
    q91_ok = _q91_ok(q91)
    q93_ok = _q93_ok(q93)
    q128_ok = _q128_ok(q128)
    v_supersession = (
        orphan == 0
        and deep_chains == 0
        and not active_with_supersedes
        and q128_supersessions == 1
        and other_protected_supersessions == 0
    )
    v_dups = active_dup_count == 0 and not active_duplicate_pairs
    v_binding = all(
        (rec["status"] == "BOUND" and rec["bound"] == CANON and rec["canonical_recovered"])
        or rec["status"] in ("NEEDS_REVIEW", None)
        for rec in binding_records
    )
    v_baseline = True  # captured unconditionally

    ready = [row["n"] for row in table if row["state"] == "READY_FOR_INITIAL"]

    all_pass = all(
        [v_alembic, v_inventory, v_cls, v_target, q91_ok, q93_ok, q128_ok, v_supersession, v_dups, v_binding]
    )

    # ---------------- report ----------------
    def flag(x):
        return "PASS" if x else "FAIL"

    print("PHASE 9U.2-E — PRODUCTION READ-ONLY PRE-FLIGHT")
    print()
    print(f"DATABASE_CONNECTION: PASS")
    print(f"ALEMBIC_REVISION: {flag(v_alembic)}  ({alembic_rev})")
    print(f"OFFICIAL_INVENTORY: {flag(v_inventory)}")
    if inventory_blockers:
        print(f"  BLOCKER dup_in_booklet={dup_in_booklet} ambiguous={ambiguous_numbers} "
              f"bad_number={bad_number} missing_version={missing_version} missing_text={missing_text}")
    print(f"CLASSIFICATION_INVENTORY: {flag(v_cls)}  {dict(cls_buckets)}")
    if cls_unexpected:
        print(f"  UNEXPECTED rows: {cls_unexpected}")
    print(f"TARGET_TAXONOMY: {flag(v_target)}  registered={registered} canonical_node_present={canon_node_present}")
    print(f"Q91_PROTECTED: {flag(q91_ok)}")
    print(f"Q93_PROTECTED: {flag(q93_ok)}")
    print(f"Q128_PROTECTED: {flag(q128_ok)}")
    print(f"SUPERSESSION_INTEGRITY: {flag(v_supersession)}  "
          f"orphan={orphan} deep_chains={deep_chains} active_with_supersedes={active_with_supersedes} "
          f"q128_supersessions={q128_supersessions} other_protected_supersessions={other_protected_supersessions}")
    print(f"ACTIVE_DUPLICATES: {flag(v_dups)}  count={active_dup_count} pairs={active_duplicate_pairs}")
    print(f"DETERMINISTIC_BINDING: {flag(v_binding)}  records={len(binding_records)}")
    print(f"BLAST_RADIUS_BASELINE: {flag(v_baseline)}")
    print()
    print(f"TOTAL_OFFICIAL_QUESTIONS: {len(inventory)}")
    print(f"TOTAL_QUESTION_VERSIONS: {total_qv}")
    print(f"TOTAL_BOOKLET_QUESTIONS: {total_bq}  (with official_number: {total_bq_official})")
    print(f"TOTAL_CLASSIFICATIONS: {total_classifications}")
    print(f"  ACTIVE={active_total} SUPERSEDED={superseded_total} NEEDS_REVIEW(status)={needs_review_total}")
    print(f"  GLOBAL_SUPERSESSION_COUNT={global_supersessions} ACTIVE_DUPLICATE_COUNT={active_dup_count} "
          f"ORPHAN_SUPERSESSION_COUNT={orphan}")
    print()
    print("STATE COUNTS (Batch-0 decision core):")
    for st in ("PROTECTED", "ALREADY_CLASSIFIED", "READY_FOR_INITIAL", "NEEDS_REVIEW",
               "BLOCKED", "OUT_OF_SCOPE", "SUPERSEDED_ONLY", "ERROR"):
        print(f"  {st}: {state_counts.get(st, 0)}")
    print()
    print("PROTECTED QUESTION DETAIL:")
    for number in PROTECTED:
        d = protected_detail.get(number, {})
        print(f"  #{number}: qv={d.get('question_version_id')} count={d.get('count')} "
              f"active={d.get('active')} superseded={d.get('superseded')}")
        for r in d.get("rows", []):
            print(f"      {r}")
    print()
    print("ACTIVE-ROW CONFLICT SCAN:")
    print(f"  ACTIVE duplicate (qv, taxonomy_version) pairs: {active_duplicate_pairs or 'none'}")
    print(f"  question_versions with >1 ACTIVE row (any taxonomy): {multi_active_any or 'none'}")
    print(f"  ACTIVE rows with classification_mode = NULL: {active_null_mode or 'none'}")
    print()
    print("DETERMINISTIC BINDING RECORDS (candidates that reached D9):")
    for rec in binding_records:
        print(f"  #{rec['n']}: status={rec['status']} bound={rec['bound']} "
              f"canonical_recovered={rec['canonical_recovered']} literal_evidence={rec['literal_evidence']}")
    if not binding_records:
        print("  (none)")
    print()
    print("INVENTORY DECISION TABLE:")
    print("  official_number | question_version_id | current_state | target_taxonomy | decision | reason")
    for row in table:
        print(f"  {row['n']:>3} | {row['qv']} | {row['current']} | {TARGET} | {row['state']} | {row['reason']}")
    print()
    print(f"CANDIDATE_WRITE_SET (READY_FOR_INITIAL, PLAN ONLY): {ready}")
    print(f"READY_FOR_INITIAL_COUNT: {len(ready)}")
    print()
    print("DATABASE_WRITES: 0")
    print("OPENAI_CALLS: 0")
    print("ALEMBIC_EXECUTION: 0")
    print("POSTGRESQL_WRITES: 0")
    print()
    print("FINAL_DECISION:")
    print("PHASE_9U2_PRODUCTION_PREFLIGHT_PASS" if all_pass else "PHASE_9U2_PRODUCTION_PREFLIGHT_NEEDS_REVIEW")
    return 0 if all_pass else 1


def _current_state(ctx) -> str:
    rows = ctx.classifications
    if not rows:
        return "UNCLASSIFIED"
    active = [r for r in rows if r.lifecycle == "ACTIVE"]
    superseded = [r for r in rows if r.lifecycle == "SUPERSEDED"]
    at = [r for r in active if r.taxonomy_version == TARGET]
    if at:
        return f"ACTIVE_TARGET(mode={at[0].classification_mode})"
    if active:
        return "ACTIVE_OTHER_TAXONOMY(" + ",".join(sorted({str(r.taxonomy_version) for r in active})) + ")"
    if superseded:
        return "SUPERSEDED_ONLY"
    return "UNKNOWN"


def _q91_ok(d: dict) -> bool:
    rows = d.get("rows", [])
    act = [r for r in rows if r["lifecycle"] == "ACTIVE"]
    return (
        len(act) == 1
        and act[0]["taxonomy_version"] == "023_curriculum_taxonomy"
        and act[0]["classification_mode"] is None
        and act[0]["supersedes_id"] == "None"
    )


def _q93_ok(d: dict) -> bool:
    rows = d.get("rows", [])
    act = [r for r in rows if r["lifecycle"] == "ACTIVE"]
    return (
        len(act) == 1
        and act[0]["taxonomy_version"] == TARGET
        and act[0]["classification_mode"] == "INITIAL"
        and act[0]["content"] == CANON
        and act[0]["supersedes_id"] == "None"
    )


def _q128_ok(d: dict) -> bool:
    rows = d.get("rows", [])
    act = [r for r in rows if r["lifecycle"] == "ACTIVE"]
    sup = [r for r in rows if r["lifecycle"] == "SUPERSEDED"]
    return (
        len(act) == 1
        and act[0]["taxonomy_version"] == TARGET
        and act[0]["classification_mode"] == "INITIAL"
        and act[0]["content"] == CANON
        and act[0]["supersedes_id"] != "None"
        and len(sup) == 1
        and sup[0]["content"] == "CHEMISTRY-SOLUTIONS"
    )


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception as exc:  # fail closed
        import os

        msg = str(exc)
        for key in ("DATABASE_URL", "OPENAI_API_KEY"):
            secret = os.getenv(key)
            if secret:
                msg = msg.replace(secret, "[REDACTED]")
        print("PHASE 9U.2-E — PRODUCTION READ-ONLY PRE-FLIGHT")
        print(f"FATAL: {type(exc).__name__}: {msg[:300]}")
        print()
        print("DATABASE_CONNECTION: FAIL")
        print("FINAL_DECISION:")
        print("PHASE_9U2_PRODUCTION_PREFLIGHT_NEEDS_REVIEW")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
