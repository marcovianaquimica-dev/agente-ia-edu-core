"""One-shot tool: decode the INEP cartilha PDF and emit a YAML draft.

Why this is a tool and not a runtime parser
-------------------------------------------
The cartilha embeds subsetted fonts with no ToUnicode CMap, so ``pypdf`` returns
glyph codes rather than text: "Demonstra domínio insuficiente" arrives as
``'HPRQVWUDGRPtQLRLQVX¿FLHQWH``. The mapping is recoverable - ASCII letters are
shifted by a constant 29 code points, and a handful of glyphs are ligatures -
but running a decoder like this in production, over a third party's PDF, is
fragile and unnecessary. The rubric is seeded once from a reviewed YAML file.

Everything this tool cannot decode is replaced with UNDECODED_MARKER so the
human reviewing the YAML against the PDF knows exactly where to look.

Usage:
    python -m tools.extract_cartilha_enem <cartilha.pdf> > draft.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

_SHIFT = 29
UNDECODED_MARKER = "⟨?⟩"

# Glyphs the subset maps to multi-character ligatures rather than to a shifted
# code point. Extend as the review surfaces more.
_LIGATURES = {
    "¿": "fi",  # ¿ -> fi
    "¾": "fl",  # ¾ -> fl
}

# A run is treated as encoded when it is made of printable ASCII that decodes
# into letters. Text that is already correct contains accented Portuguese and
# lowercase runs that would decode into control characters, so it is left alone.
_ENCODED_RANGE = range(0x21, 0x60)


def decode_subset(text: str) -> str:
    """Decode one string extracted from a subsetted-font run.

    Text that is already legible is returned unchanged.
    """
    if not _looks_encoded(text):
        return text

    out: list[str] = []
    for char in text:
        if char in _LIGATURES:
            out.append(_LIGATURES[char])
        elif ord(char) in _ENCODED_RANGE:
            out.append(chr(ord(char) + _SHIFT))
        elif char.isspace():
            out.append(char)
        else:
            out.append(UNDECODED_MARKER)
    return "".join(out)


def _looks_encoded(text: str) -> bool:
    """A run is encoded when most of its non-space characters sit in the shifted
    range and it contains no accented Portuguese letter."""
    meaningful = [c for c in text if not c.isspace()]
    if not meaningful:
        return False
    if any(c in "áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ" for c in meaningful):
        return False
    in_range = sum(1 for c in meaningful if ord(c) in _ENCODED_RANGE or c in _LIGATURES)
    return in_range / len(meaningful) > 0.8


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    from pypdf import PdfReader

    reader = PdfReader(Path(argv[1]))
    for number, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        print(f"# --- page {number} ---")
        for line in raw.splitlines():
            print(f"# {decode_subset(line)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
