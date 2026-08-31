"""
Pydantic schemas for Teaching Context and Lesson Registration API endpoints (Phase 12B.1).
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TeachingLessonCreateRequest(BaseModel):
    school_id: UUID
    classroom_id: str = Field(..., description="Target classroom identifier e.g. TURMA_3A")
    content_node_id: UUID
    subcontent_node_id: Optional[UUID] = None
    academic_year: str = Field("2026", description="Academic year e.g. 2026")
    unit_id: Optional[str] = None
    segment_id: Optional[str] = None
    grade_level: Optional[str] = None
    lesson_date: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(None, ge=1)
    title: Optional[str] = Field(None, max_length=500)
    summary_observation: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class TeachingLessonResponse(BaseModel):
    id: UUID
    school_id: UUID
    academic_year: str
    unit_id: Optional[str] = None
    segment_id: Optional[str] = None
    grade_level: Optional[str] = None
    classroom_id: str
    teacher_id: str
    content_node_id: UUID
    subcontent_node_id: Optional[UUID] = None
    lesson_date: datetime
    duration_minutes: Optional[int] = None
    title: Optional[str] = None
    summary_observation: Optional[str] = None
    pedagogical_context_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


class CoordinationContextCreateRequest(BaseModel):
    school_id: UUID
    content_node_id: UUID
    classroom_id: Optional[str] = None
    source: str = Field("COORDINATION", description="COORDINATION or SCHOOL_PLAN")
    title: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    academic_year: str = Field("2026", description="Academic year e.g. 2026")
    recorded_at: Optional[datetime] = None
    metadata: Optional[dict[str, Any]] = None


class PedagogicalContextResponse(BaseModel):
    id: UUID
    content_node_id: UUID
    source: str
    institution_id: Optional[str] = None
    classroom_id: Optional[str] = None
    author_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    recorded_at: datetime
    active: bool
    created_at: datetime
