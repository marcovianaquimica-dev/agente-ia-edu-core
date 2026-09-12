"""PHASE 10.1 — tests for the ENEM batch ingestion runner.

Pure tests (manifest / gates / static safety) always run. Integration tests
that exercise the real pipeline require the local INEP pilot PDFs and run on an
isolated in-memory SQLite database — never production.
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "manual"))

from agente_ia_edu.db.base import Base  # noqa: E402
from agente_ia_edu.db.models import (  # noqa: E402
    BookletQuestion,
    IngestionDocument,
    IngestionQuestion,
    PedagogicalClassification,
    Question,
    QuestionVersion,
)

import phase10_ingest_batch as r  # noqa: E402

PILOT = ROOT / "var" / "inep-pilot"
D1_PV = PILOT / "2020_PV_impresso_D1_CD1.pdf"
D1_GB = PILOT / "2020_GB_impresso_D1_CD1.pdf"
D2_PV = PILOT / "2020_PV_impresso_D2_CD5.pdf"
D2_GB = PILOT / "2020_GB_impresso_D2_CD5.pdf"
HAVE_PILOT = D1_PV.exists() and D1_GB.exists() and D2_PV.exists() and D2_GB.exists()


def _entry(year, day, *, booklet="D_X", pv=None, gb=None, url="http://s/pv", key_url="http://s/gb", color=None):
    return {
        "exam_year": year,
        "exam_day": day,
        "booklet": booklet,
        "booklet_color": color,
        "proof_pdf": pv,
        "answer_key_pdf": gb,
        "source_url": url,
        "answer_key_source_url": key_url,
    }


def _d1_entry():
    return _entry(2020, 1, booklet="D1_CD1", pv=str(D1_PV), gb=str(D1_GB),
                  url="http://inep/2020_PV_D1_CD1.pdf", key_url="http://inep/2020_GB_D1_CD1.pdf")


def _d2_entry():
    return _entry(2020, 2, booklet="D2_CD5", pv=str(D2_PV), gb=str(D2_GB), color="AMARELO",
                  url="http://inep/2020_PV_D2_CD5.pdf", key_url="http://inep/2020_GB_D2_CD5.pdf")


def _manifest(entries):
    return {"phase": "10.1", "exam": "ENEM", "booklets": entries}


def _write_manifest(tmp: Path, entries) -> str:
    p = tmp / f"mf_{uuid.uuid4().hex[:8]}.json"
    p.write_text(json.dumps(_manifest(entries), ensure_ascii=False), encoding="utf-8")
    return str(p)


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _count(factory, model):
    async with factory() as s:
        return int(await s.scalar(select(func.count()).select_from(model)))


# --------------------------------------------------------------------------- #
# 1-3, 15  Manifest validation
# --------------------------------------------------------------------------- #


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "_p10tmp"
        self.tmp.mkdir(exist_ok=True)

    def tearDown(self):
        for f in self.tmp.glob("*.json"):
            f.unlink()

    def test_01_valid_manifest_all_available(self):
        if not HAVE_PILOT:
            self.skipTest("pilot PDFs absent")
        m = _manifest([_d1_entry(), _d2_entry()])
        available, missing = r.validate_manifest(m)
        self.assertEqual(len(available), 2)
        self.assertEqual(missing, [])

    def test_02_file_not_found_goes_to_missing(self):
        m = _manifest([_entry(2019, 1, booklet="X", pv="var/inep-pilot/DOES_NOT_EXIST.pdf",
                              gb="var/inep-pilot/ALSO_NOT.pdf")])
        available, missing = r.validate_manifest(m)
        self.assertEqual(available, [])
        self.assertEqual(len(missing), 1)
        self.assertTrue(any("file_not_found" in p for p in missing[0]["problems"]))

    def test_03_missing_required_metadata_goes_to_missing(self):
        e = _entry(2018, 2, pv="x.pdf", gb="y.pdf")
        del e["source_url"]
        available, missing = r.validate_manifest(_manifest([e]))
        self.assertEqual(available, [])
        self.assertIn("missing:source_url", missing[0]["problems"])

    def test_15_repo_manifest_is_complete_after_phase_10_2(self):
        # PHASE 10.2 acquired the 2016-2019 sources and filled the manifest, so it
        # no longer reports MISSING_INPUTS when the local pilot PDFs are present.
        m = r.load_manifest(r.DEFAULT_MANIFEST)
        self.assertEqual(len(m["booklets"]), 10)
        # every entry has all required metadata + non-null PDF paths
        for b in m["booklets"]:
            for k in r.REQUIRED_METADATA_KEYS:
                self.assertNotIn(b.get(k), (None, "", 0), f"{b.get('exam_year')} D{b.get('exam_day')} {k}")
            for pdf_key in ("proof_pdf", "answer_key_pdf"):
                self.assertTrue(b.get(pdf_key), pdf_key)
        available, missing = r.validate_manifest(m)
        if HAVE_PILOT and (ROOT / "var" / "inep-pilot" / "2016_PV_impresso_D1_CD1.pdf").exists():
            self.assertEqual(missing, [])
            self.assertEqual(len(available), 10)
        else:
            # files may be absent on a clean checkout -> reported missing, never invented
            self.assertEqual(len(available) + len(missing), 10)

    def test_bad_year_or_day_rejected(self):
        available, missing = r.validate_manifest(_manifest([_entry(2020, 3, pv="a", gb="b")]))
        self.assertEqual(available, [])
        self.assertIn("bad_year_or_day", missing[0]["problems"])


# --------------------------------------------------------------------------- #
# Gates  (5, and dry-run defaulting)
# --------------------------------------------------------------------------- #


class GateTests(unittest.TestCase):
    def test_dry_run_default_true_only_literal_false(self):
        self.assertTrue(r.is_dry_run({}))
        self.assertFalse(r.is_dry_run({"PHASE10_INGEST_DRY_RUN": "false"}))
        self.assertFalse(r.is_dry_run({"PHASE10_INGEST_DRY_RUN": "FALSE"}))
        self.assertTrue(r.is_dry_run({"PHASE10_INGEST_DRY_RUN": "0"}))

    def test_approval_exact_token_only(self):
        self.assertFalse(r.approval_granted({}))
        self.assertFalse(r.approval_granted({"PHASE10_INGEST_APPROVAL_TOKEN": "nope"}))
        self.assertTrue(
            r.approval_granted({"PHASE10_INGEST_APPROVAL_TOKEN": "PHASE10-ENEM-2016-2020-INGEST-APPROVED"})
        )

    def test_rejects_other_phase_tokens(self):
        for tok in (
            "PHASE9U2-H4-HUMAN-DECISIONS-APPROVED",
            "PHASE9U2-H3-VOCABULARY-REVIEWED",
            "PHASE9U2_G5_CATALOG_REVIEWED",
        ):
            self.assertFalse(r.approval_granted({"PHASE10_INGEST_APPROVAL_TOKEN": tok}), tok)


class RunControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()
        self.tmp = Path(__file__).resolve().parent / "_p10tmp"
        self.tmp.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        for f in self.tmp.glob("*.json"):
            f.unlink()
        await self.engine.dispose()

    async def test_05_real_run_without_token_is_blocked_no_write(self):
        mf = _write_manifest(self.tmp, [_d2_entry()] if HAVE_PILOT else [_entry(2020, 2, pv="x", gb="y")])
        report = await r.run(self.factory, dry_run=False, approval=False, manifest_path=mf)
        self.assertTrue(report.blocked)
        self.assertEqual(report.database_writes, 0)
        self.assertEqual(await _count(self.factory, IngestionDocument), 0)

    async def test_real_run_with_incomplete_manifest_is_blocked(self):
        mf = _write_manifest(self.tmp, [_entry(2016, 1)])  # everything null
        report = await r.run(
            self.factory, dry_run=False, approval=True, manifest_path=mf
        )
        self.assertTrue(report.blocked)
        self.assertIn("MISSING_INPUTS", report.blocked_reason)
        self.assertEqual(await _count(self.factory, IngestionDocument), 0)

    async def test_missing_inputs_reported_exactly(self):
        mf = _write_manifest(self.tmp, [_entry(2016, 1), _entry(2017, 2), _entry(2019, 1)])
        report = await r.run(self.factory, dry_run=True, approval=False, manifest_path=mf)
        self.assertEqual(report.missing_inputs, ["2016 D1", "2017 D2", "2019 D1"])
        self.assertEqual(report.available, 0)


# --------------------------------------------------------------------------- #
# 4, 6-10  Integration against the real pipeline (isolated SQLite)
# --------------------------------------------------------------------------- #


@unittest.skipUnless(HAVE_PILOT, "local INEP pilot PDFs required")
class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()
        self.tmp = Path(__file__).resolve().parent / "_p10tmp"
        self.tmp.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        for f in self.tmp.glob("*.json"):
            f.unlink()
        await self.engine.dispose()

    async def test_04_dry_run_writes_nothing(self):
        mf = _write_manifest(self.tmp, [_d1_entry(), _d2_entry()])
        report = await r.run(self.factory, dry_run=True, approval=False, manifest_path=mf)
        self.assertFalse(report.dry_run_leak)
        self.assertEqual(report.database_writes, 0)
        for model in (IngestionDocument, IngestionQuestion, Question, QuestionVersion,
                      BookletQuestion, PedagogicalClassification):
            self.assertEqual(await _count(self.factory, model), 0, model.__name__)
        self.assertEqual([b.exam_day for b in report.booklets], [1, 2])
        self.assertGreater(report.totals["staged"], 100)

    async def test_06_first_pass_dry_run_shapes_metrics(self):
        mf = _write_manifest(self.tmp, [_d1_entry()])
        report = await r.run(self.factory, dry_run=True, approval=False, manifest_path=mf)
        b = report.booklets[0]
        self.assertEqual((b.exam_year, b.exam_day, b.booklet), (2020, 1, "D1_CD1"))
        self.assertGreater(b.staged_questions, 80)
        self.assertGreater(b.imported_new, 0)          # day 1 is text-heavy -> some clean extractions
        self.assertGreaterEqual(b.requires_review, 0)
        self.assertEqual(b.errors, 0)
        self.assertEqual(b.status, "OK")

    async def test_07_second_pass_is_idempotent(self):
        mf = _write_manifest(self.tmp, [_d1_entry()])
        r1 = await r.run(self.factory, dry_run=False, approval=True, manifest_path=mf)
        b1 = r1.booklets[0]
        self.assertGreater(b1.imported_new, 0)
        self.assertEqual(b1.duplicates, 0)
        docs_after_1 = await _count(self.factory, IngestionDocument)
        qv_after_1 = await _count(self.factory, QuestionVersion)
        self.assertEqual(docs_after_1, 1)

        r2 = await r.run(self.factory, dry_run=False, approval=True, manifest_path=mf)
        b2 = r2.booklets[0]
        self.assertEqual(b2.imported_new, 0)
        self.assertEqual(b2.duplicates, b1.imported_new)
        self.assertEqual(await _count(self.factory, IngestionDocument), 1)          # not re-staged
        self.assertEqual(await _count(self.factory, QuestionVersion), qv_after_1)   # no new versions

    async def test_08_partially_existing_booklet(self):
        # ingest D2, then re-run D2 -> the second run must create nothing new
        mf = _write_manifest(self.tmp, [_d2_entry()])
        r1 = await r.run(self.factory, dry_run=False, approval=True, manifest_path=mf)
        b1 = r1.booklets[0]
        qv1 = await _count(self.factory, QuestionVersion)
        r2 = await r.run(self.factory, dry_run=False, approval=True, manifest_path=mf)
        b2 = r2.booklets[0]
        self.assertEqual(b2.imported_new, 0)
        self.assertEqual(b2.duplicates, b1.imported_new)
        self.assertEqual(b2.staged_questions, b1.staged_questions)
        self.assertEqual(await _count(self.factory, QuestionVersion), qv1)   # no new versions
        self.assertEqual(await _count(self.factory, IngestionDocument), 1)   # not re-staged
        # every staged question is accounted for in exactly one bucket
        total_buckets = b2.imported_new + b2.duplicates + b2.requires_review + b2.annulled + b2.invalid + b2.errors
        self.assertEqual(total_buckets, b2.staged_questions)

    async def test_09_multiple_booklets_isolated_metrics(self):
        mf = _write_manifest(self.tmp, [_d1_entry(), _d2_entry()])
        report = await r.run(self.factory, dry_run=True, approval=False, manifest_path=mf)
        self.assertEqual(len(report.booklets), 2)
        d1, d2 = report.booklets
        self.assertNotEqual(d1.booklet, d2.booklet)
        self.assertGreater(d1.staged_questions, 0)
        self.assertGreater(d2.staged_questions, 0)

    async def test_10_one_corrupt_booklet_does_not_kill_the_rest(self):
        bad = self.tmp / "corrupt.pdf"
        bad.write_bytes(b"%PDF-1.4 not really a pdf")
        bad_key = self.tmp / "corrupt_key.pdf"
        bad_key.write_bytes(b"not a pdf")
        entries = [
            _entry(2020, 1, booklet="BAD", pv=str(bad), gb=str(bad_key),
                   url="http://s/bad", key_url="http://s/badkey"),
            _d2_entry(),
        ]
        mf = _write_manifest(self.tmp, entries)
        report = await r.run(self.factory, dry_run=True, approval=False, manifest_path=mf)
        self.assertEqual(len(report.booklets), 2)
        self.assertEqual(report.booklets[0].status, "ERROR")
        self.assertGreaterEqual(report.booklets[0].errors, 1)
        self.assertEqual(report.booklets[1].status, "OK")
        self.assertGreater(report.booklets[1].staged_questions, 0)
        self.assertFalse(report.dry_run_leak)


# --------------------------------------------------------------------------- #
# 11-13  Static safety
# --------------------------------------------------------------------------- #


class StaticSafetyTests(unittest.TestCase):
    FILE = ROOT / "tests" / "manual" / "phase10_ingest_batch.py"

    def _tree(self):
        return ast.parse(self.FILE.read_text(), filename=str(self.FILE))

    def test_11_no_classifier_or_pedagogical_calls(self):
        banned_names = {
            "recover_candidates",
            "resolve_initial_controlled_vocabulary_binding",
            "decide_question_state",
            "match_retrieval_vocabulary",
            "classify_initial_with_provider",
            "propose_with_provider",
            "ClassificationProposalService",
        }
        src = self.FILE.read_text()
        for name in banned_names:
            self.assertNotIn(name + "(", src, name)
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn("curriculum_classification", node.module or "")
                self.assertNotEqual(node.module, "_phase9u2_decision")
        # PedagogicalClassification is imported only for the read-only leak check,
        # never constructed
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Call):
                nm = (
                    node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute) else None
                )
                self.assertNotEqual(nm, "PedagogicalClassification")

    def test_12_no_openai(self):
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertNotEqual(a.name.split(".")[0], "openai")
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual((node.module or "").split(".")[0], "openai")

    def test_13_no_alembic(self):
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertNotEqual(a.name.split(".")[0], "alembic")
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual((node.module or "").split(".")[0], "alembic")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, {"upgrade", "downgrade", "stamp"})

    def test_reuses_pipeline_verbatim(self):
        src = self.FILE.read_text()
        self.assertIn("from agente_ia_edu.services.ingestion import IngestionService", src)
        self.assertIn("from agente_ia_edu.services.ingestion_parser import PdfParser", src)
        self.assertIn(
            "from agente_ia_edu.services.question_bank_importer import QuestionBankImporter", src
        )
        # no ORM writes of Question Bank objects in the runner itself
        for frag in ("session.add(Question(", "session.add(QuestionVersion(",
                     "session.add(BookletQuestion(", "INSERT INTO"):
            self.assertNotIn(frag, src, frag)

    def test_dry_run_commit_neutralisation_is_explicit(self):
        src = self.FILE.read_text()
        self.assertIn("session.commit = session.flush", src)
        self.assertIn("session.commit = _orig_commit", src)
        self.assertIn("_count_snapshot", src)
        self.assertIn("dry_run_leak", src)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
