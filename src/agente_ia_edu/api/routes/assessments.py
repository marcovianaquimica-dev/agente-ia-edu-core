from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.api.schemas.assessments import (
    AssessmentCreateRequest,
    AssessmentItemCreateRequest,
    AssessmentListResponse,
    AssessmentPublicationCreateRequest,
    AssessmentResponse,
    AssessmentVersionCreateRequest,
    AssessmentVersionResponse,
)
from agente_ia_edu.db.session import create_session_factory
from agente_ia_edu.services.assessments import AssessmentPersistenceService

router = APIRouter(prefix="/api/v1/assessments", tags=["assessments"])


async def get_assessment_service() -> AsyncIterator[AssessmentPersistenceService]:
    session_factory = create_session_factory()
    async with session_factory() as session:
        yield AssessmentPersistenceService(session)


@router.post("", response_model=AssessmentResponse)
async def create_assessment(
    payload: AssessmentCreateRequest,
    service: AssessmentPersistenceService = Depends(get_assessment_service),
) -> AssessmentResponse:
    assessment = await service.create_assessment(
        title=payload.title,
        description=payload.description,
        institution_id=payload.institution_id,
        created_by_external_identity=payload.created_by_external_identity,
    )
    return AssessmentResponse(
        id=assessment.id,
        title=assessment.title,
        description=assessment.description,
        status=assessment.status,
        institution_id=assessment.institution_id,
        created_by_external_identity=assessment.created_by_external_identity,
    )


@router.get("", response_model=AssessmentListResponse)
async def list_assessments(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    service: AssessmentPersistenceService = Depends(get_assessment_service),
) -> AssessmentListResponse:
    assessments = await service.list_assessments(page=page, limit=limit)
    return AssessmentListResponse(
        items=[
            AssessmentResponse(
                id=item.id,
                title=item.title,
                description=item.description,
                status=item.status,
                institution_id=item.institution_id,
                created_by_external_identity=item.created_by_external_identity,
            )
            for item in assessments
        ],
        total=len(assessments),
    )


@router.get("/{assessment_id}", response_model=AssessmentResponse)
async def get_assessment(
    assessment_id: UUID,
    service: AssessmentPersistenceService = Depends(get_assessment_service),
) -> AssessmentResponse:
    assessment = await service.get_assessment(assessment_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return AssessmentResponse(
        id=assessment.id,
        title=assessment.title,
        description=assessment.description,
        status=assessment.status,
        institution_id=assessment.institution_id,
        created_by_external_identity=assessment.created_by_external_identity,
    )


@router.post("/{assessment_id}/versions", response_model=AssessmentVersionResponse)
async def create_assessment_version(
    assessment_id: UUID,
    payload: AssessmentVersionCreateRequest,
    service: AssessmentPersistenceService = Depends(get_assessment_service),
) -> AssessmentVersionResponse:
    version = await service.create_version(
        assessment_id=assessment_id,
        version_number=1,
        title=payload.title,
        description=payload.description,
        status=payload.status,
        created_by_external_identity=payload.created_by_external_identity,
    )
    return AssessmentVersionResponse(
        id=version.id,
        assessment_id=version.assessment_id,
        version_number=version.version_number,
        title=version.title,
        description=version.description,
        status=version.status,
        created_by_external_identity=version.created_by_external_identity,
    )


@router.post("/{assessment_id}/versions/{version_id}/items", response_model=dict)
async def add_assessment_item(
    assessment_id: UUID,
    version_id: UUID,
    payload: AssessmentItemCreateRequest,
    service: AssessmentPersistenceService = Depends(get_assessment_service),
) -> dict:
    item = await service.add_item(
        assessment_version_id=version_id,
        question_version_id=payload.question_version_id,
        position=payload.position,
        points=payload.points,
        is_required=payload.is_required,
        selection_request_id=payload.selection_request_id,
    )
    return {
        "assessment_id": str(assessment_id),
        "version_id": str(version_id),
        "item_id": str(item.id),
        "question_version_id": str(item.question_version_id),
        "position": item.position,
        "points": item.points,
        "is_required": item.is_required,
    }


@router.post("/{assessment_id}/publications", response_model=dict)
async def create_publication(
    assessment_id: UUID,
    payload: AssessmentPublicationCreateRequest,
    service: AssessmentPersistenceService = Depends(get_assessment_service),
) -> dict:
    publication = await service.create_publication(
        assessment_version_id=assessment_id,
        publication_type=payload.publication_type,
        released_immediately=payload.released_immediately,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        time_limit_seconds=payload.time_limit_seconds,
        attempts_allowed=payload.attempts_allowed,
        source_display=payload.source_display,
        bncc_display=payload.bncc_display,
        show_difficulty=payload.show_difficulty,
    )
    return {
        "assessment_id": str(assessment_id),
        "publication_id": str(publication.id),
        "publication_type": publication.publication_type,
        "released_immediately": publication.released_immediately,
        "starts_at": publication.starts_at,
        "ends_at": publication.ends_at,
        "time_limit_seconds": publication.time_limit_seconds,
        "attempts_allowed": publication.attempts_allowed,
    }


@router.get("/{assessment_id}/publications", response_model=list)
async def list_publications(
    assessment_id: UUID,
    service: AssessmentPersistenceService = Depends(get_assessment_service),
) -> list:
    return await service.list_publications(assessment_id)
