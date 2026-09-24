import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agente_ia_edu.providers.adapters.openai import OpenAIProvider
from agente_ia_edu.providers.errors import (
    AllProvidersFailedError,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from agente_ia_edu.providers.models import TextGenerationRequest
from agente_ia_edu.providers.router import ProviderRouter
from agente_ia_edu.services.question_modification import ModificationType, QuestionModificationAdapter


class StubCompletions:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def client_for(result):
    completions = StubCompletions(result)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


class OpenAIProviderTests(unittest.TestCase):
    def test_requires_key_and_model(self):
        # OpenAIProvider.__init__ falls back to os.getenv("OPENAI_API_KEY")
        # when the constructor arg is falsy - patch it out so a real key in
        # the developer's own shell environment (e.g. sourced from .env to
        # run a live classification script) can never mask this check.
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "OPENAI_MODEL": ""}):
            provider = OpenAIProvider(api_key="", model="test", client=object())
            with self.assertRaises(ProviderConfigurationError):
                asyncio.run(provider.generate(TextGenerationRequest(prompt="{}")))

    def test_generate_raises_when_model_not_configured(self):
        # api_key present but neither request.model nor OPENAI_MODEL/self._model
        # is set - distinct branch from test_requires_key_and_model above.
        with patch.dict(os.environ, {"OPENAI_MODEL": ""}):
            provider = OpenAIProvider(api_key="test-key", model="", client=object())
            with self.assertRaises(ProviderConfigurationError):
                asyncio.run(provider.generate(TextGenerationRequest(prompt="{}")))

    def test_generate_raises_invalid_response_on_empty_content(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""))])
        client, _ = client_for(response)
        provider = OpenAIProvider(api_key="test-key", model="test-model", client=client)
        with self.assertRaises(ProviderInvalidResponseError):
            asyncio.run(provider.generate(TextGenerationRequest(prompt="payload")))

    def test_returns_json_content_through_provider_router(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])
        client, calls = client_for(response)
        provider = OpenAIProvider(api_key="test-key", model="test-model", client=client)
        result = asyncio.run(ProviderRouter([provider], []).generate(TextGenerationRequest(prompt="payload")))
        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.model, "test-model")
        self.assertEqual(result.text, '{"ok": true}')
        self.assertEqual(calls.calls[0]["response_format"], {"type": "json_object"})

    def test_disables_sdk_retries_for_controlled_pilots(self):
        provider = OpenAIProvider(api_key="test-key", model="test-model")
        with patch("openai.AsyncOpenAI") as client_class:
            provider._create_client()
        self.assertEqual(client_class.call_args.kwargs["max_retries"], 0)

    def test_maps_timeout_rate_limit_and_unavailable_errors(self):
        cases = (
            (type("AuthenticationError", (Exception,), {})(), ProviderAuthenticationError),
            (type("PermissionDeniedError", (Exception,), {})(), ProviderAuthenticationError),
            (type("APITimeoutError", (Exception,), {})(), ProviderTimeoutError),
            (type("RateLimitError", (Exception,), {})(), ProviderRateLimitError),
            (RuntimeError(), ProviderUnavailableError),
        )
        for error, expected in cases:
            client, _ = client_for(error)
            provider = OpenAIProvider(api_key="test-key", model="test-model", client=client)
            with self.assertRaises(expected):
                asyncio.run(provider.generate(TextGenerationRequest(prompt="payload")))

    def test_generic_error_preserves_sanitized_diagnostics(self):
        api_key = "sk-test-api-key"
        database_url = "postgresql+psycopg://user:database-password@host/database"
        low_level_error = RuntimeError(
            f"TLS failure Authorization: Bearer {api_key} {database_url}"
        )
        error = type("APIConnectionError", (Exception,), {})("Connection error.")
        error.__cause__ = low_level_error
        client, _ = client_for(error)
        provider = OpenAIProvider(api_key=api_key, model="test-model", client=client)

        with self.assertRaises(ProviderUnavailableError) as context:
            asyncio.run(provider.generate(TextGenerationRequest(prompt="payload")))

        diagnostic = context.exception
        self.assertEqual(diagnostic.original_error_type, "APIConnectionError")
        self.assertEqual(diagnostic.diagnostic_message, "Connection error.")
        self.assertEqual(diagnostic.low_level_error_type, "RuntimeError")
        self.assertIn("TLS failure", diagnostic.low_level_diagnostic_message)
        self.assertNotIn(api_key, diagnostic.diagnostic_message)
        self.assertNotIn(database_url, diagnostic.diagnostic_message)
        self.assertNotIn(api_key, diagnostic.low_level_diagnostic_message)
        self.assertNotIn(database_url, diagnostic.low_level_diagnostic_message)
        self.assertNotIn("Bearer sk-test-api-key", diagnostic.low_level_diagnostic_message)

    def test_router_preserves_sanitized_unavailable_diagnostics(self):
        api_key = "sk-test-api-key"
        database_url = "postgresql+psycopg://user:database-password@host/database"
        low_level_error = RuntimeError(f"HTTP 400 {api_key} {database_url}")
        error = type("APIConnectionError", (Exception,), {})("Connection error.")
        error.__cause__ = low_level_error
        client, _ = client_for(error)
        provider = OpenAIProvider(api_key=api_key, model="test-model", client=client)

        with self.assertRaises(AllProvidersFailedError) as context:
            asyncio.run(ProviderRouter([provider], []).generate(TextGenerationRequest(prompt="payload")))

        attempt = context.exception.attempts[0]
        self.assertEqual(attempt.provider, "openai")
        self.assertEqual(attempt.error_type, "ProviderUnavailableError")
        self.assertEqual(attempt.original_error_type, "APIConnectionError")
        self.assertIsNotNone(attempt.diagnostic_message)
        self.assertEqual(attempt.low_level_error_type, "RuntimeError")
        self.assertIsNotNone(attempt.low_level_diagnostic_message)
        self.assertNotIn(api_key, attempt.low_level_diagnostic_message)
        self.assertNotIn(database_url, attempt.low_level_diagnostic_message)

    def test_structured_modification_adapter_rejects_invalid_provider_json(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))])
        client, _ = client_for(response)
        adapter = QuestionModificationAdapter(OpenAIProvider(api_key="test-key", model="test-model", client=client))
        with self.assertRaises(ValueError):
            asyncio.run(adapter.propose(question_data={"statement": "x", "options": ["A", "B"]}, modification_type=ModificationType.MAKE_EASIER, instruction="simplifique"))