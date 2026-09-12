from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AssessmentCreateRequest(BaseModel):
    title: str
    description: str | None = None
    institution_id: str | None = None
    school_id: UUID | None = None
    academic_year: str = "2026"
    scope_type: str | None = None
    scope_external_id: str | None = None
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
    assessment_version_id: UUID
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
    school_id: UUID | None = None
    owner_external_id: str | None = None
    scope_type: str | None = None
    scope_external_id: str | None = None
    created_by_external_identity: str | None = None


class AssessmentVersionResponse(BaseModel):
    id: UUID
    assessment_id: UUID
    version_number: int
    title: str
    description: str | None = None
    status: str
    created_by_external_identity: str | None = None
    published_at: datetime | None = None


class AssessmentPublicationResponse(BaseModel):
    id: UUID
    assessment_id: UUID
    assessment_version_id: UUID
    publication_type: str
    status: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    time_limit_seconds: int | None = None
    attempts_allowed: int | None = None


class AssessmentAssignmentCreateRequest(BaseModel):
    student_external_id: str = Field(min_length=1, max_length=255)


class AssessmentAssignmentResponse(BaseModel):
    id: UUID
    assessment_id: UUID
    publication_id: UUID
    student_external_id: str
    school_id: UUID
    status: str


class AssessmentListResponse(BaseModel):
    items: list[AssessmentResponse]
    total: int


class ExerciseListCreateRequest(BaseModel):
    title: str
    description: str | None = None
    school_id: str | None = None
    institution_id: str | None = None
    owner_external_id: str | None = None
    visibility_scope: str = "SCHOOL"
    origin_type: str = "SCHOOL"
    created_by_external_identity: str | None = None


class ExerciseListResponse(BaseModel):
    id: UUID
    title: str
    description: str | None = None
    status: str
    school_id: str | None = None
    institution_id: str | None = None
    created_by_external_identity: str | None = None
    owner_external_id: str | None = None
    visibility_scope: str = "SCHOOL"
    origin_type: str = "SCHOOL"


class ExerciseListListResponse(BaseModel):
    items: list[ExerciseListResponse]
    total: int


class TeacherMaterialCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content_node_id: UUID
    quantity: int = Field(gt=0)
    difficulty_distribution: dict[str, int]


class TeacherMaterialItemCreateRequest(BaseModel):
    question_version_id: UUID


class TeacherMaterialReorderRequest(BaseModel):
    item_ids: list[UUID] = Field(min_length=1)


class TeacherMaterialQuestionResponse(BaseModel):
    id: UUID
    question_version_id: UUID
    question_number: int
    stem: str
    alternatives: list[QuestionOptionResponse]
    difficulty: str
    content: str
    source: str = "Banco de Questoes"
    modified: bool = False


class TeacherMaterialResponse(BaseModel):
    id: UUID
    title: str
    material_type: str
    status: str
    configuration: dict
    items: list[TeacherMaterialQuestionResponse]


class TeacherMaterialCandidateResponse(BaseModel):
    availability: dict[str, int]
    items: list[TeacherMaterialQuestionResponse]
    pagination: dict[str, int]


class ModificationProposalCreateRequest(BaseModel):
    assessment_item_id: UUID
    modification_type: str
    instruction: str | None = Field(default=None, max_length=2000)


class ModificationProposalResponse(BaseModel):
    id: UUID
    status: str
    original_question_version_id: UUID
    assessment_item_id: UUID
    modification_type: str
    instruction: str | None = None
    proposal: dict
    provider: str
    model: str
    requested_by_external_id: str
    school_id: UUID
    created_at: datetime
    question_version_id: UUID | None = None


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
    question_number: int
    total_questions: int
    canonical_text: str
    options: list[QuestionOptionResponse]


class AttemptStartRequest(BaseModel):
    pass


class AttemptStartResponse(BaseModel):
    id: UUID
    publication_id: UUID
    assignment_id: UUID
    assessment_version_id: UUID
    attempt_number: int
    status: str
    started_at: datetime
    expires_at: datetime | None = None
    score: float | None = None
    max_score: float | None = None


class AttemptDetailResponse(BaseModel):
    id: UUID
    publication_id: UUID
    assignment_id: UUID
    assessment_version_id: UUID
    attempt_number: int
    status: str
    started_at: datetime
    submitted_at: datetime | None = None
    expires_at: datetime | None = None
    score: float | None = None
    max_score: float | None = None
    correct_answers: int | None = None
    answered_count: int | None = None
    total_questions: int
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
    is_unknown: bool = False


class AttemptAnswerSaveResponse(BaseModel):
    assessment_item_id: UUID
    selected_option_id: UUID | None = None
    response_text: str | None = None
    is_unknown: bool = False
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
    incorrect_answers: int = 0
    unknown_answers: int = 0
    unanswered: int = 0


class AttemptResultResponse(BaseModel):
    id: UUID
    score: float | None = None
    max_score: float | None = None
    percentage: float | None = None
    correct_answers: int | None = None
    incorrect_answers: int | None = None
    unknown_answers: int = 0
    unanswered: int | None = None
    answered_count: int | None = None
    total_items: int
    duration_seconds: int | None = None
    answers: list[AnswerItemResponse]
