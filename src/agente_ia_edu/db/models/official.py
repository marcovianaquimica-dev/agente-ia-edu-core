from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible


class Institution(Base):
    __tablename__ = "institutions"
    __table_args__ = (
        UniqueConstraint("code", name="uq_institutions_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    website_url: Mapped[str | None] = mapped_column(String(2048))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    exams: Mapped[list[Exam]] = relationship(back_populates="institution")


class Exam(Base):
    __tablename__ = "exams"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "code",
            name="uq_exams_institution_code",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    institution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("institutions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    institution: Mapped[Institution] = relationship(back_populates="exams")
    applications: Mapped[list[ExamApplication]] = relationship(back_populates="exam")


class ExamApplication(Base):
    __tablename__ = "exam_applications"
    __table_args__ = (
        UniqueConstraint(
            "exam_id",
            "year",
            "application_type",
            "day",
            name="uq_exam_applications_identity",
        ),
        CheckConstraint("year > 0", name="ck_exam_applications_year_positive"),
        CheckConstraint("day IS NULL OR day > 0", name="ck_exam_applications_day_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    exam_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("exams.id", ondelete="RESTRICT"),
        nullable=False,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    application_type: Mapped[str] = mapped_column(String(50), nullable=False)
    day: Mapped[int | None] = mapped_column(Integer)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    official_identifier: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    exam: Mapped[Exam] = relationship(back_populates="applications")
    booklets: Mapped[list[ExamBooklet]] = relationship(back_populates="exam_application")
    source_documents: Mapped[list[SourceDocument]] = relationship(
        back_populates="exam_application"
    )


class ExamBooklet(Base):
    __tablename__ = "exam_booklets"
    __table_args__ = (
        UniqueConstraint(
            "exam_application_id",
            "code",
            name="uq_exam_booklets_application_code",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    exam_application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("exam_applications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    color: Mapped[str | None] = mapped_column(String(50))
    language: Mapped[str | None] = mapped_column(String(20))
    booklet_type: Mapped[str | None] = mapped_column(String(50))
    official_identifier: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    exam_application: Mapped[ExamApplication] = relationship(back_populates="booklets")
    source_documents: Mapped[list[SourceDocument]] = relationship(
        back_populates="exam_booklet"
    )
    booklet_questions: Mapped[list[BookletQuestion]] = relationship(
        back_populates="exam_booklet"
    )


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    exam_application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("exam_applications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    exam_booklet_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("exam_booklets.id", ondelete="SET NULL"),
    )
    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    official_identifier: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(500))
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_uri: Mapped[str | None] = mapped_column(String(2048))
    mime_type: Mapped[str | None] = mapped_column(String(255))
    page_count: Mapped[int | None] = mapped_column(Integer)
    extraction_method: Mapped[str | None] = mapped_column(String(100))
    extractor_version: Mapped[str | None] = mapped_column(String(100))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    __table_args__ = (
        Index("ix_source_documents_content_hash", "content_hash"),
        CheckConstraint(
            "page_count IS NULL OR page_count > 0",
            name="ck_source_documents_page_count_positive",
        ),
    )

    exam_application: Mapped[ExamApplication] = relationship(
        back_populates="source_documents"
    )
    exam_booklet: Mapped[ExamBooklet | None] = relationship(
        back_populates="source_documents"
    )
    answer_key_revisions: Mapped[list[AnswerKeyRevision]] = relationship(
        back_populates="source_document"
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    validation_status: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    versions: Mapped[list[QuestionVersion]] = relationship(back_populates="question")


class QuestionVersion(Base):
    __tablename__ = "question_versions"
    __table_args__ = (
        Index(
            "uq_question_versions_official_original",
            "question_id",
            unique=True,
            postgresql_where=text("version_kind = 'official_original'"),
        ),
        Index("ix_question_versions_content_hash", "content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("questions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("question_versions.id", ondelete="RESTRICT"),
    )
    canonical_text: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    recommended_difficulty: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # EASY, MEDIUM, HARD; null = not yet classified
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    created_by_type: Mapped[str | None] = mapped_column(String(50))
    created_by_id: Mapped[str | None] = mapped_column(String(255))
    change_reason: Mapped[str | None] = mapped_column(Text)
    is_immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    question: Mapped[Question] = relationship(back_populates="versions")
    parent_version: Mapped[QuestionVersion | None] = relationship(
        remote_side=[id],
        back_populates="child_versions",
    )
    child_versions: Mapped[list[QuestionVersion]] = relationship(
        back_populates="parent_version"
    )
    options: Mapped[list[QuestionOption]] = relationship(back_populates="question_version")
    booklet_questions: Mapped[list[BookletQuestion]] = relationship(
        back_populates="question_version"
    )
    pedagogical_classifications: Mapped[list["PedagogicalClassification"]] = relationship(
        "PedagogicalClassification", back_populates="question_version"
    )
    ingestion_questions: Mapped[list["IngestionQuestion"]] = relationship(
        "IngestionQuestion", back_populates="question_version"
    )


class QuestionOption(Base):
    __tablename__ = "question_options"
    __table_args__ = (
        UniqueConstraint(
            "question_version_id",
            "option_key",
            name="uq_question_options_version_key",
        ),
        UniqueConstraint(
            "question_version_id",
            "position",
            name="uq_question_options_version_position",
        ),
        CheckConstraint("position > 0", name="ck_question_options_position_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("question_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    option_key: Mapped[str] = mapped_column(String(10), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_valid_option: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    question_version: Mapped[QuestionVersion] = relationship(back_populates="options")
    answer_key_entries: Mapped[list[AnswerKeyEntry]] = relationship(
        back_populates="resolved_option"
    )


class BookletQuestion(Base):
    __tablename__ = "booklet_questions"
    __table_args__ = (
        UniqueConstraint(
            "exam_booklet_id",
            "position",
            name="uq_booklet_questions_booklet_position",
        ),
        Index(
            "ix_booklet_questions_booklet_official_number_not_null",
            "exam_booklet_id",
            "official_number",
            unique=True,
            postgresql_where=text("official_number IS NOT NULL"),
        ),
        CheckConstraint("position > 0", name="ck_booklet_questions_position_positive"),
        CheckConstraint(
            "official_number IS NULL OR official_number > 0",
            name="ck_booklet_questions_official_number_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    exam_booklet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("exam_booklets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("question_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    official_number: Mapped[int | None] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    extraction_method: Mapped[str | None] = mapped_column(String(100))
    extractor_version: Mapped[str | None] = mapped_column(String(100))
    evidence_uri: Mapped[str | None] = mapped_column(String(2048))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    exam_booklet: Mapped[ExamBooklet] = relationship(back_populates="booklet_questions")
    question_version: Mapped[QuestionVersion] = relationship(
        back_populates="booklet_questions"
    )
    answer_key_entries: Mapped[list[AnswerKeyEntry]] = relationship(
        back_populates="booklet_question"
    )


class AnswerKeyRevision(Base):
    __tablename__ = "answer_key_revisions"
    __table_args__ = (
        CheckConstraint(
            "revision_number > 0",
            name="ck_answer_key_revisions_revision_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("source_documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("answer_key_revisions.id", ondelete="RESTRICT"),
    )
    is_official: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    source_document: Mapped[SourceDocument] = relationship(
        back_populates="answer_key_revisions"
    )
    supersedes: Mapped[AnswerKeyRevision | None] = relationship(
        remote_side=[id],
        back_populates="superseded_by",
    )
    superseded_by: Mapped[list[AnswerKeyRevision]] = relationship(
        back_populates="supersedes"
    )
    entries: Mapped[list[AnswerKeyEntry]] = relationship(back_populates="revision")


class AnswerKeyEntry(Base):
    """An official answer for one booklet occurrence.

    The foreign key to resolved_option does not enforce that the option belongs
    to the question version referenced by booklet_question. That cross-row
    validation is intentionally deferred to a later database constraint.
    """

    __tablename__ = "answer_key_entries"
    __table_args__ = (
        UniqueConstraint(
            "answer_key_revision_id",
            "booklet_question_id",
            name="uq_answer_key_entries_revision_question",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    answer_key_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("answer_key_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    booklet_question_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("booklet_questions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    official_answer_label: Mapped[str] = mapped_column(String(10), nullable=False)
    resolved_option_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("question_options.id", ondelete="SET NULL"),
    )
    page_number: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)

    revision: Mapped[AnswerKeyRevision] = relationship(back_populates="entries")
    booklet_question: Mapped[BookletQuestion] = relationship(
        back_populates="answer_key_entries"
    )
    resolved_option: Mapped[QuestionOption | None] = relationship(
        back_populates="answer_key_entries"
    )
