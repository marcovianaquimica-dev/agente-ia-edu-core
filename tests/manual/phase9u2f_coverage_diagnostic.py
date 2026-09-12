"""PHASE 9U.2-F — READ-ONLY inventory coverage diagnostic.

Run from the operator terminal, where DATABASE_URL is the production database:

    cd /Users/marcoviana/agente-ia-edu-core
    PYTHONPATH=src .venv/bin/python tests/manual/phase9u2f_coverage_diagnostic.py

OBSERVATION ONLY. Guarantees by construction:
  * one PostgreSQL session/transaction; `SET TRANSACTION READ ONLY` first
    (via phase9u2_batch0._begin_read_only); SELECT only.
  * every per-question decision + binding is computed by the *existing*
    deterministic mechanisms (phase9u2_batch0.read_snapshot_core /
    decide_question_state, ClassificationProposalService.recover_candidates /
    .resolve_initial_controlled_vocabulary_binding /
    .match_retrieval_vocabulary). No LLM, no OpenAI, no provider.
  * no INSERT/UPDATE/DELETE/DDL, no Alembic, no classification, no correction.
    phase9u2_batch0.do_write / apply_initial are never referenced.
  * Q91 / Q93 / Q128 and classification 18b8666b are only read and reported.

The "lexical discipline signal" is an auditable keyword scan over a FIXED term
list — a grouping aid only. It never asserts a discipline the database does not
already record, and it creates nothing.

Exit 0 always (diagnostic); FINAL_DECISION line is PHASE_9U2F_DIAGNOSTIC_COMPLETE.
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _cand in (Path.cwd(), _HERE.parents[1]):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        _ENV_FILE = _cand / ".env"
        break
else:  # pragma: no cover
    _ENV_FILE = Path(".env")
sys.path.insert(0, str(_HERE))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402

from agente_ia_edu.db.models import BookletQuestion, PedagogicalClassification, QuestionVersion  # noqa: E402
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    _INITIAL_CONTROLLED_VOCABULARIES,
    ClassificationProposalService,
    KINETICS_RETRIEVAL_VOCABULARY,
)

import phase9u2_batch0 as b0  # noqa: E402

PROTECTED = sorted(b0.PROTECTED_OFFICIAL_NUMBERS | {91})
TARGET = b0.TARGET_TAXONOMY
CANON = b0.CANONICAL_CONTENT_CODE
HIST_CLASSIFICATION_PREFIX = "18b8666b"

# Fixed, auditable lexical discipline indicators (grouping aid only — invents nothing).
DISCIPLINE_SIGNALS = {
    "PHYSICS": (
        "força", "aceleração", "velocidade escalar", "energia cinética", "energia potencial",
        "movimento", "onda", "frequência", "comprimento de onda", "circuito", "corrente elétrica",
        "tensão", "resistência", "campo magnético", "lente", "espelho", "calor específico",
        "trabalho", "potência", "newton", "joule",
    ),
    "BIOLOGY": (
        "célula", "membrana plasmática", "dna", "rna", "gene", "cromossomo", "proteína",
        "enzima", "fotossíntese", "respiração celular", "ecossistema", "cadeia alimentar",
        "população", "espécie", "seleção natural", "evolução das espécies", "tecido",
        "sistema nervoso", "hormônio", "vírus", "bactéria", "biodiversidade",
    ),
    "CHEMISTRY_NON_KINETICS": (
        "mol", "concentração em mol", "molaridade", "ph", "ligação química", "ligação iônica",
        "ligação covalente", "oxidação", "redução", "pilha", "eletrólise", "entalpia",
        "termoquímica", "equilíbrio químico", "solução aquosa", "soluto", "solvente",
        "tabela periódica", "hidrocarboneto", "função orgânica", "isomeria", "polímero",
    ),
    "CHEMISTRY_KINETICS": (
        "cinética química", "estudo cinético", "velocidade da reação", "velocidade das reações",
        "taxa de reação", "catalisador", "catálise", "energia de ativação",
    ),
}


def _hp(v) -> str:
    if v is None:
        return "None"
    s = str(v)
    return (s[:12] + "…") if len(s) > 12 else s


def _summ(text_value: str, n: int = 200) -> str:
    t = " ".join((text_value or "").split())
    return (t[:n] + "…") if len(t) > n else t


def _md(row) -> dict:
    return row.metadata_ or {}


def _discipline_signals(statement: str) -> dict[str, list[str]]:
    low = " " + " ".join((statement or "").lower().split()) + " "
    return {
        disc: [term for term in terms if term in low]
        for disc, terms in DISCIPLINE_SIGNALS.items()
        if any(term in low for term in terms)
    }


async def _run() -> int:
    load_dotenv(_ENV_FILE, override=False)
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                await b0._begin_read_only(session)

                alembic_rev = (
                    await session.execute(text("SELECT version_num FROM alembic_version"))
                ).scalars().first()
                catalog_nodes = await b0.load_catalog(session)
                catalog_codes = frozenset(n.code for n in catalog_nodes)
                catalog_by_type: dict[str, list[str]] = {}
                for n in catalog_nodes:
                    catalog_by_type.setdefault(n.node_type, []).append(n.code)

                inventory = await b0.official_numbers(session)
                total_pc = int(
                    await session.scalar(select(func.count()).select_from(PedagogicalClassification))
                )

                # classification 18b8666b (historical Q91 row)
                hist_rows = list(
                    (
                        await session.scalars(
                            select(PedagogicalClassification).where(
                                text("CAST(pedagogical_classifications.id AS text) LIKE :p")
                            ).params(p=f"{HIST_CLASSIFICATION_PREFIX}%")
                        )
                    ).all()
                )
                hist = hist_rows[0] if len(hist_rows) == 1 else None

                per_question: list[dict] = []
                state_counts = Counter()
                svc = ClassificationProposalService(None)

                for number in inventory:
                    snap = await b0.read_snapshot_core(session, number, catalog_nodes, catalog_codes)
                    if not snap.exists or snap.hard_error:
                        per_question.append(
                            dict(n=number, qv=None, state="ERROR",
                                 reason=snap.hard_error or "NOT_IN_INVENTORY")
                        )
                        state_counts["ERROR"] += 1
                        continue
                    ctx = snap.ctx
                    d = b0.decide_question_state(ctx)
                    state_counts[d.state] += 1

                    qv_id = ctx.question_version.id if ctx.question_version else None
                    version = await session.get(QuestionVersion, __import__("uuid").UUID(qv_id)) if qv_id else None
                    statement = (
                        (version.statement or version.canonical_text or "") if version is not None else ""
                    )
                    rows = list(ctx.classifications)
                    existing = rows[0] if rows else None

                    # deterministic binding explanation (no provider)
                    recovered = svc.recover_candidates(statement, catalog_nodes) if statement.strip() else []
                    vmatch = (
                        svc.match_retrieval_vocabulary(statement, KINETICS_RETRIEVAL_VOCABULARY, known_codes=None)
                        if statement.strip()
                        else None
                    )
                    binding = ctx.binding
                    canonical_recovered = any(c.get("content_code") == CANON for c in recovered)

                    if not statement.strip():
                        det_reason = "NO_STATEMENT_TEXT"
                    elif binding is None and vmatch is None:
                        det_reason = "kinetics controlled vocabulary matched NO primary/specific/contextual term"
                    elif binding is not None and binding.binding_status == "NEEDS_REVIEW":
                        det_reason = "kinetics vocabulary matched but CHEMISTRY-PHYSICAL-KINETICS candidate not recovered"
                    elif binding is not None and binding.binding_status == "BOUND":
                        det_reason = "kinetics vocabulary BOUND"
                    elif rows:
                        det_reason = f"existing classification present ({d.reason_code})"
                    else:
                        det_reason = d.reason_code

                    per_question.append(
                        dict(
                            n=number,
                            qv=_hp(qv_id),
                            statement=_summ(statement),
                            existing_content=(existing.content if existing else None),
                            existing_taxonomy=(_md(existing).get("taxonomy_version") if existing else None),
                            existing_mode=(_md(existing).get("classification_mode") if existing else None),
                            existing_status=(existing.status if existing else None),
                            existing_source=(existing.source if existing else None),
                            existing_model_version=(existing.model_version if existing else None),
                            binding_status=(binding.binding_status if binding else None),
                            bound_content=((binding.bound_candidate or {}).get("content_code") if binding else None),
                            canonical_recovered=canonical_recovered,
                            literal_evidence=(bool(binding.literal_evidence_term) if binding else False),
                            vocab_matched_terms=list(
                                (vmatch.matched_primary_terms + vmatch.matched_specific_terms
                                 + vmatch.matched_contextual_expressions)
                                if vmatch else ()
                            ),
                            recovered_candidate_codes=sorted({c.get("content_code") for c in recovered if c.get("content_code")}),
                            discipline_signals=_discipline_signals(statement),
                            batch0_state=d.state,
                            batch0_reason=d.reason_code,
                            deterministic_reason=det_reason,
                        )
                    )
    finally:
        await engine.dispose()

    # ---------------- grouping ----------------
    groups: dict[str, list[int]] = Counter()  # type: ignore
    grouping: dict[str, list[int]] = {}
    for q in per_question:
        if q["n"] in PROTECTED:
            key = "PROTECTED"
        elif q["batch0_state"] == "ALREADY_CLASSIFIED":
            key = "ALREADY_CLASSIFIED"
        elif q["batch0_state"] == "READY_FOR_INITIAL":
            key = "COVERED_BY_VOCABULARY(kinetics)"
        elif q["batch0_reason"] == "NO_STATEMENT_TEXT" or q["batch0_state"] == "BLOCKED":
            key = "BLOCKED_MISSING_DATA"
        elif q["existing_taxonomy"] and q["existing_taxonomy"] != TARGET:
            key = "HISTORICAL_OTHER_TAXONOMY"
        elif q["binding_status"] == "NEEDS_REVIEW":
            key = "AMBIGUOUS(kinetics matched, canonical not recovered)"
        elif q["discipline_signals"].get("CHEMISTRY_KINETICS"):
            key = "KINETICS_PHRASING_NOT_IN_VOCABULARY"
        elif any(q["discipline_signals"].get(k) for k in ("PHYSICS", "BIOLOGY", "CHEMISTRY_NON_KINETICS")):
            key = "NOT_COVERED_BY_VOCABULARY(other discipline signal)"
        else:
            key = "NOT_COVERED_BY_VOCABULARY(no deterministic signal)"
        grouping.setdefault(key, []).append(q["n"])

    q91 = next((q for q in per_question if q["n"] == 91), {})
    q93 = next((q for q in per_question if q["n"] == 93), {})
    q128 = next((q for q in per_question if q["n"] == 128), {})

    # ---------------- report ----------------
    print("PHASE 9U.2-F — INVENTORY COVERAGE DIAGNOSTIC")
    print()
    print(f"ALEMBIC_REVISION: {alembic_rev}")
    print(f"TOTAL_OFFICIAL_QUESTIONS: {len(inventory)}")
    print(f"TOTAL_CLASSIFICATIONS: {total_pc}")
    print()
    print("BATCH-0 STATE COUNTS (deterministic core):")
    for st in ("PROTECTED", "ALREADY_CLASSIFIED", "READY_FOR_INITIAL", "NEEDS_REVIEW",
               "BLOCKED", "OUT_OF_SCOPE", "SUPERSEDED_ONLY", "ERROR"):
        print(f"  {st}: {state_counts.get(st, 0)}")
    print()
    print("REGISTERED CONTROLLED VOCABULARIES (_INITIAL_CONTROLLED_VOCABULARIES):")
    for tx, v in _INITIAL_CONTROLLED_VOCABULARIES.items():
        print(f"  {tx} -> canonical={v.canonical_code} version={v.version} enabled={v.enabled}")
        print(f"     primary_terms={list(v.primary_terms)}")
        print(f"     specific_terms={list(v.specific_terms)}")
        print(f"     contextual_expressions={list(v.contextual_expressions)}")
    print(f"  canonical catalog node present: {CANON in catalog_codes}")
    print()
    print("CATALOG NODES BY TYPE:")
    for t, codes in sorted(catalog_by_type.items()):
        print(f"  {t}: {sorted(codes)}")
    print()
    print("HISTORICAL CLASSIFICATION 18b8666b… :")
    if hist is not None:
        print(f"  id={_hp(hist.id)} qv={_hp(hist.question_version_id)} lifecycle={hist.lifecycle} "
              f"content={hist.content!r} taxonomy_version={_md(hist).get('taxonomy_version')} "
              f"classification_mode={_md(hist).get('classification_mode')} status={hist.status} "
              f"source={hist.source} model_version={hist.model_version} supersedes_id={_hp(hist.supersedes_id)}")
        print("  (READ ONLY — not modified)")
    else:
        print(f"  matched {len(hist_rows)} rows for prefix {HIST_CLASSIFICATION_PREFIX!r} — NOT_VERIFIABLE")
    print()
    print("PROTECTED QUESTIONS:")
    for lbl, q in (("Q91", q91), ("Q93", q93), ("Q128", q128)):
        print(f"  {lbl}: batch0_state={q.get('batch0_state')} reason={q.get('batch0_reason')} "
              f"existing_content={q.get('existing_content')!r} existing_taxonomy={q.get('existing_taxonomy')} "
              f"existing_mode={q.get('existing_mode')}")
    print()
    print("PER-QUESTION DETAIL (OUT_OF_SCOPE / NEEDS_REVIEW / BLOCKED):")
    for q in per_question:
        if q["n"] in PROTECTED or q["batch0_state"] in ("ALREADY_CLASSIFIED", "READY_FOR_INITIAL"):
            continue
        print(f"  #{q['n']} qv={q['qv']} state={q['batch0_state']} reason={q['batch0_reason']}")
        print(f"     statement: {q['statement']}")
        print(f"     existing: content={q['existing_content']!r} taxo={q['existing_taxonomy']} "
              f"mode={q['existing_mode']} status={q['existing_status']} source={q['existing_source']} "
              f"model={q['existing_model_version']}")
        print(f"     binding: status={q['binding_status']} bound={q['bound_content']} "
              f"canonical_recovered={q['canonical_recovered']} literal_evidence={q['literal_evidence']} "
              f"vocab_matched_terms={q['vocab_matched_terms']}")
        print(f"     recovered_candidate_codes={q['recovered_candidate_codes']}")
        print(f"     discipline_signals={q['discipline_signals']}")
        print(f"     deterministic_reason: {q['deterministic_reason']}")
    print()
    print("GRUPO | QUESTÕES | SITUAÇÃO")
    situation = {
        "PROTECTED": "OUT_OF_SCOPE LEGÍTIMO (protegido)",
        "ALREADY_CLASSIFIED": "COBERTO PELO VOCABULÁRIO (já classificado)",
        "COVERED_BY_VOCABULARY(kinetics)": "COBERTO PELO VOCABULÁRIO",
        "HISTORICAL_OTHER_TAXONOMY": "OUT_OF_SCOPE LEGÍTIMO (classificação histórica em outra taxonomy)",
        "AMBIGUOUS(kinetics matched, canonical not recovered)": "AMBÍGUO",
        "KINETICS_PHRASING_NOT_IN_VOCABULARY": "NÃO COBERTO PELO VOCABULÁRIO (cinética, fraseado fora do léxico)",
        "NOT_COVERED_BY_VOCABULARY(other discipline signal)": "NÃO COBERTO PELO VOCABULÁRIO",
        "NOT_COVERED_BY_VOCABULARY(no deterministic signal)": "NÃO COBERTO PELO VOCABULÁRIO / revisão humana",
        "BLOCKED_MISSING_DATA": "BLOQUEADO POR FALTA DE DADO",
    }
    for key, nums in sorted(grouping.items()):
        print(f"  {key} | {sorted(nums)} | {situation.get(key, key)}")
    print()
    print("DATABASE_WRITES: 0")
    print("OPENAI_CALLS: 0")
    print("ALEMBIC_EXECUTION: 0")
    print("FILES_MODIFIED: 0")
    print()
    print("FINAL_DECISION:")
    print("PHASE_9U2F_DIAGNOSTIC_COMPLETE")
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception as exc:  # fail closed
        import os

        msg = str(exc)
        for key in ("DATABASE_URL", "OPENAI_API_KEY"):
            secret = os.getenv(key)
            if secret:
                msg = msg.replace(secret, "[REDACTED]")
        print("PHASE 9U.2-F — INVENTORY COVERAGE DIAGNOSTIC")
        print(f"FATAL: {type(exc).__name__}: {msg[:300]}")
        print()
        print("FINAL_DECISION:")
        print("PHASE_9U2F_DIAGNOSTIC_INCOMPLETE")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
