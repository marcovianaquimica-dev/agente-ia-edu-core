"""R3 - "Revisar correcao" (spec §5): the teacher-facing review surface.
TEACHER/COORDINATOR/DIRECTOR/PLATFORM_ADMIN, the same role set
essay_prompts.py already uses for managing a proposal. school_id always
comes from the resolved context; essay_correction_id is always checked
against it before EssayCorrectionService ever sees it (403, never 404/422,
for not yours).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_identity, get_session_factory
from ...db.models import EssayCorrection, EssayPrompt, EssaySubmission, Person, PromptAssignment, Student
from ...identity import ExternalIdentityContext
from ...services.authorization import AuthorizationService
from ...services.essay_correction import EssayCorrectionService

essay_corrections_router = APIRouter(
    prefix="/api/v1/teacher/essay-corrections", tags=["essay-corrections"]
)

_LISTABLE_STATUSES = ("PENDING_REVIEW", "NEEDS_REVIEW", "APPROVED", "REJECTED")


class ApproveCorrectionRequest(BaseModel):
    final_scores: Optional[dict] = None
    final_feedback: Optional[dict] = None


class BulkApproveRequest(BaseModel):
    essay_correction_ids: list[UUID]


class EssayCorrectionResponse(BaseModel):
    id: UUID
    essay_submission_id: UUID
    school_id: UUID
    status: str
    rubric_version: str
    model_version: Optional[str] = None
    prompt_version: str
    engine_version: str
    ai_output: Optional[dict] = None
    final_scores: Optional[dict] = None
    final_feedback: Optional[dict] = None
    failure_reason: Optional[str] = None
    reviewed_by_external_identity: Optional[str] = None
    student_name: Optional[str] = None
    prompt_title: Optional[str] = None
    submitted_at: Optional[datetime] = None


class BulkApproveResponse(BaseModel):
    approved: list[EssayCorrectionResponse]
    failures: dict[str, str]


def _correction_to_response(
    correction: EssayCorrection, *, student_name: Optional[str] = None,
    prompt_title: Optional[str] = None, submitted_at: Optional[datetime] = None,
) -> EssayCorrectionResponse:
    return EssayCorrectionResponse(
        id=correction.id, essay_submission_id=correction.essay_submission_id,
        school_id=correction.school_id, status=correction.status,
        rubric_version=correction.rubric_version, model_version=correction.model_version,
        prompt_version=correction.prompt_version, engine_version=correction.engine_version,
        ai_output=correction.ai_output, final_scores=correction.final_scores,
        final_feedback=correction.final_feedback, failure_reason=correction.failure_reason,
        reviewed_by_external_identity=correction.reviewed_by_external_identity,
        student_name=student_name, prompt_title=prompt_title, submitted_at=submitted_at,
    )


async def _authorize(identity: ExternalIdentityContext, session: AsyncSession) -> uuid.UUID:
    authz = AuthorizationService(session)
    context = await authz.resolve_context(identity)
    role_check = await authz.require_role(context, "TEACHER", "COORDINATOR", "DIRECTOR", "PLATFORM_ADMIN")
    if not role_check.allowed:
        raise HTTPException(
            status_code=403,
            detail="Reviewing an essay correction requires a teacher, coordinator, director, or platform admin role.",
        )
    if context.school_id is None:
        raise HTTPException(status_code=403, detail="An active school context is required.")
    return uuid.UUID(str(context.school_id))


async def _correction_for_own_school_or_403(
    session: AsyncSession, *, essay_correction_id: uuid.UUID, school_id: uuid.UUID,
) -> EssayCorrection:
    correction = await session.get(EssayCorrection, essay_correction_id)
    if correction is None or correction.school_id != school_id:
        raise HTTPException(status_code=403, detail="This correction is not yours.")
    return correction


@essay_corrections_router.get("", response_model=list[EssayCorrectionResponse])
async def list_essay_corrections(
    status: str = "PENDING_REVIEW",
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EssayCorrectionResponse]:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        if status not in _LISTABLE_STATUSES:
            raise HTTPException(status_code=422, detail=f"Unknown status: {status!r}")
        rows = (
            await session.execute(
                select(EssayCorrection, Person.full_name, EssayPrompt.title, EssaySubmission.submitted_at)
                .join(EssaySubmission, EssaySubmission.id == EssayCorrection.essay_submission_id)
                .join(Student, Student.id == EssaySubmission.student_id)
                .join(Person, Person.id == Student.person_id)
                .join(PromptAssignment, PromptAssignment.id == EssaySubmission.prompt_assignment_id)
                .join(EssayPrompt, EssayPrompt.id == PromptAssignment.essay_prompt_id)
                .where(EssayCorrection.school_id == school_id, EssayCorrection.status == status)
                .order_by(EssayCorrection.created_at)
            )
        ).all()
        return [
            _correction_to_response(
                correction, student_name=student_name, prompt_title=prompt_title,
                # essay_submission.submitted_at is always written as an
                # aware UTC instant (services/essay_submission.py _utcnow()),
                # but SQLite (used in tests; see conventions) drops tzinfo on
                # read even for DateTime(timezone=True) columns. Reattach UTC
                # only when it's missing, so the JSON payload always carries
                # an explicit offset for the frontend to parse correctly -
                # on Postgres (production) submitted_at is already aware and
                # this is a no-op.
                submitted_at=(
                    submitted_at.replace(tzinfo=timezone.utc)
                    if submitted_at is not None and submitted_at.tzinfo is None
                    else submitted_at
                ),
            )
            for correction, student_name, prompt_title, submitted_at in rows
        ]


@essay_corrections_router.post(
    "/{essay_correction_id}/approve", response_model=EssayCorrectionResponse
)
async def approve_essay_correction(
    essay_correction_id: UUID,
    request: ApproveCorrectionRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayCorrectionResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _correction_for_own_school_or_403(
            session, essay_correction_id=essay_correction_id, school_id=school_id
        )
        service = EssayCorrectionService(session)
        try:
            correction = await service.approve(
                essay_correction_id, reviewed_by_external_identity=identity.external_user_id,
                final_scores=request.final_scores, final_feedback=request.final_feedback,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await session.commit()
        return _correction_to_response(correction)


@essay_corrections_router.post(
    "/{essay_correction_id}/reject", response_model=EssayCorrectionResponse
)
async def reject_essay_correction(
    essay_correction_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayCorrectionResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _correction_for_own_school_or_403(
            session, essay_correction_id=essay_correction_id, school_id=school_id
        )
        service = EssayCorrectionService(session)
        try:
            correction = await service.reject(
                essay_correction_id, reviewed_by_external_identity=identity.external_user_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await session.commit()
        return _correction_to_response(correction)


@essay_corrections_router.post(
    "/{essay_correction_id}/retry", response_model=EssayCorrectionResponse
)
async def retry_essay_correction(
    essay_correction_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayCorrectionResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _correction_for_own_school_or_403(
            session, essay_correction_id=essay_correction_id, school_id=school_id
        )
        service = EssayCorrectionService(session)
        try:
            correction = await service.retry(essay_correction_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await session.commit()
        return _correction_to_response(correction)


@essay_corrections_router.post("/bulk-approve", response_model=BulkApproveResponse)
async def bulk_approve_essay_corrections(
    request: BulkApproveRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> BulkApproveResponse:
    """Spec §2 item 5 / §5: the endpoint is delivered scope, a batch-review
    UI is not (spec §2 "Não entrega" only defers the UI). Every id is
    checked against the caller's own school BEFORE the service ever sees
    it - same 403-not-404 rule as every other route here - so a stale or
    malicious id list can't smuggle another school's correction into a
    batch that otherwise succeeds."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        for essay_correction_id in request.essay_correction_ids:
            await _correction_for_own_school_or_403(
                session, essay_correction_id=essay_correction_id, school_id=school_id
            )
        service = EssayCorrectionService(session)
        approved, failures = await service.bulk_approve(
            request.essay_correction_ids, reviewed_by_external_identity=identity.external_user_id,
        )
        await session.commit()
        return BulkApproveResponse(
            approved=[_correction_to_response(c) for c in approved],
            failures={str(correction_id): reason for correction_id, reason in failures.items()},
        )
