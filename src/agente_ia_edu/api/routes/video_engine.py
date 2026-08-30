"""
API routes for Video Intelligence & Feedback Engine.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_current_identity, get_session_factory
from ..schemas.video_engine import (
    VideoEventRequest,
    VideoFeedbackRequest,
    VideoProgressResponse,
    VideoRequestAnotherRequest,
)
from ...identity import ExternalIdentityContext
from ...services.knowledge import KnowledgeService
from ...services.recommendation import ResourceTrackingService
from ...services.video_engine import VideoRecommendationEngine

video_router = APIRouter(
    prefix="/api/v1/videos",
    tags=["videos"],
)


@video_router.post(
    "/events",
    status_code=201,
    summary="Record video interaction event",
    description="Records video interactions (OPENED, STARTED, PROGRESS, COMPLETED, FEEDBACK) with idempotency.",
)
async def record_video_event(
    request: VideoEventRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    student_id = identity.external_user_id

    async with session_factory() as session:
        tracking_service = ResourceTrackingService(session)
        result = await tracking_service.record_interaction(
            student_id=student_id,
            resource_id=request.resource_id,
            action_type=request.event_type,
            recommendation_id=request.recommendation_id,
            content_node_id=request.content_node_id,
            progress_percentage=request.progress_percentage,
            feedback_type=request.feedback_type,
            feedback_reason=request.feedback_reason,
            event_id=request.event_id,
            metadata=request.metadata,
        )
        return result


@video_router.post(
    "/feedback",
    status_code=201,
    summary="Record feedback on a video",
    description="Records LIKED or DISLIKED feedback with optional reasons (TOO_FAST, TOO_BASIC, etc.).",
)
async def record_video_feedback(
    request: VideoFeedbackRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    student_id = identity.external_user_id

    async with session_factory() as session:
        tracking_service = ResourceTrackingService(session)
        result = await tracking_service.record_interaction(
            student_id=student_id,
            resource_id=request.resource_id,
            action_type="FEEDBACK",
            recommendation_id=request.recommendation_id,
            content_node_id=request.content_node_id,
            feedback_type=request.feedback_type,
            feedback_reason=request.feedback_reason,
        )
        return result


@video_router.post(
    "/request-another",
    summary="Request another video for a content node ('Quero outro')",
    description="Excludes the current video, records optional feedback, and returns the next best video.",
)
async def request_another_video(
    request: VideoRequestAnotherRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    student_id = identity.external_user_id
    institution_id = identity.institution_id

    async with session_factory() as session:
        knowledge_service = KnowledgeService(session)
        engine = VideoRecommendationEngine(session, knowledge_service)
        result = await engine.request_another_video(
            student_id=student_id,
            content_node_id=request.content_node_id,
            current_video_id=request.current_video_id,
            feedback_type=request.feedback_type,
            feedback_reason=request.feedback_reason,
            institution_id=institution_id,
        )
        # Drop internal SQLAlchemy object from JSON response payload
        if result.get("video_object"):
            del result["video_object"]
        return result


@video_router.get(
    "/{video_id}/progress",
    response_model=VideoProgressResponse,
    summary="Get student progress and feedback on a video",
)
async def get_video_progress(
    video_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    student_id = identity.external_user_id

    async with session_factory() as session:
        knowledge_service = KnowledgeService(session)
        engine = VideoRecommendationEngine(session, knowledge_service)
        result = await engine.get_student_video_progress(
            student_id=student_id,
            resource_id=video_id,
        )
        return result
