from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..types import JSONBCompatible


class ModificationProposal(Base):
    __tablename__ = "modification_proposals"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'ACCEPTED', 'CANCELLED', 'REJECTED')", name="ck_modification_proposals_status"),
        Index("ix_modification_proposals_original_version", "original_question_version_id"),
        Index("ix_modification_proposals_requester", "requested_by_external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False)
    assessment_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("assessment_items.id", ondelete="RESTRICT"), nullable=False)
    original_question_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("question_versions.id", ondelete="RESTRICT"), nullable=False)
    requested_by_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    modification_type: Mapped[str] = mapped_column(String(40), nullable=False)
    instruction: Mapped[str | None] = mapped_column(Text)
    proposed_content: Mapped[dict[str, Any]] = mapped_column(JSONBCompatible, nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_external_id: Mapped[str | None] = mapped_column(String(255))
    accepted_question_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("question_versions.id", ondelete="RESTRICT")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_external_id: Mapped[str | None] = mapped_column(String(255))