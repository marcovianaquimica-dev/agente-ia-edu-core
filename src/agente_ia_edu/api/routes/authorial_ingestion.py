"""PHASE 26 - Authorial Material Ingestion Engine: teacher/coordination API.

Reuses the EXISTING authz pattern from catalog.py's material routes verbatim
(role check TEACHER/COORDINATOR/DIRECTOR/PLATFORM_ADMIN + tenant scope from
AuthorizationService.resolve_context) - no parallel authorization. INGESTION
!= PUBLICATION: upload only extracts/structures/classifies; a human must
approve() before publish() creates anything a student can see (reusing the
EXISTING PHASE 23 TheoryMaterialService, then the EXISTING PHASE 25 student
delivery path - unchanged).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_identity, get_session_factory
from ...identity import ExternalIdentityContext
from ...services.authorial_material_ingestion import (
    AuthorialIngestionError,
    AuthorialIngestionNotFound,
    AuthorialMaterialIngestionService,
    _review_to_dict,
)
from ...services.authorization import AuthorizationService
from ...db.models import IngestionMaterialReview

ingestion_router = APIRouter(prefix="/api/v1/catalog/ingestion", tags=["authorial-ingestion"])

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25MB - a sane ceiling, never unbounded


class ClassificationUpdateRequest(BaseModel):
    discipline_code: str | None = None
    area_code: str | None = None
    content_code: str | None = None
    subcontent_codes: list[str] | None = None
    notes: str | None = None


class RejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


async def _authorize(
    identity: ExternalIdentityContext, session: AsyncSession,
):
    authz = AuthorizationService(session)
    context = await authz.resolve_context(identity)
    role_check = await authz.require_role(context, "TEACHER", "COORDINATOR", "DIRECTOR", "PLATFORM_ADMIN")
    if not role_check.allowed:
        raise HTTPException(
            status_code=403,
            detail="Material ingestion requires a teacher, coordinator, director, or platform admin role.",
        )
    return context


async def _require_review_access(
    review: IngestionMaterialReview, identity: ExternalIdentityContext, context,
) -> None:
    if review.school_id is not None:
        if context.school_id is None or str(context.school_id) != str(review.school_id):
            raise HTTPException(status_code=403, detail="This ingestion is outside your school scope.")


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorialIngestionNotFound):
        return HTTPException(status_code=404, detail=str(exc) or "not found")
    if isinstance(exc, AuthorialIngestionError):
        return HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc), **exc.payload})
    raise exc  # pragma: no cover


@ingestion_router.post("/upload", status_code=201, summary="Upload a file for authorial ingestion (does not publish)")
async def upload_material(
    file: UploadFile = File(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".pdf", ".docx", ".txt", ".md"):
        raise HTTPException(status_code=422, detail=f"unsupported file format: {suffix!r}")

    async with session_factory() as session:
        context = await _authorize(identity, session)

        tmp_dir = Path(tempfile.mkdtemp(prefix="p26_upload_"))
        tmp_path = tmp_dir / (file.filename or f"upload{suffix}")
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
            svc = AuthorialMaterialIngestionService(session)
            review, created = await svc.ingest_file(
                tmp_path, uploaded_by=identity.external_user_id,
                school_id=context.school_id, origin_type="AUTHORIAL",
            )
            detail = await svc.detail(review.id)
            detail["created"] = created
            return detail
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc
        finally:
            tmp_path.unlink(missing_ok=True)
            try:
                tmp_dir.rmdir()
            except OSError:
                pass


@ingestion_router.get("", summary="List authorial ingestions in scope")
async def list_ingestions(
    review_status: str | None = Query(default=None),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[dict]:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = AuthorialMaterialIngestionService(session)
        reviews = await svc.list_reviews(school_id=context.school_id, review_status=review_status)
        return [_review_to_dict(r) for r in reviews]


@ingestion_router.get("/{review_id}", summary="Ingestion review detail: document + structure + exercises")
async def get_ingestion(
    review_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = AuthorialMaterialIngestionService(session)
        try:
            review = await svc.get_review(review_id)
            await _require_review_access(review, identity, context)
            return await svc.detail(review_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@ingestion_router.patch("/{review_id}/classification", summary="Edit the curriculum-v2 classification suggestion")
async def update_classification(
    review_id: UUID,
    payload: ClassificationUpdateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = AuthorialMaterialIngestionService(session)
        try:
            review = await svc.get_review(review_id)
            await _require_review_access(review, identity, context)
            updated = await svc.update_classification(
                review_id, discipline_code=payload.discipline_code, area_code=payload.area_code,
                content_code=payload.content_code, subcontent_codes=payload.subcontent_codes,
                notes=payload.notes, reviewed_by=identity.external_user_id,
            )
            return _review_to_dict(updated)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@ingestion_router.post("/{review_id}/approve", summary="Approve a reviewed ingestion (still not published)")
async def approve_ingestion(
    review_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = AuthorialMaterialIngestionService(session)
        try:
            review = await svc.get_review(review_id)
            await _require_review_access(review, identity, context)
            updated = await svc.approve(review_id, reviewed_by=identity.external_user_id)
            return _review_to_dict(updated)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@ingestion_router.post("/{review_id}/reject", summary="Reject an ingestion")
async def reject_ingestion(
    review_id: UUID,
    payload: RejectRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = AuthorialMaterialIngestionService(session)
        try:
            review = await svc.get_review(review_id)
            await _require_review_access(review, identity, context)
            updated = await svc.reject(review_id, reviewed_by=identity.external_user_id, reason=payload.reason)
            return _review_to_dict(updated)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc


@ingestion_router.post("/{review_id}/publish", summary="Publish an APPROVED ingestion as a real TheoryMaterial")
async def publish_ingestion(
    review_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        context = await _authorize(identity, session)
        svc = AuthorialMaterialIngestionService(session)
        try:
            review = await svc.get_review(review_id)
            await _require_review_access(review, identity, context)
            updated = await svc.publish(review_id, published_by=identity.external_user_id)
            return _review_to_dict(updated)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc) from exc
