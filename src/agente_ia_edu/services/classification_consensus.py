"""Formal consensus & plausibility gate for AI-proposed curriculum classification.

    THE AI PROPOSES.  THE SYSTEM DECIDES.

PHASE 11.19 makes the safety behaviour that was proven operationally in PHASE
11.5-A3 and PHASE 11.14 a first-class, provider-independent part of the system:
run the existing single-run proposal pipeline N times and admit a CLASSIFIED
verdict only when every run agrees deterministically.

This layer CONSOLIDATES results. It does not re-implement candidate recovery,
evidence validation, taxonomy/hierarchy validation, lifecycle rules, confidence
mapping or review-reason logic - all of that already runs inside
``ClassificationProposalService.propose_with_provider`` and is reused unchanged.
It knows nothing about any specific AI vendor or SDK: it depends only on the
``TextGenerationProvider`` contract and the neutral ``ProviderError`` hierarchy.

Nothing here persists: every run executes in its own transaction that is rolled
back (the PHASE 11.5-A3 isolation technique), so a consensus evaluation performs
ZERO database writes regardless of outcome.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from agente_ia_edu.providers.contracts import TextGenerationProvider
from agente_ia_edu.providers.errors import ProviderError
from agente_ia_edu.services.curriculum_classification import ClassificationProposalService

DEFAULT_N = 3
DEFAULT_REQUIRED_CONFIDENCE = "HIGH"
_CONFIDENCE_BANDS = ("HIGH", "MEDIUM", "LOW")
_SECRET_RE = re.compile(r"(?:sk|sk-proj)-[A-Za-z0-9_-]+")


def _scrub(value: object) -> str:
    text = "" if value is None else str(value)
    text = _SECRET_RE.sub("[REDACTED]", text)
    text = re.sub(r"postgres(?:ql)?(?:\+[^:]+)?://[^\s]+", "[REDACTED]", text, flags=re.I)
    return text


@dataclass(frozen=True)
class ConsensusPolicy:
    """Explicit, testable acceptance policy. Default = the proven N=3 HIGH gate."""

    n: int = DEFAULT_N
    required_confidence: str = DEFAULT_REQUIRED_CONFIDENCE

    def __post_init__(self) -> None:
        if self.n < 1:
            raise ValueError("ConsensusPolicy.n must be >= 1")
        if self.required_confidence not in _CONFIDENCE_BANDS:
            raise ValueError(
                f"ConsensusPolicy.required_confidence must be one of {_CONFIDENCE_BANDS}"
            )


DEFAULT_CONSENSUS_POLICY = ConsensusPolicy()


@dataclass(frozen=True)
class ProposalRun:
    """Provider-independent summary of ONE proposal outcome.

    ``outcome`` is one of CLASSIFIED / HUMAN_REVIEW / CURRICULUM_GAP /
    PROVIDER_ERROR. Anything that is not a clean CLASSIFIED fails the consensus
    gate closed.
    """

    outcome: str
    content_code: str | None = None
    confidence: str | None = None
    discipline_code: str | None = None
    review_reason: str | None = None
    gap_type: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ConsensusOutcome:
    verdict: str  # "CLASSIFIED" | "HUMAN_REVIEW"
    content_code: str | None
    confidence: str | None
    n: int
    reason: str
    runs: tuple[ProposalRun, ...] = field(default_factory=tuple)


def classify_run(record) -> ProposalRun:
    """Map a persisted-shape ``PedagogicalClassification`` record to a ``ProposalRun``.

    Uses only the fields the decision core already populated - it does not add or
    change any rule.
    """
    md = record.metadata_ or {}
    gap = (
        bool(md.get("catalog_gap"))
        or md.get("gap_type") is not None
        or md.get("review_reason") in {"CATALOG_GAP", "TAXONOMY_GRANULARITY_GAP"}
    )
    if gap:
        return ProposalRun(
            outcome="CURRICULUM_GAP",
            content_code=md.get("content_code"),
            confidence=md.get("confidence_band"),
            discipline_code=md.get("discipline_code"),
            review_reason=md.get("review_reason"),
            gap_type=md.get("gap_type"),
        )
    if (
        md.get("proposal_status") == "NEEDS_REVIEW"
        or record.status == "NEEDS_REVIEW"
        or md.get("review_reason")
    ):
        return ProposalRun(
            outcome="HUMAN_REVIEW",
            content_code=md.get("content_code"),
            confidence=md.get("confidence_band"),
            discipline_code=md.get("discipline_code"),
            review_reason=md.get("review_reason"),
        )
    if md.get("content_code"):
        return ProposalRun(
            outcome="CLASSIFIED",
            content_code=md.get("content_code"),
            confidence=md.get("confidence_band"),
            discipline_code=md.get("discipline_code"),
        )
    return ProposalRun(outcome="HUMAN_REVIEW", confidence=md.get("confidence_band"))


def consolidate_consensus(
    runs: Sequence[ProposalRun],
    policy: ConsensusPolicy = DEFAULT_CONSENSUS_POLICY,
) -> ConsensusOutcome:
    """Apply the acceptance policy to N proposal runs. Fails closed to HUMAN_REVIEW.

    CLASSIFIED requires, for EVERY run: outcome == CLASSIFIED, the same non-null
    CONTENT code, and confidence == policy.required_confidence. Any
    CURRICULUM_GAP, HUMAN_REVIEW, PROVIDER_ERROR, missing/mismatched content, or
    below-policy confidence in any run -> HUMAN_REVIEW. Never forces a
    classification.
    """
    runs = tuple(runs)
    if len(runs) != policy.n:
        return ConsensusOutcome(
            "HUMAN_REVIEW", None, None, policy.n,
            f"expected {policy.n} runs, received {len(runs)}", runs,
        )
    non_classified = [r for r in runs if r.outcome != "CLASSIFIED"]
    if non_classified:
        kinds = sorted({r.outcome for r in non_classified})
        return ConsensusOutcome(
            "HUMAN_REVIEW", None, None, policy.n,
            f"fail-closed: {len(non_classified)}/{policy.n} run(s) not CLASSIFIED ({', '.join(kinds)})",
            runs,
        )
    contents = {r.content_code for r in runs}
    if len(contents) != 1 or None in contents:
        return ConsensusOutcome(
            "HUMAN_REVIEW", None, None, policy.n,
            f"content disagreement across runs: {sorted(c for c in contents if c is not None)}",
            runs,
        )
    off_policy = [r for r in runs if r.confidence != policy.required_confidence]
    if off_policy:
        seen = sorted({r.confidence for r in runs})
        return ConsensusOutcome(
            "HUMAN_REVIEW", None, None, policy.n,
            f"confidence below policy {policy.required_confidence}: runs reported {seen}",
            runs,
        )
    content = next(iter(contents))
    return ConsensusOutcome(
        "CLASSIFIED", content, policy.required_confidence, policy.n,
        f"unanimous {policy.n}/{policy.n} CLASSIFIED {policy.required_confidence} -> {content}",
        runs,
    )


_PROVIDER_EXC = (ProviderError,)


async def _one_isolated_run(
    session_factory,
    question_version_id: UUID,
    provider: TextGenerationProvider,
    *,
    classifier_version: str,
    taxonomy_version: str,
    prompt_version: str,
    classification_mode: str,
    target_content_code: str | None,
    confidence_threshold: float,
) -> ProposalRun:
    """Run the UNMODIFIED proposal pipeline once inside a rolled-back transaction.

    ``propose_with_provider`` commits internally, so ``session.commit`` is rebound
    to ``session.flush`` for the duration and the transaction is always rolled
    back afterwards - zero writes.
    """
    async with session_factory() as session:
        original_commit = session.commit
        session.commit = session.flush  # type: ignore[method-assign]
        await session.begin()
        try:
            service = ClassificationProposalService(session, confidence_threshold=confidence_threshold)
            try:
                if classification_mode == "INITIAL":
                    record = await service.classify_initial_with_provider(
                        question_version_id, provider,
                        target_taxonomy_version=taxonomy_version,
                        classifier_version=classifier_version,
                        prompt_version=prompt_version,
                        target_content_code=target_content_code,
                    )
                else:
                    record = await service.propose_with_provider(
                        question_version_id, provider,
                        classifier_version=classifier_version,
                        taxonomy_version=taxonomy_version,
                        prompt_version=prompt_version,
                        classification_mode=classification_mode,
                    )
                return classify_run(record)
            except _PROVIDER_EXC as exc:
                return ProposalRun(outcome="PROVIDER_ERROR", error=_scrub(exc))
            except ValueError as exc:
                # decision-core rejection: invalid candidate / evidence / hierarchy / gap mismatch
                return ProposalRun(outcome="HUMAN_REVIEW", error=_scrub(exc))
        finally:
            session.commit = original_commit  # type: ignore[method-assign]
            if session.in_transaction():
                await session.rollback()


async def run_classification_consensus(
    *,
    question_version_id: UUID,
    provider: TextGenerationProvider,
    session_factory,
    classifier_version: str,
    taxonomy_version: str,
    prompt_version: str,
    policy: ConsensusPolicy = DEFAULT_CONSENSUS_POLICY,
    classification_mode: str = "STANDARD",
    target_content_code: str | None = None,
    confidence_threshold: float = 0.8,
) -> ConsensusOutcome:
    """Evaluate the consensus gate for one question version.

    Runs the existing single-run proposal pipeline ``policy.n`` times, each in its
    own rolled-back transaction, then applies :func:`consolidate_consensus`.
    Performs ZERO database writes. ``policy.n == 1`` runs the pipeline once and
    reports that single run's own verdict (all its deterministic gates still
    apply); the standalone ``ClassificationProposalService`` API is unchanged and
    still usable directly for callers that want to persist.
    """
    runs: list[ProposalRun] = []
    for index in range(policy.n):
        runs.append(
            await _one_isolated_run(
                session_factory,
                question_version_id,
                provider,
                classifier_version=f"{classifier_version}::consensus-r{index}",
                taxonomy_version=taxonomy_version,
                prompt_version=prompt_version,
                classification_mode=classification_mode,
                target_content_code=target_content_code,
                confidence_threshold=confidence_threshold,
            )
        )
    return consolidate_consensus(runs, policy)


__all__ = [
    "ConsensusOutcome",
    "ConsensusPolicy",
    "DEFAULT_CONSENSUS_POLICY",
    "DEFAULT_N",
    "DEFAULT_REQUIRED_CONFIDENCE",
    "ProposalRun",
    "classify_run",
    "consolidate_consensus",
    "run_classification_consensus",
]
