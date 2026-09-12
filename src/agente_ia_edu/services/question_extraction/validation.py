"""PHASE 27 - Question Extraction Engine: validation.

STEP 11-12 of the pipeline (spec s3/s13): sequence/duplicate/gap checks +
confidence-based review routing. A document is only "validated" once these
checks pass (spec s13). Never masks a problem - every irregularity is
reported by name, and every question with a real issue is routed to
REVIEW_REQUIRED rather than silently trusted (spec s18/s28).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .boundary import ExtractedQuestionDraft

CONFIDENCE_REVIEW_THRESHOLD = 0.6


@dataclass
class ValidationReport:
    expected_question_count: int | None
    detected_question_count: int
    missing_numbers: list[int] = field(default_factory=list)
    duplicated_numbers: list[int] = field(default_factory=list)  # always [] - detect_boundaries dedupes
    sequence_gaps: list[int] = field(default_factory=list)
    low_confidence_numbers: list[int] = field(default_factory=list)
    unknown_type_numbers: list[int] = field(default_factory=list)
    orphan_asset_count: int = 0

    @property
    def sequence_ok(self) -> bool:
        return not self.missing_numbers and not self.sequence_gaps

    @property
    def validated(self) -> bool:
        """Spec s13: only 'validated' when sequence/duplicate checks pass.
        Low-confidence/unknown-type questions do NOT block document-level
        validation (they are individually REVIEW_REQUIRED) - a document is
        validated when its DETECTION is structurally sound."""
        return self.sequence_ok and not self.duplicated_numbers

    def as_dict(self) -> dict:
        return {
            "expected_question_count": self.expected_question_count,
            "detected_question_count": self.detected_question_count,
            "missing_numbers": self.missing_numbers,
            "duplicated_numbers": self.duplicated_numbers,
            "sequence_gaps": self.sequence_gaps,
            "low_confidence_numbers": self.low_confidence_numbers,
            "unknown_type_numbers": self.unknown_type_numbers,
            "orphan_asset_count": self.orphan_asset_count,
            "sequence_ok": self.sequence_ok,
            "validated": self.validated,
        }


def validate(
    drafts: list[ExtractedQuestionDraft],
    *,
    expected_question_count: int | None = None,
    orphan_asset_count: int = 0,
) -> ValidationReport:
    numbers = sorted(d.number for d in drafts)
    missing: list[int] = []
    if expected_question_count:
        missing = sorted(set(range(1, expected_question_count + 1)) - set(numbers))

    gaps: list[int] = []
    for a, b in zip(numbers, numbers[1:]):
        if b - a > 1:
            gaps.extend(range(a + 1, b))

    seen: set[int] = set()
    dupes: list[int] = []
    for n in numbers:
        if n in seen:
            dupes.append(n)
        seen.add(n)

    low_conf = sorted(d.number for d in drafts if d.confidence < CONFIDENCE_REVIEW_THRESHOLD)
    unknown = sorted(d.number for d in drafts if d.question_type == "unknown")

    return ValidationReport(
        expected_question_count=expected_question_count,
        detected_question_count=len(drafts),
        missing_numbers=missing, duplicated_numbers=dupes, sequence_gaps=sorted(set(gaps)),
        low_confidence_numbers=low_conf, unknown_type_numbers=unknown,
        orphan_asset_count=orphan_asset_count,
    )


def review_status_for(draft: ExtractedQuestionDraft) -> str:
    """The INITIAL staging status for one question (spec s18). Never
    PUBLISHED/APPROVED automatically - those require an explicit human
    action regardless of how clean the extraction looks."""
    if draft.confidence < CONFIDENCE_REVIEW_THRESHOLD:
        return "REVIEW_REQUIRED"
    if draft.question_type == "unknown":
        return "REVIEW_REQUIRED"
    if "possible_missing_content" in draft.flags:
        return "REVIEW_REQUIRED"
    return "VALIDATED"
