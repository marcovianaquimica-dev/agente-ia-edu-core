from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Pagination(BaseModel):
    page: int
    limit: int
    total: int
    total_pages: int = 0


class QuestionOption(BaseModel):
    id: UUID
    key: str
    position: int
    text: str


class QuestionContent(BaseModel):
    version_id: UUID
    version_kind: str
    canonical_text: str
    statement: str | None
    options: list[QuestionOption]


class SourceInstitution(BaseModel):
    code: str
    name: str


class SourceExam(BaseModel):
    code: str
    name: str


class SourceApplication(BaseModel):
    year: int
    type: str
    day: int | None


class SourceBooklet(BaseModel):
    code: str
    color: str | None
    language: str | None
    official_number: int | None
    position: int


class QuestionSource(BaseModel):
    institution: SourceInstitution
    exam: SourceExam
    application: SourceApplication
    booklets: list[SourceBooklet]


class TaxonomyReference(BaseModel):
    code: str
    version: str


class TaxonomyNodeReference(BaseModel):
    id: UUID
    code: str
    name: str


class QuestionClassification(BaseModel):
    taxonomy: TaxonomyReference
    competency: TaxonomyNodeReference
    skill: TaxonomyNodeReference
    confidence: Decimal | None
    source: str
    classifier_version: str | None


class QuestionDifficulty(BaseModel):
    score: Decimal
    band: str | None
    confidence: Decimal | None
    source: str
    method: str
    method_version: str | None


class QuestionListItem(BaseModel):
    id: UUID
    validation_status: str
    content: QuestionContent
    sources: list[QuestionSource]
    classification: QuestionClassification | None
    difficulty: QuestionDifficulty | None


class AnswerKeyItem(BaseModel):
    booklet_code: str
    label: str
    option_id: UUID | None
    revision: int
    official: bool


class QuestionDetail(QuestionListItem):
    answer_key: list[AnswerKeyItem] | None = Field(default=None)


class QuestionListResponse(BaseModel):
    items: list[QuestionListItem]
    pagination: Pagination
    model_config = ConfigDict(from_attributes=True)


class QuestionQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)
    order_by: str = Field(default="updated_at")
    order_direction: str = Field(default="desc")
    # Exam source filters
    institution_code: str | None = None
    exam_code: str | None = None
    year: int | None = Field(default=None, gt=0)
    # Question content and classification
    content: str | None = None
    subject: str | None = None
    difficulty: str | None = None
    question_type: str | None = None
    taxonomy_code: str | None = None
    bncc_competency_code: str | None = None
    bncc_skill_code: str | None = None
    pisa: str | None = None
    # Question governance filters
    status: str | None = None
    visibility_scope: str | None = None
    origin_type: str | None = None
    author_external_id: str | None = None
    school_id: str | None = None
    # Eligibility
    eligible_only: bool = Field(default=False)
    # Date range filters
    created_from: str | None = Field(default=None)
    created_to: str | None = Field(default=None)
    updated_from: str | None = Field(default=None)
    updated_to: str | None = Field(default=None)


class QuestionAuthoringRequest(BaseModel):
    statement: str
    options: list[str]
    correct_option: str
    author_type: str = "TEACHER"
    subject: str | None = None
    metadata: dict[str, str] | None = None


class QuestionReviewRequest(BaseModel):
    action: str = Field(default="submit")
    reason: str | None = None


class QuestionAuthoringResponse(BaseModel):
    question_id: UUID
    version_id: UUID
    status: str


class QuestionCreateRequest(BaseModel):
    statement: str
    options: list[str]
    correct_option: str
    question_type: str = "MULTIPLE_CHOICE"
    visibility_scope: str = "PRIVATE"
    subject: str | None = None
    difficulty: str | None = None
    metadata: dict[str, str] | None = None
    school_id: str | None = None
    author_external_id: str | None = None
    created_by_external_identity: str | None = None


class QuestionUpdateRequest(BaseModel):
    statement: str | None = None
    options: list[str] | None = None
    correct_option: str | None = None
    question_type: str | None = None
    visibility_scope: str | None = None
    subject: str | None = None
    difficulty: str | None = None
    metadata: dict[str, str] | None = None


class QuestionVersionCreateRequest(BaseModel):
    statement: str
    options: list[str]
    correct_option: str
    reason: str | None = None
    difficulty: str | None = None


class QuestionStatusTransitionRequest(BaseModel):
    status: str
    reason: str | None = None


class QuestionStatusTransitionResponse(BaseModel):
    id: UUID | None = None
    question_id: UUID
    from_status: str | None = None
    to_status: str
    performed_by_external_id: str | None = None
    reason: str | None = None


class QuestionEligibilityResponse(BaseModel):
    question_id: UUID
    is_eligible: bool
    reasons: list[str] = Field(default_factory=list)


class QuestionApprovalRequest(BaseModel):
    decision: str = Field(default="APPROVED")
    feedback_text: str | None = None


class QuestionApprovalResponse(BaseModel):
    id: UUID | None = None
    question_id: UUID
    reviewer_external_id: str
    decision: str
    feedback_text: str | None = None


class QuestionResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
