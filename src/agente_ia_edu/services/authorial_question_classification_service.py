"""PHASE 30 - Authorial Question Pedagogical Classification Engine.

Per the mandatory pre-implementation audit (spec s1/s44): ``catalog_nodes``
and ``pedagogical_classifications`` already implement almost everything this
phase needs - a curriculum tree with active/inactive nodes and stable codes,
and a classification row with confidence, status (CLASSIFIED/NEEDS_REVIEW/
DRAFT), an ACTIVE/SUPERSEDED lifecycle, and classifier/prompt/provider
versioning. ``curriculum_classification.ClassificationProposalService`` (the
PHASE 9-11 engine already in production for OFFICIAL questions) already
implements the mandated pipeline (spec s5): deterministic candidate
generation against the WHOLE active catalog -> an AI call constrained to
choosing among those candidates -> full post-AI validation against the
catalog (existence, hierarchy, evidence-is-a-literal-substring) -> confidence
banding -> CLASSIFIED/NEEDS_REVIEW. This module is a THIN orchestration
layer around that exact engine for AUTHORIAL questions (never a second
taxonomy, never a rewritten classifier - spec s4) plus the two things that
engine does not yet do:

1. Difficulty is a SEPARATE axis (spec s12/s13) - not part of the official
   engine's output today (it always persists "UNKNOWN"). This module makes
   one additional, independently-confidence-scored AI call for it.
2. A human review/approval/reclassification audit trail (spec s27/s29) -
   the one genuinely new table this phase adds (migration 038).

Never calls the AI provider anywhere else in the platform (spec s42) - this
module and ``curriculum_classification.py`` are the only two importers of
provider machinery for classification purposes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    CatalogNode,
    PedagogicalClassification,
    PedagogicalClassificationReview,
    Question,
    QuestionOption,
    QuestionVersion,
)
from ..providers.contracts import TextGenerationProvider
from ..providers.errors import ProviderError
from ..providers.models import TextGenerationRequest
from .authorial_classification_policy import (
    DIFFICULTY_VALUES,
    UNKNOWN_DIFFICULTY,
    confidence_band,
)
from .curriculum_classification import ClassificationProposalService

CLASSIFIER_VERSION = "authorial-classifier-v1"
# The SAME catalog official questions are classified against, tagged with
# the SAME taxonomy_version string the engine already uses for its current
# 19-content generation (spec s4: "questões autorais e oficiais devem
# utilizar os mesmos códigos curriculares") - never a parallel taxonomy.
TAXONOMY_VERSION = "curriculum-v2"
PROMPT_VERSION = "v1"
# Difficulty is a NEW axis this phase adds (spec s12) - its own small,
# versioned prompt, kept OUT of the shared classification_prompts package
# (that package is the OFFICIAL content-classification artifact; touching
# it would risk the official pipeline for a feature it doesn't need).
DIFFICULTY_PROMPT_VERSION = "authorial-difficulty-v1"

_PROVIDER_FAILURE_EXCEPTIONS = (ProviderError,)


class ClassificationValidationError(ValueError):
    """CLASSIFICATION_INVALID (spec s8/s9): a manual or curriculum-code
    input failed hierarchy/existence/active-status validation. Never
    persisted as an approved classification."""


@dataclass
class ClassificationOutcome:
    classification: PedagogicalClassification
    status: str
    review_reason: str | None
    ai_calls: int
    cache_hit: bool
    error: str | None = None


@dataclass
class BatchClassificationResult:
    questions_processed: int = 0
    classified: int = 0
    needs_review: int = 0
    errors: int = 0
    ai_calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    elapsed_s: float = 0.0
    outcomes: list[dict] = field(default_factory=list)


def _build_difficulty_prompt(statement: str, options: list[dict]) -> str:
    options_text = (
        "\n".join(f"{o['key']}) {o['text']}" for o in options)
        if options else "(questão discursiva, sem alternativas)"
    )
    return (
        "Avalie a dificuldade desta questão considerando: complexidade "
        "conceitual, número de etapas de raciocínio, necessidade de cálculo, "
        "interpretação exigida, integração de múltiplos conceitos, "
        "quantidade de informações e qualidade dos distratores (quando "
        "houver alternativas). NÃO use o tamanho do enunciado isoladamente "
        "como critério.\n\n"
        f"Enunciado:\n{statement}\n\nAlternativas:\n{options_text}\n\n"
        "Responda em JSON estrito, sem nenhum texto fora do JSON, com "
        "exatamente estas chaves: "
        '{"difficulty": "EASY"|"MEDIUM"|"HARD", "confidence": <numero de 0 a 1>, '
        '"reasoning": "<justificativa curta em portugues>"}'
    )


class AuthorialQuestionClassificationService:
    def __init__(self, session: AsyncSession, *, proposal_service: ClassificationProposalService | None = None):
        self.session = session
        self.proposal_service = proposal_service or ClassificationProposalService(session)

    # ------------------------------------------------------------------
    # READ
    # ------------------------------------------------------------------
    async def get_active_classification(self, question_version_id: UUID) -> PedagogicalClassification | None:
        return await self._get_active_any_classifier(question_version_id)

    async def get_history(self, question_version_id: UUID) -> list[PedagogicalClassificationReview]:
        classification_ids = list((await self.session.scalars(
            select(PedagogicalClassification.id).where(
                PedagogicalClassification.question_version_id == question_version_id)
        )).all())
        if not classification_ids:
            return []
        return list((await self.session.scalars(
            select(PedagogicalClassificationReview)
            .where(PedagogicalClassificationReview.pedagogical_classification_id.in_(classification_ids))
            .order_by(PedagogicalClassificationReview.created_at)
        )).all())

    async def list_needs_review(self, *, school_id: UUID | None) -> list[PedagogicalClassification]:
        q = (
            select(PedagogicalClassification)
            .join(QuestionVersion, QuestionVersion.id == PedagogicalClassification.question_version_id)
            .join(Question, Question.id == QuestionVersion.question_id)
            .where(
                PedagogicalClassification.status == "NEEDS_REVIEW",
                PedagogicalClassification.lifecycle == "ACTIVE",
            )
        )
        if school_id is not None:
            q = q.where(Question.school_id == school_id)
        return list((await self.session.scalars(q.order_by(PedagogicalClassification.created_at))).all())

    async def _get_active_any_classifier(self, question_version_id: UUID) -> PedagogicalClassification | None:
        rows = list((await self.session.scalars(
            select(PedagogicalClassification).where(
                PedagogicalClassification.question_version_id == question_version_id,
                PedagogicalClassification.lifecycle == "ACTIVE",
            )
        )).all())
        for row in rows:
            if (row.metadata_ or {}).get("taxonomy_version") == TAXONOMY_VERSION:
                return row
        return None

    # ------------------------------------------------------------------
    # CLASSIFY (spec s5-s9): candidate generation -> AI selection among
    # candidates -> curriculum validation -> confidence -> CLASSIFIED/
    # NEEDS_REVIEW, entirely inside the reused engine; this method adds the
    # cache check, difficulty axis, and failure-never-crashes wrapping.
    # ------------------------------------------------------------------
    async def classify_question_version(
        self, question_version_id: UUID, provider: TextGenerationProvider, *, actor: str,
    ) -> ClassificationOutcome:
        cached = await self._get_active_this_classifier(question_version_id)
        if cached is not None:
            return self._cache_hit_outcome(cached)
        return await self._classify_uncached(question_version_id, provider, actor=actor)

    @staticmethod
    def _cache_hit_outcome(cached: PedagogicalClassification) -> ClassificationOutcome:
        return ClassificationOutcome(
            classification=cached, status=cached.status,
            review_reason=(cached.metadata_ or {}).get("review_reason"),
            ai_calls=0, cache_hit=True,
        )

    async def _classify_uncached(
        self, question_version_id: UUID, provider: TextGenerationProvider, *, actor: str,
    ) -> ClassificationOutcome:
        """The AI-calling path of `classify_question_version`, factored out so
        `batch_classify` can skip straight here for ids its own bulk cache
        check (`_get_active_this_classifier_bulk`) already proved are NOT
        cached, instead of re-running the single-item cache-check query."""
        try:
            record = await self.proposal_service.propose_with_provider(
                question_version_id, provider, classifier_version=CLASSIFIER_VERSION,
                taxonomy_version=TAXONOMY_VERSION, prompt_version=PROMPT_VERSION,
            )
        except _PROVIDER_FAILURE_EXCEPTIONS as exc:
            record = await self._persist_failure(
                question_version_id, reason="CLASSIFIER_UNAVAILABLE", detail=str(exc), actor=actor)
            return ClassificationOutcome(
                classification=record, status="NEEDS_REVIEW", review_reason="CLASSIFIER_UNAVAILABLE",
                ai_calls=1, cache_hit=False, error=str(exc))
        except ValueError as exc:
            record = await self._persist_failure(
                question_version_id, reason="INVALID_AI_OUTPUT", detail=str(exc), actor=actor,
                diagnostic_output=getattr(exc, "diagnostic_output", None))
            return ClassificationOutcome(
                classification=record, status="NEEDS_REVIEW", review_reason="INVALID_AI_OUTPUT",
                ai_calls=1, cache_hit=False, error=str(exc))

        await self._assess_and_apply_difficulty(record, provider)
        await self._record_review_event(
            record, action="AI_CLASSIFY", actor=actor, actor_type="AI", new_value=self._snapshot(record))
        return ClassificationOutcome(
            classification=record, status=record.status,
            review_reason=(record.metadata_ or {}).get("review_reason"),
            ai_calls=2, cache_hit=False,  # one content-classification call + one difficulty call
        )

    async def _get_active_this_classifier(self, question_version_id: UUID) -> PedagogicalClassification | None:
        return await self.session.scalar(
            select(PedagogicalClassification).where(
                PedagogicalClassification.question_version_id == question_version_id,
                PedagogicalClassification.model_version == CLASSIFIER_VERSION,
                PedagogicalClassification.lifecycle == "ACTIVE",
                PedagogicalClassification.metadata_["taxonomy_version"].as_string() == TAXONOMY_VERSION,
            ).order_by(PedagogicalClassification.created_at.desc())
        )

    async def _get_active_this_classifier_bulk(
        self, question_version_ids: list[UUID],
    ) -> dict[UUID, PedagogicalClassification]:
        """Same filter as `_get_active_this_classifier`, batched: ONE query for
        an entire `batch_classify` call instead of one SELECT per question
        version id (real N+1 - a re-run of scripts/classify_remaining_questions.py
        over an already-classified corpus used to cost one cache-check SELECT
        per question just to discover it was a cache hit). `order_by(created_at
        .desc())` + first-wins-via-setdefault preserves the exact same
        "freshest ACTIVE row" tie-break the single-item query used."""
        if not question_version_ids:
            return {}
        rows = list((await self.session.scalars(
            select(PedagogicalClassification).where(
                PedagogicalClassification.question_version_id.in_(question_version_ids),
                PedagogicalClassification.model_version == CLASSIFIER_VERSION,
                PedagogicalClassification.lifecycle == "ACTIVE",
                PedagogicalClassification.metadata_["taxonomy_version"].as_string() == TAXONOMY_VERSION,
            ).order_by(PedagogicalClassification.created_at.desc())
        )).all())
        cache: dict[UUID, PedagogicalClassification] = {}
        for row in rows:
            cache.setdefault(row.question_version_id, row)
        return cache

    async def _persist_failure(
        self, question_version_id: UUID, *, reason: str, detail: str, actor: str,
        diagnostic_output: dict[str, Any] | None = None,
    ) -> PedagogicalClassification:
        """spec s16/s41 - a provider failure or an AI response this engine's
        own validation rejected NEVER crashes the system and NEVER invents a
        classification: the question is still made visible (NEEDS_REVIEW),
        with the exact reason, rather than silently disappearing."""
        metadata: dict[str, Any] = {
            "taxonomy_version": TAXONOMY_VERSION, "review_reason": reason,
            "error_detail": detail[:2000], "classification_mode": "STANDARD",
        }
        if diagnostic_output is not None:
            metadata["diagnostic_output"] = diagnostic_output
        record = PedagogicalClassification(
            question_version_id=question_version_id, discipline="", content="", subcontent="",
            difficulty=UNKNOWN_DIFFICULTY, classification_confidence=None, difficulty_confidence=None,
            reasoning_type="UNSPECIFIED", prerequisites=[], keywords=[], competencies=[], skills=[],
            model_name=None, model_version=CLASSIFIER_VERSION, prompt_version=PROMPT_VERSION, provider_name=None,
            input_tokens=None, output_tokens=None, total_tokens=None,
            status="NEEDS_REVIEW", source="ai", lifecycle="ACTIVE",
            metadata_=metadata,
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        await self._record_review_event(
            record, action="AI_CLASSIFY", actor=actor, actor_type="SYSTEM",
            new_value=self._snapshot(record), reason=detail[:500])
        return record

    async def _assess_and_apply_difficulty(
        self, record: PedagogicalClassification, provider: TextGenerationProvider,
    ) -> None:
        version = await self.session.get(QuestionVersion, record.question_version_id)
        options = list((await self.session.scalars(
            select(QuestionOption)
            .where(QuestionOption.question_version_id == record.question_version_id)
            .order_by(QuestionOption.position)
        )).all())
        statement = (version.statement or version.canonical_text) if version else ""
        prompt = _build_difficulty_prompt(statement, [{"key": o.option_key, "text": o.text} for o in options])
        metadata = dict(record.metadata_ or {})
        try:
            result = await provider.generate(TextGenerationRequest(prompt=prompt))
            payload = json.loads(result.text)
            if not isinstance(payload, dict):
                raise ValueError("difficulty response must be an object")
            difficulty = str(payload.get("difficulty", "")).upper()
            confidence = payload.get("confidence")
            reasoning = payload.get("reasoning")
            if difficulty not in DIFFICULTY_VALUES:
                raise ValueError("invalid difficulty value")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                raise ValueError("invalid difficulty confidence")
            if not isinstance(reasoning, str) or not reasoning.strip():
                raise ValueError("missing difficulty reasoning")
            record.difficulty = difficulty
            record.difficulty_confidence = Decimal(str(round(float(confidence), 4)))
            metadata["difficulty_reasoning"] = reasoning
            metadata["difficulty_model_confidence"] = float(confidence)
            metadata["difficulty_prompt_version"] = DIFFICULTY_PROMPT_VERSION
            if confidence_band(record.difficulty_confidence) == "LOW" and record.status == "CLASSIFIED":
                record.status = "NEEDS_REVIEW"
                metadata["review_reason"] = metadata.get("review_reason") or "DIFFICULTY_UNCERTAIN"
        except (json.JSONDecodeError, ValueError, *_PROVIDER_FAILURE_EXCEPTIONS) as exc:
            record.difficulty = UNKNOWN_DIFFICULTY
            record.difficulty_confidence = None
            metadata["difficulty_error"] = str(exc)[:500]
            if record.status == "CLASSIFIED":
                record.status = "NEEDS_REVIEW"
                metadata["review_reason"] = metadata.get("review_reason") or "DIFFICULTY_UNCERTAIN"
        record.metadata_ = metadata
        await self.session.commit()
        await self.session.refresh(record)

    # ------------------------------------------------------------------
    # BATCH (spec s22/s23/s43)
    # ------------------------------------------------------------------
    async def batch_classify(
        self, question_version_ids: list[UUID], provider: TextGenerationProvider, *, actor: str,
    ) -> BatchClassificationResult:
        t0 = time.perf_counter()
        out = BatchClassificationResult()
        cached_by_id = await self._get_active_this_classifier_bulk(question_version_ids)
        for qvid in question_version_ids:
            out.questions_processed += 1
            cached = cached_by_id.get(qvid)
            outcome = (
                self._cache_hit_outcome(cached) if cached is not None
                else await self._classify_uncached(qvid, provider, actor=actor)
            )
            out.ai_calls += outcome.ai_calls
            if outcome.cache_hit:
                out.cache_hits += 1
            else:
                out.cache_misses += 1
            if outcome.error:
                out.errors += 1
            elif outcome.status == "CLASSIFIED":
                out.classified += 1
            else:
                out.needs_review += 1
            out.outcomes.append({
                "question_version_id": str(qvid), "status": outcome.status,
                "review_reason": outcome.review_reason, "cache_hit": outcome.cache_hit,
            })
        out.elapsed_s = time.perf_counter() - t0
        return out

    # ------------------------------------------------------------------
    # MANUAL CLASSIFY / RECLASSIFY / APPROVE (spec s27-s29)
    # ------------------------------------------------------------------
    async def manual_classify(
        self, question_version_id: UUID, *, discipline_code: str | None, area_code: str | None,
        content_code: str, subcontent_code: str | None, difficulty: str, reason: str,
        actor: str, actor_type: str,
    ) -> PedagogicalClassification:
        if difficulty not in DIFFICULTY_VALUES:
            raise ClassificationValidationError(f"difficulty must be one of {DIFFICULTY_VALUES}")
        if not content_code:
            raise ClassificationValidationError("content_code is required")
        catalog = list((await self.session.scalars(
            select(CatalogNode).where(CatalogNode.active.is_(True))
        )).all())
        code_map = {node.code: node for node in catalog}
        path = {
            "discipline_code": discipline_code, "area_code": area_code,
            "content_code": content_code, "subcontent_code": subcontent_code,
        }
        if not ClassificationProposalService._valid_path(path, code_map):
            raise ClassificationValidationError(
                "CLASSIFICATION_INVALID: os códigos informados não formam uma hierarquia "
                "curricular válida e ativa")

        old = await self._get_active_any_classifier(question_version_id)
        if old is not None:
            old.lifecycle = "SUPERSEDED"
        new = PedagogicalClassification(
            question_version_id=question_version_id,
            discipline=discipline_code or "", content=content_code, subcontent=subcontent_code or "",
            difficulty=difficulty, classification_confidence=Decimal("1.0"), difficulty_confidence=Decimal("1.0"),
            reasoning_type="MANUAL", prerequisites=[], keywords=[], competencies=[], skills=[],
            model_name=None, model_version=CLASSIFIER_VERSION, prompt_version=None, provider_name=None,
            status="CLASSIFIED", source="human", lifecycle="ACTIVE",
            supersedes_id=old.id if old is not None else None,
            metadata_={
                "taxonomy_version": TAXONOMY_VERSION, "classification_mode": "MANUAL",
                "discipline_code": discipline_code, "area_code": area_code,
                "content_code": content_code, "subcontent_code": subcontent_code,
                "manual_reason": reason,
            },
        )
        self.session.add(new)
        await self.session.commit()
        await self.session.refresh(new)
        if old is not None:
            # `old` was loaded (and mutated in-place) before the commit above,
            # which expires every object in the session - refresh it too,
            # otherwise the snapshot below hits the same expired-attribute
            # MissingGreenlet against real Postgres.
            await self.session.refresh(old)
        await self._record_review_event(
            new, action="MANUAL_CLASSIFY", actor=actor, actor_type=actor_type,
            previous_value=self._snapshot(old), new_value=self._snapshot(new), reason=reason)
        return new

    async def reclassify(
        self, question_version_id: UUID, provider: TextGenerationProvider, *,
        actor: str, actor_type: str, reason: str,
    ) -> PedagogicalClassification:
        """spec s27 - EXPLICIT only: never triggered by classify_question_version's
        own cache check. Always makes a FRESH AI call (a unique prompt_version
        discriminator busts the shared engine's own input-hash cache) - a
        reclassify that returned the exact previous answer unchanged would
        defeat the entire point of asking again."""
        old = await self._get_active_any_classifier(question_version_id)
        if old is None:
            raise ValueError("no active classification exists for this question version to reclassify")
        old.lifecycle = "SUPERSEDED"
        await self.session.flush()
        discriminator = f"{PROMPT_VERSION}+reclassify-{uuid4().hex[:8]}"
        try:
            record = await self.proposal_service.propose_with_provider(
                question_version_id, provider, classifier_version=CLASSIFIER_VERSION,
                taxonomy_version=TAXONOMY_VERSION, prompt_version=discriminator,
                reclassification_audit={
                    "previous_classification_id": str(old.id), "reason": reason, "actor": actor,
                },
            )
        except Exception:
            old.lifecycle = "ACTIVE"
            await self.session.commit()
            raise
        if record.id == old.id:  # pragma: no cover - defensive, see engine docstring
            old.lifecycle = "ACTIVE"
            await self.session.commit()
            raise ValueError("reclassification resolved to the same row being superseded")
        record.supersedes_id = old.id
        await self._assess_and_apply_difficulty(record, provider)
        await self.session.commit()
        await self.session.refresh(record)
        await self.session.refresh(old)
        await self._record_review_event(
            record, action="RECLASSIFY", actor=actor, actor_type=actor_type,
            previous_value=self._snapshot(old), new_value=self._snapshot(record), reason=reason)
        return record

    async def approve_classification(
        self, classification_id: UUID, *, actor: str, actor_type: str, reason: str | None = None,
    ) -> PedagogicalClassification:
        record = await self.session.get(PedagogicalClassification, classification_id)
        if record is None:
            raise LookupError("classification not found")
        if record.lifecycle != "ACTIVE":
            raise ValueError("only an ACTIVE classification can be approved")
        record.metadata_ = {
            **(record.metadata_ or {}), "human_approved": True, "approved_by": actor,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.session.commit()
        await self.session.refresh(record)
        await self._record_review_event(
            record, action="APPROVE", actor=actor, actor_type=actor_type,
            new_value=self._snapshot(record), reason=reason)
        return record

    # ------------------------------------------------------------------
    async def _record_review_event(
        self, record: PedagogicalClassification, *, action: str, actor: str, actor_type: str,
        new_value: dict | None = None, previous_value: dict | None = None, reason: str | None = None,
    ) -> None:
        event = PedagogicalClassificationReview(
            pedagogical_classification_id=record.id, action=action, actor=actor, actor_type=actor_type,
            previous_value=previous_value, new_value=new_value or self._snapshot(record),
            reason=reason, classifier_version=record.model_version,
        )
        self.session.add(event)
        await self.session.commit()
        # This commit expires every ORM object already loaded in the session
        # (expire_on_commit=True in production - see db/session.py), including
        # `record`, which every caller of this method returns to its route
        # for response serialization right after. Without this refresh, the
        # very next attribute access on that returned object raises
        # MissingGreenlet against real Postgres (invisible under the test
        # suite's SQLite fixtures, which set expire_on_commit=False).
        await self.session.refresh(record)

    @staticmethod
    def _snapshot(record: PedagogicalClassification | None) -> dict | None:
        if record is None:
            return None
        meta = record.metadata_ or {}
        return {
            "discipline_code": meta.get("discipline_code") or record.discipline or None,
            "area_code": meta.get("area_code"),
            "content_code": meta.get("content_code") or record.content or None,
            "subcontent_code": meta.get("subcontent_code") or record.subcontent or None,
            "difficulty": record.difficulty, "status": record.status,
            "classification_confidence": float(record.classification_confidence)
            if record.classification_confidence is not None else None,
            "difficulty_confidence": float(record.difficulty_confidence)
            if record.difficulty_confidence is not None else None,
            "review_reason": meta.get("review_reason"),
        }
