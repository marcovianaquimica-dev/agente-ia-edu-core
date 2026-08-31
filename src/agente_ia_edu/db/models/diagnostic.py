"""
Initial Diagnostic domain models (Phase 13).

Models for:
- InitialDiagnostic: Adaptive diagnostic sondage session for students (School-bound or Independent).
- DiagnosticQuestionSelection: Individual question selected and answered during an initial diagnostic session.
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible


class InitialDiagnostic(Base):
    """
    Adaptive diagnostic session to estimate a student's initial mastery map.

    Supports both School-bound students (school_id, classroom_id provided)
    and Independent students (school_id is NULL).
    """

    __tablename__ = "initial_diagnostics"
    __table_args__ = (
        CheckConstraint(
            "status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED', 'INSUFFICIENT_EVIDENCE', 'CANCELLED')",
            name="ck_initial_diagnostics_status",
        ),
        Index("ix_initial_diagnostics_student_id", "student_id"),
        Index("ix_initial_diagnostics_school_id", "school_id"),
        Index("ix_initial_diagnostics_status", "status"),
        Index("ix_initial_diagnostics_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[str] = mapped_column(String(255), nullable=False)
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT")
    )
    classroom_id: Mapped[str | None] = mapped_column(String(255))
    academic_year: Mapped[str] = mapped_column(String(10), nullable=False, default="2026")
    grade_level: Mapped[str | None] = mapped_column(String(255))
    discipline: Mapped[str] = mapped_column(String(255), nullable=False, default="Química")
    diagnostic_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1")

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="NOT_STARTED")
    total_questions_asked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    overall_confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0.0)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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

    school: Mapped[Optional["School"]] = relationship("School", foreign_keys=[school_id])
    question_selections: Mapped[list["DiagnosticQuestionSelection"]] = relationship(
        "DiagnosticQuestionSelection", back_populates="diagnostic", cascade="all, delete-orphan"
    )


class DiagnosticQuestionSelection(Base):
    """
    Individual question presented during an initial diagnostic session.
    """

    __tablename__ = "diagnostic_question_selections"
    __table_args__ = (
        UniqueConstraint(
            "diagnostic_id", "position", name="uq_diagnostic_selections_diagnostic_position"
        ),
        CheckConstraint("position > 0", name="ck_diagnostic_selections_position_positive"),
        Index("ix_diagnostic_selections_diagnostic_id", "diagnostic_id"),
        Index("ix_diagnostic_selections_question_version_id", "question_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    diagnostic_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("initial_diagnostics.id", ondelete="CASCADE"), nullable=False
    )
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_versions.id", ondelete="RESTRICT"), nullable=False
    )
    content_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("catalog_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    difficulty_level: Mapped[str] = mapped_column(String(20), nullable=False)  # EASY, MEDIUM, HARD
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    selected_option_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    response_text: Mapped[str | None] = mapped_column(Text)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    diagnostic: Mapped["InitialDiagnostic"] = relationship(
        "InitialDiagnostic", back_populates="question_selections"
    )
    question_version: Mapped["QuestionVersion"] = relationship("QuestionVersion", foreign_keys=[question_version_id])
    content_node: Mapped["CatalogNode"] = relationship("CatalogNode", foreign_keys=[content_node_id])
