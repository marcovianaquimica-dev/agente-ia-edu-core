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
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_identity, get_session_factory
from ...db.models import EssayCorrection, EssayPrompt, EssaySubmission, EssaySubmissionPage, Person, PromptAssignment, Student
from ...identity import ExternalIdentityContext
from ...services.authorization import AuthorizationService
from ...services.essay_correction import EssayCorrectionService
from ...services.essay_evolution import EssayEvolutionResponse, build_evolution
from ...services.essay_pdf_export import build_render_model, filename_for_title, pdf_available, render_pdf

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


class SubmissionPageSummary(BaseModel):
    page_number: int


class SubmissionContentResponse(BaseModel):
    essay_submission_id: UUID
    anchor_mode: str
    canonical_text: Optional[str] = None
    pages: Optional[list[SubmissionPageSummary]] = None


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
        # Build the response BEFORE commit: commit() expires `correction`
        # (expire_on_commit=True in production - see db/session.py), and
        # accessing its attributes afterwards triggers a synchronous
        # lazy-load that raises MissingGreenlet in an async context. Test
        # fixtures using expire_on_commit=False mask this.
        response = _correction_to_response(correction)
        await session.commit()
        return response


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
        # See approve_essay_correction above: build the response before
        # commit() expires `correction`, to avoid a MissingGreenlet error
        # in production (expire_on_commit=True).
        response = _correction_to_response(correction)
        await session.commit()
        return response


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
        # See approve_essay_correction above: build the response before
        # commit() expires `correction`, to avoid a MissingGreenlet error
        # in production (expire_on_commit=True).
        response = _correction_to_response(correction)
        await session.commit()
        return response


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
        # See approve_essay_correction above: build the response before
        # commit() expires each corrected row, to avoid a MissingGreenlet
        # error in production (expire_on_commit=True).
        response = BulkApproveResponse(
            approved=[_correction_to_response(c) for c in approved],
            failures={str(correction_id): reason for correction_id, reason in failures.items()},
        )
        await session.commit()
        return response


@essay_corrections_router.get(
    "/{essay_correction_id}/submission-content", response_model=SubmissionContentResponse
)
async def get_essay_correction_submission_content(
    essay_correction_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> SubmissionContentResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        correction = await _correction_for_own_school_or_403(
            session, essay_correction_id=essay_correction_id, school_id=school_id
        )
        submission = await session.get(EssaySubmission, correction.essay_submission_id)
        pages = None
        if submission.anchor_mode == "IMAGE_REGION":
            page_numbers = (
                await session.execute(
                    select(EssaySubmissionPage.page_number)
                    .where(EssaySubmissionPage.essay_submission_id == submission.id)
                    .order_by(EssaySubmissionPage.page_number)
                )
            ).scalars().all()
            pages = [SubmissionPageSummary(page_number=n) for n in page_numbers]
        return SubmissionContentResponse(
            essay_submission_id=submission.id, anchor_mode=submission.anchor_mode,
            canonical_text=submission.canonical_text if submission.anchor_mode == "TEXT_OFFSET" else None,
            pages=pages,
        )


@essay_corrections_router.get("/{essay_correction_id}/export.pdf")
async def export_essay_correction_pdf(
    essay_correction_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    if not pdf_available():
        raise HTTPException(status_code=503, detail="PDF export requires the 'pymupdf' package")
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        correction = await _correction_for_own_school_or_403(
            session, essay_correction_id=essay_correction_id, school_id=school_id
        )
        if correction.status not in ("APPROVED", "PENDING_REVIEW"):
            raise HTTPException(status_code=404, detail="No publishable correction to export.")

        submission = await session.get(EssaySubmission, correction.essay_submission_id)
        prompt_title = (
            await session.execute(
                select(EssayPrompt.title)
                .join(PromptAssignment, PromptAssignment.essay_prompt_id == EssayPrompt.id)
                .where(PromptAssignment.id == submission.prompt_assignment_id)
            )
        ).scalar_one_or_none() or "Redação"

        ai_output = correction.ai_output or {}
        model = build_render_model({
            "final_scores": correction.final_scores,
            "final_feedback": correction.final_feedback,
            "annotations": ai_output.get("annotations"),
            "rewrites": ai_output.get("rewrites"),
            "alerts": ai_output.get("alerts"),
            "intervention": ai_output.get("intervention"),
            "rationales": ai_output.get("rationales"),
            "intro_message": ai_output.get("intro_message"),
            "closing_message": ai_output.get("closing_message"),
            "mechanical_review": ai_output.get("mechanical_review"),
        })

        page_images = None
        if submission.anchor_mode == "IMAGE_REGION":
            page_images = [
                (page_number, storage_uri)
                for page_number, storage_uri in (
                    await session.execute(
                        select(EssaySubmissionPage.page_number, EssaySubmissionPage.storage_uri)
                        .where(EssaySubmissionPage.essay_submission_id == submission.id)
                        .order_by(EssaySubmissionPage.page_number)
                    )
                ).all()
            ]

        pdf_bytes = render_pdf(
            model, title=prompt_title, anchor_mode=submission.anchor_mode,
            canonical_text=submission.canonical_text, page_images=page_images,
        )
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename_for_title(prompt_title)}"'},
    )


@essay_corrections_router.get("/{essay_correction_id}/pages/{page_number}/image")
async def get_essay_correction_page_image(
    essay_correction_id: UUID,
    page_number: int,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        correction = await _correction_for_own_school_or_403(
            session, essay_correction_id=essay_correction_id, school_id=school_id
        )
        page = await session.scalar(
            select(EssaySubmissionPage).where(
                EssaySubmissionPage.essay_submission_id == correction.essay_submission_id,
                EssaySubmissionPage.page_number == page_number,
            )
        )
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found")
        return FileResponse(page.storage_uri)


class EssayEvolutionStudentItem(BaseModel):
    student_id: uuid.UUID
    student_name: str


essay_evolution_teacher_router = APIRouter(
    prefix="/api/v1/teacher/essay-evolution", tags=["essay-evolution"]
)


@essay_evolution_teacher_router.get("", response_model=EssayEvolutionResponse)
async def get_teacher_essay_evolution(
    student_id: uuid.UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayEvolutionResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        data = await build_evolution(session, school_id=school_id, student_id=student_id)
        return EssayEvolutionResponse(**data)


@essay_evolution_teacher_router.get("/students", response_model=list[EssayEvolutionStudentItem])
async def list_essay_evolution_students(
    q: Optional[str] = None,
    limit: int = 50,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EssayEvolutionStudentItem]:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        stmt = (
            select(Student.id, Person.full_name)
            .join(Person, Person.id == Student.person_id)
            .join(EssaySubmission, EssaySubmission.student_id == Student.id)
            .join(EssayCorrection, EssayCorrection.essay_submission_id == EssaySubmission.id)
            .where(Student.school_id == school_id, EssayCorrection.status == "APPROVED")
            .distinct()
            .order_by(Person.full_name)
            .limit(min(limit, 200))
        )
        if q:
            stmt = stmt.where(Person.full_name.ilike(f"%{q}%"))
        rows = (await session.execute(stmt)).all()
        return [
            EssayEvolutionStudentItem(student_id=row_student_id, student_name=row_student_name)
            for row_student_id, row_student_name in rows
        ]
