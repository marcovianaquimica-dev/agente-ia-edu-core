"""Text normalisation and the repeatability key (spec v1.0 §19).

Offsets always index the NORMALISED text. Normalisation therefore happens once,
when the essay version is stored (R2), and the normalised string is the canonical
text the engine reads and the annotations anchor into. Normalising again later,
after offsets exist, would silently move every anchor - so this function is
idempotent and R2 applies it exactly once.

What normalisation does and does not do: it unifies line endings, applies Unicode
NFC so that "ção" typed two different ways hashes the same, strips trailing
whitespace per line, and trims the edges. It does NOT collapse internal runs of
spaces or touch punctuation, because both would change what the student wrote.
"""

from __future__ import annotations

import unicodedata

from agente_ia_edu.services.canonical_hash import canonical_hash


def normalize_essay_text(text: str) -> str:
    """Canonical form of an essay text. Idempotent."""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    composed = unicodedata.normalize("NFC", unified)
    lines = [line.rstrip() for line in composed.split("\n")]
    return "\n".join(lines).strip()


def essay_text_hash(text: str) -> str:
    """SHA-256 of the canonical-JSON encoding of the normalised essay text.

    Not a hash of the raw normalised string: it goes through
    :func:`canonical_hash`/:func:`canonical_json`, the same serialisation every
    other hash in the system uses, so every stored hash - this one, an engine
    output hash, a classification hash - is produced the same way and none of
    them can be recomputed with a bare ``hashlib.sha256(text.encode())``.
    """
    return canonical_hash(normalize_essay_text(text))


def correction_key(
    *,
    normalized_text_hash: str,
    essay_prompt_id: str,
    rubric_version: str,
    model_version: str,
    prompt_version: str,
    engine_version: str,
) -> str:
    """Identity of a correction under a fixed set of versions.

    ``essay_prompt_id`` is the PROPOSAL the student answered (EssayPrompt);
    ``prompt_version`` is the version of the prompt sent to the model. The spec
    uses "proposta" and "prompt" interchangeably in §19; these two names do not.

    Same text, same proposal and same four versions must produce the same key, so
    R3 can recognise a correction it has already made instead of paying for it
    twice - and so a historical correction is never silently recomputed.
    """
    return canonical_hash(
        {
            "normalized_text_hash": normalized_text_hash,
            "essay_prompt_id": essay_prompt_id,
            "rubric_version": rubric_version,
            "model_version": model_version,
            "prompt_version": prompt_version,
            "engine_version": engine_version,
        }
    )


__all__ = ["correction_key", "essay_text_hash", "normalize_essay_text"]
