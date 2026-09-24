"""PHASE 10.6 - tests for the isolated PDF text-recovery layer.

No database, no OpenAI, no Alembic, no ingestion. Synthetic-text unit tests
always run. The heavy tests over the real INEP pilot PDFs skip when those files
are absent (clean checkout) or when the optional ``pymupdf`` backend is missing.
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agente_ia_edu.services.text_recovery import (  # noqa: E402
    NATIVE_PYPDF,
    RECOVERED_PYMUPDF,
    STATUS_OCR_PENDING,
    STATUS_OK,
    STATUS_RECOVERED,
    STATUS_RECOVERY_PENDING,
    extract_pymupdf_pages,
    pymupdf_available,
    recover_pdf_text,
    score_pages,
)

PILOT = ROOT / "var" / "inep-pilot"
HAVE_PYMUPDF = pymupdf_available()


def _pilot(name: str) -> Path:
    return PILOT / name


def _have(name: str) -> bool:
    return _pilot(name).exists()


def _clean_question(i: int) -> str:
    return (
        f"Questão {i}\n"
        f"Enunciado da questao {i} com contexto suficiente para ler.\n"
        "A    primeira alternativa plausivel\n"
        "B    segunda alternativa plausivel\n"
        "C    terceira alternativa plausivel\n"
        "D    quarta alternativa plausivel\n"
        "E    quinta alternativa plausivel\n"
    )


def _clean_booklet(n: int = 90, start: int = 1) -> list[str]:
    return [_clean_question(i) for i in range(start, start + n)]


def _symbol_soup_booklet(n: int = 90) -> list[str]:
    # what an un-decodable subset font degrades to: headers survive, body is
    # punctuation / digits, almost no real letters.
    soup = "] ^ & ^ 8 8 & _4 \" & 8 ( 8 ` & ( ? ~ 4 6 ( H 8 I 4 * ^ 8 ( 7 A * 4 B "
    return [f"Questão {i}\n{soup * 6}\n" for i in range(1, n + 1)]


# --------------------------------------------------------------------------- #
# score_pages - pure, synthetic, always runs
# --------------------------------------------------------------------------- #


class ScorePagesUnitTests(unittest.TestCase):
    def test_clean_two_question_page_scores_full_legibility_and_headers(self):
        s = score_pages([_clean_question(1) + _clean_question(2)])
        self.assertGreater(s.legibility_ratio, 0.8)
        self.assertGreater(s.word_like_ratio, 0.5)
        self.assertEqual(s.question_headers, 2)
        self.assertEqual(s.distinct_numbers, 2)
        self.assertEqual((s.number_min, s.number_max), (1, 2))
        self.assertEqual(s.five_option_runs, 2)
        self.assertEqual(s.glyph_tokens, 0)

    def test_symbol_soup_drives_legibility_down(self):
        clean = score_pages([_clean_question(1)])
        soup = score_pages(_symbol_soup_booklet(3))
        self.assertLess(soup.legibility_ratio, 0.4)
        self.assertLess(soup.word_like_ratio, 0.3)
        self.assertLess(soup.legibility_ratio, clean.legibility_ratio)

    def test_literal_glyph_tokens_are_counted_but_not_the_only_signal(self):
        page = "Questão 1\n" + " ".join(f"/g{n}" for n in range(40, 120)) + "\n"
        s = score_pages([page])
        self.assertGreater(s.glyph_tokens, 70)
        self.assertLess(s.legibility_ratio, 0.75)

    def test_distinct_numbers_dedupe_repeated_headers(self):
        s = score_pages(["Questão 5\nx\nQuestão 5\ny\nQuestão 6\nz\n"])
        self.assertEqual(s.question_headers, 3)
        self.assertEqual(s.distinct_numbers, 2)

    def test_empty_pages_do_not_divide_by_zero(self):
        s = score_pages(["", ""])
        self.assertEqual(s.legibility_ratio, 1.0)
        self.assertEqual(s.word_like_ratio, 1.0)
        self.assertEqual(s.distinct_numbers, 0)
        self.assertIsNone(s.number_min)


# --------------------------------------------------------------------------- #
# recover_pdf_text decision logic - backends monkeypatched
# --------------------------------------------------------------------------- #


class DecisionLogicTests(unittest.TestCase):
    def setUp(self):
        import agente_ia_edu.services.text_recovery as tr

        self._tr = tr
        self._orig_native = tr.extract_native_pages
        self._orig_mupdf = tr.extract_pymupdf_pages

    def tearDown(self):
        self._tr.extract_native_pages = self._orig_native
        self._tr.extract_pymupdf_pages = self._orig_mupdf

    def _set(self, native_pages, mupdf_pages):
        self._tr.extract_native_pages = lambda _p: list(native_pages)
        self._tr.extract_pymupdf_pages = lambda _p: (
            None if mupdf_pages is None else list(mupdf_pages)
        )

    # -- clean native -----------------------------------------------------
    def test_D_clean_native_is_OK_and_recovery_not_attempted(self):
        called = {"mupdf": False}

        def _boom(_p):
            called["mupdf"] = True
            return None

        self._tr.extract_native_pages = lambda _p: _clean_booklet(90)
        self._tr.extract_pymupdf_pages = _boom
        r = recover_pdf_text("x.pdf", expected_questions=90)
        self.assertEqual(r.recovery_status, STATUS_OK)
        self.assertEqual(r.extraction_method, NATIVE_PYPDF)
        self.assertFalse(called["mupdf"], "recovery must not run for clean native text")

    # -- E: header-degraded but legible -> recovered ---------------------
    def test_E_few_headers_but_legible_triggers_recovery(self):
        self._set(_clean_booklet(4), _clean_booklet(90))
        r = recover_pdf_text("x.pdf", expected_questions=90)
        self.assertEqual(r.recovery_status, STATUS_RECOVERED)
        self.assertEqual(r.extraction_method, RECOVERED_PYMUPDF)
        self.assertEqual(r.native_score.distinct_numbers, 4)
        self.assertEqual(r.recovered_score.distinct_numbers, 90)
        self.assertGreaterEqual(r.confidence, 0.7)

    # -- F: partial extraction -> recovered -----------------------------
    def test_F_partial_extraction_below_coverage_triggers_recovery(self):
        self._set(_clean_booklet(62), _clean_booklet(90))
        r = recover_pdf_text("x.pdf", expected_questions=90)
        self.assertEqual(r.recovery_status, STATUS_RECOVERED)

    # -- A: illegible native, legible recovery -> recovered ------------
    def test_A_illegible_native_legible_recovery_is_recovered(self):
        self._set(_symbol_soup_booklet(90), _clean_booklet(90))
        r = recover_pdf_text("x.pdf", expected_questions=90)
        self.assertEqual(r.recovery_status, STATUS_RECOVERED)
        self.assertEqual(r.extraction_method, RECOVERED_PYMUPDF)

    # -- A/2021: illegible native AND illegible recovery -> OCR_PENDING -
    def test_A_illegible_native_and_recovery_is_ocr_pending(self):
        self._set(_symbol_soup_booklet(90), _symbol_soup_booklet(90))
        r = recover_pdf_text("x.pdf", expected_questions=90)
        self.assertEqual(r.recovery_status, STATUS_OCR_PENDING)
        self.assertEqual(r.extraction_method, NATIVE_PYPDF)

    # -- C: native text is always preserved verbatim ------------------
    def test_C_native_text_preserved_even_when_recovered(self):
        native = _symbol_soup_booklet(90)
        clean = _clean_booklet(90)
        self._set(native, clean)
        r = recover_pdf_text("x.pdf", expected_questions=90)
        self.assertEqual(r.native_text, "\f".join(native))
        self.assertEqual(r.recovered_text, "\f".join(clean))
        self.assertEqual(r.chosen_text, r.recovered_text)

    def test_C_native_text_preserved_when_ocr_pending(self):
        native = _symbol_soup_booklet(90)
        self._set(native, _symbol_soup_booklet(90))
        r = recover_pdf_text("x.pdf", expected_questions=90)
        self.assertEqual(r.native_text, "\f".join(native))
        self.assertEqual(r.chosen_text, r.native_text)  # never silently swapped

    # -- D: no backend --------------------------------------------------
    def test_D_no_backend_header_degraded_yields_recovery_pending(self):
        self._set(_clean_booklet(4), None)
        r = recover_pdf_text("x.pdf", expected_questions=90)
        self.assertEqual(r.recovery_status, STATUS_RECOVERY_PENDING)
        self.assertEqual(r.extraction_method, NATIVE_PYPDF)
        self.assertIsNone(r.recovered_text)

    def test_D_no_backend_illegible_yields_ocr_pending(self):
        self._set(_symbol_soup_booklet(90), None)
        r = recover_pdf_text("x.pdf", expected_questions=90)
        self.assertEqual(r.recovery_status, STATUS_OCR_PENDING)
        self.assertIsNone(r.recovered_text)

    # -- determinism --------------------------------------------------
    def test_result_is_deterministic(self):
        self._set(_clean_booklet(50), _clean_booklet(90))
        a = recover_pdf_text("x.pdf", expected_questions=90).as_dict()
        b = recover_pdf_text("x.pdf", expected_questions=90).as_dict()
        self.assertEqual(a, b)

    # -- chosen_pages property mirrors chosen_text -----------------------
    def test_chosen_pages_returns_recovered_pages_when_recovery_wins(self):
        self._set(_clean_booklet(4), _clean_booklet(90))
        r = recover_pdf_text("x.pdf", expected_questions=90)
        self.assertEqual(r.extraction_method, RECOVERED_PYMUPDF)
        self.assertEqual(r.chosen_pages, r.recovered_pages)
        self.assertNotEqual(r.chosen_pages, r.native_pages)

    def test_chosen_pages_returns_native_pages_when_native_wins(self):
        native = _clean_booklet(90)
        self._set(native, None)
        r = recover_pdf_text("x.pdf", expected_questions=90)
        self.assertEqual(r.extraction_method, NATIVE_PYPDF)
        self.assertEqual(r.chosen_pages, r.native_pages)
        self.assertEqual(r.chosen_pages, native)

    # -- no expected_questions: falls back to the DISTINCT_FLOOR heuristic -
    def test_no_expected_questions_uses_distinct_floor_and_full_coverage(self):
        # 10 native headers with no expected count given falls below
        # DISTINCT_FLOOR (60) -> native is judged degraded on that basis
        # alone (_native_is_degraded's "no expected count" branch), and
        # _coverage() with expected=None always reports full (1.0) coverage
        # rather than dividing by a missing denominator.
        self._set(_clean_booklet(10), _clean_booklet(90))
        r = recover_pdf_text("x.pdf", expected_questions=None)
        self.assertEqual(r.native_score.distinct_numbers, 10)
        self.assertEqual(r.recovery_status, STATUS_RECOVERED)
        self.assertEqual(r.extraction_method, RECOVERED_PYMUPDF)
        self.assertTrue(any("no expected count given" in n for n in r.notes))

    # -- recovery attempted, legible but coverage still short -> RECOVERY_PENDING
    def test_legible_but_under_covered_recovery_is_recovery_pending_not_ocr(self):
        # Both native and recovered text are perfectly legible; the recovered
        # text simply does not reach RECOVERED_COVERAGE_MIN of the expected
        # question count. This must NOT be reported as OCR_PENDING (that is
        # reserved for genuinely illegible text) - it is a RECOVERY_PENDING.
        self._set(_clean_booklet(4), _clean_booklet(50))
        r = recover_pdf_text("x.pdf", expected_questions=90)
        self.assertEqual(r.recovery_status, STATUS_RECOVERY_PENDING)
        self.assertEqual(r.extraction_method, NATIVE_PYPDF)
        self.assertGreaterEqual(r.recovered_score.legibility_ratio, 0.68)
        self.assertIsNotNone(r.recovered_text)  # attempt kept for the record


# --------------------------------------------------------------------------- #
# pymupdf/fitz import-fallback branches - simulated via sys.modules, since
# this environment normally HAS pymupdf installed (optional dependency).
# Setting sys.modules[name] = None is the standard way to force the next
# `import name` to raise ModuleNotFoundError without uninstalling anything.
# --------------------------------------------------------------------------- #


class ImportFallbackTests(unittest.TestCase):
    def test_pymupdf_available_is_false_when_neither_backend_importable(self):
        with unittest.mock.patch.dict(sys.modules, {"pymupdf": None, "fitz": None}):
            self.assertFalse(pymupdf_available())

    def test_extract_pymupdf_pages_returns_none_when_neither_backend_importable(self):
        with unittest.mock.patch.dict(sys.modules, {"pymupdf": None, "fitz": None}):
            self.assertIsNone(extract_pymupdf_pages("x.pdf"))


# --------------------------------------------------------------------------- #
# Real INEP pilot PDFs
# --------------------------------------------------------------------------- #

_R2021_D1 = "2021_PV_impresso_D1_CD1.pdf"
_R2021_D2 = "2021_PV_impresso_D2_CD5.pdf"
_R2025_D1 = "2025_PV_impresso_D1_CD1.pdf"
_R2024_D1 = "2024_PV_impresso_D1_CD1.pdf"
_R2024_D2 = "2024_PV_impresso_D2_CD5.pdf"
_R2025_D2 = "2025_PV_impresso_D2_CD5.pdf"
_R2020_D1 = "2020_PV_impresso_D1_CD1.pdf"
_R2017_D1 = "2017_PV_impresso_D1_CD1.pdf"


@unittest.skipUnless(_have(_R2021_D1) and _have(_R2021_D2), "2021 pilot provas absent")
class Real2021OcrPendingTests(unittest.TestCase):
    """A/C - 2021 provas have no decodable text layer; neither pypdf nor MuPDF
    recovers real words, so the layer must report OCR_PENDING and keep native."""

    def test_A_native_2021_is_illegible(self):
        for fn in (_R2021_D1, _R2021_D2):
            r = recover_pdf_text(_pilot(fn), expected_questions=90)
            self.assertLess(r.native_score.legibility_ratio, 0.65, fn)

    def test_2021_is_ocr_pending_and_not_swapped(self):
        for fn in (_R2021_D1, _R2021_D2):
            r = recover_pdf_text(_pilot(fn), expected_questions=90)
            self.assertEqual(r.recovery_status, STATUS_OCR_PENDING, fn)
            self.assertEqual(r.extraction_method, NATIVE_PYPDF, fn)
            self.assertEqual(r.chosen_text, r.native_text, fn)

    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf backend not installed")
    def test_2021_mupdf_attempt_is_recorded_but_still_illegible(self):
        r = recover_pdf_text(_pilot(_R2021_D1), expected_questions=90)
        self.assertIsNotNone(r.recovered_text)  # attempt kept for the record
        self.assertLess(r.recovered_score.legibility_ratio, 0.65)
        self.assertTrue(any("rejected" in n or "OCR" in n for n in r.notes))


@unittest.skipUnless(_have(_R2025_D1), "2025 D1 pilot prova absent")
@unittest.skipUnless(HAVE_PYMUPDF, "pymupdf backend not installed")
class Real2025D1HeaderTests(unittest.TestCase):
    def test_E_native_finds_almost_no_headers_but_text_is_legible(self):
        r = recover_pdf_text(_pilot(_R2025_D1), expected_questions=90)
        self.assertLessEqual(r.native_score.distinct_numbers, 10)
        self.assertGreater(r.native_score.legibility_ratio, 0.75)

    def test_E_recovery_restores_full_question_range(self):
        r = recover_pdf_text(_pilot(_R2025_D1), expected_questions=90)
        self.assertEqual(r.recovery_status, STATUS_RECOVERED)
        self.assertEqual(r.extraction_method, RECOVERED_PYMUPDF)
        self.assertGreaterEqual(r.recovered_score.distinct_numbers, 88)
        self.assertEqual(r.recovered_score.number_min, 1)
        self.assertGreaterEqual(r.recovered_score.number_max, 88)


@unittest.skipUnless(
    _have(_R2024_D1) and _have(_R2024_D2) and _have(_R2025_D2),
    "2024/2025 D2 pilot provas absent",
)
@unittest.skipUnless(HAVE_PYMUPDF, "pymupdf backend not installed")
class RealPartialExtractionTests(unittest.TestCase):
    def test_F_native_partial_then_recovered_full(self):
        for fn in (_R2024_D1, _R2024_D2, _R2025_D2):
            r = recover_pdf_text(_pilot(fn), expected_questions=90)
            self.assertLess(r.native_score.distinct_numbers, 85, fn)
            self.assertGreater(r.native_score.legibility_ratio, 0.65, fn)
            self.assertEqual(r.recovery_status, STATUS_RECOVERED, fn)
            self.assertGreaterEqual(r.recovered_score.distinct_numbers, 88, fn)


@unittest.skipUnless(_have(_R2020_D1) and _have(_R2017_D1), "2016-2020 pilot provas absent")
class RealNoRegressionTests(unittest.TestCase):
    """G - booklets that already ingest fine must stay on native text, untouched."""

    def test_2020_and_2017_d1_stay_native_ok(self):
        for fn in (_R2020_D1, _R2017_D1):
            r = recover_pdf_text(_pilot(fn), expected_questions=90)
            self.assertEqual(r.extraction_method, NATIVE_PYPDF, fn)
            self.assertEqual(r.recovery_status, STATUS_OK, fn)
            self.assertGreater(r.native_score.legibility_ratio, 0.9, fn)

    def test_native_text_matches_pdfparser_extraction_path(self):
        from pypdf import PdfReader

        for fn in (_R2020_D1, _R2017_D1):
            reader = PdfReader(str(_pilot(fn)))
            parser_pages = [p.extract_text() or "" for p in reader.pages]
            r = recover_pdf_text(_pilot(fn), expected_questions=90)
            self.assertEqual(r.native_pages, parser_pages, fn)


@unittest.skipUnless(
    _have("2022_PV_impresso_D1_CD1.pdf") and _have("2023_PV_impresso_D1_CD1.pdf"),
    "2022/2023 pilot provas absent",
)
class Real2022And2023DiagnosticTests(unittest.TestCase):
    """DIAG - 2022/2023 have a legible text layer and full header coverage; the
    100%-requires_review seen in PHASE 10.5 is a downstream option/asset-gate
    matter, NOT a text-extraction defect. The recovery layer must leave them OK."""

    def test_2022_2023_are_native_ok(self):
        for fn in ("2022_PV_impresso_D1_CD1.pdf", "2022_PV_impresso_D2_CD5.pdf",
                   "2023_PV_impresso_D1_CD1.pdf", "2023_PV_impresso_D2_CD5.pdf"):
            if not _have(fn):
                continue
            r = recover_pdf_text(_pilot(fn), expected_questions=90)
            self.assertEqual(r.recovery_status, STATUS_OK, fn)
            self.assertEqual(r.extraction_method, NATIVE_PYPDF, fn)
            self.assertGreaterEqual(r.native_score.distinct_numbers, 88, fn)


class ParserUntouchedTests(unittest.TestCase):
    """G - this phase must not have changed PdfParser's public extraction surface."""

    def test_pdfparser_regexes_are_unchanged(self):
        from agente_ia_edu.services.ingestion_parser import PdfParser

        self.assertEqual(PdfParser.QUESTION_PATTERN.pattern, r"(?mi)^\s*Questão\s+(\d+)\s*")
        self.assertEqual(
            PdfParser.OPTION_PATTERN.pattern,
            r"(?ms)^\s*([A-E])\s{2,}(.+?)(?=^\s*[A-E]\s{2,}|\f|\Z)",
        )
        self.assertEqual(PdfParser.ANSWER_KEY_PATTERN.pattern, r"(?m)^\s*(\d{2,3})\s+([A-E])\s*$")

    def test_text_recovery_module_has_no_forbidden_imports(self):
        import ast

        src = (ROOT / "src" / "agente_ia_edu" / "services" / "text_recovery.py").read_text()
        tree = ast.parse(src)
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mods.add(node.module or "")
            elif isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
        joined = " ".join(mods)
        for forbidden in ("openai", "alembic", "sqlalchemy", "agente_ia_edu.db"):
            self.assertNotIn(forbidden, joined)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
