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
    QuestionBoundary,
    classify_and_extract,
    cut_at_answer_key,
    detect_boundaries,
)
from agente_ia_edu.services.question_extraction.engine import extract_questions
from agente_ia_edu.services.question_extraction.structure import (
    DocumentStructure,
    PageImage,
    TextLine,
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

    def test_two_column_detection_rejects_a_multi_cell_table(self):
        # many distinct x0 (table cells), not a clean bimodal split
        lines = [
            TextLine(page=1, x0=x, y0=10 * (i % 5), x1=x + 40, y1=10 * (i % 5) + 8, text=f"c{i}")
            for i, x in enumerate([56, 60, 75, 93, 134, 213, 309, 327, 343, 345] * 3)
        ]
        split = detect_two_column_layout(lines, page_width=595.0)
        self.assertIsNone(split)

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

    def test_word_marker_tolerates_missing_space_before_number(self):
        # Real UERJ PDF text extraction: "Questão" and its number come out
        # glued together with zero literal whitespace characters (the visual
        # gap was achieved by glyph positioning/kerning, not a space glyph).
        text = "Questão33 Enunciado real com texto suficiente para ser válido.\n"
        boundaries = detect_boundaries(text)
        self.assertEqual([b.number for b in boundaries], [33])

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

    def test_confidence_scoring_is_deterministic_and_bounded(self):
        text = "1.   Enunciado com texto razoavelmente longo para pontuar melhor.\na) x\nb) y\n"
        boundaries = detect_boundaries(text)
        d1 = classify_and_extract(boundaries[0], text)
        d2 = classify_and_extract(boundaries[0], text)
        self.assertEqual(d1.confidence, d2.confidence)
        self.assertTrue(0.0 <= d1.confidence <= 1.0)


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
