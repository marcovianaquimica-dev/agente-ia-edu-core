"""Coverage for the undertested branches of ``authorial_material_parser.py``
(SERVICE-layer wave, 2026-09) - synthetic PDF/TXT/MD fixtures generated at
test time via pymupdf (``fitz``), matching the convention already used by
``tests/test_phase26_authorial_material_ingestion.py``. No dependency on a
real personal file.

This module has no HTTP route of its own - it is pure, DB-free parsing logic
consumed by ``AuthorialMaterialIngestionService.ingest_file()``
(``authorial_material_ingestion.py``), which is already exercised end to end
by ``test_phase26_authorial_material_ingestion.py``/``test_authorial_ingestion
_http.py`` for the HAPPY paths. This file targets the specific malformed-
input / structural edge cases those suites never happen to trigger, calling
the parser functions directly (the "direct service-level test" alternative
the wave brief calls for when there is no DB/HTTP layer to go through).

One real bug was found and fixed here (see
test_md_document_starting_with_heading_keeps_first_paragraph_as_content /
test_txt_document_starting_with_heading_keeps_first_paragraph_as_content):
``parse_authorial_text`` used to let the "first non-heading line becomes the
document title" convention fire on ANY line up until one was consumed - so a
document that OPENS with a heading (a very ordinary MD/TXT convention) had
its very first PARAGRAPH silently stolen into the ``title`` field and
dropped from that heading's ``content_lines``. Root-caused in
``parse_authorial_text`` (now gated on "the literal first non-blank line of
the file", not "the first non-heading line encountered").
"""

from __future__ import annotations

import unittest
from pathlib import Path

import fitz

from agente_ia_edu.services.authorial_material_parser import (
    parse_authorial_document,
    parse_authorial_pdf,
    parse_authorial_text,
)

_TMP = Path("/tmp/phase31_authorial_parser_fixtures")


def _pdf(name: str, lines: list[str]) -> Path:
    """A minimal single-page PDF with one ``insert_text`` call per line,
    stacked top to bottom - the same technique test_phase26 already uses."""
    _TMP.mkdir(exist_ok=True)
    path = _TMP / name
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=10)
        y += 20
    doc.save(str(path))
    return path


class UnreadablePdfTests(unittest.TestCase):
    """spec s10 territory: a genuinely broken input must fail loudly with a
    clear ValueError, never silently produce an empty/garbage document."""

    def test_garbage_bytes_raise_value_error(self):
        _TMP.mkdir(exist_ok=True)
        path = _TMP / "garbage.pdf"
        path.write_bytes(b"this is not a pdf file, just plain garbage bytes")
        with self.assertRaises(ValueError) as ctx:
            parse_authorial_pdf(path)
        self.assertIn("Unable to read PDF text layer", str(ctx.exception))

    def test_pdf_with_no_extractable_text_raises_value_error(self):
        path = _TMP / "blank_page.pdf"
        doc = fitz.open()
        doc.new_page()  # no insert_text at all - no text layer whatsoever
        doc.save(str(path))
        with self.assertRaises(ValueError) as ctx:
            parse_authorial_pdf(path)
        self.assertIn("no extractable text layer", str(ctx.exception))


class PdfStructureTests(unittest.TestCase):
    """Chapter/episode boundary handling: numbered subsections, the
    intro-before-the-first-heading rescue, the no-boundary-at-all fallback,
    and a heading immediately followed by an exercise item (an appendix
    with no real subtitle of its own)."""

    def test_numbered_subsection_becomes_heading_marker_in_content_lines(self):
        # the episode heading ends in its own '.' well before "1.1" - if it
        # didn't, _clip_heading_title's own sentence-boundary search would
        # swallow the "1.1 ..." marker AS PART OF the episode heading title
        # instead of leaving it in the body for _NUMBERED_SUBSECTION to see.
        path = _pdf("subsection.pdf", [
            "TEMPORADA 1: TESTE",
            "EPISÓDIO 01 - Introdução Geral.",
            "1.1 Primeiro Conceito. Texto de introducao do primeiro conceito, "
            "com texto suficiente para ultrapassar o minimo exigido.",
            "1.2 Segundo Conceito. Texto de introducao do segundo conceito, "
            "tambem com bastante texto para ficar acima do minimo.",
        ])
        doc = parse_authorial_pdf(path)
        self.assertEqual(len(doc.sections), 1)
        content = doc.sections[0].content_lines
        self.assertTrue(any(line.startswith("## 1.1 Primeiro Conceito") for line in content),
                        content)
        self.assertTrue(any(line.startswith("## 1.2 Segundo Conceito") for line in content),
                        content)

    def test_intro_before_first_boundary_becomes_introducao_section(self):
        path = _pdf("intro.pdf", [
            "TEMPORADA 1: TESTE INTRO",
            "Este e um texto introdutorio bastante longo que antecede qualquer "
            "capitulo ou episodio em todo o documento inteiro.",
            "EPISÓDIO 01 - Depois da Introducao.",
            "01. Pergunta de exemplo com texto suficiente para ultrapassar o minimo exigido?",
            "a) opcao um b) opcao dois c) opcao tres d) opcao quatro e) opcao cinco",
        ])
        doc = parse_authorial_pdf(path)
        self.assertEqual(doc.sections[0].title, "Introdução")
        self.assertEqual(doc.sections[0].position, 0)
        # the episode section shifted to position 2 (1 -> 2) to make room
        self.assertEqual(doc.sections[1].position, 2)
        # the question's section_index shifted along with it (was captured
        # while the episode section was still position 1)
        self.assertEqual(len(doc.questions), 1)
        self.assertEqual(doc.questions[0].section_index, 2)

    def test_no_chapter_or_episode_boundary_is_a_single_section(self):
        # a pure exercise sheet with no TEMPORADA/EPISODIO/Capitulo heading
        # at all must still land every question somewhere - never dropped.
        path = _pdf("no_boundary.pdf", [
            "Lista de exercicios avulsa sem capitulos.",
            "01. Primeira pergunta da lista de exercicios sem capitulo algum, "
            "com texto suficiente?",
            "a) opcao um b) opcao dois c) opcao tres d) opcao quatro e) opcao cinco",
        ])
        doc = parse_authorial_pdf(path)
        self.assertEqual(len(doc.sections), 1)
        self.assertEqual(doc.sections[0].section_type, "SECTION")
        self.assertIsNone(doc.sections[0].section_number)
        self.assertEqual(len(doc.questions), 1)
        self.assertEqual(doc.questions[0].section_index, 0)

    def test_heading_immediately_followed_by_exercise_uses_generic_title(self):
        # an exercise appendix organised per-episode with no real subtitle:
        # the heading is followed straight away by "01. ..." - never invent
        # a fake title out of the exercise text; fall back to "Episode 02".
        path = _pdf("heading_exercise.pdf", [
            "TEMPORADA 1: TESTE APPENDIX",
            "EPISÓDIO 02",
            "01. Pergunta de apendice logo apos o cabecalho do episodio, "
            "sem titulo real algum?",
            "a) opcao um b) opcao dois c) opcao tres d) opcao quatro e) opcao cinco",
        ])
        doc = parse_authorial_pdf(path)
        self.assertEqual(len(doc.sections), 1)
        self.assertEqual(doc.sections[0].title, "Episode 02")
        self.assertEqual(len(doc.questions), 1)


class PdfAssetTests(unittest.TestCase):
    """Visual-evidence detection: a bare textual reference (no embeddable
    image) becomes a review-required PAGE_REGION asset; a real embedded
    image degrades gracefully to "no asset" in THIS environment, because
    the project deliberately keeps Pillow out of the dependency set (see
    pyproject.toml: "Do NOT add pdfplumber here: it pulls in Pillow, which
    changes pypdf's page.images behaviour and breaks the parser") - so
    pypdf's own `page.images` raises ImportError, which
    authorial_material_parser.py (like the existing, unmodified
    ingestion_parser.py PdfParser it mirrors) deliberately swallows rather
    than letting the whole ingestion crash over an unrelated image. The
    "successfully decoded a real embedded image" success path is therefore
    NOT reachable in this project as configured - left deliberately
    untested, not a gap: exercising it would require adding Pillow, which
    the project's own comment says never to do."""

    def test_visual_reference_text_with_no_image_creates_page_region_asset(self):
        path = _pdf("figura_ref.pdf", [
            "TEMPORADA 1: TESTE FIG",
            "EPISÓDIO 01 - Com Figura.",
            "Observe a figura abaixo para entender melhor o fenomeno descrito no texto.",
        ])
        doc = parse_authorial_pdf(path)
        self.assertEqual(doc.total_images, 0)
        self.assertEqual(len(doc.assets), 1)
        self.assertEqual(doc.assets[0].asset_type, "PAGE_REGION")
        self.assertEqual(doc.assets[0].page, 1)

    def test_embedded_image_extraction_failure_degrades_gracefully(self):
        path = _TMP / "embedded_image.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "TEMPORADA 1: TESTE IMG", fontsize=14)
        page.insert_text((72, 100), "EPISÓDIO 01 - Com Imagem.", fontsize=12)
        page.insert_text((72, 130), "Texto de conteudo qualquer para a secao com uma imagem "
                                    "incorporada de verdade no PDF.", fontsize=10)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10))
        pix.set_rect(pix.irect, (255, 0, 0))
        page.insert_image(fitz.Rect(72, 200, 122, 250), stream=pix.tobytes("png"))
        doc.save(str(path))

        parsed = parse_authorial_pdf(path)  # must not raise despite the embedded image
        self.assertEqual(parsed.total_images, 0)


class ExerciseSplittingTests(unittest.TestCase):
    """_extract_options()/_flush_exercises() edge cases: an inline a)-e)
    split is only ever trusted when it is a CLEAN, confidently-ordered
    sequence - anything else keeps the raw statement untouched rather than
    fabricating a broken option list (spec s10: never invent content)."""

    def test_single_option_marker_is_not_treated_as_a_split(self):
        path = _pdf("single_marker.pdf", [
            "Lista avulsa de exercicios.",
            "01. Pergunta unica com apenas uma alternativa mencionada aqui "
            "a) somente uma opcao.",
        ])
        doc = parse_authorial_pdf(path)
        self.assertEqual(len(doc.questions), 1)
        q = doc.questions[0]
        self.assertIsNone(q.alternatives_text)
        self.assertIn("a) somente uma opcao", q.statement_text)

    def test_out_of_order_option_letters_are_not_treated_as_a_split(self):
        path = _pdf("out_of_order.pdf", [
            "Lista avulsa de exercicios.",
            "01. Pergunta com alternativas fora de ordem b) opcao b primeiro "
            "a) opcao a depois c) opcao c por ultimo.",
        ])
        doc = parse_authorial_pdf(path)
        self.assertEqual(len(doc.questions), 1)
        q = doc.questions[0]
        self.assertIsNone(q.alternatives_text)
        self.assertIn("b) opcao b primeiro", q.statement_text)

    def test_exercise_item_with_no_leading_statement_text_is_dropped(self):
        # item 01's body is ENTIRELY options (nothing before the first
        # option marker) -> statement ends up empty -> the item is silently
        # dropped, never fabricated with a blank statement; item 02 (a real
        # question) must still survive. The option letters are UPPERCASE
        # here so _EXERCISE_ITEM's own lookahead (which requires an
        # uppercase letter or '(' right after "01.") recognises "01." as an
        # item start at all - _OPTION_INLINE itself is case-insensitive.
        path = _pdf("no_statement.pdf", [
            "Lista avulsa de exercicios.",
            "01. A) opcao a valida B) opcao b valida C) opcao c valida "
            "D) opcao d valida E) opcao e valida.",
            "02. Pergunta normal seguinte com texto valido e completo "
            "para nao ser descartada?",
            "a) x b) y c) z d) w e) v",
        ])
        doc = parse_authorial_pdf(path)
        self.assertEqual(len(doc.questions), 1)
        self.assertEqual(doc.questions[0].question_number, 2)


class TextAndMarkdownTests(unittest.TestCase):
    """parse_authorial_text(): MD heading recognition and the "no heading at
    all" fallback, plus the title-stealing bug fixed above."""

    def test_md_headings_become_sections(self):
        path = _TMP / "headings.md"
        path.write_text(
            "# Titulo Principal\n\n"
            "Primeiro paragrafo do documento em markdown.\n\n"
            "## Subtitulo\n\n"
            "Segundo paragrafo.\n",
            encoding="utf-8",
        )
        doc = parse_authorial_text(path)
        self.assertEqual([s.title for s in doc.sections], ["Titulo Principal", "Subtitulo"])
        self.assertEqual(doc.sections[1].content_lines, ["Segundo paragrafo."])

    def test_md_document_starting_with_heading_keeps_first_paragraph_as_content(self):
        # regression test for the fixed bug: the title-stealing convention
        # must NOT reach past the very first non-blank line of the file.
        path = _TMP / "starts_with_heading.md"
        path.write_text(
            "# Titulo Principal\n\n"
            "Primeiro paragrafo do documento em markdown.\n\n"
            "## Subtitulo\n\n"
            "Segundo paragrafo.\n",
            encoding="utf-8",
        )
        doc = parse_authorial_text(path)
        self.assertEqual(doc.title, path.stem)  # never stolen from the body
        self.assertEqual(
            doc.sections[0].content_lines,
            ["Primeiro paragrafo do documento em markdown."],
        )

    def test_txt_document_starting_with_heading_keeps_first_paragraph_as_content(self):
        path = _TMP / "starts_with_heading.txt"
        path.write_text(
            "INTRODUCAO\n"
            "Primeiro paragrafo do documento em texto puro apos o cabecalho.\n",
            encoding="utf-8",
        )
        doc = parse_authorial_text(path)
        self.assertEqual(doc.title, path.stem)
        self.assertEqual(doc.sections[0].title, "INTRODUCAO")
        self.assertEqual(
            doc.sections[0].content_lines,
            ["Primeiro paragrafo do documento em texto puro apos o cabecalho."],
        )

    def test_txt_without_any_heading_uses_document_title_as_section_title(self):
        path = _TMP / "no_heading_at_all.txt"
        path.write_text(
            "Titulo do documento\n\n"
            "primeiro paragrafo em minusculas sem nenhum cabecalho.\n"
            "segunda linha tambem sem cabecalho.\n",
            encoding="utf-8",
        )
        doc = parse_authorial_text(path)
        self.assertEqual(doc.title, "Titulo do documento")
        self.assertEqual(len(doc.sections), 1)
        self.assertEqual(doc.sections[0].title, "Titulo do documento")
        self.assertEqual(
            doc.sections[0].content_lines,
            ["primeiro paragrafo em minusculas sem nenhum cabecalho.",
             "segunda linha tambem sem cabecalho."],
        )


class DispatchTests(unittest.TestCase):
    def test_unsupported_extension_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            parse_authorial_document(Path("/tmp/whatever.xyz"))
        self.assertIn("Unsupported authorial document format", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
