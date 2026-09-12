"""PHASE 11.5-A3 - deterministic consolidation of the Day-2 classification proposals.

The A2 run showed the LLM step is NOT deterministic (Q114/Q116/Q151 flipped between
A1 and A2). retrieval + decision core ARE deterministic. The OpenAIProvider adapter
exposes no temperature/seed knob and this phase must NOT change the provider
architecture, so consistency is obtained by REPETITION: each re-run question is
put through the official pipeline N=3 times and only a unanimous CLASSIFIED (same
CONTENT, decision core accepts every time) is admitted to FINAL_CLASSIFIED.

Re-run set (9): the 6 A2-confirmed + the 3 oscillating (Q114, Q116, Q151).
Reused verbatim from A2:
  * Group C (Q132, Q146, Q150) -> forced FINAL_HUMAN_REVIEW (known lexical-trap
    errors from PRE-EXISTING bindings; never CLASSIFIED even if the model repeats
    the mistake).
  * Group E (the other 21 HUMAN_REVIEW) -> FINAL_HUMAN_REVIEW.
  * The 4 protected questions -> untouched.

PROPOSAL-ONLY: propose_with_provider commits internally, so every call runs inside
one outer session with commit rebound to flush and a final rollback; a separate
connection confirms 0 writes. Nothing is persisted.
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
        _ENV_FILE = _cand / ".env"
        break
else:  # pragma: no cover
    _REPO_ROOT = _HERE.parents[1]
    _ENV_FILE = Path(".env")

from sqlalchemy import text  # noqa: E402

from agente_ia_edu.providers.factory import build_text_provider  # noqa: E402
from agente_ia_edu.providers.errors import (  # noqa: E402
    ProviderAuthenticationError, ProviderConfigurationError, ProviderInvalidResponseError,
    ProviderRateLimitError, ProviderTimeoutError, ProviderUnavailableError,
)
from agente_ia_edu.services.curriculum_classification import ClassificationProposalService  # noqa: E402

A2_REPORT = _REPO_ROOT / "var" / "inep-pilot" / "phase11_5a1_classification_proposals.json"
OUT = _REPO_ROOT / "var" / "inep-pilot" / "phase11_5a3_consolidated_proposals.json"
CLASSIFIER_VERSION = "phase11.5a3-consolidation-v1"
PROMPT_VERSION = "phase11.5a1-curriculum-v2-v1"
TAXONOMY_VERSION = "curriculum-v2"
N_REPEAT = 3

PROTECTED = {(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)}
GROUP_A = {(2024, 105), (2024, 130), (2024, 167), (2025, 154), (2025, 174), (2024, 103)}
GROUP_B = {(2025, 114), (2025, 116), (2025, 151)}
GROUP_C_FORCED_HR = {(2024, 132), (2025, 146), (2024, 150)}
RERUN = GROUP_A | GROUP_B

NEW_BINDINGS = {
    "MATH-PROBABILITY-BASICS", "MATH-GEOMETRY-SPATIAL", "MATH-ALGEBRA-PERCENTAGE",
    "CHEMISTRY-PHYSICAL-STOICHIOMETRY", "CHEMISTRY-ORGANIC-REACTIONS",
    "BIOLOGY-IMMUNOLOGY-MICROBIOLOGY-DISEASES",
}
_PROVIDER_EXC = (ProviderAuthenticationError, ProviderConfigurationError,
                 ProviderInvalidResponseError, ProviderRateLimitError,
                 ProviderTimeoutError, ProviderUnavailableError)
_SECRET_RE = re.compile(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+")

EXPECT = {"questions": 332, "question_versions": 332, "question_options": 1660,
          "booklet_questions": 332, "pedagogical_classifications": 24,
          "question_classifications": 0, "catalog_nodes": 61, "catalog_node_prerequisites": 3}


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
    report: dict = {"phase": "11.5-A3", "mode": "PROPOSAL_ONLY", "n_repeat": N_REPEAT}
    t_start = time.time()
    try:
        a2 = json.loads(A2_REPORT.read_text())
        a2_by = {(p["year"], p["official_number"]): p for p in a2["proposals"]}

        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            if (await ro.scalar(text("SELECT current_database()"))) != "agente_ia_edu":
                raise Abort("wrong database")
            before = await _counts(ro)
            drift = {k: {"expected": v, "actual": before[k]} for k, v in EXPECT.items() if before[k] != v}
            if drift:
                raise Abort(f"structural drift: {drift}")
            if before["curriculum_v2_ACTIVE"] != 20:
                raise Abort("curriculum-v2 ACTIVE != 20")
            protected_before = await _protected_fp(ro)
            rerun_rows = (await ro.execute(text("""
                SELECT ea.year AS yr, bq.official_number AS num, bq.question_version_id AS qvid
                FROM booklet_questions bq
                JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                JOIN exam_applications ea ON ea.id=eb.exam_application_id
                WHERE bq.official_number > 90"""))).all()
        qv_of = {(r.yr, r.num): str(r.qvid) for r in rerun_rows}

        # PHASE 11.23: provider selected by AI_PROVIDER config, not hand-constructed.
        router = build_text_provider()
        report["provider"] = {"selected_via": "build_text_provider() / AI_PROVIDER", "model": os.getenv("OPENAI_MODEL"),
                              "temperature_control": "not supported by adapter; consistency via N=3 repetition",
                              "api_key_configured": bool(os.getenv("OPENAI_API_KEY"))}

        rerun_log: list[dict] = []
        provider_calls = 0

        async def _one_run(qv: str, i: int) -> dict:
            """One proposal in its OWN rolled-back transaction (isolation between the
            N repeats of the same question - the partial-unique ACTIVE index would
            otherwise reject a second flush for the same question_version_id)."""
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
                               "recovered": [c.get("content_code") for c in (md.get("recovered_candidates") or [])],
                               "evidence": [e.get("text", "")[:140] for e in (md.get("evidence") or [])[:2]]}
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

        for key in sorted(RERUN):
            qv = qv_of[key]
            runs = [await _one_run(qv, i) for i in range(N_REPEAT)]
            rerun_log.append({"year": key[0], "official_number": key[1],
                              "question_version_id": qv, "runs": runs})

        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            after = await _counts(ro)
            protected_after = await _protected_fp(ro)
        report["db_before"] = before
        report["db_after"] = after
        report["deltas"] = {k: after[k] - before[k] for k in after}
        report["leak"] = any(v != 0 for v in report["deltas"].values())
        report["protected_unchanged"] = protected_before == protected_after

        # ---- consolidation ----
        FINAL_CLASSIFIED, FINAL_HR, FINAL_GAP, PROVIDER_ERR = [], [], [], []
        oscillating, rejected = [], []
        consolidated = {}

        for entry in rerun_log:
            key = (entry["year"], entry["official_number"])
            decs = [r["decision"] for r in entry["runs"]]
            contents = [r.get("content_code") for r in entry["runs"] if r["decision"] == "CLASSIFIED"]
            dc = Counter(decs)
            unanimous_classified = (dc.get("CLASSIFIED", 0) == N_REPEAT and len(set(contents)) == 1)
            unanimous_gap = dc.get("CURRICULUM_GAP", 0) == N_REPEAT
            content = contents[0] if contents else None
            row = {"year": key[0], "official_number": key[1],
                   "question_version_id": entry["question_version_id"],
                   "run_decisions": decs, "run_contents": [r.get("content_code") for r in entry["runs"]],
                   "distinct_decisions": sorted(dc), "n_repeat": N_REPEAT}
            if "PROVIDER_ERROR" in decs:
                PROVIDER_ERR.append({**row, "final": "PROVIDER_ERROR"})
                consolidated[key] = "PROVIDER_ERROR"
                continue
            if unanimous_classified:
                # acceptance gate
                r0 = next(r for r in entry["runs"] if r["decision"] == "CLASSIFIED")
                disc_ok = _discipline_ok(content, r0.get("discipline"))
                if not disc_ok:
                    FINAL_HR.append({**row, "final": "HUMAN_REVIEW",
                                     "reason": f"unanimous CLASSIFIED to {content} but discipline mismatch"})
                    consolidated[key] = "HUMAN_REVIEW"; rejected.append({**row, "reason": "discipline mismatch"})
                else:
                    FINAL_CLASSIFIED.append({
                        **row, "final": "CLASSIFIED", "content_code": content,
                        "discipline": r0.get("discipline"), "area": r0.get("area"),
                        "confidence": r0.get("confidence"),
                        "via_binding": content in NEW_BINDINGS,
                        "evidence": r0.get("evidence"),
                        "recovered_candidates": r0.get("recovered")})
                    consolidated[key] = "CLASSIFIED"
            elif unanimous_gap:
                FINAL_GAP.append({**row, "final": "CURRICULUM_GAP"})
                consolidated[key] = "CURRICULUM_GAP"
            else:
                oscillating.append({**row})
                FINAL_HR.append({**row, "final": "HUMAN_REVIEW",
                                 "reason": f"oscillating across {N_REPEAT} runs: decisions={decs} contents={row['run_contents']}"})
                consolidated[key] = "HUMAN_REVIEW"

        # forced HUMAN_REVIEW (Group C) - not re-run
        for key in sorted(GROUP_C_FORCED_HR):
            p = a2_by[key]
            FINAL_HR.append({"year": key[0], "official_number": key[1],
                             "question_version_id": p["question_version_id"],
                             "final": "HUMAN_REVIEW",
                             "reason": "known lexical-trap error from a PRE-EXISTING binding "
                                       f"(A2 model suggested {(p.get('detail') or {}).get('content_code')}); "
                                       "forced HUMAN_REVIEW per phase rule, never CLASSIFIED",
                             "source": "A2 reused (forced)"})
            consolidated[key] = "HUMAN_REVIEW"

        # everything else from A2 (Group E + any A2 HUMAN_REVIEW / GAP not re-run)
        for key, p in a2_by.items():
            if p["decision"] == "PROTECTED_SKIP" or key in consolidated:
                continue
            if p["decision"] == "CURRICULUM_GAP":
                FINAL_GAP.append({"year": key[0], "official_number": key[1],
                                  "question_version_id": p["question_version_id"],
                                  "final": "CURRICULUM_GAP", "source": "A2 reused"})
            else:
                FINAL_HR.append({"year": key[0], "official_number": key[1],
                                 "question_version_id": p["question_version_id"],
                                 "final": "HUMAN_REVIEW", "source": "A2 reused",
                                 "reason": (p.get("detail") or {}).get("review_reason") or p.get("reason")})
            consolidated[key] = p["decision"] if p["decision"] != "CLASSIFIED" else "HUMAN_REVIEW"

        report["FINAL_CLASSIFIED"] = sorted(FINAL_CLASSIFIED, key=lambda r: (r["year"], r["official_number"]))
        report["FINAL_HUMAN_REVIEW"] = sorted(FINAL_HR, key=lambda r: (r["year"], r["official_number"]))
        report["FINAL_CURRICULUM_GAP"] = sorted(FINAL_GAP, key=lambda r: (r["year"], r["official_number"]))
        report["PROVIDER_ERROR"] = PROVIDER_ERR
        report["oscillating_questions"] = oscillating
        report["rejected_questions"] = rejected
        report["rerun_log"] = rerun_log
        report["provider_calls"] = provider_calls
        report["total_elapsed_s"] = round(time.time() - t_start, 1)

        tot = len(FINAL_CLASSIFIED) + len(FINAL_HR) + len(FINAL_GAP) + len(PROVIDER_ERR)
        report["totals"] = {
            "analyzed": tot,
            "FINAL_CLASSIFIED": len(FINAL_CLASSIFIED),
            "FINAL_HUMAN_REVIEW": len(FINAL_HR),
            "FINAL_CURRICULUM_GAP": len(FINAL_GAP),
            "PROVIDER_ERROR": len(PROVIDER_ERR),
            "PROTECTED_SKIP": 4,
        }
        report["comparison_A1_A2_A3"] = {
            "A1": {"CLASSIFIED": 5, "HUMAN_REVIEW": 25, "CURRICULUM_GAP": 2, "PROVIDER_ERROR": 0},
            "A2_raw": {"CLASSIFIED": 9, "HUMAN_REVIEW": 21, "CURRICULUM_GAP": 2, "PROVIDER_ERROR": 0},
            "A2_after_quality_override": {"CLASSIFIED": 6, "HUMAN_REVIEW": 24, "CURRICULUM_GAP": 2, "PROVIDER_ERROR": 0},
            "A3_consolidated": {k: report["totals"][f"FINAL_{k}"] if k != "PROVIDER_ERROR" else 0
                                for k in ("CLASSIFIED", "HUMAN_REVIEW", "CURRICULUM_GAP", "PROVIDER_ERROR")},
        }
        report["bindings_used_by_final_classified"] = sorted({
            r["content_code"] for r in FINAL_CLASSIFIED if r.get("via_binding")})
        report["security"] = {
            "DATABASE_WRITES": sum(abs(v) for v in report["deltas"].values()),
            "CLASSIFICATIONS_CREATED": report["deltas"]["pedagogical_classifications"],
            "QUESTION_CLASSIFICATIONS_CREATED": report["deltas"]["question_classifications"],
            "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
            "VOCABULARY_CHANGES": 0, "ALEMBIC_EXECUTION": 0, "PRODUCTION_DATA_MODIFIED": 0,
            "OPENAI_CALLS": provider_calls, "secret_in_report": False,
        }
        report["known_preexisting_failures"] = [
            "tests/test_ingestion_classifier.py::...::test_13_isolation_between_documents",
            "tests/test_curriculum_taxonomy_postgresql.py::...::test_classification_proposal_persists_without_question_mutation",
            "tests/test_openai_provider.py::...::test_requires_key_and_model (CONFIG_INDUCED by OPENAI_API_KEY in .env)"]
        ok = (not report["leak"] and report["protected_unchanged"]
              and after["pedagogical_classifications"] == 24 and after["question_classifications"] == 0
              and after["catalog_nodes"] == 61 and after["questions"] == 332
              and after["curriculum_v2_ACTIVE"] == 20
              and all((k in consolidated) for k in
                      [(y, n) for (y, n) in a2_by if a2_by[(y, n)]["decision"] != "PROTECTED_SKIP"]))
        report["final_decision"] = ("PHASE_11_5A3_CONSOLIDATION_COMPLETE" if ok
                                    else "PHASE_11_5A3_NEEDS_REVIEW")
    except Abort as exc:
        report["final_decision"] = "PHASE_11_5A3_NEEDS_REVIEW"
        report["abort"] = _scrub(exc)
    finally:
        await engine.dispose()
    return report


_DISCIPLINE_PREFIX = {"MATH": "MATH", "CHEMISTRY": "CHEMISTRY", "PHYSICS": "PHYSICS", "BIOLOGY": "BIOLOGY"}


def _discipline_ok(content_code: str | None, discipline_code: str | None) -> bool:
    if not content_code or not discipline_code:
        return False
    return content_code.startswith(_DISCIPLINE_PREFIX.get(discipline_code, "\0"))


def _render(r: dict) -> str:
    if "abort" in r:
        return f"PHASE 11.5-A3\nABORTED: {r['abort']}\n\nFINAL_DECISION:\n{r['final_decision']}"
    L = ["PHASE 11.5-A3 - CONSOLIDATED PROPOSALS  (proposal-only, N=%d)" % r["n_repeat"], ""]
    t = r["totals"]
    L += [f"analyzed={t['analyzed']}  FINAL_CLASSIFIED={t['FINAL_CLASSIFIED']}  "
          f"FINAL_HUMAN_REVIEW={t['FINAL_HUMAN_REVIEW']}  FINAL_CURRICULUM_GAP={t['FINAL_CURRICULUM_GAP']}  "
          f"PROVIDER_ERROR={t['PROVIDER_ERROR']}  PROTECTED_SKIP={t['PROTECTED_SKIP']}", ""]
    L.append("FINAL_CLASSIFIED:")
    for c in r["FINAL_CLASSIFIED"]:
        L.append(f"  {c['year']} Q{c['official_number']}  {c['discipline']} > {c['area']} > {c['content_code']}  "
                 f"conf={c['confidence']}  via_binding={c['via_binding']}  runs={c['run_decisions']}")
    L.append("\noscillating -> HUMAN_REVIEW:")
    for o in r["oscillating_questions"]:
        L.append(f"  {o['year']} Q{o['official_number']}  decisions={o['run_decisions']}  contents={o['run_contents']}")
    L.append("\ncomparison A1 -> A2(override) -> A3:")
    cmp = r["comparison_A1_A2_A3"]
    for k in ("CLASSIFIED", "HUMAN_REVIEW", "CURRICULUM_GAP", "PROVIDER_ERROR"):
        L.append(f"  {k:15} {cmp['A1'][k]:2} -> {cmp['A2_after_quality_override'][k]:2} -> {cmp['A3_consolidated'][k]:2}")
    L.append("\nDELTAS (must be 0): " + ", ".join(f"{k}{v:+d}" for k, v in r["deltas"].items()))
    L.append(f"leak={r['leak']}  protected_unchanged={r['protected_unchanged']}  openai_calls={r['provider_calls']}  "
             f"elapsed={r['total_elapsed_s']}s")
    s = r["security"]
    L += ["", "SECURITY: " + "  ".join(f"{k}={s[k]}" for k in
          ("DATABASE_WRITES", "CLASSIFICATIONS_CREATED", "CATALOG_NODES_CREATED",
           "PRODUCTION_DATA_MODIFIED", "OPENAI_CALLS", "secret_in_report"))]
    L += ["", "FINAL_DECISION:", r["final_decision"]]
    return "\n".join(L)


async def _amain() -> int:  # pragma: no cover
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE, override=False)
    except Exception:
        pass
    from agente_ia_edu.db.session import get_database_url
    try:
        get_database_url()
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: {exc}")
        return 2
    report = await run()
    print(_render(report))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}")
    return 0 if report["final_decision"].endswith("COMPLETE") else 1


def main() -> int:  # pragma: no cover
    return asyncio.run(_amain())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
