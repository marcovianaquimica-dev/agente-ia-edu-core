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


# Punctuation accepted in decoded Portuguese running text, in addition to any
# Unicode letter, any digit, and whitespace. Anything else surviving a decode
# is a sign the run was not actually encoded - see _decoded_looks_sane.
_ACCEPTABLE_PUNCTUATION = set(".,;:!?()-–—\"'/%")


def decode_subset(text: str) -> str:
    """Decode one string extracted from a subsetted-font run.

    Text that is already legible is returned unchanged. When in doubt, this
    function does NOT decode: leaving an encoded run undecoded is a loud
    failure a human will notice; decoding correct text is a silent one that
    would reach the rubric transcription unnoticed.
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
    decoded = "".join(out)

    if not _decoded_looks_sane(decoded):
        return text

    return decoded


def _looks_encoded(text: str) -> bool:
    """A run is encoded when most of its non-space characters sit in the shifted
    range and it contains no accented Portuguese letter.

    Several additional guards bias this heavily toward NOT decoding, because a
    correct-looking wrong guess is far worse than a loud, marked failure:

    - a visible ASCII digit is only produced by already-correct text (a
      plaintext digit maps to a control character under the shift, so it
      never survives in a genuinely encoded run);
    - a visible ASCII space is likewise only produced by already-correct
      text (the space glyph is remapped too in the encoded runs, so those
      runs read as words jammed together with no space at all);
    - short runs (under 8 characters) are genuinely ambiguous and not worth
      guessing at.
    """
    meaningful = [c for c in text if not c.isspace()]
    if not meaningful:
        return False
    if any(c in "áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ" for c in meaningful):
        return False
    if any(c.isdigit() for c in text):
        return False
    if " " in text:
        return False
    if len(text) < 8:
        return False
    in_range = sum(1 for c in meaningful if ord(c) in _ENCODED_RANGE or c in _LIGATURES)
    return in_range / len(meaningful) > 0.8


def _decoded_looks_sane(decoded: str) -> bool:
    """Reject a decode that introduces characters outside Portuguese running
    text - that is a signal the run was not actually encoded and the shift
    produced garbage instead. UNDECODED_MARKER is stripped first since its
    own characters are not in the acceptable set and are not the signal this
    guard is looking for."""
    stripped = decoded.replace(UNDECODED_MARKER, "")
    for char in stripped:
        if char.isspace() or char.isalnum() or char in _ACCEPTABLE_PUNCTUATION:
            continue
        return False
    return True


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
