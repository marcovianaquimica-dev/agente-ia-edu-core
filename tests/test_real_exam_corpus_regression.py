"""Real exam corpus regression tests for the question extraction engine.

Why this file exists: the two PHASE 27 golden pilot PDFs alone gave a
false sense of safety - they hit 83/83 for a long time while the engine
still failed badly (0-8% of alternatives recognized) on real exams from
other institutions, because both golden PDFs happen to use one specific
option-marker convention. Testing against a small, diverse corpus of
real, officially published exam booklets (ENEM/INEP, UNICAMP, UECE, ITA,
PUC-Rio, UERJ) is what actually found and fixed those bugs (see the
option-marker, asset-association-by-position and column-detection-merge
commits). This file exists so the NEXT change to the engine is checked
against that same diversity automatically, instead of only against the
two files everyone already knows by heart.

The PDFs themselves are official, publicly published past exam booklets
(INEP/ENEM and public university entrance exams), kept locally under
``var/real-exam-pilot/`` (gitignored, same convention the project already
uses for ``var/inep-pilot/``) rather than committed to the repository -
this suite is SKIPPED per-file, never faked with a substitute, when a
given file is absent from the machine running the tests.

Floors are set at (or slightly below) what was actually measured when
this file was written, so a genuine regression fails loudly while a
future improvement is free to raise the bar - never lower it without a
documented reason.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from agente_ia_edu.services.question_extraction.engine import extract_questions

PILOT_DIR = Path(__file__).resolve().parents[1] / "var" / "real-exam-pilot"


def _validated_count(result) -> int:
    return sum(1 for q in result.questions if q.review_status == "VALIDATED")


class RealExamCorpusRegressionTests(unittest.TestCase):
    """One test per real exam booklet. Each independently skips (never
    substitutes a different file) when its PDF isn't present locally."""

    def _check(self, filename: str, *, min_boundaries: int, min_validated: int) -> None:
        path = PILOT_DIR / filename
        if not path.is_file():
            self.skipTest(f"{filename} not present in {PILOT_DIR} on this machine")
        result = extract_questions(path)
        self.assertGreaterEqual(
            len(result.questions), min_boundaries,
            f"{filename}: detected only {len(result.questions)} question boundaries, "
            f"expected at least {min_boundaries} - possible boundary-detection regression",
        )
        validated = _validated_count(result)
        self.assertGreaterEqual(
            validated, min_validated,
            f"{filename}: only {validated} questions auto-validated, "
            f"expected at least {min_validated} - possible option/confidence regression",
        )

    def test_enem_2025_dia1(self):
        self._check("2025_PV_impresso_D1_CD1.pdf", min_boundaries=90, min_validated=71)

    def test_enem_2025_dia2(self):
        self._check("2025_PV_impresso_D2_CD5.pdf", min_boundaries=98, min_validated=88)

    def test_unicamp_2024(self):
        self._check("unicamp_2024_f1_X.pdf", min_boundaries=73, min_validated=59)

    def test_uece_cev_2025(self):
        self._check("uece_cev_20252f1g2.pdf", min_boundaries=85, min_validated=75)

    def test_ita_2024(self):
        # VALIDATED floor dropped by 1 (was 41) when GARBLED_ENCODING
        # detection landed - a real question here has an unmapped math
        # font glyph (see test_fuvest_2024_caderno_x below for the full
        # story) that used to slip through as VALIDATED; it now correctly
        # routes to REVIEW_REQUIRED. A lower VALIDATED count from THAT
        # check is a correctness improvement, not a regression.
        #
        # Dropped by 1 again (was 40) when _MIN_GAP_CANDIDATE_CLUSTER_SIZE
        # landed (structure.py, fixing a real FUVEST page - see that
        # module's own docstring): page 16 of THIS booklet is genuinely
        # single-column (five questions' options laid out as full-width
        # rows, never two independent side-by-side columns), but used to
        # be wrongly classified as multi-column anyway, because one
        # question's small per-option fraction-fragment clusters happened
        # to produce a gap that passed every check. Reordering a
        # genuinely single-column page is exactly the "far worse than not
        # reordering at all" risk this whole module exists to avoid - it
        # happened to not corrupt any OTHER question on that page, but
        # only by luck, and it happened to give Question 43 a paragraph-
        # break artifact its real geometry does not have (a WORD-style
        # marker with no other confidence signal). No longer reordering
        # that page is the more correct, safer behaviour in general, even
        # though it costs this one question's lucky accidental boost.
        self._check("ita_2024_fase1.pdf", min_boundaries=56, min_validated=39)

    def test_uerj(self):
        # The real regression this guards: "Questão" glued to its number
        # with zero literal whitespace (a real UERJ PDF text-extraction
        # quirk) used to collapse detection to just 2 boundaries.
        self._check("uerj_exame_unico_objetiva.pdf", min_boundaries=60, min_validated=57)

    def test_pucrio_2025_dia1_tarde(self):
        self._check("pucrio_2025_1dia_tarde_g1345.pdf", min_boundaries=15, min_validated=12)

    def test_pucrio_2025_dia2_manha(self):
        # Boundary floor LOWERED from 41 to 33 (the one documented
        # exception to "never lower without a reason" - the old 41 was
        # itself measuring a bug, not ground truth): this booklet's page 2
        # is a full periodic table of elements handed out as a reference
        # sheet. Each element's atomic number sits alone on its own line,
        # and one of its ~10 group columns independently satisfied every
        # geometric check the standalone-bare-marker heuristic used (see
        # structure.py's _MAX_BARE_NUMBERS_PER_PAGE) - so the engine used
        # to also "detect" ~8 fake questions numbered from atomic numbers
        # (37, 39, 40, 41, 44, 49, 50, 53), jumbling the whole document's
        # real 1..33 sequence in the process. 33 is the real count.
        self._check("pucrio_2025_2dia_manha_g2.pdf", min_boundaries=33, min_validated=27)

    def test_fuvest_2024_caderno_x(self):
        # Was the motivating case for a THIRD marker convention support -
        # boundary detection used to find only 9 of ~90 real questions on
        # this booklet (FUVEST prints the question number alone on its own
        # line, no punctuation - a convention the engine could not see at
        # all). Now finds all 90; see structure.py's
        # _normalize_standalone_number_markers for how this is confirmed
        # safe (recurring column position + spaced far apart vertically +
        # position varies with content, page to page) without colliding
        # with an unrelated numbered list/grid/page-footer elsewhere in a
        # DIFFERENT real document (UECE, UERJ, PUC-Rio all guard specific
        # real false-positive patterns this convention could otherwise
        # match - see the dedicated tests in test_phase27_question_extraction.py).
        #
        # VALIDATED floor dropped again (was 37) when GARBLED_ENCODING
        # detection landed: this exact booklet is what surfaced it (a
        # math-heavy PDF's embedded font for a special symbol - almost
        # certainly a fraction bar/radical - lacks a correct ToUnicode
        # mapping, so PyMuPDF decodes it into an unrelated script, e.g.
        # real Oriya letters, mid-statement). 8 questions on this booklet
        # carry that flag and now correctly route to REVIEW_REQUIRED
        # instead of being silently VALIDATED with garbage embedded in
        # otherwise-real content - a correctness improvement, not a
        # regression.
        self._check("fuvest2024_primeira_fase_prova_X.pdf", min_boundaries=90, min_validated=83)
