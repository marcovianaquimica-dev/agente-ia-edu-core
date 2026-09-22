import json
import unittest

from agente_ia_edu.essay_prompts import available_versions, get_essay_prompt


class EssayPromptV1Tests(unittest.TestCase):
    def setUp(self):
        self.rubric = {
            "rubric_version": "ENEM_2025",
            "competencies": [
                {"code": "C1", "official_title": "Domínio da norma padrão",
                 "levels": [{"points": 0, "descriptor": "..."}]},
            ],
        }

    def test_registered_and_available(self):
        self.assertIn("essay_correction_v1", available_versions())

    def test_text_offset_prompt_embeds_the_text_and_schema(self):
        artifact = get_essay_prompt("essay_correction_v1")
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

    def test_text_offset_prompt_requires_text(self):
        artifact = get_essay_prompt("essay_correction_v1")
        with self.assertRaises(ValueError):
            artifact.build(
                anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
                include_scores=True,
            )

    def test_image_region_prompt_embeds_page_count(self):
        artifact = get_essay_prompt("essay_correction_v1")
        prompt = artifact.build(
            anchor_mode="IMAGE_REGION",
            essay_statement="Disserte sobre X.",
            rubric=self.rubric,
            include_scores=True,
            page_count=3,
        )
        self.assertIn("IMAGE_REGION", prompt)
        self.assertIn("PAGE_COUNT: 3", prompt)
        self.assertIn("3 imagem", prompt)

    def test_image_region_prompt_requires_positive_page_count(self):
        artifact = get_essay_prompt("essay_correction_v1")
        with self.assertRaises(ValueError):
            artifact.build(
                anchor_mode="IMAGE_REGION", essay_statement="Disserte.",
                rubric=self.rubric, include_scores=True, page_count=0,
            )

    def test_unknown_anchor_mode_raises(self):
        artifact = get_essay_prompt("essay_correction_v1")
        with self.assertRaises(ValueError):
            artifact.build(
                anchor_mode="SOMETHING_ELSE", essay_statement="Disserte.", rubric=self.rubric,
                include_scores=True,
            )

    def test_avaliativo_asks_for_a_grade(self):
        artifact = get_essay_prompt("essay_correction_v1")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("SCORING_MODE: AVALIATIVO", prompt)

    def test_formativo_asks_for_no_grade(self):
        artifact = get_essay_prompt("essay_correction_v1")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=False, text="Um texto.",
        )
        self.assertIn("SCORING_MODE: FORMATIVO", prompt)
        self.assertIn("scores", prompt)
        self.assertIn("null", prompt.split("SCORING_MODE:")[1])

    def test_text_offset_anchor_shape_uses_single_braces_not_doubled(self):
        """TEXT_OFFSET anchor rules must emit valid JSON with single braces."""
        artifact = get_essay_prompt("essay_correction_v1")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        # Valid JSON syntax with single braces must appear
        self.assertIn('{"type": "TEXT_OFFSET"', prompt)
        # Malformed doubled braces must NOT appear
        self.assertNotIn('{{"type"', prompt)

    def test_image_region_anchor_shape_uses_single_braces_not_doubled(self):
        """IMAGE_REGION anchor rules must emit valid JSON with single braces after format()."""
        artifact = get_essay_prompt("essay_correction_v1")
        prompt = artifact.build(
            anchor_mode="IMAGE_REGION", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, page_count=2,
        )
        # Valid JSON syntax with single braces must appear
        self.assertIn('{"type": "IMAGE_REGION"', prompt)
        # Malformed doubled braces must NOT appear
        self.assertNotIn('{{"type"', prompt)


if __name__ == "__main__":
    unittest.main()
