"""
API routes for Coordination and Director Portal (Phase 12C.2).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_current_identity, get_session_factory
from ..schemas.coordination_portal import (
    ClassroomComparisonItem,
    CoordinationDashboardResponse,
    CoordinationHierarchyResponse,
    TeacherOversightItem,
)
from ..schemas.teacher_portal import (
    ClassroomDetailResponse,
    ClassroomSummaryItem,
    ReportExportResponse,
    StudentDetailForTeacherResponse,
    StudentSearchItem,
)
from ...identity import ExternalIdentityContext
from ...services.coordination_portal import CoordinationPortalService
from ...services.knowledge import KnowledgeService
from ...services.recommendation import RecommendationEngine
from ...services.teacher_portal import TeacherPortalService
from ...services.teaching_context import (
    ScopeAuthorizationError,
    TeachingContextService,
)
from ...services.video_engine import VideoRecommendationEngine

coordination_portal_router = APIRouter(
    prefix="/api/v1/coordination",
    tags=["coordination-portal"],
)


@coordination_portal_router.get(
    "/dashboard",
    response_model=CoordinationDashboardResponse,
    summary="Get coordination dashboard overview and action plans",
)
async def get_coordination_dashboard(
    school_id: UUID = Query(...),
    academic_year: str = Query("2026"),
    unit_id: str | None = Query(None),
    segment_id: str | None = Query(None),
    grade_level: str | None = Query(None),
    classroom_id: str | None = Query(None),
    teacher_id: str | None = Query(None),
    time_period: str = Query("academic_year"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> CoordinationDashboardResponse:
    coordinator_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)
        vid_eng = VideoRecommendationEngine(session, ks)
        teacher_portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

        coord_svc = CoordinationPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            teacher_portal_service=teacher_portal_svc,
            recommendation_engine=rec_eng,
        )

        try:
            res = await coord_svc.get_coordination_dashboard(
                coordinator_id=coordinator_id,
                school_id=school_id,
                academic_year=academic_year,
                unit_id=unit_id,
                segment_id=segment_id,
                grade_level=grade_level,
                classroom_id=classroom_id,
                teacher_id=teacher_id,
                time_period=time_period,
            )
            return CoordinationDashboardResponse(**res)
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@coordination_portal_router.get(
    "/hierarchy",
    response_model=CoordinationHierarchyResponse,
    summary="Get academic drill-down hierarchy (School -> Unit -> Segment -> Grade -> Classroom)",
)
async def get_coordination_hierarchy(
    school_id: UUID = Query(...),
    academic_year: str = Query("2026"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> CoordinationHierarchyResponse:
    coordinator_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)
        vid_eng = VideoRecommendationEngine(session, ks)
        teacher_portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

        coord_svc = CoordinationPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            teacher_portal_service=teacher_portal_svc,
            recommendation_engine=rec_eng,
        )

        try:
            res = await coord_svc.get_coordination_hierarchy(
                coordinator_id=coordinator_id,
                school_id=school_id,
                academic_year=academic_year,
            )
            return CoordinationHierarchyResponse(**res)
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@coordination_portal_router.get(
    "/classrooms/compare",
    response_model=list[ClassroomComparisonItem],
    summary="Get side-by-side comparison between classrooms in scope",
)
async def compare_classrooms(
    school_id: UUID = Query(...),
    academic_year: str = Query("2026"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[ClassroomComparisonItem]:
    coordinator_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)
        vid_eng = VideoRecommendationEngine(session, ks)
        teacher_portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

        coord_svc = CoordinationPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            teacher_portal_service=teacher_portal_svc,
            recommendation_engine=rec_eng,
        )

        try:
            items = await coord_svc.compare_classrooms(
                coordinator_id=coordinator_id,
                school_id=school_id,
                academic_year=academic_year,
            )
            return [ClassroomComparisonItem(**i) for i in items]
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@coordination_portal_router.get(
    "/classrooms/{classroom_id}",
    response_model=ClassroomDetailResponse,
    summary="Get classroom details for coordination",
)
async def get_classroom_detail_for_coordination(
    classroom_id: str,
    school_id: UUID = Query(...),
    academic_year: str = Query("2026"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> ClassroomDetailResponse:
    coordinator_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)
        vid_eng = VideoRecommendationEngine(session, ks)
        teacher_portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

        coord_svc = CoordinationPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            teacher_portal_service=teacher_portal_svc,
            recommendation_engine=rec_eng,
        )

        try:
            await coord_svc.verify_coordinator_access(
                coordinator_id=coordinator_id,
                school_id=school_id,
                classroom_id=classroom_id,
            )
            res = await teacher_portal_svc.get_classroom_detail(
                teacher_id=coordinator_id,
                school_id=school_id,
                classroom_id=classroom_id,
                academic_year=academic_year,
            )
            return ClassroomDetailResponse(**res)
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@coordination_portal_router.get(
    "/teachers",
    response_model=list[TeacherOversightItem],
    summary="Get teacher oversight metrics and assigned classrooms",
)
async def list_coordination_teachers(
    school_id: UUID = Query(...),
    academic_year: str = Query("2026"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[TeacherOversightItem]:
    coordinator_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)
        vid_eng = VideoRecommendationEngine(session, ks)
        teacher_portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

        coord_svc = CoordinationPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            teacher_portal_service=teacher_portal_svc,
            recommendation_engine=rec_eng,
        )

        try:
            teachers = await coord_svc.list_coordination_teachers(
                coordinator_id=coordinator_id,
                school_id=school_id,
                academic_year=academic_year,
            )
            return [TeacherOversightItem(**t) for t in teachers]
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@coordination_portal_router.get(
    "/students/{student_id}",
    response_model=StudentDetailForTeacherResponse,
    summary="Get student detail for coordination",
)
async def get_student_detail_for_coordination(
    student_id: str,
    school_id: UUID = Query(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentDetailForTeacherResponse:
    coordinator_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)
        vid_eng = VideoRecommendationEngine(session, ks)
        teacher_portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

        coord_svc = CoordinationPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            teacher_portal_service=teacher_portal_svc,
            recommendation_engine=rec_eng,
        )

        try:
            await coord_svc.verify_coordinator_student_scope(
                coordinator_id=coordinator_id,
                school_id=school_id,
                student_id=student_id,
            )
            res = await teacher_portal_svc.get_student_detail_for_teacher(
                teacher_id=coordinator_id,
                school_id=school_id,
                student_id=student_id,
            )
            return StudentDetailForTeacherResponse(**res)
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@coordination_portal_router.get(
    "/search",
    response_model=list[StudentSearchItem],
    summary="Search students strictly within coordinator's authorized scope",
)
async def search_students_for_coordination(
    q: str = Query(..., min_length=1),
    school_id: UUID = Query(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[StudentSearchItem]:
    coordinator_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)
        vid_eng = VideoRecommendationEngine(session, ks)
        teacher_portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

        coord_svc = CoordinationPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            teacher_portal_service=teacher_portal_svc,
            recommendation_engine=rec_eng,
        )

        try:
            res = await coord_svc.search_students_in_scope(
                coordinator_id=coordinator_id,
                school_id=school_id,
                query=q,
            )
            return [StudentSearchItem(**item) for item in res]
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@coordination_portal_router.get(
    "/contexts",
    summary="List active pedagogical contexts in coordination scope",
)
async def list_coordination_contexts(
    school_id: UUID = Query(...),
    classroom_id: str | None = Query(None),
    academic_year: str = Query("2026"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    coordinator_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)
        vid_eng = VideoRecommendationEngine(session, ks)
        teacher_portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

        coord_svc = CoordinationPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            teacher_portal_service=teacher_portal_svc,
            recommendation_engine=rec_eng,
        )

        try:
            return await coord_svc.list_coordination_contexts(
                coordinator_id=coordinator_id,
                school_id=school_id,
                classroom_id=classroom_id,
                academic_year=academic_year,
            )
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@coordination_portal_router.get(
    "/export",
    response_model=ReportExportResponse,
    summary="Export coordination report (PDF/XLSX)",
)
async def export_coordination_report(
    school_id: UUID = Query(...),
    classroom_id: str | None = Query(None),
    academic_year: str = Query("2026"),
    format: str = Query("pdf", description="pdf or xlsx"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> ReportExportResponse:
    coordinator_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)
        vid_eng = VideoRecommendationEngine(session, ks)
        teacher_portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

        coord_svc = CoordinationPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            teacher_portal_service=teacher_portal_svc,
            recommendation_engine=rec_eng,
        )

        try:
            payload = await coord_svc.export_coordination_report(
                coordinator_id=coordinator_id,
                school_id=school_id,
                academic_year=academic_year,
                classroom_id=classroom_id,
                export_format=format,
            )
            return ReportExportResponse(**payload)
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


# ============================================================================
# PHASE 24 - Study Session / Momento de Aprendizado (coordination-defined).
# Reuses TeachingContextService scope authz (no parallel authorization).
# ============================================================================
from pydantic import BaseModel as _BaseModel, Field as _Field  # noqa: E402
from ...services.study_session import (  # noqa: E402
    StudySessionService,
    StudySessionAuthError,
    StudySessionError,
)


class _CoordBreak(_BaseModel):
    duration_minutes: int | None = _Field(default=None, ge=1, le=120)
    start_at: str | None = None
    end_at: str | None = None
    after_block: int | None = _Field(default=None, ge=1, le=50)


class _CoordStudySessionRequest(_BaseModel):
    school_id: str
    target_type: str = _Field(default="CLASSROOM")   # CLASSROOM | STUDENT
    target_id: str
    session_date: str = _Field(min_length=10, max_length=10)   # YYYY-MM-DD
    start_at: str
    end_at: str
    content_codes: list[str] | None = _Field(default=None, max_length=8)
    breaks: list[_CoordBreak] | None = _Field(default=None, max_length=6)


def _map_coord_study_error(exc: Exception) -> HTTPException:
    if isinstance(exc, StudySessionAuthError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (StudySessionError, ValueError)):
        return HTTPException(status_code=422, detail={"message": str(exc)})
    raise exc  # pragma: no cover


@coordination_portal_router.post(
    "/study-sessions",
    summary="Create/refresh a scheduled Momento de Aprendizado for a class or a student",
)
async def create_coordination_study_session(
    request: _CoordStudySessionRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await StudySessionService(session).create_coordination_sessions(
                identity.external_user_id,
                school_id=request.school_id,
                target_type=request.target_type,
                target_id=request.target_id,
                session_date=request.session_date,
                start_at=request.start_at,
                end_at=request.end_at,
                target_content_codes=request.content_codes,
                breaks=[b.model_dump() for b in (request.breaks or [])],
            )
        except Exception as exc:  # noqa: BLE001
            raise _map_coord_study_error(exc) from exc


@coordination_portal_router.get(
    "/study-sessions",
    summary="List scheduled Momentos de Aprendizado within the coordinator's school",
)
async def list_coordination_study_sessions(
    school_id: str = Query(...),
    session_date: str | None = Query(None),
    classroom_id: str | None = Query(None),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await StudySessionService(session).list_coordination_sessions(
                identity.external_user_id, school_id=school_id,
                session_date=session_date, classroom_id=classroom_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_coord_study_error(exc) from exc
