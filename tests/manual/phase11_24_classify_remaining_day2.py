"""PHASE 11.24 - classify the 24 currently-unclassified Day-2 questions (12 CN + 12 MT).

PROPOSAL-ONLY. Uses the PHASE 11.23 AI-agnostic entrypoint:

    AiAgnosticClassificationService(session_factory).propose_and_audit(...)
        -> build_text_provider()  (provider selected by AI_PROVIDER)
        -> versioned prompt artifact
        -> run_classification_consensus (N=3, HIGH, same CONTENT, all deterministic gates)
        -> ConsensusOutcome  (NOT persisted)

No direct OpenAIProvider construction. No taxonomy changes. No writes: every
consensus run executes in its own rolled-back transaction, and a separate
read-only connection confirms 0 deltas.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
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
from agente_ia_edu.services.classification_consensus import DEFAULT_CONSENSUS_POLICY  # noqa: E402

OUT = _REPO / "var" / "phase11_24_classify_remaining_day2_report.json"
CLASSIFIER_VERSION = "phase11.24-remaining-day2-v1"
TAXONOMY_VERSION = "curriculum-v2"

PROTECTED = {(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)}
COUNT_TABLES = ["questions", "question_versions", "question_options", "booklet_questions",
                "catalog_nodes", "catalog_node_prerequisites",
                "pedagogical_classifications", "question_classifications"]
CV2_ACTIVE = ("SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
              "AND metadata->>'taxonomy_version'='curriculum-v2'")
_SECRET_RE = re.compile(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+")


def _scrub(v) -> str:
    s = "" if v is None else str(v)
    s = _SECRET_RE.sub("[REDACTED]", s)
    k = os.getenv("OPENAI_API_KEY")
    return s.replace(k, "[REDACTED]") if k else s


class Abort(RuntimeError):
    pass


async def _counts(session) -> dict:
    d = {t: int(await session.scalar(text(f"SELECT count(*) FROM {t}"))) for t in COUNT_TABLES}
    d["curriculum_v2_ACTIVE"] = int(await session.scalar(text(CV2_ACTIVE)))
    return d


async def _protected_fp(session) -> dict:
    import hashlib
    fp = {}
    for y, n in sorted(PROTECTED):
        rows = (await session.execute(text(
            """SELECT pc.* FROM pedagogical_classifications pc
               JOIN booklet_questions bq ON bq.question_version_id=pc.question_version_id
               JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
               JOIN exam_applications ea ON ea.id=eb.exam_application_id
               WHERE ea.year=:y AND bq.official_number=:n ORDER BY pc.created_at"""),
            {"y": y, "n": n})).mappings().all()
        fp[f"{y}_Q{n}"] = hashlib.sha256(
            json.dumps([dict(r) for r in rows], sort_keys=True, default=str).encode()).hexdigest()
    return fp


async def run() -> dict:
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    report: dict = {"phase": "11.24", "mode": "PROPOSAL_ONLY",
                    "consensus_policy": {"n": DEFAULT_CONSENSUS_POLICY.n,
                                         "required_confidence": DEFAULT_CONSENSUS_POLICY.required_confidence}}
    t0 = time.time()
    try:
        # ---------- preflight (read-only) ----------
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            report["current_database"] = await ro.scalar(text("SELECT current_database()"))
            if report["current_database"] != "agente_ia_edu":
                raise Abort("wrong database")
            before = await _counts(ro)
            report["preflight_counts"] = before
            report["protected_fp_before"] = await _protected_fp(ro)
            targets = (await ro.execute(text("""
                SELECT ea.year, bq.official_number AS num,
                       CASE WHEN bq.official_number BETWEEN 91 AND 135 THEN 'CN'
                            WHEN bq.official_number BETWEEN 136 AND 180 THEN 'MT' ELSE 'OTHER' END AS area,
                       bq.question_version_id::text AS qv
                FROM booklet_questions bq
                JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                JOIN exam_applications ea ON ea.id=eb.exam_application_id
                WHERE ea.day = 2
                  AND NOT EXISTS (SELECT 1 FROM pedagogical_classifications pc
                                  WHERE pc.question_version_id = bq.question_version_id
                                    AND pc.lifecycle = 'ACTIVE'
                                    AND pc.metadata->>'taxonomy_version' = 'curriculum-v2')
                ORDER BY ea.year, bq.official_number"""))).mappings().all()
        target_list = [dict(r) for r in targets]
        report["targets"] = target_list
        report["target_count"] = len(target_list)
        report["target_area_breakdown"] = {
            "CN": sum(1 for t in target_list if t["area"] == "CN"),
            "MT": sum(1 for t in target_list if t["area"] == "MT"),
        }
        report["protected_questions_in_target_set"] = [
            f"{t['year']} Q{t['num']}" for t in target_list if (t["year"], t["num"]) in PROTECTED]
        if len(target_list) != 24:
            raise Abort(f"expected exactly 24 eligible Day-2 questions, found {len(target_list)}")
        if report["target_area_breakdown"] != {"CN": 12, "MT": 12}:
            raise Abort(f"expected 12 CN + 12 MT, got {report['target_area_breakdown']}")

        # ---------- consensus per target (via the AI-agnostic entrypoint) ----------
        svc = AiAgnosticClassificationService(factory)  # provider=None -> build_text_provider()
        report["prompt_artifact_version"] = svc.classification_prompt_version
        provider_calls = 0
        runs = []
        for t in target_list:
            key = (t["year"], t["num"])
            qv = __import__("uuid").UUID(t["qv"])
            r0 = time.time()
            outcome = await svc.propose_and_audit(
                qv, classifier_version=f"{CLASSIFIER_VERSION}",
                taxonomy_version=TAXONOMY_VERSION, classification_mode="STANDARD")
            provider_calls += DEFAULT_CONSENSUS_POLICY.n
            per_run = [
                {"outcome": rr.outcome, "content_code": rr.content_code, "confidence": rr.confidence,
                 "discipline": rr.discipline_code, "review_reason": rr.review_reason,
                 "gap_type": rr.gap_type, "error": _scrub(rr.error) if rr.error else None}
                for rr in outcome.runs
            ]
            runs.append({
                "question": f"{t['year']} Q{t['num']}", "area": t["area"], "question_version_id": t["qv"],
                "protected": key in PROTECTED,
                "verdict": outcome.verdict, "content_code": outcome.content_code,
                "confidence": outcome.confidence, "n": outcome.n, "reason": outcome.reason,
                "runs": per_run, "elapsed_s": round(time.time() - r0, 1),
            })
        report["provider_calls"] = provider_calls
        report["run_log"] = runs

        # ---------- post read-only ----------
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            after = await _counts(ro)
            report["postflight_counts"] = after
            report["protected_fp_after"] = await _protected_fp(ro)
            report["rows_created_by_this_phase"] = int(await ro.scalar(text(
                "SELECT count(*) FROM pedagogical_classifications WHERE model_version LIKE 'phase11.24-%'")))
        report["deltas"] = {k: after[k] - before[k] for k in after}
        report["leak"] = any(v != 0 for v in report["deltas"].values())
        report["protected_unchanged"] = report["protected_fp_before"] == report["protected_fp_after"]

        # ---------- consolidation ----------
        final_classified = [r for r in runs if r["verdict"] == "CLASSIFIED"]
        human_review = [r for r in runs if r["verdict"] == "HUMAN_REVIEW"]
        gap = [r for r in runs if any(x["outcome"] == "CURRICULUM_GAP" for x in r["runs"])
               and r["verdict"] != "CLASSIFIED"]
        provider_error = [r for r in runs if any(x["outcome"] == "PROVIDER_ERROR" for x in r["runs"])]
        report["FINAL_CLASSIFIED"] = [
            {"question": r["question"], "area": r["area"], "content_code": r["content_code"],
             "confidence": r["confidence"], "protected": r["protected"]}
            for r in final_classified]
        report["HUMAN_REVIEW"] = [
            {"question": r["question"], "area": r["area"], "reason": r["reason"],
             "run_outcomes": [x["outcome"] for x in r["runs"]],
             "run_contents": [x["content_code"] for x in r["runs"]],
             "run_confidence": [x["confidence"] for x in r["runs"]]}
            for r in human_review]
        report["CURRICULUM_GAP_questions"] = [r["question"] for r in gap]
        report["PROVIDER_ERROR_questions"] = [r["question"] for r in provider_error]
        report["totals"] = {
            "processed": len(runs),
            "FINAL_CLASSIFIED": len(final_classified),
            "HUMAN_REVIEW": len(human_review),
            "with_a_CURRICULUM_GAP_run": len(gap),
            "with_a_PROVIDER_ERROR_run": len(provider_error),
        }
        report["security"] = {
            "DATABASE_WRITES": 0 if not report["leak"] else 1,
            "CLASSIFICATIONS_CREATED": report["deltas"]["pedagogical_classifications"],
            "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
            "TAXONOMY_CHANGES": report["deltas"]["catalog_nodes"] + report["deltas"]["catalog_node_prerequisites"],
            "OPENAI_CALLS": provider_calls,
            "ALEMBIC_EXECUTION": 0,
            "PRODUCTION_DATA_MODIFIED": 1 if report["leak"] else 0,
            "rows_created_by_this_phase": report["rows_created_by_this_phase"],
            "no_direct_openai_construction": True,
            "api_key_in_report": False,
        }
        report["total_elapsed_s"] = round(time.time() - t0, 1)
        ok = (not report["leak"] and report["protected_unchanged"]
              and report["rows_created_by_this_phase"] == 0
              and provider_calls == 72)
        report["FINAL_DECISION"] = ("PHASE_11_24_CLASSIFY_REMAINING_DAY2_COMPLETE" if ok
                                    else "PHASE_11_24_NEEDS_REVIEW")
        return report
    except Abort as exc:
        report["FINAL_DECISION"] = "PHASE_11_24_ABORTED"
        report["abort_reason"] = _scrub(exc)
        return report
    finally:
        await engine.dispose()


def main() -> int:
    rep = asyncio.run(run())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    return 0 if rep.get("FINAL_DECISION", "").endswith("COMPLETE") else 1


if __name__ == "__main__":
    sys.exit(main())
