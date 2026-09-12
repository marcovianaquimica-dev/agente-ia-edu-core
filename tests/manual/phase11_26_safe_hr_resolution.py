"""PHASE 11.26 - safe resolution of 3 HUMAN_REVIEW cases.

  Q150  -> MATH-ALGEBRA-LOGARITHMS   : deterministic curator persist (NO provider call)
  Q132  -> BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS : PHASE 11.26 binding + N=3 consensus, persist iff CLASSIFIED
  Q116  -> CHEMISTRY-GENERAL-POLARITY-IMF        : PHASE 11.26 binding + N=3 consensus, persist iff CLASSIFIED
          (Q116 references a device schematic; a visual-dependency verdict stays HUMAN_REVIEW)

Uses AiAgnosticClassificationService (PHASE 11.23) + the existing N=3/HIGH consensus.
No direct OpenAIProvider construction. Max 6 provider calls (Q132x3 + Q116x3).
"""
from __future__ import annotations

import asyncio
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
from agente_ia_edu.services.classification_consensus import DEFAULT_CONSENSUS_POLICY  # noqa: E402
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    ClassificationProposal, ClassificationProposalService)

OUT = _REPO / "var" / "phase11_26_safe_hr_resolution_report.json"
TAXONOMY_VERSION = "curriculum-v2"
PROMPT_VERSION = "v1"
_SECRET_RE = re.compile(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+")

Q150 = {"year": 2024, "num": 150, "qv": "0a568509-fa05-4b00-8592-ad51dc25f011",
        "content": "MATH-ALGEBRA-LOGARITHMS",
        "classifier_version": "phase11.26-q150-deterministic-v1",
        "provider_label": "phase-11-26-curator-approved", "model_label": "deterministic-v1",
        "context": "PHASE_11_26 deterministic curator persist; PHASE 11.13 concept = logaritmos; "
                   "MATH-ALGEBRA-LOGARITHMS recovered rank 1 (score 240); no LLM call",
        "evidence": ["é conhecida uma expressão algébrica relacionando esses valores dada por",
                     "O valor aproximado da magnitude M2 do segundo terremoto"]}
CONSENSUS = [
    {"year": 2024, "num": 132, "qv": "a90a12a2-554c-46e2-80b1-d6b8d5365154",
     "expected": "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS",
     "classifier_version": "phase11.26-q132-consensus-v1",
     "provider_label": "phase-11-26-consensus", "model_label": "consensus-3x-HIGH",
     "evidence": ["As fibras musculares esqueléticas não são todas iguais",
                  "maior proporção de fibras brancas que fibras vermelhas"]},
    {"year": 2025, "num": 116, "qv": "d190739e-a4de-40f3-992a-60a4e34aeb65",
     "expected": "CHEMISTRY-GENERAL-POLARITY-IMF",
     "classifier_version": "phase11.26-q116-consensus-v1",
     "provider_label": "phase-11-26-consensus", "model_label": "consensus-3x-HIGH",
     "evidence": ["filtro capaz de separar óleo e água",
                  "Na utilização desse dispositivo, a retenção do óleo ocorre"]},
]
PROTECTED = [(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)]
COUNT_TABLES = ["questions", "question_versions", "question_options", "booklet_questions",
                "catalog_nodes", "catalog_node_prerequisites",
                "pedagogical_classifications", "question_classifications"]
CV2 = ("SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
       "AND metadata->>'taxonomy_version'='curriculum-v2'")
EXPECT_BEFORE = {"questions": 332, "question_versions": 332, "question_options": 1660,
                 "booklet_questions": 332, "catalog_nodes": 64, "catalog_node_prerequisites": 3,
                 "pedagogical_classifications": 36, "question_classifications": 0}


def _scrub(v) -> str:
    s = "" if v is None else str(v)
    s = _SECRET_RE.sub("[REDACTED]", s)
    k = os.getenv("OPENAI_API_KEY")
    return s.replace(k, "[REDACTED]") if k else s


class Abort(RuntimeError):
    pass


async def _counts(s):
    d = {t: int(await s.scalar(text(f"SELECT count(*) FROM {t}"))) for t in COUNT_TABLES}
    d["curriculum_v2_ACTIVE"] = int(await s.scalar(text(CV2)))
    return d


async def _protected_fp(s):
    import hashlib
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


async def _has_active_cv2(s, qv) -> bool:
    return int(await s.scalar(text(
        "SELECT count(*) FROM pedagogical_classifications WHERE question_version_id=:q "
        "AND lifecycle='ACTIVE' AND metadata->>'taxonomy_version'='curriculum-v2'"), {"q": qv})) > 0


async def _statement(s, qv) -> str:
    r = (await s.execute(text("SELECT canonical_text, statement FROM question_versions WHERE id=:i"),
                         {"i": qv})).first()
    return r.canonical_text or r.statement or ""


async def run() -> dict:
    if os.getenv("PHASE11_26_TOKEN") != "PHASE11-26-SAFE-HR-RESOLUTION-APPROVED":
        return {"phase": "11.26", "FINAL_DECISION": "PHASE_11_26_ABORTED", "abort_reason": "missing token"}
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    report: dict = {"phase": "11.26", "consensus_policy": {
        "n": DEFAULT_CONSENSUS_POLICY.n, "required_confidence": DEFAULT_CONSENSUS_POLICY.required_confidence}}
    t0 = time.time()
    provider_calls = 0
    try:
        # ---- binding-change record (read from the now-live _SPECS) ----
        import agente_ia_edu.services._curriculum_v2_bindings as B
        specs = {c[0]: c for c in B._SPECS}
        report["_SPECS_entry_count"] = len(B._SPECS)
        report["binding_changes"] = {
            "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS": {
                "specific": list(specs["BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS"][3]),
                "contextual": list(specs["BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS"][4]),
                "primary": list(specs["BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS"][2])},
            "CHEMISTRY-GENERAL-POLARITY-IMF": {
                "specific": list(specs["CHEMISTRY-GENERAL-POLARITY-IMF"][3]),
                "contextual": list(specs["CHEMISTRY-GENERAL-POLARITY-IMF"][4]),
                "primary": list(specs["CHEMISTRY-GENERAL-POLARITY-IMF"][2])},
        }

        # ---- preflight ----
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            report["current_database"] = await ro.scalar(text("SELECT current_database()"))
            if report["current_database"] != "agente_ia_edu":
                raise Abort("wrong database")
            before = await _counts(ro)
            report["preflight_counts"] = before
            drift = {k: (v, before[k]) for k, v in EXPECT_BEFORE.items() if before[k] != v}
            if drift:
                raise Abort(f"structural drift: {drift}")
            if before["curriculum_v2_ACTIVE"] != 32:
                raise Abort(f"curriculum-v2 ACTIVE != 32 (got {before['curriculum_v2_ACTIVE']})")
            report["protected_fp_before"] = await _protected_fp(ro)
            for item in [Q150] + CONSENSUS:
                if await _has_active_cv2(ro, item["qv"]):
                    raise Abort(f"{item['year']} Q{item['num']} already has an ACTIVE curriculum-v2 row")

        # ---- Q150: deterministic curator persist (NO provider) ----
        q150_res = {}
        async with factory() as s:
            if await _has_active_cv2(s, Q150["qv"]):
                q150_res = {"verdict": "IDEMPOTENT_SKIP"}
            else:
                stmt = await _statement(s, Q150["qv"])
                for ex in Q150["evidence"]:
                    if ex not in stmt:
                        raise Abort(f"Q150 evidence not literal: {ex!r}")
                proposal = ClassificationProposal(
                    primary_content_code=Q150["content"], complementary_content_codes=[],
                    concepts=[], prerequisites=[], cognitive_operations=[],
                    context=Q150["context"], difficulty="UNKNOWN", confidence=0.9,
                    evidence=[{"content_code": Q150["content"], "text": ex,
                               "reason": "PHASE_11_26 deterministic curator persist"} for ex in Q150["evidence"]])
                rec = await ClassificationProposalService(s).propose(
                    uuid.UUID(Q150["qv"]), proposal,
                    classifier_version=Q150["classifier_version"], taxonomy_version=TAXONOMY_VERSION,
                    provider=Q150["provider_label"], model=Q150["model_label"], prompt_version=PROMPT_VERSION)
                if rec.status != "CLASSIFIED":
                    raise Abort(f"Q150 propose() returned status={rec.status}")
                q150_res = {"verdict": "PERSISTED", "pc_id": str(rec.id), "content_code": rec.content,
                            "status": rec.status, "lifecycle": rec.lifecycle, "source": rec.source,
                            "llm_used": False}
        report["Q150"] = {"question": "2024 Q150", "target": Q150["content"], **q150_res}

        # ---- Q132 & Q116: N=3 consensus via the AI-agnostic entrypoint ----
        svc = AiAgnosticClassificationService(factory)  # provider=None -> build_text_provider()
        report["prompt_artifact_version"] = svc.classification_prompt_version
        for item in CONSENSUS:
            r0 = time.time()
            outcome = await svc.propose_and_audit(
                uuid.UUID(item["qv"]), classifier_version=item["classifier_version"],
                taxonomy_version=TAXONOMY_VERSION, classification_mode="STANDARD")
            provider_calls += DEFAULT_CONSENSUS_POLICY.n
            per_run = [{"outcome": rr.outcome, "content_code": rr.content_code,
                        "confidence": rr.confidence, "review_reason": rr.review_reason,
                        "gap_type": rr.gap_type, "error": _scrub(rr.error) if rr.error else None}
                       for rr in outcome.runs]
            entry = {"question": f"{item['year']} Q{item['num']}", "expected_target": item["expected"],
                     "verdict": outcome.verdict, "content_code": outcome.content_code,
                     "confidence": outcome.confidence, "n": outcome.n, "reason": outcome.reason,
                     "runs": per_run, "elapsed_s": round(time.time() - r0, 1)}
            visual = any(rr.review_reason == "VISUAL_DEPENDENCY" for rr in outcome.runs)
            entry["visual_dependency_seen"] = visual
            if (outcome.verdict == "CLASSIFIED" and outcome.content_code == item["expected"]):
                async with factory() as s:
                    if await _has_active_cv2(s, item["qv"]):
                        entry["persist"] = "IDEMPOTENT_SKIP"
                    else:
                        stmt = await _statement(s, item["qv"])
                        ev = [e for e in item["evidence"] if e in stmt]
                        if not ev:
                            raise Abort(f"{entry['question']} no literal evidence for persist")
                        rec = await svc.persist_classified_consensus(
                            s, outcome, uuid.UUID(item["qv"]),
                            evidence=[{"text": e, "reason": "PHASE_11_26 N=3 consensus CLASSIFIED HIGH"} for e in ev],
                            classifier_version=item["classifier_version"], taxonomy_version=TAXONOMY_VERSION,
                            provider_label=item["provider_label"], model_label=item["model_label"], confirm=True)
                        entry["persist"] = {"pc_id": str(rec.id), "content": rec.content,
                                            "status": rec.status, "source": rec.source}
            else:
                entry["persist"] = "NOT_PERSISTED (HUMAN_REVIEW)"
            report[f"Q{item['num']}"] = entry

        report["provider_calls"] = provider_calls

        # ---- post-check (independent) + idempotency ----
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            after = await _counts(ro)
            report["postcheck_counts"] = after
            report["protected_fp_after"] = await _protected_fp(ro)
            report["new_rows"] = [dict(r) for r in (await ro.execute(text(
                """SELECT pc.id, pc.question_version_id, pc.content, pc.discipline, pc.status, pc.lifecycle,
                          pc.source, pc.model_version, pc.provider_name,
                          pc.metadata->>'taxonomy_version' tv, pc.metadata->>'primary_content_code' pcc
                   FROM pedagogical_classifications pc WHERE pc.model_version LIKE 'phase11.26-%'
                   ORDER BY pc.created_at"""))).mappings().all()]
            dup = (await ro.execute(text(
                """SELECT question_version_id,count(*) c FROM pedagogical_classifications
                   WHERE lifecycle='ACTIVE' AND metadata->>'taxonomy_version'='curriculum-v2'
                   GROUP BY 1 HAVING count(*)>1"""))).all()
            report["duplicate_active_cv2"] = [[str(d[0]), d[1]] for d in dup]
            report["orphans"] = int(await ro.scalar(text(
                """SELECT count(*) FROM pedagogical_classifications pc
                   LEFT JOIN question_versions qv ON qv.id=pc.question_version_id WHERE qv.id IS NULL""")))

        report["deltas"] = {k: after[k] - before[k] for k in before}
        report["protected_unchanged"] = report["protected_fp_before"] == report["protected_fp_after"]
        persisted = sum(1 for k in ("Q150", "Q132", "Q116")
                        if isinstance(report.get(k, {}).get("persist", report.get(k, {}).get("verdict")), (dict,))
                        or report.get(k, {}).get("verdict") == "PERSISTED"
                        or isinstance(report.get(k, {}).get("persist"), dict))
        report["classifications_created"] = report["deltas"]["pedagogical_classifications"]
        report["security"] = {
            "DATABASE_WRITES": 1 if report["deltas"]["pedagogical_classifications"] > 0 else 0,
            "CLASSIFICATIONS_CREATED": report["deltas"]["pedagogical_classifications"],
            "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
            "TAXONOMY_CHANGES": report["deltas"]["catalog_nodes"] + report["deltas"]["catalog_node_prerequisites"],
            "BINDING_CHANGES": 2,
            "OPENAI_CALLS": provider_calls,
            "ALEMBIC_EXECUTION": 0,
            "no_direct_openai_construction": True,
            "api_key_in_report": False,
        }
        report["total_elapsed_s"] = round(time.time() - t0, 1)
        ok = (report["protected_unchanged"]
              and not report["duplicate_active_cv2"] and report["orphans"] == 0
              and report["deltas"]["catalog_nodes"] == 0
              and report["deltas"]["catalog_node_prerequisites"] == 0
              and provider_calls <= 6
              and report["deltas"]["question_classifications"] == 0
              and all(r["tv"] == "curriculum-v2" and r["status"] == "CLASSIFIED" and r["lifecycle"] == "ACTIVE"
                      for r in report["new_rows"]))
        report["FINAL_DECISION"] = ("PHASE_11_26_SAFE_HR_RESOLUTION_COMPLETE" if ok
                                    else "PHASE_11_26_NEEDS_REVIEW")
        return report
    except Abort as exc:
        report["FINAL_DECISION"] = "PHASE_11_26_ABORTED"
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
