"""System-owned, versioned essay-correction prompt artifacts.

Mirrors ``classification_prompts``: the engine asks for a prompt by version and
never embeds prompt text; each artifact is provider-independent and immutable, so
a wording change is a new ``vN.py`` module plus a registry entry, never an edit.

The registry is EMPTY in R1 by design. R1 owns the versioning mechanism; the
correction prompt itself is written in R3, alongside the engine that uses it.
Asking for a version that is not registered raises - there is deliberately no
silent default, because a correction whose prompt cannot be identified cannot be
audited.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from . import v1

# artifact version id -> module exposing VERSION, RESPONSE_SCHEMA, build_prompt(...)
_ARTIFACTS: dict[str, Any] = {v1.VERSION: v1}


@dataclass(frozen=True)
class EssayPrompt:
    """A resolved, provider-independent essay-correction prompt artifact."""

    version: str
    response_schema: Mapping[str, Any]
    _build: Callable[..., str]

    def build(self, **kwargs: Any) -> str:
        return self._build(**kwargs)


def available_versions() -> tuple[str, ...]:
    return tuple(sorted(_ARTIFACTS))


def get_essay_prompt(version: str) -> EssayPrompt:
    """Return the artifact for ``version``, or raise :class:`ValueError`."""
    artifact = _ARTIFACTS.get(version)
    if artifact is None:
        raise ValueError(
            f"Unknown essay prompt version {version!r}; "
            f"available: {list(available_versions())}"
        )
    return EssayPrompt(
        version=artifact.VERSION,
        response_schema=artifact.RESPONSE_SCHEMA,
        _build=artifact.build_prompt,
    )


__all__ = ["EssayPrompt", "available_versions", "get_essay_prompt"]
