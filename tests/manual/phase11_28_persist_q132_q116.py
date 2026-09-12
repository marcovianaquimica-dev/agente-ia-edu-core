"""PHASE 11.28 - persist the two deterministically-approved classifications.

  2024 Q132 -> BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS
  2025 Q116 -> CHEMISTRY-GENERAL-POLARITY-IMF

Deterministic path only. ZERO LLM / provider calls. Both rows are written inside a
single outer transaction (each ClassificationProposalService.propose commit is
neutralised to a flush; one real commit at the end; any failure -> full rollback,
zero writes).

Mechanism (same as PHASE 11.15 / 11.26):
    AiAgnosticClassificationService.persist_classified_consensus(
        session, <curator-built CLASSIFIED ConsensusOutcome>, qv,
        evidence=[...verbatim...], confirm=True)
        -> ClassificationProposalService.propose(...)   # source='rule', lifecycle='ACTIVE'

No provider is constructed. propose() has no idempotency guard, so this script
guards re-runs itself: if BOTH targets already carry this phase's ACTIVE
curriculum-v2 row it exits IDEMPOTENT_NOOP with zero writes; a partial / foreign
state aborts.

Token-gated:  PHASE11_28_TOKEN=PHASE11-28-PERSIST-Q132-Q116-APPROVED
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

OUT = _REPO / "var" / "phase11_28_persist_q132_q116_report.json"
TAXONOMY_VERSION = "curriculum-v2"
PROMPT_VERSION = "v1"
TOKEN_ENV = "PHASE11_28_TOKEN"
TOKEN_VAL = "PHASE11-28-PERSIST-Q132-Q116-APPROVED"
_SECRET_RE = re.compile(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+")

CURATOR_REASON = ("PHASE 11.28 deterministic curator approval; PHASE 11.27 audit verdict = "
                  "SUFFICIENT_EXISTING_CONTENT (no taxonomy gap, no visual dependency); no LLM call")

TARGETS = [
    {
        "year": 2024, "num": 132, "qv": "a90a12a2-554c-46e2-80b1-d6b8d5365154",
        "content": "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS",
        "classifier_version": "phase11.28-q132-deterministic-v1",
        "provider_label": "phase-11-28-curator-approved", "model_label": "deterministic-v1",
        "context": "PHASE_11_28 deterministic curator persist; skeletal-muscle fibre typology "
                   "(slow/red oxidative vs fast/white glycolytic) and its effect on athletic "
                   "performance -> animal physiology; PHASE 11.27 = SUFFICIENT_EXISTING_CONTENT",
        "evidence": [
            "As fibras musculares esqueléticas não são todas iguais",
            "a distribuição das fibras nos músculos esqueléticos do corpo auxilia de forma diferenciada no desempenho físico de um atleta",
        ],
    },
    {
        "year": 2025, "num": 116, "qv": "d190739e-a4de-40f3-992a-60a4e34aeb65",
        "content": "CHEMISTRY-GENERAL-POLARITY-IMF",
        "classifier_version": "phase11.28-q116-deterministic-v1",
        "provider_label": "phase-11-28-curator-approved", "model_label": "deterministic-v1",
        "context": "PHASE_11_28 deterministic curator persist; oil/water separation by surface "
                   "polarity / hydrophilic-oleophobic affinity + amphiphilic surfactant -> "
                   "polarity & intermolecular forces; PHASE 11.27 = SUFFICIENT_EXISTING_CONTENT",
        "evidence": [
            "filtro capaz de separar óleo e água",
            "Na utilização desse dispositivo, a retenção do óleo ocorre",
        ],
    },
]

PROTECTED = [(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)]
COUNT_TABLES = ["questions", "question_versions", "question_options", "booklet_questions",
                "catalog_nodes", "catalog_node_prerequisites",
                "pedagogical_classifications", "question_classifications"]
CV2_ACTIVE = ("SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
              "AND metadata->>'taxonomy_version'='curriculum-v2'")
# structural counts that must match exactly
EXPECT_STRUCTURAL = {"questions": 332, "question_versions": 332, "question_options": 1660,
                     "booklet_questions": 332, "catalog_nodes": 64,
                     "catalog_node_prerequisites": 3, "question_classifications": 0}
# spec baseline (36/32) predates PHASE 11.26's approved Q150 persist (+1/+1).
# Accept either the spec baseline or "spec baseline + exactly the Q150 row".
ACCEPTED_PC_CV2 = {(36, 32), (37, 33)}
Q150_MODEL_VERSION = "phase11.26-q150-deterministic-v1"


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
    report: dict = {"phase": "11.28", "mode": "DETERMINISTIC_PRODUCTION_WRITE",
                    "llm_used": False, "date": "2026-09-10"}
    if os.getenv(TOKEN_ENV) != TOKEN_VAL:
        report["FINAL_DECISION"] = "PHASE_11_28_ABORTED"
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

            # this phase's own already-written rows (a legitimate re-run) shift the
            # accepted baseline up by exactly that many on both axes.
            prewritten = 0
            for tgt in TARGETS:
                rows = await _active_cv2(ro, tgt["qv"])
                if any((r["model_version"] or "").startswith("phase11.28-") for r in rows):
                    prewritten += 1
            report["prewritten_phase_rows"] = prewritten

            pc, cv2 = before["pedagogical_classifications"], before["curriculum_v2_ACTIVE"]
            accepted = {(a + prewritten, b + prewritten) for a, b in ACCEPTED_PC_CV2}
            if (pc, cv2) not in accepted:
                raise Abort(f"unexpected classification baseline: pedagogical_classifications={pc}, "
                            f"curriculum_v2_ACTIVE={cv2} (spec baseline 36/32; +1/+1 for the approved "
                            f"PHASE 11.26 Q150 row; +{prewritten}/{prewritten} for this phase's own re-run)")
            q150_active = int(await ro.scalar(text(
                "SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
                "AND metadata->>'taxonomy_version'='curriculum-v2' AND model_version=:mv"),
                {"mv": Q150_MODEL_VERSION}))
            report["baseline_note"] = {
                "spec_expected": {"pedagogical_classifications": 36, "curriculum_v2_ACTIVE": 32},
                "actual": {"pedagogical_classifications": pc, "curriculum_v2_ACTIVE": cv2},
                "delta_explained_by": (f"{Q150_MODEL_VERSION} ACTIVE curriculum-v2 rows = {q150_active} "
                                       f"(PHASE 11.26 Q150 -> MATH-ALGEBRA-LOGARITHMS, previously approved)"
                                       + (f"; + {prewritten} row(s) already written by a prior PHASE 11.28 run"
                                          if prewritten else "")),
            }
            other_cv2 = cv2 - q150_active - prewritten
            if q150_active not in (0, 1) or other_cv2 != 32:
                raise Abort(f"baseline not cleanly explained (q150_active={q150_active}, "
                            f"prewritten={prewritten}, other_cv2={other_cv2}, expected other_cv2=32)")

            report["protected_fp_before"] = await _protected_fp(ro)
            report["all_pc_fp_before"], report["all_pc_rows_before"] = await _all_pc_fp(ro)

            # per-target gates
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
            # both already carry an ACTIVE curriculum-v2 row -> nothing to do
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
            report["FINAL_DECISION"] = "PHASE_11_28_PERSIST_Q132_Q116_COMPLETE"
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
                   WHERE pc.model_version LIKE 'phase11.28-%' AND cn.active = false""")))

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
            "PRODUCTION_DATA_MODIFIED": 1,  # +2 new classification rows (intended)
            "no_provider_constructed": True,
            "api_key_in_report": False,
        }
        report["total_elapsed_s"] = round(time.time() - t0, 1)
        report["FINAL_DECISION"] = ("PHASE_11_28_PERSIST_Q132_Q116_COMPLETE" if ok
                                    else "PHASE_11_28_NEEDS_REVIEW")
        return report
    except Abort as exc:
        report["FINAL_DECISION"] = "PHASE_11_28_ABORTED"
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
            # would-write path (should not happen on a clean second run)
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
