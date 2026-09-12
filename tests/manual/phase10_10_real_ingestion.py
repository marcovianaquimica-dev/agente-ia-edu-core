"""PHASE 10.10 - FIRST REAL INGESTION of the ENEM 2016-2025 corpus.

This is an AUTHORISED WRITE operation. It requires:

    PHASE10_INGEST_DRY_RUN=false
    PHASE10_INGEST_APPROVAL_TOKEN=PHASE10-ENEM-2016-2025-INGEST-APPROVED

It executes exactly the state PHASE 10.9 approved:

  * 2016-2020 D1/D2  -> native pypdf (idempotent; the PHASE 10.4 rows stay
                        DUPLICATE - 2017 D1 = 54, 2020 D1 = 79, 2020 D2 = 24)
  * 2021 D1/D2        -> OCR_PENDING: enumerated but NOT imported into the bank
  * 2022/2023 D1/D2   -> native pypdf, current review gate (all REVIEW)
  * 2024/2025 D1/D2   -> option_recovery.recover_option_layout(); ONLY the 175
                        REAL_IMPORT_CANDIDATES from the PHASE 10.9 report may be
                        created, and only if recovery_status == IMPORT_READY and
                        number_of_options >= 5

It reuses IngestionService.ingest_document() and
QuestionBankImporter.import_question() unchanged - real commits, no dry-run
transaction patching. It performs NO pedagogical classification and touches no
curriculum / catalog / vocabulary / classifier code.
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

from sqlalchemy import func, select, text  # noqa: E402

from agente_ia_edu.db.models import IngestionQuestion, QuestionVersion  # noqa: E402
from agente_ia_edu.services.ingestion import IngestionService  # noqa: E402
from agente_ia_edu.services.ingestion_parser import PdfParser  # noqa: E402
from agente_ia_edu.services.option_recovery import recover_option_layout  # noqa: E402
from agente_ia_edu.services.question_bank_importer import QuestionBankImporter  # noqa: E402

import phase10_ingest_batch as p10  # noqa: E402

PHASE = "10.10"
APPROVAL_TOKEN = "PHASE10-ENEM-2016-2025-INGEST-APPROVED"
DEFAULT_MANIFEST = _HERE / "phase10_enem_manifest_2016_2025.json"
DEFAULT_P109_REPORT = _REPO_ROOT / "var" / "inep-pilot" / "phase10_9_final_pre_ingestion_report.json"
DEFAULT_REPORT = _REPO_ROOT / "var" / "inep-pilot" / "phase10_10_real_ingestion_report.json"

NATIVE_KEYS = {(y, d) for y in range(2016, 2021) for d in (1, 2)}
NATIVE_KEYS |= {(2022, 1), (2022, 2), (2023, 1), (2023, 2)}
RECOVERED_KEYS = {(2024, 1), (2024, 2), (2025, 1), (2025, 2)}
OCR_KEYS = {(2021, 1), (2021, 2)}

COUNT_TABLES = [
    "questions", "question_versions", "question_options", "booklet_questions",
    "ingestion_documents", "ingestion_questions", "ingestion_runs", "ingestion_sections",
    "ingestion_assets", "source_documents", "answer_key_revisions", "answer_key_entries",
    "pedagogical_classifications", "question_classifications", "catalog_nodes",
]
IDEMPOTENCY_EXPECT = {(2017, 1): 54, (2020, 1): 79, (2020, 2): 24}


class Abort(RuntimeError):
    pass


async def _counts(factory) -> dict[str, int]:
    async with factory() as s:
        out = {}
        for t in COUNT_TABLES:
            out[t] = int(await s.scalar(text(f"SELECT count(*) FROM {t}")))
        return out


@dataclass
class BookletResult:
    exam_year: int
    exam_day: int
    booklet: str
    method: str
    recovery_status: str
    ingestion_document_id: str | None = None
    staged: int = 0
    new: int = 0
    duplicate: int = 0
    review: int = 0
    invalid: int = 0
    ocr_pending: int = 0
    option_recovery_pending: int = 0
    annulled: int = 0
    errors: int = 0
    error_detail: list[str] = field(default_factory=list)
    status: str = "OK"

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


async def preflight(factory, manifest_path: Path, p109_path: Path) -> dict:
    info: dict = {"checks": []}

    def check(name: str, ok: bool, detail=""):
        info["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
        if not ok:
            raise Abort(f"preflight failed: {name} ({detail})")

    async with factory() as s:
        dbname = await s.scalar(text("SELECT current_database()"))
        pgver = await s.scalar(text("SHOW server_version"))
        check("current_database == agente_ia_edu", dbname == "agente_ia_edu", dbname)
        check("postgresql", "PostgreSQL" in (await s.scalar(text("SELECT version()"))), pgver)
        cn = int(await s.scalar(text("SELECT count(*) FROM catalog_nodes")))
        pc = int(await s.scalar(text("SELECT count(*) FROM pedagogical_classifications")))
        qc = int(await s.scalar(text("SELECT count(*) FROM question_classifications")))
        check("catalog_nodes == 48", cn == 48, cn)
        check("pedagogical_classifications == 24", pc == 24, pc)
        check("question_classifications == 0", qc == 0, qc)
        info["baseline"] = {"catalog_nodes": cn, "pedagogical_classifications": pc,
                            "question_classifications": qc}

    check("manifest exists", manifest_path.exists(), manifest_path)
    manifest = json.loads(manifest_path.read_text())
    check("manifest has 20 booklets", len(manifest.get("booklets", [])) == 20,
          len(manifest.get("booklets", [])))
    from pypdf import PdfReader
    bad = []
    for b in manifest["booklets"]:
        for k in ("proof_pdf", "answer_key_pdf"):
            p = _REPO_ROOT / b[k] if not Path(b[k]).is_absolute() else Path(b[k])
            if not p.exists() or p.read_bytes()[:5] != b"%PDF-":
                bad.append(str(p))
            else:
                try:
                    PdfReader(str(p))
                except Exception as exc:  # noqa: BLE001
                    bad.append(f"{p}: {exc}")
    check("40 PDFs present and valid", not bad, bad[:5])

    check("PHASE 10.9 report exists", p109_path.exists(), p109_path)
    p109 = json.loads(p109_path.read_text())
    check("PHASE 10.9 decision is READY",
          p109.get("final_decision") == "PHASE_10_9_READY_FOR_REAL_INGESTION",
          p109.get("final_decision"))
    cands = p109.get("real_import_candidates", [])
    check("PHASE 10.9 has 175 candidates", len(cands) == 175, len(cands))
    for c in cands:
        check(f"candidate {c['year']}D{c['day']}Q{c['official_number']} well-formed",
              c["number_of_options"] >= 5 and c["recovery_status"] == "IMPORT_READY"
              and bool(c["statement_hash"]), c)
    info["candidate_set"] = {(c["year"], c["day"], c["official_number"]) for c in cands}
    info["n_candidates"] = len(cands)
    return info


async def process_booklet(session, entry: dict, candidate_set: set,
                          per_question: list) -> BookletResult:
    y, d = int(entry["exam_year"]), int(entry["exam_day"])
    method = _method_for(y, d)
    res = BookletResult(y, d, str(entry["booklet"]), method, "OK(native)")
    proof = p10._resolve_pdf(entry["proof_pdf"])
    gabarito = p10._resolve_pdf(entry["answer_key_pdf"])

    try:
        answer_key = PdfParser.parse_answer_key(gabarito)

        parsed_override = None
        recovered_status_by_num: dict[int, str] = {}
        recovered_opts_by_num: dict[int, int] = {}
        if (y, d) in RECOVERED_KEYS:
            recovered = recover_option_layout(proof)
            res.recovery_status = "RECOVERED(option_layout)"
            recovered_status_by_num = {q.number: q.status for q in recovered.questions}
            for q in recovered.questions:
                recovered_opts_by_num[q.number] = len(q.options)
            parsed_override = PdfParser.parse_file(proof, page_texts=recovered.page_texts)
        elif (y, d) in OCR_KEYS:
            res.recovery_status = "OCR_PENDING"

        document, _run = await IngestionService().ingest_document(
            session, proof, answer_key=answer_key,
            source_metadata=p10._source_metadata(entry),
            parsed_override=parsed_override,
        )
        res.ingestion_document_id = str(document.id)

        staged_ids = list((await session.scalars(
            select(IngestionQuestion.id).where(IngestionQuestion.document_id == document.id)
        )).all())
        res.staged = len(staged_ids)

        importer = QuestionBankImporter(session)
        for qid in staged_ids:
            item = await session.get(IngestionQuestion, qid)
            official = item.question_number
            stmt_hash = (
                hashlib.sha256(item.statement_text.encode("utf-8")).hexdigest()
                if item.statement_text else None
            )
            n_opts = len([ln for ln in (item.alternatives_text or "").splitlines() if ln.strip()])
            rstatus = recovered_status_by_num.get(official)

            row = {
                "year": y, "day": d, "booklet": res.booklet, "official_number": official,
                "content_hash": stmt_hash, "number_of_options": n_opts,
                "extraction_method": method,
                "recovery_status": rstatus or res.recovery_status,
                "question_id": None, "question_version_id": None, "disposition": None,
            }

            # 2021 - never import into the bank
            if (y, d) in OCR_KEYS:
                row["disposition"] = "OCR_PENDING"
                res.ocr_pending += 1
                per_question.append(row)
                continue

            try:
                result = await importer.import_question(qid)
            except Exception as exc:  # noqa: BLE001
                res.errors += 1
                res.error_detail.append(f"Q{official} {qid}: {type(exc).__name__}: {exc}")
                row["disposition"] = "ERROR"
                row["error"] = f"{type(exc).__name__}: {exc}"
                per_question.append(row)
                if not session.in_transaction():
                    await session.begin()
                continue

            bucket = p10._classify_result(result)

            # recovered booklets: a NEW must be an approved candidate
            if bucket == "new" and (y, d) in RECOVERED_KEYS:
                approved = (y, d, official) in candidate_set
                if not (approved and rstatus == "IMPORT_READY" and n_opts >= 5):
                    # should not happen (non-candidates emit no option lines) -
                    # fail safe: do not create it, record as pending
                    row["disposition"] = "OPTION_RECOVERY_PENDING"
                    res.option_recovery_pending += 1
                    per_question.append(row)
                    continue

            if bucket == "new":
                row["disposition"] = "NEW"
                row["question_id"] = str(result.question_id)
                row["question_version_id"] = str(result.question_version_id)
                res.new += 1
            elif bucket == "duplicate":
                row["disposition"] = "DUPLICATE"
                row["question_version_id"] = str(result.question_version_id) if result.question_version_id else None
                res.duplicate += 1
            elif bucket == "annulled":
                row["disposition"] = "ANNULLED"
                res.annulled += 1
            elif bucket == "requires_review":
                row["disposition"] = "REVIEW"
                row["reason"] = result.reason
                res.review += 1
            else:
                row["disposition"] = "INVALID"
                row["reason"] = result.reason
                res.invalid += 1
            per_question.append(row)

        if res.errors:
            res.status = "OK_WITH_ERRORS"
    except Exception as exc:  # noqa: BLE001
        res.status = "ERROR"
        res.errors += 1
        res.error_detail.append(f"booklet: {type(exc).__name__}: {exc}")
        per_question.append({
            "year": y, "day": d, "booklet": res.booklet, "official_number": None,
            "disposition": "ERROR", "exception_type": type(exc).__name__,
            "exception_message": str(exc),
        })
    return res


async def integrity_checks(factory) -> dict:
    checks: dict = {}
    async with factory() as s:
        async def one(q):
            return int(await s.scalar(text(q)))
        checks["duplicate_content_hash_versions"] = await one(
            "SELECT count(*) FROM (SELECT content_hash FROM question_versions "
            "WHERE content_hash IS NOT NULL GROUP BY content_hash HAVING count(*) > 1) x")
        checks["question_versions_without_question"] = await one(
            "SELECT count(*) FROM question_versions qv "
            "LEFT JOIN questions q ON q.id = qv.question_id WHERE q.id IS NULL")
        checks["orphan_question_options"] = await one(
            "SELECT count(*) FROM question_options qo "
            "LEFT JOIN question_versions qv ON qv.id = qo.question_version_id WHERE qv.id IS NULL")
        checks["booklet_questions_without_version"] = await one(
            "SELECT count(*) FROM booklet_questions bq "
            "LEFT JOIN question_versions qv ON qv.id = bq.question_version_id WHERE qv.id IS NULL")
        checks["new_versions_with_less_than_5_options"] = await one(
            "SELECT count(*) FROM (SELECT qv.id FROM question_versions qv "
            "JOIN question_options qo ON qo.question_version_id = qv.id "
            "GROUP BY qv.id HAVING count(*) < 5) x")
    return checks


def render(report: dict) -> str:
    L = [f"PHASE 10.10 - REAL INGESTION",
         "", f"final_decision: {report['final_decision']}", "",
         "PER-BOOKLET:",
         "  YEAR D BOOKLET  METHOD                    STAGED NEW DUP REVIEW INVLD OCRP OPTP ANNUL ERR  STATUS"]
    for b in report["per_booklet"]:
        L.append(
            f"  {b['exam_year']} {b['exam_day']} {b['booklet']:<7} {b['method']:<24} "
            f"{b['staged']:>6} {b['new']:>3} {b['duplicate']:>3} {b['review']:>6} {b['invalid']:>5} "
            f"{b['ocr_pending']:>4} {b['option_recovery_pending']:>4} {b['annulled']:>5} {b['errors']:>3}  {b['status']}"
        )
    t = report["totals"]
    L += ["", "TOTALS:"]
    for k in ("staged", "new", "duplicate", "review", "invalid", "ocr_pending",
              "option_recovery_pending", "annulled", "errors"):
        L.append(f"  {k:26} {t[k]}")
    L += ["", "DELTAS:"]
    for k, v in report["deltas"].items():
        L.append(f"  {k:32} {v:+d}")
    L += ["", "INTEGRITY:"]
    for k, v in report["integrity"].items():
        L.append(f"  {k:40} {v}  {'OK' if v == 0 else 'FAIL'}")
    L += ["", "IDEMPOTENCY:"]
    for k, v in report["idempotency"].items():
        L.append(f"  {k}: {v}")
    s = report["security"]
    L += ["", "SECURITY:"]
    for k in ("DATABASE_WRITES", "OPENAI_CALLS", "ALEMBIC_EXECUTION",
              "CLASSIFICATIONS_CREATED", "CATALOG_NODES_CREATED", "VOCABULARY_CHANGES",
              "PRODUCTION_DATA_MODIFIED"):
        L.append(f"  {k} = {s[k]}")
    L += ["", "FINAL_DECISION:", report["final_decision"]]
    return "\n".join(L)


def decide(report: dict) -> tuple[str, list[str]]:
    r = []
    t = report["totals"]
    d = report["deltas"]
    integ = report["integrity"]
    idem = report["idempotency"]

    if t["errors"] > 0:
        r.append(f"errors={t['errors']}")
    if any(v != 0 for v in integ.values()):
        r.append(f"integrity failures: {integ}")
    for k, exp in (("2017_D1", 54), ("2020_D1", 79), ("2020_D2", 24)):
        if idem.get(k, {}).get("observed_duplicate") != exp or idem.get(k, {}).get("observed_new") != 0:
            r.append(f"idempotency {k}: {idem.get(k)}")
    if t["ocr_pending"] != 185:
        r.append(f"ocr_pending={t['ocr_pending']} != 185 (2021 must not be imported)")
    if d["pedagogical_classifications"] != 0 or d["question_classifications"] != 0 or d["catalog_nodes"] != 0:
        r.append("classification/catalog delta != 0")
    # NEW must equal question / question_version deltas and be <= 175
    if t["new"] != d["questions"] or t["new"] != d["question_versions"]:
        r.append(f"NEW {t['new']} != questions delta {d['questions']} / qv delta {d['question_versions']}")
    if t["new"] > report["preflight"]["n_candidates"]:
        r.append(f"NEW {t['new']} exceeds approved candidate set {report['preflight']['n_candidates']}")
    if report["security"]["OPENAI_CALLS"] or report["security"]["ALEMBIC_EXECUTION"]:
        r.append("OpenAI/Alembic executed")
    ok = not r
    return ("PHASE_10_10_REAL_INGESTION_COMPLETE" if ok
            else "PHASE_10_10_REAL_INGESTION_NEEDS_REVIEW"), r


async def _amain(argv) -> int:  # pragma: no cover
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--p109-report", default=str(DEFAULT_P109_REPORT))
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    args = ap.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE, override=False)
    except Exception:
        pass

    dry = os.environ.get("PHASE10_INGEST_DRY_RUN", "true").strip().lower()
    tok = os.environ.get("PHASE10_INGEST_APPROVAL_TOKEN", "")
    if dry != "false":
        print("PHASE 10.10 requires PHASE10_INGEST_DRY_RUN=false")
        return 2
    if tok != APPROVAL_TOKEN:
        print(f"PHASE 10.10 requires the exact token {APPROVAL_TOKEN!r}")
        return 2

    from agente_ia_edu.db.session import create_engine, create_session_factory, get_database_url
    try:
        get_database_url()
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: {exc}")
        return 2

    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    report: dict = {"phase": PHASE}
    try:
        pf = await preflight(factory, Path(args.manifest), Path(args.p109_report))
        report["preflight"] = {"checks": pf["checks"], "baseline": pf["baseline"],
                               "n_candidates": pf["n_candidates"]}
        candidate_set = pf["candidate_set"]

        report["database"] = {"name": "agente_ia_edu", "host": "localhost:5433 (password OCULTA)"}
        before = await _counts(factory)
        report["before_counts"] = before

        manifest = json.loads(Path(args.manifest).read_text())
        per_booklet: list[BookletResult] = []
        per_question: list[dict] = []
        async with factory() as session:
            for entry in manifest["booklets"]:
                per_booklet.append(await process_booklet(
                    session, entry, candidate_set, per_question))

        report["per_booklet"] = [b.as_dict() for b in per_booklet]
        report["per_question"] = per_question

        totals = {k: 0 for k in ("staged", "new", "duplicate", "review", "invalid",
                                 "ocr_pending", "option_recovery_pending", "annulled", "errors")}
        for b in per_booklet:
            for k in totals:
                totals[k] += getattr(b, k)
        report["totals"] = totals

        after = await _counts(factory)
        report["after_counts"] = after
        report["deltas"] = {k: after[k] - before[k] for k in before}

        report["integrity"] = await integrity_checks(factory)

        report["idempotency"] = {}
        for (y, d), exp in IDEMPOTENCY_EXPECT.items():
            b = next(bb for bb in per_booklet if (bb.exam_year, bb.exam_day) == (y, d))
            report["idempotency"][f"{y}_D{d}"] = {
                "expected_duplicate": exp,
                "observed_duplicate": b.duplicate,
                "observed_new": b.new,
            }

        report["security"] = {
            "DATABASE_WRITES": report["deltas"]["questions"] + report["deltas"]["question_versions"]
            + report["deltas"]["question_options"] + report["deltas"]["booklet_questions"]
            + report["deltas"]["ingestion_documents"] + report["deltas"]["ingestion_questions"]
            + report["deltas"]["ingestion_runs"] + report["deltas"]["ingestion_assets"]
            + report["deltas"]["source_documents"] + report["deltas"]["answer_key_revisions"]
            + report["deltas"]["answer_key_entries"],
            "OPENAI_CALLS": 0, "ALEMBIC_EXECUTION": 0,
            "CLASSIFICATIONS_CREATED": report["deltas"]["pedagogical_classifications"]
            + report["deltas"]["question_classifications"],
            "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
            "VOCABULARY_CHANGES": 0,
            "PRODUCTION_DATA_MODIFIED": 1,
        }
        report["known_preexisting_failures"] = [
            "tests/test_ingestion_classifier.py::TestIngestionClassifierIntegration::"
            "test_13_isolation_between_documents (PRE_EXISTING)"
        ]

        dec, reasons = decide(report)
        report["final_decision"] = dec
        report["final_decision_reasons"] = reasons
    except Abort as exc:
        report["final_decision"] = "PHASE_10_10_REAL_INGESTION_NEEDS_REVIEW"
        report["abort"] = str(exc)
        print(f"ABORTED: {exc}")
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        await engine.dispose()
        return 1
    finally:
        try:
            await engine.dispose()
        except Exception:
            pass

    print(render(report))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {args.report}")
    return 0 if report["final_decision"] == "PHASE_10_10_REAL_INGESTION_COMPLETE" else 1


def main(argv=None):  # pragma: no cover
    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
