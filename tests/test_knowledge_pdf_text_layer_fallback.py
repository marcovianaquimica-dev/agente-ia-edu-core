"""CEREBRO / Knowledge Engine - Fase 3: leitura da camada de texto do PDF.

A Fase 0 encontrou um PDF (a BNCC EI+EF) que ``pypdf`` NAO abre
(``PdfReadError: Cannot find Root object in pdf``, mesmo com
``strict=False``) e que ``pymupdf`` le sem esforco - 600 paginas, texto
limpo. O arquivo nao esta corrompido; e o parser de container do pypdf que
falha.

Hoje esse caso e indistinguivel, no codigo, de um PDF sem camada de texto:
as duas situacoes terminam em excecao. Sao problemas diferentes e merecem
respostas diferentes - um precisa de outro leitor, o outro precisa de OCR.

A alteracao e deliberadamente minima: as oito linhas de leitura de
``parse_authorial_pdf`` viram uma funcao propria, e ``parse_authorial_pdf``
ganha um parametro ``page_texts`` opcional, no idioma ``parsed_override`` que
``IngestionService.ingest_document`` ja usa. Quando pypdf funciona, a saida e
IDENTICA - a suite PHASE 26 existente e o portao de regressao.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf
from pypdf.errors import PdfReadError

from agente_ia_edu.services import authorial_material_parser as parser_module
from agente_ia_edu.services.authorial_material_parser import (
    PdfTextLayer,
    parse_authorial_pdf,
    read_pdf_page_texts,
)


def _write_pdf(path: Path, pages: list[str]) -> Path:
    """Gera um PDF real, com camada de texto de verdade."""
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=11)
    document.save(str(path))
    document.close()
    return path


class _PdfCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class HappyPathTests(_PdfCase):
    def test_a_normal_pdf_is_read_by_pypdf(self):
        path = _write_pdf(self.tmp / "ok.pdf", ["Capitulo 10 Estequiometria", "Pagina dois"])
        layer = read_pdf_page_texts(path)
        self.assertIsInstance(layer, PdfTextLayer)
        self.assertEqual(layer.method, "PDF_TEXT_LAYER")
        self.assertEqual(len(layer.page_texts), 2)
        self.assertIn("Estequiometria", layer.page_texts[0])

    def test_blank_pages_are_preserved_as_empty_strings_not_dropped(self):
        """A posicao da pagina e a rastreabilidade; perder uma pagina vazia
        deslocaria todas as seguintes."""
        path = _write_pdf(self.tmp / "gap.pdf", ["um", "", "tres"])
        layer = read_pdf_page_texts(path)
        self.assertEqual(len(layer.page_texts), 3)
        self.assertEqual(layer.page_texts[1].strip(), "")


class FallbackTests(_PdfCase):
    def _break_pypdf(self):
        """Simula o container que o pypdf nao abre.

        O arquivo real que motiva isto (BNCC EI+EF) foi verificado na Fase 0;
        aqui o que se testa e a LOGICA de fallback, que nao depende de ter o
        arquivo patologico em maos.
        """
        original = parser_module.PdfReader

        def exploding(*args, **kwargs):
            raise PdfReadError("Cannot find Root object in pdf")

        parser_module.PdfReader = exploding
        self.addCleanup(lambda: setattr(parser_module, "PdfReader", original))

    def test_when_pypdf_cannot_open_the_container_pymupdf_takes_over(self):
        path = _write_pdf(self.tmp / "quebrado.pdf", ["Texto que o pymupdf le"])
        self._break_pypdf()
        layer = read_pdf_page_texts(path)
        self.assertEqual(layer.method, "PDF_TEXT_LAYER_PYMUPDF")
        self.assertIn("pymupdf le", layer.page_texts[0])

    def test_the_fallback_also_fires_when_pypdf_returns_no_text_at_all(self):
        """O segundo caminho de falha ja codificado - nao so a excecao."""
        path = _write_pdf(self.tmp / "sem-texto-no-pypdf.pdf", ["conteudo real"])
        original = parser_module.PdfReader

        class Silent:
            def __init__(self, *args, **kwargs):
                real = original(*args, **kwargs)
                self.pages = [_Blank() for _ in real.pages]

        class _Blank:
            def extract_text(self):
                return ""

        parser_module.PdfReader = Silent
        self.addCleanup(lambda: setattr(parser_module, "PdfReader", original))

        layer = read_pdf_page_texts(path)
        self.assertEqual(layer.method, "PDF_TEXT_LAYER_PYMUPDF")
        self.assertIn("conteudo real", layer.page_texts[0])

    def test_when_neither_reader_finds_text_the_error_points_at_ocr(self):
        """O caso Usberco: PDF puramente de imagem. Continua indo para OCR,
        que e o destino correto - o fallback nao o mascara."""
        path = _write_pdf(self.tmp / "so-imagem.pdf", ["", ""])
        with self.assertRaises(ValueError) as caught:
            read_pdf_page_texts(path)
        self.assertIn("OCR", str(caught.exception))

    def test_without_pymupdf_installed_the_behaviour_is_exactly_todays(self):
        original_import = parser_module._import_pymupdf
        parser_module._import_pymupdf = lambda: None
        self.addCleanup(lambda: setattr(parser_module, "_import_pymupdf", original_import))
        self._break_pypdf()

        path = _write_pdf(self.tmp / "x.pdf", ["texto"])
        with self.assertRaises(ValueError) as caught:
            read_pdf_page_texts(path)
        self.assertIn("Unable to read PDF text layer", str(caught.exception))


class ParserIntegrationTests(_PdfCase):
    """``parse_authorial_pdf`` aceita paginas prontas, para nao reler o
    arquivo - mesmo idioma de ``parsed_override``."""

    def test_passing_page_texts_avoids_a_second_read_and_gives_the_same_result(self):
        path = _write_pdf(
            self.tmp / "livro.pdf",
            ["Capitulo 1 - Introducao\n\nTexto do capitulo um.",
             "Capitulo 2 - Estequiometria\n\nTexto do capitulo dois."],
        )
        from_file = parse_authorial_pdf(path)
        layer = read_pdf_page_texts(path)
        from_pages = parse_authorial_pdf(path, page_texts=layer.page_texts)

        self.assertEqual(
            [s.title for s in from_file.sections], [s.title for s in from_pages.sections]
        )
        self.assertEqual(from_file.page_count, from_pages.page_count)
        self.assertEqual(from_file.document_hash, from_pages.document_hash)

    def test_the_default_path_is_untouched_when_pypdf_works(self):
        """Portao de regressao da PHASE 26, em miniatura: sem page_texts, o
        comportamento e o de sempre."""
        path = _write_pdf(self.tmp / "a.pdf", ["Capitulo 1 - Teste\n\nCorpo."])
        parsed = parse_authorial_pdf(path)
        self.assertEqual(parsed.page_count, 1)
        self.assertTrue(parsed.sections)


if __name__ == "__main__":
    unittest.main()
