"""PHASE 28 - Question Reconstruction & Review Quality: tests.

Structural-invariant assertions (spec s14 explicitly allows this instead of
literal text comparison): question number, page range, alternative count/
labels, column assignment, cross-page reconstruction, absence of gabarito/
header/footer contamination. No AI/LLM anywhere in this module or the code
it exercises (spec s6) - asserted directly via an AST import scan.

Covers:
- ``structure.py``'s new ``lines_in_range`` / ``detect_repeated_page_artifacts``.
- ``reconstruction.py``'s ``_quality_score`` / ``reconstruct_question`` /
  ``review_reasons_for``.
- Eight synthetic PDF fixtures (spec s15, cases A-H).
- Determinism (same file processed twice -> identical result).
- Performance at 1/10/50/100 questions (no quadratic blowup).
- Service-layer persistence of the PHASE 28 fields + status-history audit
  trail on every review transition.
"""

from __future__ import annotations

import ast
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agente_ia_edu.services.question_extraction.boundary import OptionDraft
from agente_ia_edu.services.question_extraction.engine import extract_questions
from agente_ia_edu.services.question_extraction.reconstruction import (
    BROKEN_READING_ORDER,
    COLUMN_AMBIGUITY,
    _quality_score,
    reconstruct_question,
    review_reasons_for,
)
from agente_ia_edu.services.question_extraction.structure import (
    DocumentStructure,
    TextLine,
    detect_repeated_page_artifacts,
)


def _pdf(path: Path, pages: list[list[tuple[float, float, str, float]]],
         images: list[tuple[int, float, float, float, float]] | None = None) -> None:
    """``pages``: one list of (x, y, text, fontsize) per page.
    ``images``: [(page_index, x0, y0, x1, y1), ...] - a small solid-color
    square inserted at that rect, big enough to survive ``associate_assets``'
    degenerate-image filter."""
    import fitz
    doc = fitz.open()
    for page_lines in pages:
        page = doc.new_page()
        for x, y, text, size in page_lines:
            page.insert_text((x, y), text, fontsize=size)
    for page_index, x0, y0, x1, y1 in (images or []):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
        pix.set_rect(pix.irect, (200, 0, 0))
        doc[page_index].insert_image(fitz.Rect(x0, y0, x1, y1), pixmap=pix)
    doc.save(str(path))


class StructureAdditionsTests(unittest.TestCase):
    def test_lines_in_range_returns_only_contributing_lines(self):
        lines = [
            TextLine(page=1, x0=0, y0=0, x1=10, y1=10, text="alpha"),
            TextLine(page=1, x0=0, y0=12, x1=10, y1=22, text="beta"),
            TextLine(page=1, x0=0, y0=24, x1=10, y1=34, text="gamma"),
        ]
        structure = DocumentStructure(document_hash="x", page_count=1, lines=lines)
        full = structure.text()
        beta_start = full.index("beta")
        beta_end = beta_start + len("beta")
        selected = structure.lines_in_range(beta_start, beta_end)
        self.assertEqual([ln.text for ln in selected], ["beta"])

    def test_lines_in_range_spans_a_multi_line_slice(self):
        lines = [
            TextLine(page=1, x0=0, y0=0, x1=10, y1=10, text="one"),
            TextLine(page=1, x0=0, y0=12, x1=10, y1=22, text="two"),
            TextLine(page=1, x0=0, y0=24, x1=10, y1=34, text="three"),
        ]
        structure = DocumentStructure(document_hash="x", page_count=1, lines=lines)
        selected = structure.lines_in_range(0, len(structure.text()))
        self.assertEqual([ln.text for ln in selected], ["one", "two", "three"])

    def test_detect_repeated_page_artifacts_needs_three_distinct_pages(self):
        header = TextLine(page=1, x0=10, y0=2, x1=100, y1=10, text="MATERIAL DIDATICO")
        pages = [
            (1, 800.0, [header]),
            (2, 800.0, [TextLine(page=2, x0=10, y0=2, x1=100, y1=10, text="MATERIAL DIDATICO")]),
        ]
        self.assertEqual(detect_repeated_page_artifacts(pages), set())
        pages.append((3, 800.0, [TextLine(page=3, x0=10, y0=2, x1=100, y1=10, text="MATERIAL DIDATICO")]))
        self.assertEqual(detect_repeated_page_artifacts(pages), {"MATERIAL DIDATICO"})

    def test_detect_repeated_page_artifacts_ignores_body_text_outside_margin(self):
        body = TextLine(page=1, x0=10, y0=400, x1=100, y1=410, text="Enunciado repetido por acaso")
        pages = [(n, 800.0, [TextLine(page=n, x0=10, y0=400, x1=100, y1=410,
                                      text="Enunciado repetido por acaso")]) for n in (1, 2, 3)]
        self.assertEqual(detect_repeated_page_artifacts(pages), set())


class ReconstructionUnitTests(unittest.TestCase):
    def test_quality_score_prefers_more_options(self):
        self.assertGreater(
            _quality_score("stmt", [OptionDraft("A", "x")] * 4),
            _quality_score("stmt", [OptionDraft("A", "x")] * 2),
        )

    def test_quality_score_prefers_longer_statement_when_options_tie(self):
        self.assertGreater(_quality_score("a much longer statement", []), _quality_score("short", []))

    def test_reconstruct_question_no_op_below_minimum_line_count(self):
        from agente_ia_edu.services.question_extraction.boundary import QuestionBoundary
        structure = DocumentStructure(document_hash="x", page_count=1, lines=[
            TextLine(page=1, x0=0, y0=0, x1=10, y1=10, text="1. pouca coisa"),
        ])
        boundary = QuestionBoundary(number=1, marker_style="PERIOD", start=0,
                                     end=len(structure.text()), preceded_by_paragraph_break=True)
        result = reconstruct_question(structure, boundary, structure.text())
        self.assertFalse(result.reconstruction_applied)
        self.assertIsNone(result.column_split_x)

    def test_reconstruct_question_adopts_a_measurably_better_column_split(self):
        from agente_ia_edu.services.question_extraction.boundary import QuestionBoundary
        # left column (statement, 3 lines) interleaved row-by-row with a
        # right column (5 options) - exactly the corruption PHASE 27's
        # default reading order produces on a genuine two-column page.
        rows = [
            (56.0, "1.   Enunciado parte um da questao."),
            (320.0, "A) alternativa um"),
            (56.0, "Enunciado parte dois da questao."),
            (320.0, "B) alternativa dois"),
            (56.0, "Enunciado parte tres da questao."),
            (320.0, "C) alternativa tres"),
            (320.0, "D) alternativa quatro"),
            (320.0, "E) alternativa cinco"),
        ]
        lines = [
            TextLine(page=1, x0=x, y0=float(i * 12), x1=x + 100, y1=float(i * 12 + 10), text=text)
            for i, (x, text) in enumerate(rows)
        ]
        structure = DocumentStructure(document_hash="x", page_count=1, lines=lines)
        full_text = structure.text()
        boundary = QuestionBoundary(number=1, marker_style="PERIOD", start=0,
                                     end=len(full_text), preceded_by_paragraph_break=True)
        original_body = full_text[boundary.start:boundary.end]
        result = reconstruct_question(structure, boundary, original_body)
        self.assertTrue(result.reconstruction_applied)
        self.assertIsNotNone(result.column_split_x)
        self.assertEqual([o.label for o in result.options], ["A", "B", "C", "D", "E"])
        for opt in result.options:
            # the reconstructed option text is clean - no leaked statement
            # fragment ("Enunciado parte...") glued onto it (spec s2/s26).
            self.assertNotIn("Enunciado", opt.text)

    def test_reconstruct_question_rejects_a_hanging_indent_misread_as_two_columns(self):
        from agente_ia_edu.services.question_extraction.boundary import QuestionBoundary
        # Real regression found on a real ITA exam: an entirely ordinary
        # single-column question whose multi-line options wrap with a
        # HANGING INDENT (the marker "A ( )" starts a line; when the
        # option's own text wraps, the CONTINUATION indents to align
        # under the TEXT, not the marker - a universal typesetting
        # convention, not a second column) geometrically resembles two
        # real columns just as well as a genuine one does: two x-clusters,
        # comparable width, comparable vertical span. The original
        # (natural, correct) reading already has each option's marker
        # immediately followed by its own continuation - complete and
        # correctly ordered. "Reconstructing" it instead groups all five
        # markers' first lines together, THEN all five continuations
        # together, unmarked - so `_extract_options` (verbatim capture
        # from one marker to the next) truncates every option except the
        # LAST, which absorbs every other option's orphaned continuation
        # as its own tail: one wildly long option beside four short,
        # truncated ones.
        #
        # What actually tips the raw quality score (option count,
        # statement length) in the contaminated candidate's favour: a
        # WORD-style marker ("Questão 1.") shares its OWN physical PDF
        # line with the first bit of the statement - ``lines_in_range``
        # returns that whole line, so the reconstructed candidate's
        # statement carries the marker text (which ``original_body``,
        # sliced precisely at the parsed body offset, never includes),
        # making it a few characters LONGER than the real original for a
        # reason with nothing to do with structure. Real option count
        # ties (5 vs 5); the marker-leak alone was enough to win the
        # statement-length tiebreak. The resulting options' lengths are
        # the real, structural tell this comparison misses entirely.
        marker = "Questão 1."
        rows = [
            (56.0, f"{marker}   Enunciado de uma questao comum, com alternativas"),
            (56.0, "que quebram linha com um recuo pendurado, nada mais."),
            (56.0, "A ( )  primeira parte da alternativa um,"),
            (92.0, "com uma segunda linha bem mais longa aqui tambem."),
            (56.0, "B ( )  primeira parte da alternativa dois,"),
            (92.0, "tambem com uma segunda linha mais longa aqui igual."),
            (56.0, "C ( )  primeira parte da alternativa tres,"),
            (92.0, "e mais uma segunda linha longa igual as outras tres."),
            (56.0, "D ( )  primeira parte da alternativa quatro,"),
            (92.0, "com sua propria segunda linha tambem bem longa assim."),
            (56.0, "E ( )  primeira parte da alternativa cinco,"),
            (92.0, "e a ultima segunda linha, do mesmo tamanho das outras."),
        ]
        lines = [
            TextLine(page=1, x0=x, y0=float(i * 15),
                    x1=x + (430 if x == 56 else 390), y1=float(i * 15 + 10), text=text)
            for i, (x, text) in enumerate(rows)
        ]
        structure = DocumentStructure(document_hash="x", page_count=1, lines=lines)
        full_text = structure.text()
        body_start = full_text.index("Enunciado")
        boundary = QuestionBoundary(number=1, marker_style="WORD", start=body_start,
                                     end=len(full_text), preceded_by_paragraph_break=True)
        original_body = full_text[boundary.start:boundary.end]
        result = reconstruct_question(structure, boundary, original_body)
        self.assertFalse(result.reconstruction_applied)

    def test_review_reasons_for_reports_column_ambiguity_when_split_found_but_not_adopted(self):
        from agente_ia_edu.services.question_extraction.boundary import ExtractedQuestionDraft
        from agente_ia_edu.services.question_extraction.reconstruction import ReconstructionResult
        draft = ExtractedQuestionDraft(
            number=1, question_type="unknown", raw_text="x", normalized_text="",
            flags={"possible_missing_content"}, confidence=0.3,
        )
        recon = ReconstructionResult(reconstructed_text="x", options=[], reconstruction_applied=False,
                                      column_split_x=123.0)
        self.assertIn(COLUMN_AMBIGUITY, review_reasons_for(draft, reconstruction=recon))

    def test_review_reasons_for_reports_broken_reading_order_when_no_split_found(self):
        from agente_ia_edu.services.question_extraction.boundary import ExtractedQuestionDraft
        from agente_ia_edu.services.question_extraction.reconstruction import ReconstructionResult
        draft = ExtractedQuestionDraft(
            number=1, question_type="unknown", raw_text="x", normalized_text="",
            flags={"possible_missing_content"}, confidence=0.3,
        )
        recon = ReconstructionResult(reconstructed_text="x", options=[], reconstruction_applied=False,
                                      column_split_x=None)
        self.assertIn(BROKEN_READING_ORDER, review_reasons_for(draft, reconstruction=recon))


class SyntheticCaseTests(unittest.TestCase):
    """Spec s15 cases A-H. Each asserts structural invariants, never a
    literal-text comparison (spec explicitly allows this)."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_case_a_single_column_needs_no_reconstruction(self):
        path = self.dir / "case_a.pdf"
        _pdf(path, [[
            (72, 60, "1.   Enunciado de uma questao em coluna unica, bem formada.", 9),
            (90, 75, "a) alternativa um", 9), (90, 87, "b) alternativa dois", 9),
            (90, 99, "c) alternativa tres", 9), (90, 111, "d) alternativa quatro", 9),
        ]])
        result = extract_questions(path)
        self.assertEqual(len(result.questions), 1)
        q = result.questions[0]
        self.assertFalse(q.reconstruction_applied)
        self.assertEqual(len(q.draft.options), 4)

    def test_case_b_two_column_is_reconstructed(self):
        path = self.dir / "case_b.pdf"
        _pdf(path, [[
            (56, 60, "1.   Enunciado parte um.", 9),
            (320, 60, "A) alternativa um", 9),
            (56, 75, "Enunciado parte dois.", 9),
            (320, 75, "B) alternativa dois", 9),
            (56, 90, "Enunciado parte tres.", 9),
            (320, 90, "C) alternativa tres", 9),
            (320, 105, "D) alternativa quatro", 9),
            (320, 120, "E) alternativa cinco", 9),
        ]])
        result = extract_questions(path)
        self.assertEqual(len(result.questions), 1)
        q = result.questions[0]
        self.assertTrue(q.reconstruction_applied)
        self.assertEqual(len(q.draft.options), 5)
        self.assertEqual(q.review_status, "VALIDATED")

    def test_case_c_header_and_footer_are_stripped_around_a_two_column_question(self):
        header = "MATERIAL DIDATICO - QUIMICA"
        footer = "Pagina 1"
        pages = []
        for _ in range(3):
            pages.append([
                (56, 20, header, 8),
                (56, 780, footer, 8),
            ])
        pages[0] += [
            (56, 100, "1.   Enunciado parte um.", 9), (320, 100, "A) alternativa um", 9),
            (56, 115, "Enunciado parte dois.", 9), (320, 115, "B) alternativa dois", 9),
            (56, 130, "Enunciado parte tres.", 9), (320, 130, "C) alternativa tres", 9),
            (320, 145, "D) alternativa quatro", 9), (320, 160, "E) alternativa cinco", 9),
        ]
        path = self.dir / "case_c.pdf"
        _pdf(path, pages)
        result = extract_questions(path)
        self.assertEqual(len(result.questions), 1)
        q = result.questions[0]
        self.assertNotIn(header, q.draft.raw_text)
        self.assertNotIn(footer, q.draft.raw_text)
        self.assertTrue(q.reconstruction_applied)

    def test_case_d_cross_page_question_keeps_all_options(self):
        path = self.dir / "case_d.pdf"
        _pdf(path, [
            [(72, 700, "1.   Enunciado que comeca na primeira pagina e continua na proxima.", 9)],
            [(72, 60, "a) alternativa um", 9), (72, 75, "b) alternativa dois", 9),
             (72, 90, "c) alternativa tres", 9), (72, 105, "d) alternativa quatro", 9)],
        ])
        result = extract_questions(path)
        self.assertEqual(len(result.questions), 1)
        q = result.questions[0]
        self.assertTrue(q.cross_page)
        self.assertEqual(q.source_page_start, 1)
        self.assertEqual(q.source_page_end, 2)
        self.assertEqual(len(q.draft.options), 4)

    def test_case_e_cross_page_alternatives_are_not_falsely_column_reconstructed(self):
        path = self.dir / "case_e.pdf"
        _pdf(path, [
            [(72, 700, "1.   Enunciado completo nesta pagina.", 9),
             (72, 715, "a) alternativa um", 9), (72, 730, "b) alternativa dois", 9),
             (72, 745, "c) alternativa tres", 9)],
            [(72, 60, "d) alternativa quatro", 9), (72, 75, "e) alternativa cinco", 9)],
        ])
        result = extract_questions(path)
        self.assertEqual(len(result.questions), 1)
        q = result.questions[0]
        self.assertTrue(q.cross_page)
        self.assertEqual(len(q.draft.options), 5)

    def test_case_f_image_question_is_associated_and_flagged(self):
        path = self.dir / "case_f.pdf"
        _pdf(
            path,
            [[(72, 60, "1.   Observe a figura e responda: a) opcao um b) opcao dois c) opcao tres d) opcao quatro", 9)]],
            images=[(0, 200.0, 200.0, 260.0, 260.0)],
        )
        result = extract_questions(path)
        self.assertEqual(len(result.questions), 1)
        q = result.questions[0]
        self.assertTrue(len(q.assets) >= 1)
        self.assertIn("image_present", q.draft.flags)

    def test_case_g_discursive_question_has_no_alternatives(self):
        path = self.dir / "case_g.pdf"
        _pdf(path, [[
            (72, 60, "1.   Explique com suas palavras por que a agua ferve a temperaturas diferentes em altitudes diferentes.", 9),
        ]])
        result = extract_questions(path)
        self.assertEqual(len(result.questions), 1)
        q = result.questions[0]
        self.assertEqual(q.draft.question_type, "discursive")
        self.assertEqual(q.draft.options, [])

    def test_case_h_gabarito_after_exercises_is_excluded(self):
        path = self.dir / "case_h.pdf"
        _pdf(path, [[
            (72, 60, "1.   Primeira questao com enunciado suficiente.", 9),
            (90, 75, "a) um", 9), (90, 87, "b) dois", 9), (90, 99, "c) tres", 9), (90, 111, "d) quatro", 9),
            (72, 140, "2.   Segunda questao com enunciado suficiente.", 9),
            (90, 155, "a) um", 9), (90, 167, "b) dois", 9), (90, 179, "c) tres", 9), (90, 191, "d) quatro", 9),
            (72, 230, "Gabarito:", 9),
            (72, 245, "01. B", 9), (72, 257, "02. A", 9),
        ]])
        result = extract_questions(path)
        self.assertEqual(len(result.questions), 2)
        self.assertEqual({q.draft.number for q in result.questions}, {1, 2})
        for q in result.questions:
            self.assertNotIn("Gabarito", q.draft.raw_text)


class DeterminismTests(unittest.TestCase):
    def test_same_file_processed_twice_is_byte_identical_in_every_structural_field(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "det.pdf"
            _pdf(path, [[
                (56, 60, "1.   Enunciado parte um.", 9), (320, 60, "A) alternativa um", 9),
                (56, 75, "Enunciado parte dois.", 9), (320, 75, "B) alternativa dois", 9),
                (56, 90, "Enunciado parte tres.", 9), (320, 90, "C) alternativa tres", 9),
                (320, 105, "D) alternativa quatro", 9), (320, 120, "E) alternativa cinco", 9),
            ]])
            r1 = extract_questions(path)
            r2 = extract_questions(path)
            self.assertEqual(r1.document_hash, r2.document_hash)
            self.assertEqual(len(r1.questions), len(r2.questions))
            for a, b in zip(r1.questions, r2.questions):
                self.assertEqual(a.draft.number, b.draft.number)
                self.assertEqual(a.draft.normalized_text, b.draft.normalized_text)
                self.assertEqual([o.label for o in a.draft.options], [o.label for o in b.draft.options])
                self.assertEqual(a.draft.confidence, b.draft.confidence)
                self.assertEqual(a.reconstruction_applied, b.reconstruction_applied)
                self.assertEqual(a.review_status, b.review_status)
                self.assertEqual(a.review_reasons, b.review_reasons)


class PerformanceTests(unittest.TestCase):
    def _make(self, path: Path, n: int) -> None:
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        y = 50
        for i in range(1, n + 1):
            page.insert_text((72, y), f"{i}.   Enunciado da questao numero {i} razoavelmente longo.", fontsize=9)
            y += 14
            for letter in "abcd":
                page.insert_text((90, y), f"{letter}) alternativa {letter}", fontsize=9)
                y += 12
            y += 16
            if y > 780:
                page = doc.new_page()
                y = 50
        doc.save(str(path))

    def test_no_quadratic_blowup_from_1_to_100_questions(self):
        timings = {}
        with TemporaryDirectory() as tmp:
            for n in (1, 10, 50, 100):
                path = Path(tmp) / f"perf_{n}.pdf"
                self._make(path, n)
                start = time.perf_counter()
                result = extract_questions(path)
                timings[n] = time.perf_counter() - start
                self.assertEqual(len(result.questions), n)
        # a quadratic algorithm scales ~100x from n=10 to n=100; an O(n) or
        # O(n log n) one scales roughly linearly. Allow generous slack for
        # timing noise on a shared CI box while still catching real O(n^2).
        ratio = timings[100] / max(timings[10], 1e-6)
        self.assertLess(ratio, 40, f"suspicious superlinear scaling: {timings}")


class ServicePersistenceTests(unittest.TestCase):
    """PHASE 28's service-layer wiring: reconstructed_text/review_reasons/
    status_history are persisted and the audit trail is appended on every
    manual review transition (spec s19)."""

    @classmethod
    def setUpClass(cls):
        import asyncio
        cls.loop = asyncio.new_event_loop()

    @classmethod
    def tearDownClass(cls):
        cls.loop.close()

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def test_reconstruction_fields_and_audit_trail_are_persisted(self):
        import uuid as _uuid
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import StaticPool
        from agente_ia_edu.db.base import Base
        from agente_ia_edu.db.models import IngestionDocument
        from agente_ia_edu.services.question_extraction_service import QuestionExtractionService
        from agente_ia_edu.services.question_publication_service import QuestionPublicationService

        async def scenario():
            engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)

            with TemporaryDirectory() as tmp:
                pdf_path = Path(tmp) / "svc.pdf"
                _pdf(pdf_path, [[
                    (56, 60, "1.   Enunciado parte um.", 9), (320, 60, "A) alternativa um", 9),
                    (56, 75, "Enunciado parte dois.", 9), (320, 75, "B) alternativa dois", 9),
                    (56, 90, "Enunciado parte tres.", 9), (320, 90, "C) alternativa tres", 9),
                    (320, 105, "D) alternativa quatro", 9), (320, 120, "E) alternativa cinco", 9),
                ]])
                doc_id = _uuid.uuid4()
                async with factory() as s:
                    s.add(IngestionDocument(
                        id=doc_id, filename="svc.pdf", document_type="PDF",
                        document_hash="hash-svc", storage_uri=str(pdf_path),
                        file_size_bytes=pdf_path.stat().st_size, status="processed",
                    ))
                    await s.commit()

                async with factory() as s:
                    svc = QuestionExtractionService(s)
                    run, created = await svc.run_extraction(
                        doc_id, pdf_path, started_by="prof_x", school_id=None,
                    )
                    self.assertTrue(created)
                    questions = await svc.list_questions(run.id)
                    self.assertEqual(len(questions), 1)
                    q = questions[0]
                    self.assertTrue(q.reconstruction_applied)
                    self.assertTrue(q.reconstructed_text)
                    self.assertIsInstance(q.status_history, list)
                    self.assertEqual(len(q.status_history), 1)
                    self.assertIsNone(q.status_history[0]["from_status"])
                    self.assertEqual(q.status_history[0]["to_status"], q.review_status)

                    approved = await svc.approve_question(q.id, reviewed_by="prof_x")
                    self.assertEqual(len(approved.status_history), 2)
                    self.assertEqual(approved.status_history[-1]["from_status"], "VALIDATED")
                    self.assertEqual(approved.status_history[-1]["to_status"], "APPROVED")
                    self.assertEqual(approved.status_history[-1]["actor"], "prof_x")

                    pub = QuestionPublicationService(s)
                    result = await pub.publish_run(run.id, published_by="prof_y", school_id=None)
                    self.assertEqual(result["published_count"], 1)
                    published = await svc.get_question(q.id)
                    self.assertEqual(published.review_status, "PUBLISHED")
                    self.assertEqual(len(published.status_history), 3)
                    self.assertEqual(published.status_history[-1]["to_status"], "PUBLISHED")
                    self.assertEqual(published.status_history[-1]["actor"], "prof_y")
            await engine.dispose()

        self._run(scenario())


class AIAgnosticGuardTests(unittest.TestCase):
    """Spec s6/s20: zero AI/LLM decision-making anywhere in the PHASE 28
    reconstruction pipeline."""

    _FORBIDDEN = {"openai", "AsyncOpenAI", "OpenAIProvider", "providers",
                  "classification_consensus", "classification_prompts"}

    def _assert_clean(self, path: Path) -> None:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], self._FORBIDDEN, f"{path}: {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], self._FORBIDDEN, f"{path}: {node.module}")

    def test_reconstruction_module_has_no_ai_imports(self):
        root = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu" / "services" / "question_extraction"
        for name in ("reconstruction.py", "structure.py", "boundary.py", "engine.py", "validation.py", "assets.py"):
            self._assert_clean(root / name)

    def test_service_module_has_no_ai_imports(self):
        path = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu" / "services" / "question_extraction_service.py"
        self._assert_clean(path)


if __name__ == "__main__":
    unittest.main()
