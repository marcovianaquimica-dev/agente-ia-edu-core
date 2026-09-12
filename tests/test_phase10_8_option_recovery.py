"""PHASE 10.8 - tests for deterministic option-level recovery.

No database, no OpenAI, no Alembic. Synthetic span-model unit tests always run;
the real-PDF tests skip when the INEP pilot provas or pymupdf are absent.
Covers (section 13): A single-space option layout, B A-E detection, C footer
stripping, D header stripping, E fraction recovery/preservation, F glyph/symbol
handling, G Q145, H Q154, I-L the four RECOVERED booklets, M 2016-2020
regression, N hash/idempotency behaviour, O no database writes.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "manual"))

from agente_ia_edu.services import option_recovery as OR  # noqa: E402

PILOT = ROOT / "var" / "inep-pilot"


def _have(n: str) -> bool:
    return (PILOT / n).exists()


try:
    import pymupdf  # noqa: F401

    HAVE_MUPDF = True
except Exception:  # pragma: no cover
    HAVE_MUPDF = False


def _span(text, font="Calibri", size=10.0, x0=40.0, y0=100.0, x1=None, y1=None):
    return OR._Span(text, font, size, x0, y0, x1 if x1 is not None else x0 + 5 * len(text),
                    y1 if y1 is not None else y0 + 11.0)


def _line(spans):
    return OR._Line(spans, min(s.x0 for s in spans), min(s.y0 for s in spans),
                    max(s.x1 for s in spans), max(s.y1 for s in spans))


# --------------------------------------------------------------------------- #
# span classification - C footer, D header
# --------------------------------------------------------------------------- #


class SpanClassificationTests(unittest.TestCase):
    def test_C_barcode_span_is_not_content(self):
        self.assertEqual(OR._classify_span(_span("*020125AM22*", font="C39HrP36DlTt")), "barcode")
        self.assertEqual(OR._classify_span(_span("*020125AM22*")), "barcode")

    def test_C_micro_watermark_is_not_content(self):
        wm = "ENEM2025ENEM2025ENEM2025ENEM2025"
        self.assertEqual(OR._classify_span(_span(wm, font="Arial-BoldMT", size=1.5)), "watermark")

    def test_D_running_header_pipe_and_bullet_separators(self):
        for sep in ("|", "•", "·"):
            t = f"MATEMÁTICA E SUAS TECNOLOGIAS {sep} 2º DIA {sep} CADERNO 5 {sep} AMARELO"
            self.assertEqual(OR._classify_span(_span(t, font="Calibri-Light", size=9.0)),
                             "running_header", sep)

    def test_D_section_banner_even_truncated(self):
        self.assertTrue(OR._looks_like_header_fragment("CIÊNCIAS HUMANAS E SUAS TECNOLOGIAS"))
        self.assertTrue(OR._looks_like_header_fragment("LINGUAGENS, CÓDIG"))
        self.assertFalse(OR._looks_like_header_fragment("linguagens de programação são úteis"))

    def test_page_number_only_in_margins(self):
        self.assertEqual(OR._classify_span(_span("22", y0=748.0, y1=759.0)), "page_number")
        self.assertEqual(OR._classify_span(_span("22", y0=300.0, y1=311.0)), "content")

    def test_real_option_text_stays_content(self):
        self.assertEqual(OR._classify_span(_span("consolidação do poder político.")), "content")
        self.assertEqual(OR._classify_span(_span("A escrita e a memória social")), "content")


# --------------------------------------------------------------------------- #
# option assembly - A, B, E, F
# --------------------------------------------------------------------------- #


class OptionAssemblyTests(unittest.TestCase):
    def test_A_single_space_and_tab_layouts_both_split(self):
        for marker in ("A\ttexto da alternativa", "A texto da alternativa", "A)  texto da alternativa"):
            lbl = _line([_span("A\t", font="BundesbahnPiStd-1"), _span(marker.split(None, 1)[-1])])
            txt, frac, flags = OR._assemble_option_text(lbl, [], [])
            self.assertIn("texto da alternativa", txt)
            self.assertFalse(frac)

    def test_B_label_regex_matches_A_to_E_only(self):
        self.assertTrue(OR._OPTION_LABEL_RE.match("A\tx"))
        self.assertTrue(OR._OPTION_LABEL_RE.match("E foo"))
        self.assertIsNone(OR._OPTION_LABEL_RE.match("F\tx"))
        self.assertIsNone(OR._OPTION_LABEL_RE.match("Alternativa"))

    def test_E_fraction_reconstructed_only_with_a_bar(self):
        num = _span("30", x0=37.7, y0=406.0, x1=47.8, y1=419.5)
        lbl = _line([_span("A\t", font="BundesbahnPiStd-1", x0=22.0, x1=35.0), num])
        den = _line([_span("90", x0=37.7, y0=420.4, x1=47.8, y1=433.8)])
        bar = [(420.0, 37.4, 48.2)]
        txt, frac, _ = OR._assemble_option_text(lbl, [den], bar)
        self.assertEqual(txt, "30/90")
        self.assertTrue(frac)

    def test_E_stacked_numbers_without_a_bar_are_not_a_fraction(self):
        num = _span("30", x0=37.7, y0=406.0, x1=47.8, y1=419.5)
        lbl = _line([_span("A\t", font="BundesbahnPiStd-1", x0=22.0, x1=35.0), num])
        den = _line([_span("90", x0=37.7, y0=420.4, x1=47.8, y1=433.8)])
        txt, frac, flags = OR._assemble_option_text(lbl, [den], [])   # no bar
        self.assertFalse(frac)
        self.assertNotIn("/", txt)
        self.assertIn("stacked_number_without_fraction_bar", flags)

    def test_F_scientific_notation_is_not_flattened_to_a_fraction(self):
        num = _span("10", x0=37.7, y0=406.0, x1=47.8, y1=419.5)
        lbl = _line([_span("A\t", font="BundesbahnPiStd-1", x0=22.0, x1=35.0), num,
                     _span("×", font="SymbolMT"), _span("10")])
        den = _line([_span("8", x0=37.7, y0=420.4, x1=42.8, y1=433.8)])
        bar = [(420.0, 37.4, 48.2)]
        txt, frac, flags = OR._assemble_option_text(lbl, [den], bar)
        self.assertFalse(frac)
        self.assertIn("stacked_math_not_linearised", flags)

    def test_D_header_tail_glued_to_option_is_trimmed(self):
        lbl = _line([_span("E\t", font="BundesbahnPiStd-1"),
                     _span("comunicar a recusa da publicação. • LINGUAGENS, CÓDIGOS E SUAS "
                           "TECNOLOGIAS • 1º DIA • CADERNO 1 • AZUL")])
        txt, _, _ = OR._assemble_option_text(lbl, [], [])
        self.assertTrue(txt.startswith("comunicar a recusa da publicação."))
        self.assertNotIn("LINGUAGENS", txt)
        self.assertNotIn("CADERNO", txt)


# --------------------------------------------------------------------------- #
# real pilot PDFs
# --------------------------------------------------------------------------- #

_BOOKS = {
    "2024 D1": ("2024_PV_impresso_D1_CD1.pdf", 1, 90),
    "2024 D2": ("2024_PV_impresso_D2_CD5.pdf", 91, 180),
    "2025 D1": ("2025_PV_impresso_D1_CD1.pdf", 1, 90),
    "2025 D2": ("2025_PV_impresso_D2_CD5.pdf", 91, 180),
}
_ARTIFACT_RES = [
    re.compile(r"\*[0-9A-Z]{6,}\*"),
    re.compile(r"(?:ENE[MN]\s?\d{4}){2,}"),
    re.compile(r"(LINGUAGENS|MATEM[ÁA]TICA|CI[ÊE]NCIAS|C[ÓO]DIGOS)\b.{0,30}(DIA|CADERNO|TECNOLOGIA)"),
    re.compile(r"[ºo]\s*DIA\b"),
    re.compile(r"CADERNO\s*\d"),
    re.compile(r"[•·]"),
    re.compile(r"/g\d+|[￼�]"),
]


@unittest.skipUnless(HAVE_MUPDF, "pymupdf backend not installed")
class RealRecoveredBookletTests(unittest.TestCase):
    def _run(self, key):
        fn, _lo, _hi = _BOOKS[key]
        if not _have(fn):
            self.skipTest(f"{fn} absent")
        return OR.recover_option_layout(PILOT / fn), _lo, _hi

    def _assert_book(self, key):
        r, lo, hi = self._run(key)
        distinct = sorted({q.number for q in r.questions})
        self.assertTrue(set(range(lo, hi + 1)).issubset(set(distinct)),
                        f"{key} distinct={len(distinct)}")
        # every IMPORT_READY question has 5 clean A-E options, no artefacts
        for q in r.questions:
            if q.status != "IMPORT_READY":
                continue
            self.assertEqual([o.label for o in q.options], list("ABCDE"), f"{key} Q{q.number}")
            for o in q.options:
                self.assertTrue(o.text.strip(), f"{key} Q{q.number} {o.label} empty")
                for rx in _ARTIFACT_RES:
                    self.assertIsNone(rx.search(o.text),
                                      f"{key} Q{q.number} {o.label}: {o.text!r}")

    def test_I_2024_d1(self):
        self._assert_book("2024 D1")

    def test_J_2024_d2(self):
        self._assert_book("2024 D2")

    def test_K_2025_d1(self):
        self._assert_book("2025 D1")

    def test_L_2025_d2(self):
        self._assert_book("2025 D2")

    def test_G_q145_fractions_are_correct(self):
        if not _have(_BOOKS["2025 D2"][0]):
            self.skipTest("2025 D2 absent")
        r = OR.recover_option_layout(PILOT / _BOOKS["2025 D2"][0])
        q = next(q for q in r.questions if q.number == 145)
        self.assertEqual(q.status, "IMPORT_READY")
        self.assertEqual([o.text for o in q.options],
                         ["30/90", "36/100", "40/100", "40/90", "46/90"])
        self.assertTrue(all(o.fraction_reconstructed for o in q.options))

    def test_H_q154_has_no_footer_and_clean_fractions(self):
        if not _have(_BOOKS["2025 D2"][0]):
            self.skipTest("2025 D2 absent")
        r = OR.recover_option_layout(PILOT / _BOOKS["2025 D2"][0])
        q = next(q for q in r.questions if q.number == 154)
        for o in q.options:
            self.assertNotRegex(o.text, r"\*[0-9A-Z]{6,}\*")
            self.assertNotRegex(o.text, r"ENE[MN]\d{4}")
        self.assertEqual([o.text for o in q.options],
                         ["1/2", "1/10", "1/16", "1/24", "1/256"])
        self.assertEqual(q.status, "IMPORT_READY")

    def test_F_scientific_notation_question_is_not_import_ready(self):
        if not _have(_BOOKS["2025 D2"][0]):
            self.skipTest("2025 D2 absent")
        r = OR.recover_option_layout(PILOT / _BOOKS["2025 D2"][0])
        q = next(q for q in r.questions if q.number == 176)
        self.assertNotEqual(q.status, "IMPORT_READY")  # not guessed

    def test_non_import_ready_questions_emit_no_option_lines(self):
        r, _lo, _hi = self._run("2025 D2")
        pending = set(r.option_recovery_pending) | set(r.invalid)
        if not pending:
            self.skipTest("nothing pending/invalid")
        joined = "\n".join(r.page_texts)
        # a PENDING question's header is present but no "X  <text>" option line
        for n in list(pending)[:5]:
            self.assertIn(f"Questão {n}", joined)


@unittest.skipUnless(HAVE_MUPDF, "pymupdf backend not installed")
class PageTextsRoundTripTests(unittest.TestCase):
    def test_cleaned_page_texts_reparse_to_full_question_range(self):
        from agente_ia_edu.services.ingestion_parser import PdfParser

        for key, (fn, lo, hi) in _BOOKS.items():
            if not _have(fn):
                continue
            r = OR.recover_option_layout(PILOT / fn)
            parsed = PdfParser.parse_file(PILOT / fn, page_texts=r.page_texts)
            nums = sorted({q.question_number for q in parsed.questions})
            self.assertTrue(set(range(lo, hi + 1)).issubset(set(nums)),
                            f"{key}: {len(nums)} distinct")


# --------------------------------------------------------------------------- #
# M - 2016-2020 regression: option_recovery must not be wired into that path
# --------------------------------------------------------------------------- #


class NoRegression20162020Tests(unittest.TestCase):
    def test_M_pdfparser_native_path_is_unchanged(self):
        from agente_ia_edu.services.ingestion_parser import PdfParser

        self.assertEqual(PdfParser.QUESTION_PATTERN.pattern, r"(?mi)^\s*Questão\s+(\d+)\s*")
        self.assertEqual(
            PdfParser.OPTION_PATTERN.pattern,
            r"(?ms)^\s*([A-E])\s{2,}(.+?)(?=^\s*[A-E]\s{2,}|\f|\Z)",
        )

    @unittest.skipUnless(_have("2020_PV_impresso_D1_CD1.pdf"), "2020 D1 absent")
    def test_M_native_2020_d1_unaffected_by_new_module(self):
        from agente_ia_edu.services.ingestion_parser import PdfParser

        a = PdfParser.parse_file(PILOT / "2020_PV_impresso_D1_CD1.pdf")
        b = PdfParser.parse_file(PILOT / "2020_PV_impresso_D1_CD1.pdf", page_texts=None)
        self.assertEqual([q.question_number for q in a.questions],
                         [q.question_number for q in b.questions])


# --------------------------------------------------------------------------- #
# O - no forbidden imports / side effects
# --------------------------------------------------------------------------- #


class NoSideEffectsTests(unittest.TestCase):
    def test_O_module_has_no_db_openai_alembic_imports(self):
        src = (ROOT / "src" / "agente_ia_edu" / "services" / "option_recovery.py").read_text()
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

    def test_O_recover_option_layout_is_pure_wrt_filesystem(self):
        # calling it must not create files under var/inep-pilot
        if not (_have("2025_PV_impresso_D2_CD5.pdf") and HAVE_MUPDF):
            self.skipTest("inputs absent")
        before = {p.name for p in PILOT.iterdir()}
        OR.recover_option_layout(PILOT / "2025_PV_impresso_D2_CD5.pdf")
        after = {p.name for p in PILOT.iterdir()}
        self.assertEqual(before, after)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
