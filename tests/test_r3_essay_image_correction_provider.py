import asyncio
import base64
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agente_ia_edu.providers.adapters.openai import OpenAIProvider
from agente_ia_edu.providers.errors import (
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderUnavailableError,
)
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


    def test_raises_when_vision_model_not_configured_but_key_present(self):
        # Distinct branch from test_raises_when_not_configured: api_key IS
        # set (and request.model is unset), only vision_model is missing.
        provider = OpenAIProvider(api_key="sk-test", vision_model=None)
        request = EssayImageCorrectionRequest(
            image_paths=(Path("/tmp/page1.png"),), mime_type="image/png", prompt="corrija",
        )
        with self.assertRaises(ProviderConfigurationError):
            asyncio.run(provider.correct_from_images(request))

    def test_raises_invalid_response_on_empty_correction(self):
        page = Path("/tmp/r3_empty_test_page.png")
        page.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        async def _create(**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""))])

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
        provider = OpenAIProvider(api_key="sk-test", vision_model="gpt-4o-mini", client=fake_client)
        request = EssayImageCorrectionRequest(
            image_paths=(page,), mime_type="image/png", prompt="corrija",
        )
        with self.assertRaises(ProviderInvalidResponseError):
            asyncio.run(provider.correct_from_images(request))
        page.unlink(missing_ok=True)

    def test_maps_generic_exception_during_correction(self):
        page = Path("/tmp/r3_error_test_page.png")
        page.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        async def _create(**kwargs):
            raise RuntimeError("boom")

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
        provider = OpenAIProvider(api_key="sk-test", vision_model="gpt-4o-mini", client=fake_client)
        request = EssayImageCorrectionRequest(
            image_paths=(page,), mime_type="image/png", prompt="corrija",
        )
        with self.assertRaises(ProviderUnavailableError):
            asyncio.run(provider.correct_from_images(request))
        page.unlink(missing_ok=True)


class FactoryTests(unittest.TestCase):
    def test_build_essay_image_corrector_requires_configuration(self):
        with self.assertRaises(ProviderConfigurationError):
            build_essay_image_corrector("openai")

    def test_build_essay_image_corrector_raises_when_vision_model_missing(self):
        with patch.dict(
            "os.environ",
            {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"},
            clear=False,
        ):
            import os as _os
            _os.environ.pop("OPENAI_VISION_MODEL", None)
            with self.assertRaises(ProviderConfigurationError) as ctx:
                build_essay_image_corrector("openai")
        self.assertIn("OPENAI_VISION_MODEL", str(ctx.exception))

    def test_build_essay_image_corrector_succeeds_with_full_configuration(self):
        with patch.dict(
            "os.environ",
            {
                "AI_PROVIDER": "openai",
                "OPENAI_API_KEY": "test-key",
                "OPENAI_VISION_MODEL": "gpt-4o-mini",
            },
            clear=False,
        ):
            provider = build_essay_image_corrector("openai")
        self.assertIsInstance(provider, OpenAIProvider)

    def test_build_essay_image_corrector_raises_for_unsupported_provider(self):
        with self.assertRaises(ProviderConfigurationError) as ctx:
            build_essay_image_corrector("provider_x")
        self.assertIn("provider_x", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
