"""PHASE 11.13 - HUMAN_REVIEW resolution audit. READ-ONLY. Zero writes. No LLM.

Audits the 4 questions left HUMAN_REVIEW by PHASE 11.11:
  2024 Q104, 2024 Q150, 2024 Q133, 2025 Q99.

For each: full statement + 5 options + provenance + page/asset info, the current
deterministic recover_candidates() output (curriculum-v2, 26 bindings, 64 nodes),
the current binding situation, and the PHASE 11.11 3-run traces. No provider call.
"""
from __future__ import annotations

import asyncio
import json
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
from agente_ia_edu.services.curriculum_classification import ClassificationProposalService  # noqa: E402
import agente_ia_edu.services._curriculum_v2_bindings as B  # noqa: E402

TARGETS = [(2024, 104), (2024, 150), (2024, 133), (2025, 99)]
A3_REPORT = _REPO / "var" / "phase11_11_classification_proposals_report.json"
OUT = _REPO / "var" / "phase11_13_human_review_audit_report.json"

COUNT_TABLES = ["questions", "question_versions", "question_options", "booklet_questions",
                "catalog_nodes", "catalog_node_prerequisites",
                "pedagogical_classifications", "question_classifications"]
CV2_ACTIVE = ("SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
              "AND metadata->>'taxonomy_version'='curriculum-v2'")


async def _counts(s):
    d = {t: int(await s.scalar(text(f"SELECT count(*) FROM {t}"))) for t in COUNT_TABLES}
    d["curriculum_v2_ACTIVE"] = int(await s.scalar(text(CV2_ACTIVE)))
    return d


async def main():
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    report = {"phase": "11.13", "mode": "READ_ONLY_AUDIT"}
    try:
        a3 = json.loads(A3_REPORT.read_text())
        a3_runs = {(e["year"], e["official_number"]): e for e in a3.get("run_log", [])}

        async with factory() as s:
            await s.execute(text("SET TRANSACTION READ ONLY"))
            report["current_database"] = await s.scalar(text("SELECT current_database()"))
            report["counts_before"] = await _counts(s)

            catalog = list((await s.scalars(
                select(CatalogNode).where(CatalogNode.active.is_(True)).order_by(CatalogNode.code))).all())
            report["taxonomy_size"] = {"active_nodes": len(catalog),
                                       "curriculum_v2_binding_specs": len(B._SPECS)}
            bound_codes = {sp[0] for sp in B._SPECS}
            svc = ClassificationProposalService(s)

            items = []
            for y, n in TARGETS:
                r = (await s.execute(text("""
                    SELECT ea.year yr, bq.official_number num, ea.day AS exam_day, eb.code booklet,
                           bq.question_version_id qvid, bq.page_number, bq.evidence_uri,
                           bq.extraction_method, bq.metadata AS bq_meta,
                           qv.statement stmt, qv.canonical_text ctext, qv.metadata AS qv_meta,
                           (SELECT json_agg(json_build_object('key', qo.option_key, 'text', qo.text)
                                            ORDER BY qo.position)
                            FROM question_options qo WHERE qo.question_version_id=bq.question_version_id) opts
                    FROM booklet_questions bq
                    JOIN question_versions qv ON qv.id=bq.question_version_id
                    JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                    JOIN exam_applications ea ON ea.id=eb.exam_application_id
                    WHERE ea.year=:y AND bq.official_number=:n"""), {"y": y, "n": n})).mappings().first()
                stmt = r["stmt"] or r["ctext"] or ""
                cands = svc.recover_candidates(stmt, catalog)
                slim = [{"rank": c["rank"], "content_code": c.get("content_code"),
                         "area_code": c.get("area_code"), "discipline_code": c.get("discipline_code"),
                         "candidate_type": c.get("candidate_type"), "score": c.get("score"),
                         "matched_terms": (c.get("matched_terms") or [])[:8]} for c in cands]
                # current ACTIVE pedagogical classifications for this qv (any taxonomy)
                pcs = (await s.execute(text("""
                    SELECT lifecycle, status, metadata->>'taxonomy_version' tv,
                           metadata->>'content_code' cc, metadata->>'primary_content_code' pcc
                    FROM pedagogical_classifications WHERE question_version_id=:qv
                    ORDER BY created_at"""), {"qv": str(r["qvid"])})).mappings().all()
                a3e = a3_runs.get((y, n), {})
                items.append({
                    "question": f"{y} Q{n}",
                    "day": r["exam_day"], "booklet": r["booklet"],
                    "question_version_id": str(r["qvid"]),
                    "page_number": r["page_number"],
                    "evidence_uri": r["evidence_uri"],
                    "extraction_method": r["extraction_method"],
                    "bq_metadata": r["bq_meta"],
                    "qv_metadata": r["qv_meta"],
                    "statement": " ".join(stmt.split()),
                    "options": r["opts"],
                    "recover_candidates_now": slim,
                    "target_node_bound": {
                        "MATH-ALGEBRA-LOGARITHMS": "MATH-ALGEBRA-LOGARITHMS" in bound_codes,
                        "BIOLOGY-EVOLUTION-MECHANISMS": "BIOLOGY-EVOLUTION-MECHANISMS" in bound_codes,
                        "BIOLOGY-ECOLOGY-COMMUNITIES-SUCCESSION": "BIOLOGY-ECOLOGY-COMMUNITIES-SUCCESSION" in bound_codes,
                        "BIOLOGY-CYTOLOGY-ORGANELLES": "BIOLOGY-CYTOLOGY-ORGANELLES" in bound_codes,
                        "PHYSICS-MECHANICS-KINEMATICS": "PHYSICS-MECHANICS-KINEMATICS" in bound_codes,
                    },
                    "existing_pedagogical_classifications": [dict(p) for p in pcs],
                    "phase_11_11_runs": a3e.get("runs"),
                })
            report["questions"] = items

            # binding-collision / granularity helpers (bounded scans, only where needed)
            # (1) does the literal token 'logaritmo' occur in any of the 4? and how many
            #     of the 332 statements contain 'logaritm' vs 'log ' (for a future Q150 binding)
            log_hits = (await s.execute(text("""
                SELECT ea.year y, bq.official_number n, ea.day d
                FROM booklet_questions bq
                JOIN question_versions qv ON qv.id=bq.question_version_id
                JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                JOIN exam_applications ea ON ea.id=eb.exam_application_id
                WHERE lower(qv.statement) LIKE '%logaritm%' """))).all()
            report["statements_containing_logaritm"] = [[h[0], h[1], h[2]] for h in log_hits]
            richter_hits = (await s.execute(text("""
                SELECT ea.year y, bq.official_number n, ea.day d
                FROM booklet_questions bq
                JOIN question_versions qv ON qv.id=bq.question_version_id
                JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                JOIN exam_applications ea ON ea.id=eb.exam_application_id
                WHERE lower(qv.statement) LIKE '%escala richter%' OR lower(qv.statement) LIKE '%richter%' """))).all()
            report["statements_containing_richter"] = [[h[0], h[1], h[2]] for h in richter_hits]
            mimet_hits = (await s.execute(text("""
                SELECT ea.year y, bq.official_number n, ea.day d
                FROM booklet_questions bq
                JOIN question_versions qv ON qv.id=bq.question_version_id
                JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                JOIN exam_applications ea ON ea.id=eb.exam_application_id
                WHERE lower(qv.statement) LIKE '%mimetismo%' OR lower(qv.statement) LIKE '%falsas-corais%'
                   OR lower(qv.statement) LIKE '%falsa-coral%' """))).all()
            report["statements_containing_mimicry_terms"] = [[h[0], h[1], h[2]] for h in mimet_hits]
            # (2) BIOLOGY-CYTOLOGY area children (granularity of the organelles node)
            cyto = (await s.execute(text("""
                SELECT c.code, c.node_type, p.code parent FROM catalog_nodes c
                LEFT JOIN catalog_nodes p ON p.id=c.parent_id
                WHERE c.code LIKE 'BIOLOGY-CYTOLOGY%' ORDER BY c.code"""))).all()
            report["biology_cytology_subtree"] = [[x[0], x[1], x[2]] for x in cyto]
            evo = (await s.execute(text("""
                SELECT c.code, c.node_type, p.code parent FROM catalog_nodes c
                LEFT JOIN catalog_nodes p ON p.id=c.parent_id
                WHERE c.code LIKE 'BIOLOGY-EVOLUTION%' OR c.code LIKE 'BIOLOGY-ECOLOGY%' ORDER BY c.code"""))).all()
            report["biology_evolution_ecology_subtree"] = [[x[0], x[1], x[2]] for x in evo]

            report["counts_after"] = await _counts(s)

        report["deltas"] = {k: report["counts_after"][k] - report["counts_before"][k]
                            for k in report["counts_before"]}
        report["security"] = {
            "DATABASE_WRITES": 0,
            "OPENAI_CALLS": 0,
            "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
            "VOCABULARY_CHANGES": 0,
            "PRODUCTION_DATA_MODIFIED": 1 if any(v != 0 for v in report["deltas"].values()) else 0,
        }
        report["integrity_ok"] = all(v == 0 for v in report["deltas"].values())
        report["FINAL_DECISION"] = ("PHASE_11_13_HUMAN_REVIEW_AUDIT_COMPLETE"
                                    if report["integrity_ok"] else "PHASE_11_13_NEEDS_REVIEW")
    finally:
        await engine.dispose()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("FINAL_DECISION", "").endswith("COMPLETE") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
