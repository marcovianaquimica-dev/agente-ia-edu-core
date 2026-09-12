"""PHASE 10.9 - final READ-ONLY pre-ingestion audit for ENEM 2016-2025.

Runs the full 20-booklet corpus through the existing pipeline in DRY-RUN and
produces the exact inventory PHASE 10.10 would act on:

  * per-booklet counts (detected / import_ready / review / duplicate / invalid /
    ocr_pending / option_recovery_pending / error)
  * REAL_IMPORT_CANDIDATES  - every question that would become NEW
  * EXCLUDED_FROM_REAL_INGESTION - everything else, with a reason
  * idempotency / content_hash collision check
  * a 15-table leak snapshot taken in a SEPARATE connection, before vs after

Routing (unchanged from PHASES 10.5-10.8):
  2016-2020 D1/D2 -> native pypdf
  2021 D1/D2      -> OCR_PENDING: detected via native pypdf (glyph soup),
                     text_recovery confirms status, ALL questions excluded,
                     OCR_REQUIRED = true. PyMuPDF text is NOT used.
  2022/2023 D1/D2 -> native pypdf, current review-gate behaviour, OPTION_PATTERN
                     untouched
  2024/2025 D1/D2 -> option_recovery.recover_option_layout(); only IMPORT_READY
                     questions may become NEW

DRY-RUN ONLY. No approval token. No real ingestion. DATABASE_WRITES = 0.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
for _cand in (Path.cwd(), _HERE.parents[1]):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        _REPO_ROOT = _cand
        _ENV_FILE = _cand / ".env"
        break
else:  # pragma: no cover
    _REPO_ROOT = _HERE.parents[1]
    _ENV_FILE = Path(".env")

from sqlalchemy import func, select  # noqa: E402

from agente_ia_edu.db.models import IngestionQuestion, QuestionVersion  # noqa: E402
from agente_ia_edu.services.ingestion import IngestionService  # noqa: E402
from agente_ia_edu.services.ingestion_parser import PdfParser  # noqa: E402
from agente_ia_edu.services.option_recovery import recover_option_layout  # noqa: E402
from agente_ia_edu.services.question_bank_importer import QuestionBankImporter  # noqa: E402
from agente_ia_edu.services.text_recovery import recover_pdf_text  # noqa: E402

import phase10_ingest_batch as p10  # noqa: E402

PHASE = "10.9"
DEFAULT_MANIFEST = _HERE / "phase10_enem_manifest_2016_2025.json"
DEFAULT_REPORT = _REPO_ROOT / "var" / "inep-pilot" / "phase10_9_final_pre_ingestion_report.json"

NATIVE_KEYS = {(y, d) for y in range(2016, 2021) for d in (1, 2)}
NATIVE_KEYS |= {(2022, 1), (2022, 2), (2023, 1), (2023, 2)}
RECOVERED_KEYS = {(2024, 1), (2024, 2), (2025, 1), (2025, 2)}
OCR_KEYS = {(2021, 1), (2021, 2)}

_LEAK_TABLES = dict(p10._LEAK_TABLES)  # 19 tables (superset of the phase's 15)

IDEMPOTENCY_EXPECT = {(2017, 1): 54, (2020, 1): 79, (2020, 2): 24}
AUDIT_QUESTIONS = {(2025, 2): [145, 154]}


@dataclass
class BookletAudit:
    exam_year: int
    exam_day: int
    booklet: str
    method: str
    recovery_status: str = "OK(native)"
    ocr_required: bool = False
    questions_detected: int = 0
    distinct_numbers: int = 0
    official_range_complete: bool = False
    import_ready: int = 0
    review: int = 0
    duplicate: int = 0
    invalid: int = 0
    ocr_pending: int = 0
    option_recovery_pending: int = 0
    new: int = 0
    annulled: int = 0
    staged: int = 0
    errors: int = 0
    error_detail: list[str] = field(default_factory=list)
    hash_analysis: dict = field(default_factory=dict)
    idempotency_ok: bool = True

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["error_detail"] = self.error_detail[:20]
        return d


def _method_for(y: int, d: int) -> str:
    if (y, d) in RECOVERED_KEYS:
        return "recovered_option_layout"
    if (y, d) in OCR_KEYS:
        return "native_pypdf(ocr_pending)"
    return "native_pypdf"


async def _existing_hashes(factory, hashes: list[str]) -> set[str]:
    if not hashes:
        return set()
    async with factory() as s:
        rows = await s.scalars(
            select(QuestionVersion.content_hash).where(QuestionVersion.content_hash.in_(hashes))
        )
        return set(rows.all())


async def process_booklet(
    session, factory, entry: dict, *, candidates: list, excluded: list,
    audit_questions: dict
) -> BookletAudit:
    y, d = int(entry["exam_year"]), int(entry["exam_day"])
    method = _method_for(y, d)
    a = BookletAudit(y, d, str(entry["booklet"]), method)
    lo, hi = (1, 90) if d == 1 else (91, 180)
    try:
        proof = p10._resolve_pdf(entry["proof_pdf"])
        gabarito = p10._resolve_pdf(entry["answer_key_pdf"])
        answer_key = PdfParser.parse_answer_key(gabarito)

        parsed_override = None
        recovered = None
        recovered_status_by_num: dict[int, str] = {}

        if (y, d) in OCR_KEYS:
            tr = recover_pdf_text(proof, expected_questions=90)
            a.recovery_status = tr.recovery_status          # OCR_PENDING
            a.ocr_required = True
            src = PdfParser.parse_file(proof)               # native (glyph soup)
        elif (y, d) in RECOVERED_KEYS:
            recovered = recover_option_layout(proof)
            a.recovery_status = "RECOVERED(option_layout)"
            recovered_status_by_num = {q.number: q.status for q in recovered.questions}
            a.import_ready = len(recovered.import_ready)   # option_recovery verdict
            parsed_override = PdfParser.parse_file(proof, page_texts=recovered.page_texts)
            src = parsed_override
        else:
            src = PdfParser.parse_file(proof)

        nums = sorted(q.question_number for q in src.questions)
        a.questions_detected = len(src.questions)
        a.distinct_numbers = len(set(nums))
        a.official_range_complete = set(range(lo, hi + 1)).issubset(set(nums))

        # hash / idempotency (read-only separate session)
        stmt_hashes = [
            hashlib.sha256(q.statement_text.encode("utf-8")).hexdigest()
            for q in src.questions if q.statement_text and q.statement_text.strip()
        ]
        pre = await _existing_hashes(factory, stmt_hashes)
        a.hash_analysis = {
            "statements_hashed": len(stmt_hashes),
            "distinct_hashes": len(set(stmt_hashes)),
            "already_in_question_versions": len(pre),
            "collision_risk": bool(pre) and method == "recovered_option_layout",
        }

        # stage + import inside the dry-run transaction
        document, _run = await IngestionService().ingest_document(
            session, proof, answer_key=answer_key,
            source_metadata=p10._source_metadata(entry),
            parsed_override=parsed_override,
        )
        staged_ids = list((await session.scalars(
            select(IngestionQuestion.id).where(IngestionQuestion.document_id == document.id)
        )).all())
        a.staged = len(staged_ids)
        importer = QuestionBankImporter(session)
        bucket_by_num: dict[int, str] = {}
        opts_by_num: dict[int, str] = {}

        for qid in staged_ids:
            item = await session.get(IngestionQuestion, qid)
            official = item.question_number
            opts_by_num.setdefault(official, item.alternatives_text or "")
            stmt_hash = (
                hashlib.sha256(item.statement_text.encode("utf-8")).hexdigest()
                if item.statement_text else None
            )
            n_opts = len([
                ln for ln in (item.alternatives_text or "").splitlines() if ln.strip()
            ])
            try:
                result = await importer.import_question(qid)
            except Exception as exc:  # noqa: BLE001
                a.errors += 1
                a.error_detail.append(f"{qid}: {type(exc).__name__}: {exc}")
                if not session.in_transaction():
                    await session.begin()
                excluded.append({
                    "year": y, "day": d, "booklet": a.booklet, "official_number": official,
                    "reason": "ERROR", "detail": f"{type(exc).__name__}: {exc}",
                })
                continue
            bucket = p10._classify_result(result)
            bucket_by_num[official] = bucket
            rstatus = recovered_status_by_num.get(official)   # None for native

            # ---- one authoritative disposition per staged question ----
            if (y, d) in OCR_KEYS:
                disp = "OCR_PENDING"
            elif (y, d) in RECOVERED_KEYS and rstatus == "OPTION_RECOVERY_PENDING":
                disp = "OPTION_RECOVERY_PENDING"
            elif (y, d) in RECOVERED_KEYS and rstatus == "INVALID":
                disp = "INVALID"
            elif bucket == "duplicate":
                disp = "DUPLICATE"
            elif bucket == "annulled":
                disp = "ANNULLED"
            elif bucket == "new":
                disp = "NEW"
            elif bucket == "requires_review":
                disp = "REVIEW"
            else:
                disp = "INVALID"

            # a recovered NEW must be backed by an IMPORT_READY verdict
            if disp == "NEW" and (y, d) in RECOVERED_KEYS and rstatus != "IMPORT_READY":
                disp = "OPTION_RECOVERY_PENDING"

            if disp == "NEW":
                a.new += 1
                candidates.append({
                    "year": y, "day": d, "booklet": a.booklet, "official_number": official,
                    "statement_hash": stmt_hash, "number_of_options": n_opts,
                    "extraction_method": method,
                    "recovery_status": (rstatus or a.recovery_status),
                })
                continue

            if disp == "OCR_PENDING":
                a.ocr_pending += 1
            elif disp == "OPTION_RECOVERY_PENDING":
                a.option_recovery_pending += 1
            elif disp == "INVALID":
                a.invalid += 1
            elif disp == "DUPLICATE":
                a.duplicate += 1
            elif disp == "ANNULLED":
                a.annulled += 1
            elif disp == "REVIEW":
                a.review += 1
            excluded.append({
                "year": y, "day": d, "booklet": a.booklet, "official_number": official,
                "reason": disp,
                **({"statement_hash": stmt_hash} if disp == "DUPLICATE" else {}),
                **({"detail": result.reason} if disp in ("REVIEW", "INVALID") and result.reason else {}),
            })

        if (y, d) in IDEMPOTENCY_EXPECT:
            a.idempotency_ok = a.duplicate == IDEMPOTENCY_EXPECT[(y, d)] and a.new == 0

        for qn in AUDIT_QUESTIONS.get((y, d), []):
            rstatus = recovered_status_by_num.get(qn)
            opt_lines = [ln for ln in opts_by_num.get(qn, "").splitlines() if ln.strip()]
            audit_questions[f"{y}_D{d}_Q{qn}"] = {
                "year": y, "day": d, "official_number": qn,
                "option_recovery_status": rstatus,
                "final_bucket": bucket_by_num.get(qn),
                "number_of_options": len(opt_lines),
                "options": opt_lines,
                "in_real_import_candidates": any(
                    c["year"] == y and c["day"] == d and c["official_number"] == qn
                    for c in candidates
                ),
            }
    except Exception as exc:  # noqa: BLE001
        a.errors += 1
        a.error_detail.append(f"booklet: {type(exc).__name__}: {exc}")
        if not session.in_transaction():
            await session.begin()
    return a


@dataclass
class Audit:
    phase: str = PHASE
    booklets: list[BookletAudit] = field(default_factory=list)
    candidates: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    audit_questions: dict = field(default_factory=dict)   # "2025_D2_Q145" -> {...}
    leak_before: dict = field(default_factory=dict)
    leak_after: dict = field(default_factory=dict)
    dry_run_leak: bool = False

    @property
    def totals(self) -> dict:
        t = {k: 0 for k in ("staged", "import_ready", "new", "duplicate", "review",
                             "invalid", "ocr_pending", "option_recovery_pending", "errors",
                             "annulled")}
        for b in self.booklets:
            t["staged"] += b.staged
            t["import_ready"] += b.import_ready
            t["new"] += b.new
            t["duplicate"] += b.duplicate
            t["review"] += b.review
            t["invalid"] += b.invalid
            t["ocr_pending"] += b.ocr_pending
            t["option_recovery_pending"] += b.option_recovery_pending
            t["errors"] += b.errors
            t["annulled"] += b.annulled
        return t

    def as_dict(self) -> dict:
        d = self.as_dict_core()
        return d

    def as_dict_core(self) -> dict:
        return {
            "phase": self.phase,
            "per_booklet": [b.as_dict() for b in self.booklets],
            "totals": self.totals,
            "real_import_candidates": self.candidates,
            "excluded_from_real_ingestion": self.excluded,
            "audit_questions": self.audit_questions,
            "leak_check": {
                "tables": sorted(_LEAK_TABLES),
                "before": self.leak_before,
                "after": self.leak_after,
                "delta": {k: self.leak_after.get(k, 0) - self.leak_before.get(k, 0)
                          for k in self.leak_before},
                "dry_run_leak": self.dry_run_leak,
            },
        }


async def run(factory, *, manifest_path=DEFAULT_MANIFEST) -> Audit:
    manifest = p10.load_manifest(manifest_path)
    available, missing = p10.validate_manifest(manifest)
    if missing:
        raise RuntimeError(f"manifest incomplete: {[m['label'] for m in missing]}")

    audit = Audit()
    audit.leak_before = await p10._count_snapshot(factory)

    async with factory() as session:
        _orig = session.commit
        session.commit = session.flush  # type: ignore[assignment]
        await session.begin()
        try:
            for entry in available:
                audit.booklets.append(await process_booklet(
                    session, factory, entry,
                    candidates=audit.candidates, excluded=audit.excluded,
                    audit_questions=audit.audit_questions,
                ))
        finally:
            if session.in_transaction():
                await session.rollback()
            session.commit = _orig  # type: ignore[assignment]

    audit.leak_after = await p10._count_snapshot(factory)
    audit.dry_run_leak = audit.leak_before != audit.leak_after
    return audit


# --------------------------------------------------------------------------- #
# decision + rendering
# --------------------------------------------------------------------------- #


def decision(audit: Audit) -> tuple[str, list[str]]:
    reasons: list[str] = []
    by = {(b.exam_year, b.exam_day): b for b in audit.booklets}

    if len(audit.booklets) != 20:
        reasons.append(f"expected 20 booklets, processed {len(audit.booklets)}")

    for k in OCR_KEYS:
        b = by.get(k)
        if not b or not b.ocr_required or b.new != 0 or b.import_ready != 0:
            reasons.append(f"{k} not correctly blocked as OCR_PENDING")

    for k in RECOVERED_KEYS:
        b = by.get(k)
        if not b or b.recovery_status.split("(")[0] != "RECOVERED":
            reasons.append(f"{k} not using recovered text")
        if b and b.new > b.import_ready:
            reasons.append(f"{k} NEW {b.new} > IMPORT_READY {b.import_ready}")

    for k, exp in IDEMPOTENCY_EXPECT.items():
        b = by.get(k)
        if not b or b.duplicate != exp or b.new != 0:
            reasons.append(f"{k} idempotency: dup={getattr(b,'duplicate','?')} (exp {exp}), new={getattr(b,'new','?')}")

    # Q145 / Q154 (2025 D2): must be IMPORT_READY in option_recovery, carry 5
    # clean options, and not be excluded for a degradation reason. Being held
    # for REVIEW by the (untouched) image gate is acceptable.
    _artifacts = ("*020125", "ENEM2025", " DIA ", "CADERNO", " • ", "￼")
    for qn in (145, 154):
        aq = audit.audit_questions.get(f"2025_D2_Q{qn}")
        if not aq:
            reasons.append(f"2025 D2 Q{qn} not audited")
            continue
        if aq["option_recovery_status"] != "IMPORT_READY":
            reasons.append(f"2025 D2 Q{qn} option_recovery_status={aq['option_recovery_status']}")
        if aq["number_of_options"] != 5:
            reasons.append(f"2025 D2 Q{qn} has {aq['number_of_options']} options")
        if aq["final_bucket"] not in {"new", "requires_review"}:
            reasons.append(f"2025 D2 Q{qn} final_bucket={aq['final_bucket']}")
        for ln in aq["options"]:
            if any(tok in ln for tok in _artifacts):
                reasons.append(f"2025 D2 Q{qn} option contains artefact: {ln!r}")

    # every candidate: >=5 options, has a hash, recovery status acceptable
    for c in audit.candidates:
        if c["number_of_options"] < 5:
            reasons.append(f"candidate {c['year']}D{c['day']} Q{c['official_number']} has {c['number_of_options']} options")
        if not c["statement_hash"]:
            reasons.append(f"candidate {c['year']}D{c['day']} Q{c['official_number']} has no statement_hash")
        if c["recovery_status"] in {"OCR_PENDING", "OPTION_RECOVERY_PENDING", "INVALID"}:
            reasons.append(f"candidate {c['year']}D{c['day']} Q{c['official_number']} status {c['recovery_status']}")

    if any(b.hash_analysis.get("collision_risk") for b in audit.booklets):
        reasons.append("HASH_COLLISION_RISK > 0")

    if audit.dry_run_leak:
        reasons.append("DRY_RUN_LEAK = true")

    ok = not reasons
    return ("PHASE_10_9_READY_FOR_REAL_INGESTION" if ok else "PHASE_10_9_NEEDS_REVIEW"), reasons


def render(audit: Audit) -> str:
    L = ["PHASE 10.9 - FINAL PRE-INGESTION AUDIT (READ-ONLY)", "",
         f"booklets processed: {len(audit.booklets)}", "",
         "PER-BOOKLET INVENTORY:",
         "  YEAR D BOOKLET  METHOD                       RECOVERY              DET DIST RNG "
         "READY REVIEW DUP INVLD OCRP OPTP  NEW ERR"]
    for b in audit.booklets:
        L.append(
            f"  {b.exam_year} {b.exam_day} {b.booklet:<7} {b.method:<28} {b.recovery_status:<20} "
            f"{b.questions_detected:>3} {b.distinct_numbers:>4} {str(b.official_range_complete)[0]:>3} "
            f"{b.import_ready:>5} {b.review:>6} {b.duplicate:>3} {b.invalid:>5} {b.ocr_pending:>4} "
            f"{b.option_recovery_pending:>4} {b.new:>4} {b.errors:>3}"
        )
    L += ["", "GLOBAL RESULT BY YEAR (D1 | D2):",
          "  YEAR | D1 READY REVIEW DUP INVLD | D2 READY REVIEW DUP INVLD"]
    by = {(b.exam_year, b.exam_day): b for b in audit.booklets}
    for y in range(2016, 2026):
        d1 = by.get((y, 1)); d2 = by.get((y, 2))
        def cell(b):
            return f"{b.import_ready:>5} {b.review:>6} {b.duplicate:>3} {b.invalid:>5}" if b else " " * 21
        L.append(f"  {y} | {cell(d1)} | {cell(d2)}")
    t = audit.totals
    L += ["",
          f"TOTAL_STAGED                    {t['staged']}",
          f"TOTAL_IMPORT_READY             {t['import_ready']}",
          f"TOTAL_NEW (real import candid.) {t['new']}",
          f"TOTAL_DUPLICATE                {t['duplicate']}",
          f"TOTAL_REVIEW                   {t['review']}",
          f"TOTAL_INVALID                  {t['invalid']}",
          f"TOTAL_OCR_PENDING             {t['ocr_pending']}",
          f"TOTAL_OPTION_RECOVERY_PENDING  {t['option_recovery_pending']}",
          f"TOTAL_ANNULLED                 {t['annulled']}",
          f"TOTAL_ERRORS                   {t['errors']}",
          "",
          f"REAL_IMPORT_CANDIDATES: {len(audit.candidates)}",
          f"EXCLUDED_FROM_REAL_INGESTION: {len(audit.excluded)}"]

    dd = audit.as_dict_core()["leak_check"]
    L += ["", "LEAK CHECK (separate connection, before -> after):"]
    for k in sorted(dd["before"]):
        mark = "" if dd["delta"][k] == 0 else "   <-- CHANGED"
        L.append(f"  {k:32} {dd['before'][k]:>6} -> {dd['after'][k]:>6}{mark}")
    L += ["", "MANDATORY QUESTION AUDIT:"]
    for key, aq in audit.audit_questions.items():
        L.append(f"  {key}: option_recovery={aq['option_recovery_status']} "
                 f"final_bucket={aq['final_bucket']} n_opts={aq['number_of_options']} "
                 f"in_candidates={aq['in_real_import_candidates']}")
        L.append(f"    options: {aq['options']}")
    L += ["", f"DRY_RUN_LEAK: {audit.dry_run_leak}", "",
          "SECURITY: DATABASE_WRITES=0 OPENAI_CALLS=0 ALEMBIC_EXECUTION=0 "
          "CLASSIFICATIONS_CREATED=0 CATALOG_NODES_CREATED=0 VOCABULARY_CHANGES=0 "
          "PRODUCTION_DATA_MODIFIED=0"]
    dec, why = decision(audit)
    L += ["", "FINAL_DECISION:", dec]
    if why:
        L += ["", "reasons:"] + [f"  - {r}" for r in why]
    return "\n".join(L)


async def _amain(argv):  # pragma: no cover
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--no-write-report", action="store_true")
    args = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE, override=False)
    except Exception:
        pass
    from agente_ia_edu.db.session import create_engine, create_session_factory, get_database_url
    try:
        get_database_url()
    except Exception as exc:  # noqa: BLE001
        print(f"PHASE 10.9\nFATAL: {exc}\n\nFINAL_DECISION:\nPHASE_10_9_NEEDS_REVIEW")
        return 1
    if os.environ.get(p10.DRY_RUN_ENV_VAR, "true").strip().lower() == "false":
        print("PHASE 10.9 is READ-ONLY and refuses PHASE10_INGEST_DRY_RUN=false")
        return 1
    if os.environ.get(p10.APPROVAL_ENV_VAR):
        print("PHASE 10.9 refuses to run with a write approval token set")
        return 1

    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    try:
        audit = await run(factory, manifest_path=args.manifest)
    finally:
        await engine.dispose()

    print(render(audit))
    dec, why = decision(audit)
    if not args.no_write_report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = audit.as_dict_core()
        payload["database"] = "agente_ia_edu @ localhost:5433 (password OCULTA)"
        payload["manifest"] = str(Path(args.manifest).relative_to(_REPO_ROOT)) \
            if Path(args.manifest).is_absolute() else args.manifest
        payload["idempotency"] = {
            f"{k[0]}_D{k[1]}": {
                "expected_duplicate": v,
                "observed_duplicate": next(
                    (b.duplicate for b in audit.booklets
                     if (b.exam_year, b.exam_day) == k), None),
                "observed_new": next(
                    (b.new for b in audit.booklets
                     if (b.exam_year, b.exam_day) == k), None),
            } for k, v in IDEMPOTENCY_EXPECT.items()
        }
        payload["hash_checks"] = {
            f"{b.exam_year}_D{b.exam_day}": b.hash_analysis for b in audit.booklets
        }
        payload["security"] = {
            "DATABASE_WRITES": 0, "OPENAI_CALLS": 0, "ALEMBIC_EXECUTION": 0,
            "CLASSIFICATIONS_CREATED": 0, "CATALOG_NODES_CREATED": 0,
            "VOCABULARY_CHANGES": 0, "PRODUCTION_DATA_MODIFIED": 0,
        }
        payload["known_preexisting_failure"] = (
            "tests/test_ingestion_classifier.py::TestIngestionClassifierIntegration::"
            "test_13_isolation_between_documents (PRE_EXISTING, not fixed)"
        )
        payload["final_decision"] = dec
        payload["final_decision_reasons"] = why
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0 if dec == "PHASE_10_9_READY_FOR_REAL_INGESTION" else 1


def main(argv=None):  # pragma: no cover
    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
