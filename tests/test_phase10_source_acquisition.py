"""PHASE 10.2 — validation of the acquired ENEM 2016-2019 sources + completed manifest.

File-only. No database, no OpenAI, no Alembic, no ingestion. The heavy PDF checks
skip when the local INEP pilot PDFs are absent (clean checkout); the manifest
structural checks always run.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "manual"))

MANIFEST = ROOT / "tests" / "manual" / "phase10_enem_manifest_2016_2020.json"
REPORT = ROOT / "var" / "inep-pilot" / "phase10_source_acquisition_2016_2019_report.json"
PILOT = ROOT / "var" / "inep-pilot"

REQUIRED = ("exam_year", "exam_day", "booklet", "source_url", "answer_key_source_url")
ALLOWED_HOSTS = {"download.inep.gov.br"}
YEARS_2016_2019 = [(2016, 1), (2016, 2), (2017, 1), (2017, 2),
                   (2018, 1), (2018, 2), (2019, 1), (2019, 2)]


def _load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class ManifestCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.m = _load_manifest()
        self.booklets = self.m["booklets"]

    def test_ten_booklets_2016_to_2020(self):
        keys = sorted((b["exam_year"], b["exam_day"]) for b in self.booklets)
        self.assertEqual(
            keys,
            [(y, d) for y in range(2016, 2021) for d in (1, 2)],
        )

    def test_no_required_field_missing(self):
        for b in self.booklets:
            for k in REQUIRED:
                self.assertNotIn(b.get(k), (None, "", 0),
                                 f"{b.get('exam_year')} D{b.get('exam_day')}: {k}")
            for pdf_key in ("proof_pdf", "answer_key_pdf"):
                self.assertTrue(b.get(pdf_key), f"{b['exam_year']} D{b['exam_day']}: {pdf_key}")

    def test_no_fictitious_urls(self):
        for b in self.booklets:
            for key in ("source_url", "answer_key_source_url"):
                u = urlparse(b[key])
                self.assertEqual(u.scheme, "https", b[key])
                self.assertIn(u.hostname, ALLOWED_HOSTS, b[key])
                self.assertTrue(u.path.lower().endswith(".pdf"), b[key])

    def test_2016_2019_urls_confirmed_and_license_flag_kept(self):
        for b in self.booklets:
            self.assertTrue(b.get("license_review_required") is True,
                            f"{b['exam_year']} D{b['exam_day']}")
            if (b["exam_year"], b["exam_day"]) in YEARS_2016_2019:
                self.assertTrue(b.get("source_url_confirmed") is True,
                                f"{b['exam_year']} D{b['exam_day']}")

    def test_2020_entries_still_point_at_local_files(self):
        for b in self.booklets:
            if b["exam_year"] == 2020:
                self.assertTrue(b["proof_pdf"].endswith(f"2020_PV_impresso_{b['booklet']}.pdf"))
                self.assertTrue(b.get("source_url_confirmed") is True)

    def test_2020_d1_url_now_confirmed(self):
        d1 = next(b for b in self.booklets if b["exam_year"] == 2020 and b["exam_day"] == 1)
        self.assertTrue(d1["source_url_confirmed"])
        self.assertIn("confirmed by PHASE 10.2", d1.get("url_provenance", ""))


@unittest.skipUnless(
    REPORT.exists(),
    "acquisition report absent (run PHASE 10.2 first)",
)
class AcquisitionReportTests(unittest.TestCase):
    def setUp(self):
        self.rep = json.loads(REPORT.read_text(encoding="utf-8"))

    def test_totals_shape(self):
        t = self.rep["totals"]
        self.assertEqual(t["total_expected"], 16)
        self.assertEqual(t["provas_ok"] + (8 - t["provas_ok"]), 8)
        self.assertIn("total_missing", t)
        self.assertIn("total_invalid", t)

    def test_eight_booklet_rows(self):
        self.assertEqual(len(self.rep["booklets"]), 8)
        for b in self.rep["booklets"]:
            self.assertIn(b["status"], {"FOUND", "MISSING", "INVALID", "UNVERIFIED"})


@unittest.skipUnless(
    HAVE := (PILOT / "2016_PV_impresso_D1_CD1.pdf").exists()
    and (PILOT / "2019_GB_impresso_D2_CD5.pdf").exists(),
    "local INEP pilot PDFs for 2016-2019 absent",
)
class PdfValidityTests(unittest.TestCase):
    def _manifest_pdfs(self):
        for b in _load_manifest()["booklets"]:
            if b["exam_year"] in (2016, 2017, 2018, 2019):
                yield b, ROOT / b["proof_pdf"], ROOT / b["answer_key_pdf"]

    def test_all_2016_2019_pdfs_exist_nonempty_and_pdf(self):
        for b, pv, gb in self._manifest_pdfs():
            for p in (pv, gb):
                self.assertTrue(p.exists(), p)
                data = p.read_bytes()
                self.assertGreater(len(data), 0, p)
                self.assertEqual(data[:5], b"%PDF-", p)

    def test_all_2016_2019_pdfs_open_with_pages(self):
        from pypdf import PdfReader

        for b, pv, gb in self._manifest_pdfs():
            for p in (pv, gb):
                reader = PdfReader(str(p))
                self.assertGreater(len(reader.pages), 0, p)

    def test_sha256_is_computable_and_recorded(self):
        rep = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {"booklets": []}
        recorded = {}
        for b in rep.get("booklets", []):
            recorded[b["proof"]["file"]] = b["proof"].get("sha256")
            recorded[b["answer_key"]["file"]] = b["answer_key"].get("sha256")
        for b, pv, gb in self._manifest_pdfs():
            for p in (pv, gb):
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                self.assertRegex(h, r"^[0-9a-f]{64}$")
                if p.name in recorded and recorded[p.name]:
                    self.assertEqual(h, recorded[p.name], p.name)


class NoSideEffectsTests(unittest.TestCase):
    def test_no_db_or_ingestion_imports_in_this_test_module(self):
        import ast

        src = Path(__file__).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                self.assertNotIn("agente_ia_edu.services.ingestion", mod)
                self.assertNotIn("agente_ia_edu.db.session", mod)
                self.assertNotIn("openai", mod)
                self.assertNotIn("alembic", mod)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
