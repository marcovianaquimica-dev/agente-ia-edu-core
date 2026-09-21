"""R2 - "Enviar redação" (spec §6): the student-facing submission surface.

Every route here resolves the caller's own real enrollment
(resolve_active_enrollment) and validates prompt_assignment ownership
directly, before calling EssaySubmissionService - a prompt_assignment_id
that isn't the caller's own class is 403, never 404 (spec §6 item 4, the
Fase 3C rule reused verbatim).
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_identity, get_session_factory
from ...db.models import EssaySubmission, EssaySubmissionPage, PromptAssignment
from ...identity import ExternalIdentityContext
from ...services.admin import PlatformModuleKey
from ...services.authorization import AuthorizationService
from ...services.essay_submission import EssayResubmissionBlockedError, EssaySubmissionService
from ...services.institution_settings import InstitutionSettingsService
from ...services.student_enrollment_resolution import resolve_active_enrollment

essay_submissions_router = APIRouter(
    prefix="/api/v1/student/essay-submissions", tags=["essay-submissions"]
)

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_ALLOWED_PAGE_SUFFIXES = (".png", ".jpg", ".jpeg")
_ALLOWED_DOCUMENT_SUFFIXES = (".pdf",)


class EssaySubmissionCreateRequest(BaseModel):
    prompt_assignment_id: UUID
    mode: str = Field(..., description="TYPED, PHOTO, PDF")
    text: Optional[str] = Field(None, description="required when mode=TYPED")
    resubmit_essay_id: Optional[UUID] = Field(
        None, description="set to create a new version of an existing essay_id (reenvio)"
    )


class EssaySubmissionResponse(BaseModel):
    id: UUID
    essay_id: UUID
    school_id: UUID
    prompt_assignment_id: UUID
    student_id: UUID
    mode: str
    anchor_mode: str
    status: str
    canonical_text: Optional[str] = None
    normalized_text_hash: Optional[str] = None


class EssaySubmissionPageResponse(BaseModel):
    id: UUID
    essay_submission_id: UUID
    page_number: int
    storage_uri: str
    ocr_tokens: Optional[list] = None
    reviewed_text: Optional[str] = None


class PageReviewRequest(BaseModel):
    reviewed_text: str = Field(..., min_length=1)


def _submission_to_response(submission: EssaySubmission) -> EssaySubmissionResponse:
    return EssaySubmissionResponse(
        id=submission.id, essay_id=submission.essay_id, school_id=submission.school_id,
        prompt_assignment_id=submission.prompt_assignment_id, student_id=submission.student_id,
        mode=submission.mode, anchor_mode=submission.anchor_mode, status=submission.status,
        canonical_text=submission.canonical_text, normalized_text_hash=submission.normalized_text_hash,
    )


def _page_to_response(page: EssaySubmissionPage) -> EssaySubmissionPageResponse:
    return EssaySubmissionPageResponse(
        id=page.id, essay_submission_id=page.essay_submission_id, page_number=page.page_number,
        storage_uri=page.storage_uri, ocr_tokens=page.ocr_tokens, reviewed_text=page.reviewed_text,
    )


async def _authorize_student(
    identity: ExternalIdentityContext, session: AsyncSession,
):
    authz = AuthorizationService(session)
    context = await authz.resolve_context(identity)
    role_check = await authz.require_role(context, "STUDENT")
    if not role_check.allowed:
        raise HTTPException(status_code=403, detail="Only a student may submit an essay.")
    if context.school_id is None:
        raise HTTPException(status_code=403, detail="An active school context is required.")

    module_check = await authz.require_module(context, PlatformModuleKey.REDACAO_IA)
    if not module_check.allowed:
        raise HTTPException(status_code=403, detail=module_check.reason)
    return context


async def _resolve_enrollment_or_403(session: AsyncSession, *, school_id: uuid.UUID, external_user_id: str):
    enrollment = await resolve_active_enrollment(
        session, school_id=school_id, external_user_id=external_user_id
    )
    if enrollment is None:
        raise HTTPException(status_code=403, detail="No active enrollment - cannot submit an essay.")
    return enrollment


async def _assignment_for_own_class_or_403(
    session: AsyncSession, *, prompt_assignment_id: uuid.UUID, school_id: uuid.UUID, class_id: uuid.UUID,
) -> PromptAssignment:
    assignment = await session.get(PromptAssignment, prompt_assignment_id)
    if (
        assignment is None
        or assignment.school_id != school_id
        or assignment.class_id != class_id
    ):
        raise HTTPException(
            status_code=403, detail="This proposal was not assigned to your class."
        )
    return assignment


async def _submission_for_own_school_or_403(
    session: AsyncSession, *, essay_submission_id: uuid.UUID, school_id: uuid.UUID,
    student_id: uuid.UUID,
) -> EssaySubmission:
    """Not just "same school" - a student's own submission specifically.
    school_id alone would let any student at the school read or modify any
    other student's essay; student_id (the caller's own, resolved via
    resolve_active_enrollment, never client-supplied) closes that."""
    submission = await session.get(EssaySubmission, essay_submission_id)
    if (
        submission is None
        or submission.school_id != school_id
        or submission.student_id != student_id
    ):
        raise HTTPException(status_code=403, detail="This submission is not yours.")
    return submission


async def _resubmission_target_or_403(
    session: AsyncSession, *, essay_id: uuid.UUID, school_id: uuid.UUID, student_id: uuid.UUID,
) -> EssaySubmission:
    """The current SUBMITTED version of essay_id, only if it's genuinely the
    caller's own. EssaySubmissionService._validate_resubmission queries for the
    same row by essay_id+status alone and trusts it completely - this check
    is what makes that trust safe, by running before the service ever sees
    the id."""
    result = await session.execute(
        select(EssaySubmission).where(
            EssaySubmission.essay_id == essay_id, EssaySubmission.status == "SUBMITTED"
        )
    )
    current = result.scalars().first()
    if (
        current is None
        or current.school_id != school_id
        or current.student_id != student_id
    ):
        raise HTTPException(status_code=403, detail="This essay is not yours to resubmit.")
    return current


@essay_submissions_router.post("", status_code=201, response_model=EssaySubmissionResponse)
async def create_essay_submission(
    request: EssaySubmissionCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssaySubmissionResponse:
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        enrollment = await _resolve_enrollment_or_403(
            session, school_id=school_id, external_user_id=identity.external_user_id
        )
        assignment = await _assignment_for_own_class_or_403(
            session, prompt_assignment_id=request.prompt_assignment_id,
            school_id=school_id, class_id=enrollment.class_id,
        )
        if request.resubmit_essay_id is not None:
            await _resubmission_target_or_403(
                session, essay_id=request.resubmit_essay_id,
                school_id=school_id, student_id=enrollment.student_id,
            )
        settings = await InstitutionSettingsService(session).get_settings(school_id)
        service = EssaySubmissionService(session)

        try:
            if request.mode == "TYPED":
                if not request.text:
                    raise HTTPException(status_code=422, detail="text is required when mode=TYPED")
                submission = await service.start_typed_submission(
                    school_id=school_id, prompt_assignment_id=assignment.id,
                    student_id=enrollment.student_id, text=request.text,
                    essay_id=request.resubmit_essay_id, correction_mode=settings.correction_mode,
                )
            elif request.mode in ("PHOTO", "PDF"):
                submission = await service.start_photo_submission(
                    school_id=school_id, prompt_assignment_id=assignment.id,
                    student_id=enrollment.student_id, mode=request.mode,
                    transcription_enabled=settings.transcription_enabled,
                    essay_id=request.resubmit_essay_id, correction_mode=settings.correction_mode,
                )
            else:
                raise HTTPException(status_code=422, detail=f"Unknown mode: {request.mode!r}")
        except EssayResubmissionBlockedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        await session.commit()
        return _submission_to_response(submission)


@essay_submissions_router.post(
    "/{essay_submission_id}/pages", status_code=201, response_model=EssaySubmissionPageResponse
)
async def upload_essay_submission_page(
    essay_submission_id: UUID,
    page_number: int = Form(...),
    file: UploadFile = File(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssaySubmissionPageResponse:
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        enrollment = await _resolve_enrollment_or_403(
            session, school_id=school_id, external_user_id=identity.external_user_id
        )
        submission = await _submission_for_own_school_or_403(
            session, essay_submission_id=essay_submission_id, school_id=school_id,
            student_id=enrollment.student_id,
        )

        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in _ALLOWED_PAGE_SUFFIXES:
            raise HTTPException(status_code=422, detail=f"unsupported file format: {suffix!r}")

        tmp_dir = Path(tempfile.mkdtemp(prefix="r2_page_upload_"))
        tmp_path = tmp_dir / (file.filename or f"page{page_number}")
        size = 0
        with open(tmp_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    out.close()
                    tmp_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="file too large (max 25MB)")
                out.write(chunk)

        try:
            service = EssaySubmissionService(session)
            page = await service.upload_page(
                essay_submission_id=submission.id, page_number=page_number,
                source_path=tmp_path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        await session.commit()
        return _page_to_response(page)


@essay_submissions_router.post(
    "/{essay_submission_id}/document", status_code=201,
    response_model=list[EssaySubmissionPageResponse],
)
async def upload_essay_submission_document(
    essay_submission_id: UUID,
    file: UploadFile = File(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EssaySubmissionPageResponse]:
    """PDF mode's entry point: one whole-file upload, server-side split into
    N pages via EssaySubmissionService.upload_document (Task 8). PHOTO mode
    uses upload_essay_submission_page instead - one call per page."""
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        enrollment = await _resolve_enrollment_or_403(
            session, school_id=school_id, external_user_id=identity.external_user_id
        )
        submission = await _submission_for_own_school_or_403(
            session, essay_submission_id=essay_submission_id, school_id=school_id,
            student_id=enrollment.student_id,
        )

        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in _ALLOWED_DOCUMENT_SUFFIXES:
            raise HTTPException(status_code=422, detail=f"unsupported file format: {suffix!r}")

        tmp_dir = Path(tempfile.mkdtemp(prefix="r2_document_upload_"))
        tmp_path = tmp_dir / (file.filename or "document.pdf")
        size = 0
        with open(tmp_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    out.close()
                    tmp_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="file too large (max 25MB)")
                out.write(chunk)

        try:
            service = EssaySubmissionService(session)
            pages = await service.upload_document(
                essay_submission_id=submission.id, source_path=tmp_path,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            # rmtree, not unlink+rmdir: _split_pdf_pages wrote a "<stem>_pages"
            # subdirectory of rasterized PNGs alongside the uploaded PDF, and
            # both are scratch - MaterialStorage.store() already copied
            # everything that needs to survive into managed storage.
            shutil.rmtree(tmp_dir, ignore_errors=True)

        await session.commit()
        return [_page_to_response(p) for p in pages]


@essay_submissions_router.get(
    "/{essay_submission_id}/pages", response_model=list[EssaySubmissionPageResponse]
)
async def list_essay_submission_pages(
    essay_submission_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EssaySubmissionPageResponse]:
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        enrollment = await _resolve_enrollment_or_403(
            session, school_id=school_id, external_user_id=identity.external_user_id
        )
        submission = await _submission_for_own_school_or_403(
            session, essay_submission_id=essay_submission_id, school_id=school_id,
            student_id=enrollment.student_id,
        )
        service = EssaySubmissionService(session)
        pages = await service.list_pages(submission.id)
        return [_page_to_response(p) for p in pages]


@essay_submissions_router.patch(
    "/{essay_submission_id}/pages/{page_number}", response_model=EssaySubmissionPageResponse
)
async def review_essay_submission_page(
    essay_submission_id: UUID,
    page_number: int,
    request: PageReviewRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssaySubmissionPageResponse:
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        enrollment = await _resolve_enrollment_or_403(
            session, school_id=school_id, external_user_id=identity.external_user_id
        )
        submission = await _submission_for_own_school_or_403(
            session, essay_submission_id=essay_submission_id, school_id=school_id,
            student_id=enrollment.student_id,
        )
        service = EssaySubmissionService(session)
        try:
            page = await service.review_page(
                essay_submission_id=submission.id, page_number=page_number,
                reviewed_text=request.reviewed_text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await session.commit()
        return _page_to_response(page)


@essay_submissions_router.post(
    "/{essay_submission_id}/confirm", response_model=EssaySubmissionResponse
)
async def confirm_essay_submission(
    essay_submission_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssaySubmissionResponse:
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        enrollment = await _resolve_enrollment_or_403(
            session, school_id=school_id, external_user_id=identity.external_user_id
        )
        submission = await _submission_for_own_school_or_403(
            session, essay_submission_id=essay_submission_id, school_id=school_id,
            student_id=enrollment.student_id,
        )
        service = EssaySubmissionService(session)
        try:
            confirmed = await service.confirm_submission(submission.id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await session.commit()
        return _submission_to_response(confirmed)
