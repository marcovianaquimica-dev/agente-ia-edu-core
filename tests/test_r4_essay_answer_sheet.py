"""R4 lote - folha de resposta padrao em branco, gerada pelo sistema.

A geometria aqui NAO e cosmetica: o cabecalho ocupa exatamente
HEADER_REGION_FRACTION da altura da pagina, e e essa mesma fracao que
services/essay_batch.py recorta depois pra ler nome e CPF por OCR. Se a
geometria mudar sem a fracao mudar junto, o OCR passa a ler o lugar errado -
por isso os testes abaixo checam posicao, nao so "gerou algum PDF".

Folha e generica (nao menciona o tema da proposta) e sempre mostra uma logo
no cabecalho - a da escola, ou a do Nucleo Edu 360 quando a escola nao tem
uma cadastrada (decisao do usuario, 2026-10-03).
"""

import unittest

from agente_ia_edu.services.essay_answer_sheet import (
    HEADER_REGION_FRACTION,
    LINE_COUNT,
    answer_sheet_available,
    render_answer_sheet_pdf,
)


@unittest.skipUnless(answer_sheet_available(), "pymupdf nao instalado")
class AnswerSheetTests(unittest.TestCase):
    def _open(self, data: bytes):
        import pymupdf

        return pymupdf.open(stream=data, filetype="pdf")

    def test_one_page_per_copy(self):
        doc = self._open(render_answer_sheet_pdf(copies=3))
        try:
            self.assertEqual(doc.page_count, 3)
        finally:
            doc.close()

    def test_defaults_to_one_copy(self):
        doc = self._open(render_answer_sheet_pdf())
        try:
            self.assertEqual(doc.page_count, 1)
        finally:
            doc.close()

    def test_rejects_non_positive_copies(self):
        with self.assertRaises(ValueError):
            render_answer_sheet_pdf(copies=0)

    def test_header_labels_and_title_are_inside_the_header_region(self):
        data = render_answer_sheet_pdf()
        doc = self._open(data)
        try:
            page = doc[0]
            header_bottom = page.rect.height * HEADER_REGION_FRACTION
            for needle in ("NOME COMPLETO DO PARTICIPANTE", "CPF", "FOLHA DE REDA"):
                hits = page.search_for(needle)
                self.assertTrue(hits, f"{needle!r} nao encontrado na folha")
                self.assertLess(
                    max(rect.y1 for rect in hits), header_bottom,
                    f"{needle!r} vazou para fora da regiao de cabecalho",
                )
        finally:
            doc.close()

    def test_title_is_horizontally_centered(self):
        doc = self._open(render_answer_sheet_pdf())
        try:
            page = doc[0]
            hits = page.search_for("FOLHA DE REDAÇÃO")
            self.assertTrue(hits, "titulo nao encontrado na folha")
            center_x = (min(r.x0 for r in hits) + max(r.x1 for r in hits)) / 2
            self.assertAlmostEqual(center_x, page.rect.width / 2, delta=2.0)
        finally:
            doc.close()

    def test_numbered_lines_start_below_the_header_region(self):
        doc = self._open(render_answer_sheet_pdf())
        try:
            page = doc[0]
            header_bottom = page.rect.height * HEADER_REGION_FRACTION
            first = page.search_for("01")
            last = page.search_for(f"{LINE_COUNT:02d}")
            self.assertTrue(first, "numero de linha 01 nao encontrado")
            self.assertTrue(last, f"numero de linha {LINE_COUNT:02d} nao encontrado")
            self.assertGreater(min(r.y0 for r in first), header_bottom)
            self.assertLess(max(r.y1 for r in last), page.rect.height)
        finally:
            doc.close()

    def test_falls_back_to_the_nucleo_edu_logo_when_the_school_has_none(self):
        data = render_answer_sheet_pdf(logo_path=None)
        doc = self._open(data)
        try:
            self.assertEqual(doc.page_count, 1)
            self.assertEqual(len(doc[0].get_images(full=True)), 1)
        finally:
            doc.close()

    def test_embeds_the_school_logo_when_one_is_given(self):
        import tempfile
        from pathlib import Path

        import pymupdf

        tmp_dir = Path(tempfile.mkdtemp(prefix="r4_logo_"))
        logo_path = tmp_dir / "logo.png"
        source = pymupdf.open()
        page = source.new_page(width=120, height=60)
        page.insert_text((10, 35), "LOGO", fontsize=20)
        page.get_pixmap(dpi=150).save(str(logo_path))
        source.close()

        doc = self._open(render_answer_sheet_pdf(logo_path=str(logo_path)))
        try:
            # Exatamente uma logo - a da escola, nunca as duas juntas.
            self.assertEqual(len(doc[0].get_images(full=True)), 1)
        finally:
            doc.close()

    def test_a_missing_logo_file_falls_back_to_the_nucleo_edu_logo(self):
        doc = self._open(
            render_answer_sheet_pdf(logo_path="/caminho/que/nao/existe/logo.png")
        )
        try:
            self.assertEqual(doc.page_count, 1)
            self.assertEqual(len(doc[0].get_images(full=True)), 1)
        finally:
            doc.close()


if __name__ == "__main__":
    unittest.main()
