"""PHASE 11.31 - final Day-2 classification batch (single controlled phase).

Processes ALL 19 currently-unclassified Day-2 questions (4 protected + 15 non-protected).

Outcome of the deterministic + granularity analysis (see the FINAL REPORT block):

  DETERMINISTIC PERSIST (no provider, no binding, no node) - concept unambiguous,
  retrieval merely failed to surface a candidate:
      2025 Q096 -> CHEMISTRY-SOLUTIONS               (removal of dissolved salts / ionic deficiency)
      2025 Q146 -> MATH-ALGEBRA-FUNCTIONS            (unit-rate proportional consumption; RATIO subcontent)
      2025 Q152 -> MATH-ALGEBRA-FUNCTIONS            (regra de tres composta; RATIO subcontent)
      2024 Q160 -> MATH-ALGEBRA-FUNCTIONS            (first-degree equation 15 + 5x = 135)

  HUMAN_REVIEW - VISUAL_DEPENDENCY (asset mandatory, concept not independently establishable):
      2024 Q159, 2024 Q161, 2024 Q168, 2025 Q150, 2025 Q155
  HUMAN_REVIEW - TAXONOMY_GAP (self-contained but no defensible existing CONTENT node;
  single-question -> NO node created, per anti-fragmentation policy):
      2024 Q107, 2024 Q129, 2025 Q097, 2025 Q098, 2025 Q153, 2025 Q173
  PROTECTED (analysis only - persistence blocked by protection rule):
      2020 Q091, 2020 Q093, 2020 Q107, 2020 Q128

Provider calls: 0.  Bindings: 0.  Taxonomy nodes: 0.
Persistence: deterministic ClassificationProposalService.propose via
AiAgnosticClassificationService.persist_classified_consensus(confirm=True), all four
rows in ONE outer transaction (per-propose commit neutralised to flush; one real
commit; any failure -> full rollback).

Token-gated:  PHASE11_31_TOKEN=PHASE11-31-FINAL-DAY2-APPROVED
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

OUT = _REPO / "var" / "phase11_31_final_day2_batch_report.json"
TAXONOMY_VERSION = "curriculum-v2"
PROMPT_VERSION = "v1"
TOKEN_ENV = "PHASE11_31_TOKEN"
TOKEN_VAL = "PHASE11-31-FINAL-DAY2-APPROVED"
PHASE_MV_PREFIX = "phase11.31-"
_SECRET_RE = re.compile(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+")

_R = ("PHASE 11.31 deterministic curator classification: the assessed concept is "
      "unambiguous and maps to an ACTIVE CONTENT node; PHASE 11.24 N=3 returned "
      "HUMAN_REVIEW only because retrieval recovered no candidate (or a wrong one), "
      "not because the concept is ambiguous; no LLM call, no binding, no new node.")

TARGETS = [
    {
        "year": 2025, "num": 96, "qv": "5228c34b-7c7b-42c9-9052-a2cb667c277b",
        "content": "CHEMISTRY-SOLUTIONS",
        "classifier_version": "phase11.31-q096-deterministic-v1",
        "provider_label": "phase-11-31-curator-approved", "model_label": "deterministic-v1",
        "context": "PHASE_11_31: water purified by removing dissolved salts (deionised / "
                   "demineralised water); ionic deficiency on ingestion -> solutions chemistry "
                   "(dissolved ionic solutes and their removal). Answer D) destilada.",
        "evidence": [
            "Existe um processo de purificação de água em que são removidos os sais dissolvidos",
            "esse tipo de água não é adequado para ingestão, pois pode causar problemas de saúde, como carência iônica e diarreia",
        ],
    },
    {
        "year": 2025, "num": 146, "qv": "7d5e07e8-2328-4651-944f-37819c90d612",
        "content": "MATH-ALGEBRA-FUNCTIONS",
        "classifier_version": "phase11.31-q146-deterministic-v1",
        "provider_label": "phase-11-31-curator-approved", "model_label": "deterministic-v1",
        "context": "PHASE_11_31: unit-rate proportional reasoning - consumption 1 m3 / 13 km "
                   "scaled to a weekly distance, then choose the smallest sufficient cylinder "
                   "(price proportional to capacity). RATIO is a SUBCONTENT of "
                   "MATH-ALGEBRA-FUNCTIONS, so FUNCTIONS is the correct CONTENT-level target.",
        "evidence": [
            "O preço do cilindro é proporcional à sua capacidade",
            "o consumo do GNV é de 1 m3 a cada 13 km rodados",
        ],
    },
    {
        "year": 2025, "num": 152, "qv": "fc0bb18a-27ed-4f52-bd24-6cce99b06f9d",
        "content": "MATH-ALGEBRA-FUNCTIONS",
        "classifier_version": "phase11.31-q152-deterministic-v1",
        "provider_label": "phase-11-31-curator-approved", "model_label": "deterministic-v1",
        "context": "PHASE_11_31: regra de tres composta - output scales with (workers x hours). "
                   "720 / (3x6) = 40 per worker-hour; 5x9x40 = 1800. Proportional reasoning; "
                   "RATIO subcontent of MATH-ALGEBRA-FUNCTIONS.",
        "evidence": [
            "cada um trabalhando 6 horas diárias, produz 720 unidades por dia",
            "Todos os funcionários produzem igual quantidade de tijolos a cada hora, independentemente de trabalharem 6 ou 9 horas diárias",
        ],
    },
    {
        "year": 2024, "num": 160, "qv": "e467975b-e37f-4a39-b075-c312fccc184e",
        "content": "MATH-ALGEBRA-FUNCTIONS",
        "classifier_version": "phase11.31-q160-deterministic-v1",
        "provider_label": "phase-11-31-curator-approved", "model_label": "deterministic-v1",
        "context": "PHASE_11_31: first-degree equation. Fixed fee + per-unit price; total for 5 "
                   "portions unchanged: 10 + 5*25 = 135; 15 + 5x = 135 -> x = 24. Core linear "
                   "algebra -> MATH-ALGEBRA-FUNCTIONS.",
        "evidence": [
            "o valor total a ser pago por um cliente na compra de 5 porções permaneça o mesmo",
            "qual será o novo valor cobrado, em real, por uma porção",
        ],
    },
]

# Full disposition of every one of the 19 unclassified Day-2 questions (static analysis).
DISPOSITION = {
    "2025 Q96":  {"decision": "CLASSIFIED", "target": "CHEMISTRY-SOLUTIONS", "method": "deterministic"},
    "2025 Q146": {"decision": "CLASSIFIED", "target": "MATH-ALGEBRA-FUNCTIONS", "method": "deterministic"},
    "2025 Q152": {"decision": "CLASSIFIED", "target": "MATH-ALGEBRA-FUNCTIONS", "method": "deterministic"},
    "2024 Q160": {"decision": "CLASSIFIED", "target": "MATH-ALGEBRA-FUNCTIONS", "method": "deterministic"},
    "2024 Q159": {"decision": "HUMAN_REVIEW", "subtype": "VISUAL_DEPENDENCY",
                  "reason": "price list is 'divulgados em um cartaz' and is NOT reproduced in the statement; "
                            "minimum-cost answer is uncomputable without the asset"},
    "2024 Q161": {"decision": "HUMAN_REVIEW", "subtype": "VISUAL_DEPENDENCY",
                  "reason": "every option A-E is decided by quantities/percentages read from the bar chart "
                            "(setor x faixa etaria); probability framing alone is insufficient"},
    "2024 Q168": {"decision": "HUMAN_REVIEW", "subtype": "VISUAL_DEPENDENCY",
                  "reason": "two piecewise-linear graphs (I-II and II-III) are mandatory to read the maximum of III"},
    "2025 Q150": {"decision": "HUMAN_REVIEW", "subtype": "VISUAL_DEPENDENCY",
                  "reason": "options are bare I..V; the five scale figures carry all the data"},
    "2025 Q155": {"decision": "HUMAN_REVIEW", "subtype": "VISUAL_DEPENDENCY",
                  "reason": "graphs with 'algumas quantidades nao informadas' are mandatory to total the students"},
    "2024 Q107": {"decision": "HUMAN_REVIEW", "subtype": "TAXONOMY_GAP",
                  "reason": "phytoremediation / bioremediation mechanism; no environmental-biology CONTENT node "
                            "(CHEMISTRY-ENVIRONMENTAL-AIR-POLLUTION is air+chemistry). Single question -> no node created."},
    "2024 Q129": {"decision": "HUMAN_REVIEW", "subtype": "TAXONOMY_GAP",
                  "reason": "X-ray transmission / EM-radiation-matter interaction; PHYSICS-WAVES-PHENOMENA is "
                            "interference/sound, PHYSICS-EM-* is electrostatics/induction. No EM-radiation node. "
                            "'razao' EXACT retrieval is a pure lexical trap. Single question -> no node created."},
    "2025 Q97":  {"decision": "HUMAN_REVIEW", "subtype": "TAXONOMY_GAP",
                  "reason": "pupillary light reflex (dim light -> pupil dilates -> more light to retina). "
                            "Borderline between BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS and an optics node; "
                            "held for curator review rather than a loose fit."},
    "2025 Q98":  {"decision": "HUMAN_REVIEW", "subtype": "TAXONOMY_GAP",
                  "reason": "tetrodotoxin location (gonads/viscera) and thermostability; no toxinology / "
                            "animal-chemical-defence CONTENT node. Single question -> no node created."},
    "2025 Q153": {"decision": "HUMAN_REVIEW", "subtype": "TAXONOMY_GAP",
                  "reason": "positional construction of a 7-digit identification code; not an arrangement-counting "
                            "problem, so MATH-COMBINATORICS-COUNTING is a weak fit. Single question -> no node created."},
    "2025 Q173": {"decision": "HUMAN_REVIEW", "subtype": "TAXONOMY_GAP",
                  "reason": "evaluate three cost expressions and pick the minimum - pure arithmetic comparison, "
                            "no proportion / function / equation; no arithmetic-optimization CONTENT node. "
                            "Single question -> no node created."},
    "2020 Q91":  {"decision": "PROTECTED_ANALYSIS_ONLY", "note": "retrieval CHEMISTRY-ORGANIC-POLYMERS s=60 "
                  "(tie with a 'revestimento' PLANE-AREA trap); PHASE 11.24 N=3 = 3/3 CLASSIFIED HIGH -> POLYMERS. "
                  "Classifiable in principle; protection rule blocks persistence."},
    "2020 Q93":  {"decision": "PROTECTED_ANALYSIS_ONLY", "note": "retrieval CHEMISTRY-PHYSICAL-KINETICS s=30; "
                  "PHASE 11.24 N=3 = 2/3 only. Protected."},
    "2020 Q107": {"decision": "PROTECTED_ANALYSIS_ONLY", "note": "ancestor-only retrieval; PHASE 11.24 N=3 = 3/3 "
                  "HUMAN_REVIEW. Protected."},
    "2020 Q128": {"decision": "PROTECTED_ANALYSIS_ONLY", "note": "retrieval CHEMISTRY-SOLUTIONS EXACT s=124; "
                  "PHASE 11.24 N=3 = 3/3 CLASSIFIED HIGH. Classifiable in principle; protection rule blocks persistence."},
}

PROTECTED = [(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)]
COUNT_TABLES = ["questions", "question_versions", "question_options", "booklet_questions",
                "catalog_nodes", "catalog_node_prerequisites",
                "pedagogical_classifications", "question_classifications"]
CV2_ACTIVE = ("SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
              "AND metadata->>'taxonomy_version'='curriculum-v2'")
EXPECT_STRUCTURAL = {"questions": 332, "question_versions": 332, "question_options": 1660,
                     "booklet_questions": 332, "catalog_nodes": 64,
                     "catalog_node_prerequisites": 3, "question_classifications": 0}
EXPECT_PC_BASE = 41
EXPECT_CV2_BASE = 37


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


async def run() -> dict:
    report: dict = {"phase": "11.31", "mode": "FINAL_DAY2_BATCH", "llm_used": False,
                    "date": "2026-09-10",
                    "policy": {"provider_calls_budget_used": 0, "bindings": 0, "nodes": 0,
                               "deterministic_only": True}}
    if os.getenv(TOKEN_ENV) != TOKEN_VAL:
        report["FINAL_DECISION"] = "PHASE_11_31_ABORTED"
        report["abort_reason"] = f"missing/invalid {TOKEN_ENV}"
        return report
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    t0 = time.time()
    provider_calls = 0
    try:
        # -------------------- A. PREFLIGHT --------------------
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

            unclassified = await _unclassified_day2(ro)
            report["initial_unclassified_day2"] = unclassified
            report["initial_unclassified_count"] = len(unclassified)
            missing = [q for q in DISPOSITION if q not in unclassified]
            extra = [q for q in unclassified if q not in DISPOSITION]
            if extra:
                raise Abort(f"unclassified Day-2 questions with no disposition entry: {extra}")
            report["disposition_covers_all_unclassified"] = not extra
            report["disposition_entries_now_classified"] = missing  # informational (idempotent re-run)

            prewritten = 0
            for tgt in TARGETS:
                rows = await _active_cv2(ro, tgt["qv"])
                if any((r["model_version"] or "").startswith(PHASE_MV_PREFIX) for r in rows):
                    prewritten += 1
            report["prewritten_phase_rows"] = prewritten

            pc, cv2 = before["pedagogical_classifications"], before["curriculum_v2_ACTIVE"]
            accepted = {(EXPECT_PC_BASE + k, EXPECT_CV2_BASE + k) for k in range(len(TARGETS) + 1)}
            if (pc, cv2) not in accepted:
                raise Abort(f"unexpected classification baseline: pc={pc}, cv2={cv2} "
                            f"(pre-11.31 baseline {EXPECT_PC_BASE}/{EXPECT_CV2_BASE}; +k/+k only for re-run rows)")
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
                if (tgt["year"], tgt["num"]) in PROTECTED:
                    raise Abort(f"Q{tgt['num']} is a protected question - cannot be a persist target")
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
                                     "note": "all deterministic targets already classified; no-op"}
            report["provider_calls"] = 0
            _finalize_static(report)
            report["security"] = {"DATABASE_WRITES": 0, "OPENAI_CALLS": 0, "CATALOG_NODES_CREATED": 0,
                                  "CLASSIFICATIONS_CREATED": 0, "VOCABULARY_CHANGES": 0,
                                  "ALEMBIC_EXECUTION": 0, "CODE_FILES_MODIFIED": 0}
            report["FINAL_DECISION"] = "PHASE_11_31_FINAL_DAY2_CLASSIFICATION_COMPLETE"
            report["total_elapsed_s"] = round(time.time() - t0, 1)
            return report
        if already != 0:
            raise Abort(f"partial pre-existing state: {already}/{len(TARGETS)} targets already classified")

        # -------------------- G. PERSIST (single outer transaction) --------------------
        svc = AiAgnosticClassificationService(factory)  # provider never resolved on this path
        created = []
        async with factory() as s:
            _orig_commit = s.commit
            s.commit = s.flush
            try:
                for tgt in TARGETS:
                    q = uuid.UUID(tgt["qv"])
                    outcome = ConsensusOutcome(verdict="CLASSIFIED", content_code=tgt["content"],
                                               confidence="HIGH", n=0, reason=_R, runs=())
                    rec = await svc.persist_classified_consensus(
                        s, outcome, q,
                        evidence=[{"text": e, "reason": _R} for e in tgt["evidence"]],
                        classifier_version=tgt["classifier_version"], taxonomy_version=TAXONOMY_VERSION,
                        provider_label=tgt["provider_label"], model_label=tgt["model_label"],
                        prompt_version=PROMPT_VERSION, context=tgt["context"], confirm=True)
                    if rec.status != "CLASSIFIED" or rec.lifecycle != "ACTIVE" or rec.source != "rule":
                        raise Abort(f"Q{tgt['num']} propose() unexpected: status={rec.status} "
                                    f"lifecycle={rec.lifecycle} source={rec.source}")
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
             "provider_name": r.provider_name,
             "evidence_used": t["evidence"]}
            for t, r in created]
        for t, r in created:
            report[f"Q{t['num']}"] = {"question": f"{t['year']} Q{t['num']}", "verdict": "PERSISTED",
                                      "expected_target": t["content"], "content_code": r.content,
                                      "pc_id": str(r.id), "status": r.status, "lifecycle": r.lifecycle,
                                      "source": r.source, "llm_used": False}
        new_ids = [r.id for _, r in created]

        # -------------------- POST-COMMIT VERIFY --------------------
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            after = await _counts(ro)
            report["after_counts"] = after
            report["deltas"] = {k: after[k] - before[k] for k in before}
            report["protected_fp_after"] = await _protected_fp(ro)
            fp_after, rows_after = await _all_pc_fp(ro, exclude_ids=new_ids)
            report["all_pc_fp_after_excluding_new"] = fp_after

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
            report["final_unclassified_day2"] = await _unclassified_day2(ro)

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
              and report["idempotency"]["new_rows"] == 0 and report["idempotency"]["provider_calls"] == 0)
        report["security"] = {
            "DATABASE_WRITES": 1 if report["deltas"]["pedagogical_classifications"] else 0,
            "CLASSIFICATIONS_CREATED": report["deltas"]["pedagogical_classifications"],
            "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
            "OPENAI_CALLS": provider_calls,
            "VOCABULARY_CHANGES": 0,
            "ALEMBIC_EXECUTION": 0,
            "CODE_FILES_MODIFIED": 0,
            "PRODUCTION_DATA_MODIFIED": 1,
            "no_provider_constructed": True,
            "api_key_in_report": False,
        }
        report["total_elapsed_s"] = round(time.time() - t0, 1)
        report["FINAL_DECISION"] = ("PHASE_11_31_FINAL_DAY2_CLASSIFICATION_COMPLETE" if ok
                                    else "PHASE_11_31_NEEDS_REVIEW")
        return report
    except Abort as exc:
        report["FINAL_DECISION"] = "PHASE_11_31_ABORTED"
        report["abort_reason"] = _scrub(exc)
        return report
    finally:
        await engine.dispose()


def _finalize_static(report: dict) -> None:
    persisted = [q for q, d in DISPOSITION.items() if d["decision"] == "CLASSIFIED"]
    hr = {q: d for q, d in DISPOSITION.items() if d["decision"] == "HUMAN_REVIEW"}
    prot = [q for q, d in DISPOSITION.items() if d["decision"] == "PROTECTED_ANALYSIS_ONLY"]
    report["per_question_final_decision"] = DISPOSITION
    report["classified_this_phase"] = persisted
    report["human_review"] = list(hr)
    report["human_review_visual_dependency"] = [q for q, d in hr.items() if d["subtype"] == "VISUAL_DEPENDENCY"]
    report["human_review_taxonomy_gap"] = [q for q, d in hr.items() if d["subtype"] == "TAXONOMY_GAP"]
    report["protected_analysis_only"] = prot
    report["genuine_taxonomy_gaps"] = {
        "electromagnetic_radiation_xrays": ["2024 Q129"],
        "environmental_remediation_biology": ["2024 Q107"],
        "arithmetic_cost_optimization": ["2025 Q173", "2024 Q159(partly, also asset-blocked)"],
        "sensory_physiology_or_optics": ["2025 Q97"],
        "animal_chemical_defence_toxinology": ["2025 Q98"],
        "note": "No node created: each gap currently maps to a single unclassified question; per "
                "anti-fragmentation policy a dedicated design phase should decide these together.",
    }
    report["visual_dependencies"] = report["human_review_visual_dependency"]
    report["bindings_added_or_changed"] = []
    report["taxonomy_nodes_created"] = []
    report["counts_summary"] = {
        "initial_unclassified": report.get("initial_unclassified_count"),
        "classified_this_phase": len(persisted),
        "remaining_unclassified": (report.get("initial_unclassified_count") or 0) - len(persisted),
        "remaining_human_review": len(hr),
        "protected_untouched": len(prot),
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
