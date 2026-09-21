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
    discipline: str = Field("Quimica", description="Discipline name")
    diagnostic_version: str = Field("v1", description="Algorithm version")
    metadata: Optional[dict[str, Any]] = None
    requested_universe_id: Optional[UUID] = None


class DiagnosticEntryRequest(BaseModel):
    preferred_name: Optional[str] = Field(None, max_length=255)
    age_range: Optional[str] = Field(None, max_length=100)
    study_objectives: list[str] = Field(default_factory=list, max_length=6)
    interest_areas: list[str] = Field(default_factory=list, max_length=10)
    perceived_difficulties: list[str] = Field(default_factory=list, max_length=10)
    free_text: Optional[str] = Field(None, max_length=2000)
    discipline: Optional[str] = Field(None, max_length=255)
    content: Optional[str] = Field(None, max_length=255)
    needs_guidance: bool = False
    diagnostic_mode: str = Field("DISCIPLINE", max_length=40)
    priority_disciplines: list[str] = Field(default_factory=list, max_length=12)
    step: str = Field("WELCOME", max_length=40)
    complete: bool = False


class DiagnosticEntryResponse(BaseModel):
    diagnostic_id: UUID
    entry_status: str
    step: str
    preferred_name: Optional[str] = None
    is_independent: bool
    next_question: Optional["DiagnosticQuestionResponse"] = None
    preferred_content_resolution: Optional[dict] = None


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
    is_unknown: bool = False


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
    # NOT_EVALUATED | IN_PROGRESS | INSUFFICIENT_EVIDENCE | SUFFICIENT_EVIDENCE
    # | CONSOLIDATED | POSSIBLE_GAP (DiagnosticCoveragePolicy.assess). The
    # student portal's result screen (app.js renderDiagnosticResult) reads
    # this to decide the "stronger"/"developing" copy - keep it declared here
    # or FastAPI/pydantic silently drops it from the HTTP response.
    coverage_status: Optional[str] = None
    is_inconsistent: bool = False
    recommended_difficulty: str
    evidence_origin: str = "INITIAL_DIAGNOSTIC"


class ProbableGap(BaseModel):
    content_node_id: UUID
    content_name: str
    estimated_mastery: float
    prerequisite_check_required: bool = False
    # The service (initial_diagnostic.py get_diagnostic_result) computes this
    # whenever the gapped content has a parent node - a dict with
    # content_node_id/content_name/confidence/evidence_origin of the possible
    # prerequisite. Keep it declared here or FastAPI/pydantic silently drops
    # it from the HTTP response (same class of bug as ContentMasteryEstimate's
    # coverage_status).
    possible_prerequisite_gap: Optional[dict[str, Any]] = None


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
    raw_result: dict[str, int] = Field(default_factory=dict)
    duration_seconds: int = 0
    completion_reason: Optional[str] = None
    latest_decision: Optional[dict[str, Any]] = None
    pedagogical_states: list[dict[str, Any]] = Field(default_factory=list)
