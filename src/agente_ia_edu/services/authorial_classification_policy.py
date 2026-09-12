"""PHASE 30 - the single confidence/threshold policy for authorial question
pedagogical classification (spec s14: "os thresholds devem existir em uma
única policy... não espalhar números pelo código").

Reuses, rather than re-derives, the EXACT content-classification confidence-
band mapping ``curriculum_classification.py`` already persists (HIGH=0.90 /
MEDIUM=0.70 / LOW=0.40, from ``ClassificationProposalService.
_persist_provider_output``) - authorial and official questions share one
CatalogNode-backed truth (spec s4), and this constant is the one place
PHASE 30 restates that mapping for its own reporting/threshold decisions.

``difficulty`` is an INDEPENDENT dimension (spec s12/s13: never confused with
student mastery/performance) with its own, separately-tracked confidence.
"""

from __future__ import annotations

from decimal import Decimal

# spec s14 - model_confidence / validation_confidence / final_confidence are
# conceptually distinct; when one cannot be computed this phase stores None
# (never an invented number) rather than reusing another dimension's value.
CONTENT_CONFIDENCE_BY_BAND: dict[str, Decimal] = {
    "HIGH": Decimal("0.90"), "MEDIUM": Decimal("0.70"), "LOW": Decimal("0.40"),
}

# Below this final_confidence, a CLASSIFIED result is downgraded to
# NEEDS_REVIEW by this phase's own orchestration (on top of whatever the
# shared engine already decided) - see review_reason "LOW_CONFIDENCE".
NEEDS_REVIEW_CONFIDENCE_THRESHOLD = Decimal("0.60")

# Difficulty-assessment confidence bands - independent thresholds, spec s14.
DIFFICULTY_CONFIDENCE_HIGH = Decimal("0.80")
DIFFICULTY_CONFIDENCE_MEDIUM = Decimal("0.55")

# Exactly QuestionVersion.recommended_difficulty's existing 3-value enum
# (official.py) - spec s12: "ou exatamente os valores já existentes no
# sistema". Never a new taxonomy of difficulty labels.
DIFFICULTY_VALUES: tuple[str, ...] = ("EASY", "MEDIUM", "HARD")
# The sentinel curriculum_classification.py's own propose_with_provider
# already writes when difficulty is not (yet) determined - reused here for
# the exact same "not determinable, never invented" meaning (spec s14/s41).
UNKNOWN_DIFFICULTY = "UNKNOWN"


def confidence_band(value: Decimal | float | None) -> str | None:
    """HIGH/MEDIUM/LOW for any 0-1 confidence in this phase. None in, None
    out (spec s14: null over an invented number) - never guesses a band for
    a value that could not be computed."""
    if value is None:
        return None
    value = Decimal(str(value))
    if value >= Decimal("0.85"):
        return "HIGH"
    if value >= Decimal("0.60"):
        return "MEDIUM"
    return "LOW"


# spec s12 review-reason vocabulary, EXTENDING (never replacing) the shared
# engine's own set (curriculum_classification.py's ClassificationProposalService
# ._review_reasons) with the failure/uncertainty modes unique to this phase's
# own orchestration (provider unavailable, malformed AI output, an uncertain
# difficulty judgement) - all stored in the SAME metadata_["review_reason"]
# slot the shared engine already uses, never a parallel field.
PHASE30_REVIEW_REASONS = frozenset({
    "CLASSIFIER_UNAVAILABLE", "INVALID_AI_OUTPUT", "DIFFICULTY_UNCERTAIN",
})
