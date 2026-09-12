"""System-owned, versioned classification prompt artifacts.

The classification service asks for a prompt by version through
:func:`get_classification_prompt` and never embeds prompt text. Each artifact is
provider-independent (no vendor name, model name, API key, SDK, or vendor
parameter) and immutable: a wording change is a new ``vN.py`` module plus a
registry entry here, never an edit to an existing one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import v1

DEFAULT_VERSION = "v1"

# artifact version id -> module exposing VERSION, RESPONSE_SCHEMA, build_prompt(...)
_ARTIFACTS = {v1.VERSION: v1}

# Free-form ``prompt_version`` labels used by callers before PHASE 11.18, each
# mapped to the artifact version whose text actually built their prompt. Kept for
# audit/traceability; the service pins the artifact explicitly (see DEFAULT_VERSION).
HISTORICAL_PROMPT_VERSION_TO_ARTIFACT: dict[str, str] = {
    label: v1.VERSION for label in v1.HISTORICAL_PROMPT_VERSIONS
}


@dataclass(frozen=True)
class ClassificationPrompt:
    """A resolved, provider-independent classification prompt artifact."""

    version: str
    response_schema: Mapping[str, Any]
    _build: Callable[..., str]

    def build(
        self,
        *,
        recovered_candidates: Sequence[Mapping[str, Any]],
        question_data: Mapping[str, Any],
    ) -> str:
        return self._build(
            recovered_candidates=recovered_candidates, question_data=question_data
        )


def available_versions() -> tuple[str, ...]:
    return tuple(sorted(_ARTIFACTS))


def artifact_version_for_prompt_version(prompt_version: str | None) -> str:
    """Explicit mapping from a caller-supplied ``prompt_version`` label to the
    artifact version that built (or would build) its prompt.

    ``None`` or an exact artifact id -> that artifact; a known historical label ->
    its recorded artifact; anything else -> :data:`DEFAULT_VERSION` (there is a
    single artifact and every real prompt so far was this text).
    """
    if prompt_version is None or prompt_version in _ARTIFACTS:
        return prompt_version or DEFAULT_VERSION
    return HISTORICAL_PROMPT_VERSION_TO_ARTIFACT.get(prompt_version, DEFAULT_VERSION)


def get_classification_prompt(version: str | None = None) -> ClassificationPrompt:
    """Return the classification prompt artifact for ``version``.

    ``version`` is an artifact version id (``"v1"``); ``None`` -> :data:`DEFAULT_VERSION`.
    An unknown artifact version raises :class:`ValueError`.
    """
    resolved = version or DEFAULT_VERSION
    artifact = _ARTIFACTS.get(resolved)
    if artifact is None:
        raise ValueError(
            f"Unknown classification prompt version {version!r}; "
            f"available: {list(available_versions())}"
        )
    return ClassificationPrompt(
        version=artifact.VERSION,
        response_schema=artifact.RESPONSE_SCHEMA,
        _build=artifact.build_prompt,
    )


__all__ = [
    "ClassificationPrompt",
    "DEFAULT_VERSION",
    "HISTORICAL_PROMPT_VERSION_TO_ARTIFACT",
    "artifact_version_for_prompt_version",
    "available_versions",
    "get_classification_prompt",
]
