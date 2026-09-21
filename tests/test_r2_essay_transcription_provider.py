import asyncio
import math
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agente_ia_edu.providers.adapters.fake import FakeProvider
from agente_ia_edu.providers.adapters.openai import OpenAIProvider
from agente_ia_edu.providers.errors import ProviderConfigurationError
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


class FactoryTests(unittest.TestCase):
    def test_build_essay_transcriber_requires_configuration(self):
        with self.assertRaises(ProviderConfigurationError):
            build_essay_transcriber("openai")


if __name__ == "__main__":
    unittest.main()
