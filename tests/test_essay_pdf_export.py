import unittest

from agente_ia_edu.services.essay_pdf_export import (
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
        self.assertEqual(filename_for_title("???"), "devolutiva-___.pdf")

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
        pdf_bytes = render_pdf(model, title="Foto de teste", anchor_mode="IMAGE_REGION", page_image_paths=None)
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


if __name__ == "__main__":
    unittest.main()
