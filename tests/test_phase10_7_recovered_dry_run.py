"""PHASE 10.7 - tests for the recovered-text ingestion seam + dry-run orchestrator.

No database. The two pipeline seams (PdfParser.parse_file(page_texts=...),
IngestionService.ingest_document(parsed_override=...)) and the orchestrator's
booklet routing / decision logic are covered here. Real-PDF checks skip when the
INEP pilot provas or pymupdf are absent.
"""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "manual"))

from agente_ia_edu.services.ingestion import IngestionService  # noqa: E402
from agente_ia_edu.services.ingestion_parser import PdfParser, parse_document  # noqa: E402

PILOT = ROOT / "var" / "inep-pilot"
_NATIVE = "2020_PV_impresso_D1_CD1.pdf"
_RECOVERED = "2025_PV_impresso_D1_CD1.pdf"


def _have(n: str) -> bool:
    return (PILOT / n).exists()


class SeamSignatureTests(unittest.TestCase):
    def test_parse_file_has_optional_keyword_only_page_texts(self):
        sig = inspect.signature(PdfParser.parse_file)
        self.assertIn("page_texts", sig.parameters)
        p = sig.parameters["page_texts"]
        self.assertEqual(p.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(p.default)

    def test_parse_document_forwards_page_texts(self):
        sig = inspect.signature(parse_document)
        self.assertIn("page_texts", sig.parameters)
        self.assertEqual(sig.parameters["page_texts"].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_ingest_document_has_optional_parsed_override(self):
        sig = inspect.signature(IngestionService.ingest_document)
        self.assertIn("parsed_override", sig.parameters)
        self.assertIsNone(sig.parameters["parsed_override"].default)

    def test_parser_regexes_unchanged(self):
        self.assertEqual(PdfParser.QUESTION_PATTERN.pattern, r"(?mi)^\s*Questão\s+(\d+)\s*")
        self.assertEqual(
            PdfParser.OPTION_PATTERN.pattern,
            r"(?ms)^\s*([A-E])\s{2,}(.+?)(?=^\s*[A-E]\s{2,}|\f|\Z)",
        )


@unittest.skipUnless(_have(_NATIVE), "2020 D1 pilot prova absent")
class ParseFilePageTextsSeamTests(unittest.TestCase):
    def test_none_is_byte_identical_to_default_pypdf_path(self):
        a = PdfParser.parse_file(PILOT / _NATIVE)
        b = PdfParser.parse_file(PILOT / _NATIVE, page_texts=None)
        self.assertEqual(
            [(q.question_number, q.statement_text, q.alternatives_text) for q in a.questions],
            [(q.question_number, q.statement_text, q.alternatives_text) for q in b.questions],
        )
        self.assertEqual(a.document_hash, b.document_hash)
        self.assertEqual(a.page_count, b.page_count)

    def test_injected_page_texts_are_used_for_the_text_layer(self):
        from pypdf import PdfReader

        n_pages = len(PdfReader(str(PILOT / _NATIVE)).pages)
        fake = [""] * n_pages
        fake[0] = (
            "Questão 1\nEnunciado sintetico injetado para o teste.\n"
            "A    alfa\nB    beta\nC    gama\nD    delta\nE    epsilon\n"
        )
        parsed = PdfParser.parse_file(PILOT / _NATIVE, page_texts=fake)
        self.assertEqual([q.question_number for q in parsed.questions], [1])
        self.assertIn("injetado", parsed.questions[0].statement_text)
        # document hash still reflects the FILE, not the injected text
        self.assertEqual(parsed.document_hash, PdfParser.file_hash(PILOT / _NATIVE))

    def test_wrong_length_page_texts_is_rejected(self):
        with self.assertRaises(ValueError):
            PdfParser.parse_file(PILOT / _NATIVE, page_texts=["only one page"])

    def test_all_empty_injected_pages_raise_like_the_native_path(self):
        from pypdf import PdfReader

        n_pages = len(PdfReader(str(PILOT / _NATIVE)).pages)
        with self.assertRaises(ValueError):
            PdfParser.parse_file(PILOT / _NATIVE, page_texts=[""] * n_pages)


@unittest.skipUnless(_have(_RECOVERED), "2025 D1 pilot prova absent")
class RecoveredRoutingRealTests(unittest.TestCase):
    def test_recovered_pages_feed_full_question_range(self):
        try:
            from agente_ia_edu.services.text_recovery import recover_pdf_text
        except Exception:  # pragma: no cover
            self.skipTest("text_recovery import failed")
        r = recover_pdf_text(PILOT / _RECOVERED, expected_questions=90)
        if r.recovery_status != "RECOVERED":
            self.skipTest("pymupdf backend unavailable")
        parsed = PdfParser.parse_file(PILOT / _RECOVERED, page_texts=r.recovered_pages)
        nums = sorted({q.question_number for q in parsed.questions})
        self.assertEqual(nums, list(range(1, 91)))
        # native alone finds almost nothing
        native = PdfParser.parse_file(PILOT / _RECOVERED)
        self.assertLess(len({q.question_number for q in native.questions}), 10)


class OrchestratorRoutingTests(unittest.TestCase):
    def setUp(self):
        import phase10_7_recovered_dry_run as p107

        self.p107 = p107

    def test_native_set_is_exactly_2016_2020(self):
        self.assertEqual(
            self.p107.NATIVE_YEARS,
            {(y, d) for y in range(2016, 2021) for d in (1, 2)},
        )

    def test_recovered_set_is_exactly_the_four_phase_10_6_booklets(self):
        self.assertEqual(
            self.p107.RECOVERED_KEYS,
            {(2024, 1), (2024, 2), (2025, 1), (2025, 2)},
        )

    def test_skip_set_is_2021_ocr_and_2022_2023_review(self):
        self.assertEqual(self.p107.SKIP_KEYS[(2021, 1)], "OCR_PENDING")
        self.assertEqual(self.p107.SKIP_KEYS[(2021, 2)], "OCR_PENDING")
        for k in [(2022, 1), (2022, 2), (2023, 1), (2023, 2)]:
            self.assertEqual(self.p107.SKIP_KEYS[k], "REVIEW_PENDING")
        self.assertNotIn((2024, 1), self.p107.SKIP_KEYS)

    def test_method_for_routing(self):
        self.assertEqual(self.p107._method_for(2019, 1), "native")
        self.assertEqual(self.p107._method_for(2025, 1), "recovered")
        self.assertIsNone(self.p107._method_for(2021, 1))
        self.assertIsNone(self.p107._method_for(2022, 2))


class DecisionLogicTests(unittest.TestCase):
    def setUp(self):
        import phase10_7_recovered_dry_run as p107

        self.p107 = p107

    def _good_recovered(self, key):
        b = self.p107.BookletResult(
            exam_year=key[0], exam_day=key[1], booklet="X",
            extraction_method="recovered", recovery_status="RECOVERED",
        )
        b.questions_detected = 90
        b.distinct_numbers = 90
        b.official_range_complete = True
        b.imported_new = 0
        b.duplicates = 0
        b.hash_analysis = {"collision_risk": False}
        return b

    def _report(self, recovered, d2_new=0, d2_dup=24, leak=False):
        rep = self.p107.Report(dry_run=True)
        rep.processed = list(recovered)
        d2 = self.p107.BookletResult(
            exam_year=2020, exam_day=2, booklet="D2_CD5",
            extraction_method="native", recovery_status="OK(native)",
        )
        d2.imported_new = d2_new
        d2.duplicates = d2_dup
        d2.hash_analysis = {"collision_risk": False}
        rep.processed.append(d2)
        rep.dry_run_leak = leak
        return rep

    def test_all_green_is_complete(self):
        recovered = [self._good_recovered(k) for k in self.p107.RECOVERED_KEYS]
        rep = self._report(recovered)
        self.assertEqual(self.p107._decision(rep), "PHASE_10_7_RECOVERED_TEXT_DRY_RUN_COMPLETE")

    def test_recovered_new_gt_zero_forces_review(self):
        recovered = [self._good_recovered(k) for k in self.p107.RECOVERED_KEYS]
        recovered[0].imported_new = 1
        rep = self._report(recovered)
        self.assertIn("NEEDS_REVIEW", self.p107._decision(rep))

    def test_2020_d2_non_idempotent_forces_review(self):
        recovered = [self._good_recovered(k) for k in self.p107.RECOVERED_KEYS]
        rep = self._report(recovered, d2_new=1, d2_dup=23)
        self.assertIn("NEEDS_REVIEW", self.p107._decision(rep))

    def test_incomplete_range_forces_review(self):
        recovered = [self._good_recovered(k) for k in self.p107.RECOVERED_KEYS]
        recovered[1].official_range_complete = False
        rep = self._report(recovered)
        self.assertIn("NEEDS_REVIEW", self.p107._decision(rep))

    def test_leak_forces_review(self):
        recovered = [self._good_recovered(k) for k in self.p107.RECOVERED_KEYS]
        rep = self._report(recovered, leak=True)
        self.assertIn("NEEDS_REVIEW", self.p107._decision(rep))

    def test_hash_collision_forces_review(self):
        recovered = [self._good_recovered(k) for k in self.p107.RECOVERED_KEYS]
        recovered[2].hash_analysis = {"collision_risk": True}
        rep = self._report(recovered)
        self.assertIn("NEEDS_REVIEW", self.p107._decision(rep))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
