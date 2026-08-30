"""
External Video Discovery domain models (Phase 10).

Models for:
- ExternalVideoCandidate: Candidate videos discovered from external/internal providers
  before pedagogical review, classification, and conversion to catalog resources.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

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

from ..base import Base
from ..types import JSONBCompatible


class ExternalVideoCandidate(Base):
    """
    A candidate video discovered from an external or internal provider (YouTube, YouTube EDU, etc.)
    held for classification, pedagogical review, and approval before being materialized into
    the EducationalResource catalog.
    """

    __tablename__ = "external_video_candidates"
    __table_args__ = (
        UniqueConstraint(
            "source", "external_id", name="uq_external_video_candidates_source_external_id"
        ),
        CheckConstraint(
            "status IN ('DISCOVERED', 'PENDING_REVIEW', 'CLASSIFIED', 'APPROVED', 'REJECTED', 'AVAILABLE')",
            name="ck_external_video_candidates_status",
        ),
        Index("ix_external_video_candidates_source", "source"),
        Index("ix_external_video_candidates_status", "status"),
        Index("ix_external_video_candidates_content_node_id", "content_node_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g., YOUTUBE, YOUTUBE_EDU, MOCK, INTERNAL
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    channel_or_author: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    language: Mapped[str | None] = mapped_column(String(10), default="pt-BR")

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="DISCOVERED")
    classification_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    recommended_difficulty: Mapped[str | None] = mapped_column(String(20))  # EASY, MEDIUM, HARD

    content_node_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("catalog_nodes.id", ondelete="RESTRICT")
    )
    converted_resource_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("educational_resources.id", ondelete="RESTRICT")
    )

    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    content_node: Mapped[Optional["CatalogNode"]] = relationship("CatalogNode", foreign_keys=[content_node_id])
    converted_resource: Mapped[Optional["EducationalResource"]] = relationship("EducationalResource", foreign_keys=[converted_resource_id])
