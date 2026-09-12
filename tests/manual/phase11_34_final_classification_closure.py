"""PHASE 11.34 - final classification closure of the Day-2 set.

Layered closure (rule 2) of the 10 non-protected HUMAN_REVIEW questions. None has a
strong (A) or acceptable-defensible (B) fit (established in PHASE 11.31/11.32/11.33
and re-checked here), so each is closed at layer C: nearest defensible CONTENT with
an EXPLICIT pedagogical justification, persisted with confidence LOW/MEDIUM and
review_reason FORCED_CLOSURE / FORCED_CLOSURE_VISUAL. No new node is created (rule 6
+ PHASE 11.33: every gap is a single-question occurrence). Zero AI calls (rule 4:
the blockers are missing candidate / figure / taxonomy - AI cannot improve the
decision).

Protected 2020 Q91/Q93/Q107/Q128 are NOT persisted (rule 5) - proposals are only
registered in the report.

Persistence: deterministic ClassificationProposalService.propose (rule 8), then an
in-transaction metadata merge adding the rule-9 traceability keys WITHOUT touching
historical keys. All 10 rows in ONE transaction; rollback on any error; self-guarded
idempotency.

Token-gated:  PHASE11_34_TOKEN=PHASE11-34-FINAL-CLOSURE-APPROVED
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
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    ClassificationProposal, ClassificationProposalService)

OUT = _REPO / "var" / "phase11_34_final_classification_closure_report.json"
TAXONOMY_VERSION = "curriculum-v2"
PROMPT_VERSION = "v1"
TOKEN_ENV = "PHASE11_34_TOKEN"
TOKEN_VAL = "PHASE11-34-FINAL-CLOSURE-APPROVED"
PHASE_MV_PREFIX = "phase11.34-"
_SECRET_RE = re.compile(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+")
_BAND_NUM = {"HIGH": 0.90, "MEDIUM": 0.70, "LOW": 0.40}

# year, num, qv, content, band, mode, review_reason, visual, justification, evidence[]
TARGETS = [
    # ---- GAP (self-contained; no defensible strong CONTENT -> nearest ancestor-adjacent) ----
    dict(year=2024, num=129, qv="868b24d9-9dd4-42f0-95a5-ba6c6f323db2",
         content="PHYSICS-WAVES-PHENOMENA", band="LOW", visual=False,
         review_reason="FORCED_CLOSURE",
         justification="X-rays are electromagnetic WAVES; the assessed physics is wave-matter "
                       "interaction - transmission blocked by dense/thick material, so overlapping "
                       "objects cannot be imaged. curriculum-v2 has no EM-radiation node; "
                       "PHYSICS-WAVES-PHENOMENA is the nearest wave-phenomena CONTENT. Evidence "
                       "supports 'X-ray physics question' but not the mechanical-wave framing -> LOW.",
         evidence=["os passageiros devem ter suas bagagens de mão examinadas antes do embarque, passando-as em esteiras para sua inspeção por aparelhos de raios X",
                   "Que explicação física justifica esse procedimento?"]),
    dict(year=2024, num=107, qv="8dacc467-72fe-45fc-830a-e5eeb5ef86c5",
         content="BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES", band="LOW", visual=False,
         review_reason="FORCED_CLOSURE",
         justification="Phytoremediation intercepts the movement of a heavy metal (Hg) through the "
                       "soil-plant-food-chain pathway ('impedindo sua entrada na cadeia alimentar'). "
                       "Nearest ecology CONTENT is BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES (movement / "
                       "trophic transfer of matter through the environment). No applied/environmental-"
                       "biology node exists -> LOW.",
         evidence=["A fitorremediação é uma técnica que utiliza plantas para a remediação de ambientes contaminados",
                   "O método que retira o mercúrio de uma área contaminada, impedindo sua entrada na cadeia alimentar"]),
    dict(year=2025, num=98, qv="b2b97017-41b6-48b2-84b7-a6a04f270946",
         content="BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS", band="LOW", visual=False,
         review_reason="FORCED_CLOSURE",
         justification="Tetrodotoxin is produced and compartmentalised in the pufferfish gonads and "
                       "viscera - an animal chemical-defence trait. BIOLOGY-ANIMAL-PHYSIOLOGY-"
                       "ADAPTATIONS is the area catch-all CONTENT. The tested competency is applied "
                       "food safety, so support is partial -> LOW.",
         evidence=["esse peixe contém uma potente neurotoxina termoestável, a tetrodotoxina, que é produzida e armazenada nas gônadas e vísceras",
                   "Que ação poderia evitar essa intoxicação?"]),
    dict(year=2025, num=153, qv="2ffa5f95-e2ec-4937-8d4a-f50b03802a00",
         content="MATH-COMBINATORICS-COUNTING", band="LOW", visual=False,
         review_reason="FORCED_CLOSURE",
         justification="A 7-digit identification code built from fixed-width positional fields with "
                       "given value ranges (floor 1-4, sector 01-20, employee 001-135, shift 0/1). "
                       "Structured numeric codes are taught within combinatorics/counting (the "
                       "fundamental counting principle underlies the code space). Q153 only assembles "
                       "the code, so support is partial -> LOW.",
         evidence=["cada visitante será identificado com um código sequencial numérico com 7 dígitos",
                   "o primeiro dígito indica o andar ao qual o visitante se dirige"]),
    dict(year=2025, num=173, qv="a48f837d-7927-4878-9439-10a9df363480",
         content="MATH-ALGEBRA-FUNCTIONS", band="LOW", visual=False,
         review_reason="FORCED_CLOSURE",
         justification="The total cost per school is an affine expression C = 20*t + 10*p + r; the "
                       "task compares three linear cost models and picks the minimum. Comparing "
                       "first-degree cost models is algebra/functions territory (consistent with "
                       "2024 Q160, 2025 Q146/Q152 in this node). Q173 only evaluates at points, so "
                       "support is partial -> LOW.",
         evidence=["Ela contratará os três produtos numa mesma autoescola de modo que o custo total nessa primeira etapa seja o menor possível",
                   "pacote com 20 aulas teóricas"]),
    # ---- VISUAL (concept identifiable from the stem; figure needed for the numeric answer) ----
    dict(year=2024, num=159, qv="4a25d4bb-fb7b-4e85-964a-8d9a501619d8",
         content="MATH-ALGEBRA-FUNCTIONS", band="LOW", visual=True,
         review_reason="FORCED_CLOSURE_VISUAL",
         justification="Unit-rate proportional reasoning: litres per hectare -> litres for 20 ha -> "
                       "least-cost purchase. The concept is explicit in the stem; the price list "
                       "('cartaz') is not reproduced, so the numeric answer and the cost-minimisation "
                       "step cannot be verified -> LOW, visual_dependency.",
         evidence=["1 litro de defensivo do Tipo A é suficiente para aplicação em 0,5 hectare",
                   "O valor mínimo, em real, a ser gasto pelo agricultor é"]),
    dict(year=2024, num=161, qv="22ef18fa-1f29-4706-8d02-5ab2e7aa1d6a",
         content="MATH-PROBABILITY-BASICS", band="MEDIUM", visual=True,
         review_reason="FORCED_CLOSURE_VISUAL",
         justification="Uniform draw over a population: with equal selection probability, the most "
                       "likely age band is the one with the largest count. The probability concept is "
                       "fully stated and retrieval matches MATH-PROBABILITY-BASICS strongly "
                       "(controlled-vocabulary, verbatim). Only the numeric comparison needs the bar "
                       "chart -> MEDIUM, visual_dependency.",
         evidence=["Uma viagem de férias será sorteada entre esses funcionários, de forma que todos terão igual probabilidade de serem sorteados",
                   "A maior probabilidade é que o funcionário sorteado esteja na faixa etária"]),
    dict(year=2024, num=168, qv="d98f8712-607a-4fa0-8d0b-e009cf411123",
         content="MATH-ALGEBRA-FUNCTIONS", band="LOW", visual=True,
         review_reason="FORCED_CLOSURE_VISUAL",
         justification="Functional dependence between three quantities via piecewise-linear graphs; "
                       "find the maximum of III as I varies over [1,3] by chaining the two relations. "
                       "The 'relations of dependence between quantities' concept is in the stem; the "
                       "graphs are indispensable for any value -> LOW, visual_dependency.",
         evidence=["Três grandezas (I, II e III) se relacionam entre si",
                   "descrevem as relações de dependência existentes entre as grandezas I e II, e entre as grandezas II e III"]),
    dict(year=2025, num=150, qv="7bcd88a2-564a-4f70-87b0-9cf1c7039944",
         content="MATH-ALGEBRA-FUNCTIONS", band="LOW", visual=True,
         review_reason="FORCED_CLOSURE_VISUAL",
         justification="Scale as a ratio between image length and real length (RATIO subcontent of "
                       "MATH-ALGEBRA-FUNCTIONS); pick the displacement with the greatest real length. "
                       "The scale/proportion concept is in the stem; the five figures are "
                       "indispensable to compare -> LOW, visual_dependency.",
         evidence=["uma tela que ajusta automaticamente a escala empregada na exibição de cada deslocamento",
                   "o comprimento desse deslocamento, em centímetro, em conformidade com a escala empregada"]),
    dict(year=2025, num=155, qv="1998dba7-7147-45a6-bc9f-4e1886d44ebc",
         content="MATH-STATISTICS-DATA-INTERPRETATION", band="MEDIUM", visual=True,
         review_reason="FORCED_CLOSURE_VISUAL",
         justification="Read partial values from graphs and reconstruct a total across three disjoint "
                       "sport categories (each student practises exactly one). The data-interpretation "
                       "concept is unambiguous from the stem; the graphs supply the numbers -> MEDIUM, "
                       "visual_dependency.",
         evidence=["todos os estudantes do ensino médio praticam uma das três modalidades esportivas oferecidas como atividade física",
                   "Qual é a quantidade de estudantes no ensino médio dessa escola?"]),
]

PROTECTED = [(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)]
PROTECTED_PROPOSALS = {
    "2020 Q91": {"proposed_content": "CHEMISTRY-ORGANIC-POLYMERS",
                 "basis": "PHASE 11.24 N=3 = 3/3 CLASSIFIED HIGH; retrieval s=60 (tie w/ a PLANE-AREA trap)",
                 "action": "REGISTERED FOR REVIEW ONLY - not persisted (protected)"},
    "2020 Q93": {"proposed_content": "CHEMISTRY-PHYSICAL-KINETICS",
                 "basis": "PHASE 11.24 N=3 = 2/3 CLASSIFIED HIGH; retrieval s=30",
                 "action": "REGISTERED FOR REVIEW ONLY - not persisted (protected)"},
    "2020 Q107": {"proposed_content": None,
                  "basis": "PHASE 11.24 N=3 = 3/3 HUMAN_REVIEW; ancestor-only retrieval; no defensible CONTENT",
                  "action": "REGISTERED AS UNRESOLVED - not persisted (protected)"},
    "2020 Q128": {"proposed_content": "CHEMISTRY-SOLUTIONS",
                  "basis": "PHASE 11.24 N=3 = 3/3 CLASSIFIED HIGH; retrieval EXACT s=124",
                  "action": "REGISTERED FOR REVIEW ONLY - not persisted (protected)"},
}

COUNT_TABLES = ["questions", "question_versions", "question_options", "booklet_questions",
                "catalog_nodes", "catalog_node_prerequisites",
                "pedagogical_classifications", "question_classifications"]
CV2_ACTIVE = ("SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
              "AND metadata->>'taxonomy_version'='curriculum-v2'")
EXPECT_STRUCTURAL = {"questions": 332, "question_versions": 332, "question_options": 1660,
                     "booklet_questions": 332, "catalog_nodes": 64,
                     "catalog_node_prerequisites": 3, "question_classifications": 0}
EXPECT_PC_BASE = 46
EXPECT_CV2_BASE = 42


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
        """SELECT id, metadata->>'primary_content_code' pcc, model_version, status, lifecycle,
                  metadata->>'classification_mode' cm, metadata->>'confidence' cf
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
        SELECT ea.year yr, bq.official_number num FROM booklet_questions bq
        JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
        JOIN exam_applications ea ON ea.id=eb.exam_application_id
        WHERE ea.day=2 AND NOT EXISTS (
            SELECT 1 FROM pedagogical_classifications pc
            WHERE pc.question_version_id=bq.question_version_id AND pc.lifecycle='ACTIVE'
              AND pc.metadata->>'taxonomy_version'='curriculum-v2')
        ORDER BY ea.year, bq.official_number"""))).all()
    return [f"{r[0]} Q{r[1]}" for r in rows]


async def _catalog_integrity(s) -> dict:
    dup_code = (await s.execute(text(
        "SELECT code FROM catalog_nodes GROUP BY code HAVING count(*)>1"))).all()
    orphan_parent = int(await s.scalar(text(
        """SELECT count(*) FROM catalog_nodes c LEFT JOIN catalog_nodes p ON p.id=c.parent_id
           WHERE c.parent_id IS NOT NULL AND p.id IS NULL""")))
    bad_root = int(await s.scalar(text(
        """SELECT count(*) FROM catalog_nodes c LEFT JOIN catalog_nodes r ON r.id=c.root_id
           WHERE c.root_id IS NULL OR r.id IS NULL OR r.node_type<>'DISCIPLINE' OR r.parent_id IS NOT NULL""")))
    bad_parent_type = int(await s.scalar(text(
        """SELECT count(*) FROM catalog_nodes c JOIN catalog_nodes p ON p.id=c.parent_id
           WHERE NOT ((c.node_type='AREA' AND p.node_type='DISCIPLINE') OR
                      (c.node_type='CONTENT' AND p.node_type='AREA') OR
                      (c.node_type='SUBCONTENT' AND p.node_type='CONTENT'))""")))
    return {"duplicate_codes": [d[0] for d in dup_code], "orphan_parent": orphan_parent,
            "bad_root_id": bad_root, "invalid_parent_type": bad_parent_type,
            "ok": not dup_code and orphan_parent == 0 and bad_root == 0 and bad_parent_type == 0}


async def _disc(s):
    rows = (await s.execute(text(
        """SELECT split_part(coalesce(pc.metadata->>'primary_content_code',''),'-',1) disc,
                  coalesce(pc.metadata->>'confidence','(historic)') conf,
                  coalesce(pc.metadata->>'classification_mode','(historic)') mode,
                  count(*) c
           FROM pedagogical_classifications pc
           WHERE pc.lifecycle='ACTIVE' AND pc.metadata->>'taxonomy_version'='curriculum-v2'
           GROUP BY 1,2,3 ORDER BY 1,2,3"""))).all()
    by_disc, by_conf, by_mode = {}, {}, {}
    for disc, conf, mode, c in rows:
        by_disc[disc or "(none)"] = by_disc.get(disc or "(none)", 0) + c
        by_conf[conf] = by_conf.get(conf, 0) + c
        by_mode[mode] = by_mode.get(mode, 0) + c
    return {"by_discipline": by_disc, "by_confidence": by_conf, "by_classification_mode": by_mode}


async def _persist_one(s, svc, tgt):
    stmt = await _statement(s, tgt["qv"])
    ev = [e for e in tgt["evidence"] if e in stmt]
    if not ev:
        raise Abort(f"Q{tgt['num']} has no literal evidence")
    proposal = ClassificationProposal(
        primary_content_code=tgt["content"], complementary_content_codes=[], concepts=[],
        prerequisites=[], cognitive_operations=[],
        context=(f"PHASE_11_34 layered closure (layer C). {tgt['justification']}"),
        difficulty="UNKNOWN", confidence=_BAND_NUM[tgt["band"]],
        evidence=[{"content_code": tgt["content"], "text": e,
                   "reason": f"supports the concept partially ({tgt['review_reason']})"} for e in ev])
    mv = f"{PHASE_MV_PREFIX}q{tgt['num']:03d}-{'visual' if tgt['visual'] else 'gap'}-closure-v1"
    rec = await svc.propose(
        uuid.UUID(tgt["qv"]), proposal,
        classifier_version=mv, taxonomy_version=TAXONOMY_VERSION,
        provider="phase-11-34-forced-closure", model="deterministic-forced-v1",
        prompt_version=PROMPT_VERSION)
    # rule 9: merge traceability keys WITHOUT overwriting historical metadata
    merge = {"closure_phase": "11.34", "classification_mode": "FORCED_CLOSURE",
             "confidence": tgt["band"], "review_reason": tgt["review_reason"]}
    if tgt["visual"]:
        merge["visual_dependency"] = True
    await s.execute(text(
        "UPDATE pedagogical_classifications SET metadata = metadata || CAST(:m AS jsonb) WHERE id = :id"),
        {"m": json.dumps(merge), "id": rec.id})
    return rec, ev, mv


async def run() -> dict:
    report: dict = {"phase": "11.34", "mode": "FINAL_CLASSIFICATION_CLOSURE", "llm_used": False,
                    "date": "2026-09-10"}
    if os.getenv(TOKEN_ENV) != TOKEN_VAL:
        report["FINAL_DECISION"] = "PHASE_11_34_ABORTED"
        report["abort_reason"] = f"missing/invalid {TOKEN_ENV}"
        return report
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    t0 = time.time()
    provider_calls = 0
    try:
        # -------- 1. PRECHECK --------
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            report["current_database"] = await ro.scalar(text("SELECT current_database()"))
            if report["current_database"] != "agente_ia_edu":
                raise Abort(f"wrong database: {report['current_database']}")
            before = await _counts(ro)
            report["before_counts"] = before
            drift = {k: {"expected": v, "actual": before[k]} for k, v in EXPECT_STRUCTURAL.items()
                     if before[k] != v}
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
                raise Abort(f"unexpected baseline pc={pc} cv2={cv2} (base {EXPECT_PC_BASE}/{EXPECT_CV2_BASE})")
            phase_rows = int(await ro.scalar(text(
                "SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
                "AND metadata->>'taxonomy_version'='curriculum-v2' AND model_version LIKE :p"),
                {"p": PHASE_MV_PREFIX + "%"}))
            if pc - phase_rows != EXPECT_PC_BASE or cv2 - phase_rows != EXPECT_CV2_BASE or phase_rows != prewritten:
                raise Abort(f"baseline not cleanly explained (phase_rows={phase_rows}, prewritten={prewritten})")

            report["unclassified_day2_before"] = await _unclassified_day2(ro)
            report["protected_fp_before"] = await _protected_fp(ro)
            report["all_pc_fp_before"], _ = await _all_pc_fp(ro)
            report["catalog_integrity_before"] = await _catalog_integrity(ro)

            gates, already = {}, 0
            for tgt in TARGETS:
                if (tgt["year"], tgt["num"]) in PROTECTED:
                    raise Abort(f"Q{tgt['num']} is protected - cannot be a persist target")
                node = (await ro.execute(text("SELECT node_type, active FROM catalog_nodes WHERE code=:c"),
                                         {"c": tgt["content"]})).first()
                if node is None or node.node_type != "CONTENT" or not node.active:
                    raise Abort(f"target node {tgt['content']} not ACTIVE CONTENT: {node}")
                vk, imm, nopt = await _qv_shape(ro, tgt["qv"])
                if vk != "official_original" or imm is not True or nopt != 5:
                    raise Abort(f"Q{tgt['num']} shape: kind={vk} immutable={imm} opts={nopt}")
                stmt = await _statement(ro, tgt["qv"])
                miss = [e for e in tgt["evidence"] if e not in stmt]
                if miss:
                    raise Abort(f"Q{tgt['num']} evidence not literal: {miss}")
                ex = await _active_cv2(ro, tgt["qv"])
                if ex:
                    already += 1
                gates[f"Q{tgt['num']}"] = {"node": tgt["content"], "band": tgt["band"],
                                           "review_reason": tgt["review_reason"], "visual": tgt["visual"],
                                           "evidence_literal": True,
                                           "existing_active_cv2": [dict(x) for x in ex]}
            report["preflight_gates"] = gates

        # -------- idempotency short-circuit --------
        if already == len(TARGETS):
            for tgt in TARGETS:
                g = report["preflight_gates"][f"Q{tgt['num']}"]["existing_active_cv2"][0]
                report[f"Q{tgt['num']}"] = {"question": f"{tgt['year']} Q{tgt['num']}",
                                            "verdict": "IDEMPOTENT_NOOP", "existing_pc_id": g["id"],
                                            "content_code": g["pcc"], "model_version": g["model_version"],
                                            "classification_mode": g["cm"], "confidence": g["cf"]}
            async with factory() as ro:
                await ro.execute(text("SET TRANSACTION READ ONLY"))
                after = await _counts(ro)
                report["unclassified_day2_after"] = await _unclassified_day2(ro)
                report["distribution"] = await _disc(ro)
            report["after_counts"] = after
            report["deltas"] = {k: after[k] - before[k] for k in before}
            report["created_rows"] = []
            report["idempotency"] = {"second_run": True, "new_rows": 0, "provider_calls": 0,
                                     "note": "all 10 closure rows already present; no-op"}
            report["provider_calls"] = 0
            _finalize_static(report)
            report["security"] = {"DATABASE_WRITES": 0, "OPENAI_CALLS": 0, "CATALOG_NODES_CREATED": 0,
                                  "CLASSIFICATIONS_CREATED": 0, "BINDINGS_CREATED": 0,
                                  "ALEMBIC_EXECUTION": 0, "CODE_FILES_MODIFIED": 0}
            report["FINAL_DECISION"] = "PHASE_11_34_FINAL_CLASSIFICATION_CLOSURE_COMPLETE"
            report["total_elapsed_s"] = round(time.time() - t0, 1)
            return report
        if already != 0:
            raise Abort(f"partial pre-existing state: {already}/{len(TARGETS)}")

        # -------- 8. PERSIST (single transaction) --------
        created = []
        async with factory() as s:
            svc = ClassificationProposalService(s)
            _orig = s.commit
            s.commit = s.flush
            try:
                for tgt in TARGETS:
                    rec, ev, mv = await _persist_one(s, svc, tgt)
                    if rec.lifecycle != "ACTIVE" or rec.source != "rule" or rec.content != tgt["content"]:
                        raise Abort(f"Q{tgt['num']} unexpected row: {rec.lifecycle}/{rec.source}/{rec.content}")
                    created.append((tgt, rec, ev, mv))
                s.commit = _orig
                await s.commit()
            except Exception:
                s.commit = _orig
                await s.rollback()
                raise

        report["created_rows"] = [
            {"question": f"{t['year']} Q{t['num']}", "pc_id": str(r.id), "content_code": r.content,
             "status": r.status, "lifecycle": r.lifecycle, "source": r.source, "model_version": mv,
             "classification_mode": "FORCED_CLOSURE", "confidence": t["band"],
             "review_reason": t["review_reason"], "visual_dependency": t["visual"],
             "justification": t["justification"], "evidence_used": ev}
            for t, r, ev, mv in created]
        for t, r, ev, mv in created:
            report[f"Q{t['num']}"] = {"question": f"{t['year']} Q{t['num']}", "verdict": "PERSISTED_FORCED_CLOSURE",
                                      "content_code": r.content, "pc_id": str(r.id), "status": r.status,
                                      "confidence": t["band"], "review_reason": t["review_reason"],
                                      "visual_dependency": t["visual"]}
        new_ids = [r.id for _, r, _, _ in created]

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
            report["catalog_integrity_after"] = await _catalog_integrity(ro)
            report["distribution"] = await _disc(ro)
            per_q = {}
            for tgt in TARGETS:
                rows = await _active_cv2(ro, tgt["qv"])
                per_q[f"Q{tgt['num']}"] = {
                    "active_cv2_rows": [dict(x) for x in rows],
                    "ok": len(rows) == 1 and rows[0]["pcc"] == tgt["content"]
                    and rows[0]["cm"] == "FORCED_CLOSURE" and rows[0]["cf"] == tgt["band"]}
            report["target_verification"] = per_q
            dup = (await ro.execute(text(
                """SELECT question_version_id, count(*) FROM pedagogical_classifications
                   WHERE lifecycle='ACTIVE' AND metadata->>'taxonomy_version'='curriculum-v2'
                   GROUP BY 1 HAVING count(*)>1"""))).all()
            report["duplicate_active_cv2"] = [[str(d[0]), d[1]] for d in dup]
            report["orphan_classifications"] = int(await ro.scalar(text(
                """SELECT count(*) FROM pedagogical_classifications pc
                   LEFT JOIN question_versions qv ON qv.id=pc.question_version_id WHERE qv.id IS NULL""")))
            report["classifications_pointing_to_inactive_node"] = int(await ro.scalar(text(
                """SELECT count(*) FROM pedagogical_classifications pc
                   JOIN catalog_nodes cn ON cn.code = pc.metadata->>'primary_content_code'
                   WHERE pc.lifecycle='ACTIVE' AND pc.metadata->>'taxonomy_version'='curriculum-v2'
                     AND cn.active = false""")))
            # historical-metadata preservation check on the 10 new rows
            hist_ok = True
            for _, r, _, _ in created:
                md = (await ro.execute(text("SELECT metadata FROM pedagogical_classifications WHERE id=:i"),
                                       {"i": r.id})).scalar()
                if not all(k in md for k in ("primary_content_code", "taxonomy_version", "evidence",
                                             "question_content_hash", "proposal_status")):
                    hist_ok = False
            report["historical_metadata_preserved_on_new_rows"] = hist_ok

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
            "inactive_node_refs": report["classifications_pointing_to_inactive_node"],
            "catalog_ok": report["catalog_integrity_after"]["ok"],
            "all_targets_ok": all(v["ok"] for v in per_q.values()),
            "historical_metadata_preserved": report["historical_metadata_preserved_on_new_rows"],
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
              and ic["catalog_ok"] and ic["all_targets_ok"] and ic["historical_metadata_preserved"]
              and provider_calls == 0
              and report["idempotency"]["new_rows"] == 0 and report["idempotency"]["provider_calls"] == 0)
        report["security"] = {
            "DATABASE_WRITES": 1 if report["deltas"]["pedagogical_classifications"] else 0,
            "CLASSIFICATIONS_CREATED": report["deltas"]["pedagogical_classifications"],
            "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
            "BINDINGS_CREATED": 0,
            "OPENAI_CALLS": provider_calls,
            "ALEMBIC_EXECUTION": 0,
            "CODE_FILES_MODIFIED": 0,
            "PRODUCTION_DATA_MODIFIED": 1,
            "questions_versions_options_modified": 0,
            "existing_classifications_modified": 0,
            "no_provider_constructed": True,
            "secrets_in_report": False,
        }
        report["total_elapsed_s"] = round(time.time() - t0, 1)
        report["FINAL_DECISION"] = ("PHASE_11_34_FINAL_CLASSIFICATION_CLOSURE_COMPLETE" if ok
                                    else "PHASE_11_34_NEEDS_REVIEW")
        return report
    except Abort as exc:
        report["FINAL_DECISION"] = "PHASE_11_34_ABORTED"
        report["abort_reason"] = _scrub(exc)
        return report
    finally:
        await engine.dispose()


def _finalize_static(report: dict) -> None:
    forced = [f"{t['year']} Q{t['num']}" for t in TARGETS]
    report["forced_closure_questions"] = forced
    report["forced_closure_detail"] = [
        {"question": f"{t['year']} Q{t['num']}", "content": t["content"], "confidence": t["band"],
         "review_reason": t["review_reason"], "visual_dependency": t["visual"],
         "justification": t["justification"]}
        for t in TARGETS]
    report["classification_mode_counts"] = {"DETERMINISTIC": 0, "AI_ASSISTED": 0, "FORCED_CLOSURE": len(TARGETS)}
    report["protected_proposals_registered_only"] = PROTECTED_PROPOSALS
    d2_total = 56
    d2_class_before = 42
    report["day2_coverage_before"] = f"{d2_class_before}/{d2_total} ({round(100*d2_class_before/d2_total)}%)"
    d2_after = d2_class_before + len(TARGETS)
    report["day2_coverage_after"] = f"{d2_after}/{d2_total} ({round(100*d2_after/d2_total)}%)"
    report["day2_remaining_unclassified"] = ["2020 Q91", "2020 Q93", "2020 Q107", "2020 Q128"]
    report["day2_remaining_reason"] = ("4 protected 2020 questions - rule 5 forbids persistence; "
                                       "proposals registered for review only")
    report["day1_note"] = ("276 Day-1 questions (Linguagens + Ciencias Humanas) are structurally "
                           "OUT OF SCOPE for curriculum-v2, which has only Physics/Chemistry/Biology/"
                           "Math nodes. They are not HUMAN_REVIEW - they are not in this taxonomy's domain.")
    report["closure_summary"] = {
        "day2_in_scope": d2_total,
        "day2_final_classified": d2_after,
        "day2_forced_closure_of_those": len(TARGETS),
        "day2_protected_pending": 4,
        "day2_unresolved_non_protected": 0,
    }


async def _second_run(factory) -> dict:
    async with factory() as ro:
        await ro.execute(text("SET TRANSACTION READ ONLY"))
        c0 = await _counts(ro)
    noop, wrote = 0, 0
    for tgt in TARGETS:
        async with factory() as s:
            if await _active_cv2(s, tgt["qv"]):
                noop += 1
                continue
            svc = ClassificationProposalService(s)
            _orig = s.commit
            s.commit = s.flush
            try:
                await _persist_one(s, svc, tgt)
                wrote += 1
                s.commit = _orig
                await s.commit()
            except Exception:
                s.commit = _orig
                await s.rollback()
                raise
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
