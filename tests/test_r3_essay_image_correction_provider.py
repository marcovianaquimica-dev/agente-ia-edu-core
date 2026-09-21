import asyncio
import base64
import unittest
from pathlib import Path
from types import SimpleNamespace

from agente_ia_edu.providers.adapters.openai import OpenAIProvider
from agente_ia_edu.providers.errors import ProviderConfigurationError
from agente_ia_edu.providers.factory import build_essay_image_corrector
from agente_ia_edu.providers.models import EssayImageCorrectionRequest


class OpenAIImageCorrectionTests(unittest.TestCase):
    def test_raises_when_not_configured(self):
        provider = OpenAIProvider(api_key=None, vision_model=None)
        request = EssayImageCorrectionRequest(
            image_paths=(Path("/tmp/page1.png"),), mime_type="image/png", prompt="corrija",
        )
        with self.assertRaises(ProviderConfigurationError):
            asyncio.run(provider.correct_from_images(request))

    def test_sends_one_image_block_per_page_and_returns_json_text(self):
        page1 = Path("/tmp/r3_test_page1.png")
        page2 = Path("/tmp/r3_test_page2.png")
        page1.write_bytes(b"\x89PNG\r\n\x1a\nfake1")
        page2.write_bytes(b"\x89PNG\r\n\x1a\nfake2")

        captured = {}

        async def _create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
            )

        fake_client = SimpleNamespace()
        fake_client.chat = SimpleNamespace()
        fake_client.chat.completions = SimpleNamespace(create=_create)

        provider = OpenAIProvider(api_key="sk-test", vision_model="gpt-4o-mini", client=fake_client)
        request = EssayImageCorrectionRequest(
            image_paths=(page1, page2), mime_type="image/png", prompt="corrija a redacao",
        )
        result = asyncio.run(provider.correct_from_images(request))

        self.assertEqual(result.text, '{"ok": true}')
        self.assertEqual(result.model, "gpt-4o-mini")
        self.assertEqual(result.provider, "openai")
        self.assertEqual(captured["response_format"], {"type": "json_object"})
        user_message = captured["messages"][1]
        self.assertEqual(user_message["content"][0], {"type": "text", "text": "corrija a redacao"})
        self.assertEqual(len(user_message["content"]), 3)  # 1 text block + 2 image blocks

        # Verify each image block has correct type and verify per-image ordering/correctness
        expected_page_bytes = [page1.read_bytes(), page2.read_bytes()]
        expected_b64 = [
            base64.b64encode(page1.read_bytes()).decode("ascii"),
            base64.b64encode(page2.read_bytes()).decode("ascii"),
        ]

        for idx, block in enumerate(user_message["content"][1:]):
            self.assertEqual(block["type"], "image_url", f"Block {idx + 1} should be image_url")
            url = block["image_url"]["url"]
            self.assertTrue(url.startswith("data:image/png;base64,"), f"Block {idx + 1} URL should have data URI prefix")

            # Extract and verify the base64 payload matches the expected page
            b64_payload = url.replace("data:image/png;base64,", "")
            self.assertEqual(
                b64_payload,
                expected_b64[idx],
                f"Block {idx + 1} base64 payload should match page{idx + 1} bytes (detects swapped/duplicated images)"
            )

        page1.unlink(missing_ok=True)
        page2.unlink(missing_ok=True)


class FactoryTests(unittest.TestCase):
    def test_build_essay_image_corrector_requires_configuration(self):
        with self.assertRaises(ProviderConfigurationError):
            build_essay_image_corrector("openai")


if __name__ == "__main__":
    unittest.main()
