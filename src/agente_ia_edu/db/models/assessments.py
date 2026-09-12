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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible
from .official import QuestionVersion


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        CheckConstraint(
            "visibility_scope IN ('PRIVATE', 'CLASSROOM', 'SCHOOL', 'PUBLIC')",
            name="ck_assessments_visibility_scope",
        ),
        CheckConstraint(
            "origin_type IN ('PLATFORM', 'SCHOOL', 'TEACHER', 'IMPORTED', 'GENERATED')",
            name="ck_assessments_origin_type",
        ),
        CheckConstraint(
            "material_type IN ('EXERCISE_LIST', 'ASSESSMENT', 'SIMULATION')",
            name="ck_assessments_material_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    institution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("institutions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("schools.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_by_external_identity: Mapped[str | None] = mapped_column(String(255))
    owner_external_id: Mapped[str | None] = mapped_column(String(255))
    visibility_scope: Mapped[str] = mapped_column(String(30), nullable=False, default="SCHOOL")
    origin_type: Mapped[str] = mapped_column(String(30), nullable=False, default="SCHOOL")
    material_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="ASSESSMENT"
    )
    scope_type: Mapped[str | None] = mapped_column(String(30))
    scope_external_id: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
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
            "status IN ('draft', 'review', 'approved', 'rejected', 'published', 'archived')",
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


class AssessmentAssignment(Base):
    __tablename__ = "assessment_assignments"
    __table_args__ = (
        UniqueConstraint(
            "publication_id",
            "recipient_type",
            "recipient_id",
            name="uq_assessment_assignments_publication_recipient",
        ),
        CheckConstraint(
            "recipient_type IN ('STUDENT', 'CLASS', 'GRADE', 'CLASSROOM', 'UNIT', 'SCHOOL', 'USER')",
            name="ck_assessment_assignments_recipient_type",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
            name="ck_assessment_assignments_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("assessments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    publication_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("assessment_publications.id", ondelete="RESTRICT"),
    )
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("schools.id", ondelete="RESTRICT"),
        nullable=True,
    )
    recipient_type: Mapped[str] = mapped_column(String(30), nullable=False)
    recipient_id: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_by_external_id: Mapped[str | None] = mapped_column(String(255))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata_", JSONBCompatible)

    publication: Mapped[AssessmentPublication | None] = relationship(
        foreign_keys=[publication_id]
    )


class AssessmentWorkflowAudit(Base):
    __tablename__ = "assessment_workflow_audit"
    __table_args__ = (
        Index("ix_assessment_workflow_audit_assessment_id", "assessment_id"),
        Index("ix_assessment_workflow_audit_action", "action"),
        Index("ix_assessment_workflow_audit_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("assessments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(30))
    new_status: Mapped[str | None] = mapped_column(String(30))
    performed_by_external_id: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata_", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
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
        UniqueConstraint(
            "assessment_version_id",
            "question_version_id",
            name="uq_assessment_items_version_question_version",
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
    frozen_correct_option_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("question_options.id", ondelete="RESTRICT"),
    )
    answer_key_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("answer_key_revisions.id", ondelete="RESTRICT"),
    )

    assessment_version: Mapped[AssessmentVersion] = relationship(back_populates="items")
    question_version: Mapped[QuestionVersion] = relationship(
        foreign_keys=[question_version_id]
    )
    frozen_correct_option: Mapped["QuestionOption | None"] = relationship(
        foreign_keys=[frozen_correct_option_id]
    )
    answer_key_revision: Mapped["AnswerKeyRevision | None"] = relationship(
        foreign_keys=[answer_key_revision_id]
    )
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
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("assessment_assignments.id", ondelete="RESTRICT"),
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
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONBCompatible
    )
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
    assignment: Mapped[AssessmentAssignment | None] = relationship(
        foreign_keys=[assignment_id]
    )
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


class ActivityAssignment(Base):
    """PHASE 16 - distribution of a PUBLISHED question list to a recipient.

    Content lives in Assessment/AssessmentVersion/AssessmentItem; this row only
    records WHO gets the activity and WHEN. ``(assessment_id,
    assessment_version_id)`` is the canonical activity identity. Recipients are
    institutional references (STUDENT | CLASS + target_id) - never copied rosters.
    """

    __tablename__ = "activity_assignments"
    __table_args__ = (
        CheckConstraint("target_type IN ('STUDENT', 'CLASS')",
                        name="ck_activity_assignments_target_type"),
        CheckConstraint("status IN ('ACTIVE', 'CLOSED', 'CANCELLED')",
                        name="ck_activity_assignments_status"),
        CheckConstraint("question_count >= 0",
                        name="ck_activity_assignments_question_count_nonnegative"),
        CheckConstraint("due_at IS NULL OR available_from IS NULL OR due_at >= available_from",
                        name="ck_activity_assignments_window_order"),
        Index("ix_activity_assignments_school_id", "school_id"),
        Index("ix_activity_assignments_assessment_id", "assessment_id"),
        Index("ix_activity_assignments_version_id", "assessment_version_id"),
        Index("ix_activity_assignments_target", "target_type", "target_id"),
        Index("ix_activity_assignments_status", "status"),
        Index("ix_activity_assignments_available_from", "available_from"),
        Index("ix_activity_assignments_due_at", "due_at"),
        Index("uq_activity_assignments_active_target",
              "assessment_version_id", "target_type", "target_id",
              unique=True,
              postgresql_where=text("status = 'ACTIVE'"),
              sqlite_where=text("status = 'ACTIVE'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("assessments.id", ondelete="RESTRICT"), nullable=False)
    assessment_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("assessment_versions.id", ondelete="RESTRICT"), nullable=False)
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"))
    created_by_external_id: Mapped[str | None] = mapped_column(String(255))
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    available_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    selection_fingerprint: Mapped[str | None] = mapped_column(String(128))
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    answer_key_presentation: Mapped[str | None] = mapped_column(String(40))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))

    assessment: Mapped[Assessment] = relationship(foreign_keys=[assessment_id])
    assessment_version: Mapped[AssessmentVersion] = relationship(foreign_keys=[assessment_version_id])


class ActivityAttempt(Base):
    """PHASE 17 - one student's execution of an ActivityAssignment.

    Pure execution state: NOT_STARTED / IN_PROGRESS / COMPLETED. No score,
    percentage, correction or pedagogical result (later phases). Generic enough
    to later back exercises, simulados, diagnostics and assessments without a
    schema change. One row per (assignment, student) in this phase.
    """

    __tablename__ = "activity_attempts"
    __table_args__ = (
        CheckConstraint("status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED')",
                        name="ck_activity_attempts_status"),
        CheckConstraint("current_position IS NULL OR current_position >= 0",
                        name="ck_activity_attempts_current_position_nonneg"),
        UniqueConstraint("assignment_id", "student_external_id",
                         name="uq_activity_attempts_assignment_student"),
        Index("ix_activity_attempts_assignment_id", "assignment_id"),
        Index("ix_activity_attempts_student", "student_external_id"),
        Index("ix_activity_attempts_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("activity_assignments.id", ondelete="RESTRICT"), nullable=False)
    student_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="IN_PROGRESS")
    current_position: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))

    assignment: Mapped[ActivityAssignment] = relationship(foreign_keys=[assignment_id])
    answers: Mapped[list["ActivityAnswer"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan")


class ActivityAnswer(Base):
    """PHASE 17 - the CURRENT choice for one question in an attempt.

    UNIQUE(attempt_id, question_version_id): changing the choice UPDATEs this
    row, never inserts a second. No correctness/score field - that is a later
    phase. ``question_version_id`` and ``selected_option_id`` are read-only
    references into the official bank; execution never writes those tables.
    """

    __tablename__ = "activity_answers"
    __table_args__ = (
        UniqueConstraint("attempt_id", "question_version_id",
                         name="uq_activity_answers_attempt_question"),
        Index("ix_activity_answers_attempt_id", "attempt_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("activity_attempts.id", ondelete="CASCADE"), nullable=False)
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_versions.id", ondelete="RESTRICT"), nullable=False)
    selected_option_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("question_options.id", ondelete="RESTRICT"))
    selected_option_key: Mapped[str | None] = mapped_column(String(8))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))

    attempt: Mapped[ActivityAttempt] = relationship(back_populates="answers")


class ActivityResult(Base):
    """PHASE 18 - deterministic correction of one COMPLETED ActivityAttempt.

    The raw outcome: how many questions were correct / incorrect / unanswered,
    plus the lifecycle timestamps. NO note, percentage-as-grade, TRI, ranking or
    pedagogical result is stored here - the aproveitamento (correct/count) is a
    derived display value only. ``attempt_id`` is UNIQUE: correction is
    idempotent. Per-question detail lives in ActivityResultItem so a later phase
    can analyse by content/discipline/difficulty without re-correcting.
    """

    __tablename__ = "activity_results"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_activity_results_attempt"),
        CheckConstraint(
            "correct_count >= 0 AND incorrect_count >= 0 AND unanswered_count >= 0 "
            "AND answered_count >= 0 AND question_count >= 0",
            name="ck_activity_results_counts_nonneg"),
        CheckConstraint(
            "correct_count + incorrect_count + unanswered_count = question_count",
            name="ck_activity_results_counts_partition"),
        Index("ix_activity_results_assignment_id", "assignment_id"),
        Index("ix_activity_results_student", "student_external_id"),
        Index("ix_activity_results_version_id", "assessment_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("activity_attempts.id", ondelete="RESTRICT"), nullable=False)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("activity_assignments.id", ondelete="RESTRICT"), nullable=False)
    student_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    assessment_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("assessment_versions.id", ondelete="RESTRICT"), nullable=False)
    selection_fingerprint: Mapped[str | None] = mapped_column(String(128))
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    answered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incorrect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unanswered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_status: Mapped[str] = mapped_column(String(20), nullable=False, default="COMPLETED")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    corrected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))

    attempt: Mapped[ActivityAttempt] = relationship(foreign_keys=[attempt_id])
    items: Mapped[list["ActivityResultItem"]] = relationship(
        back_populates="result", cascade="all, delete-orphan",
        order_by="ActivityResultItem.position")


class ActivityResultItem(Base):
    """PHASE 18 - the corrected outcome of ONE question in an ActivityResult.

    Keeps the raw per-question facts (student key, frozen correct key, is_correct,
    answered, position, official_number) in the frozen activity order. Not coupled
    to curriculum-v2 / PedagogicalClassification - that relation is resolved
    later, from ``question_version_id``.
    """

    __tablename__ = "activity_result_items"
    __table_args__ = (
        UniqueConstraint("result_id", "question_version_id",
                         name="uq_activity_result_items_result_qv"),
        UniqueConstraint("result_id", "position",
                         name="uq_activity_result_items_result_position"),
        CheckConstraint("position >= 1", name="ck_activity_result_items_position_positive"),
        Index("ix_activity_result_items_result_id", "result_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    result_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("activity_results.id", ondelete="CASCADE"), nullable=False)
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_versions.id", ondelete="RESTRICT"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    official_number: Mapped[int | None] = mapped_column(Integer)
    selected_option_key: Mapped[str | None] = mapped_column(String(8))
    correct_option_key: Mapped[str | None] = mapped_column(String(8))
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    answered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    result: Mapped[ActivityResult] = relationship(back_populates="items")


class DomainContentMastery(Base):
    """PHASE 20 - one grain of the DERIVED, curriculum-v2 Domain Map for a student.

    ALUNO x taxonomy_version x content_code (subcontent_code NULL) is the CONTENT
    grain; a row with subcontent_code set is the SUBCONTENT grain. Every row is
    recomputable from the immutable ActivityResult / ActivityResultItem history
    (DomainMapRebuild) - this table is a cache, never a source of truth. It holds
    only OBSERVED evidence (answered questions) aggregated per curriculum-v2
    content; UNCLASSIFIED questions are never folded in. No score / grade / TRI /
    ranking / recommendation column. discipline_code / area_code are resolved
    from the catalog at read time, not stored.
    """

    __tablename__ = "domain_content_mastery"
    __table_args__ = (
        CheckConstraint("questions_correct <= questions_answered",
                        name="ck_domain_content_mastery_correct_lte_answered"),
        CheckConstraint("questions_correct + questions_incorrect = questions_answered",
                        name="ck_domain_content_mastery_counts_partition"),
        CheckConstraint("questions_answered <= questions_seen",
                        name="ck_domain_content_mastery_answered_lte_seen"),
        CheckConstraint(
            "questions_seen >= 0 AND questions_answered >= 0 AND questions_correct >= 0 "
            "AND questions_incorrect >= 0 AND evidence_count >= 0",
            name="ck_domain_content_mastery_nonneg"),
        CheckConstraint("evidence_state IN ('INSUFFICIENT_EVIDENCE', 'OBSERVED')",
                        name="ck_domain_content_mastery_evidence_state"),
        Index("ix_domain_content_mastery_student", "student_external_id"),
        Index("ix_domain_content_mastery_student_tv", "student_external_id", "taxonomy_version"),
        Index("ix_domain_content_mastery_content_code", "content_code"),
        Index("ix_domain_content_mastery_last_activity_at", "last_activity_at"),
        Index("uq_domain_content_mastery_content_grain",
              "student_external_id", "taxonomy_version", "content_code",
              unique=True, postgresql_where=text("subcontent_code IS NULL"),
              sqlite_where=text("subcontent_code IS NULL")),
        Index("uq_domain_content_mastery_subcontent_grain",
              "student_external_id", "taxonomy_version", "content_code", "subcontent_code",
              unique=True, postgresql_where=text("subcontent_code IS NOT NULL"),
              sqlite_where=text("subcontent_code IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(String(40), nullable=False, default="curriculum-v2")
    content_code: Mapped[str] = mapped_column(String(100), nullable=False)
    subcontent_code: Mapped[str | None] = mapped_column(String(100))
    questions_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    questions_answered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    questions_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    questions_incorrect: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accuracy: Mapped[float | None] = mapped_column(Numeric(6, 4))
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_state: Mapped[str] = mapped_column(String(30), nullable=False,
                                               default="INSUFFICIENT_EVIDENCE")
    definitive_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provisional_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    forced_closure_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visual_dependency_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    origin_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONBCompatible)
    first_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))
