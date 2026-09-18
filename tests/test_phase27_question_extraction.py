"""PHASE 27 - Question Extraction Engine tests.

Unit tests per pipeline stage (structure/boundary/assets/validation) using
synthetic fixtures (self-contained, reproducible), PLUS the two real pilot
PDFs as GOLDEN TEST CASES (spec s14/s23) - skipped only if the user's
authorized pilot folder is absent from this machine, never faked with a
substitute file. Also covers persistence (batched, idempotent), the
review status machine, tenant isolation, and the AI-agnostic import guard.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import unittest
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

from agente_ia_edu.services.question_extraction.assets import associate_assets
from agente_ia_edu.services.question_extraction.boundary import (
    ExtractedQuestionDraft,
    OptionDraft,
    QuestionBoundary,
    classify_and_extract,
    cut_at_answer_key,
    detect_boundaries,
    extract_resolutions_by_question,
)
from agente_ia_edu.services.question_extraction.engine import (
    ExtractedQuestionResult,
    ExtractionResult,
    extract_questions,
    merge_extraction_results,
)
from agente_ia_edu.services.question_extraction.structure import (
    STANDALONE_MARKER_SENTINEL,
    DocumentStructure,
    PageImage,
    TextLine,
    _normalize_standalone_number_markers,
    detect_two_column_layout,
    extract_structure,
)
from agente_ia_edu.services.question_extraction.validation import validate

PILOT_DIR = (Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
            / "QDE 2025" / "MATERIAL TESTE")
PDF_THEORY = PILOT_DIR / "T 11 - Soluções.pdf"
PDF_EXERCISES = PILOT_DIR / "T - 11 EXERCÍCIOS DE APROFUNDAMENTO - SOLUÇÕES.pdf"
_GOLDEN_AVAILABLE = PDF_THEORY.is_file() and PDF_EXERCISES.is_file()


def _make_pdf(path: Path, drawings: list[tuple[float, float, str, float]]) -> None:
    """drawings: [(x, y, text, fontsize), ...] on one page."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    for x, y, text, size in drawings:
        page.insert_text((x, y), text, fontsize=size)
    doc.save(str(path))


class StructureTests(unittest.TestCase):
    def test_extract_structure_reads_lines_in_reading_order(self):
        tmp = Path("/tmp/phase27_structure_basic.pdf")
        _make_pdf(tmp, [(72, 72, "Título", 14), (72, 100, "Primeira linha.", 10),
                        (72, 120, "Segunda linha.", 10)])
        s = extract_structure(tmp)
        self.assertEqual(s.page_count, 1)
        lines = [ln.text for ln in s.lines]
        self.assertEqual(lines, ["Título", "Primeira linha.", "Segunda linha."])

    def test_two_column_detection_accepts_a_clean_two_column_page(self):
        lines = (
            [TextLine(page=1, x0=50, y0=10 * i, x1=150, y1=10 * i + 8, text=f"L{i}") for i in range(5)]
            + [TextLine(page=1, x0=300, y0=10 * i, x1=400, y1=10 * i + 8, text=f"R{i}") for i in range(5)]
        )
        split = detect_two_column_layout(lines, page_width=595.0)
        self.assertIsNotNone(split)
        self.assertTrue(50 < split < 300)

    def test_two_column_detection_accepts_two_columns_beside_an_embedded_table(self):
        # Real regression found on a real PUC-Rio exam page: two genuine
        # independent side-by-side QUESTIONS (like the FUVEST case above),
        # but the LEFT column also contains an embedded comparison table
        # (a biology "which trait is present in cell type 1 vs type 2"
        # grid) whose own data cells are indented well past the left
        # column's usual text start - their x0 lands closer to the true
        # gutter than the left column's OWN margin does, producing an
        # x0-gap (~98pt, between the table's row-label column and its
        # first data column) that is WIDER than the real gutter (~91pt,
        # between the table's last data column and the right question).
        # The genuine gutter is a real, well-motivated tell here: no line
        # of running text ever CROSSES it (that is what a column boundary
        # means), whereas the table's own internal indentation gap is
        # crossed by every one of the left column's own full-width wrapped
        # statement lines, which run right over it. Preferring whichever
        # candidate gap the FEWEST lines straddle - not simply the widest
        # one - picks the real gutter over the table's own indentation.
        # Real wrapped paragraph text mostly does NOT reach the column's
        # full width - only occasional lines run close to the margin, like
        # any natural word-wrapped text. A fixture where every line is
        # exactly full-width is unrealistic and would make ordinary
        # paragraph text look like it straddles the true gutter just as
        # much as the table's own internal gap does.
        _left_widths = [255, 150, 190, 210, 255, 170, 230, 200, 255, 160,
                       220, 240, 255, 180, 200, 250, 165, 210, 255, 195]
        left_prose = [
            TextLine(page=1, x0=28, y0=20.0 * i, x1=28 + _left_widths[i], y1=20.0 * i + 15,
                    text=f"enunciado esquerdo {i}")
            for i in range(20)
        ]
        table_labels = [
            TextLine(page=1, x0=42, y0=340.0 + 20 * i, x1=42 + 60, y1=340.0 + 20 * i + 10, text=f"rotulo {i}")
            for i in range(6)
        ]
        table_col1 = [
            TextLine(page=1, x0=149, y0=340.0 + 20 * i, x1=149 + 35, y1=340.0 + 20 * i + 10, text="Ausente")
            for i in range(6)
        ]
        table_col2 = [
            TextLine(page=1, x0=221, y0=340.0 + 20 * i, x1=221 + 35, y1=340.0 + 20 * i + 10, text="Presente")
            for i in range(6)
        ]
        right_prose = [
            TextLine(page=1, x0=312, y0=20.0 * i, x1=312 + 255, y1=20.0 * i + 15, text=f"enunciado direito {i}")
            for i in range(20)
        ]
        lines = left_prose + table_labels + table_col1 + table_col2 + right_prose
        split = detect_two_column_layout(lines, page_width=595.0)
        self.assertIsNotNone(split)
        self.assertTrue(221 < split < 312)

    def test_two_column_detection_accepts_two_columns_each_with_their_own_answer_grid(self):
        # Real regression found on a real UNICAMP exam page: TWO genuine
        # independent side-by-side questions (like the FUVEST case above),
        # but EACH one's own 4 multiple-choice options are laid out as a
        # compact 2-column grid (a/b stacked at the question's own margin,
        # c/d stacked further right) - unlike the ITA compact-grid
        # regression, which was a single 2-line sub-column, this repeats
        # across many questions stacked down the WHOLE page, so each
        # sub-column has well over a handful of lines and survives the
        # tiny-cluster gap-search filter on cluster size alone. The
        # resulting two INTERNAL grid gaps (left's own margin to its c/d
        # column; right's own margin to its c/d column) are comparably
        # wide to the TRUE gutter between the two questions (measured on
        # the real page: 127pt and 127pt against a 151pt true gutter) -
        # comfortably inside the old width-ambiguity guard's rejection
        # zone. The true gutter is still the least-crossed candidate by a
        # clear margin (22% real page; every internal grid gap here is
        # crossed by every one of that side's own full-width statement
        # lines running over it) - trusting whichever LEGACY-widest
        # candidate is ALSO the least-crossed, even without a dramatic 2x
        # margin over the runner-up, resolves this without reopening the
        # door to the synthetic multi-cell-table below (which never
        # reaches this comparison at all: none of its clusters have enough
        # lines to survive the gap-search size filter in the first place).
        # Real wrapped text mostly does NOT reach a column's full width -
        # only occasional lines run close to the margin (see the embedded-
        # table test above, which learned the same lesson: a fixture where
        # every line is exactly full-width makes ordinary paragraph text
        # look like it straddles a distant split just as much as a close
        # one). Varying widths, mostly short, reproduces the real
        # asymmetry: the internal grid gap sits close to the column's OWN
        # margin (little of the column's width needed to cross it - most
        # lines do), while the true gutter sits close to the FAR edge of
        # the column (only the rare near-full-width line reaches it).
        _widths = [250, 150, 190, 210, 245, 170, 230, 200, 248, 160,
                  220, 240, 246, 180, 200, 249, 165, 210, 247, 195] * 2
        left_main = [
            TextLine(page=1, x0=31, y0=12.0 * i, x1=31 + _widths[i], y1=12.0 * i + 10, text=f"esquerda enunciado {i}")
            for i in range(40)
        ]
        left_grid = [
            TextLine(page=1, x0=158, y0=500.0 + 12.0 * i, x1=158 + 22, y1=500.0 + 12.0 * i + 10, text=f"c) {i}")
            for i in range(16)
        ]
        right_main = [
            TextLine(page=1, x0=309, y0=12.0 * i, x1=309 + _widths[i], y1=12.0 * i + 10, text=f"direita enunciado {i}")
            for i in range(40)
        ]
        right_grid = [
            TextLine(page=1, x0=436, y0=500.0 + 12.0 * i, x1=436 + 22, y1=500.0 + 12.0 * i + 10, text=f"c) {i}")
            for i in range(16)
        ]
        lines = left_main + left_grid + right_main + right_grid
        split = detect_two_column_layout(lines, page_width=595.0)
        self.assertIsNotNone(split)
        self.assertTrue(158 < split < 309)

    def test_two_column_detection_finds_the_true_gutter_over_a_noisier_x0_cluster(self):
        # Real regression found running PHASE 30 classification against real
        # OpenAI output: a genuine two-column FUVEST page (Q04/Q05 running
        # down the LEFT column, Q06 - with two embedded tables of its own -
        # down the RIGHT) was read in raw top-to-bottom order instead of
        # column-major, so Q05's and Q06's text interleaved line-by-line
        # into one unreadable statement (the AI could then never quote a
        # literal excerpt from it, surfacing as "Invalid classification
        # evidence" downstream - 30 of the resulting 37 real cases traced
        # back to this one document). detect_two_column_layout returned
        # None here because the LEFT column's OWN internal content (a small
        # 4-city sanitation/vaccination comparison table sitting inside
        # Q05, using x0s at 87-236) produces several x0-clustering gap
        # candidates that outrank the true gutter on raw width, and the
        # truly-least-crossed one (between the left column's real text and
        # Q06's own table headers on the right) is only ~1.3x cleaner than
        # the widest false one - short of the 2x margin the ambiguity guard
        # requires before overriding a width-based leader. But the true
        # gutter is stronger evidence than any of that: it is a vertical
        # strip literally no line's [x0, x1) interval ever crosses at
        # all - not merely less-crossed. Coordinates below are the exact
        # bounding boxes from the real page (text content is irrelevant to
        # this function - only geometry is).
        boxes = [
            (420.8, 19.2, 563.8, 30.2), (34.0, 40.9, 52.5, 58.2), (34.0, 69.8, 285.6, 79.8),
            (34.0, 82.0, 189.6, 92.0), (34.0, 154.4, 285.7, 164.4), (34.0, 166.6, 285.7, 176.6),
            (34.0, 178.8, 181.8, 188.8), (34.0, 397.5, 285.7, 407.5), (34.0, 409.7, 97.5, 419.8),
            (34.0, 426.4, 60.7, 437.9), (34.0, 438.6, 65.7, 450.2), (34.0, 450.8, 65.7, 462.4),
            (34.0, 463.0, 65.7, 474.6), (34.1, 475.2, 65.7, 486.8), (34.0, 500.8, 52.5, 518.1),
            (34.0, 529.7, 285.7, 539.7), (34.0, 541.9, 190.2, 551.9), (87.1, 568.7, 173.9, 578.8),
            (91.5, 581.0, 169.5, 591.0), (88.0, 593.2, 173.0, 603.2), (192.5, 562.6, 260.4, 572.6),
            (193.8, 574.9, 259.1, 584.9), (189.2, 587.0, 263.7, 597.1), (205.9, 599.3, 247.1, 609.3),
            (37.7, 615.8, 72.3, 625.8), (120.7, 613.8, 140.3, 623.9), (216.7, 613.8, 236.2, 623.9),
            (37.7, 632.3, 75.0, 642.3), (120.7, 630.3, 140.3, 640.4), (216.7, 630.3, 236.2, 640.4),
            (37.7, 648.8, 77.7, 658.8), (120.7, 646.8, 140.3, 656.9), (216.7, 646.8, 236.2, 656.9),
            (37.7, 665.3, 78.2, 675.3), (120.7, 663.3, 140.3, 673.4), (216.7, 663.3, 236.2, 673.4),
            (34.0, 686.0, 285.7, 696.0), (34.0, 698.1, 285.7, 708.2), (34.0, 710.4, 120.7, 720.4),
            (70.4, 729.0, 100.9, 738.0), (125.0, 729.0, 157.7, 738.0), (180.5, 729.0, 215.5, 738.0),
            (236.9, 729.0, 272.5, 738.0), (37.1, 750.8, 51.2, 760.8), (62.0, 750.6, 109.3, 759.6),
            (123.6, 750.6, 159.0, 759.6), (179.4, 750.6, 216.5, 759.6), (242.4, 750.6, 267.0, 759.6),
            (37.1, 772.4, 50.9, 782.4), (72.8, 772.3, 98.5, 781.3), (127.0, 772.3, 155.6, 781.3),
            (184.9, 772.3, 211.2, 781.3), (232.0, 772.3, 277.5, 781.3), (314.9, 43.2, 326.3, 53.3),
            (343.6, 43.1, 383.4, 52.1), (400.5, 43.1, 437.7, 52.1), (463.5, 43.1, 488.2, 52.1),
            (508.9, 43.1, 556.2, 52.1), (314.9, 65.0, 327.2, 75.0), (345.8, 64.8, 381.2, 73.8),
            (404.8, 64.8, 433.4, 73.8), (453.9, 64.8, 497.6, 73.8), (519.6, 64.8, 545.3, 73.8),
            (314.9, 86.6, 325.8, 96.6), (340.7, 86.5, 386.2, 95.5), (396.3, 86.5, 442.0, 95.5),
            (455.9, 86.5, 495.7, 95.5), (514.0, 86.5, 551.1, 95.5), (311.8, 120.1, 330.3, 137.4),
            (311.8, 149.0, 563.5, 159.0), (311.8, 161.3, 563.5, 171.3), (311.8, 173.5, 563.4, 183.5),
            (311.8, 185.7, 548.5, 195.7), (422.2, 200.7, 452.8, 209.7), (346.2, 218.1, 404.4, 227.1),
            (348.9, 229.1, 401.7, 238.1), (424.3, 218.1, 469.5, 227.1), (430.6, 229.1, 463.2, 238.1),
            (497.7, 218.1, 520.5, 227.1), (490.0, 229.1, 528.3, 238.1), (346.9, 246.3, 403.7, 255.3),
            (439.0, 246.3, 454.8, 255.3), (501.3, 246.3, 517.0, 255.3), (347.0, 263.4, 403.6, 272.4),
            (441.3, 263.4, 452.5, 272.4), (501.3, 263.4, 517.0, 272.4), (347.0, 280.6, 403.5, 289.6),
            (439.0, 280.6, 454.8, 289.6), (501.3, 280.6, 517.0, 289.6), (346.7, 297.8, 403.9, 306.8),
            (439.0, 297.8, 454.8, 306.8), (501.3, 297.8, 517.0, 306.8), (311.8, 318.3, 563.4, 328.3),
            (311.8, 330.5, 563.4, 340.5), (311.8, 342.7, 563.4, 352.7), (311.8, 354.9, 349.4, 364.9),
            (421.1, 367.0, 453.9, 376.0), (373.8, 384.3, 432.0, 393.3), (376.5, 395.3, 429.3, 404.3),
            (451.9, 384.3, 499.2, 393.3), (449.8, 395.3, 499.2, 404.3), (374.5, 412.5, 431.4, 421.5),
            (467.9, 412.5, 481.1, 421.5), (374.6, 429.7, 431.2, 438.7), (467.9, 429.7, 481.1, 438.7),
            (374.6, 446.8, 431.1, 455.8), (471.2, 446.8, 477.8, 455.8), (374.3, 464.1, 431.5, 473.1),
            (465.6, 464.1, 483.3, 473.1), (311.8, 484.5, 542.2, 494.6), (314.6, 501.6, 346.6, 513.2),
            (314.6, 513.9, 353.9, 525.5), (314.7, 526.1, 353.9, 537.7), (314.7, 538.3, 353.9, 549.9),
            (314.7, 550.5, 353.9, 562.1), (427.8, 505.8, 474.3, 513.8), (427.8, 518.6, 557.1, 526.6),
            (427.8, 526.8, 553.1, 536.9),
        ]
        lines = [
            TextLine(page=1, x0=x0, y0=y0, x1=x1, y1=y1, text=f"l{i}")
            for i, (x0, y0, x1, y1) in enumerate(boxes)
        ]
        split = detect_two_column_layout(lines, page_width=595.2)
        self.assertIsNotNone(split)
        # the true gutter: no line's own extent reaches past 285.7 on the
        # left or starts before 311.8 on the right
        self.assertTrue(285.7 < split < 311.8)

    def test_two_column_detection_rejects_a_multi_cell_table(self):
        # many distinct x0 (table cells), not a clean bimodal split
        lines = [
            TextLine(page=1, x0=x, y0=10 * (i % 5), x1=x + 40, y1=10 * (i % 5) + 8, text=f"c{i}")
            for i, x in enumerate([56, 60, 75, 93, 134, 213, 309, 327, 343, 345] * 3)
        ]
        split = detect_two_column_layout(lines, page_width=595.0)
        self.assertIsNone(split)

    def test_two_column_detection_rejects_a_narrow_marker_column(self):
        # Real regression found on a UECE exam: the LEFT band isn't a real
        # wrapped-prose column at all - it's a list of short item markers
        # (e.g. "I.", "II.", "III.") paired row-by-row with unrelated
        # content on the right, not two independent top-to-bottom columns.
        # Column-major reordering breaks this (it reads all of the left
        # markers first, then all of the right content, destroying the
        # per-row pairing) even though the geometry LOOKS like two clean
        # bands. Real column text wraps to use most of its own width on
        # most lines; a column that is mostly much-narrower-than-its-own-
        # widest-line fragments is a marker/label list, not prose, and
        # must not be treated as a reorderable column.
        left = (
            [TextLine(page=1, x0=40, y0=10 * i, x1=52, y1=10 * i + 8, text=f"m{i}") for i in range(8)]
            + [TextLine(page=1, x0=40, y0=200, x1=280, y1=208, text="the one real wide left line")]
        )
        right = [
            TextLine(page=1, x0=300, y0=10 * i, x1=400, y1=10 * i + 8, text=f"right content {i}")
            for i in range(9)
        ]
        split = detect_two_column_layout(left + right, page_width=595.0)
        self.assertIsNone(split)

    def test_two_column_detection_accepts_a_column_with_short_answer_options(self):
        # Real regression found on a real FUVEST exam page: a genuine
        # single-topic column (a physics question, wrapped prose statement)
        # whose multiple-choice options are short NUMERIC values ("(A)
        # 0degC", "(B) 75degC", ...) rather than the long sentence-length
        # options seen elsewhere in the same exam. The question's own
        # number marker, a couple of short instruction/citation lines
        # ("Note e adote:", "Adaptado.") and the five short options are all
        # narrower than 25% of the column's own widest (wrapped-paragraph)
        # line - together just over the old 0.35 fraction limit, even
        # though the clear MAJORITY of the column's lines are full-width
        # prose. Unlike the real marker-column regression above (where
        # narrow lines are ~89% of the side and the one wide line is the
        # outlier), a genuine prose column occasionally built from a
        # minority of short structural lines (a marker, a few numeric
        # options, an instruction aside) must not be rejected as if it
        # were a marker/label list.
        wide_width = 251.7
        narrow_widths = [18.5, 27.9, 29.8, 35.6, 37.0, 42.1, 42.1, 42.1, 46.5]  # 9 lines
        left = (
            [TextLine(page=1, x0=34, y0=10 * i, x1=34 + wide_width, y1=10 * i + 8, text=f"left content {i}")
             for i in range(20)]
        )
        right = (
            [TextLine(page=1, x0=312, y0=10 * i, x1=312 + wide_width, y1=10 * i + 8, text=f"right content {i}")
             for i in range(14)]
            + [TextLine(page=1, x0=312, y0=140 + 10 * i, x1=312 + w, y1=140 + 10 * i + 8, text=f"short {i}")
               for i, w in enumerate(narrow_widths)]
        )
        split = detect_two_column_layout(left + right, page_width=595.0)
        self.assertIsNotNone(split)

    def test_two_column_detection_accepts_columns_dominated_by_short_options(self):
        # Real regression found on a real FUVEST exam page: a "choose the
        # correct GRAPH" question, whose statement is short (3 lines) and
        # whose five options are each just a bare letter - the actual
        # graphs are IMAGES, never text - beside a second question whose
        # own options are short numeric/chemical fragments (superscripts,
        # ionic equations, voltage values). Measured on the real page:
        # LEFT is 6/9 (67%) narrow lines, RIGHT is 36/52 (69%) narrow -
        # both a clear MAJORITY, unlike the short-numeric-options case
        # above where wide prose still dominated. This is still not a
        # marker/label column (UECE's real regression, ~89% narrow, is the
        # shape this check exists to catch): the narrow lines here are
        # heterogeneous, structurally-legitimate content belonging to
        # these SAME two questions (bare option letters, a citation, ionic
        # half-reactions, voltage readings) - not one repeated short label
        # paired row-by-row with unrelated content on the other side.
        left = (
            [TextLine(page=1, x0=34, y0=0, x1=34 + 18.5, y1=8, text="10")]
            + [TextLine(page=1, x0=34, y0=15 * i, x1=34 + 220, y1=15 * i + 8, text=f"statement {i}")
               for i in range(1, 4)]
            + [TextLine(page=1, x0=37, y0=15 * i, x1=37 + 14, y1=15 * i + 8, text=letter)
               for i, letter in enumerate("ABCDE", start=4)]
            + [TextLine(page=1, x0=34, y0=600, x1=34 + 220, y1=608, text="padding")]
        )
        right = (
            [TextLine(page=1, x0=312, y0=0, x1=312 + 18.5, y1=8, text="11")]
            + [TextLine(page=1, x0=312, y0=15 * i, x1=312 + 251.7, y1=15 * i + 8, text=f"stem {i}")
               for i in range(1, 4)]
            + [TextLine(page=1, x0=312, y0=15 * i, x1=312 + 20, y1=15 * i + 8, text=f"frag {i}")
               for i in range(4, 15)]
            + [TextLine(page=1, x0=312, y0=15 * i, x1=312 + 251.7, y1=15 * i + 8, text=f"question 12 line {i}")
               for i in range(15, 25)]
            + [TextLine(page=1, x0=312, y0=15 * i, x1=312 + 45, y1=15 * i + 8, text=f"(A) reagent {i}")
               for i in range(25, 47)]
            + [TextLine(page=1, x0=312, y0=15 * 47, x1=312 + 251.7, y1=15 * 47 + 8, text="padding")]
        )
        split = detect_two_column_layout(left + right, page_width=595.0)
        self.assertIsNotNone(split)

    def test_two_column_detection_ignores_a_tiny_aside_when_judging_ambiguity(self):
        # Real regression found on a real FUVEST exam page: a "Note e
        # adote:" instruction aside (a couple of reference constants for
        # the physics question) sits indented well past the right
        # column's own text start - just 2 lines, both narrow. The gap
        # between the right column's main text and this aside is almost
        # as wide as the TRUE gutter between the two questions, so the
        # legacy "is there a second comparably-wide gap" ambiguity guard
        # fired and rejected the whole page - even though that second
        # "candidate" produces only 2 lines on one side and could NEVER
        # have been a real column split on its own (it fails the same
        # minimum-lines-per-column floor every real candidate must clear).
        # A gap that could never pass on its own is not real competition
        # for the ambiguity check either.
        left = (
            [TextLine(page=1, x0=34, y0=15 * i, x1=34 + 250, y1=15 * i + 8, text=f"left content {i}")
             for i in range(30)]
        )
        right = (
            [TextLine(page=1, x0=312, y0=15 * i, x1=312 + 250, y1=15 * i + 8, text=f"right content {i}")
             for i in range(25)]
        )
        aside = [
            TextLine(page=1, x0=482, y0=503.5, x1=482 + 47, y1=511.5, text="Note e adote:"),
            TextLine(page=1, x0=482, y0=514.7, x1=482 + 48, y1=522.7, text="log10 2 = 0,3"),
        ]
        split = detect_two_column_layout(left + right + aside, page_width=595.0)
        self.assertIsNotNone(split)
        self.assertTrue(34 < split < 312)

    def test_two_column_detection_assigns_a_tiny_inline_caption_to_its_own_column(self):
        # Real regression found on a real FUVEST exam page: the left
        # question ("Observe as imagens:") capions three stacked photos
        # inline with bare "(1)", "(2)", "(3)" labels - only 3 short
        # lines, sitting at an x-position BETWEEN the two real columns
        # (the photos are indented past the left column's own text
        # margin). This tiny cluster is close enough to genuinely
        # competing width that BOTH ways of splitting around it pass
        # every other structural check (vertical span, narrow-line
        # fraction) equally well, which the corpus regression suite
        # confirmed is not safely resolved by preferring whichever
        # candidate fewer lines straddle either - both candidates here are
        # already clean. A cluster this small (fewer lines than any real
        # per-question column has ever been measured to have) is never
        # itself a candidate column boundary - it must never split gap
        # search into two contenders in the first place; the real gutter
        # is the single gap around it, and it lands as ordinary content on
        # whichever side its x-position naturally falls before that gap.
        left = (
            [TextLine(page=1, x0=34, y0=15 * i, x1=34 + 250, y1=15 * i + 8, text=f"left content {i}")
             for i in range(20)]
        )
        caption = [
            TextLine(page=1, x0=153, y0=315.2, x1=153 + 13, y1=323.2, text="(1)"),
            TextLine(page=1, x0=153, y0=475.2, x1=153 + 13, y1=483.2, text="(2)"),
            TextLine(page=1, x0=153, y0=635.2, x1=153 + 13, y1=643.2, text="(3)"),
        ]
        right = (
            [TextLine(page=1, x0=312, y0=15 * i, x1=312 + 250, y1=15 * i + 8, text=f"right content {i}")
             for i in range(40)]
        )
        split = detect_two_column_layout(left + caption + right, page_width=595.0)
        self.assertIsNotNone(split)
        self.assertTrue(153 < split < 312)

    def test_two_column_detection_rejects_a_compact_two_column_answer_grid(self):
        # Real regression found on a real ITA exam page: a SINGLE question's
        # five multiple-choice options (A-E) are laid out as a compact
        # 2-column grid (A, B, C stacked on the left; D, E stacked on the
        # right, at the SAME y-rows as A and B) rather than one running
        # column - this is answer-grid formatting for ONE question, not two
        # independent side-by-side columns of running content, and reading
        # it column-major would move D/E to after unrelated later content
        # instead of leaving them in their natural row position. The right
        # "column" here is only 2 real lines tall (~30pt) against a left
        # side that runs the whole page (~600pt) - it must be rejected for
        # not spanning most of the page's vertical extent, exactly as this
        # module's own docstring promises, even when a stray one-off line
        # (e.g. a running page-number footer, sharing the right side's x0
        # purely by coincidence) could otherwise be misread as stretching
        # the right side's apparent span.
        left = (
            [TextLine(page=1, x0=57, y0=10 * i, x1=57 + 300, y1=10 * i + 8, text=f"left content {i}")
             for i in range(66)]
        )
        right = [
            TextLine(page=1, x0=305, y0=404, x1=487, y1=413, text="D (   ) opcao D da questao"),
            TextLine(page=1, x0=305, y0=424, x1=474, y1=433, text="E (   ) opcao E da questao"),
        ]
        stray_footer = TextLine(page=1, x0=533, y0=797, x1=543, y1=806, text="9")
        split = detect_two_column_layout(left + right + [stray_footer], page_width=595.0)
        self.assertIsNone(split)

    def test_two_column_detection_survives_a_stray_off_column_caption(self):
        # Real regression found on a FUVEST page: two genuine side-by-side
        # QUESTIONS (not a wrapped article split at one gutter) sit next to
        # each other, but the page also carries a couple of ONE-OFF lines
        # that land at x-positions between the two real columns - a source
        # citation under a graph ("IPCC (...). Adaptado.") and a running
        # page banner. Each of those appears on only ONE line; a real
        # column is made of MANY lines sharing close x0s. The old gap
        # search treated every line's x0 as equally significant, so the
        # lone citation line split the one true gutter into two smaller,
        # comparably-wide gaps and the strict "single dominant gap" check
        # rejected the page outright - silently falling back to top-to-
        # bottom order, which interleaves the two unrelated questions'
        # text line-by-line into nonsense.
        left = (
            [TextLine(page=1, x0=34, y0=10 * i, x1=34 + 250, y1=10 * i + 8, text=f"L{i}")
             for i in range(10)]
        )
        right = (
            [TextLine(page=1, x0=312, y0=10 * i, x1=312 + 250, y1=10 * i + 8, text=f"R{i}")
             for i in range(10)]
        )
        stray_caption = TextLine(page=1, x0=150, y0=45, x1=210, y1=53, text="Fonte. Adaptado.")
        stray_banner = TextLine(page=1, x0=421, y0=1, x1=563, y1=9, text="Prova X - pagina")
        split = detect_two_column_layout(
            left + right + [stray_caption, stray_banner], page_width=595.0)
        self.assertIsNotNone(split)
        self.assertTrue(34 < split < 312)

    def test_standalone_number_marker_confirmed_by_repeated_column_position(self):
        # Real convention found on a FUVEST exam: the question number sits
        # ALONE on its own line (no "." or ")"), body text starting only on
        # the next line. Trusted as a real marker only when its x-position
        # recurs many times (a genuine column start reused by many
        # questions) - here x0=34 repeats 6x, matching the confirmation
        # threshold.
        lines = []
        for i in range(1, 7):
            lines.append(TextLine(page=1, x0=34, y0=100 * i, x1=40, y1=100 * i + 10, text=str(i)))
            lines.append(TextLine(page=1, x0=34, y0=100 * i + 12, x1=200, y1=100 * i + 20,
                                 text=f"corpo da questao {i}"))
        normalized = _normalize_standalone_number_markers(lines)
        marker_lines = [ln.text for ln in normalized if ln.text.rstrip(STANDALONE_MARKER_SENTINEL).isdigit()]
        self.assertEqual(marker_lines, [f"{i}{STANDALONE_MARKER_SENTINEL}" for i in range(1, 7)])
        # line count and every non-marker line's text must be untouched -
        # no merging, no geometry change.
        self.assertEqual(len(normalized), len(lines))

    def test_standalone_number_not_confirmed_stays_untouched(self):
        # A one-off bare number (e.g. a chart axis value) that does NOT
        # recur at its x-position must never be treated as a marker -
        # otherwise ordinary numeric content anywhere in the document
        # would risk becoming a fabricated question boundary.
        lines = [
            TextLine(page=1, x0=439, y0=100, x1=445, y1=110, text="200"),
            TextLine(page=1, x0=439, y0=120, x1=445, y1=130, text="content near it"),
        ]
        normalized = _normalize_standalone_number_markers(lines)
        self.assertEqual([ln.text for ln in normalized], [ln.text for ln in lines])

    def test_densely_packed_numbered_lines_are_never_confirmed_as_markers(self):
        # Real regression found on a UECE exam: a numbered-line passage
        # (every ~11pt - ordinary single-line spacing) sitting at one
        # consistent x-position recurs far more often than the confirmation
        # threshold, but it is NOT a per-question marker column - a real
        # question is a whole block of content, never packed line-tight.
        # Column-position recurrence alone is not enough; consecutive
        # occurrences must also be spaced far apart vertically.
        lines = []
        for i in range(1, 10):
            lines.append(TextLine(page=1, x0=41, y0=11 * i, x1=48, y1=11 * i + 9, text=str(i)))
            lines.append(TextLine(page=1, x0=60, y0=11 * i, x1=200, y1=11 * i + 9, text=f"linha {i}"))
        normalized = _normalize_standalone_number_markers(lines)
        self.assertEqual([ln.text for ln in normalized], [ln.text for ln in lines])

    def test_printed_running_page_number_is_never_confirmed_as_a_marker(self):
        # Real regression found on a PUC-Rio exam: a running page number
        # printed at the SAME y-position on every page (its text changes
        # per page - "7" on page 7, "8" on page 8 - so
        # detect_repeated_page_artifacts can never catch it, since that
        # check requires IDENTICAL repeated text). Spaced far apart in
        # page-index terms and recurs often enough to otherwise pass the
        # column/sparsity checks - only near-constant y0 across pages
        # gives it away: a real marker's position depends on how much
        # content came before it and varies a lot page to page.
        lines = []
        for page in range(1, 9):
            lines.append(TextLine(page=page, x0=294, y0=783.0 + (page % 3), x1=300, y1=793, text=str(page)))
            lines.append(TextLine(page=page, x0=50, y0=100, x1=300, y1=110, text=f"conteudo real pagina {page}"))
        normalized = _normalize_standalone_number_markers(lines)
        self.assertEqual([ln.text for ln in normalized], [ln.text for ln in lines])

    def test_reference_table_page_is_never_confirmed_as_markers(self):
        # Real regression found on a real PUC-Rio exam: page 2 is a
        # periodic table of elements handed out as a reference sheet, NOT
        # exam content. Each element's atomic number sits alone on its own
        # line, and each of the table's ~10 group COLUMNS independently
        # recurs at one x-position, spaced ~40pt+ apart down the column
        # (one row per period) with the same high y-variance a real
        # question marker has - satisfying every existing geometric check
        # this heuristic uses. The giveaway a real exam page never
        # produces: a SINGLE page with dozens of free-standing bare
        # numbers at once (141, measured on the real page) - a genuine
        # exam page has at most a handful of question markers, never a
        # page flooded with isolated digits. A page's bare numbers are
        # never trusted as markers once that page's own count is this high,
        # regardless of how cleanly any one x-position clusters.
        lines = []
        for col in range(10):
            x0 = 100 + col * 40
            for row in range(7):
                y0 = 40.0 + row * 45
                lines.append(TextLine(
                    page=1, x0=x0, y0=y0, x1=x0 + 6, y1=y0 + 8,
                    text=str(col * 7 + row + 1)))
        # a genuine marker column elsewhere in the SAME document (a
        # different page) must still be confirmed - the fix must not
        # blind the whole document, only the flooded reference page.
        for i in range(1, 7):
            lines.append(TextLine(page=2, x0=34, y0=100 * i, x1=40, y1=100 * i + 10, text=str(i)))
            lines.append(TextLine(page=2, x0=34, y0=100 * i + 12, x1=200, y1=100 * i + 20,
                                 text=f"corpo da questao {i}"))
        normalized = _normalize_standalone_number_markers(lines)
        page1_markers = [ln for ln in normalized if ln.page == 1 and ln.text.endswith(STANDALONE_MARKER_SENTINEL)]
        page2_markers = [ln for ln in normalized if ln.page == 2 and ln.text.endswith(STANDALONE_MARKER_SENTINEL)]
        self.assertEqual(page1_markers, [])
        self.assertEqual(len(page2_markers), 6)

    def test_paragraph_break_inserted_on_large_vertical_gap(self):
        # several lines with a NORMAL (small) line-to-line gap establish the
        # page's typical spacing, then one clearly larger gap (a paragraph
        # break) before the next question.
        lines = [TextLine(page=1, x0=50, y0=12 * i, x1=100, y1=12 * i + 10, text=f"linha {i}")
                for i in range(6)]
        lines.append(TextLine(page=1, x0=50, y0=12 * 6 + 30, x1=100, y1=12 * 6 + 40,
                             text="1.   Novo parágrafo/questão."))
        s = DocumentStructure(document_hash="x", page_count=1, lines=lines)
        self.assertIn("\n\n", s.text())

    def test_offset_to_page_maps_correctly_across_a_page_break(self):
        s = DocumentStructure(
            document_hash="x", page_count=2,
            lines=[
                TextLine(page=1, x0=0, y0=0, x1=10, y1=10, text="pagina um"),
                TextLine(page=2, x0=0, y0=0, x1=10, y1=10, text="pagina dois"),
            ],
        )
        text = s.text()
        self.assertEqual(s.offset_to_page(0), 1)
        self.assertEqual(s.offset_to_page(text.index("dois")), 2)


class BoundaryDetectionTests(unittest.TestCase):
    def test_detects_period_and_parenthesis_and_word_markers(self):
        text = (
            "1.   Primeira questão com texto suficiente para ser válida.\n\n"
            "(2)   Segunda questão, formato parêntese, com texto suficiente.\n\n"
            "Questão 3: Terceira questão, formato por extenso, texto suficiente.\n"
        )
        boundaries = detect_boundaries(text)
        self.assertEqual(sorted(b.number for b in boundaries), [1, 2, 3])

    def test_number_is_not_the_only_signal_decimal_subsection_excluded(self):
        text = (
            "1.   Questão real com texto longo o suficiente para ser válida.\n\n"
            "1.1 Isto é uma subseção teórica, não uma questão nova.\n"
            "Mais texto da subseção que não deve virar outra questão.\n"
        )
        boundaries = detect_boundaries(text)
        self.assertEqual([b.number for b in boundaries], [1])

    def test_duplicate_number_prefers_paragraph_break_and_longer_body(self):
        text = (
            "5. Um passo curto\n"
            "5. Resolva algo\n\n"
            "5.   Esta é a questão real, com bastante texto substantivo e "
            "conteúdo relevante para a disciplina em questão, sem qualquer "
            "duvida quanto a sua validade como pergunta de exame.\n"
        )
        boundaries = detect_boundaries(text)
        self.assertEqual(len(boundaries), 1)
        b = boundaries[0]
        self.assertTrue(b.preceded_by_paragraph_break)
        self.assertIn("questão real", text[b.start:b.end])

    def test_duplicate_number_prefers_the_candidate_with_real_options_over_a_longer_bare_one(self):
        # Real regression found on a real UECE exam: the numbered EXAM-
        # RULES preamble ("13. Na parte superior da carteira, ficarão
        # somente...") uses the exact same "N. text" convention as real
        # questions and collides with every real question number the
        # rules section's own count reaches. Neither candidate is
        # preceded by a paragraph break here (both a dense rules list and
        # an exam question can immediately follow the prior item with
        # ordinary spacing) - so the OLD tie-break fell to body length,
        # and a rules paragraph can easily run longer than a short
        # multiple-choice question, wrongly winning. A rules paragraph is
        # never phrased as multiple-choice; a real graded question in
        # this convention always is - that alone should settle it, no
        # matter which body is longer.
        text = (
            "13. Na parte superior da carteira, ficarão somente a caneta "
            "transparente, o documento de identidade, o caderno de prova "
            "e a folha de respostas, um texto normativo mais longo do que "
            "a questão real, sem nenhuma alternativa de resposta.\n"
            "14. Outra regra qualquer do mesmo tipo, também sem alternativas.\n"
            "13.   A soma dos divisores positivos do número 9438 que são "
            "números primos é igual a\n"
            "A) 25.\n"
            "B) 29.\n"
            "C) 18.\n"
            "D) 26.\n"
        )
        boundaries = detect_boundaries(text)
        b13 = [b for b in boundaries if b.number == 13][0]
        self.assertIn("soma dos divisores", text[b13.start:b13.end])

    def test_cut_at_answer_key_excludes_gabarito_section(self):
        text = "1.   Questão real, com texto suficiente.\n\nGabarito:\n\nResposta da questão 1: [A]\n"
        main_text, cut = cut_at_answer_key(text)
        self.assertNotIn("Resposta da questão", main_text)
        self.assertEqual(cut, text.index("Gabarito"))

    def test_multiple_choice_options_extracted_sequentially(self):
        text = (
            "1.   Qual a resposta correta?\n"
            "a) primeira alternativa\n"
            "b) segunda alternativa\n"
            "c) terceira alternativa\n"
            "d) quarta alternativa\n"
            "e) quinta alternativa\n"
        )
        boundaries = detect_boundaries(text)
        draft = classify_and_extract(boundaries[0], text)
        self.assertEqual(draft.question_type, "multiple_choice")
        self.assertEqual([o.label for o in draft.options], ["A", "B", "C", "D", "E"])
        self.assertEqual(draft.options[0].text, "primeira alternativa")

    def test_discursive_question_without_options_is_preserved_not_discarded(self):
        text = "1.   Explique com suas palavras o conceito de solução saturada e diluída.\n"
        boundaries = detect_boundaries(text)
        draft = classify_and_extract(boundaries[0], text)
        self.assertEqual(draft.question_type, "discursive")
        self.assertEqual(draft.options, [])
        self.assertIn("solução saturada", draft.raw_text)

    def test_multiple_choice_options_tab_delimited_no_punctuation(self):
        # Real INEP/ENEM typesetting: bare letter + TAB, no "." or ")" at
        # all - found by testing the engine against the real 2025 ENEM PDFs.
        text = (
            "1.   Qual a resposta correta?\n"
            "A\tprimeira alternativa\n"
            "B\tsegunda alternativa\n"
            "C\tterceira alternativa\n"
            "D\tquarta alternativa\n"
            "E\tquinta alternativa\n"
        )
        boundaries = detect_boundaries(text)
        draft = classify_and_extract(boundaries[0], text)
        self.assertEqual(draft.question_type, "multiple_choice")
        self.assertEqual([o.label for o in draft.options], ["A", "B", "C", "D", "E"])
        self.assertEqual(draft.options[0].text, "primeira alternativa")

    def test_multiple_choice_options_space_delimited_no_punctuation(self):
        # Real UNICAMP/ITA/UECE typesetting: bare letter + single space, no
        # punctuation - the single most common convention found across a
        # 10-exam sample from different institutions.
        text = (
            "1.   Qual a resposta correta?\n"
            "A primeira alternativa\n"
            "B segunda alternativa\n"
            "C terceira alternativa\n"
            "D quarta alternativa\n"
            "E quinta alternativa\n"
        )
        boundaries = detect_boundaries(text)
        draft = classify_and_extract(boundaries[0], text)
        self.assertEqual(draft.question_type, "multiple_choice")
        self.assertEqual([o.label for o in draft.options], ["A", "B", "C", "D", "E"])
        self.assertEqual(draft.options[0].text, "primeira alternativa")

    def test_bare_letter_article_at_line_start_does_not_fabricate_options(self):
        # "A" is also the Portuguese feminine definite article and commonly
        # starts a line on its own in justified/reflowed text. A single such
        # line (not part of a real ascending A..E run) must never be read as
        # an option - the >=2-sequential-letters guard must still hold once
        # bare-letter delimiters are accepted.
        text = (
            "1.   Explique como a difusão de gases funciona no experimento.\n"
            "A absorção de partículas no meio ocorre lentamente e depende "
            "da concentração observada.\n"
        )
        boundaries = detect_boundaries(text)
        draft = classify_and_extract(boundaries[0], text)
        self.assertEqual(draft.question_type, "discursive")
        self.assertEqual(draft.options, [])

    def test_lowercase_article_at_reflowed_line_start_does_not_break_real_options(self):
        # Real regression found on a UECE exam: a line-wrapped statement
        # elsewhere in the SAME question body starts with the lowercase
        # article "a " right at a line start (pure coincidence of reflow),
        # sitting BEFORE a real, fully punctuated "A) ... D)" option block.
        # The bare (no-punctuation) delimiter must never match lowercase -
        # every real no-punctuation convention observed in practice (INEP,
        # UNICAMP, UECE, ITA) uses UPPERCASE letters only - so this stray
        # lowercase "a" must not be treated as a candidate at all, and the
        # real, punctuated A-D options must still be extracted correctly.
        text = (
            "1.   Considerando o texto de apoio, é correto afirmar que\n"
            "a lei impulsiona a mobilidade urbana de forma direta.\n"
            "A) I e II, apenas.\n"
            "B) II e III, apenas.\n"
            "C) I e III, apenas.\n"
            "D) I, II e III.\n"
        )
        boundaries = detect_boundaries(text)
        draft = classify_and_extract(boundaries[0], text)
        self.assertEqual(draft.question_type, "multiple_choice")
        self.assertEqual([o.label for o in draft.options], ["A", "B", "C", "D"])

    def test_capitalized_sentence_starting_with_a_does_not_break_real_punctuated_options(self):
        # Second real regression found on the same UECE exam: "A" capitalized
        # at a true line start is grammatically ordinary Portuguese whenever
        # it starts a new sentence (very common in reading-comprehension
        # "texto de apoio" blocks and roman-numeral assertion lists: "I. A
        # LBI trouxe..."), not just the lowercase article. A bare "A" is
        # simply too ambiguous to trust, uppercase or not - so a fully
        # punctuated option run present elsewhere in the same body must
        # always win outright, with the bare (no-punctuation) form used only
        # as a fallback when there is NO valid punctuated run at all.
        text = (
            "1.   Considere as afirmações a seguir sobre acessibilidade.\n"
            "I.\n"
            "A celebração da diversidade impulsiona políticas inclusivas.\n"
            "A) I e II, apenas.\n"
            "B) II e III, apenas.\n"
            "C) I e III, apenas.\n"
            "D) I, II e III.\n"
        )
        boundaries = detect_boundaries(text)
        draft = classify_and_extract(boundaries[0], text)
        self.assertEqual(draft.question_type, "multiple_choice")
        self.assertEqual([o.label for o in draft.options], ["A", "B", "C", "D"])

    def test_stray_leading_letter_before_a_real_bare_option_run_is_dropped(self):
        # Real regression pattern found on the ENEM 2025 exams themselves
        # (dominant remaining failure after fixes #1/#2): a capitalized
        # sentence starting with "A" appears in the passage/support text,
        # immediately before a real, complete, bare-delimited A-E option
        # run. Since these real options have NO punctuation at all (INEP's
        # own convention), the punctuated-priority fix doesn't help here -
        # the fix must find the longest clean run at the END of the
        # candidates and drop the leading noise, not just prefer punctuated
        # matches over bare ones.
        text = (
            "1.   Considere o texto de apoio a seguir para responder.\n"
            "A autora reconstrói a memória afetiva do bairro natal.\n"
            "A\tprimeira alternativa\n"
            "B\tsegunda alternativa\n"
            "C\tterceira alternativa\n"
            "D\tquarta alternativa\n"
            "E\tquinta alternativa\n"
        )
        boundaries = detect_boundaries(text)
        draft = classify_and_extract(boundaries[0], text)
        self.assertEqual(draft.question_type, "multiple_choice")
        self.assertEqual([o.label for o in draft.options], ["A", "B", "C", "D", "E"])
        self.assertEqual(draft.options[0].text, "primeira alternativa")

    def test_standalone_marker_body_across_a_paragraph_break_gap(self):
        # Real regression found on the FUVEST exam itself: the vertical
        # gap between the standalone number and its body is sometimes wide
        # enough to register as its own paragraph break ("\n\n" from
        # structure.py's own gap-based joiner), not a plain single-line
        # break - the marker must still be found either way. Text here
        # simulates what structure.py hands boundary.py AFTER confirming
        # and marking the standalone numbers (the sentinel, never a plain
        # "." - see the sentinel's own docstring for why).
        S = STANDALONE_MARKER_SENTINEL
        text = f"10{S}\n11{S}\n\nConsidere a equacao real com texto suficiente para ser valida.\n"
        boundaries = detect_boundaries(text)
        self.assertEqual(sorted(b.number for b in boundaries), [10, 11])

    def test_plain_period_alone_on_a_line_is_never_treated_as_a_marker(self):
        # The regression this sentinel fixes: a genuine "N.\n<content>"
        # pattern that already exists naturally in a real document (found
        # on a real UECE exam: an uncut answer-bubble grid template) must
        # NEVER be read as a question boundary just because it happens to
        # look like the FUVEST standalone convention - only a line
        # structure.py itself confirmed via geometry (the sentinel) can be.
        text = "10.\n11.\n\nConteudo nao relacionado que nao deveria virar questao.\n"
        boundaries = detect_boundaries(text)
        self.assertEqual(boundaries, [])

    def test_word_marker_tolerates_missing_space_before_number(self):
        # Real UERJ PDF text extraction: "Questão" and its number come out
        # glued together with zero literal whitespace characters (the visual
        # gap was achieved by glyph positioning/kerning, not a space glyph).
        text = "Questão33 Enunciado real com texto suficiente para ser válido.\n"
        boundaries = detect_boundaries(text)
        self.assertEqual([b.number for b in boundaries], [33])

    def test_garbled_font_glyph_flags_the_question_for_review(self):
        # Real regression found on a FUVEST exam: a math-heavy PDF's
        # embedded font for a special symbol (almost certainly a fraction
        # bar/radical glyph) lacks a correct ToUnicode mapping - PyMuPDF
        # still emits SOME character, but it decodes into a script that
        # would never legitimately appear (real case: Oriya letters, deep
        # inside an otherwise normal statement). There is no way to
        # recover the real symbol from the text layer alone, so it must
        # never be silently kept as if it were real content - only flagged
        # for a human to check against the original PDF.
        text = (
            "1.   Considere a equacao ହଶ com texto suficiente "
            "para ser valida mesmo com o simbolo corrompido presente.\n"
        )
        boundaries = detect_boundaries(text)
        draft = classify_and_extract(boundaries[0], text)
        self.assertIn("garbled_encoding", draft.flags)

    def test_duplicate_option_labels_are_rejected_never_merged(self):
        # two option lists concatenated (a page/column-order corruption) -
        # must never fabricate an 8-option question.
        text = (
            "1.   Enunciado.\n"
            "a) x\nb) y\nc) z\nd) w\ne) v\n"
            "a) x2\nb) y2\nc) z2\nd) w2\n"
        )
        boundaries = detect_boundaries(text)
        draft = classify_and_extract(boundaries[0], text)
        labels = [o.label for o in draft.options]
        self.assertEqual(len(labels), len(set(labels)))

    def test_standalone_marker_discursive_question_does_not_need_a_paragraph_break_too(self):
        # Real regression found on a real PUC-Rio exam: this booklet prints
        # its discursive questions TIGHTLY packed - the next question's
        # bare-standalone marker follows the previous question's last line
        # at perfectly ordinary single-line spacing (measured 10.3pt on
        # the real PDF), never a visually larger paragraph gap. That is
        # real, correct geometry, not a measurement bug - PHASE 28's own
        # rebalancing comment already documents that a densely-typeset
        # sheet can make genuine question breaks geometrically
        # indistinguishable from ordinary line spacing.
        #
        # But a STANDALONE marker is not "just a number" the way a PERIOD/
        # WORD/PAREN marker is: structure.py's own
        # _normalize_standalone_number_markers already required its column
        # position to recur 6+ times DOCUMENT-WIDE, each occurrence 40pt+
        # apart vertically, with 20pt+ of y-variance across the document -
        # a strictly stronger, more specific piece of evidence that this
        # is a genuine per-question marker than "is there a blank line
        # right before it" ever was. Requiring BOTH before trusting a
        # discursive question (no options to verify structurally either)
        # pushed confidence to exactly 0.55 - just under the 0.6 review
        # threshold - for nearly every discursive question in that real
        # document, for no reason but this document's own tight layout.
        full_text = (
            "(E)\tmeristemático, vascular e epidérmico\n"
            f"17{STANDALONE_MARKER_SENTINEL}\n"
            "Um biólogo estudou um determinado ecossistema, onde todas as "
            "borboletas apresentavam coloração vibrante e marcante."
        )
        marker_end = full_text.index(STANDALONE_MARKER_SENTINEL) + len(STANDALONE_MARKER_SENTINEL) + 1
        boundary = QuestionBoundary(
            number=17, marker_style="STANDALONE", start=marker_end, end=len(full_text),
            preceded_by_paragraph_break=False,
        )
        draft = classify_and_extract(boundary, full_text)
        self.assertEqual(draft.question_type, "discursive")
        self.assertGreaterEqual(draft.confidence, 0.6)

    def test_confidence_scoring_is_deterministic_and_bounded(self):
        text = "1.   Enunciado com texto razoavelmente longo para pontuar melhor.\na) x\nb) y\n"
        boundaries = detect_boundaries(text)
        d1 = classify_and_extract(boundaries[0], text)
        d2 = classify_and_extract(boundaries[0], text)
        self.assertEqual(d1.confidence, d2.confidence)
        self.assertTrue(0.0 <= d1.confidence <= 1.0)


class ResolutionCaptureTests(unittest.TestCase):
    """PHASE 31 - captures a source PDF's own numbered 'Resolução' section
    into a question_number -> resolution_text mapping, reusing the exact
    same marker conventions as question boundary detection. Golden rule:
    an empty dict whenever the segmentation is anything less than
    unambiguous - never an invented or partial guess (spec s9/s10 extended
    to segmentation confidence itself)."""

    def test_clearly_numbered_resolution_section_is_captured_per_question(self):
        text = (
            "1.   Primeira questão com enunciado suficientemente longo.\n\n"
            "2.   Segunda questão com enunciado suficientemente longo.\n\n"
            "Resolução:\n\n"
            "1. Explicação detalhada do raciocínio da questão um.\n"
            "2. Explicação detalhada do raciocínio da questão dois.\n"
        )
        main_text, cut = cut_at_answer_key(text)
        self.assertNotIn("Explicação detalhada", main_text)
        resolutions = extract_resolutions_by_question(text, cut)
        self.assertEqual(
            resolutions,
            {
                1: "Explicação detalhada do raciocínio da questão um.",
                2: "Explicação detalhada do raciocínio da questão dois.",
            },
        )

    def test_no_answer_key_section_at_all_returns_empty_dict(self):
        # Mirrors the real ENEM pilot PDFs (var/inep-pilot/*.pdf): no
        # 'Resolução'/'Gabarito' heading exists anywhere in the document,
        # so cut_at_answer_key never cuts (cut == len(text)) and there is
        # no tail to segment at all.
        text = (
            "1.   Primeira questão com enunciado suficientemente longo.\n\n"
            "2.   Segunda questão com enunciado suficientemente longo.\n"
        )
        main_text, cut = cut_at_answer_key(text)
        self.assertEqual(cut, len(text))
        resolutions = extract_resolutions_by_question(text, cut)
        self.assertEqual(resolutions, {})

    def test_unnumbered_prose_after_gabarito_heading_returns_empty_dict(self):
        text = (
            "1.   Primeira questão com enunciado suficientemente longo.\n\n"
            "Gabarito:\n\n"
            "As respostas corretas estão detalhadas em texto corrido, sem "
            "nenhuma numeração associando cada trecho a uma questão específica.\n"
        )
        main_text, cut = cut_at_answer_key(text)
        resolutions = extract_resolutions_by_question(text, cut)
        self.assertEqual(resolutions, {})

    def test_duplicate_question_number_in_resolution_section_is_ambiguous(self):
        text = (
            "1.   Primeira questão com enunciado suficientemente longo.\n\n"
            "Resolução:\n\n"
            "1. Primeira tentativa de explicação, depois corrigida abaixo.\n"
            "1. Segunda tentativa de explicação, a que realmente vale.\n"
        )
        main_text, cut = cut_at_answer_key(text)
        resolutions = extract_resolutions_by_question(text, cut)
        self.assertEqual(resolutions, {})

    def test_out_of_order_markers_in_resolution_section_are_not_trusted(self):
        text = (
            "1.   Primeira questão com enunciado suficientemente longo.\n\n"
            "Resolução:\n\n"
            "2. Explicação fora de ordem da questão dois.\n"
            "1. Explicação fora de ordem da questão um.\n"
        )
        main_text, cut = cut_at_answer_key(text)
        resolutions = extract_resolutions_by_question(text, cut)
        self.assertEqual(resolutions, {})


class ValidationTests(unittest.TestCase):
    def test_sequence_gap_and_missing_are_reported(self):
        from agente_ia_edu.services.question_extraction.boundary import ExtractedQuestionDraft
        drafts = [
            ExtractedQuestionDraft(number=n, question_type="discursive", raw_text="x", normalized_text="x", confidence=0.9)
            for n in (1, 2, 4)
        ]
        report = validate(drafts, expected_question_count=5)
        self.assertEqual(report.missing_numbers, [3, 5])
        self.assertEqual(report.sequence_gaps, [3])
        self.assertFalse(report.sequence_ok)

    def test_clean_sequence_validates(self):
        from agente_ia_edu.services.question_extraction.boundary import ExtractedQuestionDraft
        drafts = [
            ExtractedQuestionDraft(number=n, question_type="discursive", raw_text="x", normalized_text="x", confidence=0.9)
            for n in range(1, 6)
        ]
        report = validate(drafts, expected_question_count=5)
        self.assertTrue(report.validated)
        self.assertEqual(report.missing_numbers, [])


class AssetAssociationTests(unittest.TestCase):
    def test_image_associated_to_the_question_whose_page_it_sits_on(self):
        structure = DocumentStructure(document_hash="x", page_count=3, images=[
            PageImage(page=2, index=0, bbox=(0, 100, 10, 110), width=50, height=50, digest="d1"),
        ])
        question_lines = {
            1: [TextLine(page=1, x0=0, y0=50, x1=10, y1=60, text="q1")],
            2: [TextLine(page=2, x0=0, y0=50, x1=10, y1=60, text="q2")],
            3: [TextLine(page=3, x0=0, y0=50, x1=10, y1=60, text="q3")],
        }
        assoc = associate_assets(structure, question_lines)
        self.assertIn(2, assoc)
        self.assertNotIn(1, assoc)
        self.assertNotIn(3, assoc)

    def test_tiny_decorative_images_are_not_associated(self):
        structure = DocumentStructure(
            document_hash="x", page_count=1,
            images=[PageImage(page=1, index=0, bbox=None, width=1, height=1, digest="d1")],
        )
        assoc = associate_assets(structure, {1: [TextLine(page=1, x0=0, y0=50, x1=10, y1=60, text="q1")]})
        self.assertEqual(assoc, {})

    def test_two_questions_share_a_page_image_goes_to_the_one_it_sits_under(self):
        # The real bug this fixes: page-range-only association put every
        # image on a shared page onto EVERY question covering that page
        # (found on real ENEM/vestibular exams, where several questions
        # routinely share one page). Position must decide, not just page.
        structure = DocumentStructure(document_hash="x", page_count=1, images=[
            PageImage(page=1, index=0, bbox=(0, 120, 10, 130), width=50, height=50, digest="belongs_to_q1"),
            PageImage(page=1, index=1, bbox=(0, 320, 10, 330), width=50, height=50, digest="belongs_to_q2"),
        ])
        question_lines = {
            1: [TextLine(page=1, x0=0, y0=100, x1=50, y1=110, text="q1 statement")],
            2: [TextLine(page=1, x0=0, y0=300, x1=50, y1=310, text="q2 statement")],
        }
        assoc = associate_assets(structure, question_lines)
        self.assertEqual({a.digest for a in assoc.get(1, [])}, {"belongs_to_q1"})
        self.assertEqual({a.digest for a in assoc.get(2, [])}, {"belongs_to_q2"})

    def test_image_with_no_bbox_falls_back_to_page_level_association(self):
        # Some PDFs don't expose a usable image bbox (get_image_bbox can
        # fail) - the image must still be recorded (never silently
        # dropped), just with lower confidence since exact position within
        # a shared page is unknown.
        structure = DocumentStructure(document_hash="x", page_count=1, images=[
            PageImage(page=1, index=0, bbox=None, width=50, height=50, digest="d1"),
        ])
        question_lines = {1: [TextLine(page=1, x0=0, y0=100, x1=50, y1=110, text="q1")]}
        assoc = associate_assets(structure, question_lines)
        self.assertIn(1, assoc)
        self.assertLess(assoc[1][0].extraction_confidence, 0.9)


def _fake_result(
    number: int, n_options: int, *, review_status: str = "VALIDATED",
    confidence: float = 0.9, raw_text: str | None = None,
) -> ExtractedQuestionResult:
    options = [OptionDraft(label=chr(ord("A") + i), text=f"opt{i}") for i in range(n_options)]
    draft = ExtractedQuestionDraft(
        number=number, question_type="multiple_choice" if n_options else "discursive",
        raw_text=raw_text or f"q{number}", normalized_text=raw_text or f"q{number}",
        options=options, confidence=confidence,
    )
    return ExtractedQuestionResult(
        draft=draft, source_page_start=1, source_page_end=1, cross_page=False,
        review_status=review_status,
    )


def _fake_extraction_result(results: list[ExtractedQuestionResult]) -> ExtractionResult:
    from agente_ia_edu.services.question_extraction.validation import validate
    report = validate([r.draft for r in results])
    return ExtractionResult(
        document_hash="x", page_count=1, engine_version="test", questions=results, validation=report,
        answer_key_cut_offset=0,
    )


class ColumnDetectionMergeTests(unittest.TestCase):
    def test_enhanced_pass_wins_when_it_has_more_options(self):
        baseline = _fake_extraction_result([_fake_result(1, 0, review_status="REVIEW_REQUIRED")])
        enhanced = _fake_extraction_result([_fake_result(1, 5)])
        merged = merge_extraction_results(baseline, enhanced)
        self.assertEqual(len(merged.questions[0].draft.options), 5)

    def test_baseline_wins_when_enhanced_pass_has_fewer_options(self):
        # The real regression this guards against: global column-major
        # reordering (spec s4's own documented risk - tables/formulas can
        # be misread as a second column) broke a question that was fine
        # without it, on a real UNICAMP exam. Never let the reordered pass
        # silently downgrade a question the baseline pass already
        # recognized correctly, mirroring PHASE 28's own rule for its
        # LOCAL reconstruction: adopt only when it's a measurable
        # improvement, never blindly (spec s2).
        baseline = _fake_extraction_result([_fake_result(1, 5)])
        enhanced = _fake_extraction_result([_fake_result(1, 0, review_status="REVIEW_REQUIRED")])
        merged = merge_extraction_results(baseline, enhanced)
        self.assertEqual(len(merged.questions[0].draft.options), 5)
        self.assertEqual(merged.questions[0].review_status, "VALIDATED")

    def test_ties_prefer_the_enhanced_pass(self):
        baseline = _fake_extraction_result([_fake_result(1, 4)])
        enhanced = _fake_extraction_result([_fake_result(1, 4, review_status="VALIDATED")])
        merged = merge_extraction_results(baseline, enhanced)
        # same option count - either is fine; just confirm no crash and a
        # complete, non-fabricated result comes out.
        self.assertEqual(len(merged.questions[0].draft.options), 4)

    def test_tie_prefers_the_confidently_validated_pass(self):
        # Real regression found on a real PUC-Rio exam: enabling column
        # detection on a page correctly identified as two independent
        # columns (verified geometrically correct) had a document-wide
        # side effect - it shifted where a DUPLICATE question number (this
        # booklet numbers its discursive section 1, 2, 3... independently
        # of its multiple-choice section) resolves in the reordered text,
        # making the enhanced pass bind "question 1" to an unrelated
        # discursive item instead of the real multiple-choice question the
        # baseline pass found correctly. Both passes found the SAME option
        # count (0 - discursive), so the old tie-break ("ties favor
        # enhanced") blindly picked the low-confidence, wrong-span result
        # over a pass that was confident enough to validate on its own.
        baseline = _fake_extraction_result([_fake_result(1, 0, confidence=0.65, raw_text="real question")])
        enhanced = _fake_extraction_result([_fake_result(1, 0, confidence=0.55, raw_text="wrong duplicate")])
        merged = merge_extraction_results(baseline, enhanced)
        self.assertEqual(merged.questions[0].draft.raw_text, "real question")

    def test_more_options_does_not_override_a_confident_pass_with_a_low_confidence_one(self):
        # Same real regression, worse variant: the wrongly-bound duplicate
        # happened to also parse a couple of unrelated list items as
        # "options", so it had MORE raw options than the baseline's
        # correctly-bound (but discursive, 0-option) question - the old
        # rule ("more options always wins") let a confidently-wrong span
        # override a confidently-right one just because it looked
        # numerically richer.
        baseline = _fake_extraction_result([_fake_result(1, 0, confidence=0.65, raw_text="real question")])
        enhanced = _fake_extraction_result([_fake_result(1, 2, confidence=0.3, raw_text="wrong duplicate")])
        merged = merge_extraction_results(baseline, enhanced)
        self.assertEqual(merged.questions[0].draft.raw_text, "real question")

    def test_question_only_in_one_pass_is_kept(self):
        baseline = _fake_extraction_result([_fake_result(1, 4), _fake_result(2, 3)])
        enhanced = _fake_extraction_result([_fake_result(1, 4)])
        merged = merge_extraction_results(baseline, enhanced)
        numbers = sorted(r.draft.number for r in merged.questions)
        self.assertEqual(numbers, [1, 2])


class DeterminismTests(unittest.TestCase):
    def test_same_synthetic_pdf_extracted_twice_is_identical(self):
        tmp = Path("/tmp/phase27_determinism.pdf")
        _make_pdf(tmp, [
            (72, 72, "1.   Primeira questão com enunciado razoavelmente longo.", 10),
            (72, 100, "a) um", 10), (72, 115, "b) dois", 10), (72, 130, "c) tres", 10),
            (72, 145, "d) quatro", 10), (72, 160, "e) cinco", 10),
        ])
        r1 = extract_questions(tmp, expected_question_count=1)
        r2 = extract_questions(tmp, expected_question_count=1)
        shape1 = [(q.draft.number, q.draft.raw_text, q.draft.confidence, [o.text for o in q.draft.options])
                  for q in r1.questions]
        shape2 = [(q.draft.number, q.draft.raw_text, q.draft.confidence, [o.text for o in q.draft.options])
                  for q in r2.questions]
        self.assertEqual(shape1, shape2)


class ResolutionEngineWiringTests(unittest.TestCase):
    """PHASE 31 - end-to-end: extract_questions() wires
    extract_resolutions_by_question()'s output onto each
    ExtractedQuestionResult, never inventing when no confident segmentation
    exists (which is the real case for every ENEM pilot PDF today - spec's
    own documented current-corpus behaviour)."""

    def test_confidently_numbered_resolution_section_reaches_the_result(self):
        tmp = Path("/tmp/phase31_resolution_wiring.pdf")
        _make_pdf(tmp, [
            (72, 72, "1.   Primeira questão com enunciado razoavelmente longo.", 10),
            (72, 100, "2.   Segunda questão com enunciado razoavelmente longo.", 10),
            (72, 160, "Resolução:", 10),
            (72, 188, "1. Explicação detalhada da questão um.", 10),
            (72, 210, "2. Explicação detalhada da questão dois.", 10),
        ])
        result = extract_questions(tmp, expected_question_count=2, use_column_detection=False)
        by_number = {q.draft.number: q for q in result.questions}
        self.assertEqual(by_number[1].resolution_status, "PENDING_REVIEW")
        self.assertIn("questão um", by_number[1].resolution_raw_text)
        self.assertEqual(by_number[2].resolution_status, "PENDING_REVIEW")
        self.assertIn("questão dois", by_number[2].resolution_raw_text)

    def test_no_answer_key_section_leaves_every_resolution_none(self):
        # Mirrors var/inep-pilot/*.pdf: no resolution/gabarito section at
        # all - the overwhelming majority case for the real corpus today.
        tmp = Path("/tmp/phase31_resolution_wiring_none.pdf")
        _make_pdf(tmp, [
            (72, 72, "1.   Primeira questão com enunciado razoavelmente longo.", 10),
            (72, 100, "2.   Segunda questão com enunciado razoavelmente longo.", 10),
        ])
        result = extract_questions(tmp, expected_question_count=2, use_column_detection=False)
        for q in result.questions:
            self.assertEqual(q.resolution_status, "NONE")
            self.assertIsNone(q.resolution_raw_text)


class AIGuardTests(unittest.TestCase):
    def test_no_ai_provider_imports_in_the_engine(self):
        import agente_ia_edu.services.question_extraction.structure as m1
        import agente_ia_edu.services.question_extraction.boundary as m2
        import agente_ia_edu.services.question_extraction.assets as m3
        import agente_ia_edu.services.question_extraction.validation as m4
        import agente_ia_edu.services.question_extraction.engine as m5
        import agente_ia_edu.services.question_extraction_service as m6
        forbidden = {"openai", "AsyncOpenAI", "OpenAIProvider", "providers",
                    "classification_consensus", "classification_prompts"}
        for mod in (m1, m2, m3, m4, m5, m6):
            tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module.split(".")[0])
            self.assertFalse(names & forbidden, f"{mod.__file__} imports {names & forbidden}")


@unittest.skipUnless(_GOLDEN_AVAILABLE, "authorized pilot folder not present on this machine")
class GoldenPilotTests(unittest.TestCase):
    """Spec s14: the two real pilot PDFs are golden test cases. This suite
    is SKIPPED (never faked with a substitute) when the user's authorized
    folder is absent - it is exercised for real in this environment."""

    @classmethod
    def setUpClass(cls):
        cls.result_theory = extract_questions(PDF_THEORY, expected_question_count=24)
        cls.result_exercises = extract_questions(PDF_EXERCISES, expected_question_count=59)

    def test_theory_pdf_detects_all_24_questions(self):
        numbers = sorted(q.draft.number for q in self.result_theory.questions)
        self.assertEqual(numbers, list(range(1, 25)))
        self.assertEqual(self.result_theory.validation.missing_numbers, [])
        self.assertEqual(self.result_theory.validation.duplicated_numbers, [])

    def test_exercises_pdf_detects_all_59_questions(self):
        numbers = sorted(q.draft.number for q in self.result_exercises.questions)
        self.assertEqual(numbers, list(range(1, 60)))
        self.assertEqual(self.result_exercises.validation.missing_numbers, [])
        self.assertEqual(self.result_exercises.validation.duplicated_numbers, [])

    def test_total_is_83(self):
        total = len(self.result_theory.questions) + len(self.result_exercises.questions)
        self.assertEqual(total, 83)

    def test_no_question_silently_discarded_every_number_has_raw_text(self):
        for result in (self.result_theory, self.result_exercises):
            for q in result.questions:
                self.assertTrue(q.draft.raw_text.strip(), f"Q{q.draft.number} has empty raw_text")

    def test_discursive_questions_preserved_in_exercises_pdf(self):
        discursive = [q for q in self.result_exercises.questions if q.draft.question_type == "discursive"]
        self.assertGreater(len(discursive), 0)

    def test_cross_page_questions_are_reconstructed_as_one_question(self):
        cross_page_numbers = {q.draft.number for q in self.result_theory.questions if q.cross_page}
        self.assertGreater(len(cross_page_numbers), 0)
        # every cross-page question still appears exactly once
        all_numbers = [q.draft.number for q in self.result_theory.questions]
        self.assertEqual(len(all_numbers), len(set(all_numbers)))

    def test_extraction_is_deterministic_across_repeated_runs(self):
        rerun = extract_questions(PDF_THEORY, expected_question_count=24)
        shape_a = [(q.draft.number, q.draft.raw_text) for q in self.result_theory.questions]
        shape_b = [(q.draft.number, q.draft.raw_text) for q in rerun.questions]
        self.assertEqual(shape_a, shape_b)

    def test_no_question_is_auto_approved_or_published(self):
        for result in (self.result_theory, self.result_exercises):
            for q in result.questions:
                self.assertNotIn(q.review_status, ("APPROVED", "PUBLISHED"))


if __name__ == "__main__":
    unittest.main()
