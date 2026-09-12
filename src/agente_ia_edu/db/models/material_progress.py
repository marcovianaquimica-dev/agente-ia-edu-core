"""PHASE 25 - Material Delivery & Study Integration.

The SMALLEST possible structure to answer one question: "where did this
student leave off inside this published material version?" - so the Material
Player can resume approximately where the student left.

Explicitly NOT evidence of domain mastery: opening/reading a material never
writes to domain_content_mastery, and this table is never read by the Domain
Map (PHASE 20) or the Adaptive Learning Path (PHASE 21) priority algorithm.
One row per (student, material_version) - upsert/idempotent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class MaterialProgress(Base):
    __tablename__ = "material_progress"
    __table_args__ = (
        UniqueConstraint(
            "student_external_id", "material_version_id",
            name="uq_material_progress_student_version",
        ),
        CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED')", name="ck_material_progress_status"
        ),
        Index("ix_material_progress_student_material", "student_external_id", "material_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    material_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("theory_materials.id", ondelete="RESTRICT"), nullable=False
    )
    material_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("theory_material_versions.id", ondelete="RESTRICT"), nullable=False
    )
    current_section_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("material_sections.id", ondelete="SET NULL")
    )
    current_block_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("material_blocks.id", ondelete="SET NULL")
    )
    sections_completed: Mapped[int] = mapped_column(default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="IN_PROGRESS")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
