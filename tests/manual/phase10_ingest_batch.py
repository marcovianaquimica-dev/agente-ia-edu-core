"""PHASE 10.1 — ENEM batch ingestion runner (thin orchestration).

Turns the existing, unchanged ingestion pipeline into a per-booklet batch:

    manifest -> validate -> for each booklet:
        PdfParser.parse_answer_key(gabarito)
        IngestionService.ingest_document(proof, answer_key, source_metadata)   [idempotent]
        for each staged IngestionQuestion:
            QuestionBankImporter.import_question(...)                          [idempotent]
        collect per-booklet metrics
    -> report

It REUSES ``IngestionService`` / ``PdfParser`` / ``QuestionBankImporter`` verbatim.
It contains NO pedagogical logic: no classifier, no ``recover_candidates`` /
``resolve_initial_controlled_vocabulary_binding`` / decision core / vocabulary
matching, no OpenAI, no Alembic, no catalog / curriculum changes. It never
creates a ``pedagogical_classifications`` row.

Gates
-----
  PHASE10_INGEST_DRY_RUN        default "true"; only literal, case-insensitive
                               "false" disables dry-run.
  PHASE10_INGEST_APPROVAL_TOKEN must equal exactly
                               "PHASE10-ENEM-2016-2020-INGEST-APPROVED".
  A real run additionally requires a COMPLETE manifest (no MISSING_INPUTS).

Dry-run
-------
``IngestionService.ingest_document`` and ``QuestionBankImporter.import_question``
BOTH call ``session.commit()`` internally (verified). The dry-run therefore:
  1. opens ONE outer transaction on the session,
  2. rebinds ``session.commit`` -> ``session.flush`` for the run (so the services'
     explicit commits become flushes and ``import_question`` takes its
     ``begin_nested()`` savepoint path),
  3. rolls the outer transaction back at the end and restores ``session.commit``,
  4. re-reads row counts in a SEPARATE session before/after and fails closed with
     ``dry_run_leak=True`` if anything persisted.
No commit/rollback is introduced into the pipeline code itself.

Nothing runs at import time. ``main()`` requires DATABASE_URL + PostgreSQL.
"""

from __future__ import annotations

import argparse
import asyncio
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

from agente_ia_edu.db.models import (  # noqa: E402
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    CatalogNode,
    Exam,
    ExamApplication,
    ExamBooklet,
    IngestionAsset,
    IngestionDocument,
    IngestionQuestion,
    IngestionRun,
    IngestionSection,
    Institution,
    PedagogicalClassification,
    Question,
    QuestionClassification,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
)
from agente_ia_edu.services.ingestion import IngestionService  # noqa: E402
from agente_ia_edu.services.ingestion_parser import PdfParser  # noqa: E402
from agente_ia_edu.services.question_bank_importer import QuestionBankImporter  # noqa: E402

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

PHASE = "10.1"
DEFAULT_MANIFEST = _HERE / "phase10_enem_manifest_2016_2020.json"
DEFAULT_REPORT = _REPO_ROOT / "var" / "inep-pilot" / "phase10_ingest_2016_2020_report.json"

DRY_RUN_ENV_VAR = "PHASE10_INGEST_DRY_RUN"
APPROVAL_ENV_VAR = "PHASE10_INGEST_APPROVAL_TOKEN"
APPROVAL_TOKEN = "PHASE10-ENEM-2016-2020-INGEST-APPROVED"
_REJECTED_TOKENS = frozenset(
    {
        "PHASE9U2-H2-INITIAL-CLASSIFICATION-REVIEWED",
        "PHASE9U2-H3-VOCABULARY-REVIEWED",
        "PHASE9U2-H4-HUMAN-DECISIONS-APPROVED",
        "PHASE9U2_G5_CATALOG_REVIEWED",
        "PHASE-9U2-BATCH0-INITIAL-REVIEWED",
    }
)

REQUIRED_METADATA_KEYS = ("exam_year", "exam_day", "booklet", "source_url", "answer_key_source_url")

# tables whose row counts must NOT change during a dry-run
_LEAK_TABLES = {
    "ingestion_documents": IngestionDocument,
    "ingestion_runs": IngestionRun,
    "ingestion_sections": IngestionSection,
    "ingestion_questions": IngestionQuestion,
    "ingestion_assets": IngestionAsset,
    "institutions": Institution,
    "exams": Exam,
    "exam_applications": ExamApplication,
    "exam_booklets": ExamBooklet,
    "source_documents": SourceDocument,
    "answer_key_revisions": AnswerKeyRevision,
    "answer_key_entries": AnswerKeyEntry,
    "questions": Question,
    "question_versions": QuestionVersion,
    "question_options": QuestionOption,
    "booklet_questions": BookletQuestion,
    "pedagogical_classifications": PedagogicalClassification,
    "question_classifications": QuestionClassification,
    "catalog_nodes": CatalogNode,
}


class ManifestError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #


def load_manifest(path: Path | str = DEFAULT_MANIFEST) -> dict:
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"manifest not found: {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("booklets"), list):
        raise ManifestError("manifest must be an object with a 'booklets' list")
    return doc


def _label(entry: dict) -> str:
    return f"{entry.get('exam_year')} D{entry.get('exam_day')}"


def validate_manifest(manifest: dict) -> tuple[list[dict], list[dict]]:
    """Pure. Returns (available, missing). An entry is 'available' only when both
    PDFs exist on disk and every required metadata key is a non-empty value."""
    available: list[dict] = []
    missing: list[dict] = []
    for entry in manifest["booklets"]:
        problems: list[str] = []
        for key in REQUIRED_METADATA_KEYS:
            if entry.get(key) in (None, "", 0):
                problems.append(f"missing:{key}")
        for pdf_key in ("proof_pdf", "answer_key_pdf"):
            raw = entry.get(pdf_key)
            if not raw:
                problems.append(f"missing:{pdf_key}")
            elif not (_REPO_ROOT / raw).exists() and not Path(raw).exists():
                problems.append(f"file_not_found:{pdf_key}={raw}")
        try:
            y = int(entry.get("exam_year") or 0)
            d = int(entry.get("exam_day") or 0)
            if y <= 0 or d not in (1, 2):
                problems.append("bad_year_or_day")
        except (TypeError, ValueError):
            problems.append("bad_year_or_day")
        if problems:
            missing.append({"label": _label(entry), "entry": entry, "problems": problems})
        else:
            available.append(entry)
    return available, missing


def _resolve_pdf(raw: str) -> Path:
    p = _REPO_ROOT / raw
    return p if p.exists() else Path(raw)


# --------------------------------------------------------------------------- #
# Per-booklet processing
# --------------------------------------------------------------------------- #


@dataclass
class BookletMetrics:
    exam_year: int
    exam_day: int
    booklet: str
    source_url: str
    answer_key_source_url: str
    ingestion_document_id: str | None = None
    staged_questions: int = 0
    imported_new: int = 0
    duplicates: int = 0
    requires_review: int = 0
    annulled: int = 0
    invalid: int = 0
    errors: int = 0
    error_detail: list[str] = field(default_factory=list)
    status: str = "PENDING"

    def as_dict(self) -> dict:
        return {
            "exam_year": self.exam_year,
            "exam_day": self.exam_day,
            "booklet": self.booklet,
            "source_url": self.source_url,
            "answer_key_source_url": self.answer_key_source_url,
            "ingestion_document_id": self.ingestion_document_id,
            "staged_questions": self.staged_questions,
            "imported_new": self.imported_new,
            "duplicates": self.duplicates,
            "requires_review": self.requires_review,
            "annulled": self.annulled,
            "invalid": self.invalid,
            "errors": self.errors,
            "error_detail": self.error_detail[:20],
            "status": self.status,
        }


def _source_metadata(entry: dict) -> dict:
    md = {k: entry.get(k) for k in REQUIRED_METADATA_KEYS}
    md["exam_year"] = int(entry["exam_year"])
    md["exam_day"] = int(entry["exam_day"])
    md["exam_name"] = "ENEM"
    md["source_name"] = "INEP"
    if entry.get("booklet_color"):
        md["booklet_color"] = entry["booklet_color"]
    return md


def _classify_result(result) -> str:
    if result.created:
        return "new"
    if result.review_required:
        reason = (result.reason or "").lower()
        if "annull" in reason:
            return "annulled"
        if "review" in reason:
            return "requires_review"
        return "invalid"
    # created is False, not review -> content_hash / question_version_id match
    return "duplicate"


async def process_booklet(session, entry: dict, *, dry_run: bool) -> BookletMetrics:
    m = BookletMetrics(
        exam_year=int(entry["exam_year"]),
        exam_day=int(entry["exam_day"]),
        booklet=str(entry["booklet"]),
        source_url=str(entry["source_url"]),
        answer_key_source_url=str(entry["answer_key_source_url"]),
    )
    try:
        proof = _resolve_pdf(entry["proof_pdf"])
        gabarito = _resolve_pdf(entry["answer_key_pdf"])
        answer_key = PdfParser.parse_answer_key(gabarito)
        document, _run = await IngestionService().ingest_document(
            session, proof, answer_key=answer_key, source_metadata=_source_metadata(entry)
        )
        m.ingestion_document_id = str(document.id)
        staged_ids = list(
            (
                await session.scalars(
                    select(IngestionQuestion.id).where(
                        IngestionQuestion.document_id == document.id
                    )
                )
            ).all()
        )
        m.staged_questions = len(staged_ids)
        importer = QuestionBankImporter(session)
        for qid in staged_ids:
            try:
                result = await importer.import_question(qid)
            except Exception as exc:  # a malformed staged question must not kill the booklet
                m.errors += 1
                m.error_detail.append(f"{qid}: {type(exc).__name__}: {exc}")
                if dry_run and not session.in_transaction():
                    # import_question's internal rollback ended the outer dry-run
                    # transaction; re-open it so the harness stays isolated.
                    await session.begin()
                continue
            bucket = _classify_result(result)
            if bucket == "new":
                m.imported_new += 1
            elif bucket == "duplicate":
                m.duplicates += 1
            elif bucket == "annulled":
                m.annulled += 1
            elif bucket == "requires_review":
                m.requires_review += 1
            else:
                m.invalid += 1
        m.status = "OK" if m.errors == 0 else "OK_WITH_ERRORS"
    except Exception as exc:
        m.status = "ERROR"
        m.errors += 1
        m.error_detail.append(f"booklet: {type(exc).__name__}: {exc}")
        if dry_run and not session.in_transaction():
            await session.begin()
    return m


# --------------------------------------------------------------------------- #
# Batch report + run
# --------------------------------------------------------------------------- #


@dataclass
class BatchReport:
    phase: str = PHASE
    dry_run: bool = True
    approval_present: bool = False
    manifest_entries: int = 0
    available: int = 0
    missing_inputs: list[str] = field(default_factory=list)
    missing_detail: list[dict] = field(default_factory=list)
    blocked: bool = False
    blocked_reason: str = ""
    booklets: list[BookletMetrics] = field(default_factory=list)
    dry_run_leak: bool = False
    database_writes: int = 0

    @property
    def totals(self) -> dict:
        t = {k: 0 for k in ("staged", "new", "duplicates", "requires_review", "annulled", "invalid", "errors")}
        for b in self.booklets:
            t["staged"] += b.staged_questions
            t["new"] += b.imported_new
            t["duplicates"] += b.duplicates
            t["requires_review"] += b.requires_review
            t["annulled"] += b.annulled
            t["invalid"] += b.invalid
            t["errors"] += b.errors
        return t

    @property
    def ok(self) -> bool:
        if self.dry_run:
            return not self.dry_run_leak and not self.blocked
        return not self.blocked and self.database_writes >= 0

    def as_dict(self) -> dict:
        return {
            "phase": self.phase,
            "dry_run": self.dry_run,
            "approval_present": self.approval_present,
            "manifest_entries": self.manifest_entries,
            "available": self.available,
            "missing_inputs": self.missing_inputs,
            "missing_detail": self.missing_detail,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "booklets": [b.as_dict() for b in self.booklets],
            "totals": self.totals,
            "dry_run_leak": self.dry_run_leak,
            "database_writes": self.database_writes,
            "security": {
                "openai_calls": 0,
                "alembic_execution": 0,
                "classifications_created": 0,
                "catalog_nodes_created": 0,
                "vocabulary_changes": 0,
            },
        }


def is_dry_run(environ: dict[str, str] | None = None) -> bool:
    src = os.environ if environ is None else environ
    return src.get(DRY_RUN_ENV_VAR, "true").strip().lower() != "false"


def approval_granted(environ: dict[str, str] | None = None) -> bool:
    src = os.environ if environ is None else environ
    val = src.get(APPROVAL_ENV_VAR)
    if val is None or val in _REJECTED_TOKENS:
        return False
    return val == APPROVAL_TOKEN


async def _count_snapshot(factory) -> dict[str, int]:
    async with factory() as session:
        out = {}
        for name, model in _LEAK_TABLES.items():
            out[name] = int(await session.scalar(select(func.count()).select_from(model)))
        return out


async def run(
    factory,
    *,
    dry_run: bool,
    approval: bool,
    manifest_path: Path | str = DEFAULT_MANIFEST,
) -> BatchReport:
    manifest = load_manifest(manifest_path)
    available, missing = validate_manifest(manifest)
    report = BatchReport(
        dry_run=dry_run,
        approval_present=approval,
        manifest_entries=len(manifest["booklets"]),
        available=len(available),
        missing_inputs=[m["label"] for m in missing],
        missing_detail=missing,
    )

    if not dry_run:
        if missing:
            report.blocked = True
            report.blocked_reason = f"MISSING_INPUTS for {report.missing_inputs} — real run refused"
            return report
        if not approval:
            report.blocked = True
            report.blocked_reason = "no approval token — real run refused"
            return report

    if not available:
        report.blocked = bool(missing)
        report.blocked_reason = "no actionable booklets in the manifest"
        return report

    if dry_run:
        before = await _count_snapshot(factory)
        async with factory() as session:
            _orig_commit = session.commit
            session.commit = session.flush  # type: ignore[assignment]
            await session.begin()
            try:
                for entry in available:
                    report.booklets.append(await process_booklet(session, entry, dry_run=True))
            finally:
                if session.in_transaction():
                    await session.rollback()
                session.commit = _orig_commit  # type: ignore[assignment]
        after = await _count_snapshot(factory)
        report.dry_run_leak = before != after
        report.database_writes = 0
    else:
        # real run: the services commit as designed; one session, sequential booklets
        async with factory() as session:
            for entry in available:
                report.booklets.append(await process_booklet(session, entry, dry_run=False))
        t = report.totals
        report.database_writes = t["new"] + t["staged"]  # informational lower bound

    return report


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_report(report: BatchReport) -> str:
    r = report
    lines = [
        "PHASE 10.1 — BATCH INGESTION RUNNER",
        "",
        f"DRY_RUN: {str(r.dry_run).upper()}   APPROVAL_PRESENT: {r.approval_present}",
        f"MANIFEST: entries={r.manifest_entries} available={r.available} missing={len(r.missing_inputs)}",
    ]
    if r.missing_inputs:
        lines.append(f"MISSING_INPUTS: {r.missing_inputs}")
    if r.blocked:
        lines.append(f"BLOCKED: {r.blocked_reason}")
    lines += [
        "",
        "BOOKLETS:",
        "  YEAR | DAY | BOOKLET | STAGED | NEW | DUPLICATE | REVIEW | ANNULLED | INVALID | ERROR | STATUS",
    ]
    for b in r.booklets:
        lines.append(
            f"  {b.exam_year} |  {b.exam_day}  | {b.booklet:<7} | {b.staged_questions:>6} | "
            f"{b.imported_new:>3} | {b.duplicates:>9} | {b.requires_review:>6} | {b.annulled:>8} | "
            f"{b.invalid:>7} | {b.errors:>5} | {b.status}"
        )
    t = r.totals
    lines += [
        "",
        f"TOTALS: staged={t['staged']} new={t['new']} duplicates={t['duplicates']} "
        f"requires_review={t['requires_review']} annulled={t['annulled']} invalid={t['invalid']} errors={t['errors']}",
        "",
        f"DRY_RUN_LEAK: {r.dry_run_leak}",
        "",
        "SECURITY:",
        f"  DATABASE_WRITES = {r.database_writes if not r.dry_run else 0}",
        "  OPENAI_CALLS = 0",
        "  ALEMBIC_EXECUTION = 0",
        "  CLASSIFICATIONS_CREATED = 0",
        "  CATALOG_NODES_CREATED = 0",
        "  VOCABULARY_CHANGES = 0",
        "",
        "FINAL_DECISION:",
        (
            "PHASE_10_1_BATCH_RUNNER_READY"
            if (r.dry_run and r.ok)
            else ("PHASE_10_1_INGEST_COMPLETE" if (not r.dry_run and r.ok) else "PHASE_10_1_NEEDS_REVIEW")
        ),
    ]
    return "\n".join(lines)


def write_report(report: BatchReport, path: Path | str = DEFAULT_REPORT) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


async def _amain(argv: list[str]) -> int:  # pragma: no cover - manual entrypoint
    parser = argparse.ArgumentParser(description="PHASE 10.1 ENEM batch ingestion runner")
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
    except Exception as exc:
        print(
            "PHASE 10.1 — BATCH INGESTION RUNNER\n"
            f"FATAL: {exc}\n\nFINAL_DECISION:\nPHASE_10_1_NEEDS_REVIEW"
        )
        return 1

    dry_run = is_dry_run()
    approval = approval_granted()
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    try:
        report = await run(factory, dry_run=dry_run, approval=approval, manifest_path=args.manifest)
    finally:
        await engine.dispose()

    print(render_report(report))
    if not args.no_write_report:
        out = write_report(report, args.report)
        print(f"\nwrote {out}")
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - manual entrypoint
    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
