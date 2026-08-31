"""
Pydantic schemas for Initial Diagnostic API endpoints (Phase 13).
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .learning_path import PracticeQuestionOption


class DiagnosticStartRequest(BaseModel):
    school_id: Optional[UUID] = Field(None, description="Optional school_id. Leave null for Independent student.")
    classroom_id: Optional[str] = Field(None, description="Optional classroom_id.")
    academic_year: str = Field("2026", description="Academic year e.g. 2026")
    grade_level: Optional[str] = Field("3ª Série", description="Grade level e.g. 3ª Série")
    discipline: str = Field("Química", description="Discipline name")
    diagnostic_version: str = Field("v1", description="Algorithm version")
    metadata: Optional[dict[str, Any]] = None


class DiagnosticQuestionResponse(BaseModel):
    selection_id: UUID
    position: int
    question_version_id: UUID
    content_node_id: UUID
    difficulty_level: str
    canonical_text: str
    options: list[PracticeQuestionOption] = Field(default_factory=list)


class DiagnosticStartResponse(BaseModel):
    diagnostic_id: UUID
    student_id: str
    school_id: Optional[UUID] = None
    is_independent: bool
    status: str
    diagnostic_version: str
    started_at: datetime
    next_question: Optional[DiagnosticQuestionResponse] = None


class DiagnosticAnswerRequest(BaseModel):
    selected_option_id: Optional[UUID] = None
    response_text: Optional[str] = None


class DiagnosticAnswerResponse(BaseModel):
    diagnostic_id: UUID
    is_correct: bool
    is_complete: bool
    status: str
    questions_asked: int
    overall_confidence: float
    next_question: Optional[DiagnosticQuestionResponse] = None


class ContentMasteryEstimate(BaseModel):
    content_node_id: UUID
    content_name: str
    estimated_mastery: float
    confidence: float
    recommended_difficulty: str
    evidence_origin: str = "INITIAL_DIAGNOSTIC"


class ProbableGap(BaseModel):
    content_node_id: UUID
    content_name: str
    estimated_mastery: float
    prerequisite_check_required: bool = False


class DiagnosticResultResponse(BaseModel):
    diagnostic_id: UUID
    student_id: str
    school_id: Optional[UUID] = None
    is_independent: bool
    status: str
    diagnostic_version: str
    total_questions_asked: int
    total_correct: int
    overall_confidence: float
    started_at: datetime
    completed_at: Optional[datetime] = None
    mastery_map: list[ContentMasteryEstimate] = Field(default_factory=list)
    probable_gaps: list[ProbableGap] = Field(default_factory=list)
    evidence_count: int = 0
