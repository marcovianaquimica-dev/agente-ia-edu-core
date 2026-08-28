from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    and_,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from .official import QuestionVersion


class Taxonomy(Base):
    __tablename__ = "taxonomies"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_taxonomies_code_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    nodes: Mapped[list[TaxonomyNode]] = relationship(back_populates="taxonomy")
    classifications: Mapped[list[QuestionClassification]] = relationship(
        back_populates="taxonomy"
    )


class TaxonomyNode(Base):
    __tablename__ = "taxonomy_nodes"
    __table_args__ = (
        UniqueConstraint("taxonomy_id", "code", name="uq_taxonomy_nodes_taxonomy_code"),
        UniqueConstraint("taxonomy_id", "id", name="uq_taxonomy_nodes_taxonomy_id_id"),
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_taxonomy_nodes_parent_not_self",
        ),
        CheckConstraint(
            "node_type IN ('competency', 'skill', 'subject', 'subsubject')",
            name="ck_taxonomy_nodes_node_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    taxonomy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("taxonomies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("taxonomy_nodes.id", ondelete="RESTRICT"),
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    node_type: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    taxonomy: Mapped[Taxonomy] = relationship(back_populates="nodes")
    parent: Mapped[TaxonomyNode | None] = relationship(
        remote_side=[id],
        back_populates="children",
    )
    children: Mapped[list[TaxonomyNode]] = relationship(back_populates="parent")
    competency_classifications: Mapped[list[QuestionClassification]] = relationship(
        back_populates="competency_node",
        foreign_keys="QuestionClassification.competency_node_id",
    )
    skill_classifications: Mapped[list[QuestionClassification]] = relationship(
        back_populates="skill_node",
        foreign_keys="QuestionClassification.skill_node_id",
    )


class QuestionClassification(Base):
    __tablename__ = "question_classifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["taxonomy_id", "competency_node_id"],
            ["taxonomy_nodes.taxonomy_id", "taxonomy_nodes.id"],
            name="fk_question_classifications_competency_taxonomy_node",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["taxonomy_id", "skill_node_id"],
            ["taxonomy_nodes.taxonomy_id", "taxonomy_nodes.id"],
            name="fk_question_classifications_skill_taxonomy_node",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'superseded')",
            name="ck_question_classifications_status",
        ),
        CheckConstraint(
            "source IN ('ai', 'human', 'hybrid', 'rule')",
            name="ck_question_classifications_source",
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_question_classifications_confidence_range",
        ),
        CheckConstraint(
            "NOT is_primary OR competency_node_id IS NOT NULL",
            name="ck_question_classifications_primary_competency_required",
        ),
        CheckConstraint(
            "NOT is_primary OR skill_node_id IS NOT NULL",
            name="ck_question_classifications_primary_skill_required",
        ),
        Index(
            "uq_question_classifications_active_primary",
            "question_version_id",
            "taxonomy_id",
            unique=True,
            postgresql_where=text("is_primary = true AND status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("question_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    taxonomy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("taxonomies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    competency_node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    skill_node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    classifier_version: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("question_classifications.id", ondelete="RESTRICT"),
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)

    question_version: Mapped[QuestionVersion] = relationship()
    taxonomy: Mapped[Taxonomy] = relationship(back_populates="classifications")
    competency_node: Mapped[TaxonomyNode | None] = relationship(
        back_populates="competency_classifications",
        foreign_keys=[competency_node_id],
        primaryjoin=and_(
            taxonomy_id == TaxonomyNode.taxonomy_id,
            competency_node_id == TaxonomyNode.id,
        ),
    )
    skill_node: Mapped[TaxonomyNode | None] = relationship(
        back_populates="skill_classifications",
        foreign_keys=[skill_node_id],
        primaryjoin=and_(
            taxonomy_id == TaxonomyNode.taxonomy_id,
            skill_node_id == TaxonomyNode.id,
        ),
    )
    supersedes: Mapped[QuestionClassification | None] = relationship(
        remote_side=[id],
        back_populates="superseded_by",
    )
    superseded_by: Mapped[list[QuestionClassification]] = relationship(
        back_populates="supersedes"
    )


class DifficultyEstimate(Base):
    __tablename__ = "difficulty_estimates"
    __table_args__ = (
        CheckConstraint(
            "score BETWEEN 0 AND 100",
            name="ck_difficulty_estimates_score_range",
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_difficulty_estimates_confidence_range",
        ),
        CheckConstraint(
            "sample_size IS NULL OR sample_size >= 0",
            name="ck_difficulty_estimates_sample_size_nonnegative",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'superseded')",
            name="ck_difficulty_estimates_status",
        ),
        CheckConstraint(
            "source IN ('ai', 'human', 'empirical', 'heuristic')",
            name="ck_difficulty_estimates_source",
        ),
        Index(
            "uq_difficulty_estimates_active",
            "question_version_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("question_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    band: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    method: Mapped[str] = mapped_column(String(100), nullable=False)
    method_version: Mapped[str | None] = mapped_column(String(100))
    sample_size: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("difficulty_estimates.id", ondelete="RESTRICT"),
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)

    question_version: Mapped[QuestionVersion] = relationship()
    supersedes: Mapped[DifficultyEstimate | None] = relationship(
        remote_side=[id],
        back_populates="superseded_by",
    )
    superseded_by: Mapped[list[DifficultyEstimate]] = relationship(
        back_populates="supersedes"
    )
