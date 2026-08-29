from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible
from .official import QuestionVersion


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    institution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("institutions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_by_external_identity: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    versions: Mapped[list[AssessmentVersion]] = relationship(back_populates="assessment")


class AssessmentVersion(Base):
    __tablename__ = "assessment_versions"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id",
            "version_number",
            name="uq_assessment_versions_assessment_version_number",
        ),
        CheckConstraint(
            "status IN ('draft', 'review', 'published', 'archived')",
            name="ck_assessment_versions_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("assessments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    created_by_external_identity: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assessment: Mapped[Assessment] = relationship(back_populates="versions")
    items: Mapped[list[AssessmentItem]] = relationship(back_populates="assessment_version")
    selection_requests: Mapped[list[AssessmentSelectionRequest]] = relationship(
        back_populates="assessment_version"
    )
    publications: Mapped[list[AssessmentPublication]] = relationship(
        back_populates="assessment_version"
    )


class AssessmentSelectionRequest(Base):
    __tablename__ = "assessment_selection_requests"
    __table_args__ = (
        CheckConstraint(
            "selection_type IN ('manual', 'prompt')",
            name="ck_assessment_selection_requests_selection_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_assessment_selection_requests_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("assessment_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    selection_type: Mapped[str] = mapped_column(String(20), nullable=False)
    original_prompt: Mapped[str | None] = mapped_column(Text)
    requested_count: Mapped[int | None] = mapped_column(Integer)
    criteria_: Mapped[dict[str, Any] | None] = mapped_column("criteria", JSONBCompatible)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assessment_version: Mapped[AssessmentVersion] = relationship(
        back_populates="selection_requests"
    )
    items: Mapped[list[AssessmentItem]] = relationship(back_populates="selection_request")


class AssessmentItem(Base):
    __tablename__ = "assessment_items"
    __table_args__ = (
        UniqueConstraint(
            "assessment_version_id",
            "position",
            name="uq_assessment_items_version_position",
        ),
        CheckConstraint(
            "points >= 0",
            name="ck_assessment_items_points_nonnegative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("assessment_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("question_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    selection_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("assessment_selection_requests.id", ondelete="SET NULL"),
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    assessment_version: Mapped[AssessmentVersion] = relationship(back_populates="items")
    selection_request: Mapped[AssessmentSelectionRequest | None] = relationship(
        back_populates="items"
    )
    answers: Mapped[list[AssessmentAnswer]] = relationship(back_populates="assessment_item")


class AssessmentPublication(Base):
    __tablename__ = "assessment_publications"
    __table_args__ = (
        CheckConstraint(
            "publication_type IN ('immediate', 'scheduled')",
            name="ck_assessment_publications_publication_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'closed', 'archived')",
            name="ck_assessment_publications_status",
        ),
        CheckConstraint(
            "source_display IN ('none', 'exam', 'exam_year')",
            name="ck_assessment_publications_source_display",
        ),
        CheckConstraint(
            "bncc_display IN ('none', 'competency', 'skill', 'competency_skill')",
            name="ck_assessment_publications_bncc_display",
        ),
        CheckConstraint(
            "time_limit_seconds IS NULL OR time_limit_seconds > 0",
            name="ck_assessment_publications_time_limit_positive",
        ),
        CheckConstraint(
            "attempts_allowed IS NULL OR attempts_allowed > 0",
            name="ck_assessment_publications_attempts_allowed_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("assessment_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    publication_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    released_immediately: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer)
    attempts_allowed: Mapped[int | None] = mapped_column(Integer)
    source_display: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    bncc_display: Mapped[str] = mapped_column(String(30), nullable=False, default="none")
    show_difficulty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    assessment_version: Mapped[AssessmentVersion] = relationship(
        back_populates="publications"
    )
    attempts: Mapped[list[AssessmentAttempt]] = relationship(back_populates="publication")


class AssessmentAttempt(Base):
    __tablename__ = "assessment_attempts"
    __table_args__ = (
        UniqueConstraint(
            "publication_id",
            "external_identity_id",
            "attempt_number",
            name="uq_assessment_attempts_publication_student_attempt_number",
        ),
        CheckConstraint(
            "status IN ('not_started', 'in_progress', 'submitted', 'expired', 'cancelled')",
            name="ck_assessment_attempts_status",
        ),
        CheckConstraint(
            "attempt_number > 0",
            name="ck_assessment_attempts_attempt_number_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    publication_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("assessment_publications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_identity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_started")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    score: Mapped[float | None] = mapped_column(Numeric(10, 2))
    max_score: Mapped[float | None] = mapped_column(Numeric(10, 2))
    correct_answers: Mapped[int | None] = mapped_column(Integer)
    answered_count: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    publication: Mapped[AssessmentPublication] = relationship(back_populates="attempts")
    answers: Mapped[list[AssessmentAnswer]] = relationship(back_populates="attempt")


class AssessmentAnswer(Base):
    __tablename__ = "assessment_answers"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "assessment_item_id",
            name="uq_assessment_answers_attempt_item",
        ),
        CheckConstraint(
            "correction_status IN ('pending', 'correct', 'incorrect', 'ungraded')",
            name="ck_assessment_answers_correction_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("assessment_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    assessment_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("assessment_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    selected_option_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("question_options.id", ondelete="RESTRICT"),
        nullable=True,
    )
    response_text: Mapped[str | None] = mapped_column(Text)
    first_answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_time_ms: Mapped[int | None] = mapped_column(Integer)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    correction_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    points_awarded: Mapped[float | None] = mapped_column(Numeric(10, 2))
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    attempt: Mapped[AssessmentAttempt] = relationship(back_populates="answers")
    assessment_item: Mapped[AssessmentItem] = relationship(back_populates="answers")
