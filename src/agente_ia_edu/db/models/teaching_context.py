"""
Teaching Context and Lesson Registration domain models (Phase 12B.1).

Models for:
- TeachingLesson: Lesson recorded by a teacher for a classroom, linking school, teacher,
  classroom, academic year, and content node to generate/synchronize PedagogicalContext.
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
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible


class TeachingLesson(Base):
    """
    Lesson recorded by a teacher for a classroom.

    Connects teacher, school, classroom, academic year, and content node,
    generating or linking a PedagogicalContext entry for learning path recommendations.
    """

    __tablename__ = "teaching_lessons"
    __table_args__ = (
        CheckConstraint("duration_minutes IS NULL OR duration_minutes > 0", name="ck_teaching_lessons_duration_positive"),
        Index("ix_teaching_lessons_school_id", "school_id"),
        Index("ix_teaching_lessons_classroom_id", "classroom_id"),
        Index("ix_teaching_lessons_teacher_id", "teacher_id"),
        Index("ix_teaching_lessons_content_node_id", "content_node_id"),
        Index("ix_teaching_lessons_academic_year", "academic_year"),
        Index("ix_teaching_lessons_lesson_date", "lesson_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    academic_year: Mapped[str] = mapped_column(String(10), nullable=False, default="2026")
    unit_id: Mapped[str | None] = mapped_column(String(255))
    segment_id: Mapped[str | None] = mapped_column(String(255))
    grade_level: Mapped[str | None] = mapped_column(String(255))
    classroom_id: Mapped[str] = mapped_column(String(255), nullable=False)

    teacher_id: Mapped[str] = mapped_column(String(255), nullable=False)  # external_user_id
    content_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("catalog_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    subcontent_node_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("catalog_nodes.id", ondelete="RESTRICT")
    )

    lesson_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(500))
    summary_observation: Mapped[str | None] = mapped_column(Text)

    pedagogical_context_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pedagogical_contexts.id", ondelete="RESTRICT")
    )

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

    school: Mapped["School"] = relationship("School", foreign_keys=[school_id])
    content_node: Mapped["CatalogNode"] = relationship("CatalogNode", foreign_keys=[content_node_id])
    subcontent_node: Mapped[Optional["CatalogNode"]] = relationship("CatalogNode", foreign_keys=[subcontent_node_id])
    pedagogical_context: Mapped[Optional["PedagogicalContext"]] = relationship("PedagogicalContext", foreign_keys=[pedagogical_context_id])
