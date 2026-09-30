import json
import unittest
import uuid

from agente_ia_edu.services.essay_engine_validation import RubricView
from agente_ia_edu.services.mass_correction_batch import (
    apply_correction_batch_result,
    build_correction_batch_request,
)

RUBRIC_PAYLOAD = {
    "rubric_version": "ENEM_2025",
    "competencies": [
        {"code": c, "official_title": "titulo", "levels": [
            {"points": p, "descriptor": "descricao"} for p in (0, 40, 80, 120, 160, 200)
        ]}
        for c in ("C1", "C2", "C3", "C4", "C5")
    ],
}
RUBRIC_VIEW = RubricView(
    rubric_version="ENEM_2025",
    levels={c: frozenset((0, 40, 80, 120, 160, 200)) for c in ("C1", "C2", "C3", "C4", "C5")},
    signal_keys=frozenset(),
)


class BuildCorrectionBatchRequestTests(unittest.TestCase):
    def test_builds_a_text_offset_prompt_for_the_submission(self):
        submission_id = str(uuid.uuid4())
        line = build_correction_batch_request(
            submission_id, essay_statement="Disserte sobre X.",
            rubric_payload=RUBRIC_PAYLOAD, canonical_text="Um texto qualquer.",
            include_scores=True,
        )
        self.assertEqual(line["custom_id"], submission_id)
        prompt_text = line["body"]["messages"][-1]["content"]
        self.assertIn("Um texto qualquer.", prompt_text)
        self.assertIn("TEXT_OFFSET", prompt_text)


class ApplyCorrectionBatchResultTests(unittest.TestCase):
    def test_rejects_and_returns_failure_shape_on_invalid_payload(self):
        result_line = {
            "custom_id": str(uuid.uuid4()),
            "response": {"status_code": 200, "body": {"choices": [{"message": {"content": "{}"}}]}},
            "error": None,
        }
        result = apply_correction_batch_result(result_line, rubric_view=RUBRIC_VIEW, text="Um texto qualquer.")
        self.assertIsNone(result["ai_output"])
        self.assertIsNotNone(result["failure_reason"])
        self.assertEqual(result["rubric_version"], "ENEM_2025")

    def test_a_provider_level_error_also_returns_the_failure_shape(self):
        result_line = {"custom_id": str(uuid.uuid4()), "response": None, "error": {"message": "boom"}}
        result = apply_correction_batch_result(result_line, rubric_view=RUBRIC_VIEW, text="Um texto qualquer.")
        self.assertIsNone(result["ai_output"])
        self.assertIn("boom", result["failure_reason"])
        self.assertEqual(result["rubric_version"], "ENEM_2025")


if __name__ == "__main__":
    unittest.main()
