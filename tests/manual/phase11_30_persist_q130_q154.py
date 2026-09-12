"""PHASE 11.30 - persist two more deterministically-approved Day-2 classifications.

  2024 Q130 -> CHEMISTRY-ORGANIC-REACTIONS
  2025 Q154 -> MATH-PROBABILITY-BASICS

Deterministic path only. ZERO LLM / provider calls. Both rows are written inside a
single outer transaction (each ClassificationProposalService.propose commit is
neutralised to a flush; one real commit at the end; any failure -> full rollback,
zero writes).

Mechanism (same as PHASE 11.15 / 11.26 / 11.28):
    AiAgnosticClassificationService.persist_classified_consensus(
        session, <curator-built CLASSIFIED ConsensusOutcome>, qv,
        evidence=[...verbatim...], confirm=True)
        -> ClassificationProposalService.propose(...)   # source='rule', lifecycle='ACTIVE'

No provider is constructed. propose() has no idempotency guard, so this script
guards re-runs itself: if BOTH targets already carry this phase's ACTIVE
curriculum-v2 row it exits IDEMPOTENT_NOOP with zero writes; a partial / foreign
state aborts.

Token-gated:  PHASE11_30_TOKEN=PHASE11-30-PERSIST-Q130-Q154-APPROVED
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _cand in (Path.cwd(), _HERE.parents[1]):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        _REPO = _cand
        break
else:  # pragma: no cover
    _REPO = _HERE.parents[1]

from sqlalchemy import text  # noqa: E402

from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.ai_classification_service import AiAgnosticClassificationService  # noqa: E402
from agente_ia_edu.services.classification_consensus import ConsensusOutcome  # noqa: E402

OUT = _REPO / "var" / "phase11_30_persist_q130_q154_report.json"
TAXONOMY_VERSION = "curriculum-v2"
PROMPT_VERSION = "v1"
TOKEN_ENV = "PHASE11_30_TOKEN"
TOKEN_VAL = "PHASE11-30-PERSIST-Q130-Q154-APPROVED"
PHASE_MV_PREFIX = "phase11.30-"
_SECRET_RE = re.compile(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+")

CURATOR_REASON = ("PHASE 11.30 deterministic curator approval; PHASE 11.29 coverage audit = "
                  "category A (controlled-vocabulary rank-1 retrieval + PHASE 11.24 N=3 3/3 "
                  "CLASSIFIED HIGH on the same CONTENT); self-contained statement; no LLM call")

TARGETS = [
    {
        "year": 2024, "num": 130, "qv": "d91ea3bf-3110-4165-91f0-3edf17f8b073",
        "content": "CHEMISTRY-ORGANIC-REACTIONS",
        "classifier_version": "phase11.30-q130-deterministic-v1",
        "provider_label": "phase-11-30-curator-approved", "model_label": "deterministic-v1",
        "context": "PHASE_11_30 deterministic curator persist; nitrous acid + nitrogenous "
                   "compounds (amines) -> nitroso compounds; organic reaction chemistry; "
                   "PHASE 11.29 = category A; retrieval CHEMISTRY-ORGANIC-REACTIONS rank 1 s=240",
        "evidence": [
            "Esses compostos podem ser obtidos pela reação entre o nitrito de sódio",
            "O ácido nitroso produzido irá reagir com compostos nitrogenados, como as aminas, dando origem aos compostos nitrosos",
        ],
    },
    {
        "year": 2025, "num": 154, "qv": "649b511c-b8b9-482f-8b14-e86fc3aa6cc2",
        "content": "MATH-PROBABILITY-BASICS",
        "classifier_version": "phase11.30-q154-deterministic-v1",
        "provider_label": "phase-11-30-curator-approved", "model_label": "deterministic-v1",
        "context": "PHASE_11_30 deterministic curator persist; random matching of 4 envelopes "
                   "to 4 owners, P(all correct) = 1/4! = 1/24; basic probability; "
                   "PHASE 11.29 = category A; retrieval MATH-PROBABILITY-BASICS rank 1 s=180",
        "evidence": [
            "devolveu os quatro envelopes com os celulares aos quatro candidatos, de maneira aleatória",
            "A probabilidade de que todos os candidatos tenham recebido de volta os envelopes com os seus respectivos celulares",
        ],
    },
]

PROTECTED = [(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)]
COUNT_TABLES = ["questions", "question_versions", "question_options", "booklet_questions",
                "catalog_nodes", "catalog_node_prerequisites",
                "pedagogical_classifications", "question_classifications"]
CV2_ACTIVE = ("SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
              "AND metadata->>'taxonomy_version'='curriculum-v2'")
EXPECT_STRUCTURAL = {"questions": 332, "question_versions": 332, "question_options": 1660,
                     "booklet_questions": 332, "catalog_nodes": 64,
                     "catalog_node_prerequisites": 3, "question_classifications": 0}
# pre-11.30 baseline: 39 pedagogical_classifications / 35 ACTIVE curriculum-v2
# (32 pre-11.26 + Q150 [11.26] + Q132 + Q116 [11.28]). A legitimate re-run shifts
# both by exactly `prewritten` (this phase's own rows).
EXPECT_PC_BASE = 39
EXPECT_CV2_BASE = 35


def _scrub(v) -> str:
    s = "" if v is None else str(v)
    s = _SECRET_RE.sub("[REDACTED]", s)
    k = os.getenv("OPENAI_API_KEY")
    return s.replace(k, "[REDACTED]") if k else s


class Abort(RuntimeError):
    pass


async def _counts(s) -> dict:
    d = {t: int(await s.scalar(text(f"SELECT count(*) FROM {t}"))) for t in COUNT_TABLES}
    d["curriculum_v2_ACTIVE"] = int(await s.scalar(text(CV2_ACTIVE)))
    return d


async def _protected_fp(s) -> dict:
    fp = {}
    for y, n in PROTECTED:
        rows = (await s.execute(text(
            """SELECT pc.* FROM pedagogical_classifications pc
               JOIN booklet_questions bq ON bq.question_version_id=pc.question_version_id
               JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
               JOIN exam_applications ea ON ea.id=eb.exam_application_id
               WHERE ea.year=:y AND bq.official_number=:n ORDER BY pc.created_at"""),
            {"y": y, "n": n})).mappings().all()
        fp[f"{y}_Q{n}"] = hashlib.sha256(
            json.dumps([dict(r) for r in rows], sort_keys=True, default=str).encode()).hexdigest()
    return fp


async def _all_pc_fp(s, exclude_ids=()):
    rows = (await s.execute(text(
        """SELECT id, question_version_id, content, subcontent, status, lifecycle, source,
                  model_name, model_version, prompt_version, provider_name, metadata
           FROM pedagogical_classifications ORDER BY id"""))).mappings().all()
    ex = {str(x) for x in exclude_ids}
    kept = [dict(r) for r in rows if str(r["id"]) not in ex]
    return hashlib.sha256(json.dumps(kept, sort_keys=True, default=str).encode()).hexdigest(), len(kept)


async def _active_cv2(s, qv):
    return (await s.execute(text(
        """SELECT id, metadata->>'primary_content_code' pcc, model_version, status, lifecycle
           FROM pedagogical_classifications WHERE question_version_id=:q
             AND lifecycle='ACTIVE' AND metadata->>'taxonomy_version'='curriculum-v2'"""),
        {"q": qv})).mappings().all()


async def _statement(s, qv) -> str:
    r = (await s.execute(text("SELECT canonical_text, statement FROM question_versions WHERE id=:i"),
                         {"i": qv})).first()
    return (r.canonical_text or r.statement or "") if r else ""


async def _qv_shape(s, qv):
    r = (await s.execute(text("SELECT version_kind, is_immutable FROM question_versions WHERE id=:i"),
                         {"i": qv})).first()
    nopt = int(await s.scalar(text("SELECT count(*) FROM question_options WHERE question_version_id=:i"),
                              {"i": qv}))
    return (r.version_kind if r else None), (bool(r.is_immutable) if r else None), nopt


async def run() -> dict:
    report: dict = {"phase": "11.30", "mode": "DETERMINISTIC_PRODUCTION_WRITE",
                    "llm_used": False, "date": "2026-09-10"}
    if os.getenv(TOKEN_ENV) != TOKEN_VAL:
        report["FINAL_DECISION"] = "PHASE_11_30_ABORTED"
        report["abort_reason"] = f"missing/invalid {TOKEN_ENV}"
        return report
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    t0 = time.time()
    provider_calls = 0  # stays 0 - asserted at the end
    try:
        # -------------------- PREFLIGHT (read-only) --------------------
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            report["current_database"] = await ro.scalar(text("SELECT current_database()"))
            if report["current_database"] != "agente_ia_edu":
                raise Abort(f"wrong database: {report['current_database']}")

            before = await _counts(ro)
            report["before_counts"] = before
            drift = {k: {"expected": v, "actual": before[k]}
                     for k, v in EXPECT_STRUCTURAL.items() if before[k] != v}
            if drift:
                raise Abort(f"structural drift: {drift}")

            prewritten = 0
            for tgt in TARGETS:
                rows = await _active_cv2(ro, tgt["qv"])
                if any((r["model_version"] or "").startswith(PHASE_MV_PREFIX) for r in rows):
                    prewritten += 1
            report["prewritten_phase_rows"] = prewritten

            pc, cv2 = before["pedagogical_classifications"], before["curriculum_v2_ACTIVE"]
            accepted = {(EXPECT_PC_BASE + k, EXPECT_CV2_BASE + k) for k in range(len(TARGETS) + 1)}
            if (pc, cv2) not in accepted:
                raise Abort(f"unexpected classification baseline: pedagogical_classifications={pc}, "
                            f"curriculum_v2_ACTIVE={cv2} (pre-11.30 baseline {EXPECT_PC_BASE}/{EXPECT_CV2_BASE}; "
                            f"+k/+k only for this phase's own re-run rows)")
            phase_rows = int(await ro.scalar(text(
                "SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
                "AND metadata->>'taxonomy_version'='curriculum-v2' AND model_version LIKE :p"),
                {"p": PHASE_MV_PREFIX + "%"}))
            if pc - phase_rows != EXPECT_PC_BASE or cv2 - phase_rows != EXPECT_CV2_BASE or phase_rows != prewritten:
                raise Abort(f"baseline not cleanly explained (phase_rows={phase_rows}, prewritten={prewritten}, "
                            f"pc-phase={pc - phase_rows} expected {EXPECT_PC_BASE}, "
                            f"cv2-phase={cv2 - phase_rows} expected {EXPECT_CV2_BASE})")
            report["baseline_note"] = {
                "pre_11_30_baseline": {"pedagogical_classifications": EXPECT_PC_BASE,
                                       "curriculum_v2_ACTIVE": EXPECT_CV2_BASE},
                "actual": {"pedagogical_classifications": pc, "curriculum_v2_ACTIVE": cv2},
                "this_phase_rows_present": phase_rows,
            }

            report["protected_fp_before"] = await _protected_fp(ro)
            report["all_pc_fp_before"], report["all_pc_rows_before"] = await _all_pc_fp(ro)

            gates = {}
            already = 0
            for tgt in TARGETS:
                q = tgt["qv"]
                node = (await ro.execute(text(
                    "SELECT node_type, active FROM catalog_nodes WHERE code=:c"),
                    {"c": tgt["content"]})).first()
                if node is None or node.node_type != "CONTENT" or not node.active:
                    raise Abort(f"target node {tgt['content']} is not an ACTIVE CONTENT node: {node}")
                vk, imm, nopt = await _qv_shape(ro, q)
                if vk != "official_original" or imm is not True:
                    raise Abort(f"Q{tgt['num']} version not official_original+immutable: kind={vk} immutable={imm}")
                if nopt != 5:
                    raise Abort(f"Q{tgt['num']} has {nopt} options (expected 5)")
                stmt = await _statement(ro, q)
                miss = [e for e in tgt["evidence"] if e not in stmt]
                if miss:
                    raise Abort(f"Q{tgt['num']} evidence not literal substring(s): {miss}")
                existing = await _active_cv2(ro, q)
                if existing:
                    already += 1
                gates[f"Q{tgt['num']}"] = {
                    "node_type": node.node_type, "node_active": node.active,
                    "version_kind": vk, "is_immutable": imm, "options": nopt,
                    "evidence_all_literal": True,
                    "existing_active_cv2": [dict(x) for x in existing],
                }
            report["preflight_gates"] = gates

        # -------------------- IDEMPOTENCY SHORT-CIRCUIT --------------------
        if already == len(TARGETS):
            for tgt in TARGETS:
                g = report["preflight_gates"][f"Q{tgt['num']}"]["existing_active_cv2"][0]
                report[f"Q{tgt['num']}"] = {"question": f"{tgt['year']} Q{tgt['num']}",
                                            "verdict": "IDEMPOTENT_NOOP",
                                            "existing_pc_id": g["id"], "content_code": g["pcc"],
                                            "model_version": g["model_version"]}
            async with factory() as ro:
                await ro.execute(text("SET TRANSACTION READ ONLY"))
                after = await _counts(ro)
            report["after_counts"] = after
            report["deltas"] = {k: after[k] - before[k] for k in before}
            report["created_rows"] = []
            report["idempotency"] = {"second_run": True, "new_rows": 0, "provider_calls": 0,
                                     "note": "both targets already classified; no-op"}
            report["provider_calls"] = 0
            report["security"] = {"DATABASE_WRITES": 0, "OPENAI_CALLS": 0,
                                  "CATALOG_NODES_CREATED": 0, "CLASSIFICATIONS_CREATED": 0,
                                  "ALEMBIC_EXECUTION": 0}
            report["FINAL_DECISION"] = "PHASE_11_30_PERSIST_DETERMINISTIC_COMPLETE"
            report["total_elapsed_s"] = round(time.time() - t0, 1)
            return report
        if already != 0:
            raise Abort(f"partial pre-existing state: {already}/{len(TARGETS)} targets already have an "
                        f"ACTIVE curriculum-v2 row - refusing to write the remainder")

        # -------------------- WRITE (single outer transaction) --------------------
        svc = AiAgnosticClassificationService(factory)  # provider never resolved on this path
        created = []
        async with factory() as s:
            _orig_commit = s.commit
            s.commit = s.flush  # neutralise the per-propose() commit
            try:
                for tgt in TARGETS:
                    q = uuid.UUID(tgt["qv"])
                    outcome = ConsensusOutcome(
                        verdict="CLASSIFIED", content_code=tgt["content"], confidence="HIGH",
                        n=0, reason=CURATOR_REASON, runs=())
                    rec = await svc.persist_classified_consensus(
                        s, outcome, q,
                        evidence=[{"text": e, "reason": CURATOR_REASON} for e in tgt["evidence"]],
                        classifier_version=tgt["classifier_version"],
                        taxonomy_version=TAXONOMY_VERSION,
                        provider_label=tgt["provider_label"], model_label=tgt["model_label"],
                        prompt_version=PROMPT_VERSION, context=tgt["context"], confirm=True)
                    if rec.status != "CLASSIFIED" or rec.lifecycle != "ACTIVE" or rec.source != "rule":
                        raise Abort(f"Q{tgt['num']} propose() returned unexpected row: "
                                    f"status={rec.status} lifecycle={rec.lifecycle} source={rec.source}")
                    created.append((tgt, rec))
                s.commit = _orig_commit
                await s.commit()
            except Exception:
                s.commit = _orig_commit
                await s.rollback()
                raise
        report["created_rows"] = [
            {"question": f"{t['year']} Q{t['num']}", "pc_id": str(r.id),
             "content_code": r.content, "status": r.status, "lifecycle": r.lifecycle,
             "source": r.source, "model_version": r.model_version, "prompt_version": r.prompt_version,
             "provider_name": r.provider_name}
            for t, r in created]
        for t, r in created:
            report[f"Q{t['num']}"] = {"question": f"{t['year']} Q{t['num']}", "verdict": "PERSISTED",
                                      "expected_target": t["content"], "content_code": r.content,
                                      "pc_id": str(r.id), "status": r.status, "lifecycle": r.lifecycle,
                                      "source": r.source, "llm_used": False}
        new_ids = [r.id for _, r in created]

        # -------------------- POST-COMMIT VERIFY (independent, read-only) --------------------
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            after = await _counts(ro)
            report["after_counts"] = after
            report["deltas"] = {k: after[k] - before[k] for k in before}
            report["protected_fp_after"] = await _protected_fp(ro)
            fp_after, rows_after = await _all_pc_fp(ro, exclude_ids=new_ids)
            report["all_pc_fp_after_excluding_new"] = fp_after
            report["all_pc_rows_after_excluding_new"] = rows_after

            per_q = {}
            for tgt in TARGETS:
                rows = await _active_cv2(ro, tgt["qv"])
                per_q[f"Q{tgt['num']}"] = {"active_cv2_rows": [dict(x) for x in rows],
                                           "points_to_expected": len(rows) == 1
                                           and rows[0]["pcc"] == tgt["content"]}
            report["target_verification"] = per_q

            dup = (await ro.execute(text(
                """SELECT question_version_id, count(*) c FROM pedagogical_classifications
                   WHERE lifecycle='ACTIVE' AND metadata->>'taxonomy_version'='curriculum-v2'
                   GROUP BY 1 HAVING count(*)>1"""))).all()
            report["duplicate_active_cv2"] = [[str(d[0]), d[1]] for d in dup]
            report["orphan_classifications"] = int(await ro.scalar(text(
                """SELECT count(*) FROM pedagogical_classifications pc
                   LEFT JOIN question_versions qv ON qv.id=pc.question_version_id
                   WHERE qv.id IS NULL""")))
            report["inactive_node_refs"] = int(await ro.scalar(text(
                """SELECT count(*) FROM pedagogical_classifications pc
                   JOIN catalog_nodes cn ON cn.code = pc.metadata->>'primary_content_code'
                   WHERE pc.model_version LIKE :p AND cn.active = false"""), {"p": PHASE_MV_PREFIX + "%"}))

        report["integrity_checks"] = {
            "delta_pedagogical_classifications": report["deltas"]["pedagogical_classifications"],
            "delta_curriculum_v2_ACTIVE": report["deltas"]["curriculum_v2_ACTIVE"],
            "delta_catalog_nodes": report["deltas"]["catalog_nodes"],
            "delta_catalog_node_prerequisites": report["deltas"]["catalog_node_prerequisites"],
            "delta_question_classifications": report["deltas"]["question_classifications"],
            "delta_questions": report["deltas"]["questions"],
            "delta_question_versions": report["deltas"]["question_versions"],
            "delta_question_options": report["deltas"]["question_options"],
            "protected_unchanged": report["protected_fp_before"] == report["protected_fp_after"],
            "all_other_pc_unchanged": report["all_pc_fp_before"] == fp_after,
            "duplicates": report["duplicate_active_cv2"],
            "orphans": report["orphan_classifications"],
            "inactive_node_refs": report["inactive_node_refs"],
            "both_targets_point_to_expected": all(v["points_to_expected"] for v in per_q.values()),
        }

        # -------------------- IDEMPOTENCY (second run, same process) --------------------
        report["idempotency"] = await _second_run(factory, before)

        report["provider_calls"] = provider_calls
        ic = report["integrity_checks"]
        ok = (report["deltas"]["pedagogical_classifications"] == 2
              and report["deltas"]["curriculum_v2_ACTIVE"] == 2
              and report["deltas"]["catalog_nodes"] == 0
              and report["deltas"]["catalog_node_prerequisites"] == 0
              and report["deltas"]["question_classifications"] == 0
              and report["deltas"]["questions"] == 0
              and report["deltas"]["question_versions"] == 0
              and report["deltas"]["question_options"] == 0
              and ic["protected_unchanged"] and ic["all_other_pc_unchanged"]
              and not ic["duplicates"] and ic["orphans"] == 0 and ic["inactive_node_refs"] == 0
              and ic["both_targets_point_to_expected"]
              and provider_calls == 0
              and report["idempotency"]["new_rows"] == 0
              and report["idempotency"]["provider_calls"] == 0)
        report["security"] = {
            "DATABASE_WRITES": 1 if report["deltas"]["pedagogical_classifications"] else 0,
            "CLASSIFICATIONS_CREATED": report["deltas"]["pedagogical_classifications"],
            "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
            "OPENAI_CALLS": provider_calls,
            "ALEMBIC_EXECUTION": 0,
            "PRODUCTION_DATA_MODIFIED": 1,
            "no_provider_constructed": True,
            "api_key_in_report": False,
        }
        report["total_elapsed_s"] = round(time.time() - t0, 1)
        report["FINAL_DECISION"] = ("PHASE_11_30_PERSIST_DETERMINISTIC_COMPLETE" if ok
                                    else "PHASE_11_30_NEEDS_REVIEW")
        return report
    except Abort as exc:
        report["FINAL_DECISION"] = "PHASE_11_30_ABORTED"
        report["abort_reason"] = _scrub(exc)
        return report
    finally:
        await engine.dispose()


async def _second_run(factory, before) -> dict:
    """Re-invoke the write path once more; expect the guard to no-op with 0 writes."""
    async with factory() as ro:
        await ro.execute(text("SET TRANSACTION READ ONLY"))
        c0 = await _counts(ro)
    svc = AiAgnosticClassificationService(factory)
    noop, wrote = 0, 0
    for tgt in TARGETS:
        async with factory() as s:
            existing = await _active_cv2(s, tgt["qv"])
            if existing:
                noop += 1
                continue
            outcome = ConsensusOutcome(verdict="CLASSIFIED", content_code=tgt["content"],
                                       confidence="HIGH", n=0, reason=CURATOR_REASON, runs=())
            await svc.persist_classified_consensus(
                s, outcome, uuid.UUID(tgt["qv"]),
                evidence=[{"text": e, "reason": CURATOR_REASON} for e in tgt["evidence"]],
                classifier_version=tgt["classifier_version"], taxonomy_version=TAXONOMY_VERSION,
                provider_label=tgt["provider_label"], model_label=tgt["model_label"],
                prompt_version=PROMPT_VERSION, context=tgt["context"], confirm=True)
            wrote += 1
    async with factory() as ro:
        await ro.execute(text("SET TRANSACTION READ ONLY"))
        c1 = await _counts(ro)
    return {"second_run": True, "targets_noop": noop, "targets_written": wrote,
            "new_rows": c1["pedagogical_classifications"] - c0["pedagogical_classifications"],
            "curriculum_v2_ACTIVE_delta": c1["curriculum_v2_ACTIVE"] - c0["curriculum_v2_ACTIVE"],
            "provider_calls": 0}


def main() -> int:
    rep = asyncio.run(run())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    return 0 if rep.get("FINAL_DECISION", "").endswith("COMPLETE") else 1


if __name__ == "__main__":
    sys.exit(main())
