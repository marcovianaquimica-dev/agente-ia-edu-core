"""HTTP contract for the student domain-map projection."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EvidenceContextResponse(BaseModel):
    activity_type: str
    assessment_attempt_id: UUID | None = None
    practice_session_id: UUID | None = None
    evidence_count: int
    last_evidence_at: datetime


class PedagogicalContextResponse(BaseModel):
    source: str
    title: str | None = None
    recorded_at: datetime


class PrerequisiteStateResponse(BaseModel):
    content_node_id: UUID
    content_name: str
    hypothesis_status: str
    hypothesis_confidence: float
    mastery_score: float | None = None
    confidence: float
    evidence_count: int


class DomainMapContentResponse(BaseModel):
    content_node_id: UUID
    content_name: str
    node_type: str
    discipline_id: UUID | None = None
    discipline_name: str | None = None
    area_id: UUID | None = None
    area_name: str | None = None
    mastery_score: float | None = None
    confidence: float
    evidence_count: int
    correct_count: int
    error_count: int
    unknown_count: int
    evidence_origins: dict[str, int]
    evidence_contexts: list[EvidenceContextResponse] = Field(default_factory=list)
    trend: str
    trend_delta: float | None = None
    state: str
    is_gap: bool
    last_evidence_at: datetime | None = None
    pedagogical_contexts: list[PedagogicalContextResponse] = Field(default_factory=list)
    objective_aligned: bool
    prerequisites: list[PrerequisiteStateResponse] = Field(default_factory=list)
    recent_practice_count: int


class NextBestActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action: str
    target_content_node_id: UUID
    target_content_name: str
    related_content_node_id: UUID | None = None
    priority_score: float
    reason: str
    factors: list[str]


class StudentDomainMapResponse(BaseModel):
    student_id: str
    school_id: str | None = None
    is_independent: bool
    universe: dict[str, Any] | None = None
    generated_at: datetime
    summary: dict[str, int]
    contents: list[DomainMapContentResponse] = Field(default_factory=list)
    next_best_action: NextBestActionResponse | None = None


__all__ = ["StudentDomainMapResponse"]