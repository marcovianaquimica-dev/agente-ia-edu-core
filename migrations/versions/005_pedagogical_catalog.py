"""Create pedagogical catalog domain (Phase 2 foundation).

Generic, subject-agnostic catalog: content taxonomy tree, educational
resources (theory material, book, PDF, video, question set, external,
other), resource<->content and resource<->question links, and authored
theory materials with versioning/sections/exercises.

Revision ID: 005_pedagogical_catalog
Revises: 004_learning_path
Create Date: 2026-08-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "005_pedagogical_catalog"
down_revision: Union[str, None] = "004_learning_path"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # catalog_nodes (self-referencing tree: Discipline -> ... -> Subcontent)
    # ------------------------------------------------------------------
    op.create_table(
        "catalog_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("root_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("node_type", sa.String(length=50), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_catalog_nodes_parent_not_self",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["catalog_nodes.id"], name="fk_catalog_nodes_parent", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["root_id"], ["catalog_nodes.id"], name="fk_catalog_nodes_root", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_catalog_nodes"),
    )
    op.create_index("ix_catalog_nodes_parent_id", "catalog_nodes", ["parent_id"])
    op.create_index("ix_catalog_nodes_root_id", "catalog_nodes", ["root_id"])
    op.create_index("ix_catalog_nodes_node_type", "catalog_nodes", ["node_type"])

    # ------------------------------------------------------------------
    # educational_resources
    # ------------------------------------------------------------------
    op.create_table(
        "educational_resources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("resource_type", sa.String(length=30), nullable=False),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("origin_type", sa.String(length=20), nullable=False),
        sa.Column("owner_external_id", sa.String(length=255), nullable=True),
        sa.Column("license_reference", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("storage_uri", sa.String(length=2048), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("visibility_scope", sa.String(length=20), nullable=False),
        sa.Column("created_by_external_identity", sa.String(length=255), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "resource_type IN ('THEORY_MATERIAL', 'BOOK', 'PDF', 'VIDEO', "
            "'QUESTION_SET', 'EXTERNAL_RESOURCE', 'OTHER')",
            name="ck_educational_resources_resource_type",
        ),
        sa.CheckConstraint(
            "origin_type IN ('AUTHOR', 'SCHOOL', 'PLATFORM', 'LICENSED', 'EXTERNAL')",
            name="ck_educational_resources_origin_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_educational_resources_status",
        ),
        sa.CheckConstraint(
            "visibility_scope IN ('PRIVATE', 'CLASSROOM', 'SCHOOL', 'INSTITUTION', "
            "'SHARED', 'PUBLIC', 'LICENSED')",
            name="ck_educational_resources_visibility_scope",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_educational_resources"),
    )
    op.create_index(
        "ix_educational_resources_resource_type", "educational_resources", ["resource_type"]
    )
    op.create_index(
        "ix_educational_resources_owner_external_id", "educational_resources", ["owner_external_id"]
    )

    # ------------------------------------------------------------------
    # resource_access_grants (depends on educational_resources)
    # ------------------------------------------------------------------
    op.create_table(
        "resource_access_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grantee_type", sa.String(length=20), nullable=False),
        sa.Column("grantee_external_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "grantee_type IN ('INSTITUTION', 'SCHOOL_UNIT', 'CLASSROOM', 'EXTERNAL_IDENTITY')",
            name="ck_resource_access_grants_grantee_type",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["educational_resources.id"],
            name="fk_resource_access_grants_resource", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resource_access_grants"),
        sa.UniqueConstraint(
            "resource_id", "grantee_type", "grantee_external_id",
            name="uq_resource_access_grants_resource_grantee",
        ),
    )

    # ------------------------------------------------------------------
    # video_resource_details / book_resource_details (depend on educational_resources)
    # ------------------------------------------------------------------
    op.create_table(
        "video_resource_details",
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("external_video_id", sa.String(length=255), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("ranking_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.CheckConstraint(
            "platform IN ('YOUTUBE', 'OWN', 'PLATFORM', 'LICENSED', 'EXTERNAL')",
            name="ck_video_resource_details_platform",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["educational_resources.id"],
            name="fk_video_resource_details_resource", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("resource_id", name="pk_video_resource_details"),
    )

    op.create_table(
        "book_resource_details",
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edition", sa.String(length=100), nullable=True),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("isbn", sa.String(length=20), nullable=True),
        sa.Column("processing_status", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "processing_status IN ('NOT_STARTED', 'PENDING', 'PROCESSING', 'PROCESSED', 'FAILED')",
            name="ck_book_resource_details_processing_status",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["educational_resources.id"],
            name="fk_book_resource_details_resource", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("resource_id", name="pk_book_resource_details"),
    )

    # ------------------------------------------------------------------
    # content_resource_links (depends on catalog_nodes + educational_resources)
    # ------------------------------------------------------------------
    op.create_table(
        "content_resource_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pedagogical_role", sa.String(length=20), nullable=False),
        sa.Column("relevance", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("recommended_level", sa.String(length=20), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "pedagogical_role IN ('THEORY', 'EXPLANATION', 'PRACTICE', 'REVIEW', 'VIDEO', 'REFERENCE')",
            name="ck_content_resource_links_pedagogical_role",
        ),
        sa.CheckConstraint(
            "recommended_level IS NULL OR recommended_level IN ('EASY', 'MEDIUM', 'HARD')",
            name="ck_content_resource_links_recommended_level",
        ),
        sa.ForeignKeyConstraint(
            ["content_node_id"], ["catalog_nodes.id"],
            name="fk_content_resource_links_content_node", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["educational_resources.id"],
            name="fk_content_resource_links_resource", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_content_resource_links"),
        sa.UniqueConstraint(
            "content_node_id", "resource_id", "pedagogical_role",
            name="uq_content_resource_links_node_resource_role",
        ),
    )
    op.create_index(
        "ix_content_resource_links_content_node_id", "content_resource_links", ["content_node_id"]
    )
    op.create_index(
        "ix_content_resource_links_resource_id", "content_resource_links", ["resource_id"]
    )

    # ------------------------------------------------------------------
    # content_question_links (depends on catalog_nodes + question_versions)
    # ------------------------------------------------------------------
    op.create_table(
        "content_question_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["content_node_id"], ["catalog_nodes.id"],
            name="fk_content_question_links_content_node", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"], ["question_versions.id"],
            name="fk_content_question_links_question_version", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_content_question_links"),
        sa.UniqueConstraint(
            "content_node_id", "question_version_id",
            name="uq_content_question_links_node_question",
        ),
    )
    op.create_index(
        "ix_content_question_links_content_node_id", "content_question_links", ["content_node_id"]
    )
    op.create_index(
        "ix_content_question_links_question_version_id",
        "content_question_links",
        ["question_version_id"],
    )

    # ------------------------------------------------------------------
    # resource_question_links (depends on educational_resources + question_versions)
    # ------------------------------------------------------------------
    op.create_table(
        "resource_question_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position > 0", name="ck_resource_question_links_position_positive"
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["educational_resources.id"],
            name="fk_resource_question_links_resource", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"], ["question_versions.id"],
            name="fk_resource_question_links_question_version", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resource_question_links"),
        sa.UniqueConstraint(
            "resource_id", "position", name="uq_resource_question_links_resource_position"
        ),
    )
    op.create_index(
        "ix_resource_question_links_resource_id", "resource_question_links", ["resource_id"]
    )
    op.create_index(
        "ix_resource_question_links_question_version_id",
        "resource_question_links",
        ["question_version_id"],
    )

    # ------------------------------------------------------------------
    # theory_materials (depends on catalog_nodes, nullable)
    # ------------------------------------------------------------------
    op.create_table(
        "theory_materials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("primary_content_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_external_identity", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["primary_content_node_id"], ["catalog_nodes.id"],
            name="fk_theory_materials_primary_content_node", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_theory_materials"),
    )
    op.create_index(
        "ix_theory_materials_primary_content_node_id", "theory_materials", ["primary_content_node_id"]
    )
    op.create_index(
        "ix_theory_materials_created_by_external_identity",
        "theory_materials",
        ["created_by_external_identity"],
    )

    # ------------------------------------------------------------------
    # theory_material_versions (depends on theory_materials + educational_resources)
    # ------------------------------------------------------------------
    op.create_table(
        "theory_material_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("material_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("introduction", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_by_external_identity", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_theory_material_versions_status",
        ),
        sa.CheckConstraint(
            "version_number > 0", name="ck_theory_material_versions_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["material_id"], ["theory_materials.id"],
            name="fk_theory_material_versions_material", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["educational_resources.id"],
            name="fk_theory_material_versions_resource", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_theory_material_versions"),
        sa.UniqueConstraint(
            "material_id", "version_number",
            name="uq_theory_material_versions_material_version_number",
        ),
    )

    # ------------------------------------------------------------------
    # material_sections (depends on theory_material_versions)
    # ------------------------------------------------------------------
    op.create_table(
        "material_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("material_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_type", sa.String(length=50), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint("position > 0", name="ck_material_sections_position_positive"),
        sa.ForeignKeyConstraint(
            ["material_version_id"], ["theory_material_versions.id"],
            name="fk_material_sections_material_version", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_material_sections"),
        sa.UniqueConstraint(
            "material_version_id", "position", name="uq_material_sections_version_position"
        ),
    )
    op.create_index(
        "ix_material_sections_material_version_id", "material_sections", ["material_version_id"]
    )

    # ------------------------------------------------------------------
    # material_exercises (depends on theory_material_versions + material_sections + question_versions)
    # ------------------------------------------------------------------
    op.create_table(
        "material_exercises",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("material_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("authored_text", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("points", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "source_type IN ('EXISTING_QUESTION', 'AUTHORED', 'AI_GENERATED')",
            name="ck_material_exercises_source_type",
        ),
        sa.CheckConstraint(
            "source_type <> 'EXISTING_QUESTION' OR question_version_id IS NOT NULL",
            name="ck_material_exercises_existing_question_requires_id",
        ),
        sa.CheckConstraint("position > 0", name="ck_material_exercises_position_positive"),
        sa.ForeignKeyConstraint(
            ["material_version_id"], ["theory_material_versions.id"],
            name="fk_material_exercises_material_version", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["section_id"], ["material_sections.id"],
            name="fk_material_exercises_section", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"], ["question_versions.id"],
            name="fk_material_exercises_question_version", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_material_exercises"),
        sa.UniqueConstraint(
            "material_version_id", "position", name="uq_material_exercises_version_position"
        ),
    )
    op.create_index(
        "ix_material_exercises_material_version_id", "material_exercises", ["material_version_id"]
    )
    op.create_index("ix_material_exercises_section_id", "material_exercises", ["section_id"])


def downgrade() -> None:
    op.drop_index("ix_material_exercises_section_id", table_name="material_exercises")
    op.drop_index("ix_material_exercises_material_version_id", table_name="material_exercises")
    op.drop_table("material_exercises")

    op.drop_index("ix_material_sections_material_version_id", table_name="material_sections")
    op.drop_table("material_sections")

    op.drop_table("theory_material_versions")

    op.drop_index(
        "ix_theory_materials_created_by_external_identity", table_name="theory_materials"
    )
    op.drop_index("ix_theory_materials_primary_content_node_id", table_name="theory_materials")
    op.drop_table("theory_materials")

    op.drop_index(
        "ix_resource_question_links_question_version_id", table_name="resource_question_links"
    )
    op.drop_index("ix_resource_question_links_resource_id", table_name="resource_question_links")
    op.drop_table("resource_question_links")

    op.drop_index(
        "ix_content_question_links_question_version_id", table_name="content_question_links"
    )
    op.drop_index(
        "ix_content_question_links_content_node_id", table_name="content_question_links"
    )
    op.drop_table("content_question_links")

    op.drop_index("ix_content_resource_links_resource_id", table_name="content_resource_links")
    op.drop_index(
        "ix_content_resource_links_content_node_id", table_name="content_resource_links"
    )
    op.drop_table("content_resource_links")

    op.drop_table("book_resource_details")
    op.drop_table("video_resource_details")

    op.drop_table("resource_access_grants")

    op.drop_index(
        "ix_educational_resources_owner_external_id", table_name="educational_resources"
    )
    op.drop_index("ix_educational_resources_resource_type", table_name="educational_resources")
    op.drop_table("educational_resources")

    op.drop_index("ix_catalog_nodes_node_type", table_name="catalog_nodes")
    op.drop_index("ix_catalog_nodes_root_id", table_name="catalog_nodes")
    op.drop_index("ix_catalog_nodes_parent_id", table_name="catalog_nodes")
    op.drop_table("catalog_nodes")
