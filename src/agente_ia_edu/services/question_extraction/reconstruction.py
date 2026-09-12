"""PHASE 28 - Question Reconstruction & Review Quality.

Sits strictly BETWEEN extraction and validation (spec s16), acting on the
lines belonging to ONE already-detected question boundary (PHASE 27's
document-wide detection - 83/83 on the two golden PDFs - is REUSED
unchanged; this module never touches boundary detection).

Why local, not global (spec s3/s4/s5): PHASE 27's investigation found that
a whole-page two-column classifier cannot reliably separate a genuine
two-column exercise page from a single-column page that happens to contain
a wide table or scattered formula fragments, IN THIS REAL DOCUMENT SET -
both share the same bimodal left/right margins. Restricting the same
strict column test (``structure.detect_two_column_layout``) to the ~5-40
lines that make up ONE question shrinks the blast radius of a
misclassification to that one question, and - critically - lets this
module VERIFY the result before trusting it: a reconstruction is only
adopted when it produces a STRICTLY BETTER structured result (more
complete, sequential options; less orphan text) than PHASE 27's original
linear reading order. A reconstruction that is not clearly better is
discarded and the original stands, flagged for review - never a coin flip
(spec s2: "não simplesmente aumentar APPROVED").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .boundary import ExtractedQuestionDraft, OptionDraft, QuestionBoundary, _extract_options, classify_and_extract
from .structure import DocumentStructure, TextLine, detect_two_column_layout

# spec s12 - structured reasons, never a bare "REVIEW_REQUIRED".
COLUMN_AMBIGUITY = "COLUMN_AMBIGUITY"
BROKEN_READING_ORDER = "BROKEN_READING_ORDER"
MISSING_OPTION = "MISSING_OPTION"
ORPHAN_TEXT = "ORPHAN_TEXT"
UNASSIGNED_ASSET = "UNASSIGNED_ASSET"
FORMULA_AMBIGUITY = "FORMULA_AMBIGUITY"
CROSS_PAGE_AMBIGUITY = "CROSS_PAGE_AMBIGUITY"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
SEQUENCE_ANOMALY = "SEQUENCE_ANOMALY"


@dataclass
class ReconstructionResult:
    reconstructed_text: str
    options: list[OptionDraft]
    reconstruction_applied: bool  # True only if a LOCAL column fix was adopted
    column_split_x: float | None = None


def _quality_score(statement: str, options: list[OptionDraft]) -> tuple[int, int]:
    """(usable_option_count, statement_len) - a reconstruction that finds
    MORE clean, sequential options (or, tied, a longer coherent statement)
    is the better one. Purely structural, never a guess."""
    return (len(options), len(statement))


def reconstruct_question(
    structure: DocumentStructure, boundary: QuestionBoundary, original_body: str,
) -> ReconstructionResult:
    """Try a LOCAL, strict two-column reorder of exactly this question's
    own lines. Falls back to the original (PHASE 27) linear text untouched
    when no clean column split exists, or when it does not measurably
    improve the structured result."""
    lines = structure.lines_in_range(boundary.start, boundary.end)
    original_statement, original_options = _extract_options(original_body)
    baseline = ReconstructionResult(
        reconstructed_text=original_body, options=original_options, reconstruction_applied=False,
    )
    if len(lines) < 6:  # too little geometry to judge safely
        return baseline

    split_x = detect_two_column_layout(lines)
    if split_x is None:
        return baseline

    left = sorted((ln for ln in lines if ln.x0 < split_x), key=lambda ln: (ln.y0, ln.x0))
    right = sorted((ln for ln in lines if ln.x0 >= split_x), key=lambda ln: (ln.y0, ln.x0))
    reordered_lines = left + right
    candidate_text = "\n".join(ln.text for ln in reordered_lines)
    candidate_statement, candidate_options = _extract_options(candidate_text)

    if _quality_score(candidate_statement, candidate_options) > _quality_score(original_statement, original_options):
        return ReconstructionResult(
            reconstructed_text=candidate_text, options=candidate_options,
            reconstruction_applied=True, column_split_x=split_x,
        )
    # a column split candidate existed but did not clearly improve the
    # result - report it (column_split_x set) so the caller can tell
    # COLUMN_AMBIGUITY (a split was found, just not trusted) apart from
    # BROKEN_READING_ORDER (no column geometry at all).
    baseline.column_split_x = split_x
    return baseline


def review_reasons_for(
    draft: ExtractedQuestionDraft, *, reconstruction: ReconstructionResult,
) -> list[str]:
    """spec s12 - WHY a question needs review, not just THAT it does."""
    reasons: list[str] = []
    if "cross_page" in draft.flags and not reconstruction.options and draft.question_type == "multiple_choice":
        reasons.append(CROSS_PAGE_AMBIGUITY)
    if reconstruction.reconstruction_applied is False and "possible_missing_content" in draft.flags:
        # a column split existed in the geometry but reconstruction did not
        # clearly help, OR no split existed at all and content still looks
        # truncated/garbled - the two are distinguished by the caller, which
        # knows whether detect_two_column_layout found a candidate at all.
        reasons.append(COLUMN_AMBIGUITY if reconstruction.column_split_x is not None else BROKEN_READING_ORDER)
    if draft.question_type == "multiple_choice" and len(draft.options) not in (4, 5):
        reasons.append(MISSING_OPTION)
    if "possible_missing_content" in draft.flags and draft.question_type != "multiple_choice":
        reasons.append(ORPHAN_TEXT)
    if "image_present" in draft.flags and draft.confidence < 0.6:
        reasons.append(UNASSIGNED_ASSET)
    if draft.confidence < 0.6 and not reasons:
        reasons.append(LOW_CONFIDENCE)
    return sorted(set(reasons))
