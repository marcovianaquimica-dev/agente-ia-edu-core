"""PHASE 11.11 - classify the 7 questions newly unblocked by PHASE 11.10.

PROPOSAL-ONLY. Uses only the official pipeline:
  ProviderRouter -> OpenAIProvider -> ClassificationProposalService.propose_with_provider
  -> recover_candidates -> decision core.

Each question is put through the pipeline N=3 times, each in its OWN session with
commit rebound to flush and a final rollback (the PHASE 11.5-A3 technique). A
question is admitted to FINAL_CLASSIFIED only if all 3 runs return CLASSIFIED,
select exactly the same ACTIVE curriculum-v2 content, are HIGH confidence, and the
decision/evidence gates accept every time. Unanimity is necessary, not sufficient:
a semantically questionable unanimous result is still returned for HUMAN_REVIEW
(that judgement is made by a human from this report's evidence block).

Nothing is persisted. A separate read-only connection confirms 0 writes.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _cand in (Path.cwd(), _HERE.parents[1]):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        _REPO_ROOT = _cand
        break
else:  # pragma: no cover
    _REPO_ROOT = _HERE.parents[1]

from sqlalchemy import text  # noqa: E402

from agente_ia_edu.providers.factory import build_text_provider  # noqa: E402
from agente_ia_edu.providers.errors import (  # noqa: E402
    ProviderAuthenticationError, ProviderConfigurationError, ProviderInvalidResponseError,
    ProviderRateLimitError, ProviderTimeoutError, ProviderUnavailableError,
)
from agente_ia_edu.services.curriculum_classification import ClassificationProposalService  # noqa: E402

OUT = _REPO_ROOT / "var" / "phase11_11_classification_proposals_report.json"
CLASSIFIER_VERSION = "phase11.11-newly-unblocked-v1"
PROMPT_VERSION = "phase11.5a1-curriculum-v2-v1"
TAXONOMY_VERSION = "curriculum-v2"
N_REPEAT = 3

TARGETS = [(2024, 104), (2024, 134), (2024, 150), (2024, 131), (2024, 133), (2025, 100), (2025, 99)]
EXPECTED_TARGET = {
    (2024, 104): "PHYSICS-MECHANICS-KINEMATICS",
    (2024, 134): "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS",
    (2024, 150): "MATH-ALGEBRA-LOGARITHMS",
    (2024, 131): "BIOLOGY-ECOLOGY-COMMUNITIES-SUCCESSION",
    (2024, 133): "BIOLOGY-ECOLOGY-COMMUNITIES-SUCCESSION",
    (2025, 100): "BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES",
    (2025, 99): "BIOLOGY-CYTOLOGY-ORGANELLES",
}
PROTECTED = {(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)}

EXPECT = {"questions": 332, "question_versions": 332, "question_options": 1660,
          "booklet_questions": 332, "pedagogical_classifications": 30,
          "question_classifications": 0, "catalog_nodes": 64, "catalog_node_prerequisites": 3}

_PROVIDER_EXC = (ProviderAuthenticationError, ProviderConfigurationError,
                 ProviderInvalidResponseError, ProviderRateLimitError,
                 ProviderTimeoutError, ProviderUnavailableError)
_SECRET_RE = re.compile(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+")


def _scrub(s) -> str:
    s = "" if s is None else str(s)
    s = _SECRET_RE.sub("[REDACTED]", s)
    key = os.getenv("OPENAI_API_KEY")
    if key:
        s = s.replace(key, "[REDACTED]")
    return s


class Abort(RuntimeError):
    pass


async def _counts(session) -> dict:
    out = {t: int(await session.scalar(text(f"SELECT count(*) FROM {t}"))) for t in EXPECT}
    out["curriculum_v2_ACTIVE"] = int(await session.scalar(text(
        "SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
        "AND metadata->>'taxonomy_version'='curriculum-v2'")))
    out["rows_phase11_11"] = int(await session.scalar(text(
        "SELECT count(*) FROM pedagogical_classifications WHERE model_version LIKE 'phase11.11-%'")))
    return out


async def _protected_fp(session) -> dict:
    fp = {}
    for y, n in sorted(PROTECTED):
        row = (await session.execute(text("""
            SELECT bq.question_version_id AS qvid,
              (SELECT string_agg(pc.id::text||':'||pc.lifecycle,',' ORDER BY pc.id::text)
               FROM pedagogical_classifications pc WHERE pc.question_version_id=bq.question_version_id) AS cls
            FROM booklet_questions bq
            JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
            JOIN exam_applications ea ON ea.id=eb.exam_application_id
            WHERE ea.year=:y AND bq.official_number=:n"""), {"y": y, "n": n})).first()
        fp[f"{y}_Q{n}"] = {"qvid": str(row.qvid), "cls": row.cls}
    return fp


def _outcome(rec):
    md = rec.metadata_ or {}
    gap = bool(md.get("catalog_gap")) or md.get("gap_type") or md.get("review_reason") in (
        "CATALOG_GAP", "TAXONOMY_GRANULARITY_GAP")
    if gap:
        return "CURRICULUM_GAP", md.get("content_code"), md
    if md.get("proposal_status") == "NEEDS_REVIEW" or rec.status == "NEEDS_REVIEW" or md.get("review_reason"):
        return "HUMAN_REVIEW", md.get("content_code"), md
    if md.get("content_code"):
        return "CLASSIFIED", md.get("content_code"), md
    return "HUMAN_REVIEW", None, md


async def run() -> dict:
    from agente_ia_edu.db.session import create_engine, create_session_factory
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    report: dict = {"phase": "11.11", "mode": "PROPOSAL_ONLY", "n_repeat": N_REPEAT}
    t_start = time.time()
    try:
        # ---- preflight (read-only) ----
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            report["current_database"] = await ro.scalar(text("SELECT current_database()"))
            if report["current_database"] != "agente_ia_edu":
                raise Abort("wrong database")
            before = await _counts(ro)
            drift = {k: {"expected": v, "actual": before[k]} for k, v in EXPECT.items() if before[k] != v}
            if drift:
                raise Abort(f"structural drift: {drift}")
            if before["curriculum_v2_ACTIVE"] != 26:
                raise Abort(f"curriculum-v2 ACTIVE != 26 (got {before['curriculum_v2_ACTIVE']})")
            if before["rows_phase11_11"] != 0:
                raise Abort("phase 11.11 rows already present")
            protected_before = await _protected_fp(ro)
            # the 3 new nodes must exist and be ACTIVE
            newnodes = (await ro.execute(text(
                "SELECT code, active, node_type FROM catalog_nodes WHERE code = ANY(:c)"),
                {"c": ["BIOLOGY-CYTOLOGY-ORGANELLES", "MATH-ALGEBRA-LOGARITHMS",
                       "BIOLOGY-ECOLOGY-COMMUNITIES-SUCCESSION"]})).mappings().all()
            report["new_nodes_present"] = [dict(r) for r in newnodes]
            if len(newnodes) != 3 or not all(r["active"] and r["node_type"] == "CONTENT" for r in newnodes):
                raise Abort("PHASE 11.10 nodes missing/inactive")
            # batch read the 7 questions
            qrows = (await ro.execute(text("""
                SELECT ea.year yr, bq.official_number num, ea.day AS exam_day, eb.code booklet,
                       bq.question_version_id qvid, qv.statement stmt,
                       (SELECT json_agg(json_build_object('key', qo.option_key, 'text', qo.text)
                               ORDER BY qo.position)
                        FROM question_options qo WHERE qo.question_version_id=bq.question_version_id) opts
                FROM booklet_questions bq
                JOIN question_versions qv ON qv.id=bq.question_version_id
                JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                JOIN exam_applications ea ON ea.id=eb.exam_application_id
                WHERE (ea.year, bq.official_number) IN :pairs
            """).bindparams(__import__("sqlalchemy").bindparam("pairs", expanding=True)),
                {"pairs": [tuple(t) for t in TARGETS]})).mappings().all()
        qmap = {(r["yr"], r["num"]): r for r in qrows}
        if set(qmap) != set(map(tuple, TARGETS)):
            raise Abort(f"could not read all 7 questions: got {sorted(qmap)}")
        report["preflight_counts"] = before
        report["questions"] = [
            {"year": k[0], "official_number": k[1], "day": qmap[k]["exam_day"], "booklet": qmap[k]["booklet"],
             "question_version_id": str(qmap[k]["qvid"]),
             "statement": " ".join((qmap[k]["stmt"] or "").split()),
             "options": qmap[k]["opts"], "expected_target": EXPECTED_TARGET[k]}
            for k in map(tuple, TARGETS)]

        # ---- provider ----
        # PHASE 11.23: provider selected by AI_PROVIDER config, not hand-constructed.
        router = build_text_provider()
        report["provider"] = {"selected_via": "build_text_provider() / AI_PROVIDER", "model": os.getenv("OPENAI_MODEL"),
                              "temperature_control": "not supported by adapter; consistency via N=3 repetition",
                              "api_key_configured": bool(os.getenv("OPENAI_API_KEY"))}
        provider_calls = 0

        async def _one_run(qv: str, i: int) -> dict:
            nonlocal provider_calls
            t0 = time.time()
            async with factory() as session:
                _orig = session.commit
                session.commit = session.flush  # type: ignore[assignment]
                await session.begin()
                try:
                    svc = ClassificationProposalService(session)
                    try:
                        rec = await svc.propose_with_provider(
                            qv, router, classifier_version=f"{CLASSIFIER_VERSION}-r{i}",
                            taxonomy_version=TAXONOMY_VERSION, prompt_version=PROMPT_VERSION)
                        provider_calls += 1
                        dec, content, md = _outcome(rec)
                        out = {"run": i, "decision": dec, "content_code": content,
                               "discipline": md.get("discipline_code"), "area": md.get("area_code"),
                               "confidence": md.get("confidence_band"),
                               "review_reason": md.get("review_reason"),
                               "gap_type": md.get("gap_type"),
                               "selected_rank": md.get("selected_candidate_rank"),
                               "recovered": [c.get("content_code") for c in (md.get("recovered_candidates") or [])],
                               "candidate_classifications": [
                                   {"rank": c.get("rank"), "content_code": c.get("content_code"),
                                    "discipline_code": c.get("discipline_code")}
                                   for c in (md.get("candidate_classifications") or [])],
                               "evidence": [{"text": e.get("text", "")[:200], "reason": e.get("reason", "")[:200]}
                                            for e in (md.get("evidence") or [])[:3]]}
                    except _PROVIDER_EXC as exc:
                        provider_calls += 1
                        out = {"run": i, "decision": "PROVIDER_ERROR", "content_code": None,
                               "error": f"{type(exc).__name__}: "
                                        f"{_scrub(getattr(exc, 'diagnostic_message', None) or exc)}"}
                    except ValueError as exc:
                        provider_calls += 1
                        out = {"run": i, "decision": "HUMAN_REVIEW", "content_code": None,
                               "note": f"decision-core rejected: {_scrub(exc)}"}
                finally:
                    if session.in_transaction():
                        await session.rollback()
                    session.commit = _orig  # type: ignore[assignment]
            out["elapsed_s"] = round(time.time() - t0, 2)
            return out

        run_log = []
        for k in map(tuple, TARGETS):
            qv = str(qmap[k]["qvid"])
            runs = [await _one_run(qv, i) for i in range(N_REPEAT)]
            run_log.append({"year": k[0], "official_number": k[1], "question_version_id": qv, "runs": runs})

        # ---- post read-only ----
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            after = await _counts(ro)
            protected_after = await _protected_fp(ro)
        report["postflight_counts"] = after
        report["deltas"] = {k: after[k] - before[k] for k in after}
        report["leak"] = any(v != 0 for v in report["deltas"].values())
        report["protected_unchanged"] = protected_before == protected_after
        report["provider_calls"] = provider_calls
        report["run_log"] = run_log

        # ---- consolidation (unanimity gate) ----
        FINAL_CLASSIFIED, FINAL_HR, FINAL_GAP, PROVIDER_ERR = [], [], [], []
        for entry in run_log:
            k = (entry["year"], entry["official_number"])
            decs = [r["decision"] for r in entry["runs"]]
            contents = [r.get("content_code") for r in entry["runs"] if r["decision"] == "CLASSIFIED"]
            confs = [r.get("confidence") for r in entry["runs"] if r["decision"] == "CLASSIFIED"]
            dc = Counter(decs)
            row = {"year": k[0], "official_number": k[1],
                   "question_version_id": entry["question_version_id"],
                   "expected_target": EXPECTED_TARGET[k],
                   "run_decisions": decs,
                   "run_contents": [r.get("content_code") for r in entry["runs"]],
                   "run_confidence": [r.get("confidence") for r in entry["runs"]],
                   "recovered_candidates_run0": entry["runs"][0].get("recovered"),
                   "candidate_classifications_run0": entry["runs"][0].get("candidate_classifications"),
                   "evidence_run0": entry["runs"][0].get("evidence")}
            if "PROVIDER_ERROR" in decs:
                PROVIDER_ERR.append({**row, "final": "PROVIDER_ERROR"})
                continue
            unanimous_classified = (dc.get("CLASSIFIED", 0) == N_REPEAT
                                    and len(set(contents)) == 1
                                    and all(c == "HIGH" for c in confs))
            unanimous_gap = dc.get("CURRICULUM_GAP", 0) == N_REPEAT
            if unanimous_classified:
                content = contents[0]
                disc = entry["runs"][0].get("discipline") or ""
                disc_prefix_ok = content.startswith(disc.split("-")[0]) if disc else False
                sem = _semantic_flags(k, content, entry["runs"][0])
                if not disc_prefix_ok or sem["doubt"]:
                    FINAL_HR.append({**row, "final": "HUMAN_REVIEW", "content_code": content,
                                     "reason": "unanimous CLASSIFIED but " +
                                     ("discipline/content mismatch" if not disc_prefix_ok else sem["why"])})
                else:
                    FINAL_CLASSIFIED.append({**row, "final": "CLASSIFIED", "content_code": content,
                                             "discipline": disc, "area": entry["runs"][0].get("area"),
                                             "confidence": "HIGH",
                                             "matches_expected_target": content == EXPECTED_TARGET[k],
                                             "semantic_audit": sem})
            elif unanimous_gap:
                FINAL_GAP.append({**row, "final": "CURRICULUM_GAP",
                                  "note": "target node exists but is not recovered (no binding / no lexical hit); "
                                          "NOT forced into a classification"})
            else:
                FINAL_HR.append({**row, "final": "HUMAN_REVIEW",
                                 "reason": f"non-unanimous / not all HIGH: decisions={decs} contents={row['run_contents']} conf={row['run_confidence']}"})

        report["FINAL_CLASSIFIED"] = FINAL_CLASSIFIED
        report["FINAL_HUMAN_REVIEW"] = FINAL_HR
        report["FINAL_CURRICULUM_GAP"] = FINAL_GAP
        report["PROVIDER_ERROR"] = PROVIDER_ERR
        report["totals"] = {
            "analyzed": len(TARGETS),
            "FINAL_CLASSIFIED": len(FINAL_CLASSIFIED),
            "FINAL_HUMAN_REVIEW": len(FINAL_HR),
            "FINAL_CURRICULUM_GAP": len(FINAL_GAP),
            "PROVIDER_ERROR": len(PROVIDER_ERR),
        }
        report["security"] = {
            "DATABASE_WRITES": 0 if not report["leak"] else 1,
            "PRODUCTION_DATA_MODIFIED": 0 if not report["leak"] else 1,
            "OPENAI_CALLS": provider_calls,
            "CLASSIFICATIONS_CREATED": report["deltas"]["pedagogical_classifications"],
            "QUESTION_CLASSIFICATIONS_CREATED": report["deltas"]["question_classifications"],
            "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
            "ALEMBIC_EXECUTION": 0,
            "rows_created_by_this_phase": after["rows_phase11_11"],
            "api_key_in_report": False,
        }
        report["total_elapsed_s"] = round(time.time() - t_start, 1)
        ok = (not report["leak"]
              and report["protected_unchanged"]
              and after["rows_phase11_11"] == 0
              and report["deltas"]["pedagogical_classifications"] == 0
              and report["deltas"]["catalog_nodes"] == 0)
        report["FINAL_DECISION"] = ("PHASE_11_11_CLASSIFICATION_PROPOSALS_COMPLETE" if ok
                                    else "PHASE_11_11_NEEDS_REVIEW")
        return report
    except Abort as exc:
        report["FINAL_DECISION"] = "PHASE_11_11_ABORTED"
        report["abort_reason"] = _scrub(exc)
        return report
    finally:
        await engine.dispose()


def _semantic_flags(key, content, run0):
    """Deterministic semantic audit hooks. Records competing candidates and a
    doubt flag for cases the reviewer must confirm; no LLM call."""
    y, n = key
    ev = run0.get("evidence") or []
    cands = run0.get("candidate_classifications") or []
    competing = [c["content_code"] for c in cands
                 if c.get("content_code") and c["content_code"] != content][:4]
    doubt = False
    why = ""
    if not ev:
        doubt, why = True, "no literal evidence recorded"
    # Q133: expected only if directly supported by the statement
    if (y, n) == (2024, 133) and content == "BIOLOGY-ECOLOGY-COMMUNITIES-SUCCESSION":
        joined = " ".join(e.get("text", "").lower() for e in ev)
        if not any(w in joined for w in ("mimetismo", "similaridade", "predação", "coloração")):
            doubt, why = True, "Q133 mimicry not directly supported by the cited evidence"
    # Q104: relative-velocity item that also depends on the circuit diagram
    if (y, n) == (2024, 104):
        doubt, why = True, "Q104 requires the circuit diagram to solve (visual dependency)"
    return {"central_evidence": ev, "competing_candidates": competing, "doubt": doubt, "why": why}


def main() -> int:
    rep = asyncio.run(run())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    return 0 if rep.get("FINAL_DECISION", "").endswith("COMPLETE") else 1


if __name__ == "__main__":
    sys.exit(main())
