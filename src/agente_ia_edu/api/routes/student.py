"""
API routes for Student Dashboard and Student Experience (Phase 11).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from ..dependencies import get_current_identity, get_session_factory
from ..schemas.student import (
    StudentDashboardResponse,
    StudentEvolutionResponse,
    StudentLearningPathResponse,
)
from ...identity import ExternalIdentityContext
from ...services.knowledge import KnowledgeService
from ...services.recommendation import RecommendationEngine
from ...services.student_dashboard import StudentDashboardService
from ...services.video_engine import VideoRecommendationEngine

student_router = APIRouter(
    prefix="/api/v1/student",
    tags=["student-experience"],
)


@student_router.get(
    "/dashboard",
    response_model=StudentDashboardResponse,
    summary="Get student dashboard overview",
    description="Returns aggregated student stats, active recommendation, action plan, and period filters.",
)
async def get_student_dashboard(
    time_period: str = Query("academic_year", description="academic_year, last_30_days, bimester, semester"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentDashboardResponse:
    student_id = identity.external_user_id
    institution_id = identity.institution_id
    classroom_id = identity.classroom_id

    async with session_factory() as session:
        knowledge_service = KnowledgeService(session)
        recommendation_engine = RecommendationEngine(session, knowledge_service)
        video_engine = VideoRecommendationEngine(session, knowledge_service)

        dashboard_service = StudentDashboardService(
            session=session,
            knowledge_service=knowledge_service,
            recommendation_engine=recommendation_engine,
            video_engine=video_engine,
        )

        res = await dashboard_service.get_dashboard(
            student_id=student_id,
            institution_id=institution_id,
            classroom_id=classroom_id,
            time_period=time_period,
        )
        return StudentDashboardResponse(**res)


@student_router.get(
    "/evolution",
    response_model=StudentEvolutionResponse,
    summary="Get student evolution timeline and analytics",
)
async def get_student_evolution(
    time_period: str = Query("academic_year", description="academic_year, last_30_days, bimester, semester"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentEvolutionResponse:
    student_id = identity.external_user_id

    async with session_factory() as session:
        knowledge_service = KnowledgeService(session)
        recommendation_engine = RecommendationEngine(session, knowledge_service)
        video_engine = VideoRecommendationEngine(session, knowledge_service)

        dashboard_service = StudentDashboardService(
            session=session,
            knowledge_service=knowledge_service,
            recommendation_engine=recommendation_engine,
            video_engine=video_engine,
        )

        res = await dashboard_service.get_evolution(
            student_id=student_id,
            time_period=time_period,
        )
        return StudentEvolutionResponse(**res)


@student_router.get(
    "/learning-path",
    response_model=StudentLearningPathResponse,
    summary="Get active step-by-step student learning path",
)
async def get_student_learning_path(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentLearningPathResponse:
    student_id = identity.external_user_id
    institution_id = identity.institution_id
    classroom_id = identity.classroom_id

    async with session_factory() as session:
        knowledge_service = KnowledgeService(session)
        recommendation_engine = RecommendationEngine(session, knowledge_service)
        video_engine = VideoRecommendationEngine(session, knowledge_service)

        dashboard_service = StudentDashboardService(
            session=session,
            knowledge_service=knowledge_service,
            recommendation_engine=recommendation_engine,
            video_engine=video_engine,
        )

        res = await dashboard_service.get_learning_path(
            student_id=student_id,
            institution_id=institution_id,
            classroom_id=classroom_id,
        )
        return StudentLearningPathResponse(**res)
