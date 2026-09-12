"""PHASE 11.32 - final Day-2 taxonomy-gap resolution & coverage closure.

Minimal, non-forcing resolution of the 11 remaining Day-2 pendencies.

  DETERMINISTIC PERSIST (1) - unambiguous fit to an existing ACTIVE CONTENT node,
  no binding / node / provider needed:
      2025 Q097 -> BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS
        (pupillary light reflex: dim light -> pupil dilates -> more light to the
         retina. Sensory/animal physiology. Per PHASE 11.27 this CONTENT node is
         the single deliberate home for the whole BIOLOGY-ANIMAL-PHYSIOLOGY area;
         precedent = 2024 Q132 muscle fibres, 2024 Q134 feathers/exoskeleton.)

  REMAIN HUMAN_REVIEW (10):
    VISUAL_DEPENDENCY - asset mandatory (rule 6, no inference):
        2024 Q159, 2024 Q161, 2024 Q168, 2025 Q150, 2025 Q155
    TAXONOMY_GAP - self-contained but no defensible existing CONTENT node; each
    maps to exactly ONE question in the current bank -> no node created
    (anti-fragmentation, rule 3):
        2024 Q129 (EM radiation / X-ray attenuation)
        2024 Q107 (phytoremediation / environmental biology)
        2025 Q098 (tetrodotoxin localisation & thermostability / toxicology)
        2025 Q153 (positional identification-code construction)
        2025 Q173 (min-cost arithmetic comparison)

Provider calls: 0.  Bindings: 0.  Nodes: 0.  (rule 5: no AI when the blocker is a
missing candidate / figure / taxonomy.)

Persistence: deterministic ClassificationProposalService.propose via
AiAgnosticClassificationService.persist_classified_consensus(confirm=True), single
transaction, rollback on any error, self-guarded idempotency.

Token-gated:  PHASE11_32_TOKEN=PHASE11-32-FINAL-DAY2-GAP-APPROVED
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

OUT = _REPO / "var" / "phase11_32_final_day2_gap_resolution_report.json"
TAXONOMY_VERSION = "curriculum-v2"
PROMPT_VERSION = "v1"
TOKEN_ENV = "PHASE11_32_TOKEN"
TOKEN_VAL = "PHASE11-32-FINAL-DAY2-GAP-APPROVED"
PHASE_MV_PREFIX = "phase11.32-"
_SECRET_RE = re.compile(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+")

_R = ("PHASE 11.32 deterministic curator classification: pupillary light reflex is "
      "unambiguous animal/sensory physiology; BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS "
      "is the single ACTIVE CONTENT node for the whole BIOLOGY-ANIMAL-PHYSIOLOGY "
      "area (PHASE 11.27); no LLM call, no binding, no new node.")

TARGETS = [
    {
        "year": 2025, "num": 97, "qv": "a40789f7-0f07-4bae-ab60-67a0c50e1392",
        "content": "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS",
        "classifier_version": "phase11.32-q097-deterministic-v1",
        "provider_label": "phase-11-32-curator-approved", "model_label": "deterministic-v1",
        "context": "PHASE_11_32: red-eye photography phenomenon; the assessed concept is the "
                   "pupillary light reflex - in dim light the pupil dilates so more light reaches "
                   "the retina (answer A). Sensory/animal physiology -> "
                   "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS (area catch-all CONTENT; precedent "
                   "2024 Q132 / Q134). Retrieval recovered no candidate, so N=3 has no value.",
        "evidence": [
            "a luz do flash da câmera incide diretamente no globo ocular, sendo refletida por uma região repleta de vasos sanguíneos",
            "Esse efeito é mais comum à noite ou em lugares pouco iluminados porque, com a pupila",
        ],
    },
]

PENDING_11 = {
    "VISUAL": ["2024 Q159", "2024 Q161", "2024 Q168", "2025 Q150", "2025 Q155"],
    "GAP": ["2024 Q129", "2024 Q107", "2025 Q97", "2025 Q98", "2025 Q153", "2025 Q173"],
}
PROTECTED = [(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)]

DISPOSITION = {
    "2025 Q97": {"before": "GAP", "after": "CLASSIFIED", "target": "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS",
                 "method": "deterministic", "reason": "pupillary light reflex = animal sensory physiology; "
                 "area catch-all CONTENT node; literal stem evidence; not superficial (precedent Q132/Q134)"},
    "2024 Q159": {"before": "VISUAL", "after": "HUMAN_REVIEW", "subtype": "VISUAL_DEPENDENCY",
                  "reason": "price list on a 'cartaz' is not in the statement; min-cost uncomputable without the asset"},
    "2024 Q161": {"before": "VISUAL", "after": "HUMAN_REVIEW", "subtype": "VISUAL_DEPENDENCY",
                  "reason": "options A-E each cite bar-chart quantities/percentages"},
    "2024 Q168": {"before": "VISUAL", "after": "HUMAN_REVIEW", "subtype": "VISUAL_DEPENDENCY",
                  "reason": "two piecewise-linear graphs mandatory to read the maximum of grandeza III"},
    "2025 Q150": {"before": "VISUAL", "after": "HUMAN_REVIEW", "subtype": "VISUAL_DEPENDENCY",
                  "reason": "options are bare I..V; the five scale figures carry all the data"},
    "2025 Q155": {"before": "VISUAL", "after": "HUMAN_REVIEW", "subtype": "VISUAL_DEPENDENCY",
                  "reason": "graphs with unstated quantities mandatory to total the students"},
    "2024 Q129": {"before": "GAP", "after": "HUMAN_REVIEW", "subtype": "TAXONOMY_GAP",
                  "reason": "X-ray attenuation by density / EM-radiation-matter interaction. No EM-radiation "
                  "CONTENT node (PHYSICS-WAVES-PHENOMENA = interference/sound; PHYSICS-EM-* = "
                  "electrostatics/induction). Reusable in principle, but serves exactly 1 question in the "
                  "current bank -> not created (rule 3)."},
    "2024 Q107": {"before": "GAP", "after": "HUMAN_REVIEW", "subtype": "TAXONOMY_GAP",
                  "reason": "phytoremediation mechanism (fitovolatilizacao). No environmental-/applied-biology "
                  "CONTENT node. 1 question -> not created."},
    "2025 Q98": {"before": "GAP", "after": "HUMAN_REVIEW", "subtype": "TAXONOMY_GAP",
                 "reason": "tetrodotoxin localisation (gonads/viscera) + thermostability -> food-safety practice. "
                 "Tested skill is applied toxicology, not a physiology concept; no defensible CONTENT node. "
                 "1 question -> not created; not forced into ANIMAL-PHYSIOLOGY-ADAPTATIONS (superficial)."},
    "2025 Q153": {"before": "GAP", "after": "HUMAN_REVIEW", "subtype": "TAXONOMY_GAP",
                  "reason": "positional construction of a 7-digit code; no combinatorial reasoning, so "
                  "MATH-COMBINATORICS-COUNTING is a superficial match. No numbering-systems node. 1 question."},
    "2025 Q173": {"before": "GAP", "after": "HUMAN_REVIEW", "subtype": "TAXONOMY_GAP",
                  "reason": "evaluate three cost expressions, pick the minimum - pure arithmetic comparison; "
                  "no proportion/function/equation. No arithmetic-optimization CONTENT node. 1 question "
                  "(2024 Q159 is the same family but asset-blocked; 2025 Q146 already covered via the "
                  "proportional-consumption angle). Not created."},
}

# Reuse sweep: count 332 statements matching each proposed-gap theme (evidence for 'no node').
REUSE_PATTERNS = {
    "EM_radiation_xray": r"\b(raios?\s+x|radia[çc][aã]o eletromagn[ée]tica|espectro eletromagn[ée]tico|ondas eletromagn[ée]ticas|ultravioleta|infravermelho|micro-?ondas)\b",
    "phytoremediation_env_bio": r"\b(fitorremedia|biorremedia|descontamina[çc][aã]o de solo|remedia[çc][aã]o de ambientes)\b",
    "identification_code_numbering": r"\b(c[oó]digo (sequencial|de identifica[çc][aã]o|num[ée]rico)|sistema de numera[çc][aã]o)\b",
    "cost_minimisation_arithmetic": r"\b(custo total .{0,40}menor poss[ií]vel|menor (pre[çc]o|custo) .{0,40}(comprar|contratar|gasto))\b",
    "toxinology_food_safety": r"\b(neurotoxina|tetrodotoxina|intoxica[çc][aã]o alimentar)\b",
}

COUNT_TABLES = ["questions", "question_versions", "question_options", "booklet_questions",
                "catalog_nodes", "catalog_node_prerequisites",
                "pedagogical_classifications", "question_classifications"]
CV2_ACTIVE = ("SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
              "AND metadata->>'taxonomy_version'='curriculum-v2'")
EXPECT_STRUCTURAL = {"questions": 332, "question_versions": 332, "question_options": 1660,
                     "booklet_questions": 332, "catalog_nodes": 64,
                     "catalog_node_prerequisites": 3, "question_classifications": 0}
EXPECT_PC_BASE = 45
EXPECT_CV2_BASE = 41


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


async def _unclassified_day2(s):
    rows = (await s.execute(text("""
        SELECT ea.year yr, bq.official_number num
        FROM booklet_questions bq
        JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
        JOIN exam_applications ea ON ea.id=eb.exam_application_id
        WHERE ea.day=2
          AND NOT EXISTS (SELECT 1 FROM pedagogical_classifications pc
                          WHERE pc.question_version_id=bq.question_version_id
                            AND pc.lifecycle='ACTIVE'
                            AND pc.metadata->>'taxonomy_version'='curriculum-v2')
        ORDER BY ea.year, bq.official_number"""))).all()
    return [f"{r[0]} Q{r[1]}" for r in rows]


async def _reuse_sweep(s):
    rows = (await s.execute(text(
        "SELECT coalesce(canonical_text, statement, '') t FROM question_versions"))).all()
    stmts = [r[0] for r in rows]
    out = {}
    for name, pat in REUSE_PATTERNS.items():
        rx = re.compile(pat, re.I)
        out[name] = sum(1 for t in stmts if rx.search(t))
    return {"total_statements": len(stmts), "matches": out}


async def run() -> dict:
    report: dict = {"phase": "11.32", "mode": "FINAL_DAY2_GAP_RESOLUTION", "llm_used": False,
                    "date": "2026-09-10"}
    if os.getenv(TOKEN_ENV) != TOKEN_VAL:
        report["FINAL_DECISION"] = "PHASE_11_32_ABORTED"
        report["abort_reason"] = f"missing/invalid {TOKEN_ENV}"
        return report
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    t0 = time.time()
    provider_calls = 0
    try:
        # -------- 1. PRECHECK (read-only) --------
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
            report["spec_vs_actual_note"] = (
                "PHASE 11.32 spec says '45 classificacoes curriculum-v2 ACTIVE'; the actual ACTIVE "
                "curriculum-v2 count is 41 and total pedagogical_classifications is 45 (the spec "
                "conflated the two). Proceeding on the verified state 45 / 41.")

            unclassified = await _unclassified_day2(ro)
            report["unclassified_day2_before"] = unclassified
            expected_unclassified = set(["2020 Q91", "2020 Q93", "2020 Q107", "2020 Q128"]
                                        + PENDING_11["VISUAL"] + PENDING_11["GAP"])
            target_qs = {f"{t['year']} Q{t['num']}" for t in TARGETS}
            got = set(unclassified)
            unexpected_extra = got - expected_unclassified
            newly_resolved = expected_unclassified - got  # allowed only if == this phase's targets
            if unexpected_extra or not newly_resolved.issubset(target_qs):
                raise Abort(f"unclassified Day-2 set drift: extra={sorted(unexpected_extra)} "
                            f"unexpected_resolved={sorted(newly_resolved - target_qs)}")
            report["targets_already_resolved_on_entry"] = sorted(newly_resolved)
            report["pending_11_before"] = PENDING_11
            report["reuse_sweep"] = await _reuse_sweep(ro)

            prewritten = 0
            for tgt in TARGETS:
                rows = await _active_cv2(ro, tgt["qv"])
                if any((r["model_version"] or "").startswith(PHASE_MV_PREFIX) for r in rows):
                    prewritten += 1
            report["prewritten_phase_rows"] = prewritten

            pc, cv2 = before["pedagogical_classifications"], before["curriculum_v2_ACTIVE"]
            accepted = {(EXPECT_PC_BASE + k, EXPECT_CV2_BASE + k) for k in range(len(TARGETS) + 1)}
            if (pc, cv2) not in accepted:
                raise Abort(f"unexpected baseline: pc={pc}, cv2={cv2} (base {EXPECT_PC_BASE}/{EXPECT_CV2_BASE})")
            phase_rows = int(await ro.scalar(text(
                "SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
                "AND metadata->>'taxonomy_version'='curriculum-v2' AND model_version LIKE :p"),
                {"p": PHASE_MV_PREFIX + "%"}))
            if pc - phase_rows != EXPECT_PC_BASE or cv2 - phase_rows != EXPECT_CV2_BASE or phase_rows != prewritten:
                raise Abort(f"baseline not cleanly explained (phase_rows={phase_rows}, prewritten={prewritten})")

            report["protected_fp_before"] = await _protected_fp(ro)
            report["all_pc_fp_before"], report["all_pc_rows_before"] = await _all_pc_fp(ro)

            gates = {}
            already = 0
            for tgt in TARGETS:
                if (tgt["year"], tgt["num"]) in PROTECTED:
                    raise Abort(f"Q{tgt['num']} is protected - cannot be a persist target")
                node = (await ro.execute(text("SELECT node_type, active FROM catalog_nodes WHERE code=:c"),
                                         {"c": tgt["content"]})).first()
                if node is None or node.node_type != "CONTENT" or not node.active:
                    raise Abort(f"target node {tgt['content']} is not ACTIVE CONTENT: {node}")
                vk, imm, nopt = await _qv_shape(ro, tgt["qv"])
                if vk != "official_original" or imm is not True:
                    raise Abort(f"Q{tgt['num']} version not official_original+immutable: {vk}/{imm}")
                if nopt != 5:
                    raise Abort(f"Q{tgt['num']} has {nopt} options (expected 5)")
                stmt = await _statement(ro, tgt["qv"])
                miss = [e for e in tgt["evidence"] if e not in stmt]
                if miss:
                    raise Abort(f"Q{tgt['num']} evidence not literal: {miss}")
                existing = await _active_cv2(ro, tgt["qv"])
                if existing:
                    already += 1
                gates[f"Q{tgt['num']}"] = {"node_type": node.node_type, "node_active": node.active,
                                           "version_kind": vk, "is_immutable": imm, "options": nopt,
                                           "evidence_all_literal": True,
                                           "existing_active_cv2": [dict(x) for x in existing]}
            report["preflight_gates"] = gates

        # -------- idempotency short-circuit --------
        if already == len(TARGETS):
            for tgt in TARGETS:
                g = report["preflight_gates"][f"Q{tgt['num']}"]["existing_active_cv2"][0]
                report[f"Q{tgt['num']}"] = {"question": f"{tgt['year']} Q{tgt['num']}",
                                            "verdict": "IDEMPOTENT_NOOP", "existing_pc_id": g["id"],
                                            "content_code": g["pcc"], "model_version": g["model_version"]}
            async with factory() as ro:
                await ro.execute(text("SET TRANSACTION READ ONLY"))
                after = await _counts(ro)
                report["unclassified_day2_after"] = await _unclassified_day2(ro)
            report["after_counts"] = after
            report["deltas"] = {k: after[k] - before[k] for k in before}
            report["created_rows"] = []
            report["idempotency"] = {"second_run": True, "new_rows": 0, "provider_calls": 0,
                                     "note": "target already classified; no-op"}
            report["provider_calls"] = 0
            _finalize_static(report)
            report["security"] = {"DATABASE_WRITES": 0, "OPENAI_CALLS": 0, "CATALOG_NODES_CREATED": 0,
                                  "CLASSIFICATIONS_CREATED": 0, "BINDINGS_ADDED_OR_CHANGED": 0,
                                  "ALEMBIC_EXECUTION": 0, "CODE_FILES_MODIFIED": 0}
            report["FINAL_DECISION"] = "PHASE_11_32_FINAL_DAY2_GAP_RESOLUTION_COMPLETE"
            report["total_elapsed_s"] = round(time.time() - t0, 1)
            return report
        if already != 0:
            raise Abort(f"partial pre-existing state: {already}/{len(TARGETS)}")

        # -------- 8. PERSIST (single transaction) --------
        svc = AiAgnosticClassificationService(factory)
        created = []
        async with factory() as s:
            _orig_commit = s.commit
            s.commit = s.flush
            try:
                for tgt in TARGETS:
                    outcome = ConsensusOutcome(verdict="CLASSIFIED", content_code=tgt["content"],
                                               confidence="HIGH", n=0, reason=_R, runs=())
                    rec = await svc.persist_classified_consensus(
                        s, outcome, uuid.UUID(tgt["qv"]),
                        evidence=[{"text": e, "reason": _R} for e in tgt["evidence"]],
                        classifier_version=tgt["classifier_version"], taxonomy_version=TAXONOMY_VERSION,
                        provider_label=tgt["provider_label"], model_label=tgt["model_label"],
                        prompt_version=PROMPT_VERSION, context=tgt["context"], confirm=True)
                    if rec.status != "CLASSIFIED" or rec.lifecycle != "ACTIVE" or rec.source != "rule":
                        raise Abort(f"Q{tgt['num']} propose() unexpected: {rec.status}/{rec.lifecycle}/{rec.source}")
                    created.append((tgt, rec))
                s.commit = _orig_commit
                await s.commit()
            except Exception:
                s.commit = _orig_commit
                await s.rollback()
                raise

        report["created_rows"] = [
            {"question": f"{t['year']} Q{t['num']}", "pc_id": str(r.id), "content_code": r.content,
             "status": r.status, "lifecycle": r.lifecycle, "source": r.source,
             "model_version": r.model_version, "prompt_version": r.prompt_version,
             "provider_name": r.provider_name, "evidence_used": t["evidence"]}
            for t, r in created]
        for t, r in created:
            report[f"Q{t['num']}"] = {"question": f"{t['year']} Q{t['num']}", "verdict": "PERSISTED",
                                      "expected_target": t["content"], "content_code": r.content,
                                      "pc_id": str(r.id), "status": r.status, "lifecycle": r.lifecycle,
                                      "source": r.source, "llm_used": False}
        new_ids = [r.id for _, r in created]

        # -------- post-commit verify --------
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            after = await _counts(ro)
            report["after_counts"] = after
            report["deltas"] = {k: after[k] - before[k] for k in before}
            report["protected_fp_after"] = await _protected_fp(ro)
            fp_after, _ = await _all_pc_fp(ro, exclude_ids=new_ids)
            report["all_pc_fp_after_excluding_new"] = fp_after
            report["unclassified_day2_after"] = await _unclassified_day2(ro)
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
                   LEFT JOIN question_versions qv ON qv.id=pc.question_version_id WHERE qv.id IS NULL""")))
            report["inactive_node_refs"] = int(await ro.scalar(text(
                """SELECT count(*) FROM pedagogical_classifications pc
                   JOIN catalog_nodes cn ON cn.code = pc.metadata->>'primary_content_code'
                   WHERE pc.model_version LIKE :p AND cn.active = false"""), {"p": PHASE_MV_PREFIX + "%"}))
            report["catalog_integrity"] = await _catalog_integrity(ro)

        report["integrity_checks"] = {
            "delta_pedagogical_classifications": report["deltas"]["pedagogical_classifications"],
            "delta_curriculum_v2_ACTIVE": report["deltas"]["curriculum_v2_ACTIVE"],
            "delta_catalog_nodes": report["deltas"]["catalog_nodes"],
            "delta_catalog_node_prerequisites": report["deltas"]["catalog_node_prerequisites"],
            "delta_questions": report["deltas"]["questions"],
            "delta_question_versions": report["deltas"]["question_versions"],
            "delta_question_options": report["deltas"]["question_options"],
            "delta_question_classifications": report["deltas"]["question_classifications"],
            "protected_unchanged": report["protected_fp_before"] == report["protected_fp_after"],
            "all_other_pc_unchanged": report["all_pc_fp_before"] == fp_after,
            "duplicates": report["duplicate_active_cv2"],
            "orphans": report["orphan_classifications"],
            "inactive_node_refs": report["inactive_node_refs"],
            "all_targets_point_to_expected": all(v["points_to_expected"] for v in per_q.values()),
        }
        report["idempotency"] = await _second_run(factory)
        report["provider_calls"] = provider_calls
        _finalize_static(report)
        ic = report["integrity_checks"]
        ok = (report["deltas"]["pedagogical_classifications"] == len(TARGETS)
              and report["deltas"]["curriculum_v2_ACTIVE"] == len(TARGETS)
              and report["deltas"]["catalog_nodes"] == 0
              and report["deltas"]["catalog_node_prerequisites"] == 0
              and report["deltas"]["question_classifications"] == 0
              and report["deltas"]["questions"] == 0 and report["deltas"]["question_versions"] == 0
              and report["deltas"]["question_options"] == 0
              and ic["protected_unchanged"] and ic["all_other_pc_unchanged"]
              and not ic["duplicates"] and ic["orphans"] == 0 and ic["inactive_node_refs"] == 0
              and ic["all_targets_point_to_expected"] and provider_calls == 0
              and report["catalog_integrity"]["ok"]
              and report["idempotency"]["new_rows"] == 0 and report["idempotency"]["provider_calls"] == 0)
        report["security"] = {
            "DATABASE_WRITES": 1 if report["deltas"]["pedagogical_classifications"] else 0,
            "CLASSIFICATIONS_CREATED": report["deltas"]["pedagogical_classifications"],
            "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
            "BINDINGS_ADDED_OR_CHANGED": 0,
            "OPENAI_CALLS": provider_calls,
            "ALEMBIC_EXECUTION": 0,
            "CODE_FILES_MODIFIED": 0,
            "PRODUCTION_DATA_MODIFIED": 1,
            "no_provider_constructed": True,
            "secrets_in_report": False,
        }
        report["total_elapsed_s"] = round(time.time() - t0, 1)
        report["FINAL_DECISION"] = ("PHASE_11_32_FINAL_DAY2_GAP_RESOLUTION_COMPLETE" if ok
                                    else "PHASE_11_32_NEEDS_REVIEW")
        return report
    except Abort as exc:
        report["FINAL_DECISION"] = "PHASE_11_32_ABORTED"
        report["abort_reason"] = _scrub(exc)
        return report
    finally:
        await engine.dispose()


async def _catalog_integrity(s) -> dict:
    dup_code = (await s.execute(text(
        "SELECT code, count(*) c FROM catalog_nodes GROUP BY code HAVING count(*)>1"))).all()
    orphan_parent = int(await s.scalar(text(
        """SELECT count(*) FROM catalog_nodes c LEFT JOIN catalog_nodes p ON p.id=c.parent_id
           WHERE c.parent_id IS NOT NULL AND p.id IS NULL""")))
    bad_root = int(await s.scalar(text(
        """SELECT count(*) FROM catalog_nodes c LEFT JOIN catalog_nodes r ON r.id=c.root_id
           WHERE c.root_id IS NULL OR r.id IS NULL
              OR r.node_type <> 'DISCIPLINE' OR r.parent_id IS NOT NULL""")))
    bad_parent_type = int(await s.scalar(text(
        """SELECT count(*) FROM catalog_nodes c JOIN catalog_nodes p ON p.id=c.parent_id
           WHERE NOT (
             (c.node_type='AREA' AND p.node_type='DISCIPLINE') OR
             (c.node_type='CONTENT' AND p.node_type='AREA') OR
             (c.node_type='SUBCONTENT' AND p.node_type='CONTENT'))""")))
    self_cycle = int(await s.scalar(text(
        "SELECT count(*) FROM catalog_nodes WHERE parent_id = id")))
    # bounded-depth walk (curriculum-v2 max depth = 4): any chain not terminating in
    # <=5 hops at a NULL parent is a cycle or a broken chain.
    rows = (await s.execute(text("SELECT id, parent_id FROM catalog_nodes"))).all()
    parent = {r[0]: r[1] for r in rows}
    broken_chain = 0
    for nid in parent:
        cur, hops = nid, 0
        while cur is not None and hops <= 6:
            cur = parent.get(cur)
            hops += 1
        if hops > 6:
            broken_chain += 1
    ok = (not dup_code and orphan_parent == 0 and bad_root == 0 and bad_parent_type == 0
          and self_cycle == 0 and broken_chain == 0)
    return {"duplicate_codes": [[d[0], d[1]] for d in dup_code], "orphan_parent": orphan_parent,
            "bad_root_id": bad_root, "invalid_parent_type": bad_parent_type,
            "self_cycle": self_cycle, "broken_or_cyclic_chain": broken_chain, "ok": ok}


def _finalize_static(report: dict) -> None:
    classified = [q for q, d in DISPOSITION.items() if d["after"] == "CLASSIFIED"]
    hr = {q: d for q, d in DISPOSITION.items() if d["after"] == "HUMAN_REVIEW"}
    report["per_question_final_decision"] = DISPOSITION
    report["classified_this_phase"] = classified
    report["human_review_remaining"] = list(hr)
    report["human_review_visual"] = [q for q, d in hr.items() if d["subtype"] == "VISUAL_DEPENDENCY"]
    report["human_review_taxonomy_gap"] = [q for q, d in hr.items() if d["subtype"] == "TAXONOMY_GAP"]
    report["taxonomy_nodes_created"] = []
    report["bindings_added_or_changed"] = []
    report["node_creation_analysis"] = {
        "verdict": "NO NODE CREATED",
        "rationale": "Each of the 6 GAP themes maps to exactly ONE unclassified question in the current "
                     "332-question bank (Day-1 LC/CH are out of curriculum-v2 scope). Rule 3 forbids a "
                     "node exclusive to a single question; reuse within actionable scope is nil. A future "
                     "taxonomy-design phase should decide EM-radiation / environmental-biology / "
                     "numbering-systems / arithmetic-optimization / applied-toxicology together.",
        "reuse_sweep": report.get("reuse_sweep"),
    }
    n_pending_before = sum(len(v) for v in PENDING_11.values())
    report["pending_summary"] = {
        "pending_11_before": n_pending_before,
        "resolved_this_phase": len(classified),
        "pending_after": n_pending_before - len(classified),
        "human_review_after": len(hr),
        "visual_dependency": len(report["human_review_visual"]),
        "taxonomy_gap": len(report["human_review_taxonomy_gap"]),
    }


async def _second_run(factory) -> dict:
    async with factory() as ro:
        await ro.execute(text("SET TRANSACTION READ ONLY"))
        c0 = await _counts(ro)
    svc = AiAgnosticClassificationService(factory)
    noop, wrote = 0, 0
    for tgt in TARGETS:
        async with factory() as s:
            if await _active_cv2(s, tgt["qv"]):
                noop += 1
                continue
            outcome = ConsensusOutcome(verdict="CLASSIFIED", content_code=tgt["content"],
                                       confidence="HIGH", n=0, reason=_R, runs=())
            await svc.persist_classified_consensus(
                s, outcome, uuid.UUID(tgt["qv"]),
                evidence=[{"text": e, "reason": _R} for e in tgt["evidence"]],
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
