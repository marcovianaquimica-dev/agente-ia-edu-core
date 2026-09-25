import os
import tempfile
import unittest

from agente_ia_edu.services.essay_pdf_export import (
    _annotations_html,
    _rewrites_html,
    build_render_model,
    filename_for_title,
    pdf_available,
    render_pdf,
)


def _full_correction_view(**overrides) -> dict:
    view = {
        "final_scores": {
            "total": 680,
            "per_competency": {
                "C1": {"points": 160}, "C2": {"points": 160}, "C3": {"points": 120},
                "C4": {"points": 160}, "C5": {"points": 80},
            },
        },
        "final_feedback": {
            "strengths": ["Boa argumentação inicial."],
            "improvements": ["Revisar concordância verbal."],
            "next_essay_strategy": "Praticar coesão entre parágrafos.",
        },
        "annotations": [
            {
                "letter": "A", "competency_code": "C1", "evidence_kind": "LOCALIZED",
                "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 10, "quote": "As redes s"},
                "short_comment": "curto", "long_comment": "longo",
            }
        ],
        "rewrites": [
            {
                "letter": "A", "competency_code": "C1",
                "original": "As redes sociais tem", "suggestion": "As redes sociais têm",
                "pedagogical_goal": "Corrigir concordância.",
            }
        ],
        "alerts": [],
        "intervention": {
            "agente": "poder público", "acao": "criar programa", "meio_modo": "parcerias",
            "finalidade": "reduzir o problema", "detalhamento": None,
            "respeita_direitos_humanos": True,
        },
        "rationales": [
            {
                "competency_code": "C1", "summary": "ok",
                "strengths": "boa norma", "growth_area": "revisar crase",
            }
        ],
        "intro_message": "Olá! Vamos ver sua redação.",
        "closing_message": "Continue praticando!",
        "mechanical_review": [
            {
                "category": "CRASE", "excerpt": "de acordo a pesquisa",
                "suggested_form": "de acordo com a pesquisa", "rule_explanation": "regência de 'de acordo'.",
            }
        ],
    }
    view.update(overrides)
    return view


class EssayPdfExportTests(unittest.TestCase):
    def test_pdf_available_is_true_in_this_environment(self):
        self.assertTrue(pdf_available())

    def test_filename_for_title_sanitizes_and_prefixes(self):
        self.assertEqual(filename_for_title("Redação: Redes Sociais!"), "devolutiva-Redação_ Redes Sociais_.pdf")

    def test_filename_for_title_falls_back_when_title_is_empty_after_sanitizing(self):
        self.assertEqual(filename_for_title("   "), "devolutiva-devolutiva.pdf")

    def test_build_render_model_full_shape(self):
        model = build_render_model(_full_correction_view())
        self.assertEqual(model["total"], 680)
        self.assertEqual(model["points_by_competency"]["C1"], 160)
        self.assertEqual(len(model["competency_rows"]), 1)
        self.assertTrue(model["competency_rows"][0]["has_split"])
        self.assertTrue(model["has_any_split"])
        self.assertEqual(model["intro_message"], "Olá! Vamos ver sua redação.")
        self.assertEqual(model["closing_message"], "Continue praticando!")
        self.assertEqual(len(model["mechanical_occurrences"]), 1)

    def test_build_render_model_tolerates_missing_new_fields(self):
        """Old data: rationales items only have summary, no strengths/growth_area;
        no intro_message/closing_message/mechanical_review keys at all."""
        view = _full_correction_view(
            rationales=[{"competency_code": "C1", "summary": "resumo antigo"}],
            intro_message=None, closing_message=None, mechanical_review=None,
        )
        model = build_render_model(view)
        self.assertFalse(model["competency_rows"][0]["has_split"])
        self.assertEqual(model["competency_rows"][0]["summary"], "resumo antigo")
        self.assertFalse(model["has_any_split"])
        self.assertEqual(model["intro_message"], "")
        self.assertEqual(model["closing_message"], "")
        self.assertEqual(model["mechanical_occurrences"], [])

    def test_build_render_model_tolerates_malformed_shapes(self):
        """A dict where the contract expects a list (matches a real dev/demo
        data artifact found in this project) must not raise."""
        view = _full_correction_view(rationales={"C1": "x"}, annotations={"weird": True})
        model = build_render_model(view)
        self.assertEqual(model["competency_rows"], [])
        self.assertEqual(model["annotations"], [])

    def test_render_pdf_text_offset_produces_valid_pdf_bytes(self):
        model = build_render_model(_full_correction_view())
        pdf_bytes = render_pdf(
            model, title="Impactos das redes sociais", anchor_mode="TEXT_OFFSET",
            canonical_text="As redes sociais tem transformado a opinião pública.",
        )
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 1000)

    def test_render_pdf_image_region_with_no_pages_produces_valid_pdf_bytes(self):
        model = build_render_model(_full_correction_view())
        pdf_bytes = render_pdf(model, title="Foto de teste", anchor_mode="IMAGE_REGION", page_images=None)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 500)

    def test_render_pdf_omits_optional_sections_when_absent(self):
        """No intro_message, no closing_message, no rewrites, empty
        mechanical_review - the PDF must still render (no KeyError/None
        formatting artifact) exactly like essay-report.js's fallback."""
        view = _full_correction_view(
            intro_message=None, closing_message=None, rewrites=[], mechanical_review=[],
        )
        model = build_render_model(view)
        pdf_bytes = render_pdf(
            model, title="Sem campos novos", anchor_mode="TEXT_OFFSET",
            canonical_text="Um texto qualquer para o teste.",
        )
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

    def test_annotations_and_rewrites_html_show_number_not_letter(self):
        """Product ask (2026-09): the header shown next to each annotation and
        each suggested rewrite must show the same number as the numbered
        marker circle (e.g. '1 — C1'), not the engine's internal letter
        (e.g. 'A — C1'). The `letter` field itself is untouched in the data
        - annotations[i]["letter"] is still 'A' below - only the rendered
        HTML changes."""
        model = build_render_model(_full_correction_view())
        self.assertEqual(model["annotations"][0]["letter"], "A")
        self.assertEqual(model["rewrites"][0]["letter"], "A")

        annotations_html = _annotations_html(model)
        self.assertIn("1 — C1", annotations_html)
        self.assertNotIn("A — C1", annotations_html)

        rewrites_html = _rewrites_html(model)
        self.assertIn("1 — C1", rewrites_html)
        self.assertNotIn("A — C1", rewrites_html)

    def test_render_pdf_image_region_draws_overlay_only_for_localized_annotation(self):
        """The main job of render_pdf's IMAGE_REGION-with-pages branch: a real
        page image gets embedded, a LOCALIZED IMAGE_REGION annotation gets an
        overlay box drawn on it, and a GLOBAL IMAGE_REGION annotation (which
        isn't pinned to a spot) does NOT (Fix 5's exclusion, exercised here
        for the first time - see Fix 6 of the 2026-09-24 whole-branch review)."""
        import pymupdf

        fd, png_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            src_doc = pymupdf.open()
            src_doc.new_page(width=200, height=300)
            pix = src_doc[0].get_pixmap()
            pix.save(png_path)
            src_doc.close()

            view = _full_correction_view(
                annotations=[
                    {
                        "letter": "A", "competency_code": "C1", "evidence_kind": "LOCALIZED",
                        "anchor": {
                            "type": "IMAGE_REGION", "page": 1, "x": 10, "y": 10,
                            "width": 50, "height": 20, "read_text": "trecho localizado",
                        },
                        "short_comment": "curto", "long_comment": "longo",
                    },
                    {
                        "letter": "B", "competency_code": "C2", "evidence_kind": "GLOBAL",
                        "anchor": {
                            "type": "IMAGE_REGION", "page": 1, "x": 20, "y": 40,
                            "width": 30, "height": 15, "read_text": "trecho global",
                        },
                        "short_comment": "curto global", "long_comment": "longo global",
                    },
                ],
            )
            model = build_render_model(view)
            pdf_bytes = render_pdf(
                model, title="Foto com anotações", anchor_mode="IMAGE_REGION",
                page_images=[(1, png_path)],
            )
            self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

            result_doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            try:
                image_pages = [p for p in result_doc if len(p.get_images()) > 0]
                self.assertEqual(len(image_pages), 1, "expected exactly one manually-drawn image page")
                image_page = image_pages[0]
                self.assertEqual(len(image_page.get_images()), 1)
                self.assertEqual(len(image_page.get_drawings()), 1)
            finally:
                result_doc.close()
        finally:
            os.unlink(png_path)

    def test_render_pdf_image_region_overlay_uses_line_based_anchor(self):
        """Line-based anchors (contract v2+, no x/y/width/height) must still
        draw a visible overlay - converted to a full-width band for that
        line, mirroring essay-annotations.js's renderImageMarkers."""
        import pymupdf

        fd, png_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            src_doc = pymupdf.open()
            src_doc.new_page(width=400, height=3000)
            pix = src_doc[0].get_pixmap()
            pix.save(png_path)
            src_doc.close()

            view = _full_correction_view(annotations=[{
                "letter": "A", "competency_code": "C2", "evidence_kind": "LOCALIZED",
                "anchor": {
                    "type": "IMAGE_REGION", "page": 1, "line": 3, "total_lines": 30,
                    "read_text": "trecho na linha 3",
                },
                "short_comment": "curto", "long_comment": "longo",
            }])
            model = build_render_model(view)
            pdf_bytes = render_pdf(
                model, title="Foto com anotações", anchor_mode="IMAGE_REGION",
                page_images=[(1, png_path)],
            )
            self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

            result_doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            try:
                image_page = next(p for p in result_doc if len(p.get_images()) > 0)
                self.assertEqual(len(image_page.get_drawings()), 1)
                rect = image_page.get_drawings()[0]["rect"]
                # Line 3 of 30 on a 3000pt-tall source, scaled to fit the
                # page - the overlay must sit near the top, not at (0, 0).
                self.assertGreater(rect.y0, 0)
            finally:
                result_doc.close()
        finally:
            os.unlink(png_path)

    def test_render_pdf_image_region_overlay_uses_a_visible_solid_color(self):
        """Confirmed live (2026-09-25): the overlay was drawn with the pale
        _COMPETENCY_COLORS background tint instead of _COMPETENCY_SOLID_COLORS,
        making a 2pt border essentially invisible against a white page - the
        student reported the devolutiva as having no markings at all. The
        overlay's stroke must be a solid, high-contrast color."""
        import pymupdf

        from agente_ia_edu.services.essay_pdf_export import _COMPETENCY_SOLID_COLORS, _hex_to_rgb

        fd, png_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            src_doc = pymupdf.open()
            src_doc.new_page(width=200, height=300)
            pix = src_doc[0].get_pixmap()
            pix.save(png_path)
            src_doc.close()

            view = _full_correction_view(annotations=[{
                "letter": "A", "competency_code": "C3", "evidence_kind": "LOCALIZED",
                "anchor": {
                    "type": "IMAGE_REGION", "page": 1, "x": 10, "y": 10,
                    "width": 50, "height": 20, "read_text": "trecho localizado",
                },
                "short_comment": "curto", "long_comment": "longo",
            }])
            model = build_render_model(view)
            pdf_bytes = render_pdf(
                model, title="Foto com anotações", anchor_mode="IMAGE_REGION",
                page_images=[(1, png_path)],
            )

            result_doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            try:
                image_page = next(p for p in result_doc if len(p.get_images()) > 0)
                drawing = image_page.get_drawings()[0]
                expected = _hex_to_rgb(_COMPETENCY_SOLID_COLORS["C3"])
                for actual, wanted in zip(drawing["color"], expected):
                    self.assertAlmostEqual(actual, wanted, places=2)
            finally:
                result_doc.close()
        finally:
            os.unlink(png_path)


if __name__ == "__main__":
    unittest.main()
