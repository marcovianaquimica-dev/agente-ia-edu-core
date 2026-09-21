"""OpenAI implementation of the provider-neutral text-generation contract."""

import base64
import math
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
from ..models import (
    EssayOcrToken,
    EssayPageTranscriptionRequest,
    EssayPageTranscriptionResult,
    TextGenerationRequest,
    TextGenerationResult,
)


class OpenAIProvider:
    provider = "openai"

    def __init__(self, *, api_key: str | None = None, model: str | None = None, vision_model: str | None = None, timeout_seconds: float | None = None, client=None):
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._model = model or os.getenv("OPENAI_MODEL")
        self._vision_model = vision_model or os.getenv("OPENAI_VISION_MODEL")
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

    async def transcribe_page(
        self, request: EssayPageTranscriptionRequest
    ) -> EssayPageTranscriptionResult:
        if not self._api_key:
            raise ProviderConfigurationError("OpenAI is not configured")
        if not self._vision_model:
            raise ProviderConfigurationError("OpenAI vision model is not configured")
        try:
            client = self._client or self._create_client()
            image_b64 = base64.b64encode(request.image_path.read_bytes()).decode("ascii")
            response = await client.chat.completions.create(
                model=self._vision_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Transcreva literalmente o texto manuscrito ou impresso na "
                            "imagem, palavra por palavra, na ordem em que aparece. Nao "
                            "corrija ortografia, gramatica ou concordancia - reproduza "
                            "exatamente o que esta escrito, mesmo que contenha erros. "
                            "Nao adicione nenhum texto que nao esteja na imagem."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{request.mime_type};base64,{image_b64}"
                                },
                            }
                        ],
                    },
                ],
                logprobs=True,
                top_logprobs=1,
                timeout=self._timeout_seconds,
            )
            choice = response.choices[0]
            content = choice.message.content
            if not content:
                raise ProviderInvalidResponseError("OpenAI returned an empty transcription")
            tokens = self._tokens_from_logprobs(content, choice.logprobs)
            return EssayPageTranscriptionResult(
                tokens=tokens, provider=self.provider, model=self._vision_model
            )
        except ProviderInvalidResponseError:
            raise
        except Exception as exc:
            raise self._map_error(exc) from exc

    def _tokens_from_logprobs(self, content: str, logprobs) -> tuple[EssayOcrToken, ...]:
        """Approximates per-token confidence from the model's own generation
        logprobs (confidence = exp(logprob)). This is NOT a real OCR
        confidence score - it is a documented approximation, the best signal
        available without a dedicated vision/OCR confidence API. When the
        response carries no logprob data at all, every token gets full
        confidence rather than an invented number, so a missing signal never
        masquerades as a low-confidence flag the student has to review."""
        if logprobs is None or not getattr(logprobs, "content", None):
            return (EssayOcrToken(text=content, confidence=1.0, start=0, end=len(content)),)
        tokens: list[EssayOcrToken] = []
        cursor = 0
        for entry in logprobs.content:
            piece = entry.token
            idx = content.find(piece, cursor)
            if idx == -1:
                continue
            start, end = idx, idx + len(piece)
            confidence = math.exp(entry.logprob)
            tokens.append(EssayOcrToken(text=piece, confidence=confidence, start=start, end=end))
            cursor = end
        return tuple(tokens)

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