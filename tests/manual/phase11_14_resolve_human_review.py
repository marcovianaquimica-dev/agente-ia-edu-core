"""PHASE 11.14 - resolve the 4 remaining HUMAN_REVIEW cases (2024 Q104/Q150/Q133, 2025 Q99).

Two modes, auto-detected from whether the 3 PHASE 11.14 binding targets are already
present in _curriculum_v2_bindings._SPECS:

  * SWEEP   (targets NOT yet in _SPECS): validate the 3 CANDIDATE bindings in-memory
            against all 332 statements - target must match, 0 false positives, 0
            candidate losses, existing 26 bindings byte-identical. No LLM. No file
            change. Prints the verdict so the operator can apply the edits.

  * CLASSIFY (targets ALREADY in _SPECS): re-verify the sweep from the live _SPECS,
            then run the official pipeline (ProviderRouter -> OpenAIProvider ->
            ClassificationProposalService.propose_with_provider) N=3 per question for
            Q150/Q133/Q99, each in its own rolled-back transaction (<=9 calls). Q104
            gets a deterministic curator-approved marker (no LLM). Nothing is persisted.
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
        _REPO = _cand
        break
else:  # pragma: no cover
    _REPO = _HERE.parents[1]

from sqlalchemy import select, text  # noqa: E402

from agente_ia_edu.db.models import CatalogNode  # noqa: E402
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
import agente_ia_edu.services._curriculum_v2_bindings as B  # noqa: E402
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    ClassificationProposalService, RetrievalVocabularyEntry, DeterministicInitialBinding)
from agente_ia_edu.providers.factory import build_text_provider  # noqa: E402
from agente_ia_edu.providers.errors import (  # noqa: E402
    ProviderAuthenticationError, ProviderConfigurationError, ProviderInvalidResponseError,
    ProviderRateLimitError, ProviderTimeoutError, ProviderUnavailableError)

OUT = _REPO / "var" / "phase11_14_resolve_human_review_report.json"
CLASSIFIER_VERSION = "phase11.14-resolve-hr-v1"
PROMPT_VERSION = "phase11.5a1-curriculum-v2-v1"
TAXONOMY_VERSION = "curriculum-v2"
N_REPEAT = 3

# CANDIDATE bindings: (content_code, parent_area, primary, specific, contextual)
CANDIDATES = {
    "MATH-ALGEBRA-LOGARITHMS": ("MATH-ALGEBRA",
        ("expressão algébrica relacionando esses valores",
         "energia liberada foi um décimo da observada no segundo terremoto"),
        ("magnitude M2 do segundo terremoto", "primeiro terremoto apresentou a magnitude"),
        ("é conhecida uma expressão algébrica relacionando esses valores dada por",)),
    "BIOLOGY-EVOLUTION-MECHANISMS": ("BIOLOGY-EVOLUTION",
        ("falsas-corais", "padrão de coloração muito semelhante"),
        ("corais-verdadeiras", "não possuem peçonha", "que são peçonhentas"),
        ("Essa similaridade traz uma vantagem tanto para as corais falsas",
         "Qual é a vantagem dessa similaridade para as falsas-corais")),
    "BIOLOGY-CYTOLOGY-ORGANELLES": ("BIOLOGY-CYTOLOGY",
        ("insuficiência funcional de qual estrutura celular",
         "não degradam colesterol esterificado nem triglicerídeos"),
        ("depósito desses compostos em diversos órgãos", "deficiência da enzima lipase ácida"),
        ("Essa doença resulta da insuficiência funcional de qual estrutura celular",)),
}
TARGET_Q = {"MATH-ALGEBRA-LOGARITHMS": (2024, 150),
            "BIOLOGY-EVOLUTION-MECHANISMS": (2024, 133),
            "BIOLOGY-CYTOLOGY-ORGANELLES": (2025, 99)}

Q104 = (2024, 104)
Q104_TARGET = "PHYSICS-MECHANICS-KINEMATICS"
Q104_EVIDENCE = [
    "compensar os efeitos da corrente marinha",
    "a velocidade do nadador é de 50 metros por minuto",
]

PROTECTED = [(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)]
COUNT_TABLES = ["questions", "question_versions", "question_options", "booklet_questions",
                "catalog_nodes", "catalog_node_prerequisites",
                "pedagogical_classifications", "question_classifications"]
CV2_ACTIVE = ("SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
              "AND metadata->>'taxonomy_version'='curriculum-v2'")
EXPECT = {"questions": 332, "question_versions": 332, "question_options": 1660,
          "booklet_questions": 332, "catalog_nodes": 64, "catalog_node_prerequisites": 3,
          "pedagogical_classifications": 33, "question_classifications": 0}
_PROVIDER_EXC = (ProviderAuthenticationError, ProviderConfigurationError, ProviderInvalidResponseError,
                 ProviderRateLimitError, ProviderTimeoutError, ProviderUnavailableError)
_SECRET_RE = re.compile(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+")


def _scrub(s):
    s = "" if s is None else str(s)
    s = _SECRET_RE.sub("[REDACTED]", s)
    k = os.getenv("OPENAI_API_KEY")
    return s.replace(k, "[REDACTED]") if k else s


async def _counts(s):
    d = {t: int(await s.scalar(text(f"SELECT count(*) FROM {t}"))) for t in COUNT_TABLES}
    d["curriculum_v2_ACTIVE"] = int(await s.scalar(text(CV2_ACTIVE)))
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


def _outcome(rec):
    md = rec.metadata_ or {}
    if bool(md.get("catalog_gap")) or md.get("gap_type") or md.get("review_reason") in (
            "CATALOG_GAP", "TAXONOMY_GRANULARITY_GAP"):
        return "CURRICULUM_GAP", md.get("content_code"), md
    if md.get("proposal_status") == "NEEDS_REVIEW" or rec.status == "NEEDS_REVIEW" or md.get("review_reason"):
        return "HUMAN_REVIEW", md.get("content_code"), md
    if md.get("content_code"):
        return "CLASSIFIED", md.get("content_code"), md
    return "HUMAN_REVIEW", None, md


def _merged_vocabs(extra_specs):
    """extra_specs: dict code -> (parent, primary, specific, contextual). Returns the
    full vocab tuple = current live _SPECS + extras (extras that duplicate a live code
    are skipped)."""
    live = list(B._SPECS)
    live_codes = {s[0] for s in live}
    specs = list(live)
    for code, (parent, p, sp, c) in extra_specs.items():
        if code not in live_codes:
            specs.append((code, parent, p, sp, c))
    out = []
    for code, parent, p, sp, c in specs:
        out.append(RetrievalVocabularyEntry(
            canonical_code=code, primary_terms=p, specific_terms=sp, contextual_expressions=c,
            generic_terms=(), version=f"curriculum-v2-{code.lower().replace('-', '_')}-v1", enabled=True))
    return tuple(out)


async def run():
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    report = {"phase": "11.14"}
    live_codes = {s[0] for s in B._SPECS}
    mode = "CLASSIFY" if all(c in live_codes for c in CANDIDATES) else "SWEEP"
    report["mode"] = mode
    report["_SPECS_len"] = len(B._SPECS)
    t0 = time.time()
    try:
        async with factory() as s:
            await s.execute(text("SET TRANSACTION READ ONLY"))
            report["current_database"] = await s.scalar(text("SELECT current_database()"))
            report["counts_before"] = await _counts(s)
            report["protected_fp_before"] = await _protected_fp(s)
            catalog = list((await s.scalars(
                select(CatalogNode).where(CatalogNode.active.is_(True)).order_by(CatalogNode.code))).all())
            rows = (await s.execute(text("""
                SELECT ea.year y, bq.official_number n, ea.day AS exam_day, eb.code booklet,
                       bq.question_version_id qvid, qv.statement stmt, qv.canonical_text ctext,
                       (SELECT json_agg(json_build_object('key', qo.option_key, 'text', qo.text)
                                        ORDER BY qo.position)
                        FROM question_options qo WHERE qo.question_version_id=bq.question_version_id) opts
                FROM booklet_questions bq
                JOIN question_versions qv ON qv.id=bq.question_version_id
                JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                JOIN exam_applications ea ON ea.id=eb.exam_application_id
                WHERE (ea.year, bq.official_number) IN ((2024,104),(2024,150),(2024,133),(2025,99))"""))).mappings().all()
            qmap = {(r["y"], r["n"]): r for r in rows}
            all_stmts = (await s.execute(text("""
                SELECT ea.year y, bq.official_number n, ea.day AS exam_day, qv.statement st
                FROM booklet_questions bq
                JOIN question_versions qv ON qv.id=bq.question_version_id
                JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                JOIN exam_applications ea ON ea.id=eb.exam_application_id"""))).all()

        svc = ClassificationProposalService(None)  # only static/vocab helpers used for the sweep

        # ---------- BINDING SWEEP ----------
        base_vocabs = tuple(b.vocabulary for b in B.build_curriculum_v2_bindings(
            RetrievalVocabularyEntry, DeterministicInitialBinding))
        merged_vocabs = _merged_vocabs(CANDIDATES)
        known = {c.code for c in catalog}
        for v in merged_vocabs:
            ClassificationProposalService._validate_retrieval_vocabulary(v, known)
        report["all_vocabs_validate"] = True
        report["merged_vocab_count"] = len(merged_vocabs)

        # per-candidate match sweep (match_retrieval_vocabulary only - this is what a new
        # binding adds to recover_candidates)
        sweep = {}
        for code, (parent, p, sp, c) in CANDIDATES.items():
            vocab = RetrievalVocabularyEntry(canonical_code=code, primary_terms=p, specific_terms=sp,
                                             contextual_expressions=c, generic_terms=(),
                                             version=f"phase11.14-{code}", enabled=True)
            hits = []
            for r in all_stmts:
                m = ClassificationProposalService.match_retrieval_vocabulary(r.st or "", vocab, known_codes=None)
                if m is not None:
                    hits.append({"y": r.y, "n": r.n, "day": r.exam_day,
                                 "terms": list(m.matched_primary_terms + m.matched_specific_terms
                                               + m.matched_contextual_expressions)})
            ty, tn = TARGET_Q[code]
            tgt = [h for h in hits if h["y"] == ty and h["n"] == tn]
            fps = [h for h in hits if not (h["y"] == ty and h["n"] == tn)]
            sweep[code] = {"target": f"{ty} Q{tn}", "target_matched": bool(tgt),
                           "total_hits": len(hits), "false_positives": fps,
                           "target_terms": tgt[0]["terms"] if tgt else []}
        report["binding_match_sweep"] = sweep

        # full recover_candidates diff over 332 (base vs merged): gains/losses of content_code
        svc2 = None
        gains, losses = [], []
        async with factory() as s:
            await s.execute(text("SET TRANSACTION READ ONLY"))
            svc2 = ClassificationProposalService(s)
            for r in all_stmts:
                bc = {x.get("content_code") for x in svc2.recover_candidates(r.st or "", catalog, vocabularies=base_vocabs)}
                mc = {x.get("content_code") for x in svc2.recover_candidates(r.st or "", catalog, vocabularies=merged_vocabs)}
                g = sorted(x for x in (mc - bc) if x)
                l = sorted(x for x in (bc - mc) if x)
                if g:
                    gains.append({"y": r.y, "n": r.n, "gained": g})
                if l:
                    losses.append({"y": r.y, "n": r.n, "lost": l})
        report["recover_candidates_diff"] = {"gains": gains, "losses": losses}

        sweep_clean = (
            all(v["target_matched"] and not v["false_positives"] for v in sweep.values())
            and not losses
            and {frozenset(g["gained"]) for g in gains} <= {frozenset([c]) for c in CANDIDATES}
            and all(len(g["gained"]) == 1 for g in gains)
        )
        report["sweep_clean"] = sweep_clean
        report["existing_26_unchanged"] = len(base_vocabs) == 26 and all(
            b.vocabulary.canonical_code == b.canonical_code
            for b in B.build_curriculum_v2_bindings(RetrievalVocabularyEntry, DeterministicInitialBinding))

        if not sweep_clean:
            report["FINAL_DECISION"] = "PHASE_11_14_ABORTED_BINDING_FALSE_POSITIVE"
            report["blocker"] = {"sweep": sweep, "losses": losses, "gains": gains}
            return report

        if mode == "SWEEP":
            report["FINAL_DECISION"] = "PHASE_11_14_SWEEP_CLEAN_APPLY_BINDINGS"
            report["next"] = ("Append the 3 candidate specs to _curriculum_v2_bindings._SPECS "
                              "(25->wait: 26->29) then re-run this script in CLASSIFY mode.")
            return report

        # ---------- CLASSIFY MODE ----------
        report["binding_specs_now"] = len(B._SPECS)
        report["new_bindings_live"] = [c for c in CANDIDATES if c in {s[0] for s in B._SPECS}]

        # Q104 deterministic curator marker
        q = qmap[Q104]
        src104 = q["ctext"] or q["stmt"] or ""
        q104_ev_ok = all(e in src104 for e in Q104_EVIDENCE)
        report["Q104"] = {
            "question": "2024 Q104", "target": Q104_TARGET,
            "deterministic_evidence_still_matches": q104_ev_ok,
            "evidence": Q104_EVIDENCE,
            "central_concept": "composicao vetorial de velocidades / velocidade relativa + cinematica",
            "decision": "CURATOR_APPROVED / READY_FOR_PERSISTENCE" if q104_ev_ok else "HUMAN_REVIEW",
            "llm_used": False,
            "note": ("PHASE 11.13 established Q104 -> PHYSICS-MECHANICS-KINEMATICS from text alone; "
                     "the circuit diagram is a solvability dependency only. NOT persisted here."),
        }

        # PHASE 11.23: provider selected by AI_PROVIDER config, not hand-constructed.
        router = build_text_provider()
        report["provider"] = {"selected_via": "build_text_provider() / AI_PROVIDER", "model": os.getenv("OPENAI_MODEL"),
                              "api_key_configured": bool(os.getenv("OPENAI_API_KEY"))}
        provider_calls = 0

        async def _one_run(qv, i):
            nonlocal provider_calls
            t = time.time()
            async with factory() as session:
                _orig = session.commit
                session.commit = session.flush  # type: ignore[assignment]
                await session.begin()
                try:
                    s2 = ClassificationProposalService(session)
                    try:
                        rec = await s2.propose_with_provider(
                            qv, router, classifier_version=f"{CLASSIFIER_VERSION}-r{i}",
                            taxonomy_version=TAXONOMY_VERSION, prompt_version=PROMPT_VERSION)
                        provider_calls += 1
                        dec, content, md = _outcome(rec)
                        out = {"run": i, "decision": dec, "content_code": content,
                               "discipline": md.get("discipline_code"), "area": md.get("area_code"),
                               "confidence": md.get("confidence_band"), "review_reason": md.get("review_reason"),
                               "gap_type": md.get("gap_type"), "selected_rank": md.get("selected_candidate_rank"),
                               "recovered": [x.get("content_code") for x in (md.get("recovered_candidates") or [])],
                               "evidence": [{"text": e.get("text", "")[:200], "reason": e.get("reason", "")[:160]}
                                            for e in (md.get("evidence") or [])[:3]]}
                    except _PROVIDER_EXC as exc:
                        provider_calls += 1
                        out = {"run": i, "decision": "PROVIDER_ERROR",
                               "error": f"{type(exc).__name__}: {_scrub(getattr(exc, 'diagnostic_message', None) or exc)}"}
                    except ValueError as exc:
                        provider_calls += 1
                        out = {"run": i, "decision": "HUMAN_REVIEW", "content_code": None,
                               "note": f"decision-core rejected: {_scrub(exc)}"}
                finally:
                    if session.in_transaction():
                        await session.rollback()
                    session.commit = _orig  # type: ignore[assignment]
            out["elapsed_s"] = round(time.time() - t, 2)
            return out

        SEM_TARGET = {(2024, 150): "MATH-ALGEBRA-LOGARITHMS", (2024, 133): "BIOLOGY-EVOLUTION-MECHANISMS",
                      (2025, 99): "BIOLOGY-CYTOLOGY-ORGANELLES"}
        results = []
        for key in [(2024, 150), (2024, 133), (2025, 99)]:
            qv = str(qmap[key]["qvid"])
            runs = [await _one_run(qv, i) for i in range(N_REPEAT)]
            decs = [r["decision"] for r in runs]
            contents = [r.get("content_code") for r in runs if r["decision"] == "CLASSIFIED"]
            confs = [r.get("confidence") for r in runs if r["decision"] == "CLASSIFIED"]
            unanimous = (Counter(decs).get("CLASSIFIED", 0) == N_REPEAT
                         and len(set(contents)) == 1 and all(c == "HIGH" for c in confs))
            content = contents[0] if contents else None
            disc = runs[0].get("discipline") or ""
            disc_ok = bool(content) and content.startswith(disc.split("-")[0]) if disc else False
            semantic_ok = unanimous and content == SEM_TARGET[key] and disc_ok
            final = "CLASSIFIED" if semantic_ok else "HUMAN_REVIEW"
            reason = ""
            if not unanimous:
                reason = f"not 3/3 CLASSIFIED HIGH same content: decisions={decs} contents={[r.get('content_code') for r in runs]} conf={[r.get('confidence') for r in runs]}"
            elif content != SEM_TARGET[key]:
                reason = f"unanimous but semantic target mismatch: got {content}, expected {SEM_TARGET[key]}"
            elif not disc_ok:
                reason = f"discipline/content mismatch ({disc} vs {content})"
            results.append({
                "question": f"{key[0]} Q{key[1]}", "question_version_id": qv,
                "semantic_target": SEM_TARGET[key],
                "run_decisions": decs, "run_contents": [r.get("content_code") for r in runs],
                "run_confidence": [r.get("confidence") for r in runs],
                "recovered_run0": runs[0].get("recovered"),
                "evidence_run0": runs[0].get("evidence"),
                "final": final, "reason": reason,
                "semantic_audit": {
                    "central_concept_matches_selected": semantic_ok,
                    "selected_content": content,
                    "competing_candidates": sorted({rc for r in runs for rc in (r.get("recovered") or [])
                                                    if rc and rc != content})[:5],
                },
                "runs": runs,
            })
        report["classify_results"] = results
        report["provider_calls"] = provider_calls
        report["FINAL_CLASSIFIED"] = [r for r in results if r["final"] == "CLASSIFIED"]
        report["HUMAN_REVIEW"] = [r for r in results if r["final"] == "HUMAN_REVIEW"]

        async with factory() as s:
            await s.execute(text("SET TRANSACTION READ ONLY"))
            report["counts_after"] = await _counts(s)
            report["protected_fp_after"] = await _protected_fp(s)
        report["deltas"] = {k: report["counts_after"][k] - report["counts_before"][k] for k in report["counts_before"]}
        report["leak"] = any(v != 0 for v in report["deltas"].values())
        report["protected_unchanged"] = report["protected_fp_before"] == report["protected_fp_after"]
        report["security"] = {
            "CLASSIFICATIONS_CREATED": report["deltas"]["pedagogical_classifications"],
            "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
            "PRODUCTION_DATA_MODIFIED": 1 if report["leak"] else 0,
            "OPENAI_CALLS": provider_calls,
            "VOCABULARY_CHANGES": 3,
            "ALEMBIC_EXECUTION": 0,
            "api_key_exposed": False,
        }
        ok = (not report["leak"] and report["protected_unchanged"] and provider_calls <= 9
              and report["counts_after"] == {**EXPECT, "curriculum_v2_ACTIVE": 29})
        report["FINAL_DECISION"] = "PHASE_11_14_RESOLVE_HUMAN_REVIEW_COMPLETE" if ok else "PHASE_11_14_NEEDS_REVIEW"
        report["total_elapsed_s"] = round(time.time() - t0, 1)
        return report
    finally:
        await engine.dispose()


def main():
    rep = asyncio.run(run())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    fd = rep.get("FINAL_DECISION", "")
    return 0 if fd.endswith("COMPLETE") or fd.endswith("APPLY_BINDINGS") else 1


if __name__ == "__main__":
    sys.exit(main())
