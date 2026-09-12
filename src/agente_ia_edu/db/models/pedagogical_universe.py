from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible


class PedagogicalUniverse(Base):
    __tablename__ = "pedagogical_universes"
    __table_args__ = (
        UniqueConstraint("external_id", name="uq_pedagogical_universes_external_id"),
        UniqueConstraint("slug", name="uq_pedagogical_universes_slug"),
        CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')", name="ck_pedagogical_universes_status"),
        CheckConstraint("owner_type IN ('PLATFORM', 'SCHOOL', 'PARTNER', 'PRODUCT', 'COURSE')", name="ck_pedagogical_universes_owner_type"),
        Index("ix_pedagogical_universes_owner_status", "owner_type", "owner_external_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False)
    owner_external_id: Mapped[str | None] = mapped_column(String(255))
    default_for_context: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    configuration_version: Mapped[str] = mapped_column(String(50), nullable=False, default="v1")
    configuration: Mapped[dict[str, Any] | None] = mapped_column(JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    catalog_scopes: Mapped[list["PedagogicalUniverseCatalogScope"]] = relationship(back_populates="universe", cascade="all, delete-orphan")
    academic_scopes: Mapped[list["PedagogicalUniverseAcademicScope"]] = relationship(back_populates="universe", cascade="all, delete-orphan")
    bindings: Mapped[list["PedagogicalUniverseBinding"]] = relationship(back_populates="universe", cascade="all, delete-orphan")


class PedagogicalUniverseCatalogScope(Base):
    __tablename__ = "pedagogical_universe_catalog_scopes"
    __table_args__ = (
        UniqueConstraint("universe_id", "catalog_node_id", name="uq_pedagogical_universe_catalog_scope"),
        CheckConstraint("scope_kind IN ('AREA', 'DISCIPLINE', 'CONTENT')", name="ck_pedagogical_universe_catalog_scope_kind"),
        Index("ix_pedagogical_universe_catalog_scopes_node", "catalog_node_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    universe_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("pedagogical_universes.id", ondelete="CASCADE"), nullable=False)
    catalog_node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("catalog_nodes.id", ondelete="RESTRICT"), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    include_descendants: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    universe: Mapped[PedagogicalUniverse] = relationship(back_populates="catalog_scopes")
    catalog_node: Mapped["CatalogNode"] = relationship("CatalogNode")


class PedagogicalUniverseAcademicScope(Base):
    __tablename__ = "pedagogical_universe_academic_scopes"
    __table_args__ = (
        UniqueConstraint("universe_id", "segment", "grade_level", "unit_id", name="uq_pedagogical_universe_academic_scope"),
        Index("ix_pedagogical_universe_academic_scope_context", "universe_id", "segment", "grade_level", "active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    universe_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("pedagogical_universes.id", ondelete="CASCADE"), nullable=False)
    segment: Mapped[str | None] = mapped_column(String(100))
    grade_level: Mapped[str | None] = mapped_column(String(100))
    unit_id: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    universe: Mapped[PedagogicalUniverse] = relationship(back_populates="academic_scopes")


class PedagogicalUniverseBinding(Base):
    __tablename__ = "pedagogical_universe_bindings"
    __table_args__ = (
        UniqueConstraint("universe_id", "subject_type", "subject_external_id", name="uq_pedagogical_universe_binding"),
        CheckConstraint("subject_type IN ('SCHOOL', 'EXTERNAL_IDENTITY', 'PRODUCT_CONTEXT')", name="ck_pedagogical_universe_binding_subject_type"),
        Index("ix_pedagogical_universe_bindings_subject", "subject_type", "subject_external_id", "active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    universe_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("pedagogical_universes.id", ondelete="CASCADE"), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    universe: Mapped[PedagogicalUniverse] = relationship(back_populates="bindings")