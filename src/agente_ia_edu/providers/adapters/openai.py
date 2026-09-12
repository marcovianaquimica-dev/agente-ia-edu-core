"""OpenAI implementation of the provider-neutral text-generation contract."""

import os
import re

from ..errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from ..models import TextGenerationRequest, TextGenerationResult


class OpenAIProvider:
    provider = "openai"

    def __init__(self, *, api_key: str | None = None, model: str | None = None, timeout_seconds: float | None = None, client=None):
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._model = model or os.getenv("OPENAI_MODEL")
        self._timeout_seconds = timeout_seconds or float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
        self._client = client

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        if not self._api_key:
            raise ProviderConfigurationError("OpenAI is not configured")
        model = request.model or self._model
        if not model:
            raise ProviderConfigurationError("OpenAI model is not configured")
        try:
            client = self._client or self._create_client()
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Return only valid JSON. Follow the user prompt exactly."},
                    {"role": "user", "content": request.prompt},
                ],
                response_format={"type": "json_object"},
                timeout=self._timeout_seconds,
            )
            content = response.choices[0].message.content
            if not content:
                raise ProviderInvalidResponseError("OpenAI returned an empty response")
            return TextGenerationResult(text=content, provider=self.provider, model=model)
        except ProviderInvalidResponseError:
            raise
        except Exception as exc:
            raise self._map_error(exc) from exc

    def _create_client(self):
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            api_key=self._api_key,
            timeout=self._timeout_seconds,
            max_retries=0,
        )

    def _map_error(self, error: Exception):
        name = type(error).__name__
        if name in {"AuthenticationError", "PermissionDeniedError"}:
            return ProviderAuthenticationError("OpenAI authentication failed")
        if name == "RateLimitError":
            return ProviderRateLimitError("OpenAI rate limit reached")
        if name in {"APITimeoutError", "TimeoutError"}:
            return ProviderTimeoutError("OpenAI request timed out")
        low_level_error = error.__cause__ or error.__context__
        unavailable = ProviderUnavailableError("OpenAI request failed")
        unavailable.original_error_type = name
        unavailable.diagnostic_message = self._sanitize_error_message(str(error))
        unavailable.low_level_error_type = (
            type(low_level_error).__name__ if low_level_error is not None else None
        )
        unavailable.low_level_diagnostic_message = (
            self._sanitize_error_message(str(low_level_error))
            if low_level_error is not None
            else None
        )
        return unavailable

    def _sanitize_error_message(self, message: str) -> str:
        for secret in (self._api_key, os.getenv("DATABASE_URL")):
            if secret:
                message = message.replace(secret, "[REDACTED]")
            message = re.sub(
                r"(?:https?|postgres(?:ql)?)(?:\+[^:]+)?://[^\s/@:]+(?::[^\s/@]*)?@[^\s]+",
                "[REDACTED]",
                message,
                flags=re.I,
            )
        message = re.sub(r"postgres(?:ql)?(?:\+[^:]+)?://[^\s]+", "[REDACTED]", message, flags=re.I)
        message = re.sub(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+", "[REDACTED]", message)
        message = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+", r"\1[REDACTED]", message)
        message = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", message)
        return message