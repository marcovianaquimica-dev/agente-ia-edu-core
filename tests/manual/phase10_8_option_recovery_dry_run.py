"""PHASE 10.8 - option-level recovery + validation + DRY-RUN (no real writes).

Builds on PHASE 10.7. For the four RECOVERED booklets (ENEM 2024 D1/D2,
2025 D1/D2) the text layer fed to the ingestion pipeline now comes from
``option_recovery.recover_option_layout`` (PyMuPDF ``dict`` + ``drawings``):
headers / footers / barcodes / watermarks stripped, options re-threaded, and a
stacked "numerator / denominator" reconstructed to ``n/d`` ONLY when a fraction
bar proves it. 2016-2020 stay on the untouched native ``pypdf`` path; 2021 is
OCR_PENDING and 2022/2023 REVIEW_PENDING - none are processed here.

DRY-RUN only. Outer transaction + commit->flush + rollback (PHASE 10.1 harness),
leak snapshot in a separate connection, DATABASE_WRITES = 0.
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

from sqlalchemy import select  # noqa: E402

from agente_ia_edu.db.models import IngestionQuestion, QuestionVersion  # noqa: E402
from agente_ia_edu.services.ingestion import IngestionService  # noqa: E402
from agente_ia_edu.services.ingestion_parser import PdfParser  # noqa: E402
from agente_ia_edu.services.option_recovery import recover_option_layout  # noqa: E402
from agente_ia_edu.services.question_bank_importer import QuestionBankImporter  # noqa: E402

import phase10_ingest_batch as p10  # noqa: E402

PHASE = "10.8"
DEFAULT_MANIFEST = _HERE / "phase10_enem_manifest_2016_2025.json"
DEFAULT_REPORT = _REPO_ROOT / "var" / "inep-pilot" / "phase10_8_option_recovery_dry_run_report.json"
EXPECTED = 90

NATIVE_KEYS = {(y, d) for y in range(2016, 2021) for d in (1, 2)}
RECOVERED_KEYS = {(2024, 1), (2024, 2), (2025, 1), (2025, 2)}
SKIP_KEYS = {
    (2021, 1): "OCR_PENDING", (2021, 2): "OCR_PENDING",
    (2022, 1): "REVIEW_PENDING", (2022, 2): "REVIEW_PENDING",
    (2023, 1): "REVIEW_PENDING", (2023, 2): "REVIEW_PENDING",
}
_LEAK_TABLES = dict(p10._LEAK_TABLES)

# specific questions the phase mandates auditing
AUDIT_QUESTIONS = {(2025, 2): [145, 154]}


@dataclass
class BookletResult:
    exam_year: int
    exam_day: int
    booklet: str
    extraction_method: str
    questions_detected: int = 0
    distinct_numbers: int = 0
    official_range_complete: bool = False
    questions_import_ready: int = 0
    option_recovery_pending: int = 0
    invalid: int = 0
    pending_numbers: list[int] = field(default_factory=list)
    invalid_numbers: list[int] = field(default_factory=list)
    fraction_numbers: list[int] = field(default_factory=list)
    staged_questions: int = 0
    imported_new: int = 0
    duplicates: int = 0
    requires_review: int = 0
    annulled: int = 0
    parser_invalid: int = 0
    errors: int = 0
    error_detail: list[str] = field(default_factory=list)
    hash_analysis: dict = field(default_factory=dict)
    audit: list[dict] = field(default_factory=list)
    status: str = "PENDING"

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["error_detail"] = self.error_detail[:20]
        return d


def _method_for(y: int, d: int) -> str | None:
    if (y, d) in RECOVERED_KEYS:
        return "recovered_option_layout"
    if (y, d) in NATIVE_KEYS:
        return "native_pypdf"
    return None


async def _existing_hashes(factory, hashes: list[str]) -> set[str]:
    if not hashes:
        return set()
    async with factory() as s:
        rows = await s.scalars(
            select(QuestionVersion.content_hash).where(QuestionVersion.content_hash.in_(hashes))
        )
        return set(rows.all())


def _native_option_text(pdf: Path, number: int) -> str:
    try:
        parsed = PdfParser.parse_file(pdf)
    except Exception:  # noqa: BLE001
        return "<native parse failed>"
    for q in parsed.questions:
        if q.question_number == number:
            return (q.alternatives_text or "").replace("\n", " | ")
    return "<not detected by native pypdf>"


async def process_booklet(session, factory, entry: dict, *, dry_run: bool) -> BookletResult:
    y, d = int(entry["exam_year"]), int(entry["exam_day"])
    method = _method_for(y, d)
    res = BookletResult(y, d, str(entry["booklet"]), method or "skipped")
    lo, hi = (1, 90) if d == 1 else (91, 180)
    try:
        proof = p10._resolve_pdf(entry["proof_pdf"])
        gabarito = p10._resolve_pdf(entry["answer_key_pdf"])
        answer_key = PdfParser.parse_answer_key(gabarito)

        parsed_override = None
        recovered = None
        if method == "recovered_option_layout":
            recovered = recover_option_layout(proof)
            res.questions_import_ready = len(recovered.import_ready)
            res.option_recovery_pending = len(recovered.option_recovery_pending)
            res.invalid = len(recovered.invalid)
            res.pending_numbers = recovered.option_recovery_pending
            res.invalid_numbers = recovered.invalid
            res.fraction_numbers = sorted(
                {q.number for q in recovered.questions
                 if any(o.fraction_reconstructed for o in q.options)}
            )
            parsed_override = PdfParser.parse_file(proof, page_texts=recovered.page_texts)
            src = parsed_override
            # mandated per-question audit (native vs recovered vs final)
            by_num = {q.number: q for q in recovered.questions}
            for qn in AUDIT_QUESTIONS.get((y, d), []):
                rq = by_num.get(qn)
                final = (
                    [f"{o.label}) {o.text}" for o in rq.options] if rq else []
                )
                res.audit.append({
                    "question": qn,
                    "native": _native_option_text(proof, qn),
                    "recovered": final,
                    "final_status": rq.status if rq else "NOT_FOUND",
                    "final_validated": rq.status == "IMPORT_READY" if rq else False,
                    "reasons": rq.reasons if rq else ["not found"],
                })
        else:
            src = PdfParser.parse_file(proof)

        nums = sorted(q.question_number for q in src.questions)
        res.questions_detected = len(src.questions)
        res.distinct_numbers = len(set(nums))
        res.official_range_complete = set(range(lo, hi + 1)).issubset(set(nums))
        if method == "native_pypdf":
            res.questions_import_ready = res.distinct_numbers  # detection == readiness proxy here

        # hash / idempotency (read-only, separate session)
        stmt_hashes = [
            hashlib.sha256(q.statement_text.encode("utf-8")).hexdigest()
            for q in src.questions if q.statement_text and q.statement_text.strip()
        ]
        pre = await _existing_hashes(factory, stmt_hashes)
        res.hash_analysis = {
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
        res.staged_questions = len(staged_ids)
        importer = QuestionBankImporter(session)
        for qid in staged_ids:
            try:
                result = await importer.import_question(qid)
            except Exception as exc:  # noqa: BLE001
                res.errors += 1
                res.error_detail.append(f"{qid}: {type(exc).__name__}: {exc}")
                if dry_run and not session.in_transaction():
                    await session.begin()
                continue
            b = p10._classify_result(result)
            if b == "new":
                res.imported_new += 1
            elif b == "duplicate":
                res.duplicates += 1
            elif b == "annulled":
                res.annulled += 1
            elif b == "requires_review":
                res.requires_review += 1
            else:
                res.parser_invalid += 1
        res.status = "OK" if res.errors == 0 else "OK_WITH_ERRORS"
    except Exception as exc:  # noqa: BLE001
        res.status = "ERROR"
        res.errors += 1
        res.error_detail.append(f"booklet: {type(exc).__name__}: {exc}")
        if dry_run and not session.in_transaction():
            await session.begin()
    return res


@dataclass
class Report:
    phase: str = PHASE
    dry_run: bool = True
    processed: list[BookletResult] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    leak_before: dict = field(default_factory=dict)
    leak_after: dict = field(default_factory=dict)
    dry_run_leak: bool = False

    def as_dict(self) -> dict:
        return {
            "phase": self.phase, "dry_run": self.dry_run,
            "processed": [b.as_dict() for b in self.processed],
            "skipped": self.skipped,
            "leak_check": {
                "tables": sorted(_LEAK_TABLES),
                "before": self.leak_before, "after": self.leak_after,
                "delta": {k: self.leak_after.get(k, 0) - self.leak_before.get(k, 0)
                          for k in self.leak_before},
                "dry_run_leak": self.dry_run_leak,
            },
            "security": {
                "DATABASE_WRITES": 0, "OPENAI_CALLS": 0, "ALEMBIC_EXECUTION": 0,
                "CLASSIFICATIONS_CREATED": 0, "CATALOG_NODES_CREATED": 0,
                "VOCABULARY_CHANGES": 0, "PRODUCTION_DATA_MODIFIED": 0,
            },
            "final_decision": _decision(self),
        }


async def run(factory, *, manifest_path=DEFAULT_MANIFEST) -> Report:
    manifest = p10.load_manifest(manifest_path)
    report = Report()
    eligible = []
    for e in manifest["booklets"]:
        k = (int(e["exam_year"]), int(e["exam_day"]))
        if k in SKIP_KEYS:
            report.skipped.append({"exam_year": k[0], "exam_day": k[1],
                                   "booklet": e["booklet"], "status": SKIP_KEYS[k]})
        else:
            eligible.append(e)

    report.leak_before = await p10._count_snapshot(factory)
    async with factory() as session:
        _orig = session.commit
        session.commit = session.flush  # type: ignore[assignment]
        await session.begin()
        try:
            for e in eligible:
                report.processed.append(await process_booklet(session, factory, e, dry_run=True))
        finally:
            if session.in_transaction():
                await session.rollback()
            session.commit = _orig  # type: ignore[assignment]
    report.leak_after = await p10._count_snapshot(factory)
    report.dry_run_leak = report.leak_before != report.leak_after
    return report


def _decision(report: Report) -> str:
    reasons = []
    by = {(b.exam_year, b.exam_day): b for b in report.processed}
    for k in RECOVERED_KEYS:
        b = by.get(k)
        if not b:
            reasons.append(f"{k} missing"); continue
        if b.distinct_numbers != 90 or not b.official_range_complete:
            reasons.append(f"{k} detection {b.distinct_numbers}/90 range_ok={b.official_range_complete}")
        if b.extraction_method != "recovered_option_layout":
            reasons.append(f"{k} wrong method {b.extraction_method}")
        # NEW is the intended outcome for cleanly-recovered options - but every
        # NEW must be backed by an option_recovery IMPORT_READY verdict (no
        # guessed / artefact-bearing option may enter as NEW).
        if b.imported_new > b.questions_import_ready:
            reasons.append(
                f"{k} NEW={b.imported_new} exceeds option_recovery IMPORT_READY={b.questions_import_ready}"
            )
        if b.hash_analysis.get("collision_risk"):
            reasons.append(f"{k} content_hash collision")
    # Q145 / Q154 must be import-ready with clean options
    d2 = by.get((2025, 2))
    aud = {a["question"]: a for a in (d2.audit if d2 else [])}
    for qn in (145, 154):
        a = aud.get(qn)
        if not a or not a["final_validated"]:
            reasons.append(f"Q{qn} not IMPORT_READY ({a['final_status'] if a else 'absent'})")
    # 2020 D2 idempotent
    d20 = by.get((2020, 2))
    if not d20 or d20.imported_new != 0 or d20.duplicates != 24:
        reasons.append(
            f"2020 D2 not idempotent (NEW={getattr(d20,'imported_new','?')}, DUP={getattr(d20,'duplicates','?')})"
        )
    for k in NATIVE_KEYS:
        b = by.get(k)
        if b and b.imported_new > 0:
            reasons.append(f"{k} native produced NEW={b.imported_new} (regression)")
    if report.dry_run_leak:
        reasons.append("DRY_RUN_LEAK true")
    if reasons:
        return "PHASE_10_8_NEEDS_REVIEW  # " + "; ".join(reasons)
    return "PHASE_10_8_OPTION_RECOVERY_COMPLETE"


def render(report: Report) -> str:
    L = ["PHASE 10.8 - OPTION-LEVEL RECOVERY DRY-RUN", "",
         f"DRY_RUN: TRUE   processed={len(report.processed)}   skipped={len(report.skipped)}", "",
         "BOOKLET RESULTS:",
         "  YEAR D  BOOKLET  METHOD                  DETECT DISTINCT RANGE  READY PEND INVLD | "
         "STAGED NEW DUP REVIEW ANNUL PINVLD ERR  STATUS"]
    for b in report.processed:
        L.append(
            f"  {b.exam_year} {b.exam_day}  {b.booklet:<7} {b.extraction_method:<22} "
            f"{b.questions_detected:>6} {b.distinct_numbers:>8} {str(b.official_range_complete)[:5]:>5} "
            f"{b.questions_import_ready:>5} {b.option_recovery_pending:>4} {b.invalid:>5} | "
            f"{b.staged_questions:>6} {b.imported_new:>3} {b.duplicates:>3} {b.requires_review:>6} "
            f"{b.annulled:>5} {b.parser_invalid:>6} {b.errors:>3}  {b.status}"
        )
    L += ["", "SKIPPED:"]
    for s in report.skipped:
        L.append(f"  {s['exam_year']} D{s['exam_day']} {s['booklet']:<7} -> {s['status']}")
    L += ["", "MANDATORY AUDIT (2025 D2):"]
    for b in report.processed:
        for a in b.audit:
            L.append(f"  Q{a['question']}  final_status={a['final_status']}  validated={a['final_validated']}")
            L.append(f"    NATIVE   : {a['native'][:110]}")
            L.append(f"    RECOVERED: {a['recovered']}")
            if a["reasons"]:
                L.append(f"    reasons  : {a['reasons']}")
    L += ["", "HASH / IDEMPOTENCY:"]
    for b in report.processed:
        h = b.hash_analysis
        L.append(f"  {b.exam_year} D{b.exam_day} {b.booklet:<7} ({b.extraction_method}): "
                 f"hashed={h.get('statements_hashed')} distinct={h.get('distinct_hashes')} "
                 f"already_in_QV={h.get('already_in_question_versions')} collision_risk={h.get('collision_risk')}")
    dd = report.as_dict()["leak_check"]
    L += ["", "LEAK CHECK (separate connection):"]
    for k in sorted(dd["before"]):
        mark = "" if dd["delta"][k] == 0 else "   <-- CHANGED"
        L.append(f"  {k:32} {dd['before'][k]:>6} -> {dd['after'][k]:>6}{mark}")
    L += ["", f"DRY_RUN_LEAK: {report.dry_run_leak}", "",
          "SECURITY: DATABASE_WRITES=0 OPENAI_CALLS=0 ALEMBIC_EXECUTION=0 PRODUCTION_DATA_MODIFIED=0",
          "", "FINAL_DECISION:", _decision(report)]
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
        print(f"PHASE 10.8\nFATAL: {exc}\n\nFINAL_DECISION:\nPHASE_10_8_NEEDS_REVIEW")
        return 1
    if os.environ.get(p10.DRY_RUN_ENV_VAR, "true").strip().lower() == "false":
        print("PHASE 10.8 refuses PHASE10_INGEST_DRY_RUN=false")
        return 1
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    try:
        report = await run(factory, manifest_path=args.manifest)
    finally:
        await engine.dispose()
    print(render(report))
    if not args.no_write_report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0 if not report.dry_run_leak else 1


def main(argv=None):  # pragma: no cover
    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
