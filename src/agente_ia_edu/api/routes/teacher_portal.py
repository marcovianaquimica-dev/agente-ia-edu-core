"""
API routes for Teacher and Coordination Portal (Phase 12B.2).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_current_identity, get_session_factory
from ..schemas.teacher_portal import (
    ClassroomDetailResponse,
    ClassroomSummaryItem,
    ReportExportResponse,
    StudentDetailForTeacherResponse,
    StudentSearchItem,
    TeacherDashboardResponse,
)
from ...identity import ExternalIdentityContext
from ...services.knowledge import KnowledgeService
from ...services.recommendation import RecommendationEngine
from ...services.report_export import ReportExportService
from ...services.teaching_context import (
    ScopeAuthorizationError,
    TeachingContextService,
)
from ...services.teacher_portal import TeacherPortalService
from ...services.video_engine import VideoRecommendationEngine

teacher_portal_router = APIRouter(
    prefix="/api/v1/teacher",
    tags=["teacher-portal"],
)


@teacher_portal_router.get(
    "/dashboard",
    response_model=TeacherDashboardResponse,
    summary="Get teacher dashboard analytics and action plan",
)
async def get_teacher_dashboard(
    school_id: UUID = Query(...),
    academic_year: str = Query("2026"),
    classroom_id: str | None = Query(None),
    grade_level: str | None = Query(None),
    segment_id: str | None = Query(None),
    time_period: str = Query("academic_year"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TeacherDashboardResponse:
    teacher_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)
        vid_eng = VideoRecommendationEngine(session, ks)

        portal_svc = TeacherPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            recommendation_engine=rec_eng,
        )

        try:
            res = await portal_svc.get_teacher_dashboard(
                teacher_id=teacher_id,
                school_id=school_id,
                academic_year=academic_year,
                classroom_id=classroom_id,
                grade_level=grade_level,
                segment_id=segment_id,
                time_period=time_period,
            )
            return TeacherDashboardResponse(**res)
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@teacher_portal_router.get(
    "/classrooms",
    response_model=list[ClassroomSummaryItem],
    summary="List classrooms in teacher's authorized scope",
)
async def list_teacher_classrooms(
    school_id: UUID = Query(...),
    academic_year: str = Query("2026"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[ClassroomSummaryItem]:
    teacher_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)

        portal_svc = TeacherPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            recommendation_engine=rec_eng,
        )

        try:
            items = await portal_svc.list_teacher_classrooms(
                teacher_id=teacher_id,
                school_id=school_id,
                academic_year=academic_year,
            )
            return [ClassroomSummaryItem(**i) for i in items]
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@teacher_portal_router.get(
    "/classrooms/{classroom_id}",
    response_model=ClassroomDetailResponse,
    summary="Get detailed classroom analytics and roster",
)
async def get_classroom_detail(
    classroom_id: str,
    school_id: UUID = Query(...),
    academic_year: str = Query("2026"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> ClassroomDetailResponse:
    teacher_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)

        portal_svc = TeacherPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            recommendation_engine=rec_eng,
        )

        try:
            res = await portal_svc.get_classroom_detail(
                teacher_id=teacher_id,
                school_id=school_id,
                classroom_id=classroom_id,
                academic_year=academic_year,
            )
            return ClassroomDetailResponse(**res)
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@teacher_portal_router.get(
    "/students/{student_id}",
    response_model=StudentDetailForTeacherResponse,
    summary="Get individual student view for authorized teacher",
)
async def get_student_detail_for_teacher(
    student_id: str,
    school_id: UUID = Query(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentDetailForTeacherResponse:
    teacher_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)

        portal_svc = TeacherPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            recommendation_engine=rec_eng,
        )

        try:
            res = await portal_svc.get_student_detail_for_teacher(
                teacher_id=teacher_id,
                school_id=school_id,
                student_id=student_id,
            )
            return StudentDetailForTeacherResponse(**res)
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@teacher_portal_router.get(
    "/search",
    response_model=list[StudentSearchItem],
    summary="Search students strictly within teacher's authorized scope",
)
async def search_students(
    q: str = Query(..., min_length=1),
    school_id: UUID = Query(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[StudentSearchItem]:
    teacher_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)

        portal_svc = TeacherPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            recommendation_engine=rec_eng,
        )

        try:
            res = await portal_svc.search_students_in_scope(
                teacher_id=teacher_id,
                school_id=school_id,
                query=q,
            )
            return [StudentSearchItem(**item) for item in res]
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))


@teacher_portal_router.get(
    "/classrooms/{classroom_id}/export",
    response_model=ReportExportResponse,
    summary="Export classroom pedagogical report (PDF/XLSX)",
)
async def export_classroom_report(
    classroom_id: str,
    school_id: UUID = Query(...),
    academic_year: str = Query("2026"),
    format: str = Query("pdf", description="pdf or xlsx"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> ReportExportResponse:
    teacher_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        t_svc = TeachingContextService(session)
        rec_eng = RecommendationEngine(session, ks)

        portal_svc = TeacherPortalService(
            session=session,
            knowledge_service=ks,
            teaching_context_service=t_svc,
            recommendation_engine=rec_eng,
        )

        try:
            detail = await portal_svc.get_classroom_detail(
                teacher_id=teacher_id,
                school_id=school_id,
                classroom_id=classroom_id,
                academic_year=academic_year,
            )
            payload = ReportExportService.export_classroom_report(detail, export_format=format)
            return ReportExportResponse(**payload)
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
