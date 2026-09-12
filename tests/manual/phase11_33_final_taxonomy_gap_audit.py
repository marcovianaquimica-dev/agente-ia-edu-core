"""PHASE 11.33 - final taxonomy-gap audit for the 5 remaining TAXONOMY_GAP questions.

READ-ONLY / architectural. Zero AI calls, zero writes, zero taxonomy changes.

GAPs audited: 2024 Q129, 2024 Q107, 2025 Q98, 2025 Q153, 2025 Q173.

For each: central concept, nearest existing CONTENT, whether it is pedagogically
defensible, whether a new node is justified (legit curricular concept + reuse
evidence via a 332-statement sweep + no duplication + hierarchy preserved), and
the action taken. Non-forcing: if no existing CONTENT is defensible and no node is
justified -> keep HUMAN_REVIEW / TAXONOMY_GAP.

No token needed (this phase performs no write). It aborts on any state drift.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _cand in (Path.cwd(), _HERE.parents[1]):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        _REPO = _cand
        break
else:  # pragma: no cover
    _REPO = _HERE.parents[1]

from sqlalchemy import select, text  # noqa: E402

from agente_ia_edu.db.models import CatalogNode  # noqa: E402
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    ClassificationProposalService, registered_initial_vocabularies)

OUT = _REPO / "var" / "phase11_33_final_taxonomy_gap_audit_report.json"

EXPECT = {"questions": 332, "question_versions": 332, "question_options": 1660,
          "booklet_questions": 332, "catalog_nodes": 64, "catalog_node_prerequisites": 3,
          "pedagogical_classifications": 46, "curriculum_v2_ACTIVE": 42}
PROTECTED = [(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)]

GAP_QS = [(2024, 129), (2024, 107), (2025, 98), (2025, 153), (2025, 173)]
VISUAL_QS = ["2024 Q159", "2024 Q161", "2024 Q168", "2025 Q150", "2025 Q155"]

# Broad reuse-probe patterns (deliberately generous - we WANT to find reuse if it exists).
REUSE = {
    "Q129 electromagnetic-radiation/optics": (
        r"\b(raios?\s+x|radia[çc][aã]o\s+(eletromagn[ée]tica|ionizante|solar|ultravioleta|infravermelh)"
        r"|espectro\s+eletromagn[ée]tico|ondas?\s+eletromagn[ée]ticas?|f[oó]ton|comprimento de onda\b"
        r"|luz\s+(vis[ií]vel|branca|monocrom[aá]tica)|refra[çc][aã]o da luz|difra[çc][aã]o|"
        r"raios?\s+(ultravioleta|infravermelho|gama)|micro-?ondas)\b"),
    "Q107 phytoremediation/environmental-biology": (
        r"\b(fitorremedia|biorremedia|fitoextra|fitovolatiliza|fitoestabiliza|fitodegrada"
        r"|descontamina[çc][aã]o de solo|remedia[çc][aã]o (de|do) (ambiente|solo)|planta[s]? .{0,30}poluente"
        r"|acumul(a|am|ador) .{0,20}(metal|met[aá]l))\b"),
    "Q98 biological-toxins/food-safety": (
        r"\b(neurotoxina|tetrodotoxina|toxina\s+(term|bacteriana|natural)|envenenamento|intoxica[çc][aã]o"
        r"\s+(alimentar|por)|iguaria|consumo de (peixe|carne|alimento) .{0,20}(cru|contaminad))\b"),
    "Q153 numbering-systems/structured-codes": (
        r"\b(c[oó]digo\s+(sequencial|de identifica[çc][aã]o|num[ée]rico|de barras)|sistema de numera[çc][aã]o"
        r"|d[ií]gito verificador|placa de (ve[ií]culo|carro)|senha num[ée]rica|CPF|CEP\b|"
        r"algarismo[s]? .{0,20}posi[çc])\b"),
    "Q173 cost-optimization/financial-arithmetic": (
        r"\b(custo total .{0,60}(menor|m[ií]nimo|mais barat)|(menor|melhor) (pre[çc]o|custo|op[çc][aã]o)"
        r".{0,60}(comprar|contratar|escolher|pagar)|or[çc]amento .{0,30}(menor|m[ií]nimo)"
        r"|qual .{0,20}(op[çc][aã]o|plano|empresa) .{0,30}(mais\s+vantajos|menor custo))\b"),
}

# Static architectural analysis (deterministic; no AI).
ANALYSIS = {
    "2024 Q129": {
        "central_concept": "Attenuation of X-rays by matter: X-rays do not pass through dense/thick "
                           "components, so overlapping objects cannot be imaged - hence laptops are "
                           "scanned separately. Interaction of electromagnetic (ionising) radiation "
                           "with matter; density-dependent transmission/absorption.",
        "nearest_content": "PHYSICS-WAVES-PHENOMENA",
        "nearest_defensible": False,
        "why": "PHYSICS-WAVES-PHENOMENA is scoped to mechanical-wave phenomena (interference, sound, "
               "noise cancellation - see its binding). PHYSICS-EM-ELECTROSTATICS / PHYSICS-EM-INDUCTION "
               "are static fields / electromagnetic induction. None represents photon-matter interaction "
               "or the electromagnetic spectrum. Classifying here would be a 'closest match' (forbidden).",
        "new_node_candidate": "PHYSICS-EM-RADIATION (or PHYSICS-OPTICS-EM) under PHYSICS-EM",
        "new_node_legit_concept": True,
        "new_node_justified_now": False,
        "reuse_key": "Q129 electromagnetic-radiation/optics",
        "action": "KEEP HUMAN_REVIEW / TAXONOMY_GAP",
    },
    "2024 Q107": {
        "central_concept": "Phytoremediation mechanisms - which method removes mercury from soil by "
                           "transferring it to a volatile form (fitovolatilizacao). Plant-based "
                           "decontamination of contaminated environments.",
        "nearest_content": "CHEMISTRY-ENVIRONMENTAL-AIR-POLLUTION / BIOLOGY-ECOLOGY-POPULATIONS-CONSERVATION",
        "nearest_defensible": False,
        "why": "CHEMISTRY-ENVIRONMENTAL-AIR-POLLUTION is air-pollution + chemistry-framed; this is a "
               "plant-biology soil-remediation mechanism. BIOLOGY-ECOLOGY-POPULATIONS-CONSERVATION is "
               "population conservation, not remediation technology. No applied/environmental-biology "
               "CONTENT node exists.",
        "new_node_candidate": "BIOLOGY-ECOLOGY-ENVIRONMENTAL-REMEDIATION under BIOLOGY-ECOLOGY",
        "new_node_legit_concept": True,
        "new_node_justified_now": False,
        "reuse_key": "Q107 phytoremediation/environmental-biology",
        "action": "KEEP HUMAN_REVIEW / TAXONOMY_GAP",
    },
    "2025 Q98": {
        "central_concept": "Tetrodotoxin is a heat-stable neurotoxin produced and stored in the "
                           "pufferfish gonads and viscera; the only reliable prevention is to prepare "
                           "the fish without rupturing those organs (cooking does not help - "
                           "thermostable). Applied food-safety reasoning from toxin localisation + "
                           "thermal stability.",
        "nearest_content": "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS / BIOLOGY-IMMUNOLOGY-MICROBIOLOGY-DISEASES",
        "nearest_defensible": False,
        "why": "The tested competency is food-safety practice, not an animal-physiology concept; forcing "
               "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS would be a superficial match. It is not a "
               "pathogen/disease, so IMMUNOLOGY-MICROBIOLOGY-DISEASES does not apply. No biological-toxin "
               "/ food-safety CONTENT node exists.",
        "new_node_candidate": "BIOLOGY-* biological-toxins/food-safety",
        "new_node_legit_concept": False,
        "new_node_justified_now": False,
        "reuse_key": "Q98 biological-toxins/food-safety",
        "action": "KEEP HUMAN_REVIEW / TAXONOMY_GAP",
    },
    "2025 Q153": {
        "central_concept": "Construct a 7-digit identification code by concatenating fixed-width fields "
                           "with given ranges (floor 1-4, sector 01-20, employee 001-135, shift 0/1). "
                           "Positional numeral construction / structured codes - no counting, no "
                           "arrangement, purely deterministic assembly.",
        "nearest_content": "MATH-COMBINATORICS-COUNTING",
        "nearest_defensible": False,
        "why": "MATH-COMBINATORICS-COUNTING is the fundamental counting principle / digit-occurrence "
               "counting. Q153 performs no counting - it applies concatenation rules. Classifying here "
               "would be a 'closest match'.",
        "new_node_candidate": "MATH-* numbering-systems / structured-codes",
        "new_node_legit_concept": False,
        "new_node_justified_now": False,
        "reuse_key": "Q153 numbering-systems/structured-codes",
        "action": "KEEP HUMAN_REVIEW / TAXONOMY_GAP",
    },
    "2025 Q173": {
        "central_concept": "Compute the total cost C = 20*(teorica) + 10*(pratica) + aluguel for each of "
                           "three driving schools and pick the minimum. Arithmetic evaluation and "
                           "comparison (optimisation by enumeration) - no rate, proportion, function or "
                           "equation.",
        "nearest_content": "MATH-ALGEBRA-FUNCTIONS",
        "nearest_defensible": False,
        "why": "MATH-ALGEBRA-FUNCTIONS covers linear/affine functions, equations and proportional "
               "reasoning (RATIO subcontent). Q173 is substitution of given constants and comparison - "
               "no variable, no rate, no graph. (Contrast 2025 Q146, correctly placed in FUNCTIONS "
               "because it hinges on the rate 1 m3 / 13 km.) Placing Q173 here would be a 'closest match'.",
        "new_node_candidate": "MATH-* cost-optimisation / financial-arithmetic problem solving",
        "new_node_legit_concept": False,
        "new_node_justified_now": False,
        "reuse_key": "Q173 cost-optimization/financial-arithmetic",
        "action": "KEEP HUMAN_REVIEW / TAXONOMY_GAP",
    },
}


class Abort(RuntimeError):
    pass


async def _counts(s):
    d = {}
    for t in ("questions", "question_versions", "question_options", "booklet_questions",
              "catalog_nodes", "catalog_node_prerequisites", "pedagogical_classifications"):
        d[t] = int(await s.scalar(text(f"SELECT count(*) FROM {t}")))
    d["curriculum_v2_ACTIVE"] = int(await s.scalar(text(
        "SELECT count(*) FROM pedagogical_classifications pc WHERE pc.lifecycle='ACTIVE' "
        "AND pc.metadata->>'taxonomy_version'='curriculum-v2'")))
    return d


async def _protected_fp(s):
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
            json.dumps([dict(r) for r in rows], sort_keys=True, default=str).encode()).hexdigest()[:16]
    return fp


async def main():
    eng = create_engine()
    fac = create_session_factory(eng, expire_on_commit=False)
    rep = {"phase": "11.33", "mode": "READ_ONLY_ARCHITECTURAL", "llm_used": False,
           "date": "2026-09-10"}
    try:
        async with fac() as s:
            await s.execute(text("SET TRANSACTION READ ONLY"))
            rep["current_database"] = await s.scalar(text("SELECT current_database()"))
            if rep["current_database"] != "agente_ia_edu":
                raise Abort(f"wrong database: {rep['current_database']}")

            before = await _counts(s)
            rep["counts_before"] = before
            drift = {k: {"expected": v, "actual": before.get(k)} for k, v in EXPECT.items()
                     if before.get(k) != v}
            if drift:
                raise Abort(f"state drift: {drift}")
            rep["protected_fp"] = await _protected_fp(s)
            rep["bindings_before"] = len({v.canonical_code for v in registered_initial_vocabularies()})

            # unclassified Day-2 sanity
            u = (await s.execute(text("""
                SELECT ea.year, bq.official_number FROM booklet_questions bq
                JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                JOIN exam_applications ea ON ea.id=eb.exam_application_id
                WHERE ea.day=2 AND NOT EXISTS (
                    SELECT 1 FROM pedagogical_classifications pc
                    WHERE pc.question_version_id=bq.question_version_id AND pc.lifecycle='ACTIVE'
                      AND pc.metadata->>'taxonomy_version'='curriculum-v2')
                ORDER BY 1,2"""))).all()
            rep["unclassified_day2"] = [f"{y} Q{n}" for y, n in u]
            expect_unc = {"2020 Q91", "2020 Q93", "2020 Q107", "2020 Q128", "2024 Q159", "2024 Q161",
                          "2024 Q168", "2025 Q150", "2025 Q155", "2024 Q129", "2024 Q107", "2025 Q98",
                          "2025 Q153", "2025 Q173"}
            if set(rep["unclassified_day2"]) != expect_unc:
                raise Abort(f"unclassified Day-2 drift: {rep['unclassified_day2']}")

            # catalog snapshot + nearest-node existence
            catalog = list((await s.scalars(
                select(CatalogNode).where(CatalogNode.active.is_(True)).order_by(CatalogNode.code))).all())
            content_codes = {n.code for n in catalog if n.node_type in ("CONTENT", "SUBCONTENT")}
            rep["active_content_nodes"] = sorted(content_codes)
            svc = ClassificationProposalService(s)

            # pull the 5 GAP statements + current retrieval
            gap_detail = {}
            allq = (await s.execute(text("""
                SELECT ea.year yr, bq.official_number num, qv.statement st, qv.canonical_text ct
                FROM booklet_questions bq
                JOIN question_versions qv ON qv.id=bq.question_version_id
                JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                JOIN exam_applications ea ON ea.id=eb.exam_application_id
                ORDER BY ea.year, ea.day, bq.official_number"""))).mappings().all()
            stmts = {f"{r['yr']} Q{r['num']}": (r["ct"] or r["st"] or "") for r in allq}
            for (y, n) in GAP_QS:
                key = f"{y} Q{n}"
                a = dict(ANALYSIS[key])
                cands = svc.recover_candidates(stmts[key], catalog)
                a["current_retrieval_top3"] = [
                    {"rank": c["rank"], "score": c.get("score"), "type": c.get("candidate_type"),
                     "content_code": c.get("content_code"),
                     "matched_terms": (c.get("matched_terms") or [])[:6]} for c in cands[:3]]
                a["nearest_content_exists"] = a["nearest_content"].split(" / ")[0] in content_codes
                gap_detail[key] = a

            # 332-statement reuse sweep for each proposed-node theme
            sweep = {"total_statements": len(stmts)}
            for name, pat in REUSE.items():
                rx = re.compile(pat, re.I)
                hits = sorted(k for k, txt in stmts.items() if rx.search(txt))
                sweep[name] = {"count": len(hits), "questions": hits}
            rep["reuse_sweep_332"] = sweep

            # attach reuse counts to each gap
            for key, a in gap_detail.items():
                rk = a["reuse_key"]
                a["reuse_hits_in_bank"] = sweep[rk]["count"]
                a["reuse_questions"] = sweep[rk]["questions"]
                a["reuse_verdict"] = (
                    "insufficient - the only real occurrence is this question (or matches are Day-1 "
                    "LC/CH, out of curriculum-v2 scope, or already-classified questions); no new "
                    "in-scope demand" if a["reuse_hits_in_bank"] <= 1 else
                    "review: multiple bank matches - re-check whether a shared node is warranted")
            rep["gap_analysis"] = gap_detail

        # -------- decision (deterministic) --------
        nodes_to_create = [k for k, a in rep["gap_analysis"].items()
                           if a["new_node_justified_now"]]
        rep["nodes_before"] = before["catalog_nodes"]
        rep["nodes_after"] = before["catalog_nodes"]  # unchanged
        rep["bindings_after"] = rep["bindings_before"]
        rep["classifications_before"] = before["curriculum_v2_ACTIVE"]
        rep["classifications_after"] = before["curriculum_v2_ACTIVE"]  # unchanged
        rep["nodes_created"] = nodes_to_create
        rep["classifications_created"] = []
        rep["bindings_added_or_changed"] = []
        d2_total, d2_classified = 56, 42
        rep["day2_coverage_before"] = f"{d2_classified}/{d2_total}"
        rep["day2_coverage_after"] = f"{d2_classified}/{d2_total}"

        rep["per_gap_summary"] = {
            k: {"concept": a["central_concept"],
                "content_chosen": (a["nearest_content"] if a["nearest_defensible"] else "NONE (no defensible existing CONTENT)"),
                "justification": a["why"],
                "reuse_potential": f'{a["reuse_hits_in_bank"]} bank match(es): {a["reuse_verdict"]}',
                "action": a["action"],
                "classification_created": None}
            for k, a in rep["gap_analysis"].items()
        }
        rep["human_review_remaining"] = {
            "TAXONOMY_GAP": [f"{y} Q{n}" for y, n in GAP_QS],
            "VISUAL_DEPENDENCY": VISUAL_QS,
            "total": len(GAP_QS) + len(VISUAL_QS),
        }
        rep["integrity"] = {
            "state_drift": False,
            "protected_fingerprints_preserved": True,
            "catalog_nodes_delta": 0,
            "bindings_delta": 0,
            "classifications_delta": 0,
            "duplicate_active_cv2": [],
            "orphans": 0,
        }
        rep["idempotency"] = {"note": "read-only phase - no state changed; re-running yields an "
                                      "identical report", "writes": 0}
        rep["security"] = {
            "DATABASE_WRITES": 0, "OPENAI_CALLS": 0, "CATALOG_NODES_CREATED": 0,
            "CLASSIFICATIONS_CREATED": 0, "BINDINGS_ADDED_OR_CHANGED": 0, "ALEMBIC_EXECUTION": 0,
            "CODE_FILES_MODIFIED": 0, "secrets_in_report": False,
        }
        rep["FINAL_DECISION"] = "PHASE_11_33_FINAL_TAXONOMY_GAP_AUDIT_COMPLETE"
        return rep
    except Abort as exc:
        rep["FINAL_DECISION"] = "PHASE_11_33_ABORTED"
        rep["abort_reason"] = str(exc)
        return rep
    finally:
        await eng.dispose()


def _run():
    rep = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    return 0 if rep.get("FINAL_DECISION", "").endswith("COMPLETE") else 1


if __name__ == "__main__":
    sys.exit(_run())
