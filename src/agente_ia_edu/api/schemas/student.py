"""
Pydantic schemas for Student Dashboard and Student Experience API (Phase 11).
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class StudentSummaryStats(BaseModel):
    overall_average: float = Field(0.0, description="Overall score average percentage (0-100)")
    contents_mastered: int = Field(0, description="Count of content nodes with mastery >= 70%")
    questions_answered: int = Field(0, description="Total practice & assessment questions answered")
    questions_correct: int = Field(0, description="Total correct questions")
    streak_days: int = Field(1, description="Active participation streak in days")


class ActionPlanCategory(BaseModel):
    content_node_id: UUID
    content_name: str
    mastery_score: float
    current_level: str  # EASY, MEDIUM, HARD
    status_label: str  # e.g., "Precisa melhorar", "Em desenvolvimento", "Domínio consolidado"


class StudentActionPlan(BaseModel):
    needs_improvement: list[ActionPlanCategory] = Field(default_factory=list)  # < 50%
    in_development: list[ActionPlanCategory] = Field(default_factory=list)     # 50-69%
    consolidated: list[ActionPlanCategory] = Field(default_factory=list)       # >= 70%


class LearningPathStep(BaseModel):
    step_number: int
    title: str
    step_type: str  # MATERIAL, VIDEO, PRACTICE, REEVALUATE
    status: str     # completed, in_progress, pending, not_available
    description: str
    resource_id: Optional[UUID] = None
    question_version_id: Optional[UUID] = None


class ActiveRecommendationStep(BaseModel):
    recommendation_id: UUID
    content_node_id: UUID
    content_name: str
    mastery_score: float
    recommended_difficulty: str
    context_source: str  # TEACHER, COORDINATION, SCHOOL_PLAN, AUTONOMOUS
    reason: str
    steps: list[LearningPathStep] = Field(default_factory=list)
    primary_resource: Optional[dict[str, Any]] = None
    practice_questions_count: int = 0


class StudentDashboardResponse(BaseModel):
    student_id: str
    time_period: str  # academic_year, last_30_days, bimester, semester, custom
    has_data: bool = False
    welcome_message: str
    summary: StudentSummaryStats
    active_recommendation: Optional[ActiveRecommendationStep] = None
    action_plan: StudentActionPlan
    mastery_breakdown: list[ActionPlanCategory] = Field(default_factory=list)


class EvolutionPoint(BaseModel):
    date_label: str
    average_score: float
    questions_answered: int
    questions_correct: int


class ContentEvolutionItem(BaseModel):
    content_node_id: UUID
    content_name: str
    initial_score: float
    current_score: float
    progress_delta: float


class StudentEvolutionResponse(BaseModel):
    student_id: str
    time_period: str
    has_data: bool = False
    overall_evolution: list[EvolutionPoint] = Field(default_factory=list)
    content_evolution: list[ContentEvolutionItem] = Field(default_factory=list)
    accuracy_percentage: float = 0.0
    total_answered: int = 0
    total_correct: int = 0
    total_incorrect: int = 0


class StudentLearningPathResponse(BaseModel):
    student_id: str
    content_node_id: UUID
    content_name: str
    current_mastery_score: float
    recommended_difficulty: str
    context_source: str
    reason: str
    steps: list[LearningPathStep] = Field(default_factory=list)
    active_step_index: int = 0


class StudySearchItem(BaseModel):
    id: str | None = None
    title: str | None = None
    content: str | None = None
    discipline: str | None = None
    difficulty: str | None = None
    resource_type: str | None = None


class StudySearchPagination(BaseModel):
    page: int = 1
    limit: int = 10
    total: int = 0
    total_pages: int = 0


class StudySearchResponse(BaseModel):
    query: str = ""
    intent: str = "SEARCH"
    resolved_context: dict[str, Any] = Field(default_factory=dict)
    results: dict[str, list[StudySearchItem]] = Field(default_factory=lambda: {"questions": [], "materials": []})
    pagination: StudySearchPagination = Field(default_factory=StudySearchPagination)


# ---------------------------------------------------------------------------
# PHASE 17 - student activity player (execution state only; no correction/score)
# ---------------------------------------------------------------------------


class ActivityAnswerSaveRequest(BaseModel):
    """Autosave payload: the CURRENT choice for one question. ``selected_option``
    is an option key (A-E); ``null`` clears the answer. Idempotent."""

    selected_option: str | None = Field(default=None, max_length=8)


class ActivityPlayerOption(BaseModel):
    key: str
    position: int
    text: str


class ActivityPlayerQuestion(BaseModel):
    position: int
    question_version_id: str
    question_id: str | None = None
    year: int | None = None
    official_number: int | None = None
    enem_area: str | None = None
    statement: str
    options: list[ActivityPlayerOption]
    answered: bool
    selected_option: str | None = None
    answered_at: str | None = None


class ActivityPlayerAttempt(BaseModel):
    id: str | None = None
    status: str
    started_at: str | None = None
    last_activity_at: str | None = None
    completed_at: str | None = None


class ActivityPlayerActivity(BaseModel):
    assignment_id: str
    assessment_id: str
    assessment_version_id: str
    title: str
    instructions: str | None = None
    author_external_id: str | None = None
    availability: str
    available_from: str | None = None
    due_at: str | None = None
    selection_fingerprint: str | None = None
    question_count: int


class ActivityPlayerState(BaseModel):
    activity: ActivityPlayerActivity
    attempt: ActivityPlayerAttempt
    status: str
    editable: bool
    total_questions: int
    answered_count: int
    pending_count: int
    pending_positions: list[int] = Field(default_factory=list)
    current_position: int | None = None
    questions: list[ActivityPlayerQuestion] = Field(default_factory=list)
    answer_key_visible: bool = False


class ActivityAnswerSaveResponse(BaseModel):
    saved: bool
    question_version_id: str
    position: int
    selected_option: str | None = None
    answered_at: str | None = None
    answered_count: int
    pending_count: int
    total_questions: int
    status: str


# ---------------------------------------------------------------------------
# PHASE 18 - deterministic correction & student result
# ---------------------------------------------------------------------------


class ActivityResultSummary(BaseModel):
    id: str
    attempt_id: str
    assignment_id: str
    assessment_version_id: str
    student_external_id: str
    selection_fingerprint: str | None = None
    question_count: int
    answered_count: int
    correct_count: int
    incorrect_count: int
    unanswered_count: int
    aproveitamento_percent: float  # correct_count / question_count * 100 - raw, NOT a grade
    completion_status: str
    started_at: str | None = None
    completed_at: str | None = None
    corrected_at: str | None = None


class ActivityResultItemView(BaseModel):
    position: int
    question_version_id: str
    official_number: int | None = None
    status: str  # CORRECT | INCORRECT | UNANSWERED
    answered: bool
    is_correct: bool
    selected_option_key: str | None = None
    correct_option_key: str | None = None  # released only after correction
    resolution: str = "em breve"


class ActivityResultActivity(BaseModel):
    assignment_id: str
    assessment_id: str
    assessment_version_id: str
    title: str


class ActivityResultView(BaseModel):
    result: ActivityResultSummary
    activity: ActivityResultActivity
    items: list[ActivityResultItemView] = Field(default_factory=list)
    answer_key_visible: bool = True
