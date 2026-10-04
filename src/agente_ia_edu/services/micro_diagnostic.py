"""MICRO-DIAGNOSTIC - the short, per-content readiness check.

    learning path   = AdaptiveLearningPathService   (decision: what is blocked)
    practice        = AdaptivePracticeService       (execution: question sets)
    micro-diagnostic= this module                   (one question: can he go?)

WHAT THIS IS FOR, AND NOTHING ELSE
===================================
The school assigns an activity. The planner says the student has
INSUFFICIENT_EVIDENCE on the content that activity requires - he has simply
never answered a question about it. We do not know whether he is ready.

Two wrong answers to that:
  - send him into the activity blind, and let him fail to discover it;
  - send him to study a prerequisite he may not need.

So we ask three questions and find out. That is the entire purpose. The result
is a DECISION, never a score.

WHY THERE IS NO NEW ENGINE HERE
================================
Every hard part already existed and is reused untouched:

    AdaptivePracticeService   selection, list building, distribution
    PracticeSelectionPolicy   deterministic order, visual/protected exclusion,
                              de-prioritising recently answered questions
    PerformanceThresholdPolicy  how much evidence is enough
    CurriculumDomainMapService  where the evidence lands
    AdaptiveLearningPathService prerequisites and content state

This module contributes three things that did not exist: an evidence ORIGIN of
its own, a STOPPING RULE, and the DECISION.

IT IS NOT THE InitialDiagnostic
================================
That one is the single onboarding session that estimates the student's whole
mastery map. This looks at ONE content and answers ONE question. Reusing it
would have meant running a full map estimation to decide whether to open a
chemistry activity.

THE NUMBER THREE IS NOT WRITTEN HERE
=====================================
How many answers are needed before evidence stops being "insufficient" is
PerformanceThresholdPolicy.min_sample_size. This module asks. If that policy
changes, the micro-diagnostic follows without an edit here - which is the
point, because a second copy of that number is a second definition of what
counts as knowing something.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.services.adaptive_practice import (
    AdaptivePracticeService,
    PracticeError,
)
from agente_ia_edu.services.curriculum_domain_map import ORIGIN_MICRO_DIAGNOSTIC
from agente_ia_edu.services.pedagogical_analysis import (
    BAND_IMPROVEMENT,
    BAND_INSUFFICIENT,
    BAND_NO_DATA,
    BAND_STRONG,
    PerformanceThresholdPolicy,
)
from agente_ia_edu.services.question_list_store import Requester

# The three outcomes. A micro-diagnostic always ends in exactly one of them,
# and INSUFFICIENT_EVIDENCE is a real answer, not a failure mode.
DECISION_PROCEED = "PROCEED_TO_ACTIVITY"
DECISION_PREPARE = "PREPARE_PREREQUISITE"
DECISION_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

# Shown to the student. Deliberately contains no form of "prova", "nota" or
# "avaliação" - he is not being assessed, and saying so in the title is the
# cheapest way to keep that true in his head.
TITULO = "Vamos ver onde você está"
INSTRUCOES = (
    "Três perguntas rápidas, só para eu descobrir por onde te ajudar. "
    "Isto não vale ponto e ninguém da escola vê como desempenho."
)


class MicroDiagnosticService:
    """Decides whether the student can go straight to the school task."""

    def __init__(self, session: AsyncSession, *,
                 practice: AdaptivePracticeService | None = None,
                 thresholds: PerformanceThresholdPolicy | None = None) -> None:
        self._session = session
        self._practice = practice or AdaptivePracticeService(session)
        self._thresholds = thresholds or PerformanceThresholdPolicy.default()

    # -- the stopping rule --------------------------------------------------

    def evidencia_suficiente(self, *, answered: int, accuracy: float | None) -> bool:
        """Is there already enough evidence to decide, under the CURRENT policy?

        This is what makes early stopping structural rather than hard-coded.
        Today the policy needs 3 answers, so it never fires before the third -
        but nothing here encodes "three". Loosen the policy and the
        micro-diagnostic stops earlier on its own.
        """
        banda = self._thresholds.band(answered=answered, accuracy=accuracy)
        return banda not in (BAND_INSUFFICIENT, BAND_NO_DATA)

    @property
    def tamanho(self) -> int:
        """How many questions to ask: the smallest sample the policy accepts."""
        return self._thresholds.min_sample_size

    # -- start --------------------------------------------------------------

    async def start(self, student_external_id: str, *, requester: Requester,
                    content_code: str) -> dict:
        """Build the check. Never fabricates a conclusion.

        When the bank cannot supply enough suitable questions for this content,
        the honest answer is that we still do not know - so it returns
        INSUFFICIENT_EVIDENCE with the counts, instead of diagnosing from one
        question or silently shrinking the sample.
        """
        pedido = self.tamanho
        try:
            criado = await self._practice.create_practice(
                student_external_id,
                requester=requester,
                content_code=content_code,
                question_count=pedido,
                origin=ORIGIN_MICRO_DIAGNOSTIC,
                metadata_extra={"micro_diagnostic": True},
                title=TITULO,
                instructions=INSTRUCOES,
            )
        except PracticeError as exc:
            relatorio = dict(getattr(exc, "payload", None) or {})
            relatorio.setdefault("available_questions", 0)
            relatorio.setdefault("requested_questions", pedido)
            return {
                "content_code": content_code,
                "question_count": pedido,
                "origin": ORIGIN_MICRO_DIAGNOSTIC,
                "sufficient": False,
                "decision": DECISION_INSUFFICIENT,
                "assignment_id": None,
                "reason": str(exc),
                "selection": relatorio,
                "title": TITULO,
                "instructions": INSTRUCOES,
            }

        return {
            "content_code": content_code,
            "question_count": criado["question_count"],
            "origin": criado["origin"],
            "sufficient": True,
            "decision": None,              # só depois das respostas
            "assignment_id": criado["assignment_id"],
            "diagnostic_id": criado["assignment_id"],
            "selection": criado["selection"],
            "title": criado["title"],
            "instructions": criado["instructions"],
        }

    # -- decide -------------------------------------------------------------

    def decidir(self, *, answered: int, accuracy: float | None,
                prerequisito_em_falta: str | None = None) -> dict:
        """Turn the evidence into one of the three outcomes.

        The bands come from PerformanceThresholdPolicy - no cut-off is decided
        here. PONTO_FORTE and DESEMPENHO_INTERMEDIARIO both mean "go": the
        micro-diagnostic asks whether he can START the activity, not whether he
        has mastered the content. Only weak evidence sends him to prepare, and
        only when there is in fact a prerequisite to prepare.
        """
        banda = self._thresholds.band(answered=answered, accuracy=accuracy)
        if banda in (BAND_INSUFFICIENT, BAND_NO_DATA):
            decisao = DECISION_INSUFFICIENT
        elif banda == BAND_IMPROVEMENT and prerequisito_em_falta:
            decisao = DECISION_PREPARE
        else:
            decisao = DECISION_PROCEED
        return {
            "decision": decisao,
            "band": banda,
            "answered": answered,
            "accuracy": accuracy,
            "prerequisite_code": prerequisito_em_falta if decisao == DECISION_PREPARE else None,
            # o aluno nao ve isto; serve para o professor entender a decisao
            "policy": self._thresholds.as_dict(),
        }


__all__ = [
    "MicroDiagnosticService",
    "DECISION_PROCEED",
    "DECISION_PREPARE",
    "DECISION_INSUFFICIENT",
    "BAND_STRONG",
]
