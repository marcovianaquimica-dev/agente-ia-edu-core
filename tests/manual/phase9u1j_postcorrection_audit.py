"""PHASE 9U.1-J - strictly READ-ONLY post-correction audit for ENEM question 128.

Run from the operator terminal, where DATABASE_URL is configured:

    cd /Users/marcoviana/agente-ia-edu-core
    PYTHONPATH=src .venv/bin/python tests/manual/phase9u1j_postcorrection_audit.py

Guarantees by construction:
  * one PostgreSQL session / one transaction,
  * `SET TRANSACTION READ ONLY` as the very first statement,
  * SELECT only afterwards - no INSERT/UPDATE/DELETE, no DDL, no Alembic,
    no OpenAI, no provider call, no file write, no correction routine.

Exit code 0 == PRODUCTION_CORRECTION_AUDIT_PASS
Exit code 1 == PRODUCTION_CORRECTION_AUDIT_NEEDS_REVIEW

Divergences are reported, never "fixed".
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ENV_FILE = Path(".env")
for _cand in (Path.cwd(), Path("/Users/marcoviana/agente-ia-edu-core")):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        ENV_FILE = _cand / ".env"
        break

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import text  # noqa: E402

from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402

KNOWN_QV = "7709144c-a9fc-4211-a126-575faf2b340a"
OLD_ID = "9685d51a-bafe-4391-9a34-fac769d6c931"
REQUIRED_REVISION = "025_classification_lifecycle"
GOOD_CC = "CHEMISTRY-PHYSICAL-KINETICS"
WRONG_CC = "CHEMISTRY-SOLUTIONS"
TAXO = "024_chemistry_kinetics"
ORIG_MODEL = "phase9u1-initial-v1"
ORIG_PROMPT = "phase9t3-kinetics-v1"
CORRECTION_TAG = "phase9u1e-correction-v1"
SHA256_RE = r"^[0-9a-f]{64}$"

# (lifecycle, content, model_version, prompt_version, status, taxonomy_version,
#  classification_mode, supersedes_id)
Q93_EXPECT = ("ACTIVE", GOOD_CC, ORIG_MODEL, ORIG_PROMPT, "NEEDS_REVIEW", TAXO, "INITIAL", None)


def _flag(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _hp(value) -> str:
    if value is None:
        return "None"
    s = str(value)
    return (s[:12] + "…") if len(s) > 12 else s


async def _run() -> int:
    load_dotenv(ENV_FILE, override=False)
    reads = 0
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    problems: list[str] = []
    try:
        async with factory() as session:
            async with session.begin():
                # First statement in the transaction.
                await session.execute(text("SET TRANSACTION READ ONLY"))

                async def rows(sql: str, **params):
                    nonlocal reads
                    reads += 1
                    result = await session.execute(text(sql), params)
                    return result.mappings().all()

                # 1. ALEMBIC
                alembic_rows = await rows("SELECT version_num FROM alembic_version")
                alembic_rev = alembic_rows[0]["version_num"] if len(alembic_rows) == 1 else None
                alembic_ok = alembic_rev == REQUIRED_REVISION

                # 2. Q128 -> question_version_id
                qv_rows = await rows(
                    "SELECT qv.id::text AS qvid, count(*) OVER () AS n "
                    "FROM booklet_questions bq "
                    "JOIN question_versions qv ON qv.id = bq.question_version_id "
                    "WHERE bq.official_number = 128"
                )
                q128_qvid = qv_rows[0]["qvid"] if len(qv_rows) == 1 else None
                q128_bind_ok = len(qv_rows) == 1 and q128_qvid == KNOWN_QV

                # 3. every Q128 classification
                q128_rows = await rows(
                    "SELECT pc.id::text AS id, pc.lifecycle, pc.content AS content_code, "
                    "pc.model_version, pc.prompt_version, pc.status, "
                    "pc.metadata->>'taxonomy_version'    AS taxonomy_version, "
                    "pc.metadata->>'classification_mode' AS classification_mode, "
                    "pc.supersedes_id::text AS supersedes_id, "
                    "pc.created_at "
                    "FROM pedagogical_classifications pc "
                    "JOIN question_versions qv ON qv.id = pc.question_version_id "
                    "JOIN booklet_questions bq ON bq.question_version_id = qv.id "
                    "WHERE bq.official_number = 128 "
                    "ORDER BY pc.created_at"
                )
                active_rows = [r for r in q128_rows if r["lifecycle"] == "ACTIVE"]
                superseded_rows = [r for r in q128_rows if r["lifecycle"] == "SUPERSEDED"]

                # 4. the single ACTIVE row
                # The replacement row must carry the correction tag, NOT the
                # original one: supersede_initial_classification() requires a
                # distinct classifier_version so propose_with_provider's
                # idempotency key cannot resolve back to the superseded row.
                active = active_rows[0] if len(active_rows) == 1 else None
                q128_active_ok = (
                    active is not None
                    and active["content_code"] == GOOD_CC
                    and active["taxonomy_version"] == TAXO
                    and active["classification_mode"] == "INITIAL"
                    and active["model_version"] == CORRECTION_TAG
                    and active["prompt_version"] == ORIG_PROMPT
                )

                # 5. the old row
                old_rows = await rows(
                    "SELECT lifecycle, content AS content_code "
                    "FROM pedagogical_classifications WHERE id = :oid",
                    oid=OLD_ID,
                )
                old = old_rows[0] if len(old_rows) == 1 else None
                old_ok = (
                    old is not None
                    and old["lifecycle"] == "SUPERSEDED"
                    and old["content_code"] == WRONG_CC
                )

                # 6. supersession relation
                supersedes_ok = active is not None and active["supersedes_id"] == OLD_ID

                # 7. Q128 counts
                q128_count_ok = (
                    len(active_rows) == 1
                    and len(superseded_rows) == 1
                    and len(q128_rows) == 2
                )

                # 8. global total
                total_rows = await rows("SELECT count(*) AS n FROM pedagogical_classifications")
                total = total_rows[0]["n"]
                total_ok = total == 4

                # 9. Q93 unchanged
                q93_rows = await rows(
                    "SELECT pc.lifecycle, pc.content AS content_code, pc.model_version, "
                    "pc.prompt_version, pc.status, "
                    "pc.metadata->>'taxonomy_version'    AS taxonomy_version, "
                    "pc.metadata->>'classification_mode' AS classification_mode, "
                    "pc.supersedes_id::text AS supersedes_id "
                    "FROM pedagogical_classifications pc "
                    "JOIN question_versions qv ON qv.id = pc.question_version_id "
                    "JOIN booklet_questions bq ON bq.question_version_id = qv.id "
                    "WHERE bq.official_number = 93 "
                    "ORDER BY pc.created_at"
                )
                q93_ok = len(q93_rows) == 1 and (
                    q93_rows[0]["lifecycle"],
                    q93_rows[0]["content_code"],
                    q93_rows[0]["model_version"],
                    q93_rows[0]["prompt_version"],
                    q93_rows[0]["status"],
                    q93_rows[0]["taxonomy_version"],
                    q93_rows[0]["classification_mode"],
                    q93_rows[0]["supersedes_id"],
                ) == Q93_EXPECT

                # 10. ACTIVE duplicates
                dup_rows = await rows(
                    "SELECT count(*) AS n FROM ("
                    "  SELECT question_version_id, metadata->>'taxonomy_version' AS tv "
                    "  FROM pedagogical_classifications WHERE lifecycle = 'ACTIVE' "
                    "  GROUP BY question_version_id, metadata->>'taxonomy_version' "
                    "  HAVING count(*) > 1"
                    ") d"
                )
                active_duplicates = dup_rows[0]["n"]
                dup_ok = active_duplicates == 0

                # 11. blast radius: correction tag on any other question
                blast_rows = await rows(
                    "SELECT count(*) AS n "
                    "FROM pedagogical_classifications pc "
                    "JOIN question_versions qv ON qv.id = pc.question_version_id "
                    "JOIN booklet_questions bq ON bq.question_version_id = qv.id "
                    "WHERE pc.model_version = :tag AND bq.official_number <> 128",
                    tag=CORRECTION_TAG,
                )
                other_questions_affected = blast_rows[0]["n"]
                blast_ok = other_questions_affected == 0

                # 12. global supersession count
                sup_rows = await rows(
                    "SELECT count(*) AS n FROM pedagogical_classifications "
                    "WHERE supersedes_id IS NOT NULL"
                )
                total_supersessions = sup_rows[0]["n"]
                sup_ok = total_supersessions == 1

                # 13. FK + referential integrity
                fk_rows = await rows(
                    "SELECT confrelid::regclass::text AS ref, confdeltype AS on_delete "
                    "FROM pg_constraint "
                    "WHERE conrelid = 'pedagogical_classifications'::regclass "
                    "AND conname = 'fk_pedagogical_classifications_supersedes_id'"
                )
                fk_present = (
                    len(fk_rows) == 1
                    and fk_rows[0]["ref"].endswith("pedagogical_classifications")
                    and fk_rows[0]["on_delete"] == "r"  # RESTRICT
                )
                orphan_rows = await rows(
                    "SELECT count(*) AS n FROM pedagogical_classifications c "
                    "LEFT JOIN pedagogical_classifications p ON p.id = c.supersedes_id "
                    "WHERE c.supersedes_id IS NOT NULL AND p.id IS NULL"
                )
                orphans = orphan_rows[0]["n"]
                rel_rows = await rows(
                    "SELECT bqc.official_number AS child_q, bqp.official_number AS parent_q, "
                    "parent.id::text AS parent_id, parent.lifecycle AS parent_lifecycle "
                    "FROM pedagogical_classifications child "
                    "JOIN pedagogical_classifications parent ON parent.id = child.supersedes_id "
                    "JOIN question_versions qvc ON qvc.id = child.question_version_id "
                    "JOIN booklet_questions bqc ON bqc.question_version_id = qvc.id "
                    "JOIN question_versions qvp ON qvp.id = parent.question_version_id "
                    "JOIN booklet_questions bqp ON bqp.question_version_id = qvp.id "
                    "WHERE child.supersedes_id IS NOT NULL"
                )
                rel_ok = (
                    len(rel_rows) == 1
                    and rel_rows[0]["child_q"] == 128
                    and rel_rows[0]["parent_q"] == 128
                    and rel_rows[0]["parent_id"] == OLD_ID
                    and rel_rows[0]["parent_lifecycle"] == "SUPERSEDED"
                )
                chain_rows = await rows(
                    "SELECT count(*) AS n FROM pedagogical_classifications a "
                    "JOIN pedagogical_classifications b ON b.id = a.supersedes_id "
                    "WHERE a.supersedes_id IS NOT NULL AND b.supersedes_id IS NOT NULL"
                )
                deep_chains = chain_rows[0]["n"]
                referential_ok = fk_present and orphans == 0 and rel_ok and deep_chains == 0

                # 14. ACTIVE Q128 must not be SOLUTIONS
                sol_rows = await rows(
                    "SELECT count(*) AS n "
                    "FROM pedagogical_classifications pc "
                    "JOIN question_versions qv ON qv.id = pc.question_version_id "
                    "JOIN booklet_questions bq ON bq.question_version_id = qv.id "
                    "WHERE bq.official_number = 128 AND pc.lifecycle = 'ACTIVE' "
                    "AND pc.content = :wrong",
                    wrong=WRONG_CC,
                )
                active_still_solutions = sol_rows[0]["n"]
                not_solutions_ok = active_still_solutions == 0

                # 15. hash / metadata of the new ACTIVE row (sanitized)
                hash_rows = await rows(
                    "SELECT "
                    "(pc.metadata ? 'input_hash')            AS has_ih, "
                    "length(pc.metadata->>'input_hash')      AS ih_len, "
                    "left(pc.metadata->>'input_hash', 12)    AS ih_pref, "
                    "(pc.metadata->>'input_hash' ~ :re)      AS ih_sha, "
                    "(pc.metadata ? 'output_hash')           AS has_oh, "
                    "length(pc.metadata->>'output_hash')     AS oh_len, "
                    "left(pc.metadata->>'output_hash', 12)   AS oh_pref, "
                    "(pc.metadata->>'output_hash' ~ :re)     AS oh_sha, "
                    "(pc.metadata ? 'question_content_hash') AS has_qch, "
                    "length(pc.metadata->>'question_content_hash')   AS qch_len, "
                    "left(pc.metadata->>'question_content_hash', 12) AS qch_pref, "
                    "(pc.metadata->>'question_content_hash' ~ :re)   AS qch_sha, "
                    "pc.metadata->>'taxonomy_version'        AS taxonomy_version, "
                    "pc.metadata->>'classification_mode'     AS classification_mode "
                    "FROM pedagogical_classifications pc "
                    "JOIN question_versions qv ON qv.id = pc.question_version_id "
                    "JOIN booklet_questions bq ON bq.question_version_id = qv.id "
                    "WHERE bq.official_number = 128 AND pc.lifecycle = 'ACTIVE'",
                    re=SHA256_RE,
                )
                h = hash_rows[0] if len(hash_rows) == 1 else None
                hash_ok = h is not None and all(
                    (
                        h["has_ih"], h["ih_len"] == 64, h["ih_sha"],
                        h["has_oh"], h["oh_len"] == 64, h["oh_sha"],
                        h["has_qch"], h["qch_len"] == 64, h["qch_sha"],
                        h["taxonomy_version"] == TAXO,
                        h["classification_mode"] == "INITIAL",
                    )
                )
    finally:
        await engine.dispose()

    # ----- assemble verdicts -----
    checks = {
        "alembic": alembic_ok,
        "q128_bind": q128_bind_ok,
        "q128_active": q128_active_ok,
        "q128_superseded": old_ok,
        "supersedes_relation": supersedes_ok,
        "q128_counts": q128_count_ok,
        "total": total_ok,
        "q93": q93_ok,
        "duplicates": dup_ok,
        "blast": blast_ok,
        "supersessions": sup_ok,
        "referential": referential_ok,
        "not_solutions": not_solutions_ok,
        "hash": hash_ok,
    }
    for name, ok in checks.items():
        if not ok:
            problems.append(name)
    passed = not problems

    # ----- report -----
    print("PHASE 9U.1-J — POST-CORRECTION AUDIT")
    print()
    print(f"ALEMBIC_REVISION: {alembic_rev} ({_flag(alembic_ok)})")
    print(
        "Q128_ACTIVE_CLASSIFICATION: "
        + (
            f"{_hp(active['id'])} / {active['content_code']} ({_flag(q128_active_ok)})"
            if active
            else f"count={len(active_rows)} ({_flag(False)})"
        )
    )
    print(
        "Q128_SUPERSEDED_CLASSIFICATION: "
        + (
            f"{_hp(OLD_ID)} / {old['lifecycle']} / {old['content_code']} ({_flag(old_ok)})"
            if old
            else f"not found ({_flag(False)})"
        )
    )
    print(
        f"SUPERSEDES_RELATION: new.supersedes_id={_hp(active['supersedes_id']) if active else None} "
        f"expected={_hp(OLD_ID)} ({_flag(supersedes_ok)})"
    )
    print(f"Q128_CONTENT_CODE: {active['content_code'] if active else None} "
          f"({_flag(bool(active) and active['content_code'] == GOOD_CC)})")
    print(f"Q128_CLASSIFICATION_MODE: {active['classification_mode'] if active else None} "
          f"({_flag(bool(active) and active['classification_mode'] == 'INITIAL')})")
    print(f"Q128_TAXONOMY_VERSION: {active['taxonomy_version'] if active else None} "
          f"({_flag(bool(active) and active['taxonomy_version'] == TAXO)})")
    print(f"Q128_TOTAL_CLASSIFICATIONS: {len(q128_rows)} "
          f"(ACTIVE={len(active_rows)}, SUPERSEDED={len(superseded_rows)}) ({_flag(q128_count_ok)})")
    print(f"TOTAL_CLASSIFICATIONS: {total} ({_flag(total_ok)})")
    print(f"Q93_UNCHANGED: {_flag(q93_ok)}")
    print(f"ACTIVE_DUPLICATES: {active_duplicates} ({_flag(dup_ok)})")
    print(f"OTHER_QUESTIONS_AFFECTED: {other_questions_affected} ({_flag(blast_ok)})")
    print(
        "HASH_INTEGRITY: "
        + (
            f"input={h['ih_pref']}… output={h['oh_pref']}… qch={h['qch_pref']}… "
            f"(all sha256/len64) ({_flag(hash_ok)})"
            if h
            else f"new ACTIVE row not found ({_flag(False)})"
        )
    )
    print(
        f"REFERENTIAL_INTEGRITY: fk={_flag(fk_present)} orphans={orphans} "
        f"single_rel={_flag(rel_ok)} deep_chains={deep_chains} "
        f"total_supersessions={total_supersessions} not_solutions={_flag(not_solutions_ok)} "
        f"({_flag(referential_ok and sup_ok and not_solutions_ok)})"
    )
    print(f"UNEXPECTED_CURRENT_STATE: {problems if problems else 'none'}")
    print(f"POSTGRESQL_READS: {reads}")
    print("POSTGRESQL_WRITES: 0")
    print("DATABASE_WRITES: 0")
    print("OPENAI_CALLS: 0")
    print()
    print("--- full Q128 classification set (sanitized) ---")
    for r in q128_rows:
        print(
            f"  id={_hp(r['id'])} lifecycle={r['lifecycle']} content={r['content_code']} "
            f"model={r['model_version']} prompt={r['prompt_version']} status={r['status']} "
            f"taxo={r['taxonomy_version']} mode={r['classification_mode']} "
            f"supersedes_id={_hp(r['supersedes_id'])} created_at={r['created_at']}"
        )
    print()
    print("FINAL_DECISION:")
    print("PRODUCTION_CORRECTION_AUDIT_PASS" if passed else "PRODUCTION_CORRECTION_AUDIT_NEEDS_REVIEW")
    return 0 if passed else 1


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception as exc:  # fail closed, never guess, never auto-fix
        name = type(exc).__name__
        msg = str(exc)
        for env_key in ("DATABASE_URL", "OPENAI_API_KEY"):
            import os

            secret = os.getenv(env_key)
            if secret:
                msg = msg.replace(secret, "[REDACTED]")
        print("PHASE 9U.1-J — POST-CORRECTION AUDIT")
        print(f"FATAL: {name}: {msg[:300]}")
        print()
        print("FINAL_DECISION:")
        print("PRODUCTION_CORRECTION_AUDIT_NEEDS_REVIEW")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
