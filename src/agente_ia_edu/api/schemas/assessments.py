from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AssessmentCreateRequest(BaseModel):
    title: str
    description: str | None = None
    institution_id: str | None = None
    created_by_external_identity: str | None = None


class AssessmentVersionCreateRequest(BaseModel):
    title: str
    description: str | None = None
    status: str = "draft"
    created_by_external_identity: str | None = None


class AssessmentItemCreateRequest(BaseModel):
    question_version_id: UUID
    position: int = Field(ge=1)
    points: int = Field(default=1, ge=0)
    is_required: bool = True
    selection_request_id: UUID | None = None


class AssessmentPublicationCreateRequest(BaseModel):
    publication_type: str = "immediate"
    released_immediately: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    time_limit_seconds: int | None = Field(default=None, gt=0)
    attempts_allowed: int | None = Field(default=None, gt=0)
    source_display: str = "none"
    bncc_display: str = "none"
    show_difficulty: bool = False


class AssessmentResponse(BaseModel):
    id: UUID
    title: str
    description: str | None = None
    status: str
    institution_id: str | None = None
    created_by_external_identity: str | None = None


class AssessmentVersionResponse(BaseModel):
    id: UUID
    assessment_id: UUID
    version_number: int
    title: str
    description: str | None = None
    status: str
    created_by_external_identity: str | None = None


class AssessmentListResponse(BaseModel):
    items: list[AssessmentResponse]
    total: int


class QuestionOptionResponse(BaseModel):
    id: UUID
    option_key: str
    text: str
    position: int


class AssessmentItemResponse(BaseModel):
    id: UUID
    position: int
    points: int
    is_required: bool
    question_version_id: UUID
    options: list[QuestionOptionResponse]


class AttemptStartRequest(BaseModel):
    pass


class AttemptStartResponse(BaseModel):
    id: UUID
    publication_id: UUID
    attempt_number: int
    status: str
    started_at: datetime
    expires_at: datetime | None = None
    score: float | None = None
    max_score: float | None = None


class AttemptDetailResponse(BaseModel):
    id: UUID
    publication_id: UUID
    attempt_number: int
    status: str
    started_at: datetime
    submitted_at: datetime | None = None
    expires_at: datetime | None = None
    score: float | None = None
    max_score: float | None = None
    correct_answers: int | None = None
    answered_count: int | None = None
    items: list[AssessmentItemResponse]


class AnswerItemResponse(BaseModel):
    assessment_item_id: UUID
    selected_option_id: UUID | None = None
    response_text: str | None = None
    is_correct: bool | None = None
    points_awarded: float | None = None
    correction_status: str


class AttemptAnswerSaveRequest(BaseModel):
    selected_option_id: UUID | None = None
    response_text: str | None = None


class AttemptAnswerSaveResponse(BaseModel):
    assessment_item_id: UUID
    selected_option_id: UUID | None = None
    response_text: str | None = None
    correction_status: str
    first_answered_at: datetime


class AttemptSubmitResponse(BaseModel):
    id: UUID
    status: str
    submitted_at: datetime
    score: float | None = None
    max_score: float | None = None
    correct_answers: int | None = None
    answered_count: int | None = None


class AttemptResultResponse(BaseModel):
    id: UUID
    score: float | None = None
    max_score: float | None = None
    percentage: float | None = None
    correct_answers: int | None = None
    incorrect_answers: int | None = None
    unanswered: int | None = None
    answered_count: int | None = None
    total_items: int
    duration_seconds: int | None = None
    answers: list[AnswerItemResponse]
