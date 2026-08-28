import asyncio
import inspect
import unittest
from datetime import datetime, timezone

from agente_ia_edu.providers.contracts import EmbeddingProvider, TextGenerationProvider
from agente_ia_edu.providers.errors import (
    AllProvidersFailedError,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from agente_ia_edu.providers.models import (
    EmbeddingArtifact,
    EmbeddingRequest,
    EmbeddingResult,
    TextGenerationRequest,
    TextGenerationResult,
)
from agente_ia_edu.providers.router import ProviderRouter


class ControlledProvider:
    def __init__(self, name, text_result=None, embedding_result=None, text_error=None, embedding_error=None):
        self.provider = name
        self.text_result = text_result
        self.embedding_result = embedding_result
        self.text_error = text_error
        self.embedding_error = embedding_error
        self.generate_calls = 0
        self.embed_calls = 0

    async def generate(self, request):
        self.generate_calls += 1
        if self.text_error is not None:
            raise self.text_error
        return self.text_result

    async def embed(self, request):
        self.embed_calls += 1
        if self.embedding_error is not None:
            raise self.embedding_error
        return self.embedding_result


def text_result(provider):
    return TextGenerationResult(text="resultado", provider=provider, model=f"{provider}-model")


def embedding_result(provider):
    artifact = EmbeddingArtifact(
        canonical_text="texto",
        text_hash="hash",
        vector=(0.1, 0.2),
        dimensions=2,
        provider=provider,
        model=f"{provider}-model",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return EmbeddingResult(
        artifacts=(artifact,),
        provider=provider,
        model=f"{provider}-model",
        dimensions=2,
    )


class ProviderRouterTests(unittest.TestCase):
    def test_primary_success(self):
        primary = ControlledProvider("primary", text_result=text_result("primary"))
        fallback = ControlledProvider("fallback", text_result=text_result("fallback"))
        router = ProviderRouter([primary, fallback], [])

        result = asyncio.run(router.generate(TextGenerationRequest(prompt="pergunta")))

        self.assertEqual(result, text_result("primary"))
        self.assertEqual(primary.generate_calls, 1)

    def test_fallback_for_transient_errors(self):
        for error in (ProviderUnavailableError, ProviderRateLimitError, ProviderTimeoutError):
            with self.subTest(error=error):
                primary = ControlledProvider("primary", text_error=error())
                fallback = ControlledProvider("fallback", text_result=text_result("fallback"))
                router = ProviderRouter([primary, fallback], [])

                result = asyncio.run(router.generate(TextGenerationRequest(prompt="pergunta")))

                self.assertEqual(result.provider, "fallback")
                self.assertEqual(fallback.generate_calls, 1)

    def test_authentication_does_not_fallback(self):
        primary = ControlledProvider("primary", text_error=ProviderAuthenticationError())
        fallback = ControlledProvider("fallback", text_result=text_result("fallback"))
        router = ProviderRouter([primary, fallback], [])

        with self.assertRaises(ProviderAuthenticationError):
            asyncio.run(router.generate(TextGenerationRequest(prompt="pergunta")))

        self.assertEqual(fallback.generate_calls, 0)

    def test_invalid_response_does_not_fallback(self):
        primary = ControlledProvider("primary", text_error=ProviderInvalidResponseError())
        fallback = ControlledProvider("fallback", text_result=text_result("fallback"))
        router = ProviderRouter([primary, fallback], [])

        with self.assertRaises(ProviderInvalidResponseError):
            asyncio.run(router.generate(TextGenerationRequest(prompt="pergunta")))

        self.assertEqual(fallback.generate_calls, 0)

    def test_provider_after_success_is_not_called(self):
        primary = ControlledProvider("primary", text_result=text_result("primary"))
        fallback = ControlledProvider("fallback", text_result=text_result("fallback"))
        router = ProviderRouter([primary, fallback], [])

        asyncio.run(router.generate(TextGenerationRequest(prompt="pergunta")))

        self.assertEqual(fallback.generate_calls, 0)

    def test_all_transient_failures_raise_neutral_error(self):
        first = ControlledProvider("first", text_error=ProviderUnavailableError())
        second = ControlledProvider("second", text_error=ProviderTimeoutError())
        router = ProviderRouter([first, second], [])

        with self.assertRaises(AllProvidersFailedError) as context:
            asyncio.run(router.generate(TextGenerationRequest(prompt="pergunta")))

        self.assertEqual(
            [(attempt.provider, attempt.error_type) for attempt in context.exception.attempts],
            [
                ("first", "ProviderUnavailableError"),
                ("second", "ProviderTimeoutError"),
            ],
        )
        self.assertFalse(any(isinstance(value, BaseException) for value in context.exception.attempts))

    def test_empty_provider_list_raises_configuration_error(self):
        router = ProviderRouter([], [])

        with self.assertRaises(ProviderConfigurationError):
            asyncio.run(router.generate(TextGenerationRequest(prompt="pergunta")))
        with self.assertRaises(ProviderConfigurationError):
            asyncio.run(router.embed(EmbeddingRequest(texts=("texto",))))

    def test_result_preserves_real_provider_and_model(self):
        primary = ControlledProvider("primary", text_error=ProviderUnavailableError())
        fallback = ControlledProvider("real-provider", text_result=text_result("real-provider"))
        router = ProviderRouter([primary, fallback], [])

        result = asyncio.run(router.generate(TextGenerationRequest(prompt="pergunta")))

        self.assertEqual(result.provider, "real-provider")
        self.assertEqual(result.model, "real-provider-model")

    def test_text_and_embedding_lists_are_independent(self):
        text_provider = ControlledProvider("text", text_result=text_result("text"))
        embedding_provider = ControlledProvider("embedding", embedding_result=embedding_result("embedding"))
        router = ProviderRouter([text_provider], [embedding_provider])

        text = asyncio.run(router.generate(TextGenerationRequest(prompt="pergunta")))
        embedding = asyncio.run(router.embed(EmbeddingRequest(texts=("texto",))))

        self.assertEqual(text.provider, "text")
        self.assertEqual(embedding.provider, "embedding")
        self.assertEqual(text_provider.embed_calls, 0)
        self.assertEqual(embedding_provider.generate_calls, 0)

    def test_methods_are_async(self):
        self.assertTrue(inspect.iscoroutinefunction(ProviderRouter.generate))
        self.assertTrue(inspect.iscoroutinefunction(ProviderRouter.embed))

    def test_router_satisfies_both_protocols(self):
        router = ProviderRouter([], [])

        self.assertIsInstance(router, TextGenerationProvider)
        self.assertIsInstance(router, EmbeddingProvider)

    def test_embedding_fallback_preserves_response(self):
        primary = ControlledProvider("primary", embedding_error=ProviderRateLimitError())
        fallback = ControlledProvider("fallback", embedding_result=embedding_result("fallback"))
        router = ProviderRouter([], [primary, fallback])

        result = asyncio.run(router.embed(EmbeddingRequest(texts=("texto",))))

        self.assertEqual(result, embedding_result("fallback"))
        self.assertEqual(result.artifacts[0].provider, "fallback")


if __name__ == "__main__":
    unittest.main()
