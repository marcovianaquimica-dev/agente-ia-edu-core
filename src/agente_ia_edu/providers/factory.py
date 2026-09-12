"""Configuration-driven construction of a provider-neutral text generator.

Application and domain code call :func:`build_text_provider` and receive a
:class:`~agente_ia_edu.providers.contracts.TextGenerationProvider` without ever
learning which vendor (or local model) backs it. Selection is driven by the
``AI_PROVIDER`` environment variable (default ``openai``). Secrets stay in the
environment - this module never accepts, stores, or echoes a key value.

Adding a new backend later (``AI_PROVIDER=provider_b`` / ``AI_PROVIDER=local``)
is a one-entry change to ``_BUILDERS`` here; no classification, taxonomy, prompt
or decision-core code changes.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from .contracts import TextGenerationProvider
from .errors import ProviderConfigurationError
from .router import ProviderRouter

DEFAULT_PROVIDER = "openai"


def _build_openai() -> TextGenerationProvider:
    # Imported lazily so vendor SDKs are only pulled in when that backend is selected.
    from .adapters.openai import OpenAIProvider

    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL")
    if not api_key:
        raise ProviderConfigurationError(
            "AI_PROVIDER=openai but OPENAI_API_KEY is not configured"
        )
    if not model:
        raise ProviderConfigurationError(
            "AI_PROVIDER=openai but OPENAI_MODEL is not configured"
        )
    return OpenAIProvider(api_key=api_key, model=model)


# name -> builder returning a single TextGenerationProvider.
# Future: "provider_b": _build_provider_b, "local": _build_local
_BUILDERS: dict[str, Callable[[], TextGenerationProvider]] = {
    "openai": _build_openai,
}


def supported_providers() -> tuple[str, ...]:
    """Deterministically ordered names accepted by :func:`build_text_provider`."""
    return tuple(sorted(_BUILDERS))


def build_text_provider(name: str | None = None) -> ProviderRouter:
    """Build the configured text-generation provider, wrapped in a ``ProviderRouter``.

    ``name`` overrides ``AI_PROVIDER`` (tests / explicit callers). The result is a
    :class:`ProviderRouter` so multi-provider fallback stays available without any
    call-site change when more backends are added.

    Raises :class:`ProviderConfigurationError` - never leaking secret values - when
    ``AI_PROVIDER`` is unsupported or the selected backend's required configuration
    is missing.
    """
    selected = (name or os.getenv("AI_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    builder = _BUILDERS.get(selected)
    if builder is None:
        raise ProviderConfigurationError(
            f"Unsupported AI_PROVIDER {selected!r}; supported: {list(supported_providers())}"
        )
    provider = builder()
    return ProviderRouter(text_providers=[provider], embedding_providers=[])
