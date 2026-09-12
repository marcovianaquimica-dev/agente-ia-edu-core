"""PHASE 30 - Authorial Question Pedagogical Classification Engine: API.

Reuses the EXACT authz pattern PHASE 27/28/29's question-extraction router
already uses (role check TEACHER/COORDINATOR/DIRECTOR/PLATFORM_ADMIN +
tenant scope via AuthorizationService.resolve_context) - no parallel
authorization (spec s33). Classification itself lives on ``QuestionVersion``
(spec s26), so every route here is scoped by question_version_id, with
tenant scope resolved by joining up to the owning ``Question.school_id``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..dependencies import get_current_identity, get_session_factory
from ...db.models import PedagogicalClassification, PedagogicalClassificationReview, Question, QuestionExtractionRun, QuestionVersion
from ...identity import ExternalIdentityContext
from ...providers.contracts import TextGenerationProvider
from ...providers.factory import build_text_provider
from ...services.authorial_classification_policy import DIFFICULTY_VALUES
from ...services.authorial_question_classification_service import (
    AuthorialQuestionClassificationService,
    ClassificationValidationError,
)
from ...services.authorization import AuthorizationService

qc_router = APIRouter(prefix="/api/v1/catalog/question-classification", tags=["question-classification"])


def get_text_generation_provider() -> TextGenerationProvider:
    """Production wiring - the SAME provider factory the rest of the
    platform uses. Tests override this via ``app.dependency_overrides``
    with a deterministic fake, never a live call (spec s42)."""
    return build_text_provider()


class ManualClassifyRequest(BaseModel):
    discipline_code: str | None = None
    area_code: str | None = None
    content_code: str = Field(min_length=1)
    subcontent_code: str | None = None
    difficulty: str
    reason: str = Field(min_length=1, max_length=2000)


class ReclassifyRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ApproveRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class BatchClassifyRequest(BaseModel):
    question_version_ids: list[UUID] = Field(default_factory=list)
    extraction_run_id: UUID | None = None


async def _authorize(identity: ExternalIdentityContext, session):
    authz = AuthorizationService(session)
    context = await authz.resolve_context(identity)
    role_check = await authz.require_role(context, "TEACHER", "COORDINATOR", "DIRECTOR", "PLATFORM_ADMIN")
    if not role_check.allowed:
        raise HTTPException(
            status_code=403,
            detail="Question classification requires a teacher, coordinator, director, or platform admin role.",
        )
    return context


async def _question_school_id(session, question_version_id: UUID) -> UUID | None:
    return await session.scalar(
        select(Question.school_id)
        .join(QuestionVersion, QuestionVersion.question_id == Question.id)
        .where(QuestionVersion.id == question_version_id)
    )


def _require_scope(context, school_id) -> None:
    if school_id is not None:
        if context.school_id is None or str(context.school_id) != str(school_id):
            raise HTTPException(status_code=403, detail="This question is outside your school scope.")


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ClassificationValidationError):
        return HTTPException(status_code=422, detail={"code": "CLASSIFICATION_INVALID", "message": str(exc)})
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc) or "not found")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail={"code": "INVALID_REQUEST", "message": str(exc)})
    raise exc  # pragma: no cover


def _classification_to_dict(c: PedagogicalClassification) -> dict:
    meta = c.metadata_ or {}
    return {
        "id": str(c.id), "question_version_id": str(c.question_version_id),
        "discipline_code": meta.get("discipline_code") or c.discipline or None,
        "area_code": meta.get("area_code"),
        "content_code": meta.get("content_code") or c.content or None,
        "subcontent_code": meta.get("subcontent_code") or c.subcontent or None,
        "difficulty": c.difficulty,
        "classification_confidence": float(c.classification_confidence) if c.classification_confidence is not None else None,
        "difficulty_confidence": float(c.difficulty_confidence) if c.difficulty_confidence is not None else None,
        "status": c.status, "lifecycle": c.lifecycle, "source": c.source,
        "review_reason": meta.get("review_reason"),
        "classification_reason": meta.get("difficulty_reasoning") or meta.get("manual_reason"),
        "classifier_version": c.model_version, "curriculum_version": meta.get("taxonomy_version"),
        "classification_mode": meta.get("classification_mode"),
        "human_approved": bool(meta.get("human_approved")),
        "approved_by": meta.get("approved_by"), "approved_at": meta.get("approved_at"),
        "created_at": c.created_at.isoformat(),
        "supersedes_id": str(c.supersedes_id) if c.supersedes_id else None,
    }


def _review_event_to_dict(e: PedagogicalClassificationReview) -> dict:
    return {
        "id": str(e.id), "action": e.action, "actor": e.actor, "actor_type": e.actor_type,
        "previous_value": e.previous_value, "new_value": e.new_value, "reason": e.reason,
        "classifier_version": e.classifier_version, "created_at": e.created_at.isoformat(),
    }


@qc_router.get("/question-versions/{question_version_id}", summary="Current active classification for a question version")
async def get_classification(
    question_version_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        school_id = await _question_school_id(session, question_version_id)
        _require_scope(context, school_id)
        svc = AuthorialQuestionClassificationService(session)
        classification = await svc.get_active_classification(question_version_id)
        if classification is None:
            raise HTTPException(status_code=404, detail="no classification exists for this question version")
        return _classification_to_dict(classification)


@qc_router.get("/question-versions/{question_version_id}/history", summary="Full classification audit history")
async def get_classification_history(
    question_version_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[dict]:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        school_id = await _question_school_id(session, question_version_id)
        _require_scope(context, school_id)
        svc = AuthorialQuestionClassificationService(session)
        events = await svc.get_history(question_version_id)
        return [_review_event_to_dict(e) for e in events]


@qc_router.post("/question-versions/{question_version_id}/classify", status_code=201,
                summary="Run the classification engine for one question version (cached if already classified)")
async def classify_question(
    question_version_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
    provider: TextGenerationProvider = Depends(get_text_generation_provider),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        school_id = await _question_school_id(session, question_version_id)
        _require_scope(context, school_id)
        svc = AuthorialQuestionClassificationService(session)
        try:
            outcome = await svc.classify_question_version(question_version_id, provider, actor=identity.external_user_id)
            return {**_classification_to_dict(outcome.classification), "ai_calls": outcome.ai_calls, "cache_hit": outcome.cache_hit}
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qc_router.post("/batch-classify", summary="Classify many question versions in one call (spec s22)")
async def batch_classify(
    payload: BatchClassifyRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
    provider: TextGenerationProvider = Depends(get_text_generation_provider),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        question_version_ids = list(payload.question_version_ids)
        if payload.extraction_run_id is not None:
            run = await session.get(QuestionExtractionRun, payload.extraction_run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="extraction run not found")
            _require_scope(context, run.school_id)
            from ...db.models import ExtractedQuestion
            published_version_ids = list((await session.scalars(
                select(ExtractedQuestion.published_version_id).where(
                    ExtractedQuestion.run_id == payload.extraction_run_id,
                    ExtractedQuestion.published_version_id.isnot(None),
                )
            )).all())
            question_version_ids.extend(published_version_ids)
        for qvid in question_version_ids:
            school_id = await _question_school_id(session, qvid)
            _require_scope(context, school_id)
        svc = AuthorialQuestionClassificationService(session)
        result = await svc.batch_classify(question_version_ids, provider, actor=identity.external_user_id)
        return {
            "questions_processed": result.questions_processed, "classified": result.classified,
            "needs_review": result.needs_review, "errors": result.errors, "ai_calls": result.ai_calls,
            "cache_hits": result.cache_hits, "cache_misses": result.cache_misses,
            "elapsed_s": round(result.elapsed_s, 3), "outcomes": result.outcomes,
        }


@qc_router.patch("/question-versions/{question_version_id}", summary="Manual classification (dropdown-validated codes only)")
async def manual_classify(
    question_version_id: UUID,
    payload: ManualClassifyRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    if payload.difficulty not in DIFFICULTY_VALUES:
        raise HTTPException(status_code=422, detail={"code": "INVALID_DIFFICULTY", "message": f"difficulty must be one of {DIFFICULTY_VALUES}"})
    async with session_factory() as session:
        context = await _authorize(identity, session)
        school_id = await _question_school_id(session, question_version_id)
        _require_scope(context, school_id)
        svc = AuthorialQuestionClassificationService(session)
        try:
            classification = await svc.manual_classify(
                question_version_id, discipline_code=payload.discipline_code, area_code=payload.area_code,
                content_code=payload.content_code, subcontent_code=payload.subcontent_code,
                difficulty=payload.difficulty, reason=payload.reason,
                actor=identity.external_user_id, actor_type=context.role,
            )
            return _classification_to_dict(classification)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qc_router.post("/question-versions/{question_version_id}/reclassify", summary="Explicit AI reclassification (never automatic)")
async def reclassify_question(
    question_version_id: UUID,
    payload: ReclassifyRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
    provider: TextGenerationProvider = Depends(get_text_generation_provider),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        school_id = await _question_school_id(session, question_version_id)
        _require_scope(context, school_id)
        svc = AuthorialQuestionClassificationService(session)
        try:
            classification = await svc.reclassify(
                question_version_id, provider, actor=identity.external_user_id,
                actor_type=context.role, reason=payload.reason,
            )
            return _classification_to_dict(classification)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qc_router.post("/classifications/{classification_id}/approve", summary="Human approves the current classification")
async def approve_classification(
    classification_id: UUID,
    payload: ApproveRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        record = await session.get(PedagogicalClassification, classification_id)
        if record is None:
            raise HTTPException(status_code=404, detail="classification not found")
        school_id = await _question_school_id(session, record.question_version_id)
        _require_scope(context, school_id)
        svc = AuthorialQuestionClassificationService(session)
        try:
            approved = await svc.approve_classification(
                classification_id, actor=identity.external_user_id, actor_type=context.role, reason=payload.reason)
            return _classification_to_dict(approved)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qc_router.get("/review-queue", summary="Classifications needing human review, tenant-scoped")
async def review_queue(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[dict]:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = AuthorialQuestionClassificationService(session)
        items = await svc.list_needs_review(school_id=context.school_id)
        return [_classification_to_dict(c) for c in items]
