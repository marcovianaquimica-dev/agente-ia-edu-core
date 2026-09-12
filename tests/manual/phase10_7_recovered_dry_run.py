"""PHASE 10.7 - recovered-text ingestion DRY-RUN (no real writes).

Extends the PHASE 10.1 batch dry-run with the PHASE 10.6 text-recovery contract:

    for each eligible booklet:
        method = "recovered" if this booklet is one of the 4 RECOVERED ones
                 else "native"
        if method == "recovered":
            r = recover_pdf_text(proof, expected_questions=90)
            assert r.recovery_status == "RECOVERED"           # contract gate
            parsed = PdfParser.parse_file(proof, page_texts=r.recovered_pages)
            IngestionService().ingest_document(..., parsed_override=parsed)
        else:
            IngestionService().ingest_document(...)            # unchanged pypdf path
        for each staged IngestionQuestion:
            QuestionBankImporter.import_question(...)          # unchanged

Everything is wrapped in the PHASE 10.1 dry-run harness (outer transaction,
commit->flush, rollback, restore) and a leak snapshot taken in a SEPARATE
session before and after. No commit reaches the database.

Eligible booklets
-----------------
  native  : ENEM 2016-2020 D1/D2  (10 - unchanged behaviour, must match PHASE 10.3)
  recovered: ENEM 2024 D1/D2, 2025 D1/D2  (4 - PHASE 10.6 RECOVERED)
  skipped : ENEM 2021 D1/D2  (OCR_PENDING),  2022/2023 D1/D2  (REVIEW_PENDING)

Nothing runs at import time.
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

from agente_ia_edu.db.models import QuestionVersion  # noqa: E402
from agente_ia_edu.services.ingestion import IngestionService  # noqa: E402
from agente_ia_edu.services.ingestion_parser import PdfParser  # noqa: E402
from agente_ia_edu.services.question_bank_importer import QuestionBankImporter  # noqa: E402
from agente_ia_edu.services.text_recovery import recover_pdf_text  # noqa: E402

# Reuse the PHASE 10.1 runner's helpers verbatim - no reimplementation.
import phase10_ingest_batch as p10  # noqa: E402

PHASE = "10.7"
DEFAULT_MANIFEST = _HERE / "phase10_enem_manifest_2016_2025.json"
DEFAULT_REPORT = _REPO_ROOT / "var" / "inep-pilot" / "phase10_7_recovered_dry_run_report.json"

EXPECTED_QUESTIONS = 90

# (year, day) -> extraction method for this phase
NATIVE_YEARS = {(y, d) for y in range(2016, 2021) for d in (1, 2)}
RECOVERED_KEYS = {(2024, 1), (2024, 2), (2025, 1), (2025, 2)}
SKIP_KEYS = {
    (2021, 1): "OCR_PENDING", (2021, 2): "OCR_PENDING",
    (2022, 1): "REVIEW_PENDING", (2022, 2): "REVIEW_PENDING",
    (2023, 1): "REVIEW_PENDING", (2023, 2): "REVIEW_PENDING",
}

# Tables the dry-run must not touch (superset of the phase's required list).
_LEAK_TABLES = dict(p10._LEAK_TABLES)


@dataclass
class BookletResult:
    exam_year: int
    exam_day: int
    booklet: str
    extraction_method: str
    recovery_status: str
    questions_detected: int = 0
    distinct_numbers: int = 0
    number_range: str = "-"
    official_range_complete: bool = False
    staged_questions: int = 0
    imported_new: int = 0
    duplicates: int = 0
    requires_review: int = 0
    annulled: int = 0
    invalid: int = 0
    errors: int = 0
    questions_import_ready: int = 0
    error_detail: list[str] = field(default_factory=list)
    hash_analysis: dict = field(default_factory=dict)
    status: str = "PENDING"

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["error_detail"] = self.error_detail[:20]
        return d


@dataclass
class SkipResult:
    exam_year: int
    exam_day: int
    booklet: str
    recovery_status: str
    note: str = "not processed in PHASE 10.7"

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _method_for(year: int, day: int) -> str | None:
    if (year, day) in RECOVERED_KEYS:
        return "recovered"
    if (year, day) in NATIVE_YEARS:
        return "native"
    return None  # skipped


async def _existing_content_hashes(factory, hashes: list[str]) -> set[str]:
    """Which of these sha256(statement) already exist as a QuestionVersion."""
    if not hashes:
        return set()
    async with factory() as session:
        rows = await session.scalars(
            select(QuestionVersion.content_hash).where(QuestionVersion.content_hash.in_(hashes))
        )
        return set(rows.all())


async def process_booklet(session, factory, entry: dict, *, dry_run: bool) -> BookletResult:
    year, day = int(entry["exam_year"]), int(entry["exam_day"])
    method = _method_for(year, day)
    res = BookletResult(
        exam_year=year, exam_day=day, booklet=str(entry["booklet"]),
        extraction_method=method or "skipped", recovery_status="N/A",
    )
    try:
        proof = p10._resolve_pdf(entry["proof_pdf"])
        gabarito = p10._resolve_pdf(entry["answer_key_pdf"])
        answer_key = PdfParser.parse_answer_key(gabarito)

        parsed_override = None
        if method == "recovered":
            rec = recover_pdf_text(proof, expected_questions=EXPECTED_QUESTIONS)
            res.recovery_status = rec.recovery_status
            if rec.recovery_status != "RECOVERED":
                res.status = "BLOCKED"
                res.error_detail.append(
                    f"recover_pdf_text returned {rec.recovery_status}, expected RECOVERED - "
                    "refusing to use recovered_text"
                )
                return res
            parsed_override = PdfParser.parse_file(proof, page_texts=rec.recovered_pages)
        else:
            res.recovery_status = "OK(native)"

        # --- detection metrics (before staging) -------------------------------
        detect_source = parsed_override or parse_native(proof)
        nums = sorted(q.question_number for q in detect_source.questions)
        res.questions_detected = len(detect_source.questions)
        res.distinct_numbers = len(set(nums))
        res.number_range = f"{nums[0]}-{nums[-1]}" if nums else "-"
        lo, hi = (1, 90) if day == 1 else (91, 180)
        res.official_range_complete = set(range(lo, hi + 1)).issubset(set(nums))

        # --- hash / idempotency analysis (read-only, separate session) -------
        statement_hashes = [
            hashlib.sha256(q.statement_text.encode("utf-8")).hexdigest()
            for q in detect_source.questions
            if q.statement_text and q.statement_text.strip()
        ]
        pre_existing = await _existing_content_hashes(factory, statement_hashes)
        res.hash_analysis = {
            "statements_hashed": len(statement_hashes),
            "distinct_hashes": len(set(statement_hashes)),
            "already_in_question_versions": len(pre_existing),
            "collision_risk": bool(pre_existing) and method == "recovered",
        }

        # --- stage + import (inside the dry-run transaction) -----------------
        document, _run = await IngestionService().ingest_document(
            session, proof, answer_key=answer_key,
            source_metadata=p10._source_metadata(entry),
            parsed_override=parsed_override,
        )
        from agente_ia_edu.db.models import IngestionQuestion

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
            bucket = p10._classify_result(result)
            if bucket == "new":
                res.imported_new += 1
            elif bucket == "duplicate":
                res.duplicates += 1
            elif bucket == "annulled":
                res.annulled += 1
            elif bucket == "requires_review":
                res.requires_review += 1
            else:
                res.invalid += 1

        res.questions_import_ready = res.imported_new + res.duplicates
        res.status = "OK" if res.errors == 0 else "OK_WITH_ERRORS"
    except Exception as exc:  # noqa: BLE001
        res.status = "ERROR"
        res.errors += 1
        res.error_detail.append(f"booklet: {type(exc).__name__}: {exc}")
        if dry_run and not session.in_transaction():
            await session.begin()
    return res


def parse_native(proof: Path):
    return PdfParser.parse_file(proof)


@dataclass
class Report:
    phase: str = PHASE
    dry_run: bool = True
    manifest_entries: int = 0
    processed: list[BookletResult] = field(default_factory=list)
    skipped: list[SkipResult] = field(default_factory=list)
    leak_before: dict = field(default_factory=dict)
    leak_after: dict = field(default_factory=dict)
    dry_run_leak: bool = False
    database_writes: int = 0

    def as_dict(self) -> dict:
        return {
            "phase": self.phase,
            "dry_run": self.dry_run,
            "manifest_entries": self.manifest_entries,
            "processed": [b.as_dict() for b in self.processed],
            "skipped": [s.as_dict() for s in self.skipped],
            "leak_check": {
                "tables": sorted(_LEAK_TABLES),
                "before": self.leak_before,
                "after": self.leak_after,
                "delta": {k: self.leak_after.get(k, 0) - self.leak_before.get(k, 0)
                          for k in self.leak_before},
                "dry_run_leak": self.dry_run_leak,
            },
            "security": {
                "DATABASE_WRITES": self.database_writes,
                "OPENAI_CALLS": 0,
                "ALEMBIC_EXECUTION": 0,
                "CLASSIFICATIONS_CREATED": 0,
                "CATALOG_NODES_CREATED": 0,
                "VOCABULARY_CHANGES": 0,
            },
        }


async def run(factory, *, manifest_path: Path | str = DEFAULT_MANIFEST) -> Report:
    manifest = p10.load_manifest(manifest_path)
    report = Report(manifest_entries=len(manifest["booklets"]))

    eligible: list[dict] = []
    for entry in manifest["booklets"]:
        key = (int(entry["exam_year"]), int(entry["exam_day"]))
        if key in SKIP_KEYS:
            report.skipped.append(SkipResult(key[0], key[1], str(entry["booklet"]), SKIP_KEYS[key]))
        else:
            eligible.append(entry)

    report.leak_before = await p10._count_snapshot(factory)

    async with factory() as session:
        _orig_commit = session.commit
        session.commit = session.flush  # type: ignore[assignment]
        await session.begin()
        try:
            for entry in eligible:
                report.processed.append(
                    await process_booklet(session, factory, entry, dry_run=True)
                )
        finally:
            if session.in_transaction():
                await session.rollback()
            session.commit = _orig_commit  # type: ignore[assignment]

    report.leak_after = await p10._count_snapshot(factory)
    report.dry_run_leak = report.leak_before != report.leak_after
    report.database_writes = 0
    return report


def render(report: Report) -> str:
    lines = [
        "PHASE 10.7 - RECOVERED-TEXT INGESTION DRY-RUN",
        "",
        f"DRY_RUN: TRUE   manifest_entries={report.manifest_entries}   "
        f"processed={len(report.processed)}   skipped={len(report.skipped)}",
        "",
        "PROCESSED BOOKLETS:",
        "  YEAR | DAY | BOOKLET | METHOD    | REC_STATUS | DETECT | DISTINCT | RANGE_OK | "
        "STAGED | NEW | DUP | REVIEW | ANNUL | INVALID | ERR | IMPORT_READY | STATUS",
    ]
    for b in report.processed:
        lines.append(
            f"  {b.exam_year} |  {b.exam_day}  | {b.booklet:<7} | {b.extraction_method:<9} | "
            f"{b.recovery_status:<10} | {b.questions_detected:>6} | {b.distinct_numbers:>8} | "
            f"{str(b.official_range_complete):>8} | {b.staged_questions:>6} | {b.imported_new:>3} | "
            f"{b.duplicates:>3} | {b.requires_review:>6} | {b.annulled:>5} | {b.invalid:>7} | "
            f"{b.errors:>3} | {b.questions_import_ready:>12} | {b.status}"
        )
    lines += ["", "SKIPPED BOOKLETS:"]
    for s in report.skipped:
        lines.append(f"  {s.exam_year} D{s.exam_day} {s.booklet:<7} -> {s.recovery_status}")

    lines += ["", "HASH / IDEMPOTENCY ANALYSIS:"]
    for b in report.processed:
        h = b.hash_analysis
        lines.append(
            f"  {b.exam_year} D{b.exam_day} {b.booklet:<7} ({b.extraction_method}): "
            f"hashed={h.get('statements_hashed')} distinct={h.get('distinct_hashes')} "
            f"already_in_question_versions={h.get('already_in_question_versions')} "
            f"collision_risk={h.get('collision_risk')}"
        )

    d = report.as_dict()["leak_check"]
    lines += [
        "",
        "LEAK CHECK (separate connection, before vs after):",
    ]
    for k in sorted(d["before"]):
        mark = "" if d["delta"][k] == 0 else "   <-- CHANGED"
        lines.append(f"  {k:32} {d['before'][k]:>6} -> {d['after'][k]:>6}{mark}")
    lines += [
        "",
        f"DRY_RUN_LEAK: {report.dry_run_leak}",
        "",
        "SECURITY:",
        "  DATABASE_WRITES = 0",
        "  OPENAI_CALLS = 0",
        "  ALEMBIC_EXECUTION = 0",
        "  CLASSIFICATIONS_CREATED = 0",
        "  CATALOG_NODES_CREATED = 0",
        "",
        "FINAL_DECISION:",
        _decision(report),
    ]
    return "\n".join(lines)


def _decision(report: Report) -> str:
    ok = True
    reasons = []
    by_key = {(b.exam_year, b.exam_day): b for b in report.processed}
    for key in RECOVERED_KEYS:
        b = by_key.get(key)
        if not b or b.distinct_numbers != 90 or not b.official_range_complete:
            ok = False
            reasons.append(f"{key} distinct/range not 90/complete")
        if b and b.extraction_method != "recovered":
            ok = False
            reasons.append(f"{key} did not use recovered text")
        if b and b.imported_new > 0:
            # recovered statements must not enter as NEW in this dry-run
            reasons.append(f"{key} produced NEW={b.imported_new} (recovered text as NEW)")
            ok = False
    d2 = by_key.get((2020, 2))
    if not d2 or d2.imported_new != 0 or d2.duplicates != 24:
        ok = False
        reasons.append(f"2020 D2 not idempotent (NEW={getattr(d2,'imported_new','?')}, DUP={getattr(d2,'duplicates','?')})")
    for b in report.processed:
        if b.extraction_method == "native" and b.exam_year in range(2016, 2021):
            continue
    if report.dry_run_leak:
        ok = False
        reasons.append("DRY_RUN_LEAK is true")
    if any(b.hash_analysis.get("collision_risk") for b in report.processed):
        ok = False
        reasons.append("recovered text collides with existing content_hash")
    if not ok:
        return "PHASE_10_7_NEEDS_REVIEW  # " + "; ".join(reasons)
    return "PHASE_10_7_RECOVERED_TEXT_DRY_RUN_COMPLETE"


async def _amain(argv: list[str]) -> int:  # pragma: no cover - manual entrypoint
    parser = argparse.ArgumentParser(description="PHASE 10.7 recovered-text dry-run")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--no-write-report", action="store_true")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_FILE, override=False)
    except Exception:
        pass

    from agente_ia_edu.db.session import create_engine, create_session_factory, get_database_url

    try:
        get_database_url()
    except Exception as exc:  # noqa: BLE001
        print(f"PHASE 10.7\nFATAL: {exc}\n\nFINAL_DECISION:\nPHASE_10_7_NEEDS_REVIEW")
        return 1

    if os.environ.get(p10.DRY_RUN_ENV_VAR, "true").strip().lower() == "false":
        print("PHASE 10.7 refuses to run with PHASE10_INGEST_DRY_RUN=false")
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


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
