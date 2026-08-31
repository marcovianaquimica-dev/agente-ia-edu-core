"""
API routes for Teacher Lesson Registration & Coordination Context (Phase 12B.1).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_current_identity, get_session_factory
from ..schemas.teaching_context import (
    CoordinationContextCreateRequest,
    PedagogicalContextResponse,
    TeachingLessonCreateRequest,
    TeachingLessonResponse,
)
from ...identity import ExternalIdentityContext
from ...services.teaching_context import (
    ScopeAuthorizationError,
    TeachingContextService,
)

teacher_router = APIRouter(
    prefix="/api/v1/teacher",
    tags=["teacher-lessons"],
)

coordination_router = APIRouter(
    prefix="/api/v1/coordination",
    tags=["coordination-context"],
)

pedagogical_context_router = APIRouter(
    prefix="/api/v1/pedagogical",
    tags=["pedagogical-context"],
)


def _to_lesson_response(lesson) -> TeachingLessonResponse:
    return TeachingLessonResponse(
        id=lesson.id,
        school_id=lesson.school_id,
        academic_year=lesson.academic_year,
        unit_id=lesson.unit_id,
        segment_id=lesson.segment_id,
        grade_level=lesson.grade_level,
        classroom_id=lesson.classroom_id,
        teacher_id=lesson.teacher_id,
        content_node_id=lesson.content_node_id,
        subcontent_node_id=lesson.subcontent_node_id,
        lesson_date=lesson.lesson_date,
        duration_minutes=lesson.duration_minutes,
        title=lesson.title,
        summary_observation=lesson.summary_observation,
        pedagogical_context_id=lesson.pedagogical_context_id,
        created_at=lesson.created_at,
        updated_at=lesson.updated_at,
    )


def _to_context_response(ctx) -> PedagogicalContextResponse:
    return PedagogicalContextResponse(
        id=ctx.id,
        content_node_id=ctx.content_node_id,
        source=ctx.source,
        institution_id=ctx.institution_id,
        classroom_id=ctx.classroom_id,
        author_id=ctx.author_id,
        title=ctx.title,
        description=ctx.description,
        recorded_at=ctx.recorded_at,
        active=ctx.active,
        created_at=ctx.created_at,
    )


# ============================================================================
# Teacher Endpoints
# ============================================================================


@teacher_router.post(
    "/lessons",
    status_code=201,
    response_model=TeachingLessonResponse,
    summary="Record a teacher lesson",
    description="Teacher records a lesson for a classroom, synchronizing PedagogicalContext for recommendations.",
)
async def record_teacher_lesson(
    request: TeachingLessonCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TeachingLessonResponse:
    teacher_id = identity.external_user_id

    async with session_factory() as session:
        service = TeachingContextService(session)
        try:
            lesson = await service.record_lesson(
                teacher_id=teacher_id,
                school_id=request.school_id,
                classroom_id=request.classroom_id,
                content_node_id=request.content_node_id,
                subcontent_node_id=request.subcontent_node_id,
                academic_year=request.academic_year,
                unit_id=request.unit_id,
                segment_id=request.segment_id,
                grade_level=request.grade_level,
                lesson_date=request.lesson_date,
                duration_minutes=request.duration_minutes,
                title=request.title,
                summary_observation=request.summary_observation,
                metadata=request.metadata,
            )
            return _to_lesson_response(lesson)
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@teacher_router.get(
    "/lessons",
    response_model=list[TeachingLessonResponse],
    summary="List teacher's recorded lessons",
)
async def list_teacher_lessons(
    school_id: UUID = Query(...),
    classroom_id: str | None = Query(None),
    academic_year: str = Query("2026"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[TeachingLessonResponse]:
    teacher_id = identity.external_user_id

    async with session_factory() as session:
        service = TeachingContextService(session)
        try:
            lessons = await service.list_teacher_lessons(
                teacher_id=teacher_id,
                school_id=school_id,
                classroom_id=classroom_id,
                academic_year=academic_year,
                limit=limit,
                offset=offset,
            )
            return [_to_lesson_response(l) for l in lessons]
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@teacher_router.get(
    "/lessons/{lesson_id}",
    response_model=TeachingLessonResponse,
    summary="Get lesson details by ID",
)
async def get_teacher_lesson(
    lesson_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TeachingLessonResponse:
    async with session_factory() as session:
        service = TeachingContextService(session)
        lesson = await service.get_lesson_by_id(lesson_id)
        if not lesson:
            raise HTTPException(status_code=404, detail="Teaching lesson not found.")

        try:
            await service.verify_teacher_classroom_scope(
                teacher_id=identity.external_user_id,
                school_id=lesson.school_id,
                classroom_id=lesson.classroom_id,
            )
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

        return _to_lesson_response(lesson)


# ============================================================================
# Coordination Endpoints
# ============================================================================


@coordination_router.post(
    "/pedagogical-context",
    status_code=201,
    response_model=PedagogicalContextResponse,
    summary="Record coordination guidance or school planning context",
)
async def record_coordination_context(
    request: CoordinationContextCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> PedagogicalContextResponse:
    coordinator_id = identity.external_user_id

    async with session_factory() as session:
        service = TeachingContextService(session)
        try:
            ctx = await service.record_coordination_context(
                coordinator_id=coordinator_id,
                school_id=request.school_id,
                content_node_id=request.content_node_id,
                classroom_id=request.classroom_id,
                source=request.source,
                title=request.title,
                description=request.description,
                academic_year=request.academic_year,
                recorded_at=request.recorded_at,
                metadata=request.metadata,
            )
            return _to_context_response(ctx)
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


# ============================================================================
# Pedagogical Context Endpoints
# ============================================================================


@pedagogical_context_router.get(
    "/context/{classroom_id}",
    response_model=list[PedagogicalContextResponse],
    summary="Get active recent pedagogical contexts for a classroom",
)
async def get_classroom_pedagogical_context(
    classroom_id: str,
    school_id: UUID = Query(...),
    academic_year: str = Query("2026"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[PedagogicalContextResponse]:
    async with session_factory() as session:
        service = TeachingContextService(session)
        contexts = await service.get_active_recent_contexts(
            school_id=school_id,
            classroom_id=classroom_id,
            academic_year=academic_year,
        )
        return [_to_context_response(c) for c in contexts]
