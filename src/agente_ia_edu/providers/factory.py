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

from .contracts import (
    DocumentPageTranscriptionProvider,
    EmbeddingProvider,
    EssayImageCorrectionProvider,
    EssayTranscriptionProvider,
    TextGenerationProvider,
)
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
    # Embedding fica DE FORA aqui de proposito: um embedder so pode ser
    # construido sabendo o modelo do espaco, e espaco e dado de tabela, nao
    # variavel de ambiente. Ver ``build_embedding_provider`` no fim do modulo.
    return ProviderRouter(text_providers=[provider], embedding_providers=[])


def _build_openai_transcriber() -> EssayTranscriptionProvider:
    from .adapters.openai import OpenAIProvider

    api_key = os.getenv("OPENAI_API_KEY")
    vision_model = os.getenv("OPENAI_VISION_MODEL")
    if not api_key:
        raise ProviderConfigurationError(
            "AI_PROVIDER=openai but OPENAI_API_KEY is not configured"
        )
    if not vision_model:
        raise ProviderConfigurationError(
            "AI_PROVIDER=openai but OPENAI_VISION_MODEL is not configured"
        )
    return OpenAIProvider(api_key=api_key, vision_model=vision_model)


# name -> builder returning a single EssayTranscriptionProvider.
# Same extension story as _BUILDERS above: a new vendor is one adapter + one
# entry here, no call-site change (this is what "replaceable in the future"
# means in this codebase).
_TRANSCRIBER_BUILDERS: dict[str, Callable[[], EssayTranscriptionProvider]] = {
    "openai": _build_openai_transcriber,
}


def build_essay_transcriber(name: str | None = None) -> EssayTranscriptionProvider:
    """Build the configured essay-page transcription provider.

    ``name`` overrides ``AI_PROVIDER`` (tests / explicit callers). Raises
    :class:`ProviderConfigurationError` when the selected backend's required
    configuration is missing.
    """
    selected = (name or os.getenv("AI_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    builder = _TRANSCRIBER_BUILDERS.get(selected)
    if builder is None:
        raise ProviderConfigurationError(
            f"Unsupported AI_PROVIDER {selected!r} for essay transcription; "
            f"supported: {sorted(_TRANSCRIBER_BUILDERS)}"
        )
    return builder()


def _build_openai_image_corrector() -> EssayImageCorrectionProvider:
    from .adapters.openai import OpenAIProvider

    api_key = os.getenv("OPENAI_API_KEY")
    vision_model = os.getenv("OPENAI_VISION_MODEL")
    if not api_key:
        raise ProviderConfigurationError(
            "AI_PROVIDER=openai but OPENAI_API_KEY is not configured"
        )
    if not vision_model:
        raise ProviderConfigurationError(
            "AI_PROVIDER=openai but OPENAI_VISION_MODEL is not configured"
        )
    return OpenAIProvider(api_key=api_key, vision_model=vision_model)


# name -> builder returning a single EssayImageCorrectionProvider. Same
# extension story as _BUILDERS/_TRANSCRIBER_BUILDERS above.
_IMAGE_CORRECTOR_BUILDERS: dict[str, Callable[[], EssayImageCorrectionProvider]] = {
    "openai": _build_openai_image_corrector,
}


def build_essay_image_corrector(name: str | None = None) -> EssayImageCorrectionProvider:
    """Build the configured image-based essay-correction provider (IMAGE_REGION
    submissions - no canonical text, correction runs directly off the page
    images). Raises :class:`ProviderConfigurationError` when the selected
    backend's required configuration is missing."""
    selected = (name or os.getenv("AI_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    builder = _IMAGE_CORRECTOR_BUILDERS.get(selected)
    if builder is None:
        raise ProviderConfigurationError(
            f"Unsupported AI_PROVIDER {selected!r} for essay image correction; "
            f"supported: {sorted(_IMAGE_CORRECTOR_BUILDERS)}"
        )
    return builder()


def _build_anthropic_document_transcriber() -> DocumentPageTranscriptionProvider:
    # Imported lazily - same story as the openai builders above: the vendor
    # SDK is only pulled in when this backend is actually selected.
    from .adapters.anthropic import AnthropicProvider

    api_key = os.getenv("ANTHROPIC_API_KEY")
    vision_model = os.getenv("ANTHROPIC_VISION_MODEL")
    if not api_key:
        raise ProviderConfigurationError(
            "AI_PROVIDER=anthropic but ANTHROPIC_API_KEY is not configured"
        )
    if not vision_model:
        raise ProviderConfigurationError(
            "AI_PROVIDER=anthropic but ANTHROPIC_VISION_MODEL is not configured"
        )
    return AnthropicProvider(api_key=api_key, vision_model=vision_model)


# name -> builder returning a single DocumentPageTranscriptionProvider. Used
# to OCR authorial source material with no extractable PDF text layer (e.g.
# a pure-image textbook e-book) into markdown for the EXISTING, unmodified
# authorial_material_ingestion.py pipeline - see
# scripts/ocr_pilot_usberco_cap10.py. Only "anthropic" exists today; the
# DEFAULT_PROVIDER ("openai") has no entry here yet, so an explicit
# name="anthropic" (or AI_PROVIDER=anthropic) is required.
_DOCUMENT_TRANSCRIBER_BUILDERS: dict[str, Callable[[], DocumentPageTranscriptionProvider]] = {
    "anthropic": _build_anthropic_document_transcriber,
}


def build_document_page_transcriber(name: str | None = None) -> DocumentPageTranscriptionProvider:
    """Build the configured authorial-document-page transcription provider.

    ``name`` overrides ``AI_PROVIDER`` (tests / explicit callers). Raises
    :class:`ProviderConfigurationError` - never leaking secret values - when
    the selected backend's required configuration is missing or unsupported.
    """
    selected = (name or os.getenv("AI_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    builder = _DOCUMENT_TRANSCRIBER_BUILDERS.get(selected)
    if builder is None:
        raise ProviderConfigurationError(
            f"Unsupported AI_PROVIDER {selected!r} for document page transcription; "
            f"supported: {sorted(_DOCUMENT_TRANSCRIBER_BUILDERS)}"
        )
    return builder()


def _build_openai_embedder(model: str) -> EmbeddingProvider:
    from .adapters.openai import OpenAIProvider

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ProviderConfigurationError(
            "AI_PROVIDER=openai but OPENAI_API_KEY is not configured"
        )
    return OpenAIProvider(api_key=api_key, embedding_model=model)


# name -> builder returning a single EmbeddingProvider. Same extension story
# as the builders above.
_EMBEDDING_BUILDERS: dict[str, Callable[[str], EmbeddingProvider]] = {
    "openai": _build_openai_embedder,
}


def supported_embedding_providers() -> tuple[str, ...]:
    return tuple(sorted(_EMBEDDING_BUILDERS))


def build_embedding_provider(name: str, *, model: str) -> ProviderRouter:
    """Build the embedding provider for ONE embedding space.

    Unlike every other builder here, ``name`` and ``model`` are REQUIRED and
    are never read from the environment. They are DATA: they come from the
    ``knowledge_embedding_spaces`` row the caller is backfilling. That is what
    lets the Knowledge Engine reach a real vendor without ever naming one - it
    forwards two strings it read from its own table.

    Wrapped in a ``ProviderRouter`` so fallback across embedding backends is a
    list change here, not a call-site change. One caveat that is NOT generic:
    the router falls back on transport failures, and a different backend would
    produce vectors in a DIFFERENT space. A real fallback list may therefore
    only hold adapters serving the same ``(provider, model, dimensions)``
    identity - otherwise the corpus silently mixes geometries.
    """
    selected = (name or "").strip().lower()
    builder = _EMBEDDING_BUILDERS.get(selected)
    if builder is None:
        raise ProviderConfigurationError(
            f"Unsupported embedding provider {selected!r}; "
            f"supported: {list(supported_embedding_providers())}"
        )
    if not model:
        raise ProviderConfigurationError(
            "An embedding space must declare its model; none was given"
        )
    return ProviderRouter(text_providers=[], embedding_providers=[builder(model)])
