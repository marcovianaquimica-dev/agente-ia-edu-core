"""Read-only Pydantic schemas for the PHASE 12 Question Bank API.

These mirror the plain dataclasses returned by
``agente_ia_edu.services.question_bank.QuestionBankService``. The API layer maps
service DTOs -> these models; no business logic lives here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class QBOption(BaseModel):
    """A question alternative in official order.

    The answer key (``is_valid_option``) is intentionally NOT part of this
    professor-facing model. A future authorized feature that legitimately needs
    the key must add an explicit opt-in parameter rather than widening this DTO.
    """

    id: UUID
    key: str
    position: int
    text: str


class QBClassification(BaseModel):
    taxonomy_version: str
    discipline_code: str | None
    area_code: str | None
    content_code: str | None
    subcontent_code: str | None
    status: str
    lifecycle: str
    source: str | None
    provider_name: str | None
    model_version: str | None
    prompt_version: str | None
    classification_mode: str | None
    confidence: str | None
    numeric_confidence: float | None
    review_reason: str | None
    closure_phase: str | None
    visual_dependency: bool
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    context: str | None
    created_at: str | None


class QBAsset(BaseModel):
    kind: str
    reference: str | None
    present: bool


class QBQuestion(BaseModel):
    """Full question detail (preview). Returned only by GET /questions/{id}."""

    question_id: UUID
    question_version_id: UUID
    version_kind: str
    year: int | None
    day: int | None
    enem_area: str | None
    enem_area_label: str | None
    booklet_code: str | None
    official_number: int | None
    position: int | None
    canonical_text: str
    statement: str | None
    recommended_difficulty: str | None
    options: list[QBOption]
    classification: QBClassification | None
    classification_state: str
    is_protected: bool
    has_visual_dependency: bool
    assets: list[QBAsset] = Field(default_factory=list)
    evidence_uri: str | None


class QBQuestionSummary(BaseModel):
    """Light list row - NO options, NO full body. Keeps list responses cheap for
    a 10k+ bank; the Professor opens one question at a time for the full text."""

    question_id: UUID
    question_version_id: UUID
    year: int | None
    day: int | None
    enem_area: str | None
    enem_area_label: str | None
    booklet_code: str | None
    official_number: int | None
    position: int | None
    statement_preview: str
    recommended_difficulty: str | None
    classification: QBClassification | None
    # Denormalized from classification.content_code: teacher.js's "Meus
    # Materiais" question search reads a top-level content_code on each row
    # (classification is a richer object added later for the full Question
    # Bank screen) - without this, every row showed a blank content code.
    content_code: str | None
    classification_state: str
    is_protected: bool
    has_visual_dependency: bool


class QBPagination(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class QBQuestionListResponse(BaseModel):
    items: list[QBQuestionSummary]
    pagination: QBPagination


class QBSelectionEntry(BaseModel):
    position: int
    question_version_id: UUID
    question_id: UUID
    year: int | None
    official_number: int | None
    enem_area: str | None
    content_code: str | None


class QBSelectionRequest(BaseModel):
    question_version_ids: list[UUID] = Field(min_length=1, max_length=500)
    source: str = "manual"


class QBSelectionResponse(BaseModel):
    entries: list[QBSelectionEntry]
    source: str
    count: int


# ---------------------------------------------------------------------------
# PHASE 14 - list generator
# ---------------------------------------------------------------------------


class QBListGenerateRequest(BaseModel):
    question_version_ids: list[UUID] = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    instructions: str | None = Field(default=None, max_length=4000)
    activity_mode: str = "EXERCISE_LIST"
    answer_key_presentation: str = "NONE"
    resolution_style: str | None = None


class QBGeneratedOption(BaseModel):
    key: str
    position: int
    text: str


class QBResolution(BaseModel):
    available: bool
    style: str | None
    text: str | None
    unavailable_reason: str | None


class QBAnswerKey(BaseModel):
    correct_option_key: str
    correct_option_id: str | None
    source: str
    revision_number: int | None
    resolution: QBResolution | None


class QBGeneratedListItem(BaseModel):
    position: int
    question_version_id: str
    question_id: str
    source: str
    year: int | None
    day: int | None
    official_number: int | None
    booklet_code: str | None
    original_position: int | None
    enem_area: str | None
    discipline_code: str | None
    content_code: str | None
    subcontent_code: str | None
    classification_state: str
    statement: str
    options: list[QBGeneratedOption]
    answer_key: QBAnswerKey | None


class QBListConfiguration(BaseModel):
    title: str
    instructions: str | None
    activity_mode: str
    answer_key_presentation: str
    resolution_style: str | None


class QBGeneratedListDefinition(BaseModel):
    configuration: QBListConfiguration
    items: list[QBGeneratedListItem]
    question_count: int
    question_version_ids: list[str]
    answer_key_included: bool
    resolution_included: bool
    generated_at: str
    selection_fingerprint: str
    future_compat: dict


# ---------------------------------------------------------------------------
# PHASE 15 - list persistence / history
# ---------------------------------------------------------------------------


class QBListPersistRequest(BaseModel):
    question_version_ids: list[UUID] = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    instructions: str | None = Field(default=None, max_length=4000)
    activity_mode: str = "EXERCISE_LIST"
    answer_key_presentation: str = "NONE"
    resolution_style: str | None = None


class QBListUpdateRequest(BaseModel):
    question_version_ids: list[UUID] | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    instructions: str | None = Field(default=None, max_length=4000)
    activity_mode: str | None = None
    answer_key_presentation: str | None = None
    resolution_style: str | None = None


class QBStoredListSummary(BaseModel):
    id: str
    title: str
    instructions: str | None
    status: str
    activity_mode: str
    answer_key_presentation: str
    resolution_style: str | None
    question_count: int
    selection_fingerprint: str
    owner_external_id: str | None
    school_id: str | None
    created_at: str
    updated_at: str
    finalized_at: str | None


class QBStoredListListResponse(BaseModel):
    items: list[QBStoredListSummary]
    pagination: QBPagination


class QBPersistedListDetail(QBGeneratedListDefinition):
    persisted: dict


# ---------------------------------------------------------------------------
# PHASE 16 - activity distribution
# ---------------------------------------------------------------------------


class QBAssignmentCreateRequest(BaseModel):
    target_type: str  # STUDENT | CLASS
    target_id: str = Field(min_length=1, max_length=255)
    available_from: str | None = None
    due_at: str | None = None
    academic_year: str | None = Field(default=None, max_length=20)


class QBAssignmentUpdateRequest(BaseModel):
    available_from: str | None = None
    due_at: str | None = None
    status: str | None = None
    clear_available_from: bool = False
    clear_due_at: bool = False


class QBAssignment(BaseModel):
    id: str
    assessment_id: str
    assessment_version_id: str
    list_id: str
    title: str
    school_id: str | None
    created_by_external_id: str | None
    target_type: str
    target_id: str
    available_from: str | None
    due_at: str | None
    status: str
    availability: str
    selection_fingerprint: str | None
    question_count: int
    answer_key_presentation: str | None
    created_at: str
    updated_at: str


class QBAssignmentListResponse(BaseModel):
    items: list[QBAssignment]
    summary: dict


class QBStudentActivity(BaseModel):
    assignment_id: str
    list_id: str
    assessment_version_id: str
    title: str
    author_external_id: str | None
    question_count: int
    available_from: str | None
    due_at: str | None
    status: str
    availability: str
    target_type: str


class QBStudentActivityListResponse(BaseModel):
    items: list[QBStudentActivity]


__all__ = [
    "QBAnswerKey",
    "QBAsset",
    "QBClassification",
    "QBGeneratedListDefinition",
    "QBGeneratedListItem",
    "QBGeneratedOption",
    "QBListConfiguration",
    "QBListGenerateRequest",
    "QBOption",
    "QBPagination",
    "QBQuestion",
    "QBQuestionListResponse",
    "QBQuestionSummary",
    "QBResolution",
    "QBSelectionEntry",
    "QBSelectionRequest",
    "QBSelectionResponse",
]
