"""
Pedagogical Recommendation and Context domain models (Phase 6).

Models for:
- PedagogicalContext: Context registered by Teachers, Coordination, or School Planning.
- PedagogicalRecommendation: Audit trail of recommendations generated for students.
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
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible


class PedagogicalContext(Base):
    """
    Pedagogical context provided by Teacher, Coordination, or School Plan.

    Example:
    - Teacher registered: "Ensinei Diluição de Soluções em 2026-08-30"
    - Coordination guided: "Revisar Diluição nesta semana"
    - School Plan: "Planejamento Bimestral - Semana 4"
    """

    __tablename__ = "pedagogical_contexts"
    __table_args__ = (
        CheckConstraint(
            "source IN ('TEACHER', 'COORDINATION', 'SCHOOL_PLAN')",
            name="ck_pedagogical_contexts_source",
        ),
        Index("ix_pedagogical_contexts_content_node_id", "content_node_id"),
        Index("ix_pedagogical_contexts_institution_id", "institution_id"),
        Index("ix_pedagogical_contexts_classroom_id", "classroom_id"),
        Index("ix_pedagogical_contexts_source", "source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    content_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("catalog_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    institution_id: Mapped[str | None] = mapped_column(String(255))
    classroom_id: Mapped[str | None] = mapped_column(String(255))
    author_id: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class PedagogicalRecommendation(Base):
    """
    Historical record of a recommendation produced for a student.

    Answers: "What did the system recommend to this student and why?"
    """

    __tablename__ = "pedagogical_recommendations"
    __table_args__ = (
        CheckConstraint(
            "recommendation_type IN ('STUDY_MATERIAL', 'PRACTICE', 'REVIEW', 'WATCH_VIDEO', 'REVIEW_PREREQUISITE')",
            name="ck_pedagogical_recommendations_type",
        ),
        CheckConstraint(
            "recommended_difficulty IN ('EASY', 'MEDIUM', 'HARD')",
            name="ck_pedagogical_recommendations_difficulty",
        ),
        CheckConstraint(
            "context_source IN ('TEACHER', 'COORDINATION', 'SCHOOL_PLAN', 'AUTONOMOUS')",
            name="ck_pedagogical_recommendations_context_source",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'ACCEPTED', 'COMPLETED', 'SUPERSEDED', 'DISMISSED')",
            name="ck_pedagogical_recommendations_status",
        ),
        Index("ix_pedagogical_recommendations_student_id", "student_id"),
        Index("ix_pedagogical_recommendations_content_node_id", "content_node_id"),
        Index("ix_pedagogical_recommendations_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[str] = mapped_column(String(255), nullable=False)
    institution_id: Mapped[str | None] = mapped_column(String(255))
    classroom_id: Mapped[str | None] = mapped_column(String(255))

    content_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("catalog_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    recommendation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    recommended_difficulty: Mapped[str] = mapped_column(String(20), nullable=False, default="EASY")
    priority_score: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False)

    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("educational_resources.id", ondelete="RESTRICT")
    )
    question_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("question_versions.id", ondelete="RESTRICT")
    )

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    context_source: Mapped[str] = mapped_column(String(30), nullable=False)
    mastery_score_at_recommendation: Mapped[float | None] = mapped_column(Numeric(5, 2))

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
