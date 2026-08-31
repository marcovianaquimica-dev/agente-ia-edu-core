"""
Learning Path domain models.

Represents student learning history, mastery per content, and practice sessions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..types import JSONBCompatible

from ..base import Base


class LearningHistory(Base):
    """
    Records every learning attempt (practice or assessment).

    NOT per attempt, but per individual response.
    Tracks which content/skill was practiced/tested, performance, and context.
    """

    __tablename__ = "learning_history"
    __table_args__ = (
        Index("ix_learning_history_external_identity_id", "external_identity_id"),
        Index("ix_learning_history_content_node_id", "content_node_id"),
        Index("ix_learning_history_activity_type", "activity_type"),
        Index(
            "ix_learning_history_identity_activity_created",
            "external_identity_id",
            "activity_type",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Student identity (no local user table)
    external_identity_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Activity context
    activity_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # OFFICIAL_ASSESSMENT, INDIVIDUAL_PRACTICE
    assessment_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True
    )  # FK to AssessmentAttempt if from assessment
    practice_session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True
    )  # FK to PracticeSession if from practice
    practice_question_selection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("practice_question_selections.id", ondelete="RESTRICT"),
        nullable=True,
    )  # Which practice question

    # Question/Response details
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("question_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    selected_option_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Difficulty context
    difficulty_level: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # EASY, MEDIUM, HARD

    # Performance
    is_correct: Mapped[bool | None] = mapped_column(
        nullable=True
    )  # None = not yet corrected/evaluated
    points_awarded: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Pedagogical context (from QuestionClassification)
    content_node_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("taxonomy_nodes.id", ondelete="RESTRICT"),
        nullable=True,
    )  # TaxonomyNode (skill/competency/subject)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class StudentContentMastery(Base):
    """
    Aggregated mastery level per student per content node.

    Represents the student's demonstrated competency in a specific skill/competency/subject.
    """

    __tablename__ = "student_content_mastery"
    __table_args__ = (
        UniqueConstraint(
            "external_identity_id",
            "content_node_id",
            name="uq_student_content_mastery_identity_node",
        ),
        CheckConstraint("mastery_score >= 0 AND mastery_score <= 100", name="ck_student_content_mastery_score_range"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_student_content_mastery_confidence_range"),
        CheckConstraint("questions_correct <= questions_answered", name="ck_student_content_mastery_correct_lte_answered"),
        Index("ix_student_content_mastery_external_identity_id", "external_identity_id"),
        Index("ix_student_content_mastery_content_node_id", "content_node_id"),
        Index("ix_student_content_mastery_current_level", "current_level"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Identity
    external_identity_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Content reference
    content_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("taxonomy_nodes.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Mastery metrics (0-100 percentage)
    mastery_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)

    # Current recommended difficulty level
    current_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="EASY"
    )  # EASY, MEDIUM, HARD

    # Performance history aggregates
    questions_answered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    questions_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Confidence metric (0-1): how confident the system is in this assessment
    # Increases with more questions answered
    # Consider recent vs. historical performance
    confidence: Mapped[float] = mapped_column(
        Numeric(5, 4), nullable=False, default=0
    )

    # Timestamps
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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


class PracticeSession(Base):
    """
    Represents a practice session initiated by a student.

    A practice session is a collection of questions selected for learning.
    """

    __tablename__ = "practice_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'completed', 'abandoned')",
            name="ck_practice_sessions_status",
        ),
        Index("ix_practice_sessions_external_identity_id", "external_identity_id"),
        Index("ix_practice_sessions_content_node_id", "content_node_id"),
        Index("ix_practice_sessions_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Student identity
    external_identity_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Content specification (optional; if null, system will recommend)
    content_node_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("taxonomy_nodes.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # Practice parameters
    recommended_difficulty: Mapped[str] = mapped_column(
        String(20), nullable=False, default="EASY"
    )  # EASY, MEDIUM, HARD
    requested_question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=10)

    # Status
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    # Execution
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Recommendations/reasoning
    recommendation_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # e.g., "Content with lowest mastery score"

    # Metadata
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible, nullable=True)
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

    # Relationships: eager loading avoids lazy SQLAlchemy queries in async contexts
    question_selections: Mapped[list[PracticeQuestionSelection]] = relationship(
        back_populates="practice_session",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="PracticeQuestionSelection.position",
    )


class PracticeQuestionSelection(Base):
    """
    Individual question selected for a practice session.

    Tracks which questions were presented, responses, and performance.
    """

    __tablename__ = "practice_question_selections"
    __table_args__ = (
        UniqueConstraint(
            "practice_session_id",
            "position",
            name="uq_practice_question_selections_session_position",
        ),
        CheckConstraint("position > 0", name="ck_practice_question_selections_position_positive"),
        Index("ix_practice_question_selections_practice_session_id", "practice_session_id"),
        Index("ix_practice_question_selections_question_version_id", "question_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Session reference
    practice_session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("practice_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Question
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("question_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Difficulty at selection time
    difficulty_level: Mapped[str] = mapped_column(String(20), nullable=False)

    # Position in session
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # Response (if completed)
    selected_option_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Performance
    is_correct: Mapped[bool | None] = mapped_column(nullable=True)
    points_awarded: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Completion
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Metadata
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible, nullable=True)
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

    # Relationships: eager loading avoids lazy SQLAlchemy queries in async contexts
    practice_session: Mapped[PracticeSession] = relationship(
        back_populates="question_selections",
        lazy="selectin",
    )
