from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Pagination(BaseModel):
    page: int
    limit: int
    total: int


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


class QuestionQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)
    institution_code: str | None = None
    exam_code: str | None = None
    year: int | None = Field(default=None, gt=0)
    content: str | None = None
    subject: str | None = None
    difficulty: str | None = None
    taxonomy_code: str | None = None
    bncc_competency_code: str | None = None
    bncc_skill_code: str | None = None
    pisa: str | None = None


class QuestionResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
