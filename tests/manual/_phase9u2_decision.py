"""Shared deterministic decision core for PHASE 9U.2 INITIAL classification.

Extracted VERBATIM from ``phase9u2_batch0.py`` (the PHASE 9U.2-B decision core,
states D0..D10). Both executors apply exactly ONE decision precedence:

  * ``phase9u2_batch0.py``          — kinetics Batch-0 (taxonomy ``024_chemistry_kinetics``)
  * ``phase9u2h2_curriculum_v2.py`` — PHASE 9U.2-H2 (taxonomy ``curriculum-v2``)

The ONLY generalisation over the original is that the four target constants and
the protected-official-number set travel in a :class:`TargetProfile` instead of
being module globals.  Nothing in the branch order, the reason codes, or the
``detail`` strings changes.  ``phase9u2_batch0.decide_question_state(ctx)``
delegates here with a frozen kinetics profile and its regression suite
(``tests/test_phase9u2_batch0.py``) must stay green.

This module is PURE: no SQLAlchemy, no database, no provider, no OpenAI, no I/O,
nothing at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

STATES = frozenset(
    {
        "PROTECTED",
        "ALREADY_CLASSIFIED",
        "READY_FOR_INITIAL",
        "NEEDS_REVIEW",
        "OUT_OF_SCOPE",
        "SUPERSEDED_ONLY",
        "BLOCKED",
        "ERROR",
    }
)


# --------------------------------------------------------------------------- #
# Value objects (verbatim from phase9u2_batch0.py)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RowView:
    """Sanitised, comparable view of one pedagogical_classifications row."""

    id: str
    lifecycle: str
    content: str
    status: str
    model_version: str | None
    prompt_version: str | None
    taxonomy_version: str | None
    classification_mode: str | None
    supersedes_id: str | None

    @property
    def immutable_key(self) -> tuple:
        return (
            self.id,
            self.lifecycle,
            self.content,
            self.taxonomy_version,
            self.classification_mode,
            self.supersedes_id,
            self.status,
        )

    @classmethod
    def from_row(cls, row: Any) -> "RowView":
        """Map a pedagogical_classifications ORM row (duck-typed) onto RowView.

        Kept identical to the former phase9u2_batch0.RowView.from_row body. Only
        attribute access is used, so this module needs no ORM import and stays
        pure.
        """
        md = getattr(row, "metadata_", None) or {}
        return cls(
            id=str(row.id),
            lifecycle=row.lifecycle,
            content=row.content,
            status=row.status,
            model_version=row.model_version,
            prompt_version=row.prompt_version,
            taxonomy_version=md.get("taxonomy_version"),
            classification_mode=md.get("classification_mode"),
            supersedes_id=str(row.supersedes_id) if row.supersedes_id else None,
        )


@dataclass(frozen=True)
class QVView:
    id: str
    statement_text: str  # already coalesced: statement or canonical_text or ""


@dataclass(frozen=True)
class BindingProbe:
    """Result of the deterministic, provider-free controlled-vocabulary probe."""

    recovered_has_canonical: bool
    binding_status: str | None  # "BOUND" | "NEEDS_REVIEW" | None
    bound_candidate: dict[str, Any] | None
    literal_evidence_term: str | None


@dataclass(frozen=True)
class QuestionContext:
    official_number: int
    question_version: QVView | None
    classifications: tuple[RowView, ...]
    catalog_codes: frozenset[str]
    target_vocabulary_registered: bool
    binding: BindingProbe | None = None


@dataclass(frozen=True)
class PlannedWrite:
    question_version_id: str
    target_taxonomy_version: str
    classifier_version: str
    prompt_version: str
    bound_candidate: dict[str, Any]
    evidence_term: str


@dataclass(frozen=True)
class Decision:
    state: str
    reason_code: str
    detail: str = ""
    planned_write: PlannedWrite | None = None

    def __post_init__(self) -> None:
        assert self.state in STATES, self.state


@dataclass(frozen=True)
class TargetProfile:
    """The per-taxonomy constants the decision core needs.

    ``phase9u2_batch0`` builds one for kinetics; ``phase9u2h2_curriculum_v2``
    builds one per curriculum-v2 CONTENT node.  ``protected_official_numbers``
    is the D0 hard-block set — questions that must NEVER trigger a per-question
    classification read or write.
    """

    taxonomy_version: str
    canonical_content_code: str
    classifier_version: str
    prompt_version: str
    protected_official_numbers: frozenset[int]


# --------------------------------------------------------------------------- #
# DECISION CORE  (pure, deterministic, provider-free, no I/O)
# --------------------------------------------------------------------------- #


def decide_question_state(ctx: QuestionContext, profile: TargetProfile) -> Decision:
    target_taxonomy = profile.taxonomy_version
    canonical_content_code = profile.canonical_content_code
    try:
        # D0
        if ctx.official_number in profile.protected_official_numbers:
            return Decision("PROTECTED", "PROTECTED_QUESTION")

        # D1
        if ctx.question_version is None:
            return Decision("ERROR", "DANGLING_QUESTION_VERSION")

        rows = tuple(ctx.classifications)

        # D3
        bad_lc = sorted({r.lifecycle for r in rows if r.lifecycle not in ("ACTIVE", "SUPERSEDED")})
        if bad_lc:
            return Decision("ERROR", "UNKNOWN_LIFECYCLE_VALUE", detail=",".join(bad_lc))

        active = [r for r in rows if r.lifecycle == "ACTIVE"]
        superseded = [r for r in rows if r.lifecycle == "SUPERSEDED"]

        # D4 — DB already forbids this; never write over it
        seen: dict[Any, int] = {}
        for r in active:
            seen[r.taxonomy_version] = seen.get(r.taxonomy_version, 0) + 1
        if any(c > 1 for c in seen.values()):
            return Decision("ERROR", "ACTIVE_UNIQUENESS_ALREADY_VIOLATED")

        active_target = [r for r in active if r.taxonomy_version == target_taxonomy]
        active_other = [r for r in active if r.taxonomy_version != target_taxonomy]

        # D5
        if len(active_target) > 1:
            return Decision("ERROR", "MULTIPLE_ACTIVE_TARGET")
        if len(active_target) == 1:
            # Documented precedence (spec section 22.4): an ACTIVE INITIAL row on
            # the TARGET taxonomy authoritatively means "already classified for
            # 9U.2". A co-existing ACTIVE row on a DIFFERENT taxonomy is legal
            # (the DB unique index is per (qv, taxonomy_version)); it is surfaced
            # in `detail` but does not downgrade to ERROR and does not block,
            # because ALREADY_CLASSIFIED performs no write.
            other = sorted({str(r.taxonomy_version) for r in active_other})
            det = f"co_active_other_taxonomy={other}" if other else ""
            mode = active_target[0].classification_mode
            if mode == "INITIAL":
                return Decision("ALREADY_CLASSIFIED", "ACTIVE_INITIAL_TARGET_TAXONOMY", detail=det)
            return Decision(
                "NEEDS_REVIEW",
                "ACTIVE_TARGET_TAXONOMY_NON_INITIAL_MODE",
                detail=(f"mode={mode!r} " + det).strip(),
            )

        # D6
        if active_other:
            taxes = sorted({str(r.taxonomy_version) for r in active_other})
            return Decision(
                "OUT_OF_SCOPE",
                "ACTIVE_CLASSIFICATION_IN_OTHER_TAXONOMY",
                detail="taxonomies=" + ",".join(taxes),
            )

        # D7
        if superseded and not active:
            return Decision(
                "SUPERSEDED_ONLY", "ONLY_SUPERSEDED_ROWS_PRESENT", detail=f"superseded_rows={len(superseded)}"
            )

        # D8 — truly unclassified
        if rows:  # defensive: unreachable (no active, no superseded => empty)
            return Decision("NEEDS_REVIEW", "UNCLASSIFIED_STATE_UNEXPECTED", detail=f"rows={len(rows)}")

        statement = (ctx.question_version.statement_text or "").strip()
        if not statement:
            return Decision("BLOCKED", "NO_STATEMENT_TEXT")
        if not ctx.target_vocabulary_registered:
            return Decision("BLOCKED", "NO_CONTROLLED_VOCABULARY_FOR_TARGET")
        if canonical_content_code not in ctx.catalog_codes:
            return Decision("BLOCKED", "CANONICAL_CATALOG_NODE_MISSING")

        # D9
        probe = ctx.binding
        if probe is None:
            return Decision("ERROR", "DECISION_EXCEPTION", detail="binding probe unavailable")
        if probe.binding_status is None:
            return Decision("OUT_OF_SCOPE", "STATEMENT_DOES_NOT_MATCH_CONTROLLED_VOCABULARY")
        if probe.binding_status == "NEEDS_REVIEW":
            return Decision("NEEDS_REVIEW", "CONTROLLED_VOCAB_MATCHED_CANONICAL_NOT_RECOVERED")
        if (
            probe.binding_status == "BOUND"
            and probe.bound_candidate is not None
            and probe.bound_candidate.get("content_code") == canonical_content_code
            and probe.recovered_has_canonical
        ):
            if not probe.literal_evidence_term:
                return Decision("NEEDS_REVIEW", "BINDING_BOUND_BUT_NO_LITERAL_EVIDENCE")
            return Decision(
                "READY_FOR_INITIAL",
                "DETERMINISTIC_BINDING_BOUND",
                planned_write=PlannedWrite(
                    question_version_id=ctx.question_version.id,
                    target_taxonomy_version=target_taxonomy,
                    classifier_version=profile.classifier_version,
                    prompt_version=profile.prompt_version,
                    bound_candidate=dict(probe.bound_candidate),
                    evidence_term=probe.literal_evidence_term,
                ),
            )
        # D9 ambiguous
        return Decision("NEEDS_REVIEW", "BINDING_AMBIGUOUS", detail=f"status={probe.binding_status!r}")

    except Exception as exc:  # D10
        return Decision("ERROR", "DECISION_EXCEPTION", detail=f"{type(exc).__name__}: {exc}")
