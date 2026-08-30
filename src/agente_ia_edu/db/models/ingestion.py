"""
Ingestion Engine domain models (Phase 3 - MVP foundation).

Tracks document ingestion, preserves original files, extracts structure
deterministically with full traceability between extracted content and
source document/page/position/section.

No LLM-based extraction in this phase - all operations are deterministic
and verifiable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible


class IngestionDocument(Base):
    """
    A document submitted for ingestion (DOCX, PDF, etc.).

    Preserves the original file, metadata, and status. Each document
    gets a unique ID and maintains traceability for all extracted content.
    """

    __tablename__ = "ingestion_documents"
    __table_args__ = (
        CheckConstraint(
            "document_type IN ('DOCX', 'PDF', 'OTHER')",
            name="ck_ingestion_documents_document_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'processed', 'failed', 'archived')",
            name="ck_ingestion_documents_status",
        ),
        Index("ix_ingestion_documents_document_hash", "document_hash"),
        Index("ix_ingestion_documents_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    document_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Hash of file contents (MD5/SHA256) for idempotency detection
    document_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    # Path where original file is stored (S3, local, etc.)
    storage_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    # File size in bytes
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Original document metadata
    title: Mapped[str | None] = mapped_column(String(500))
    author: Mapped[str | None] = mapped_column(String(500))
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # Ingestion metadata
    ingested_by_external_identity: Mapped[str | None] = mapped_column(String(255))
    ingestion_error: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    ingestion_runs: Mapped[list[IngestionRun]] = relationship(back_populates="document")
    sections: Mapped[list[IngestionSection]] = relationship(back_populates="document")
    questions: Mapped[list[IngestionQuestion]] = relationship(back_populates="document")


class IngestionRun(Base):
    """
    A single ingestion run for a document.

    Tracks when the document was processed, what version of the parser
    was used, and overall extraction statistics.
    """

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        CheckConstraint(
            "run_status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_ingestion_runs_run_status",
        ),
        Index("ix_ingestion_runs_document_id", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ingestion_documents.id", ondelete="RESTRICT"), nullable=False
    )
    run_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False)
    # Extraction statistics
    sections_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    questions_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    images_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tables_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    document: Mapped[IngestionDocument] = relationship(back_populates="ingestion_runs")


class IngestionSection(Base):
    """
    A structural section extracted from a document
    (e.g., Season 01, Episode 1, subsection, chapter, etc.).

    Maintains parent-child relationships for hierarchical structure.
    """

    __tablename__ = "ingestion_sections"
    __table_args__ = (
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_ingestion_sections_parent_not_self",
        ),
        CheckConstraint(
            "section_type IN ('SEASON', 'EPISODE', 'CHAPTER', 'SUBSECTION', 'SECTION', 'OTHER')",
            name="ck_ingestion_sections_section_type",
        ),
        Index("ix_ingestion_sections_document_id", "document_id"),
        Index("ix_ingestion_sections_parent_id", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ingestion_documents.id", ondelete="RESTRICT"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ingestion_sections.id", ondelete="RESTRICT")
    )
    section_type: Mapped[str] = mapped_column(String(50), nullable=False)
    section_number: Mapped[str | None] = mapped_column(String(50))
    title: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    # Position within document
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    # Extracted content preview
    content_preview: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    document: Mapped[IngestionDocument] = relationship(back_populates="sections")
    parent: Mapped[IngestionSection | None] = relationship(
        remote_side=[id], back_populates="children", foreign_keys=[parent_id]
    )
    children: Mapped[list[IngestionSection]] = relationship(
        back_populates="parent", foreign_keys=[parent_id]
    )


class IngestionQuestion(Base):
    """
    A question extracted from a document.

    Preserves the original text, alternatives, answer key, and page/position
    references for full traceability.
    """

    __tablename__ = "ingestion_questions"
    __table_args__ = (
        CheckConstraint(
            "question_type IN ('MULTIPLE_CHOICE', 'TRUE_FALSE', 'SHORT_ANSWER', 'ESSAY', 'OTHER')",
            name="ck_ingestion_questions_question_type",
        ),
        CheckConstraint(
            "status IN ('extracted', 'validated', 'imported', 'rejected')",
            name="ck_ingestion_questions_status",
        ),
        Index("ix_ingestion_questions_document_id", "document_id"),
        Index("ix_ingestion_questions_section_id", "section_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ingestion_documents.id", ondelete="RESTRICT"), nullable=False
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ingestion_sections.id", ondelete="RESTRICT")
    )
    question_number: Mapped[int] = mapped_column(Integer, nullable=False)
    question_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Original extracted text (never modified)
    statement_text: Mapped[str] = mapped_column(Text, nullable=False)
    # For multiple choice: comma-separated alternatives
    alternatives_text: Mapped[str | None] = mapped_column(Text)
    # Letter/number of the correct answer (if present in source)
    correct_answer: Mapped[str | None] = mapped_column(String(10))
    # Explanation/commentary from the source (if present)
    answer_explanation: Mapped[str | None] = mapped_column(Text)
    # Position within document
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    # Status in ingestion pipeline
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="extracted")
    # Link to actual Question/QuestionVersion if already imported
    question_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("question_versions.id", ondelete="RESTRICT")
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    document: Mapped[IngestionDocument] = relationship(back_populates="questions")
    section: Mapped[IngestionSection | None] = relationship(foreign_keys=[section_id])
    question_version: Mapped[Optional["QuestionVersion"]] = relationship("QuestionVersion", back_populates="ingestion_questions")


class IngestionAsset(Base):
    """
    An image, table, or other asset extracted from a document.

    Maintains reference to its source location and context.
    """

    __tablename__ = "ingestion_assets"
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('IMAGE', 'TABLE', 'FORMULA', 'DIAGRAM', 'OTHER')",
            name="ck_ingestion_assets_asset_type",
        ),
        Index("ix_ingestion_assets_document_id", "document_id"),
        Index("ix_ingestion_assets_question_id", "question_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ingestion_documents.id", ondelete="RESTRICT"), nullable=False
    )
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Identifier from source document (if any)
    asset_name: Mapped[str | None] = mapped_column(String(500))
    # Where it's stored
    storage_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    # Context: which question/section did it come from
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ingestion_sections.id", ondelete="RESTRICT")
    )
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ingestion_questions.id", ondelete="RESTRICT")
    )
    # Position within its context
    page: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
