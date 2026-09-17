from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from agente_ia_edu.api.dependencies import get_current_authenticated_context, get_session_factory
from agente_ia_edu.api.schemas.assessments import (
    ExerciseListCreateRequest,
    ExerciseListListResponse,
    ExerciseListResponse,
)
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.assessments import ExerciseListPersistenceService

router = APIRouter(prefix="/api/v1/exercise-lists", tags=["exercise-lists"])


@router.post("", response_model=ExerciseListResponse)
async def create_exercise_list(
    payload: ExerciseListCreateRequest,
    auth_context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ExerciseListResponse:
    """Create a new exercise list with real persistence and authorization."""
    if not auth_context.school_id:
        raise HTTPException(
            status_code=403,
            detail="Exercise lists require a valid school context",
        )

    async with session_factory() as session:
        service = ExerciseListPersistenceService(session)
        list_obj = await service.create_list(
            title=payload.title,
            description=payload.description,
            school_id=str(auth_context.school_id),
            institution_id=payload.institution_id,
            created_by_external_identity=auth_context.external_identity_id,
            owner_external_id=auth_context.external_identity_id,
            visibility_scope=payload.visibility_scope or "SCHOOL",
            origin_type="SCHOOL",
            scope_type="SCHOOL",
            scope_external_id=str(auth_context.school_id),
        )
        await session.commit()

    return ExerciseListResponse(
        id=list_obj.id,
        title=list_obj.title,
        description=list_obj.description,
        status=list_obj.status,
        school_id=list_obj.school_id,
        institution_id=list_obj.institution_id,
        created_by_external_identity=list_obj.created_by_external_identity,
        owner_external_id=list_obj.owner_external_id,
        visibility_scope=list_obj.visibility_scope,
        origin_type=list_obj.origin_type,
    )


@router.get("", response_model=ExerciseListListResponse)
async def list_exercise_lists(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    auth_context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ExerciseListListResponse:
    """List exercise lists filtered by school context."""
    if not auth_context.school_id:
        return ExerciseListListResponse(items=[], total=0)

    async with session_factory() as session:
        service = ExerciseListPersistenceService(session)
        assessments = await service.list_assessments(page=page, limit=limit)
        filtered = [
            item for item in assessments
            if item.school_id == str(auth_context.school_id)
        ]

    return ExerciseListListResponse(
        items=[
            ExerciseListResponse(
                id=item.id,
                title=item.title,
                description=item.description,
                status=item.status,
                school_id=item.school_id,
                institution_id=item.institution_id,
                created_by_external_identity=item.created_by_external_identity,
                owner_external_id=item.owner_external_id,
                visibility_scope=item.visibility_scope,
                origin_type=item.origin_type,
            )
            for item in filtered
        ],
        total=len(filtered),
    )


@router.get("/{exercise_list_id}", response_model=ExerciseListResponse)
async def get_exercise_list(
    exercise_list_id: UUID,
    auth_context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ExerciseListResponse:
    """Get a specific exercise list with authorization check."""
    if not auth_context.school_id:
        raise HTTPException(status_code=403, detail="No school context")

    async with session_factory() as session:
        service = ExerciseListPersistenceService(session)
        assessment = await service.get_assessment(exercise_list_id)

    if assessment is None:
        raise HTTPException(status_code=404, detail="Exercise list not found")

    if assessment.school_id != str(auth_context.school_id):
        raise HTTPException(status_code=403, detail="Access denied")

    return ExerciseListResponse(
        id=assessment.id,
        title=assessment.title,
        description=assessment.description,
        status=assessment.status,
        school_id=assessment.school_id,
        institution_id=assessment.institution_id,
        created_by_external_identity=assessment.created_by_external_identity,
        owner_external_id=assessment.owner_external_id,
        visibility_scope=assessment.visibility_scope,
        origin_type=assessment.origin_type,
    )
