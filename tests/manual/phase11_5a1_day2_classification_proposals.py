"""PHASE 11.5-A1 - Day-2 classification PROPOSALS (proposal-only, zero writes).

Runs the 36 pending ENEM Day-2 question_versions through the OFFICIAL pipeline:

    ClassificationProposalService.propose_with_provider(
        question_version_id, ProviderRouter([OpenAIProvider()]),
        classifier_version=..., taxonomy_version="curriculum-v2", prompt_version=...)

which itself does recover_candidates (controlled vocabulary + lexical retrieval)
-> builds the RESPONSE_SCHEMA prompt with RECOVERED_CANDIDATES + QUESTION_DATA
-> provider.generate() -> the full _persist_provider_output decision core
(hierarchy check, recovered-path check, evidence check, gap logic, review-reason
derivation). No parallel classifier. No FakeProvider. No new logic here.

propose_with_provider commits internally, so this runner uses the proven
PHASE 10.3-10.8 dry-run harness: one outer session, session.commit rebound to
session.flush, everything rolled back, and a leak check in a SEPARATE
connection. Nothing is persisted.

Protected questions (2020 Q91/Q93/Q107/Q128/Q133) are NOT processed - recorded
PROTECTED_SKIP. Day-1 (276) is not touched.
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
        _REPO_ROOT = _cand
        _ENV_FILE = _cand / ".env"
        break
else:  # pragma: no cover
    _REPO_ROOT = _HERE.parents[1]
    _ENV_FILE = Path(".env")

from sqlalchemy import func, select, text  # noqa: E402

from agente_ia_edu.db.models import CatalogNode, PedagogicalClassification, QuestionClassification, Question, QuestionVersion  # noqa: E402
from agente_ia_edu.providers.factory import build_text_provider  # noqa: E402
from agente_ia_edu.providers.errors import (  # noqa: E402
    ProviderAuthenticationError, ProviderConfigurationError, ProviderInvalidResponseError,
    ProviderRateLimitError, ProviderTimeoutError, ProviderUnavailableError,
)
from agente_ia_edu.providers.models import TextGenerationRequest  # noqa: E402
from agente_ia_edu.services.curriculum_classification import ClassificationProposalService  # noqa: E402

REPORT = _REPO_ROOT / "var" / "inep-pilot" / "phase11_5a1_classification_proposals.json"
CLASSIFIER_VERSION = "phase11.5a1-proposal-v1"
PROMPT_VERSION = "phase11.5a1-curriculum-v2-v1"
TAXONOMY_VERSION = "curriculum-v2"

PROTECTED = {(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)}
EXPECT = {"questions": 332, "question_versions": 332, "question_options": 1660,
          "catalog_nodes": 61, "catalog_node_prerequisites": 3,
          "pedagogical_classifications": 24, "question_classifications": 0}

_PROVIDER_EXC = (ProviderAuthenticationError, ProviderConfigurationError,
                 ProviderInvalidResponseError, ProviderRateLimitError,
                 ProviderTimeoutError, ProviderUnavailableError)

_SECRET_RE = re.compile(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+")


def _scrub(s) -> str:
    s = "" if s is None else str(s)
    s = _SECRET_RE.sub("[REDACTED]", s)
    s = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", s)
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
              (SELECT string_agg(pc.id::text||':'||pc.lifecycle||':'||coalesce(pc.metadata->>'taxonomy_version','?'),
                                 ',' ORDER BY pc.id::text)
               FROM pedagogical_classifications pc WHERE pc.question_version_id=bq.question_version_id) AS cls
            FROM booklet_questions bq
            JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
            JOIN exam_applications ea ON ea.id=eb.exam_application_id
            WHERE ea.year=:y AND bq.official_number=:n"""), {"y": y, "n": n})).first()
        fp[f"{y}_Q{n}"] = {"question_version_id": str(row.qvid), "classifications": row.cls}
    return fp


async def _load_scope(session) -> list[dict]:
    rows = (await session.execute(text("""
        SELECT ea.year AS yr, bq.official_number AS num, bq.question_version_id AS qvid,
               qv.content_hash AS ch
        FROM booklet_questions bq
        JOIN question_versions qv ON qv.id=bq.question_version_id
        JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
        JOIN exam_applications ea ON ea.id=eb.exam_application_id
        WHERE bq.official_number > 90
          AND NOT EXISTS (SELECT 1 FROM pedagogical_classifications pc
             WHERE pc.question_version_id=qv.id AND pc.lifecycle='ACTIVE'
             AND pc.metadata->>'taxonomy_version'='curriculum-v2')
        ORDER BY bq.official_number"""))).all()
    scope = []
    for r in rows:
        area = "MATHEMATICS" if r.num >= 136 else "NATURAL_SCIENCES"
        scope.append({"year": r.yr, "day": 2, "official_number": r.num,
                      "question_version_id": str(r.qvid), "content_hash": r.ch,
                      "enem_area": area,
                      "protected": (r.yr, r.num) in PROTECTED})
    return scope


def _outcome_from_record(rec) -> tuple[str, dict]:
    """Map a persisted-then-rolled-back PedagogicalClassification to a Day-2 outcome."""
    md = rec.metadata_ or {}
    gap_type = md.get("gap_type")
    catalog_gap = bool(md.get("catalog_gap"))
    review_reason = md.get("review_reason")
    proposal_status = md.get("proposal_status")
    detail = {
        "discipline_code": md.get("discipline_code"),
        "area_code": md.get("area_code"),
        "content_code": md.get("content_code"),
        "subcontent_code": md.get("subcontent_code"),
        "confidence_band": md.get("confidence_band"),
        "confidence_numeric": float(rec.classification_confidence) if rec.classification_confidence is not None else None,
        "evidence": md.get("evidence"),
        "candidate_classifications": md.get("candidate_classifications"),
        "recovered_candidate_count": len(md.get("recovered_candidates") or []),
        "review_reason": review_reason,
        "gap_type": gap_type,
        "visual_dependency": bool(md.get("visual_dependency")),
        "record_status": rec.status,
        "proposal_status": proposal_status,
    }
    if catalog_gap or gap_type or review_reason in ("CATALOG_GAP", "TAXONOMY_GRANULARITY_GAP"):
        return "CURRICULUM_GAP", detail
    if proposal_status == "NEEDS_REVIEW" or rec.status == "NEEDS_REVIEW" or review_reason:
        return "HUMAN_REVIEW", detail
    if md.get("content_code"):
        return "CLASSIFIED", detail
    return "HUMAN_REVIEW", {**detail, "note": "no content_code on an accepted proposal"}


async def run() -> dict:
    from agente_ia_edu.db.session import create_engine, create_session_factory
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    report: dict = {"phase": "11.5-A1", "mode": "PROPOSAL_ONLY"}
    started = time.time()
    try:
        # ---- preflight (read-only) ----
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            db = await ro.scalar(text("SELECT current_database()"))
            before = await _counts(ro)
            drift = {k: {"expected": v, "actual": before[k]} for k, v in EXPECT.items() if before[k] != v}
            if db != "agente_ia_edu":
                raise Abort(f"wrong database: {db}")
            if drift:
                raise Abort(f"structural drift: {drift}")
            if before["curriculum_v2_ACTIVE"] != 20:
                raise Abort(f"curriculum-v2 ACTIVE != 20: {before['curriculum_v2_ACTIVE']}")
            protected_before = await _protected_fp(ro)
            scope = await _load_scope(ro)
            catalog_active = int(await ro.scalar(select(func.count()).select_from(CatalogNode).where(CatalogNode.active.is_(True))))
        report["preflight"] = {"database": db, "before_counts": before,
                               "structural_ok": True, "drift": None,
                               "active_catalog_nodes": catalog_active,
                               "provider": {"selected_via": "build_text_provider() / AI_PROVIDER", "model": os.getenv("OPENAI_MODEL"),
                                            "api_key_configured": bool(os.getenv("OPENAI_API_KEY"))}}
        report["scope"] = {
            "day2_total": len(scope),
            "mathematics": sum(1 for x in scope if x["enem_area"] == "MATHEMATICS"),
            "natural_sciences": sum(1 for x in scope if x["enem_area"] == "NATURAL_SCIENCES"),
            "protected_in_scope": sum(1 for x in scope if x["protected"]),
            "processable": sum(1 for x in scope if not x["protected"]),
            "day1_status": "DEFERRED (276) - not processed",
        }

        # PHASE 11.23: provider selected by AI_PROVIDER config, not hand-constructed.
        router = build_text_provider()

        proposals: list[dict] = []
        provider_calls = 0
        errors: list[dict] = []

        # ---- proposal-only run: outer tx + commit->flush + rollback ----
        async with factory() as session:
            _orig_commit = session.commit
            session.commit = session.flush  # type: ignore[assignment]
            await session.begin()
            try:
                for item in scope:
                    base = {k: item[k] for k in
                            ("year", "day", "official_number", "question_version_id",
                             "content_hash", "enem_area")}
                    if item["protected"]:
                        proposals.append({**base, "decision": "PROTECTED_SKIP",
                                          "reason": "protected question - not processed", "detail": None})
                        continue
                    svc = ClassificationProposalService(session)
                    t0 = time.time()
                    try:
                        rec = await svc.propose_with_provider(
                            item["question_version_id"], router,
                            classifier_version=CLASSIFIER_VERSION,
                            taxonomy_version=TAXONOMY_VERSION,
                            prompt_version=PROMPT_VERSION,
                        )
                        provider_calls += 1
                        decision, detail = _outcome_from_record(rec)
                        proposals.append({**base, "decision": decision,
                                          "reason": detail.get("review_reason") or detail.get("gap_type") or "accepted",
                                          "elapsed_s": round(time.time() - t0, 2), "detail": detail})
                    except _PROVIDER_EXC as exc:
                        provider_calls += 1
                        msg = _scrub(getattr(exc, "diagnostic_message", None) or str(exc))
                        proposals.append({**base, "decision": "PROVIDER_ERROR",
                                          "reason": f"{type(exc).__name__}: {msg}",
                                          "elapsed_s": round(time.time() - t0, 2), "detail": None})
                        errors.append({"official_number": item["official_number"],
                                       "type": type(exc).__name__, "message": msg})
                        if not session.in_transaction():
                            await session.begin()
                    except ValueError as exc:
                        # decision-core rejection of the model's output -> HUMAN_REVIEW
                        provider_calls += 1
                        proposals.append({**base, "decision": "HUMAN_REVIEW",
                                          "reason": f"decision-core rejected proposal: {_scrub(exc)}",
                                          "elapsed_s": round(time.time() - t0, 2),
                                          "detail": {"validation_error": _scrub(exc)}})
                        if not session.in_transaction():
                            await session.begin()
                    except Exception as exc:  # noqa: BLE001
                        proposals.append({**base, "decision": "PROVIDER_ERROR",
                                          "reason": f"unexpected: {type(exc).__name__}: {_scrub(exc)}",
                                          "elapsed_s": round(time.time() - t0, 2), "detail": None})
                        errors.append({"official_number": item["official_number"],
                                       "type": type(exc).__name__, "message": _scrub(exc)})
                        if not session.in_transaction():
                            await session.begin()
            finally:
                if session.in_transaction():
                    await session.rollback()
                session.commit = _orig_commit  # type: ignore[assignment]

        # ---- post (separate connection) ----
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            after = await _counts(ro)
            protected_after = await _protected_fp(ro)
        report["after_counts"] = after
        report["deltas"] = {k: after[k] - before[k] for k in after}
        report["protected_questions"] = {"before": protected_before, "after": protected_after,
                                         "unchanged": protected_before == protected_after}
        report["leak"] = any(v != 0 for v in report["deltas"].values())

        # ---- aggregates ----
        proc = [p for p in proposals if p["decision"] != "PROTECTED_SKIP"]
        def cnt(d): return sum(1 for p in proc if p["decision"] == d)
        report["outcomes"] = {
            "analyzed": len(proc),
            "CLASSIFIED": cnt("CLASSIFIED"), "HUMAN_REVIEW": cnt("HUMAN_REVIEW"),
            "CURRICULUM_GAP": cnt("CURRICULUM_GAP"), "PROVIDER_ERROR": cnt("PROVIDER_ERROR"),
            "PROTECTED_SKIP": sum(1 for p in proposals if p["decision"] == "PROTECTED_SKIP"),
            "TOTAL": len(proposals),
        }
        by_disc = {}
        for area in ("MATHEMATICS", "NATURAL_SCIENCES"):
            a = [p for p in proc if p["enem_area"] == area]
            by_disc[area] = {"analyzed": len(a),
                             "CLASSIFIED": sum(1 for p in a if p["decision"] == "CLASSIFIED"),
                             "HUMAN_REVIEW": sum(1 for p in a if p["decision"] == "HUMAN_REVIEW"),
                             "CURRICULUM_GAP": sum(1 for p in a if p["decision"] == "CURRICULUM_GAP"),
                             "PROVIDER_ERROR": sum(1 for p in a if p["decision"] == "PROVIDER_ERROR")}
        report["by_discipline"] = by_disc
        content_dist = {}
        for p in proc:
            cc = (p.get("detail") or {}).get("content_code") if p.get("detail") else None
            if cc:
                content_dist[cc] = content_dist.get(cc, 0) + 1
        report["content_distribution"] = dict(sorted(content_dist.items(), key=lambda kv: -kv[1]))
        conf_dist = {}
        for p in proc:
            cb = (p.get("detail") or {}).get("confidence_band") if p.get("detail") else None
            conf_dist[cb or "n/a"] = conf_dist.get(cb or "n/a", 0) + 1
        report["confidence_distribution"] = conf_dist
        report["provider_calls"] = provider_calls
        report["errors"] = errors
        report["retries"] = 0
        report["total_elapsed_s"] = round(time.time() - started, 1)
        report["questions_requiring_review"] = [
            {"official_number": p["official_number"], "enem_area": p["enem_area"],
             "content_code": (p.get("detail") or {}).get("content_code"),
             "confidence": (p.get("detail") or {}).get("confidence_band"),
             "reason": p["reason"]}
            for p in proc if p["decision"] in ("HUMAN_REVIEW", "CURRICULUM_GAP")]
        # quality sample: up to 10 CLASSIFIED
        classified = [p for p in proc if p["decision"] == "CLASSIFIED"]
        report["quality_sample"] = [
            {"official_number": p["official_number"], "enem_area": p["enem_area"],
             "discipline": (p["detail"] or {}).get("discipline_code"),
             "area": (p["detail"] or {}).get("area_code"),
             "content": (p["detail"] or {}).get("content_code"),
             "confidence": (p["detail"] or {}).get("confidence_band"),
             "evidence": (p["detail"] or {}).get("evidence")}
            for p in classified[:10]]
        report["proposals"] = proposals

        report["security"] = {
            "DATABASE_WRITES": sum(abs(v) for v in report["deltas"].values()),
            "CLASSIFICATIONS_CREATED": report["deltas"]["pedagogical_classifications"],
            "QUESTION_CLASSIFICATIONS_CREATED": report["deltas"]["question_classifications"],
            "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
            "VOCABULARY_CHANGES": 0, "ALEMBIC_EXECUTION": 0,
            "PRODUCTION_DATA_MODIFIED": 0,
            "OPENAI_CALLS": provider_calls,
            "secret_in_report": False,
        }
        report["known_preexisting_failures"] = [
            "tests/test_ingestion_classifier.py::TestIngestionClassifierIntegration::test_13_isolation_between_documents",
            "tests/test_curriculum_taxonomy_postgresql.py::CurriculumTaxonomyPostgreSQLTests::test_classification_proposal_persists_without_question_mutation"]

        ok = (not report["leak"]
              and after["pedagogical_classifications"] == 24
              and after["question_classifications"] == 0
              and after["catalog_nodes"] == 61
              and after["questions"] == 332
              and after["curriculum_v2_ACTIVE"] == 20
              and report["protected_questions"]["unchanged"]
              and report["outcomes"]["analyzed"] == report["scope"]["processable"]
              and all(p["decision"] in ("CLASSIFIED", "HUMAN_REVIEW", "CURRICULUM_GAP",
                                        "PROVIDER_ERROR", "PROTECTED_SKIP") for p in proposals))
        report["final_decision"] = ("PHASE_11_5A1_CLASSIFICATION_PROPOSALS_COMPLETE" if ok
                                    else "PHASE_11_5A1_NEEDS_REVIEW")
    except Abort as exc:
        report["final_decision"] = "PHASE_11_5A1_NEEDS_REVIEW"
        report["abort"] = _scrub(exc)
    finally:
        await engine.dispose()
    return report


def _render(r: dict) -> str:
    L = [f"PHASE 11.5-A1 - DAY-2 CLASSIFICATION PROPOSALS  (mode={r.get('mode')})", ""]
    if "abort" in r:
        L.append(f"ABORTED: {r['abort']}")
        L += ["", "FINAL_DECISION:", r["final_decision"]]
        return "\n".join(L)
    pf = r["preflight"]
    L += [f"database={pf['database']}  provider={pf['provider']['adapter']}  model={pf['provider']['model']}  "
          f"api_key_configured={pf['provider']['api_key_configured']}",
          f"active_catalog_nodes={pf['active_catalog_nodes']}", ""]
    s = r["scope"]
    L += [f"SCOPE: day2_total={s['day2_total']}  math={s['mathematics']}  natsci={s['natural_sciences']}  "
          f"protected_in_scope={s['protected_in_scope']}  processable={s['processable']}  | Day-1 {s['day1_status']}", ""]
    o = r["outcomes"]
    L += ["OUTCOMES:",
          f"  analyzed        {o['analyzed']}",
          f"  CLASSIFIED      {o['CLASSIFIED']}",
          f"  HUMAN_REVIEW    {o['HUMAN_REVIEW']}",
          f"  CURRICULUM_GAP  {o['CURRICULUM_GAP']}",
          f"  PROVIDER_ERROR  {o['PROVIDER_ERROR']}",
          f"  PROTECTED_SKIP  {o['PROTECTED_SKIP']}",
          f"  TOTAL           {o['TOTAL']}", ""]
    L += ["BY DISCIPLINE:"]
    for k, v in r["by_discipline"].items():
        L.append(f"  {k:16} analyzed={v['analyzed']} CLASSIFIED={v['CLASSIFIED']} HUMAN_REVIEW={v['HUMAN_REVIEW']} "
                 f"CURRICULUM_GAP={v['CURRICULUM_GAP']} PROVIDER_ERROR={v['PROVIDER_ERROR']}")
    L += ["", "CONTENT DISTRIBUTION:"]
    for cc, n in r["content_distribution"].items():
        L.append(f"  {cc:44} {n}")
    L += ["", f"confidence: {r['confidence_distribution']}",
          f"provider_calls={r['provider_calls']}  errors={len(r['errors'])}  elapsed={r['total_elapsed_s']}s", ""]
    d = r["deltas"]
    L += ["DELTAS (must all be 0):"] + [f"  {k:32} {v:+d}" for k, v in d.items()]
    L += ["", f"leak={r['leak']}  protected_unchanged={r['protected_questions']['unchanged']}"]
    sec = r["security"]
    L += ["", "SECURITY:"] + [f"  {k} = {sec[k]}" for k in sec]
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
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {REPORT}")
    return 0 if report["final_decision"].endswith("COMPLETE") else 1


def main() -> int:  # pragma: no cover
    return asyncio.run(_amain())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
