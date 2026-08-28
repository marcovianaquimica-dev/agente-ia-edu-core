import asyncio
import inspect
import unittest
from datetime import datetime

from agente_ia_edu.providers.adapters.fake import FakeProvider
from agente_ia_edu.providers.contracts import EmbeddingProvider, TextGenerationProvider
from agente_ia_edu.providers.models import (
    EmbeddingArtifact,
    EmbeddingRequest,
    TextGenerationRequest,
    TextGenerationResult,
)


class ProviderContractsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = FakeProvider()

    def test_fake_satisfies_provider_protocols(self) -> None:
        self.assertIsInstance(self.provider, TextGenerationProvider)
        self.assertIsInstance(self.provider, EmbeddingProvider)

    def test_generate_is_async_and_returns_neutral_result(self) -> None:
        self.assertTrue(inspect.iscoroutinefunction(self.provider.generate))

        result = asyncio.run(
            self.provider.generate(TextGenerationRequest(prompt="Classifique esta questão."))
        )

        self.assertIsInstance(result, TextGenerationResult)
        self.assertEqual(result.provider, "fake")
        self.assertEqual(result.model, "fake-text-v1")
        self.assertEqual(result.text, "FAKE RESPONSE: Classifique esta questão.")

    def test_embed_is_async(self) -> None:
        self.assertTrue(inspect.iscoroutinefunction(self.provider.embed))

        result = asyncio.run(self.provider.embed(EmbeddingRequest(texts=("texto",))))

        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(result.provider, "fake")

    def test_same_text_produces_same_embedding(self) -> None:
        request = EmbeddingRequest(texts=("mesmo texto",))

        first = asyncio.run(self.provider.embed(request))
        second = asyncio.run(self.provider.embed(request))

        self.assertEqual(first, second)
        self.assertEqual(first.artifacts[0].vector, second.artifacts[0].vector)

    def test_embedding_artifact_preserves_required_metadata(self) -> None:
        result = asyncio.run(
            self.provider.embed(EmbeddingRequest(texts=("texto canônico",)))
        )
        artifact = result.artifacts[0]

        self.assertIsInstance(artifact, EmbeddingArtifact)
        self.assertEqual(artifact.canonical_text, "texto canônico")
        self.assertEqual(len(artifact.text_hash), 64)
        self.assertEqual(artifact.provider, "fake")
        self.assertEqual(artifact.model, "fake-embedding-v1")
        self.assertEqual(artifact.dimensions, 8)
        self.assertEqual(len(artifact.vector), artifact.dimensions)
        self.assertIsInstance(artifact.generated_at, datetime)


if __name__ == "__main__":
    unittest.main()
