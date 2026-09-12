"""AI-agnostic classification entrypoint - the thinnest production-callable path.

    THE AI PROPOSES.  THE SYSTEM DECIDES.  THE PROVIDER IS REPLACEABLE.

This facade composes the already-built, already-tested pieces and adds NOTHING of
its own beyond wiring:

    build_text_provider()                     (PHASE 11.17 - vendor selected by AI_PROVIDER)
        -> get_classification_prompt()        (PHASE 11.18 - system-owned versioned prompt)
        -> run_classification_consensus()     (PHASE 11.19 - N-run unanimity + HIGH gate)
        -> ConsensusOutcome                   (the system's verdict; NOT persisted here)

Persistence is a SEPARATE, explicit operation (:meth:`persist_classified_consensus`)
that reuses the existing deterministic ``ClassificationProposalService.propose``
path. A proposal / consensus evaluation NEVER persists - not even when the verdict
is CLASSIFIED.

This module imports no vendor SDK, no OpenAIProvider, no adapter. A different
provider is selected purely through the ``AI_PROVIDER`` environment variable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from agente_ia_edu.classification_prompts import get_classification_prompt
from agente_ia_edu.providers.contracts import TextGenerationProvider
from agente_ia_edu.providers.factory import build_text_provider
from agente_ia_edu.services.classification_consensus import (
    ConsensusOutcome,
    ConsensusPolicy,
    DEFAULT_CONSENSUS_POLICY,
    run_classification_consensus,
)
from agente_ia_edu.services.curriculum_classification import (
    ClassificationProposal,
    ClassificationProposalService,
)

_DEFAULT_DIFFICULTY = "UNKNOWN"
_DEFAULT_PERSIST_CONFIDENCE = 0.9


class AiAgnosticClassificationService:
    """Compose provider factory + versioned prompt + consensus gate.

    ``session_factory`` is an async session maker (each consensus run opens and
    rolls back its own session - zero writes). ``provider`` overrides the factory
    (tests inject a FakeProvider); production leaves it ``None`` so the concrete
    backend is chosen by ``AI_PROVIDER`` at call time.
    """

    def __init__(
        self,
        session_factory,
        *,
        provider: TextGenerationProvider | None = None,
        policy: ConsensusPolicy = DEFAULT_CONSENSUS_POLICY,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._policy = policy

    @property
    def classification_prompt_version(self) -> str:
        """The system-owned prompt artifact version this service uses."""
        return get_classification_prompt().version

    def _resolve_provider(self) -> TextGenerationProvider:
        return self._provider if self._provider is not None else build_text_provider()

    async def propose_and_audit(
        self,
        question_version_id: UUID,
        *,
        classifier_version: str,
        taxonomy_version: str,
        prompt_version: str | None = None,
        policy: ConsensusPolicy | None = None,
        classification_mode: str = "STANDARD",
        target_content_code: str | None = None,
        confidence_threshold: float = 0.8,
    ) -> ConsensusOutcome:
        """Run the AI proposal + system consensus. NEVER persists.

        Returns the system's :class:`ConsensusOutcome`. The provider comes from
        the factory; the prompt is the versioned artifact; the consensus rules are
        the existing ``run_classification_consensus`` (default: N=3 + HIGH + same
        CONTENT + all deterministic gates pass). Any GAP / HUMAN_REVIEW / provider
        error / disagreement -> verdict HUMAN_REVIEW; a classification is never
        forced.
        """
        return await run_classification_consensus(
            question_version_id=question_version_id,
            provider=self._resolve_provider(),
            session_factory=self._session_factory,
            classifier_version=classifier_version,
            taxonomy_version=taxonomy_version,
            prompt_version=prompt_version or self.classification_prompt_version,
            policy=policy or self._policy,
            classification_mode=classification_mode,
            target_content_code=target_content_code,
            confidence_threshold=confidence_threshold,
        )

    async def persist_classified_consensus(
        self,
        session,
        outcome: ConsensusOutcome,
        question_version_id: UUID,
        *,
        evidence: Sequence[Mapping[str, Any]],
        classifier_version: str,
        taxonomy_version: str,
        provider_label: str,
        model_label: str,
        prompt_version: str | None = None,
        context: str = "",
        difficulty: str = _DEFAULT_DIFFICULTY,
        confidence: float = _DEFAULT_PERSIST_CONFIDENCE,
        confirm: bool = False,
    ):
        """EXPLICIT, curator-authorised persistence of a CLASSIFIED consensus.

        This is the only write path. It refuses to run unless ``confirm=True`` and
        the outcome is a CLASSIFIED verdict with a content code. It performs NO
        validation or persistence of its own - it hands a
        :class:`ClassificationProposal` to the existing deterministic
        :meth:`ClassificationProposalService.propose`, which re-checks that the
        target node is an ACTIVE catalog code and that every evidence ``text`` is
        a literal substring of the question statement, then writes one ACTIVE row.

        ``evidence`` is supplied by the caller/curator: a sequence of mappings
        with a ``text`` (verbatim statement excerpt) and optional ``reason``.
        """
        if not confirm:
            raise ValueError(
                "persist_classified_consensus requires confirm=True (explicit curator authorisation); "
                "a CLASSIFIED consensus is never persisted automatically"
            )
        if outcome.verdict != "CLASSIFIED" or not outcome.content_code:
            raise ValueError(
                f"cannot persist a non-CLASSIFIED consensus outcome (verdict={outcome.verdict!r}, "
                f"content_code={outcome.content_code!r})"
            )
        if not evidence:
            raise ValueError("persist_classified_consensus requires at least one evidence excerpt")

        proposal = ClassificationProposal(
            primary_content_code=outcome.content_code,
            complementary_content_codes=[],
            concepts=[],
            prerequisites=[],
            cognitive_operations=[],
            context=context,
            difficulty=difficulty,
            confidence=confidence,
            evidence=[
                {
                    "content_code": outcome.content_code,
                    "text": item["text"],
                    "reason": item.get("reason", ""),
                }
                for item in evidence
            ],
        )
        return await ClassificationProposalService(session).propose(
            question_version_id,
            proposal,
            classifier_version=classifier_version,
            taxonomy_version=taxonomy_version,
            provider=provider_label,
            model=model_label,
            prompt_version=prompt_version or self.classification_prompt_version,
        )


__all__ = ["AiAgnosticClassificationService"]
