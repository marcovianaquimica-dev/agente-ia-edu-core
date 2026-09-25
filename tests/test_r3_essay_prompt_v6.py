import json
import unittest

from agente_ia_edu.essay_prompts import available_versions, get_essay_prompt


class EssayPromptV6Tests(unittest.TestCase):
    def setUp(self):
        self.rubric = {
            "rubric_version": "ENEM_2025",
            "competencies": [
                {"code": "C1", "official_title": "Domínio da norma padrão",
                 "levels": [{"points": 0, "descriptor": "..."}]},
            ],
        }

    def test_registered_and_available(self):
        self.assertIn("essay_correction_v6", available_versions())

    def test_text_offset_prompt_embeds_the_text_and_schema(self):
        artifact = get_essay_prompt("essay_correction_v6")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET",
            essay_statement="Disserte sobre X.",
            rubric=self.rubric,
            include_scores=True,
            text="Um texto qualquer.",
        )
        self.assertIn("RESPONSE_SCHEMA:", prompt)
        self.assertIn("TEXT_OFFSET", prompt)
        self.assertIn(json.dumps("Um texto qualquer.", ensure_ascii=False), prompt)
        self.assertIn("ESSAY_STATEMENT:", prompt)
        self.assertIn("RUBRIC:", prompt)
        self.assertNotIn('"identification"', prompt)

    def test_image_region_prompt_embeds_page_count_and_asks_for_line_counting(self):
        artifact = get_essay_prompt("essay_correction_v6")
        prompt = artifact.build(
            anchor_mode="IMAGE_REGION",
            essay_statement="Disserte sobre X.",
            rubric=self.rubric,
            include_scores=True,
            page_count=2,
        )
        self.assertIn("IMAGE_REGION", prompt)
        self.assertIn("PAGE_COUNT: 2", prompt)
        self.assertIn('"line": int, "total_lines": int', prompt)
        self.assertNotIn('"x": float', prompt)
        self.assertNotIn("dimensoes REAIS de cada pagina", prompt)

    def test_image_region_prompt_does_not_require_page_dimensions(self):
        """v6 drops page_dimensions entirely - line-counting needs no pixel
        scale (unlike v3-v5's x/y/width/height, which did)."""
        artifact = get_essay_prompt("essay_correction_v6")
        prompt = artifact.build(
            anchor_mode="IMAGE_REGION", essay_statement="Disserte.",
            rubric=self.rubric, include_scores=True, page_count=1,
        )
        self.assertIn("PAGE_COUNT: 1", prompt)

    def test_image_region_prompt_requires_positive_page_count(self):
        artifact = get_essay_prompt("essay_correction_v6")
        with self.assertRaises(ValueError):
            artifact.build(
                anchor_mode="IMAGE_REGION", essay_statement="Disserte.",
                rubric=self.rubric, include_scores=True, page_count=0,
            )

    def test_unknown_anchor_mode_raises(self):
        artifact = get_essay_prompt("essay_correction_v6")
        with self.assertRaises(ValueError):
            artifact.build(
                anchor_mode="SOMETHING_ELSE", essay_statement="Disserte.", rubric=self.rubric,
                include_scores=True,
            )

    def test_avaliativo_asks_for_a_grade(self):
        artifact = get_essay_prompt("essay_correction_v6")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("SCORING_MODE: AVALIATIVO", prompt)

    def test_formativo_asks_for_no_grade(self):
        artifact = get_essay_prompt("essay_correction_v6")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=False, text="Um texto.",
        )
        self.assertIn("SCORING_MODE: FORMATIVO", prompt)
        self.assertIn("null", prompt.split("SCORING_MODE:")[1])

    def test_image_region_anchor_shape_uses_single_braces_not_doubled(self):
        artifact = get_essay_prompt("essay_correction_v6")
        prompt = artifact.build(
            anchor_mode="IMAGE_REGION", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, page_count=1,
        )
        self.assertIn('{"type": "IMAGE_REGION"', prompt)
        self.assertNotIn('{{"type"', prompt)

    def test_system_policy_demands_portuguese_output(self):
        artifact = get_essay_prompt("essay_correction_v6")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("SEMPRE em portugues do Brasil", prompt)
        self.assertIn("nunca em ingles", prompt)

    def test_coverage_rules_require_localized_annotations_per_competency(self):
        artifact = get_essay_prompt("essay_correction_v6")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("COVERAGE_RULES", prompt)
        self.assertIn("pelo menos uma annotation com evidence_kind=LOCALIZED", prompt)

    def test_coverage_rules_state_no_maximum_or_minimum_annotation_count(self):
        """Confirmed live (2026-09-25): v4's "pelo menos 5" floor was treated
        as a ceiling by the model - every correction landed at exactly 5
        annotations. v5 must not state any fixed count at all."""
        artifact = get_essay_prompt("essay_correction_v6")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("Nao existe um numero maximo", prompt)
        self.assertNotIn("pelo menos 5 annotations no total", prompt)


if __name__ == "__main__":
    unittest.main()
