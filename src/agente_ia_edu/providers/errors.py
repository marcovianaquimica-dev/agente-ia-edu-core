from dataclasses import dataclass
from typing import Sequence


class ProviderError(Exception):
    """Base exception for provider-neutral failures."""


class ProviderUnavailableError(ProviderError):
    """The provider is temporarily unavailable."""


class ProviderRateLimitError(ProviderError):
    """The provider rate limit was reached."""


class ProviderTimeoutError(ProviderError):
    """The provider request timed out."""


class ProviderInvalidResponseError(ProviderError):
    """The provider returned an invalid response."""


class ProviderAuthenticationError(ProviderError):
    """The provider rejected authentication or credentials."""


class ProviderConfigurationError(ProviderError):
    """The provider capability is not configured."""


@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    error_type: str


class AllProvidersFailedError(ProviderError):
    """Every configured provider failed with a fallback-eligible error."""

    def __init__(self, attempts: Sequence[ProviderAttempt]) -> None:
        self.attempts = tuple(attempts)
        summary = ", ".join(
            f"{attempt.provider}: {attempt.error_type}" for attempt in self.attempts
        )
        super().__init__(f"All providers failed: {summary}")
