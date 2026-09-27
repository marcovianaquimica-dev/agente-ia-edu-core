"""Anthropic Vision implementation of the provider-neutral document-page
transcription contract (``DocumentPageTranscriptionProvider`` in
contracts.py).

This adapter is ONLY used for OCR-ing pages of authorial teaching material
that have no extractable PDF text layer (e.g. a pure-image textbook e-book -
see scripts/ocr_pilot_usberco_cap10.py). It never touches, imports, or is
imported by anything essay/redação-specific - EssayTranscriptionProvider and
EssayImageCorrectionProvider (openai.py) are a SEPARATE contract with a
SEPARATE, fixed system prompt baked into the adapter, because every essay
page is transcribed the same literal way. Document-page transcription
instead follows whatever markdown convention the caller's downstream parser
expects (authorial_material_parser.py's heading/exercise markers), so the
FULL prompt travels with the request (see DocumentPageTranscriptionRequest's
docstring) instead of living here.
"""

import base64
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
from ..models import DocumentPageTranscriptionRequest, DocumentPageTranscriptionResult

# A single page of dense textbook prose/exercises, rendered at ~150-200dpi and
# transcribed into markdown, comfortably fits well under this - it exists as
# a safety ceiling, not a target. When a page genuinely needs more, the
# response arrives with stop_reason="max_tokens" and is REJECTED below
# (the `stop_reason == "max_tokens"` branch in transcribe_document_page)
# rather than silently accepted as a complete page.
_DEFAULT_MAX_TOKENS = 4096

# Same real-world concern already confirmed for OpenAIProvider.transcribe_page
# (see openai.py's own _REFUSAL_PREFIXES/_REFUSAL_SUBSTRINGS docstring): a
# vision model can decline a page - or quietly give up partway through and
# append its own apology inside otherwise-real content - instead of using a
# clean, structured refusal signal. Anthropic's Messages API exposes a
# dedicated `stop_reason == "refusal"` for the first case (checked first,
# below); this text-level net catches the second case, and a refusal that
# lands as the ENTIRE response with no structured signal at all.
_REFUSAL_PREFIXES = (
    "i'm sorry", "i am sorry", "sorry, but", "i can't", "i cannot",
    "desculpe", "sinto muito", "não posso", "nao posso",
    "i'm unable", "i am unable",
)
_REFUSAL_SUBSTRINGS = (
    "não posso transcrever", "nao posso transcrever",
    "não posso continuar", "nao posso continuar",
    "não posso ler o resto", "nao posso ler o resto",
    "continua com o resto do texto",
    "i cannot transcribe the rest", "i can't transcribe the rest",
    "i cannot continue", "i can't continue",
)


def _looks_like_a_refusal(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    if normalized.startswith(_REFUSAL_PREFIXES):
        return True
    return any(phrase in normalized for phrase in _REFUSAL_SUBSTRINGS)


class AnthropicProvider:
    provider = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        vision_model: str | None = None,
        timeout_seconds: float | None = None,
        max_tokens: int | None = None,
        client=None,
    ) -> None:
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._vision_model = vision_model or os.getenv("ANTHROPIC_VISION_MODEL")
        self._timeout_seconds = timeout_seconds or float(os.getenv("ANTHROPIC_TIMEOUT_SECONDS", "60"))
        self._max_tokens = max_tokens or _DEFAULT_MAX_TOKENS
        self._client = client

    async def transcribe_document_page(
        self, request: DocumentPageTranscriptionRequest
    ) -> DocumentPageTranscriptionResult:
        if not self._api_key:
            raise ProviderConfigurationError("Anthropic is not configured")
        model = request.model or self._vision_model
        if not model:
            raise ProviderConfigurationError("Anthropic vision model is not configured")
        try:
            client = self._client or self._create_client()
            image_b64 = base64.b64encode(request.image_path.read_bytes()).decode("ascii")
            response = await client.messages.create(
                model=model,
                max_tokens=self._max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": request.mime_type,
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": request.prompt},
                        ],
                    }
                ],
                timeout=self._timeout_seconds,
            )
            stop_reason = getattr(response, "stop_reason", None)
            if stop_reason == "refusal":
                raise ProviderInvalidResponseError(
                    "Anthropic refused to transcribe the page (stop_reason=refusal)"
                )
            text = "".join(
                block.text for block in (response.content or []) if getattr(block, "type", None) == "text"
            )
            if not text.strip():
                raise ProviderInvalidResponseError("Anthropic returned an empty transcription")
            if stop_reason == "max_tokens":
                # A page cut off mid-transcription is as unsafe to hand to the
                # downstream markdown parser as an outright refusal - never
                # accepted as a complete page (see module docstring).
                raise ProviderInvalidResponseError(
                    "Anthropic truncated the transcription before finishing the page "
                    "(stop_reason=max_tokens) - discarding the partial result"
                )
            if _looks_like_a_refusal(text):
                raise ProviderInvalidResponseError(
                    f"Anthropic refused to transcribe the page: {text.strip()[:200]}"
                )
            return DocumentPageTranscriptionResult(text=text, provider=self.provider, model=model)
        except ProviderInvalidResponseError:
            raise
        except Exception as exc:
            raise self._map_error(exc) from exc

    def _create_client(self):
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(
            api_key=self._api_key,
            timeout=self._timeout_seconds,
            max_retries=0,
        )

    def _map_error(self, error: Exception):
        name = type(error).__name__
        if name in {"AuthenticationError", "PermissionDeniedError"}:
            return ProviderAuthenticationError("Anthropic authentication failed")
        if name == "RateLimitError":
            return ProviderRateLimitError("Anthropic rate limit reached")
        if name in {"APITimeoutError", "TimeoutError"}:
            return ProviderTimeoutError("Anthropic request timed out")
        low_level_error = error.__cause__ or error.__context__
        unavailable = ProviderUnavailableError("Anthropic request failed")
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
        message = re.sub(r"\bsk-ant-[A-Za-z0-9_-]+", "[REDACTED]", message)
        message = re.sub(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+", "[REDACTED]", message)
        message = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+", r"\1[REDACTED]", message)
        message = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", message)
        return message
