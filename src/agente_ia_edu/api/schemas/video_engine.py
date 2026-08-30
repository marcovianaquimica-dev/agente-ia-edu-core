"""
Pydantic schemas for video intelligence API endpoints.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class VideoEventRequest(BaseModel):
    resource_id: UUID
    event_type: str = Field(..., description="OPENED, STARTED, PROGRESS, COMPLETED, FEEDBACK")
    recommendation_id: Optional[UUID] = None
    content_node_id: Optional[UUID] = None
    progress_percentage: Optional[float] = Field(None, ge=0.0, le=100.0)
    feedback_type: Optional[str] = Field(None, description="LIKED, DISLIKED")
    feedback_reason: Optional[str] = Field(None, description="TOO_FAST, TOO_SLOW, TOO_BASIC, etc.")
    event_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class VideoFeedbackRequest(BaseModel):
    resource_id: UUID
    feedback_type: str = Field(..., description="LIKED or DISLIKED")
    feedback_reason: Optional[str] = Field(None, description="TOO_FAST, TOO_SLOW, TOO_BASIC, etc.")
    recommendation_id: Optional[UUID] = None
    content_node_id: Optional[UUID] = None


class VideoRequestAnotherRequest(BaseModel):
    content_node_id: UUID
    current_video_id: UUID
    feedback_type: Optional[str] = None
    feedback_reason: Optional[str] = None


class VideoProgressResponse(BaseModel):
    student_id: str
    resource_id: UUID
    status: str  # UNWATCHED, IN_PROGRESS, COMPLETED
    progress_percentage: float
    last_interaction_at: Optional[str] = None
    feedback_type: Optional[str] = None
    feedback_reason: Optional[str] = None
