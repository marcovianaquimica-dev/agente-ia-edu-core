"""Canonical JSON serialisation and hashing, shared by every engine whose output
must be repeatable.

Extracted verbatim from ``ClassificationProposalService._hash`` (PHASE 11). The
byte-level behaviour is frozen on purpose: ``pedagogical_classifications`` rows
already store ``input_hash``/``output_hash`` values produced by that expression,
and changing the serialisation would silently orphan every one of them.

``sort_keys`` makes the digest independent of key order; ``ensure_ascii=False``
keeps accented Portuguese literal instead of escaping it; the tight separators
remove insignificant whitespace.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Deterministic JSON text for ``value``."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    """SHA-256 of :func:`canonical_json` of ``value``."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


__all__ = ["canonical_hash", "canonical_json"]
