"""PHASE 27 - Question Extraction Engine: boundary detection + classification.

STEP 5-8 of the pipeline (spec s3):
    reading-order text -> question boundary detection -> question
    classification -> question content extraction -> alternative extraction

Detection is NOT "the number is the only signal" (spec s2/s5): a candidate
number is only a real question boundary when it sits at the START of a
real line (never mid-sentence or mid-formula) and is not itself a
"chapter.subsection" decimal ("5.4 Etapas..."). When the SAME number
appears more than once (a worked-example's numbered steps, an answer-key
grid), the candidate preceded by a paragraph break wins; among equally
good candidates, the one with the longer body wins - a real question is
always substantially longer than a numbered step or a one-word gabarito
answer. This combination was validated against both real pilot PDFs
(spec s14 golden cases): 24/24 and 59/59, including cross-page questions
and discursive (option-less) questions.

Pure, deterministic, no randomness, no LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# -- boundary marker: "1." / "01." / "1)" / "01)" at the START of a real
#    line. [ \t]* (never \s*) so the anchor cannot swallow a blank line -
#    that blank line is itself a signal (paragraph break), checked below.
_MARKER = re.compile(r"(?m)^[ \t]*(\d{1,3})[.)][ \t]*(?!\d[ \t]+[A-ZÀ-Ú])(?=\S)")
# "Questão N" / "QUESTÃO N" - a second, independent marker convention.
_MARKER_WORD = re.compile(r"(?mi)^[ \t]*quest[aã]o\s+(\d{1,3})\b[.:)]?[ \t]*")
# "(1)" parenthesised numbering.
_MARKER_PAREN = re.compile(r"(?m)^[ \t]*\((\d{1,3})\)[ \t]*(?=\S)")

_PARAGRAPH_BREAK_BEFORE = re.compile(r"\n[ \t]*\n[ \t]*\Z")

_ANSWER_KEY_HEADING = re.compile(
    r"(?im)^[ \t]*(gabarito|respostas?( comentadas?)?|resolu[cç][aã]o( comentada)?)[ \t]*:?[ \t]*$"
)

# option markers: "A) " / "a) " / "A. " / "A - " - at least 2 in ascending
# sequence (a), b), c)...) to be trusted as real alternatives.
_OPTION = re.compile(r"\b([A-Ea-e])[.)][ \t]*")

MIN_QUESTION_BODY_CHARS = 12
_TRUE_FALSE_HINT = re.compile(r"(?i)\b(verdadeiro|falso|\(V\)|\(F\)|V ou F)\b")
_NUMERIC_ANSWER_HINT = re.compile(r"^[\s\d.,eE+\-x×%°ºa-zA-Z/]{1,80}$")

QUESTION_TYPES = ("multiple_choice", "discursive", "numeric", "true_false", "unknown")


@dataclass(frozen=True)
class OptionDraft:
    label: str  # "A".."E"
    text: str


@dataclass
class QuestionBoundary:
    """One detected question span in the reading-order text, BEFORE content
    classification. Character offsets are into the DocumentStructure's
    ``text()`` string."""

    number: int
    marker_style: str  # PERIOD | PAREN | WORD
    start: int  # offset of the body's first character (after the marker)
    end: int  # offset just past the body's last character
    preceded_by_paragraph_break: bool


@dataclass
class ExtractedQuestionDraft:
    number: int
    question_type: str
    raw_text: str
    normalized_text: str
    options: list[OptionDraft] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    confidence: float = 0.0


def cut_at_answer_key(text: str) -> tuple[str, int]:
    """Return (main_text, cut_offset). Text from a 'Gabarito'/'Respostas'/
    'Resolução' heading onward is answer-key/worked-solution material, not
    new questions - spec s6's own worked pilot PDF glues a full page of
    per-question resolutions onto whatever the last real question was
    unless this is excluded first."""
    m = _ANSWER_KEY_HEADING.search(text)
    if m:
        return text[: m.start()], m.start()
    return text, len(text)


def _all_markers(text: str) -> list[tuple[int, int, int, str]]:
    """(number, start_of_body, match_start, style) for every candidate,
    across all supported numbering conventions."""
    out = []
    for m in _MARKER.finditer(text):
        out.append((int(m.group(1)), m.end(), m.start(), "PERIOD"))
    for m in _MARKER_WORD.finditer(text):
        out.append((int(m.group(1)), m.end(), m.start(), "WORD"))
    for m in _MARKER_PAREN.finditer(text):
        out.append((int(m.group(1)), m.end(), m.start(), "PAREN"))
    out.sort(key=lambda t: t[2])
    return out


def detect_boundaries(text: str) -> list[QuestionBoundary]:
    """Find every question boundary in ``text`` (already cut past any
    answer-key section by the caller). Deterministic: same input -> same
    output, always."""
    candidates = _all_markers(text)
    if not candidates:
        return []

    spans: list[QuestionBoundary] = []
    for i, (number, body_start, match_start, style) in enumerate(candidates):
        body_end = candidates[i + 1][2] if i + 1 < len(candidates) else len(text)
        preceded = bool(_PARAGRAPH_BREAK_BEFORE.search(text[:match_start]))
        spans.append(QuestionBoundary(
            number=number, marker_style=style, start=body_start, end=body_end,
            preceded_by_paragraph_break=preceded,
        ))

    # disambiguate duplicate numbers: paragraph-break-preceded wins; ties
    # broken by the longer body (a real question is never as short as a
    # numbered step or a one-token gabarito answer).
    best: dict[int, QuestionBoundary] = {}
    for span in spans:
        body_len = span.end - span.start
        cur = best.get(span.number)
        if cur is None:
            best[span.number] = span
            continue
        cur_len = cur.end - cur.start
        cur_score = (1 if cur.preceded_by_paragraph_break else 0, cur_len)
        new_score = (1 if span.preceded_by_paragraph_break else 0, body_len)
        if new_score > cur_score:
            best[span.number] = span
    return [best[n] for n in sorted(best)]


def _extract_options(body: str) -> tuple[str, list[OptionDraft]]:
    """Split off a trailing/embedded run of A)-E) (or a-e)) options. Returns
    (statement, options) - options=[] when no confident, sequential run of
    at least 2 ascending letters starting at A/a is found (spec s7: never
    assume every question has alternatives)."""
    marks = list(_OPTION.finditer(body))
    if len(marks) < 2:
        return body, []
    letters = [m.group(1).upper() for m in marks]
    if len(letters) != len(set(letters)):
        # a repeated letter (A...E...A...) means two option lists got
        # concatenated (page/column-order corruption) - never merge them
        # into one fabricated set; fall back to unstructured text instead.
        return body, []
    expected = [chr(ord("A") + i) for i in range(len(letters))]
    if letters != expected:
        return body, []
    statement = body[: marks[0].start()].strip()
    options: list[OptionDraft] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        opt_text = " ".join(body[m.end():end].split())
        if opt_text:
            options.append(OptionDraft(label=m.group(1).upper(), text=opt_text))
    if len(options) != len(marks):
        return body, []
    return statement, options


def classify_and_extract(boundary: QuestionBoundary, full_text: str) -> ExtractedQuestionDraft:
    """STEP 6-8: turn one boundary into a typed, structured question draft.
    Never discards - a question that cannot be confidently typed becomes
    ``unknown`` and is flagged, never dropped (spec s7/s28)."""
    raw_body = full_text[boundary.start:boundary.end]
    statement, options = _extract_options(raw_body)
    statement = " ".join(statement.split())
    normalized = statement
    flags: set[str] = set()

    has_clean_options = bool(options) and len(options) in (4, 5)
    has_substantial_statement = len(statement) >= MIN_QUESTION_BODY_CHARS
    # PHASE 28: "not preceded by a paragraph break" alone is NOT proof of
    # missing content - a complete option set or a substantial statement is
    # independently-verifiable positive evidence that outweighs it (this
    # phase's own investigation found the paragraph-break heuristic
    # unreliable on a densely-typeset real document). Only flag when BOTH
    # positive signals are absent.
    if not has_clean_options and not has_substantial_statement:
        flags.add("possible_missing_content")

    # PHASE 28: a much sharper, positive contamination signal a "long
    # statement" check alone would miss - the tail of the PREVIOUS
    # question/section leaked onto the front of this one. Concretely: this
    # question's OWN "N. <Capital...>" marker appears a SECOND time,
    # further into the statement, or a chapter/episode heading is embedded
    # mid-statement - both mean everything before that point is orphaned
    # prior content, not this question's real start.
    own_marker = re.compile(rf"\b{boundary.number}\.\s+[A-ZÀ-Ú]")
    second_hit = own_marker.search(statement, 1)
    heading_leak = re.search(r"(?i)\bEPIS[ÓO]DIO\s+\d+|\bCAP[ÍI]TULO\s+\d+", statement)
    if second_hit or heading_leak:
        flags.add("possible_missing_content")
        flags.add("orphan_prefix_contamination")

    if options:
        question_type = "multiple_choice"
        if len(options) < 4:
            flags.add("options_complete_false")
    elif _TRUE_FALSE_HINT.search(raw_body):
        question_type = "true_false"
    elif statement and _NUMERIC_ANSWER_HINT.match(statement) and len(statement) < 40:
        question_type = "numeric"
    elif statement:
        question_type = "discursive"
    else:
        question_type = "unknown"
        flags.add("possible_missing_content")

    confidence = _score_confidence(statement, options, boundary, flags)

    return ExtractedQuestionDraft(
        number=boundary.number, question_type=question_type,
        raw_text=raw_body.strip(), normalized_text=normalized,
        options=options, flags=flags, confidence=confidence,
    )


def _score_confidence(statement: str, options: list[OptionDraft],
                      boundary: QuestionBoundary, flags: set[str]) -> float:
    """Deterministic composite score in [0, 1]. Documented, not tuned by
    any ML - simple, explainable additive rule (spec s12).

    PHASE 28 rebalancing: a COMPLETE, sequential option set (A..D/E) is
    strong, independently-verifiable structural evidence a question was
    reconstructed correctly - stronger than the vertical-gap-based
    "preceded by a paragraph break" heuristic, which this phase's own
    investigation found unreliable on at least one real golden PDF (a
    densely-typeset exercise sheet with near-uniform line spacing between
    consecutive questions, so genuine question breaks are geometrically
    almost indistinguishable from ordinary line spacing). The paragraph-
    break signal is kept (it is still useful, and is exactly what
    disambiguates a duplicate number during boundary detection - see
    detect_boundaries()) but no longer carries as much weight ALONE as a
    verified, complete option structure."""
    score = 0.4
    if options and len(options) in (4, 5):
        score += 0.25
    elif options:
        score -= 0.1  # an incomplete option run is a real red flag
    if len(statement) >= 40:
        score += 0.15
    if boundary.preceded_by_paragraph_break:
        score += 0.1
    if "possible_missing_content" in flags:
        score -= 0.3
    return max(0.0, min(1.0, round(score, 3)))
