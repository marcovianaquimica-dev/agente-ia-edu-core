"""
Pydantic schemas for Video Discovery API endpoints.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class VideoDiscoveryRequest(BaseModel):
    content_node_id: Optional[UUID] = None
    query: Optional[str] = None
    discipline: Optional[str] = None
    limit_per_provider: int = Field(5, ge=1, le=50)


class VideoCandidateReviewRequest(BaseModel):
    candidate_id: UUID
    action: str = Field(..., description="APPROVE or REJECT")
    reasoning: Optional[str] = None


class VideoCandidateConvertRequest(BaseModel):
    candidate_id: UUID
    content_node_id: UUID
    origin_type: str = Field("EXTERNAL", description="EXTERNAL, SCHOOL, PLATFORM, AUTHOR, LICENSED")
    visibility_scope: str = Field("PUBLIC", description="PUBLIC, PRIVATE, SCHOOL, CLASSROOM")
    owner_external_id: Optional[str] = None
    recommended_level: Optional[str] = Field(None, description="EASY, MEDIUM, HARD")


class ExternalVideoCandidateResponse(BaseModel):
    id: UUID
    source: str
    external_id: str
    title: str
    description: Optional[str] = None
    channel_or_author: Optional[str] = None
    url: str
    thumbnail_url: Optional[str] = None
    duration_seconds: Optional[int] = None
    status: str
    classification_confidence: Optional[float] = None
    recommended_difficulty: Optional[str] = None
    content_node_id: Optional[UUID] = None
    converted_resource_id: Optional[UUID] = None
    created_at: datetime
