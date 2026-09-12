"""PHASE 11.17 - focused tests for the configuration-driven provider factory."""

import unittest
from unittest.mock import patch

from agente_ia_edu.providers.adapters.openai import OpenAIProvider
from agente_ia_edu.providers.contracts import TextGenerationProvider
from agente_ia_edu.providers.errors import ProviderConfigurationError, ProviderError
from agente_ia_edu.providers.factory import (
    build_text_provider,
    supported_providers,
)
from agente_ia_edu.providers.router import ProviderRouter


class ProviderFactoryTests(unittest.TestCase):
    def test_ai_provider_openai_builds_openai_through_router(self):
        with patch.dict("os.environ", {"AI_PROVIDER": "openai",
                                       "OPENAI_API_KEY": "test-key",
                                       "OPENAI_MODEL": "test-model"}, clear=False):
            provider = build_text_provider()
        self.assertIsInstance(provider, ProviderRouter)
        text_providers = provider._text_providers  # noqa: SLF001 - white-box wiring check
        self.assertEqual(len(text_providers), 1)
        self.assertIsInstance(text_providers[0], OpenAIProvider)

    def test_default_when_ai_provider_unset_is_openai(self):
        env = {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "test-model"}
        with patch.dict("os.environ", env, clear=False):
            import os
            os.environ.pop("AI_PROVIDER", None)
            provider = build_text_provider()
        self.assertIsInstance(provider, ProviderRouter)
        self.assertIsInstance(provider._text_providers[0], OpenAIProvider)  # noqa: SLF001

    def test_missing_openai_api_key_raises_safe_configuration_error(self):
        with patch.dict("os.environ", {"AI_PROVIDER": "openai", "OPENAI_MODEL": "m"}, clear=False):
            import os
            os.environ.pop("OPENAI_API_KEY", None)
            with self.assertRaises(ProviderConfigurationError) as ctx:
                build_text_provider()
        message = str(ctx.exception)
        self.assertIn("OPENAI_API_KEY", message)
        self.assertNotIn("test-key", message)
        self.assertTrue(message.startswith("AI_PROVIDER=openai"))

    def test_missing_openai_model_raises_safe_configuration_error(self):
        with patch.dict("os.environ", {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"}, clear=False):
            import os
            os.environ.pop("OPENAI_MODEL", None)
            with self.assertRaises(ProviderConfigurationError) as ctx:
                build_text_provider()
        self.assertIn("OPENAI_MODEL", str(ctx.exception))
        self.assertNotIn("test-key", str(ctx.exception))

    def test_unsupported_provider_raises_clear_error(self):
        with self.assertRaises(ProviderConfigurationError) as ctx:
            build_text_provider(name="provider_x")
        message = str(ctx.exception)
        self.assertIn("Unsupported AI_PROVIDER", message)
        self.assertIn("provider_x", message)
        self.assertIn("openai", message)  # supported list is shown
        self.assertIsInstance(ctx.exception, ProviderError)

    def test_unsupported_provider_via_env_raises(self):
        with patch.dict("os.environ", {"AI_PROVIDER": "nope"}, clear=False):
            with self.assertRaises(ProviderConfigurationError):
                build_text_provider()

    def test_result_satisfies_text_generation_provider_protocol(self):
        with patch.dict("os.environ", {"AI_PROVIDER": "openai",
                                       "OPENAI_API_KEY": "k", "OPENAI_MODEL": "m"}, clear=False):
            provider = build_text_provider()
        self.assertIsInstance(provider, TextGenerationProvider)  # runtime_checkable Protocol
        self.assertTrue(callable(getattr(provider, "generate", None)))

    def test_explicit_name_overrides_env(self):
        with patch.dict("os.environ", {"AI_PROVIDER": "nope",
                                       "OPENAI_API_KEY": "k", "OPENAI_MODEL": "m"}, clear=False):
            provider = build_text_provider(name="openai")
        self.assertIsInstance(provider, ProviderRouter)

    def test_supported_providers_is_sorted_and_contains_openai(self):
        names = supported_providers()
        self.assertIn("openai", names)
        self.assertEqual(list(names), sorted(names))


if __name__ == "__main__":
    unittest.main()
