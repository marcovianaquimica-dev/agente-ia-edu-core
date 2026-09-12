"""PHASE 26 - Authorial Material Ingestion Engine.

ONE new table. Adds a REVIEW/CLASSIFICATION/PUBLICATION workflow on top of the
EXISTING, unmodified PHASE 3 ingestion engine (IngestionDocument/Run/Section/
Question/Asset) - it never duplicates that model, only tracks the extra state
authorial ingestion needs that the ENEM/official pipeline never needed:
tenant ownership, declared provenance, a curriculum-v2 classification
suggestion (never auto-created nodes - TAXONOMY_GAP when unmatched), and the
review -> approval -> publication status machine. One row per
IngestionDocument (1:1). Publication links to the PHASE 23 TheoryMaterial it
produced - never a second material model.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..types import JSONBCompatible


class IngestionMaterialReview(Base):
    __tablename__ = "ingestion_material_reviews"
    __table_args__ = (
        UniqueConstraint("ingestion_document_id", name="uq_ingestion_material_reviews_document"),
        CheckConstraint(
            "origin_type IN ('AUTHORIAL', 'INSTITUTIONAL', 'EXTERNAL', 'OTHER')",
            name="ck_ingestion_material_reviews_origin_type",
        ),
        CheckConstraint(
            "review_status IN ('PENDING_REVIEW', 'NEEDS_REVIEW', 'APPROVED', 'PUBLISHED', 'REJECTED')",
            name="ck_ingestion_material_reviews_review_status",
        ),
        CheckConstraint(
            "classification_state IS NULL OR classification_state IN ('MAPPED', 'TAXONOMY_GAP')",
            name="ck_ingestion_material_reviews_classification_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ingestion_document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ingestion_documents.id", ondelete="RESTRICT"), nullable=False
    )
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT")
    )
    origin_type: Mapped[str] = mapped_column(String(20), nullable=False, default="AUTHORIAL")
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING_REVIEW")

    # -- curriculum-v2 classification SUGGESTION (deterministic matcher, PHASE
    # 26; substitutable by an AI provider later - never auto-creates a node) --
    discipline_code: Mapped[str | None] = mapped_column(String(120))
    area_code: Mapped[str | None] = mapped_column(String(120))
    content_code: Mapped[str | None] = mapped_column(String(120))
    subcontent_codes: Mapped[list[str] | None] = mapped_column(JSONBCompatible)
    classification_state: Mapped[str | None] = mapped_column(String(20))
    classification_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    classification_source: Mapped[str | None] = mapped_column(String(30))  # DETERMINISTIC_MATCH | MANUAL

    # -- structure / exercise detection summary (never fabricated content) --
    structure_issues: Mapped[list[str] | None] = mapped_column(JSONBCompatible)
    exercises_detected: Mapped[int] = mapped_column(default=0, nullable=False)

    # -- publication link (set only once APPROVED -> PUBLISHED) --
    theory_material_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("theory_materials.id", ondelete="RESTRICT")
    )
    theory_material_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("theory_material_versions.id", ondelete="RESTRICT")
    )

    reviewed_by_external_identity: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
