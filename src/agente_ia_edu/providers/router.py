from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from .contracts import EmbeddingProvider, TextGenerationProvider
from .errors import (
    AllProvidersFailedError,
    ProviderAttempt,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from .models import (
    EmbeddingRequest,
    EmbeddingResult,
    TextGenerationRequest,
    TextGenerationResult,
)


Result = TypeVar("Result")
Provider = TypeVar("Provider")
_FALLBACK_ERRORS = (
    ProviderUnavailableError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)


class ProviderRouter(TextGenerationProvider, EmbeddingProvider):
    def __init__(
        self,
        text_providers: Sequence[TextGenerationProvider],
        embedding_providers: Sequence[EmbeddingProvider],
    ) -> None:
        self._text_providers = tuple(text_providers)
        self._embedding_providers = tuple(embedding_providers)

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        if not self._text_providers:
            raise ProviderConfigurationError("No text generation providers configured")
        return await self._run(
            self._text_providers,
            lambda provider: provider.generate(request),
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        if not self._embedding_providers:
            raise ProviderConfigurationError("No embedding providers configured")
        return await self._run(
            self._embedding_providers,
            lambda provider: provider.embed(request),
        )

    async def _run(
        self,
        providers: Sequence[Provider],
        invoke: Callable[[Provider], Awaitable[Result]],
    ) -> Result:
        attempts: list[ProviderAttempt] = []
        for provider in providers:
            try:
                return await invoke(provider)
            except _FALLBACK_ERRORS as error:
                attempts.append(
                    ProviderAttempt(
                        provider=self._provider_name(provider),
                        error_type=type(error).__name__,
                    )
                )
        raise AllProvidersFailedError(attempts)

    @staticmethod
    def _provider_name(provider: object) -> str:
        return str(getattr(provider, "provider", type(provider).__name__))
