"""PHASE 9U.2 - strictly READ-ONLY production inventory pre-flight.

Run from the operator terminal, where DATABASE_URL is configured:

    cd /Users/marcoviana/agente-ia-edu-core
    PYTHONPATH=src .venv/bin/python tests/manual/phase9u2_inventory_preflight.py

Guarantees by construction:
  * one PostgreSQL session / one transaction,
  * `SET TRANSACTION READ ONLY` as the very first statement,
  * SELECT only afterwards - no INSERT/UPDATE/DELETE, no DDL, no Alembic,
  * no provider / no OpenAI call - the per-question binding check uses ONLY the
    deterministic, network-free ClassificationProposalService.recover_candidates
    and .resolve_initial_controlled_vocabulary_binding (Phase 9U.1-C),
  * no classification is persisted, nothing is "fixed".

Exit code 0 == PHASE_9U2_PRE_FLIGHT_COMPLETE (report produced)
Exit code 1 == pre-flight could not complete (fail closed)
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

ENV_FILE = Path(".env")
for _cand in (Path.cwd(), Path("/Users/marcoviana/agente-ia-edu-core")):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        ENV_FILE = _cand / ".env"
        break

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402

from agente_ia_edu.db.models import (  # noqa: E402
    BookletQuestion,
    CatalogNode,
    PedagogicalClassification,
    QuestionVersion,
)
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    KINETICS_TAXONOMY_VERSION,
    ClassificationProposalService,
)

TARGET_TAXONOMY = KINETICS_TAXONOMY_VERSION  # "024_chemistry_kinetics"
KINETICS_CANONICAL = "CHEMISTRY-PHYSICAL-KINETICS"
Q93 = 93
Q128 = 128
OLD_Q128_ID = "9685d51a-bafe-4391-9a34-fac769d6c931"


def _hp(v) -> str:
    if v is None:
        return "None"
    s = str(v)
    return (s[:12] + "…") if len(s) > 12 else s


def _mode(row: PedagogicalClassification) -> str | None:
    return (row.metadata_ or {}).get("classification_mode")


def _taxo(row: PedagogicalClassification) -> str | None:
    return (row.metadata_ or {}).get("taxonomy_version")


async def _run() -> int:
    load_dotenv(ENV_FILE, override=False)
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                await session.execute(text("SET TRANSACTION READ ONLY"))

                alembic_rev = (
                    await session.execute(text("SELECT version_num FROM alembic_version"))
                ).scalars().first()

                # G. "official question" = booklet_questions row with a non-null
                #    official_number, joined to its question_versions row and its
                #    exam_booklets / exam_applications parents.
                bq_rows = list(
                    (
                        await session.execute(
                            select(
                                BookletQuestion.official_number,
                                BookletQuestion.question_version_id,
                                BookletQuestion.exam_booklet_id,
                            ).where(BookletQuestion.official_number.is_not(None))
                        )
                    ).all()
                )
                official_numbers = sorted({r.official_number for r in bq_rows})
                qv_ids = {r.question_version_id for r in bq_rows}
                by_qv = {r.question_version_id: r for r in bq_rows}

                total_question_versions = (
                    await session.execute(select(func.count()).select_from(QuestionVersion))
                ).scalar_one()

                versions = {
                    v.id: v
                    for v in (
                        await session.scalars(
                            select(QuestionVersion).where(QuestionVersion.id.in_(qv_ids))
                        )
                    ).all()
                }

                all_pc = list(
                    (
                        await session.scalars(
                            select(PedagogicalClassification).where(
                                PedagogicalClassification.question_version_id.in_(qv_ids)
                            )
                        )
                    ).all()
                )
                pc_by_qv: dict[object, list[PedagogicalClassification]] = {}
                for pc in all_pc:
                    pc_by_qv.setdefault(pc.question_version_id, []).append(pc)

                # global-scope integrity reads (all classifications, not only official)
                every_pc = list((await session.scalars(select(PedagogicalClassification))).all())
                catalog_nodes = list(
                    (await session.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)))).all()
                )

    finally:
        await engine.dispose()

    service = ClassificationProposalService(None)  # only pure helpers are used

    # ---- D. bucket every classification attached to an official question ----
    buckets = Counter()
    unexpected_rows: list[str] = []
    for pc in all_pc:
        lc = pc.lifecycle
        md = _mode(pc)
        if lc == "SUPERSEDED":
            buckets["SUPERSEDED"] += 1
        elif lc == "ACTIVE" and md == "INITIAL":
            buckets["ACTIVE_INITIAL"] += 1
        elif lc == "ACTIVE" and md == "RECLASSIFICATION":
            buckets["ACTIVE_RECLASSIFICATION"] += 1
        elif lc == "ACTIVE" and md == "STANDARD":
            buckets["ACTIVE_STANDARD"] += 1
        elif lc == "ACTIVE":
            buckets["ACTIVE_OTHER_MODE"] += 1
            unexpected_rows.append(f"{_hp(pc.id)} ACTIVE mode={md!r}")
        else:
            unexpected_rows.append(f"{_hp(pc.id)} lifecycle={lc!r} mode={md!r}")
        if pc.status == "NEEDS_REVIEW":
            buckets["STATUS_NEEDS_REVIEW"] += 1

    # ---- F. duplicates / orphans / chains (global scope) ----
    active_pairs = Counter()
    active_initial_per_q = Counter()
    for pc in every_pc:
        if pc.lifecycle == "ACTIVE":
            active_pairs[(str(pc.question_version_id), _taxo(pc))] += 1
            if _mode(pc) == "INITIAL":
                active_initial_per_q[str(pc.question_version_id)] += 1
    active_duplicates = sum(1 for (qv, tx), n in active_pairs.items() if tx is not None and n > 1)
    multi_active_initial = sum(1 for n in active_initial_per_q.values() if n > 1)

    every_ids = {pc.id for pc in every_pc}
    id_to_pc = {pc.id: pc for pc in every_pc}
    classifications_without_version = sum(
        1 for pc in every_pc if pc.question_version_id is None
    )
    orphan_supersessions = sum(
        1 for pc in every_pc if pc.supersedes_id is not None and pc.supersedes_id not in every_ids
    )
    deep_chains = sum(
        1
        for pc in every_pc
        if pc.supersedes_id in id_to_pc and id_to_pc[pc.supersedes_id].supersedes_id is not None
    )

    # ---- E. Q93 / Q128 protection (read-only assertions) ----
    def _rows_for(number: int) -> list[PedagogicalClassification]:
        qv = next((qid for qid, r in by_qv.items() if r.official_number == number), None)
        return pc_by_qv.get(qv, []) if qv is not None else []

    q93 = _rows_for(Q93)
    q93_active = [r for r in q93 if r.lifecycle == "ACTIVE"]
    q93_ok = (
        len(q93_active) == 1
        and q93_active[0].content == KINETICS_CANONICAL
        and _taxo(q93_active[0]) == TARGET_TAXONOMY
        and _mode(q93_active[0]) == "INITIAL"
    )
    q128 = _rows_for(Q128)
    q128_active = [r for r in q128 if r.lifecycle == "ACTIVE"]
    q128_superseded = [r for r in q128 if r.lifecycle == "SUPERSEDED"]
    q128_ok = (
        len(q128_active) == 1
        and q128_active[0].content == KINETICS_CANONICAL
        and _taxo(q128_active[0]) == TARGET_TAXONOMY
        and _mode(q128_active[0]) == "INITIAL"
        and len(q128_superseded) == 1
        and q128_superseded[0].content == "CHEMISTRY-SOLUTIONS"
        and str(q128_active[0].supersedes_id) == OLD_Q128_ID
        and str(q128_superseded[0].id) == OLD_Q128_ID
    )

    # ---- C + H. per-question candidate determination (deterministic only) ----
    inventory: list[dict] = []
    ready = needs_review = blocked = already = 0
    for number in official_numbers:
        qv_id = next((qid for qid, r in by_qv.items() if r.official_number == number), None)
        rows = pc_by_qv.get(qv_id, [])
        has_active_initial_target = any(
            r.lifecycle == "ACTIVE" and _mode(r) == "INITIAL" and _taxo(r) == TARGET_TAXONOMY
            for r in rows
        )
        version = versions.get(qv_id)
        statement = ((version.statement if version else None) or (version.canonical_text if version else None) or "").strip()

        if has_active_initial_target:
            state, reason = "ALREADY_CLASSIFIED", "ACTIVE INITIAL classification for target taxonomy exists"
            already += 1
        elif not statement:
            state, reason = "BLOCKED", "question version has no statement/canonical_text"
            blocked += 1
        else:
            recovered = service.recover_candidates(statement, catalog_nodes)
            binding = service.resolve_initial_controlled_vocabulary_binding(
                statement, TARGET_TAXONOMY, recovered
            )
            kinetics_recovered = any(c.get("content_code") == KINETICS_CANONICAL for c in recovered)
            if binding is None:
                state = "BLOCKED"
                reason = (
                    "no controlled vocabulary registered for target taxonomy path "
                    "(deterministic no-provider binding unavailable)"
                )
                blocked += 1
            elif binding.status == "BOUND" and (
                binding.bound_candidate or {}
            ).get("content_code") == KINETICS_CANONICAL and kinetics_recovered:
                state, reason = "READY_FOR_INITIAL", "deterministic controlled vocabulary binding = BOUND"
                ready += 1
            else:
                state = "NEEDS_REVIEW"
                reason = f"controlled vocabulary binding status={binding.status!r} (not BOUND to canonical)"
                needs_review += 1

        if not has_active_initial_target:
            bq = by_qv.get(qv_id)
            inventory.append(
                {
                    "official_number": number,
                    "question_version_id": _hp(qv_id),
                    "exam_booklet_id": _hp(bq.exam_booklet_id) if bq else None,
                    "state": state,
                    "reason": reason,
                }
            )

    unclassified = len(inventory)

    # ------------------------------ report ------------------------------
    print("PHASE 9U.2 — PRODUCTION INVENTORY PRE-FLIGHT")
    print()
    print("DATABASE_CONNECTION: PASS")
    print("READ_ONLY_TRANSACTION: PASS")
    print(f"ALEMBIC_REVISION: {alembic_rev}")
    print(f"TOTAL_OFFICIAL_QUESTIONS: {len(official_numbers)}")
    print(f"TOTAL_QUESTION_VERSIONS: {total_question_versions}")
    print(f"ACTIVE_INITIAL_CLASSIFICATIONS: {buckets['ACTIVE_INITIAL']}")
    print(f"ACTIVE_RECLASSIFICATION_CLASSIFICATIONS: {buckets['ACTIVE_RECLASSIFICATION']}")
    print(f"ACTIVE_STANDARD_CLASSIFICATIONS: {buckets['ACTIVE_STANDARD']}")
    print(f"SUPERSEDED_CLASSIFICATIONS: {buckets['SUPERSEDED']}")
    print(f"NEEDS_REVIEW_CLASSIFICATIONS: {buckets['STATUS_NEEDS_REVIEW']}")
    print(f"UNCLASSIFIED_INITIAL_CANDIDATES: {unclassified}")
    print(f"READY_FOR_INITIAL: {ready}")
    print(f"NEEDS_REVIEW_BEFORE_CLASSIFICATION: {needs_review}")
    print(f"BLOCKED: {blocked}")
    print(f"ALREADY_CLASSIFIED_AMONG_SCANNED: {already}")
    print(f"ACTIVE_DUPLICATES: {active_duplicates}")
    print(f"MULTI_ACTIVE_INITIAL_PER_QUESTION: {multi_active_initial}")
    print(f"CLASSIFICATIONS_WITHOUT_VERSION: {classifications_without_version}")
    print(f"ORPHAN_SUPERSESSIONS: {orphan_supersessions}")
    print(f"DEEP_SUPERSESSION_CHAINS: {deep_chains}")
    print(f"Q93_PROTECTED: {'PASS' if q93_ok else 'FAIL'}")
    print(f"Q128_PROTECTED: {'PASS' if q128_ok else 'FAIL'}")
    print(
        "Q128_FINAL_CONTENT: "
        + (q128_active[0].content if q128_active else "<none>")
    )
    print(
        "CONTROLLED_VOCABULARY_AVAILABLE: "
        f"target={TARGET_TAXONOMY} canonical={KINETICS_CANONICAL} "
        f"(only this one taxonomy path has a registered controlled vocabulary)"
    )
    print(f"UNEXPECTED_CLASSIFICATION_ROWS: {unexpected_rows if unexpected_rows else 'none'}")
    print()
    print("INVENTORY (questions without an ACTIVE INITIAL/target classification):")
    for item in inventory:
        print(
            f"  #{item['official_number']:>3}  qv={item['question_version_id']}  "
            f"booklet={item['exam_booklet_id']}  {item['state']}  — {item['reason']}"
        )
    if not inventory:
        print("  (none)")
    print()
    print("DATABASE_WRITES: 0")
    print("OPENAI_CALLS: 0")
    print("ALEMBIC_EXECUTION: 0")
    print("FILES_MODIFIED: 0")
    print()
    print("FINAL_DECISION:")
    print("PHASE_9U2_PRE_FLIGHT_COMPLETE")
    return 0


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
        print("PHASE 9U.2 — PRODUCTION INVENTORY PRE-FLIGHT")
        print(f"FATAL: {type(exc).__name__}: {msg[:300]}")
        print()
        print("DATABASE_CONNECTION: FAIL")
        print("FINAL_DECISION:")
        print("PHASE_9U2_PRE_FLIGHT_INCOMPLETE")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
