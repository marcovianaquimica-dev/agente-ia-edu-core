"""Tests for the Anthropic Vision adapter used to OCR/transcribe authorial
document pages (e.g. a textbook page with no extractable PDF text layer)
into structured markdown - see authorial_material_parser.py for the target
convention. The real `anthropic` SDK client is ALWAYS mocked here; these
tests never call the live API (no key is configured in this environment)."""

import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agente_ia_edu.providers.adapters.anthropic import AnthropicProvider
from agente_ia_edu.providers.contracts import DocumentPageTranscriptionProvider
from agente_ia_edu.providers.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from agente_ia_edu.providers.factory import build_document_page_transcriber
from agente_ia_edu.providers.models import DocumentPageTranscriptionRequest


class StubMessages:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def client_for(result):
    messages = StubMessages(result)
    return SimpleNamespace(messages=messages), messages


def make_image(name: str) -> Path:
    path = Path(f"/tmp/{name}.png")
    path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return path


class AnthropicProviderContractTests(unittest.TestCase):
    def test_satisfies_document_page_transcription_protocol(self):
        provider = AnthropicProvider(api_key="key", vision_model="claude-x")
        self.assertIsInstance(provider, DocumentPageTranscriptionProvider)


class AnthropicProviderConfigurationTests(unittest.TestCase):
    def test_raises_when_api_key_missing(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            provider = AnthropicProvider(api_key="", vision_model="claude-x", client=object())
            request = DocumentPageTranscriptionRequest(
                image_path=make_image("anthropic_cfg1"), mime_type="image/png", prompt="transcreva"
            )
            with self.assertRaises(ProviderConfigurationError):
                asyncio.run(provider.transcribe_document_page(request))

    def test_raises_when_vision_model_missing_but_key_present(self):
        with patch.dict(os.environ, {"ANTHROPIC_VISION_MODEL": ""}):
            provider = AnthropicProvider(api_key="key-x", vision_model="", client=object())
            request = DocumentPageTranscriptionRequest(
                image_path=make_image("anthropic_cfg2"), mime_type="image/png", prompt="transcreva"
            )
            with self.assertRaises(ProviderConfigurationError):
                asyncio.run(provider.transcribe_document_page(request))


class AnthropicProviderTranscriptionTests(unittest.TestCase):
    def test_transcribes_page_successfully_joining_text_blocks(self):
        response = SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Capítulo 10 Caracterização dos elementos\n"),
                SimpleNamespace(type="text", text="## Número atômico\ntexto real da página."),
            ],
            stop_reason="end_turn",
        )
        client, calls = client_for(response)
        provider = AnthropicProvider(api_key="key", vision_model="claude-vision", client=client)
        image_path = make_image("anthropic_ok")
        request = DocumentPageTranscriptionRequest(
            image_path=image_path, mime_type="image/png", prompt="transcreva a página"
        )

        result = asyncio.run(provider.transcribe_document_page(request))

        self.assertEqual(result.provider, "anthropic")
        self.assertEqual(result.model, "claude-vision")
        self.assertIn("Capítulo 10", result.text)
        self.assertIn("## Número atômico", result.text)
        # the prompt travels through untouched - the adapter never rewrites it
        sent = calls.calls[0]
        self.assertEqual(sent["model"], "claude-vision")
        image_block = sent["messages"][0]["content"][0]
        text_block = sent["messages"][0]["content"][1]
        self.assertEqual(image_block["type"], "image")
        self.assertEqual(image_block["source"]["media_type"], "image/png")
        self.assertEqual(text_block, {"type": "text", "text": "transcreva a página"})

    def test_raises_when_stop_reason_is_refusal(self):
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="")],
            stop_reason="refusal",
        )
        client, _ = client_for(response)
        provider = AnthropicProvider(api_key="key", vision_model="claude-vision", client=client)
        request = DocumentPageTranscriptionRequest(
            image_path=make_image("anthropic_refusal_stop"), mime_type="image/png", prompt="p"
        )
        with self.assertRaises(ProviderInvalidResponseError):
            asyncio.run(provider.transcribe_document_page(request))

    def test_raises_when_response_has_no_text_blocks(self):
        response = SimpleNamespace(content=[], stop_reason="end_turn")
        client, _ = client_for(response)
        provider = AnthropicProvider(api_key="key", vision_model="claude-vision", client=client)
        request = DocumentPageTranscriptionRequest(
            image_path=make_image("anthropic_empty"), mime_type="image/png", prompt="p"
        )
        with self.assertRaises(ProviderInvalidResponseError):
            asyncio.run(provider.transcribe_document_page(request))

    def test_raises_when_truncated_by_max_tokens_rather_than_accepting_a_partial_page(self):
        # Confirmed concern for essay transcription (see OpenAIProvider) applies
        # here too: a page cut off mid-way is as unsafe to hand to the parser
        # as an outright refusal - never silently accepted as "done".
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Capítulo 10 texto parcial ap")],
            stop_reason="max_tokens",
        )
        client, _ = client_for(response)
        provider = AnthropicProvider(api_key="key", vision_model="claude-vision", client=client)
        request = DocumentPageTranscriptionRequest(
            image_path=make_image("anthropic_truncated"), mime_type="image/png", prompt="p"
        )
        with self.assertRaises(ProviderInvalidResponseError):
            asyncio.run(provider.transcribe_document_page(request))

    def test_raises_when_a_refusal_sentence_lands_in_text_instead_of_stop_reason(self):
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Desculpe, mas não posso transcrever esta página.")],
            stop_reason="end_turn",
        )
        client, _ = client_for(response)
        provider = AnthropicProvider(api_key="key", vision_model="claude-vision", client=client)
        request = DocumentPageTranscriptionRequest(
            image_path=make_image("anthropic_refusal_text"), mime_type="image/png", prompt="p"
        )
        with self.assertRaises(ProviderInvalidResponseError):
            asyncio.run(provider.transcribe_document_page(request))

    def test_maps_authentication_rate_limit_timeout_and_unavailable_errors(self):
        cases = (
            (type("AuthenticationError", (Exception,), {})(), ProviderAuthenticationError),
            (type("PermissionDeniedError", (Exception,), {})(), ProviderAuthenticationError),
            (type("APITimeoutError", (Exception,), {})(), ProviderTimeoutError),
            (type("RateLimitError", (Exception,), {})(), ProviderRateLimitError),
            (RuntimeError("boom"), ProviderUnavailableError),
        )
        for error, expected in cases:
            client, _ = client_for(error)
            provider = AnthropicProvider(api_key="key", vision_model="claude-vision", client=client)
            request = DocumentPageTranscriptionRequest(
                image_path=make_image("anthropic_err"), mime_type="image/png", prompt="p"
            )
            with self.assertRaises(expected):
                asyncio.run(provider.transcribe_document_page(request))

    def test_generic_error_preserves_sanitized_diagnostics_and_never_leaks_api_key(self):
        api_key = "sk-ant-test-secret-key"
        database_url = "postgresql+psycopg://user:database-password@host/database"
        low_level_error = RuntimeError(f"TLS failure Authorization: Bearer {api_key} {database_url}")
        error = type("APIConnectionError", (Exception,), {})("Connection error.")
        error.__cause__ = low_level_error
        client, _ = client_for(error)
        provider = AnthropicProvider(api_key=api_key, vision_model="claude-vision", client=client)
        request = DocumentPageTranscriptionRequest(
            image_path=make_image("anthropic_sanitize"), mime_type="image/png", prompt="p"
        )

        with self.assertRaises(ProviderUnavailableError) as ctx:
            asyncio.run(provider.transcribe_document_page(request))

        diagnostic = ctx.exception
        self.assertNotIn(api_key, str(diagnostic.diagnostic_message))
        self.assertNotIn(database_url, str(diagnostic.diagnostic_message))
        self.assertNotIn(api_key, str(diagnostic.low_level_diagnostic_message))
        self.assertNotIn(database_url, str(diagnostic.low_level_diagnostic_message))

    def test_uses_default_client_when_none_injected(self):
        provider = AnthropicProvider(api_key="key", vision_model="claude-vision")
        with patch("anthropic.AsyncAnthropic") as client_class:
            provider._create_client()
        self.assertEqual(client_class.call_args.kwargs["api_key"], "key")
        self.assertEqual(client_class.call_args.kwargs["max_retries"], 0)


class DocumentPageTranscriberFactoryTests(unittest.TestCase):
    def test_requires_configuration(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "ANTHROPIC_VISION_MODEL": ""}):
            with self.assertRaises(ProviderConfigurationError):
                build_document_page_transcriber("anthropic")

    def test_raises_when_vision_model_missing(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "ANTHROPIC_VISION_MODEL": ""}):
            with self.assertRaises(ProviderConfigurationError) as ctx:
                build_document_page_transcriber("anthropic")
        self.assertIn("ANTHROPIC_VISION_MODEL", str(ctx.exception))

    def test_succeeds_with_full_configuration(self):
        with patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "test-key", "ANTHROPIC_VISION_MODEL": "claude-vision"},
        ):
            provider = build_document_page_transcriber("anthropic")
        self.assertIsInstance(provider, AnthropicProvider)
        self.assertIsInstance(provider, DocumentPageTranscriptionProvider)

    def test_raises_for_unsupported_provider(self):
        with self.assertRaises(ProviderConfigurationError) as ctx:
            build_document_page_transcriber("provider_x")
        self.assertIn("provider_x", str(ctx.exception))
        self.assertIn("anthropic", str(ctx.exception))

    def test_never_leaks_api_key_value_in_configuration_error(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "ANTHROPIC_VISION_MODEL": ""}):
            with self.assertRaises(ProviderConfigurationError) as ctx:
                build_document_page_transcriber("anthropic")
        self.assertNotIn("sk-ant", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
