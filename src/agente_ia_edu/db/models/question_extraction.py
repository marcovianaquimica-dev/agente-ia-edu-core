"""PHASE 27 - Question Extraction Engine: staging models.

DOCUMENT INGESTION (PHASE 26) != QUESTION EXTRACTION (PHASE 27) - spec s20.
These tables are NEW and ADDITIVE, decoupled from ``ingestion_documents``/
``ingestion_questions`` (PHASE 3/26, untouched) and from the official
Question Bank (``questions``/``question_versions``/``question_options``,
untouched). They share only the source document (``ingestion_document_id``)
for traceability/tenant-isolation reuse (spec s20/s21) - a question staged
here is NEVER written into the official Question Bank by this phase (spec
s22): APPROVED/PUBLISHED here means "finalised in this staging store, ready
for a later, explicit Question Bank promotion" - never an automatic one.

One row per extraction attempt (``QuestionExtractionRun``, 1:1-per-run with
an ``ingestion_documents`` row via the shared hash/idempotency contract),
one row per detected question (``ExtractedQuestion``), one row per option
(``ExtractedQuestionOption``), one row per associated visual asset
(``ExtractedQuestionAsset``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible

_REVIEW_STATUSES = (
    "DISCOVERED", "EXTRACTED", "VALIDATED", "REVIEW_REQUIRED", "IN_REVIEW",
    "APPROVED", "PUBLISHED", "REJECTED", "DUPLICATE_REVIEW",
)
_QUESTION_TYPES = ("multiple_choice", "discursive", "numeric", "true_false", "unknown")
_REJECTION_REASONS = (
    "DUPLICATE", "CORRUPTED_SOURCE", "INCOMPLETE_SOURCE", "NOT_A_QUESTION",
    "UNUSABLE_CONTENT", "OTHER",
)
_ASSET_STATUSES = ("ASSOCIATED", "UNASSOCIATED", "IGNORED")
_RESOLUTION_STATUSES = ("NONE", "PENDING_REVIEW", "APPROVED", "REJECTED")


class QuestionExtractionRun(Base):
    """One extraction attempt over one source document. Idempotent by
    (ingestion_document_id, engine_version, document_hash) - re-running the
    SAME bytes through the SAME engine version reuses the existing run
    rather than creating a duplicate (spec s17/s21)."""

    __tablename__ = "question_extraction_runs"
    __table_args__ = (
        UniqueConstraint(
            "ingestion_document_id", "engine_version",
            name="uq_question_extraction_runs_document_engine",
        ),
        CheckConstraint(
            "run_status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')",
            name="ck_question_extraction_runs_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ingestion_document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ingestion_documents.id", ondelete="RESTRICT"), nullable=False
    )
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT")
    )
    engine_version: Mapped[str] = mapped_column(String(50), nullable=False)
    document_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    run_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    expected_question_count: Mapped[int | None] = mapped_column()
    detected_question_count: Mapped[int] = mapped_column(default=0, nullable=False)
    validated_question_count: Mapped[int] = mapped_column(default=0, nullable=False)
    review_required_count: Mapped[int] = mapped_column(default=0, nullable=False)
    missing_numbers: Mapped[list[int] | None] = mapped_column(JSONBCompatible)
    duplicated_numbers: Mapped[list[int] | None] = mapped_column(JSONBCompatible)
    sequence_gaps: Mapped[list[int] | None] = mapped_column(JSONBCompatible)
    validated: Mapped[bool] = mapped_column(default=False, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_by_external_identity: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    questions: Mapped[list["ExtractedQuestion"]] = relationship(back_populates="run")


class ExtractedQuestion(Base):
    """One staged, structured question. Never the official Question Bank
    (spec s22) - a fingerprint (sha256 of normalized_text + question_number)
    detects a re-extraction of the same content across runs (spec s17),
    without destroying the earlier version's history."""

    __tablename__ = "extracted_questions"
    __table_args__ = (
        CheckConstraint(f"review_status IN {_REVIEW_STATUSES!r}", name="ck_extracted_questions_review_status"),
        CheckConstraint(f"question_type IN {_QUESTION_TYPES!r}", name="ck_extracted_questions_question_type"),
        CheckConstraint("question_number > 0", name="ck_extracted_questions_number_positive"),
        CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="ck_extracted_questions_confidence_range",
        ),
        CheckConstraint(
            f"rejection_reason IS NULL OR rejection_reason IN {_REJECTION_REASONS!r}",
            name="ck_extracted_questions_rejection_reason",
        ),
        CheckConstraint(
            f"resolution_status IN {_RESOLUTION_STATUSES!r}",
            name="ck_extracted_questions_resolution_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_extraction_runs.id", ondelete="RESTRICT"), nullable=False
    )
    question_number: Mapped[int] = mapped_column(nullable=False)
    question_type: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    # spec s11: raw vs normalized are ALWAYS kept separate - no silent "fix".
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    # PHASE 28 (additive) - the LOCAL, verified column-reconstruction result
    # (services/question_extraction/reconstruction.py). NULL when no
    # reconstruction was attempted/adopted - raw_text/normalized_text alone
    # already reflect PHASE 27's original result in that case.
    reconstructed_text: Mapped[str | None] = mapped_column(Text)
    reconstruction_applied: Mapped[bool] = mapped_column(default=False, nullable=False)
    # PHASE 29 (additive) - the PROFESSOR's edited statement. A layer OVER
    # extraction (spec s7/s24): raw_text/normalized_text/reconstructed_text
    # (engine output) are never touched by a human edit. NULL until a
    # professor actually edits the statement - display falls back to
    # normalized_text/reconstructed_text until then.
    reviewed_text: Mapped[str | None] = mapped_column(Text)
    # PHASE 29 (additive) - structured reason recorded on REJECTED (spec s12).
    rejection_reason: Mapped[str | None] = mapped_column(String(30))
    # PHASE 31 (additive) - step-by-step resolution capture, independent of
    # review_status: a question can be APPROVED with its resolution still
    # PENDING_REVIEW or absent (NONE) entirely. raw_text is verbatim from the
    # source PDF's own "Resolução" section when the engine can unambiguously
    # associate it to this question number - never invented, mirrors the
    # raw/normalized separation used for the question statement itself.
    resolution_raw_text: Mapped[str | None] = mapped_column(Text)
    resolution_reviewed_text: Mapped[str | None] = mapped_column(Text)
    resolution_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NONE")
    # PHASE 29 (additive) - where this staging row was promoted to in the
    # OFFICIAL Question Bank, once PUBLISHED. NULL until then. This FK is
    # SET NULL on delete (never RESTRICT) - deleting an official question
    # must never be blocked by, or cascade into deleting, staging history.
    published_question_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("questions.id", ondelete="SET NULL")
    )
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("question_versions.id", ondelete="SET NULL")
    )
    # PHASE 28 (additive) - WHY this question needs review, structured
    # (spec s12): COLUMN_AMBIGUITY / BROKEN_READING_ORDER / MISSING_OPTION /
    # ORPHAN_TEXT / UNASSIGNED_ASSET / FORMULA_AMBIGUITY /
    # CROSS_PAGE_AMBIGUITY / LOW_CONFIDENCE / SEQUENCE_ANOMALY.
    review_reasons: Mapped[list[str] | None] = mapped_column(JSONBCompatible)
    # PHASE 28/29 (additive) - full audit trail: every review_status
    # transition AND every manual edit (statement/option/asset/orphan-text
    # decision) is APPENDED here, never overwritten (spec s21). Shape:
    # {"event": str, "actor": str, "at": iso8601, "from_status": str|None,
    #  "to_status": str|None, "detail": dict|None}.
    status_history: Mapped[list[dict] | None] = mapped_column(JSONBCompatible)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256
    extraction_confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0.0)
    flags: Mapped[list[str] | None] = mapped_column(JSONBCompatible)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="DISCOVERED")
    # spec s6/s15/s21 - full source traceability.
    source_page_start: Mapped[int | None] = mapped_column()
    source_page_end: Mapped[int | None] = mapped_column()
    cross_page: Mapped[bool] = mapped_column(default=False, nullable=False)
    # spec s21 - tenant isolation, mirrored from the run for direct querying.
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT")
    )
    reviewed_by_external_identity: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc),
    )

    run: Mapped[QuestionExtractionRun] = relationship(back_populates="questions")
    options: Mapped[list["ExtractedQuestionOption"]] = relationship(
        back_populates="question", order_by="ExtractedQuestionOption.position"
    )
    assets: Mapped[list["ExtractedQuestionAsset"]] = relationship(back_populates="question")


class ExtractedQuestionOption(Base):
    """One alternative (A-E), stored separately rather than as a single
    text blob (spec s8: 'não armazenar somente como texto único se for
    possível estruturar')."""

    __tablename__ = "extracted_question_options"
    __table_args__ = (
        UniqueConstraint("question_id", "label", name="uq_extracted_question_options_label"),
        CheckConstraint("position > 0", name="ck_extracted_question_options_position_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("extracted_questions.id", ondelete="RESTRICT"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(1), nullable=False)  # A..E
    text: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(nullable=False)

    question: Mapped[ExtractedQuestion] = relationship(back_populates="options")


class ExtractedQuestionAsset(Base):
    """A visual asset (image/graph/table) associated with one question.
    NEVER a substitute for the visual - spec s9: the original is preserved
    (page/bbox/digest), never reconstructed into invented text."""

    __tablename__ = "extracted_question_assets"
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('IMAGE', 'TABLE', 'GRAPH', 'FORMULA', 'OTHER')",
            name="ck_extracted_question_assets_type",
        ),
        CheckConstraint(f"status IN {_ASSET_STATUSES!r}", name="ck_extracted_question_assets_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # PHASE 29: nullable - an image detected in the document but not
    # auto-associated to any question (spec s8) is persisted here with
    # question_id=NULL and status=UNASSOCIATED, never silently dropped.
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("extracted_questions.id", ondelete="RESTRICT")
    )
    # PHASE 29 (additive) - lets an unassociated asset be found/listed by
    # run even before it has a question_id.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("question_extraction_runs.id", ondelete="RESTRICT")
    )
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_page: Mapped[int] = mapped_column(nullable=False)
    bbox: Mapped[list[float] | None] = mapped_column(JSONBCompatible)
    digest: Mapped[str] = mapped_column(String(128), nullable=False)
    extraction_confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0.0)
    # PHASE 29 (additive) - ASSOCIATED (auto-matched or human-associated) /
    # UNASSOCIATED (detected, not yet decided) / IGNORED (human decided it
    # is not question content) - never physically deleted either way.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ASSOCIATED")

    question: Mapped[ExtractedQuestion | None] = relationship(back_populates="assets")
