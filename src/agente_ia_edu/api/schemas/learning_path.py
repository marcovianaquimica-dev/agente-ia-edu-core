"""
Pydantic schemas for learning path API endpoints.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ============================================================================
# Practice Session Schemas
# ============================================================================


class PracticeSessionCreateRequest(BaseModel):
    """Request to create a practice session."""

    content_node_id: Optional[UUID] = Field(
        None, description="Content/skill to practice. If null, system will recommend."
    )
    requested_question_count: int = Field(
        10, description="Number of questions requested", ge=1, le=100
    )


class PracticeSessionResponse(BaseModel):
    """Response representing a practice session."""

    id: UUID
    external_identity_id: str
    content_node_id: Optional[UUID] = None
    recommended_difficulty: str  # EASY, MEDIUM, HARD
    requested_question_count: int
    status: str  # active, completed, abandoned
    started_at: datetime
    completed_at: Optional[datetime] = None
    recommendation_reason: Optional[str] = None


# ============================================================================
# Practice Question Selection Schemas
# ============================================================================


class PracticeQuestionOption(BaseModel):
    """Option for a practice question."""

    id: UUID
    option_key: str
    text: str
    position: int


class PracticeQuestionResponse(BaseModel):
    """Question in a practice session (not yet answered)."""

    id: UUID
    position: int
    question_version_id: UUID
    difficulty_level: str
    canonical_text: str
    options: list[PracticeQuestionOption]  # No answer key exposed pre-submission


class PracticeQuestionAnswerRequest(BaseModel):
    """Request to answer a practice question."""

    selected_option_id: Optional[UUID] = Field(
        None, description="Selected option ID (for objective questions)"
    )
    response_text: Optional[str] = Field(
        None, description="Response text (for discursive questions)"
    )


class NextPracticeQuestionResponse(BaseModel):
    """Next unanswered question in a practice session, or completion flag."""

    is_complete: bool
    question: Optional[PracticeQuestionResponse] = None


class PracticeQuestionAnswerResponse(BaseModel):
    """Confirmation of answer submission."""

    practice_question_selection_id: UUID
    is_received: bool
    position: int


class PracticeQuestionResultResponse(BaseModel):
    """Result of a single practice question (after practice completed)."""

    position: int
    question_version_id: UUID
    difficulty_level: str
    canonical_text: str
    selected_option_id: Optional[UUID] = None
    response_text: Optional[str] = None
    is_correct: Optional[bool] = None
    points_awarded: Optional[float] = None
    response_time_ms: Optional[int] = None


# ============================================================================
# Practice Completion Schemas
# ============================================================================


class PracticeSessionCompleteRequest(BaseModel):
    """Request to complete a practice session."""

    pass  # No body needed; identity from auth


class PracticeSessionResult(BaseModel):
    """Result of completed practice session."""

    practice_session_id: UUID
    total_questions: int
    answered_count: int
    correct_count: int
    incorrect_count: int
    unanswered_count: int
    score: float
    percentage: float
    updated_mastery_level: str  # EASY, MEDIUM, HARD (recommended for next)


class PracticeSessionSummaryResponse(BaseModel):
    """Summary of practice session (can be requested during or after)."""

    practice_session_id: UUID
    external_identity_id: str
    content_node_id: Optional[UUID] = None
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    requested_question_count: int
    answered_count: int
    is_complete: bool = False


# ============================================================================
# Mastery / Learning History Schemas
# ============================================================================


class StudentContentMasteryResponse(BaseModel):
    """Student's mastery in a specific content."""

    content_node_id: UUID
    mastery_score: float  # 0-100
    current_level: str  # EASY, MEDIUM, HARD
    questions_answered: int
    questions_correct: int
    confidence: float  # 0-1
    last_activity_at: Optional[datetime] = None


class StudentMasteryListResponse(BaseModel):
    """List of student's mastery in all contents."""

    external_identity_id: str
    masteries: list[StudentContentMasteryResponse]
    next_recommended_content_node_id: Optional[UUID] = None  # Lowest mastery


class LearningHistoryEntryResponse(BaseModel):
    """Single entry in learning history."""

    id: UUID
    activity_type: str  # OFFICIAL_ASSESSMENT, INDIVIDUAL_PRACTICE
    question_version_id: UUID
    difficulty_level: str
    is_correct: Optional[bool] = None
    points_awarded: Optional[float] = None
    response_time_ms: Optional[int] = None
    content_node_id: Optional[UUID] = None
    created_at: datetime


class LearningHistoryListResponse(BaseModel):
    """List of learning history entries."""

    external_identity_id: str
    entries: list[LearningHistoryEntryResponse]
    total_count: int


# ============================================================================
# Recommendation Schemas
# ============================================================================


class RecommendedContentNode(BaseModel):
    """Recommendation for content to practice."""

    content_node_id: UUID
    node_type: str  # competency, skill, subject
    name: str
    reason: str  # e.g., "Lowest mastery score", "Content requiring reinforcement"
    current_mastery_score: Optional[float] = None
    recommended_difficulty: str  # EASY, MEDIUM, HARD


class PracticeRecommendationResponse(BaseModel):
    """Recommendation for next practice."""

    recommended_content: RecommendedContentNode
    recommended_difficulty: str
    recommendation_reason: str
