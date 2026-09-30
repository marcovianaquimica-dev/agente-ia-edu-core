# tests/test_mass_correction_batch_ocr.py
import base64
import unittest
from pathlib import Path

from agente_ia_edu.services.mass_correction_batch import (
    OCR_SYSTEM_PROMPT,
    apply_ocr_batch_result,
    build_ocr_batch_request,
)


class BuildOcrBatchRequestTests(unittest.TestCase):
    def test_builds_a_valid_batch_line_shape(self):
        image_path = Path(__file__).parent / "fixtures" / "tiny.png"
        image_path.parent.mkdir(exist_ok=True)
        image_path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        ))

        line = build_ocr_batch_request("page-123", image_path)

        self.assertEqual(line["custom_id"], "page-123")
        self.assertEqual(line["method"], "POST")
        self.assertEqual(line["url"], "/v1/chat/completions")
        messages = line["body"]["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": OCR_SYSTEM_PROMPT})
        image_content = messages[1]["content"][0]
        self.assertEqual(image_content["type"], "image_url")
        self.assertEqual(image_content["image_url"]["detail"], "high")
        self.assertTrue(image_content["image_url"]["url"].startswith("data:image/png;base64,"))


class ApplyOcrBatchResultTests(unittest.TestCase):
    def test_returns_custom_id_and_transcribed_text_on_success(self):
        result_line = {
            "custom_id": "page-123",
            "response": {
                "status_code": 200,
                "body": {"choices": [{"message": {"content": "texto transcrito aqui"}}]},
            },
            "error": None,
        }
        custom_id, text = apply_ocr_batch_result(result_line)
        self.assertEqual(custom_id, "page-123")
        self.assertEqual(text, "texto transcrito aqui")

    def test_raises_on_a_batch_level_error(self):
        result_line = {"custom_id": "page-123", "response": None, "error": {"message": "rate limited"}}
        with self.assertRaises(ValueError) as caught:
            apply_ocr_batch_result(result_line)
        self.assertIn("page-123", str(caught.exception))

    def test_raises_on_a_non_200_response_status(self):
        result_line = {
            "custom_id": "page-123",
            "response": {"status_code": 500, "body": {"error": "internal"}},
            "error": None,
        }
        with self.assertRaises(ValueError) as caught:
            apply_ocr_batch_result(result_line)
        self.assertIn("500", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
