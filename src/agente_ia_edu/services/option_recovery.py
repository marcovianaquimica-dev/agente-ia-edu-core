"""Deterministic option-level recovery for the RECOVERED ENEM booklets (PHASE 10.8).

PHASE 10.7 proved that feeding MuPDF's linear ``get_text("text")`` into the
unchanged parser recovers full question *detection* for ENEM 2024/2025 but
corrupts the *alternatives*:

  * 2025 D2 Q145 - stacked fraction "30 / 90" flattened to "30 90"
  * 2025 D2 Q154 - option E picked up the page barcode + micro-watermark
    ("E) 1 256 *020125AM22* ENEM2025ENEM2025...")

This module rebuilds the text layer from MuPDF's *positional* output
(``get_text("dict")`` + ``get_drawings()``) instead:

  1. classify every text span: content | running_header | page_number |
     barcode | watermark  (by font name + geometry + literal pattern - never by
     "looks wrong");
  2. drop the non-content spans;
  3. re-thread lines in column-then-reading order (2025 D2 day-2 is 2-column);
  4. inside an option, re-attach a stacked denominator when a horizontal
     hairline (the fraction bar) sits between numerator and denominator and the
     three are x-aligned -> "num/den";  no bar => left as-is and flagged.

Output is a cleaned ``page_texts: list[str]`` in the SAME shape the existing
``PdfParser.parse_file(filepath, page_texts=...)`` seam already accepts (options
emitted as ``LETTER<2 spaces>text`` so the unchanged ``OPTION_PATTERN`` picks
them up). No pipeline code changes. Nothing here writes a database, calls a
network/LLM, or infers missing content: a value that cannot be recovered from
structure is reported ``OPTION_RECOVERY_PENDING``, never guessed.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

# --- artifact signatures (deterministic; font + pattern, not heuristics) ----- #

# Code 39 barcode font used for the sheet id "*020125AM22*".
_BARCODE_FONTS = {"C39HrP36DlTt"}
_BARCODE_RE = re.compile(r"\*[0-9A-Z]{6,}\*")

# Micro "ENEM2025ENEM2025..." security watermark: Arial-Bold at ~1.5pt.
_WATERMARK_RE = re.compile(r"(?:ENE[MN]\s?\d{4}){2,}")
_WATERMARK_MAX_SIZE = 3.0

# Running header. Separator is "|" (2025) or "•" / "·" (2024):
#   "MATEMÁTICA E SUAS TECNOLOGIAS | 2º DIA | CADERNO 5 | AMARELO"
#   "MATEMÁTICA E SUAS TECNOLOGIAS • 2º DIA • CADERNO 5 • AMARELO"
_AREA_WORDS = r"(?:LINGUAGENS|MATEM[ÁA]TICA|CI[ÊE]NCIAS(?:\s+(?:HUMANAS|DA\s+NATUREZA))?|REDA[ÇC][ÃA]O)"
_RUNNING_HEADER_RE = re.compile(
    rf"{_AREA_WORDS}.{{0,60}}[|•·]\s*\d\s*[ºo]?\s*DIA\s*[|•·]\s*CADERNO",
    re.IGNORECASE,
)
# A section banner printed when a new area starts, e.g.
#   "CIÊNCIAS HUMANAS E SUAS TECNOLOGIAS"
#   "LINGUAGENS, CÓDIGOS E SUAS TECNOLOGIAS • 1º DIA • CADERNO 1 • AZUL"
# It may be truncated mid-span ("LINGUAGENS, CÓDIG"). Match the caps prefix.
_SECTION_BANNER_RE = re.compile(
    rf"^\s*[•·]?\s*(?:{_AREA_WORDS}|C[ÓO]DIGOS)\b", re.IGNORECASE
)
# A trailing run of running-header grammar glued onto an option: an AREA word,
# or the "Nº DIA • CADERNO n • COLOR" tail, possibly starting mid-phrase.
_HEADER_TAIL_RE = re.compile(
    r"(?:\s[•·]\s*|\s+)(?:"
    rf"{_AREA_WORDS}|C[ÓO]DIGOS"
    r"|\d?\s*[ºo]\s*DIA|CADERNO\s*\d+"
    r"|AZUL|AMARELO|BRANCO|ROSA|VERDE|CINZA"
    r")\b.*$"
)


def _looks_like_header_fragment(text: str) -> bool:
    """A run of the running-header / section banner, possibly truncated."""
    t = text.strip()
    if not t or not _SECTION_BANNER_RE.match(t):
        return False
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(c.isupper() for c in letters) / len(letters)
    return upper_ratio > 0.55 or bool(_RUNNING_HEADER_RE.search(t)) or "TECNOLOGIAS" in t.upper()
_HEADER_BAND_TOP = 74.0        # content never starts above this (Questão N label ~76)
_FOOTER_BAND_BOTTOM = 743.0    # content never ends below this

_OPTION_LABEL_FONT_HINT = "BundesbahnPi"   # ENEM A-E bullet font family
_OPTION_LABEL_RE = re.compile(r"^\s*([A-E])[\t ]")
# ENEM headers are "Questão 145" (2025) or "QUESTÃO 145" (2024) - match either.
_QUESTION_HEADER_RE = re.compile(r"^\s*QUEST[AÃ]O\s+(\d+)\b", re.IGNORECASE)

# any of these still inside a finished option = not import ready
_ARTIFACT_IN_TEXT = [
    ("barcode", _BARCODE_RE),
    ("watermark", _WATERMARK_RE),
    ("running_header", _RUNNING_HEADER_RE),
]
_GLYPH_GARBAGE_RE = re.compile(r"/g\d+|[�]")


@dataclass
class RecoveredOption:
    label: str
    text: str
    fraction_reconstructed: bool = False
    flags: list[str] = field(default_factory=list)


@dataclass
class RecoveredQuestion:
    number: int
    page: int
    column: int
    statement: str
    options: list[RecoveredOption]
    status: str = "PENDING"          # IMPORT_READY | OPTION_RECOVERY_PENDING | INVALID
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "number": self.number,
            "page": self.page,
            "column": self.column,
            "statement_preview": " ".join(self.statement.split())[:160],
            "options": [
                {"label": o.label, "text": o.text,
                 "fraction_reconstructed": o.fraction_reconstructed, "flags": o.flags}
                for o in self.options
            ],
            "status": self.status,
            "reasons": self.reasons,
        }


@dataclass
class OptionRecoveryResult:
    source_path: str
    page_texts: list[str]
    questions: list[RecoveredQuestion]
    method: str = "pymupdf-dict+drawings"

    # roll-ups
    @property
    def import_ready(self) -> list[int]:
        return [q.number for q in self.questions if q.status == "IMPORT_READY"]

    @property
    def option_recovery_pending(self) -> list[int]:
        return [q.number for q in self.questions if q.status == "OPTION_RECOVERY_PENDING"]

    @property
    def invalid(self) -> list[int]:
        return [q.number for q in self.questions if q.status == "INVALID"]

    def as_dict(self) -> dict:
        return {
            "source_path": self.source_path,
            "method": self.method,
            "counts": {
                "questions": len(self.questions),
                "import_ready": len(self.import_ready),
                "option_recovery_pending": len(self.option_recovery_pending),
                "invalid": len(self.invalid),
            },
            "import_ready": self.import_ready,
            "option_recovery_pending": self.option_recovery_pending,
            "invalid": self.invalid,
            "questions": [q.as_dict() for q in self.questions],
        }


# --------------------------------------------------------------------------- #
# low-level span / line model
# --------------------------------------------------------------------------- #


@dataclass
class _Span:
    text: str
    font: str
    size: float
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class _Line:
    spans: list[_Span]
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def fonts(self) -> set[str]:
        return {s.font for s in self.spans}


def _classify_span(sp: _Span) -> str:
    t = sp.text.strip()
    if not t:
        return "blank"
    if sp.font in _BARCODE_FONTS or _BARCODE_RE.search(t):
        return "barcode"
    if sp.size <= _WATERMARK_MAX_SIZE and _WATERMARK_RE.search(t):
        return "watermark"
    if _WATERMARK_RE.search(t) and len(t) > 40:
        return "watermark"
    if _RUNNING_HEADER_RE.search(t) or _looks_like_header_fragment(t):
        return "running_header"
    if (sp.y0 >= _FOOTER_BAND_BOTTOM or sp.y1 <= _HEADER_BAND_TOP) and re.fullmatch(r"\d{1,3}", t):
        return "page_number"
    return "content"


def _load_content_lines(page) -> list[_Line]:
    """Text lines with every non-content span removed, in raw block order."""
    out: list[_Line] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for ln in block["lines"]:
            spans = [
                _Span(s["text"], s["font"], float(s["size"]),
                      float(s["bbox"][0]), float(s["bbox"][1]),
                      float(s["bbox"][2]), float(s["bbox"][3]))
                for s in ln["spans"]
            ]
            kept = [s for s in spans if _classify_span(s) == "content"]
            if not kept or not "".join(s.text for s in kept).strip():
                continue
            out.append(_Line(
                kept,
                min(s.x0 for s in kept), min(s.y0 for s in kept),
                max(s.x1 for s in kept), max(s.y1 for s in kept),
            ))
    return out


def _horizontal_hairlines(page) -> list[tuple[float, float, float]]:
    """(y, x0, x1) of thin horizontal rules - fraction-bar candidates."""
    bars: list[tuple[float, float, float]] = []
    for dr in page.get_drawings():
        if (dr.get("width") or 0) > 1.2:
            continue
        for item in dr["items"]:
            if item[0] != "l":
                continue
            p1, p2 = item[1], item[2]
            if abs(p1.y - p2.y) <= 0.6 and 2.0 <= abs(p2.x - p1.x) <= 60.0:
                bars.append((round((p1.y + p2.y) / 2, 1), min(p1.x, p2.x), max(p1.x, p2.x)))
    return bars


# --------------------------------------------------------------------------- #
# column + question grouping
# --------------------------------------------------------------------------- #


def _column_split(lines: list[_Line], page_width: float) -> list[list[_Line]]:
    """Return [left_lines, right_lines] for a 2-column page, else [all_lines]."""
    header_xs = [ln.x0 for ln in lines if _QUESTION_HEADER_RE.match(ln.text)]
    if len(header_xs) < 2:
        return [lines]
    mid = page_width / 2
    left_heads = [x for x in header_xs if x < mid]
    right_heads = [x for x in header_xs if x >= mid]
    if not left_heads or not right_heads:
        return [lines]
    left = sorted((ln for ln in lines if ln.x0 < mid), key=lambda l: l.y0)
    right = sorted((ln for ln in lines if ln.x0 >= mid), key=lambda l: l.y0)
    return [left, right]


_SYMBOLIC_FONT_HINT = ("Symbol", "Math", "CMSY", "CMMI", "Times")
_INT_RE = re.compile(r"[0-9]{1,5}")


def _assemble_option_text(
    label_line: _Line,
    following: list[_Line],
    hairlines: list[tuple[float, float, float]],
) -> tuple[str, bool, list[str]]:
    """Text of one option starting at ``label_line``.

    A stacked "numerator / denominator" fraction is reconstructed ONLY when the
    structural evidence is unambiguous:
      * the option's first line is a bare integer (the numerator),
      * followed by exactly one line that is a bare integer (the denominator),
        x-aligned under the numerator and directly below it,
      * with a thin horizontal rule (the fraction bar) between them spanning the
        numerator width,
      * and NO symbolic/superscript spans anywhere in the option (which would
        mean exponent / scientific notation, not a fraction).
    Anything short of that is left verbatim and flagged - never guessed.
    """
    flags: list[str] = []
    head = _OPTION_LABEL_RE.sub("", label_line.text, count=1).strip()

    numerator_span = None
    for s in label_line.spans:
        if _OPTION_LABEL_FONT_HINT in s.font:
            continue
        if s.text.strip():
            numerator_span = s
            break

    _opt_blob = label_line.text + " " + " ".join(l.text for l in following)
    option_has_symbolic = any(
        any(h in s.font for h in _SYMBOLIC_FONT_HINT)
        for ln in [label_line, *following] for s in ln.spans
    ) or any(ch in _opt_blob for ch in ("×", "^", "−", "√", "≤", "≥", "π", "="))

    denom_line = following[0] if following else None
    extra_numeric = sum(
        1 for ln in following[1:] if re.fullmatch(r"[0-9.,]{1,6}", ln.text.strip())
    )
    fraction_shape = (
        not option_has_symbolic
        and numerator_span is not None
        and _INT_RE.fullmatch(head or "")
        and denom_line is not None
        and _INT_RE.fullmatch(denom_line.text.strip())
        and 0.0 <= denom_line.y0 - numerator_span.y1 <= 14.0
        and extra_numeric == 0          # a 2nd stacked digit line => exponent, not fraction
    )
    fraction = False
    if fraction_shape:
        num_c = (numerator_span.x0 + numerator_span.x1) / 2
        den_c = (denom_line.x0 + denom_line.x1) / 2
        num_w = numerator_span.x1 - numerator_span.x0
        den_w = denom_line.x1 - denom_line.x0
        # the fraction bar itself is the proof: a hairline between the two, wide
        # enough to span them, overlapping BOTH x-ranges, with centres aligned.
        bar = [
            b for b in hairlines
            if numerator_span.y1 - 2.0 <= b[0] <= denom_line.y0 + 2.0
            and b[1] <= numerator_span.x1 + 3.0 and b[2] >= numerator_span.x0 - 3.0
            and b[1] <= denom_line.x1 + 3.0 and b[2] >= denom_line.x0 - 3.0
            and (b[2] - b[1]) >= 0.5 * max(num_w, den_w)
        ]
        if bar and abs(num_c - den_c) <= 8.0:
            return f"{head}/{denom_line.text.strip()}", True, flags
        flags.append("stacked_number_without_fraction_bar")

    # stacked math that is NOT a plain fraction (superscript exponent, sci-notation):
    # numerator-ish head plus extra numeric fragment lines and/or math symbols.
    numeric_following = [ln for ln in following if re.fullmatch(r"[0-9.,]{1,6}", ln.text.strip())]
    if _INT_RE.match(head or "") and (option_has_symbolic or len(numeric_following) >= 1) and not fraction:
        if option_has_symbolic or len(numeric_following) >= 1:
            flags.append("stacked_math_not_linearised")

    parts = [head] if head else []
    for ln in following:
        if _looks_like_header_fragment(ln.text):
            break
        parts.append(" ".join(ln.text.split()))
    text = " ".join(p for p in parts if p).strip()
    # trim a running-header run glued onto the end of the option text
    m = _HEADER_TAIL_RE.search(text)
    if m and m.start() > 0:
        text = text[: m.start()].rstrip(" •·")
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"\s*[•·]\s*$", "", text).strip()          # drop trailing bullet
    return text, fraction, flags


def _validate_question(q: RecoveredQuestion) -> None:
    reasons: list[str] = []
    labels = [o.label for o in q.options]
    if labels != list("ABCDE"):
        reasons.append(f"labels {labels} != A-E")
    for o in q.options:
        if not o.text.strip():
            reasons.append(f"{o.label}: empty")
        for name, rx in _ARTIFACT_IN_TEXT:
            if rx.search(o.text):
                reasons.append(f"{o.label}: contains {name}")
        if _GLYPH_GARBAGE_RE.search(o.text):
            reasons.append(f"{o.label}: glyph garbage")
        if o.text.endswith(("-", "/", "(")) or o.text.endswith(" e") or o.text.endswith(" de"):
            reasons.append(f"{o.label}: looks truncated")
        if "stacked_number_without_fraction_bar" in o.flags:
            reasons.append(f"{o.label}: stacked number, no fraction bar (OPTION_RECOVERY_PENDING)")
        if "stacked_math_not_linearised" in o.flags:
            reasons.append(f"{o.label}: stacked exponent/sci-notation not safely linearised (OPTION_RECOVERY_PENDING)")
        if "fraction_suppressed_ambiguous_item" in o.flags:
            reasons.append(f"{o.label}: fraction not trusted, sibling option has stacked math (OPTION_RECOVERY_PENDING)")
    for name, rx in _ARTIFACT_IN_TEXT:
        if rx.search(q.statement):
            reasons.append(f"statement contains {name}")
    if not q.number:
        reasons.append("no official number")

    # joined-fragment math garbage, e.g. "T F= 1 59," / "108 25," - a bare
    # trailing comma, or an "=" with stray digit groups, means MuPDF stacked
    # spans were linearised wrong. Not safe to import.
    for o in q.options:
        t = o.text.strip()
        if t and (
            re.search(r"\d\s*,\s*$", t)
            or re.search(r"[A-Za-zÀ-ÿ]\s*[A-Za-zÀ-ÿ]?\s*=\s*\S", t)
            or re.fullmatch(r"[0-9]{1,4}\s+[0-9][0-9 ,]*", t)
        ):
            reasons.append(f"{o.label}: joined math fragments (OPTION_RECOVERY_PENDING)")

    q.reasons = reasons
    empties = sum(1 for o in q.options if not o.text.strip())
    if not reasons:
        q.status = "IMPORT_READY"
    elif any("OPTION_RECOVERY_PENDING" in r for r in reasons):
        q.status = "OPTION_RECOVERY_PENDING"
    elif labels == list("ABCDE") and empties == len(q.options):
        # every option is empty -> the alternatives are images/formulas, not text;
        # the content exists in the PDF but not as a recoverable text layer.
        q.status = "OPTION_RECOVERY_PENDING"
    elif labels != list("ABCDE"):
        q.status = "INVALID"
    else:
        q.status = "OPTION_RECOVERY_PENDING"


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #


def recover_option_layout(pdf_path: Path | str) -> OptionRecoveryResult:
    import pymupdf

    pdf_path = Path(pdf_path)
    doc = pymupdf.open(str(pdf_path))
    try:
        page_texts: list[str] = []
        questions: list[RecoveredQuestion] = []

        for pindex in range(len(doc)):
            page = doc[pindex]
            width = float(page.rect.width)
            lines = _load_content_lines(page)
            hairlines = _horizontal_hairlines(page)
            columns = _column_split(lines, width)

            rendered_blocks: list[str] = []
            for col_index, col_lines in enumerate(columns):
                # segment this column into question blocks
                idx = 0
                n = len(col_lines)
                while idx < n:
                    ln = col_lines[idx]
                    hm = _QUESTION_HEADER_RE.match(ln.text)
                    if not hm:
                        rendered_blocks.append(ln.text.strip())
                        idx += 1
                        continue
                    q_number = int(hm.group(1))
                    j = idx + 1
                    while j < n and not _QUESTION_HEADER_RE.match(col_lines[j].text):
                        j += 1
                    body = col_lines[idx + 1:j]

                    # find option label lines within the body
                    label_positions = [
                        k for k, bl in enumerate(body)
                        if _OPTION_LABEL_RE.match(bl.text)
                        and any(_OPTION_LABEL_FONT_HINT in s.font for s in bl.spans)
                    ]
                    statement_lines = body[: label_positions[0]] if label_positions else body
                    statement = " ".join(" ".join(l.text.split()) for l in statement_lines).strip()

                    opts: list[RecoveredOption] = []
                    for oi, kpos in enumerate(label_positions):
                        letter = _OPTION_LABEL_RE.match(body[kpos].text).group(1)
                        end = label_positions[oi + 1] if oi + 1 < len(label_positions) else len(body)
                        following = body[kpos + 1:end]
                        text, frac, flags = _assemble_option_text(body[kpos], following, hairlines)
                        opts.append(RecoveredOption(letter, text, frac, flags))

                    # if ANY sibling option shows unlinearised stacked math, a
                    # fraction reconstructed on another option is not trustworthy
                    # either - the whole item is math-notation-heavy.
                    if any(
                        "stacked_math_not_linearised" in o.flags
                        or "stacked_number_without_fraction_bar" in o.flags
                        for o in opts
                    ):
                        for o in opts:
                            if o.fraction_reconstructed:
                                o.fraction_reconstructed = False
                                o.flags.append("fraction_suppressed_ambiguous_item")

                    q = RecoveredQuestion(
                        number=q_number, page=pindex + 1, column=col_index,
                        statement=statement, options=opts,
                    )
                    _validate_question(q)
                    questions.append(q)

                    # emit normalised text: header + statement always; options
                    # ONLY when the question passed the validation gate. A
                    # PENDING/INVALID question emits no option lines, so the
                    # downstream parser sees 0 options -> requires_review -> it
                    # can never be imported as NEW off unverified alternatives.
                    rendered_blocks.append(f"Questão {q_number}")
                    if statement:
                        rendered_blocks.append(statement)
                    if q.status == "IMPORT_READY":
                        for o in opts:
                            rendered_blocks.append(f"{o.label}  {o.text}")
                    idx = j

            # trailing newline: PdfParser joins pages with "\f" and its header
            # regex anchors on "^" (after \n, not after \f) - without this a
            # question that lands first on a page is missed.
            page_texts.append("\n".join(rendered_blocks) + "\n")

        return OptionRecoveryResult(
            source_path=str(pdf_path), page_texts=page_texts, questions=questions
        )
    finally:
        doc.close()
