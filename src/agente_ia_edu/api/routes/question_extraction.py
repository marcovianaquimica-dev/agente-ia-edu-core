"""PHASE 27/28/29 - Question Extraction Engine: teacher/coordination API.

Reuses the EXISTING authz pattern verbatim (role check TEACHER/COORDINATOR/
DIRECTOR/PLATFORM_ADMIN + tenant scope from
AuthorizationService.resolve_context) - no parallel authorization, same as
PHASE 26's ingestion router. Extraction runs against an EXISTING
``ingestion_documents`` row (shared with PHASE 26 - spec s20) via its own
``storage_uri`` - this router never accepts a raw file upload of its own.

PHASE 29 adds the review-queue, per-question review actions (start-review/
edit/approve/reject/asset association) and the run-level publish-to-
Question-Bank endpoints - see ``question_extraction_service.py`` and
``question_publication_service.py`` for the actual workflow/promotion
logic; this router only translates HTTP <-> those services.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import fitz
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from ..dependencies import get_current_identity, get_session_factory
from ...db.models import ExtractedQuestion, ExtractedQuestionAsset, IngestionDocument, QuestionExtractionRun
from ...identity import ExternalIdentityContext
from ...services.authorization import AuthorizationService
from ...services.question_extraction_service import (
    REJECTION_REASONS,
    QuestionExtractionError,
    QuestionExtractionNotFound,
    QuestionExtractionService,
)
from ...services.question_publication_service import QuestionPublicationService

qe_router = APIRouter(prefix="/api/v1/catalog/question-extraction", tags=["question-extraction"])


class RunExtractionRequest(BaseModel):
    expected_question_count: int | None = Field(default=None, gt=0, le=9999)


class OptionInput(BaseModel):
    label: str = Field(max_length=2)
    text: str = Field(max_length=4000)


class UpdateQuestionRequest(BaseModel):
    reviewed_text: str | None = None
    options: list[OptionInput] | None = None
    question_type: str | None = None
    notes: str | None = Field(default=None, max_length=4000)


class RejectQuestionRequest(BaseModel):
    reason: str = Field(max_length=30)
    notes: str | None = Field(default=None, max_length=2000)


async def _authorize(identity: ExternalIdentityContext, session):
    authz = AuthorizationService(session)
    context = await authz.resolve_context(identity)
    role_check = await authz.require_role(context, "TEACHER", "COORDINATOR", "DIRECTOR", "PLATFORM_ADMIN")
    if not role_check.allowed:
        raise HTTPException(
            status_code=403,
            detail="Question extraction requires a teacher, coordinator, director, or platform admin role.",
        )
    return context


def _require_scope(context, school_id) -> None:
    if school_id is not None:
        if context.school_id is None or str(context.school_id) != str(school_id):
            raise HTTPException(status_code=403, detail="This extraction run is outside your school scope.")


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, QuestionExtractionNotFound):
        return HTTPException(status_code=404, detail=str(exc) or "not found")
    if isinstance(exc, QuestionExtractionError):
        return HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)})
    raise exc  # pragma: no cover


def _run_to_dict(run: QuestionExtractionRun) -> dict:
    return {
        "id": str(run.id), "ingestion_document_id": str(run.ingestion_document_id),
        "school_id": str(run.school_id) if run.school_id else None,
        "engine_version": run.engine_version, "run_status": run.run_status,
        "expected_question_count": run.expected_question_count,
        "detected_question_count": run.detected_question_count,
        "validated_question_count": run.validated_question_count,
        "review_required_count": run.review_required_count,
        "missing_numbers": run.missing_numbers or [],
        "duplicated_numbers": run.duplicated_numbers or [],
        "sequence_gaps": run.sequence_gaps or [],
        "validated": run.validated, "error_message": run.error_message,
        "created_at": run.created_at.isoformat(),
    }


def _orphan_text_candidate(q: ExtractedQuestion) -> str | None:
    """spec s9 - when the ORPHAN_TEXT reason is present, surface the part
    of raw_text not already reflected in the current statement, so the UI
    can offer [Adicionar à questão]/[Ignorar] instead of a blind text dump.
    Best-effort, line-based - never invents content, only diffs what the
    engine already extracted."""
    if "ORPHAN_TEXT" not in (q.review_reasons or []):
        return None
    current = (q.reviewed_text or q.reconstructed_text or q.normalized_text or "")
    raw_lines = [ln.strip() for ln in (q.raw_text or "").splitlines() if ln.strip()]
    leftover = [ln for ln in raw_lines if ln not in current]
    return " ".join(leftover) if leftover else None


def _question_to_dict(q: ExtractedQuestion, *, with_options: bool = False, with_assets: bool = False) -> dict:
    out = {
        "id": str(q.id), "run_id": str(q.run_id), "question_number": q.question_number,
        "question_type": q.question_type, "raw_text": q.raw_text,
        "normalized_text": q.normalized_text, "reviewed_text": q.reviewed_text,
        "reconstructed_text": q.reconstructed_text,
        "display_text": q.reviewed_text or q.reconstructed_text or q.normalized_text,
        "extraction_confidence": float(q.extraction_confidence),
        "flags": q.flags or [], "review_status": q.review_status,
        "rejection_reason": q.rejection_reason,
        "source_page_start": q.source_page_start, "source_page_end": q.source_page_end,
        "cross_page": q.cross_page, "notes": q.notes,
        "reconstruction_applied": q.reconstruction_applied,
        "review_reasons": q.review_reasons or [],
        "status_history": q.status_history or [],
        "orphan_text_candidate": _orphan_text_candidate(q),
        "published_question_id": str(q.published_question_id) if q.published_question_id else None,
        "published_version_id": str(q.published_version_id) if q.published_version_id else None,
    }
    if with_options:
        out["options"] = [{"label": o.label, "text": o.text} for o in sorted(q.options, key=lambda o: o.position)]
    if with_assets:
        out["assets"] = [
            {"id": str(a.id), "asset_type": a.asset_type, "source_page": a.source_page,
             "status": a.status, "extraction_confidence": float(a.extraction_confidence)}
            for a in q.assets
        ]
    return out


def _asset_to_dict(a: ExtractedQuestionAsset) -> dict:
    return {
        "id": str(a.id), "asset_type": a.asset_type, "source_page": a.source_page,
        "status": a.status, "extraction_confidence": float(a.extraction_confidence),
    }


@qe_router.post("/{ingestion_document_id}/run", status_code=201,
                summary="Run the Question Extraction Engine over an existing ingested document")
async def run_extraction(
    ingestion_document_id: UUID,
    payload: RunExtractionRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        doc = await session.get(IngestionDocument, ingestion_document_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="ingestion document not found")
        school_id = context.school_id
        try:
            svc = QuestionExtractionService(session)
            run, created = await svc.run_extraction(
                ingestion_document_id, Path(doc.storage_uri), started_by=identity.external_user_id,
                school_id=school_id, expected_question_count=payload.expected_question_count,
            )
            questions = await svc.list_questions(run.id)
            return {"run": _run_to_dict(run), "created": created,
                   "questions": [_question_to_dict(q) for q in questions]}
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qe_router.get("/runs", summary="List extraction runs in scope")
async def list_runs(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[dict]:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = QuestionExtractionService(session)
        runs = await svc.list_runs(school_id=context.school_id)
        return [_run_to_dict(r) for r in runs]


@qe_router.get("/runs/{run_id}", summary="Run detail + its detected questions")
async def get_run(
    run_id: UUID,
    review_status: str | None = Query(default=None),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = QuestionExtractionService(session)
        try:
            run = await svc.get_run(run_id)
            _require_scope(context, run.school_id)
            questions = await svc.list_questions(run_id, review_status=review_status)
            return {"run": _run_to_dict(run),
                   "questions": [_question_to_dict(q, with_options=True, with_assets=True) for q in questions]}
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qe_router.get("/questions/{question_id}", summary="One extracted question, with options and assets")
async def get_question(
    question_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = QuestionExtractionService(session)
        try:
            q = await svc.get_question(question_id)
            _require_scope(context, q.school_id)
            return _question_to_dict(q, with_options=True, with_assets=True)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qe_router.get("/questions/{question_id}/page-image",
                summary="Render one source PDF page as PNG, for the side-by-side review UI")
async def question_page_image(
    question_id: UUID,
    page: int | None = Query(default=None, ge=1, description="1-based page; defaults to the question's first page"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> Response:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = QuestionExtractionService(session)
        try:
            q = await svc.get_question(question_id)
            _require_scope(context, q.school_id)
            run = await svc.get_run(q.run_id)
            doc = await session.get(IngestionDocument, run.ingestion_document_id)
            if doc is None:
                raise HTTPException(status_code=404, detail="source document not found")
            target_page = page if page is not None else (q.source_page_start or 1)
            pdf = fitz.open(doc.storage_uri)
            try:
                if target_page < 1 or target_page > pdf.page_count:
                    raise HTTPException(status_code=422, detail=f"page {target_page} out of range (1..{pdf.page_count})")
                pixmap = pdf[target_page - 1].get_pixmap(dpi=150)
                png_bytes = pixmap.tobytes("png")
            finally:
                pdf.close()
            return Response(content=png_bytes, media_type="image/png")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qe_router.get("/review-queue", summary="Professor's review queue - priority-sorted, filterable (spec s4/s5/s16)")
async def review_queue(
    run_id: UUID | None = Query(default=None),
    review_status: str | None = Query(default=None),
    reason: str | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    max_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    page: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, gt=0, le=500),
    offset: int = Query(default=0, ge=0),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = QuestionExtractionService(session)
        if run_id is not None:
            run = await svc.get_run(run_id)
            _require_scope(context, run.school_id)
        items = await svc.list_review_queue(
            school_id=context.school_id, run_id=run_id, review_status=review_status,
            reason=reason, min_confidence=min_confidence, max_confidence=max_confidence,
            page=page, limit=limit, offset=offset,
        )
        progress = await svc.queue_progress(school_id=context.school_id, run_id=run_id)
        return {"items": items, "progress": progress}


@qe_router.post("/questions/{question_id}/start-review", summary="REVIEW_REQUIRED -> IN_REVIEW")
async def start_review(
    question_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = QuestionExtractionService(session)
        try:
            q = await svc.get_question(question_id)
            _require_scope(context, q.school_id)
            updated = await svc.start_review(question_id, reviewer=identity.external_user_id)
            return _question_to_dict(updated, with_options=True, with_assets=True)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qe_router.patch("/questions/{question_id}", summary="Edit a staged question (never overwrites raw_text/normalized_text)")
async def update_question(
    question_id: UUID,
    payload: UpdateQuestionRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = QuestionExtractionService(session)
        try:
            q = await svc.get_question(question_id)
            _require_scope(context, q.school_id)
            options = [o.model_dump() for o in payload.options] if payload.options is not None else None
            updated = await svc.update_question(
                question_id, reviewed_text=payload.reviewed_text, options=options,
                question_type=payload.question_type, notes=payload.notes,
                reviewed_by=identity.external_user_id,
            )
            return _question_to_dict(updated, with_options=True, with_assets=True)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qe_router.post("/questions/{question_id}/approve", summary="Approve a validated question")
async def approve_question(
    question_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = QuestionExtractionService(session)
        try:
            q = await svc.get_question(question_id)
            _require_scope(context, q.school_id)
            updated = await svc.approve_question(question_id, reviewed_by=identity.external_user_id)
            return _question_to_dict(updated)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qe_router.post("/questions/{question_id}/reject", summary="Reject a question (reason required, spec s12)")
async def reject_question(
    question_id: UUID,
    payload: RejectQuestionRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    if payload.reason not in REJECTION_REASONS:
        raise HTTPException(status_code=422, detail={
            "code": "INVALID_REJECTION_REASON", "message": f"reason must be one of {REJECTION_REASONS}",
        })
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = QuestionExtractionService(session)
        try:
            q = await svc.get_question(question_id)
            _require_scope(context, q.school_id)
            updated = await svc.reject_question(
                question_id, reviewed_by=identity.external_user_id,
                reason=payload.reason, notes=payload.notes,
            )
            return _question_to_dict(updated)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qe_router.get("/questions/{question_id}/candidate-assets",
                summary="Unassociated images on this question's page range (spec s8)")
async def candidate_assets(
    question_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[dict]:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = QuestionExtractionService(session)
        try:
            q = await svc.get_question(question_id)
            _require_scope(context, q.school_id)
            assets = await svc.list_candidate_assets(question_id)
            return [_asset_to_dict(a) for a in assets]
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qe_router.post("/questions/{question_id}/assets/{asset_id}/associate",
                summary="Associate a previously-unassigned image to this question")
async def associate_asset(
    question_id: UUID,
    asset_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = QuestionExtractionService(session)
        try:
            q = await svc.get_question(question_id)
            _require_scope(context, q.school_id)
            asset = await svc.associate_asset(question_id, asset_id, reviewed_by=identity.external_user_id)
            return _asset_to_dict(asset)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qe_router.post("/questions/{question_id}/assets/{asset_id}/ignore",
                summary="Mark an image as not belonging to any question (never deleted)")
async def ignore_asset(
    question_id: UUID,
    asset_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = QuestionExtractionService(session)
        try:
            q = await svc.get_question(question_id)
            _require_scope(context, q.school_id)
            asset = await svc.ignore_asset(question_id, asset_id, reviewed_by=identity.external_user_id)
            return _asset_to_dict(asset)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qe_router.get("/runs/{run_id}/publish-summary", summary="Pre-publish summary (spec s18)")
async def publish_summary(
    run_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        qe_svc = QuestionExtractionService(session)
        pub_svc = QuestionPublicationService(session)
        try:
            run = await qe_svc.get_run(run_id)
            _require_scope(context, run.school_id)
            return await pub_svc.publish_summary(run_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@qe_router.post("/runs/{run_id}/publish",
                summary="Publish every APPROVED question in this run into the OFFICIAL Question Bank (spec s18/s20)")
async def publish_run(
    run_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        qe_svc = QuestionExtractionService(session)
        pub_svc = QuestionPublicationService(session)
        try:
            run = await qe_svc.get_run(run_id)
            _require_scope(context, run.school_id)
            return await pub_svc.publish_run(
                run_id, published_by=identity.external_user_id, school_id=run.school_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc
