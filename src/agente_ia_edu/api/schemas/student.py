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
