"""
Pedagogical Catalog domain models (Phase 2 foundation).

Generic, subject-agnostic building blocks meant to eventually connect
content, books, PDFs, authored materials, videos, questions, exercises,
external resources and licensed materials to the Learning Path / mastery
engine. Nothing here is specific to Chemistry, ENEM, "seasons" or
"episodes" - any discipline (Math, Physics, Portuguese, ...) must be able
to reuse the exact same structure.

Responsibility boundary (kept intentionally narrow in this phase):
    Catalog: "what resources/content exist?"
    Mastery engine (learning_path): "what does the student know?"
    Learning Path: "what should the student do now?"
    Recommendation (future): "which resource is best for them?"
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

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


class CatalogNode(Base):
    """
    Generic hierarchical node for organizing pedagogical content.

    A single self-referencing tree resolves Discipline -> Learning Area ->
    Learning Unit -> Content -> Subcontent (suggested node_type values, not
    enforced) without hard-coding rigid levels: different disciplines may
    need more or fewer levels, so node_type is a free-form string rather
    than a fixed CheckConstraint list.
    """

    __tablename__ = "catalog_nodes"
    __table_args__ = (
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_catalog_nodes_parent_not_self"),
        Index("ix_catalog_nodes_parent_id", "parent_id"),
        Index("ix_catalog_nodes_root_id", "root_id"),
        Index("ix_catalog_nodes_node_type", "node_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("catalog_nodes.id", ondelete="RESTRICT")
    )
    # Denormalized pointer to the top-level (Discipline) node for efficient
    # "all nodes under this discipline" queries without a recursive CTE.
    # A root node points to itself.
    root_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("catalog_nodes.id", ondelete="RESTRICT")
    )
    node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    code: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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

    parent: Mapped[CatalogNode | None] = relationship(
        remote_side=[id], back_populates="children", foreign_keys=[parent_id]
    )
    children: Mapped[list[CatalogNode]] = relationship(
        back_populates="parent", foreign_keys=[parent_id]
    )


class EducationalResource(Base):
    """
    Generic pedagogical resource (theory material, book, PDF, video,
    question set, external resource, or other).

    Preserves origin/ownership traceability: not every resource belongs to
    AGENTE IA EDU - it may be authored by a teacher, owned by a school,
    owned by the hosting platform, licensed, or fully external.
    """

    __tablename__ = "educational_resources"
    __table_args__ = (
        CheckConstraint(
            "resource_type IN ('THEORY_MATERIAL', 'BOOK', 'PDF', 'VIDEO', "
            "'QUESTION_SET', 'EXTERNAL_RESOURCE', 'OTHER')",
            name="ck_educational_resources_resource_type",
        ),
        CheckConstraint(
            "origin_type IN ('AUTHOR', 'SCHOOL', 'PLATFORM', 'LICENSED', 'EXTERNAL')",
            name="ck_educational_resources_origin_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_educational_resources_status",
        ),
        CheckConstraint(
            "visibility_scope IN ('PRIVATE', 'CLASSROOM', 'SCHOOL', 'INSTITUTION', "
            "'SHARED', 'PUBLIC', 'LICENSED')",
            name="ck_educational_resources_visibility_scope",
        ),
        Index("ix_educational_resources_resource_type", "resource_type"),
        Index("ix_educational_resources_owner_external_id", "owner_external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False)

    author: Mapped[str | None] = mapped_column(String(255))

    # Origin / ownership traceability (section 3 and 11 of the spec).
    origin_type: Mapped[str] = mapped_column(String(20), nullable=False)
    owner_external_id: Mapped[str | None] = mapped_column(String(255))
    license_reference: Mapped[str | None] = mapped_column(Text)

    source_url: Mapped[str | None] = mapped_column(String(2048))
    storage_uri: Mapped[str | None] = mapped_column(String(2048))

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    visibility_scope: Mapped[str] = mapped_column(String(20), nullable=False, default="PRIVATE")

    created_by_external_identity: Mapped[str | None] = mapped_column(String(255))
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

    video_detail: Mapped[VideoResourceDetail | None] = relationship(
        back_populates="resource", uselist=False
    )
    book_detail: Mapped[BookResourceDetail | None] = relationship(
        back_populates="resource", uselist=False
    )
    access_grants: Mapped[list[ResourceAccessGrant]] = relationship(back_populates="resource")


class ResourceAccessGrant(Base):
    """
    Explicit access grant for a resource beyond its single owner.

    Example: a resource owned by "Química do ENEM" (PLATFORM) can be
    explicitly authorized for School A and School B without being fully
    PUBLIC.
    """

    __tablename__ = "resource_access_grants"
    __table_args__ = (
        CheckConstraint(
            "grantee_type IN ('INSTITUTION', 'SCHOOL_UNIT', 'CLASSROOM', 'EXTERNAL_IDENTITY')",
            name="ck_resource_access_grants_grantee_type",
        ),
        UniqueConstraint(
            "resource_id", "grantee_type", "grantee_external_id",
            name="uq_resource_access_grants_resource_grantee",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("educational_resources.id", ondelete="RESTRICT"), nullable=False
    )
    grantee_type: Mapped[str] = mapped_column(String(20), nullable=False)
    grantee_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    resource: Mapped[EducationalResource] = relationship(back_populates="access_grants")


class VideoResourceDetail(Base):
    """
    Video-specific attributes for an EducationalResource of type VIDEO.

    Ranking, feedback and per-student watched-percentage are intentionally
    NOT modeled here: those are time-series / per-student facts that belong
    to a future tracking layer, not static attributes of the video itself.
    """

    __tablename__ = "video_resource_details"
    __table_args__ = (
        CheckConstraint(
            "platform IN ('YOUTUBE', 'OWN', 'PLATFORM', 'LICENSED', 'EXTERNAL')",
            name="ck_video_resource_details_platform",
        ),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("educational_resources.id", ondelete="RESTRICT"), primary_key=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    external_video_id: Mapped[str | None] = mapped_column(String(255))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    # Placeholder for a future ranking algorithm; unused/not computed now.
    ranking_score: Mapped[float | None] = mapped_column(Numeric(5, 2))

    resource: Mapped[EducationalResource] = relationship(back_populates="video_detail")


class BookResourceDetail(Base):
    """
    Book/PDF-specific bibliographic attributes for an EducationalResource.

    The original file is preserved by reference (storage_uri on the base
    resource) - content is never auto-copied into text columns here. The
    future ingestion pipeline (extraction/OCR/chaptering/classification)
    will consume this file and populate other tables; it is out of scope now.
    """

    __tablename__ = "book_resource_details"
    __table_args__ = (
        CheckConstraint(
            "processing_status IN ('NOT_STARTED', 'PENDING', 'PROCESSING', 'PROCESSED', 'FAILED')",
            name="ck_book_resource_details_processing_status",
        ),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("educational_resources.id", ondelete="RESTRICT"), primary_key=True
    )
    edition: Mapped[str | None] = mapped_column(String(100))
    publication_year: Mapped[int | None] = mapped_column(Integer)
    isbn: Mapped[str | None] = mapped_column(String(20))
    processing_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NOT_STARTED")

    resource: Mapped[EducationalResource] = relationship(back_populates="book_detail")


class ContentResourceLink(Base):
    """
    Many-to-many link between a CatalogNode (content/subcontent) and an
    EducationalResource, carrying pedagogical context for the relationship.
    """

    __tablename__ = "content_resource_links"
    __table_args__ = (
        CheckConstraint(
            "pedagogical_role IN ('THEORY', 'EXPLANATION', 'PRACTICE', 'REVIEW', 'VIDEO', 'REFERENCE')",
            name="ck_content_resource_links_pedagogical_role",
        ),
        CheckConstraint(
            "recommended_level IS NULL OR recommended_level IN ('EASY', 'MEDIUM', 'HARD')",
            name="ck_content_resource_links_recommended_level",
        ),
        UniqueConstraint(
            "content_node_id", "resource_id", "pedagogical_role",
            name="uq_content_resource_links_node_resource_role",
        ),
        Index("ix_content_resource_links_content_node_id", "content_node_id"),
        Index("ix_content_resource_links_resource_id", "resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    content_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("catalog_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("educational_resources.id", ondelete="RESTRICT"), nullable=False
    )
    pedagogical_role: Mapped[str] = mapped_column(String(20), nullable=False)
    relevance: Mapped[float | None] = mapped_column(Numeric(5, 4))
    priority: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[int | None] = mapped_column(Integer)
    recommended_level: Mapped[str | None] = mapped_column(String(20))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    content_node: Mapped[CatalogNode] = relationship(foreign_keys=[content_node_id])
    resource: Mapped[EducationalResource] = relationship(foreign_keys=[resource_id])


class ContentQuestionLink(Base):
    """
    Direct many-to-many link between a CatalogNode and an existing
    QuestionVersion. Never duplicates the question - only references it.
    """

    __tablename__ = "content_question_links"
    __table_args__ = (
        UniqueConstraint(
            "content_node_id", "question_version_id",
            name="uq_content_question_links_node_question",
        ),
        Index("ix_content_question_links_content_node_id", "content_node_id"),
        Index("ix_content_question_links_question_version_id", "question_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    content_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("catalog_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_versions.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    content_node: Mapped[CatalogNode] = relationship(foreign_keys=[content_node_id])
    question_version: Mapped["QuestionVersion"] = relationship("QuestionVersion", foreign_keys=[question_version_id])


class ResourceQuestionLink(Base):
    """
    Ordered link between a QUESTION_SET resource and existing QuestionVersion
    rows (e.g. "exercise list X" bundling questions already in the bank).
    """

    __tablename__ = "resource_question_links"
    __table_args__ = (
        UniqueConstraint(
            "resource_id", "position",
            name="uq_resource_question_links_resource_position",
        ),
        CheckConstraint("position > 0", name="ck_resource_question_links_position_positive"),
        Index("ix_resource_question_links_resource_id", "resource_id"),
        Index("ix_resource_question_links_question_version_id", "question_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("educational_resources.id", ondelete="RESTRICT"), nullable=False
    )
    question_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("question_versions.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class TheoryMaterial(Base):
    """
    Authored theory material (stable parent entity across versions).

    Generic: not tied to "Seasons/Episodes" or any subject-specific concept.
    """

    __tablename__ = "theory_materials"
    __table_args__ = (
        Index("ix_theory_materials_primary_content_node_id", "primary_content_node_id"),
        Index("ix_theory_materials_created_by_external_identity", "created_by_external_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # Convenience direct link to its main content; the full N:N mapping to
    # any number of content nodes happens via ContentResourceLink once a
    # version is published and materialized as an EducationalResource.
    primary_content_node_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("catalog_nodes.id", ondelete="RESTRICT")
    )
    created_by_external_identity: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    versions: Mapped[list[TheoryMaterialVersion]] = relationship(back_populates="material")


class TheoryMaterialVersion(Base):
    """
    A single version of a TheoryMaterial. Once published, it must not be
    silently modified - different students may have used different
    published versions.
    """

    __tablename__ = "theory_material_versions"
    __table_args__ = (
        UniqueConstraint(
            "material_id", "version_number",
            name="uq_theory_material_versions_material_version_number",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_theory_material_versions_status",
        ),
        CheckConstraint("version_number > 0", name="ck_theory_material_versions_version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    material_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("theory_materials.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")

    # Set only when published: materializes this version as a generic
    # resource so it can be linked to content(s) like any other resource.
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("educational_resources.id", ondelete="RESTRICT")
    )

    introduction: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)

    created_by_external_identity: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    material: Mapped[TheoryMaterial] = relationship(back_populates="versions")
    sections: Mapped[list[MaterialSection]] = relationship(
        back_populates="material_version", order_by="MaterialSection.position"
    )
    exercises: Mapped[list[MaterialExercise]] = relationship(back_populates="material_version")


class MaterialSection(Base):
    """
    Ordered content block within a material version (intro, explanation,
    example, image, table, formula, note, summary, ...). section_type is
    intentionally free-form (no CheckConstraint) so new block kinds can be
    added later without a migration.
    """

    __tablename__ = "material_sections"
    __table_args__ = (
        UniqueConstraint(
            "material_version_id", "position",
            name="uq_material_sections_version_position",
        ),
        CheckConstraint("position > 0", name="ck_material_sections_position_positive"),
        Index("ix_material_sections_material_version_id", "material_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    material_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("theory_material_versions.id", ondelete="RESTRICT"), nullable=False
    )
    section_type: Mapped[str] = mapped_column(String(50), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str | None] = mapped_column(Text)
    # Structured payload for images/tables/formulas placeholders; no visual
    # editor is built in this phase.
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    material_version: Mapped[TheoryMaterialVersion] = relationship(back_populates="sections")


class MaterialExercise(Base):
    """
    An exercise attached to a material version (optionally to one of its
    sections). Never duplicates an existing QuestionVersion - references it.
    """

    __tablename__ = "material_exercises"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('EXISTING_QUESTION', 'AUTHORED', 'AI_GENERATED')",
            name="ck_material_exercises_source_type",
        ),
        CheckConstraint(
            "source_type <> 'EXISTING_QUESTION' OR question_version_id IS NOT NULL",
            name="ck_material_exercises_existing_question_requires_id",
        ),
        CheckConstraint("position > 0", name="ck_material_exercises_position_positive"),
        UniqueConstraint(
            "material_version_id", "position",
            name="uq_material_exercises_version_position",
        ),
        Index("ix_material_exercises_material_version_id", "material_version_id"),
        Index("ix_material_exercises_section_id", "section_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    material_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("theory_material_versions.id", ondelete="RESTRICT"), nullable=False
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("material_sections.id", ondelete="RESTRICT")
    )
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    question_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("question_versions.id", ondelete="RESTRICT")
    )
    authored_text: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    points: Mapped[float | None] = mapped_column(Numeric(10, 2))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    material_version: Mapped[TheoryMaterialVersion] = relationship(back_populates="exercises")
