import asyncio
import math
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agente_ia_edu.providers.adapters.fake import FakeProvider
from agente_ia_edu.providers.adapters.openai import OpenAIProvider
from agente_ia_edu.providers.errors import (
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderUnavailableError,
)
from agente_ia_edu.providers.factory import build_essay_transcriber
from agente_ia_edu.providers.models import EssayPageTranscriptionRequest


class FakeProviderTranscriptionTests(unittest.TestCase):
    def test_returns_deterministic_tokens_for_the_same_path(self):
        provider = FakeProvider()
        request = EssayPageTranscriptionRequest(
            image_path=Path("/tmp/page1.png"), mime_type="image/png"
        )
        result1 = asyncio.run(provider.transcribe_page(request))
        result2 = asyncio.run(provider.transcribe_page(request))
        self.assertEqual(result1.tokens, result2.tokens)
        self.assertGreater(len(result1.tokens), 0)
        for token in result1.tokens:
            self.assertTrue(token.text)
            self.assertTrue(0.0 <= token.confidence <= 1.0)


class OpenAIProviderTranscriptionTests(unittest.TestCase):
    def test_raises_when_not_configured(self):
        provider = OpenAIProvider(api_key=None, vision_model=None)
        request = EssayPageTranscriptionRequest(
            image_path=Path("/tmp/page1.png"), mime_type="image/png"
        )
        with self.assertRaises(ProviderConfigurationError):
            asyncio.run(provider.transcribe_page(request))

    def test_derives_confidence_from_logprobs(self):
        image_path = Path("/tmp/r2_test_page.png")
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        fake_client = SimpleNamespace()
        fake_client.chat = SimpleNamespace()

        async def _create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="ola mundo"),
                        logprobs=SimpleNamespace(
                            content=[
                                SimpleNamespace(token="ola", logprob=0.0),
                                SimpleNamespace(token=" mundo", logprob=math.log(0.5)),
                            ]
                        ),
                    )
                ]
            )

        fake_client.chat.completions = SimpleNamespace(create=_create)

        provider = OpenAIProvider(
            api_key="sk-test", vision_model="gpt-4o-mini", client=fake_client
        )
        request = EssayPageTranscriptionRequest(image_path=image_path, mime_type="image/png")
        result = asyncio.run(provider.transcribe_page(request))

        self.assertEqual(len(result.tokens), 2)
        self.assertEqual(result.tokens[0].text, "ola")
        self.assertAlmostEqual(result.tokens[0].confidence, 1.0, places=4)
        self.assertEqual(result.tokens[1].text, " mundo")
        self.assertAlmostEqual(result.tokens[1].confidence, 0.5, places=4)
        image_path.unlink(missing_ok=True)


    def test_raises_when_vision_model_not_configured_but_key_present(self):
        # Distinct branch from test_raises_when_not_configured: api_key IS set,
        # only vision_model is missing.
        provider = OpenAIProvider(api_key="sk-test", vision_model=None)
        request = EssayPageTranscriptionRequest(
            image_path=Path("/tmp/page1.png"), mime_type="image/png"
        )
        with self.assertRaises(ProviderConfigurationError):
            asyncio.run(provider.transcribe_page(request))

    def test_raises_invalid_response_on_empty_transcription(self):
        image_path = Path("/tmp/r2_empty_test_page.png")
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        async def _create(**kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=""), logprobs=None)]
            )

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
        provider = OpenAIProvider(api_key="sk-test", vision_model="gpt-4o-mini", client=fake_client)
        request = EssayPageTranscriptionRequest(image_path=image_path, mime_type="image/png")
        with self.assertRaises(ProviderInvalidResponseError):
            asyncio.run(provider.transcribe_page(request))
        image_path.unlink(missing_ok=True)

    def test_maps_generic_exception_during_transcription(self):
        image_path = Path("/tmp/r2_error_test_page.png")
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        async def _create(**kwargs):
            raise RuntimeError("boom")

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
        provider = OpenAIProvider(api_key="sk-test", vision_model="gpt-4o-mini", client=fake_client)
        request = EssayPageTranscriptionRequest(image_path=image_path, mime_type="image/png")
        with self.assertRaises(ProviderUnavailableError):
            asyncio.run(provider.transcribe_page(request))
        image_path.unlink(missing_ok=True)

    def test_tokens_fallback_full_confidence_when_no_logprobs(self):
        # _tokens_from_logprobs: when the API returns content but no logprobs
        # payload at all, every token should get full confidence rather than
        # an invented low-confidence number (documented in the docstring).
        provider = OpenAIProvider(api_key="sk-test", vision_model="gpt-4o-mini")
        tokens = provider._tokens_from_logprobs("hello world", None)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].text, "hello world")
        self.assertEqual(tokens[0].confidence, 1.0)
        self.assertEqual(tokens[0].start, 0)
        self.assertEqual(tokens[0].end, len("hello world"))

    def test_tokens_from_logprobs_skips_piece_not_found_in_content(self):
        # If a logprob entry's token text can't be located in the transcribed
        # content (idx == -1), that entry should be skipped rather than
        # crashing or emitting a bogus token.
        provider = OpenAIProvider(api_key="sk-test", vision_model="gpt-4o-mini")
        logprobs = SimpleNamespace(
            content=[
                SimpleNamespace(token="ghost", logprob=0.0),
                SimpleNamespace(token="ola", logprob=0.0),
            ]
        )
        tokens = provider._tokens_from_logprobs("ola mundo", logprobs)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].text, "ola")


class FactoryTests(unittest.TestCase):
    def test_build_essay_transcriber_requires_configuration(self):
        with self.assertRaises(ProviderConfigurationError):
            build_essay_transcriber("openai")

    def test_build_essay_transcriber_raises_when_vision_model_missing(self):
        with patch.dict(
            "os.environ",
            {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"},
            clear=False,
        ):
            import os as _os
            _os.environ.pop("OPENAI_VISION_MODEL", None)
            with self.assertRaises(ProviderConfigurationError) as ctx:
                build_essay_transcriber("openai")
        self.assertIn("OPENAI_VISION_MODEL", str(ctx.exception))

    def test_build_essay_transcriber_succeeds_with_full_configuration(self):
        with patch.dict(
            "os.environ",
            {
                "AI_PROVIDER": "openai",
                "OPENAI_API_KEY": "test-key",
                "OPENAI_VISION_MODEL": "gpt-4o-mini",
            },
            clear=False,
        ):
            provider = build_essay_transcriber("openai")
        self.assertIsInstance(provider, OpenAIProvider)

    def test_build_essay_transcriber_raises_for_unsupported_provider(self):
        with self.assertRaises(ProviderConfigurationError) as ctx:
            build_essay_transcriber("provider_x")
        self.assertIn("provider_x", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
