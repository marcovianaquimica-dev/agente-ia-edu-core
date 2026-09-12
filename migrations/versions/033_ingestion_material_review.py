"""PHASE 26 - Authorial Material Ingestion Engine.

Revision ID: 033_ingestion_material_review
Revises: 032_material_progress

ONE new table. It sits ALONGSIDE the existing PHASE 3 ingestion engine
(ingestion_documents/runs/sections/questions/assets - all unchanged, still
used unmodified by the gated ENEM/official pipeline) and tracks exactly the
extra state authorial ingestion needs: tenant ownership, declared
provenance, a curriculum-v2 classification suggestion (never an auto-created
node), and the review -> approval -> publication status machine. One row per
ingestion_documents row (1:1, enforced by a unique constraint).

Touches no official table (questions/question_versions/question_options/
answer_key_entries/catalog_nodes/pedagogical_classifications) and does not
alter ingestion_documents/runs/sections/questions/assets in any way. Fully
reversible.
"""

from alembic import op
import sqlalchemy as sa

revision = "033_ingestion_material_review"
down_revision = "032_material_progress"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "ingestion_material_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ingestion_document_id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=True),
        sa.Column("origin_type", sa.String(length=20), nullable=False, server_default="AUTHORIAL"),
        sa.Column("review_status", sa.String(length=20), nullable=False, server_default="PENDING_REVIEW"),
        sa.Column("discipline_code", sa.String(length=120), nullable=True),
        sa.Column("area_code", sa.String(length=120), nullable=True),
        sa.Column("content_code", sa.String(length=120), nullable=True),
        sa.Column("subcontent_codes", _JSON, nullable=True),
        sa.Column("classification_state", sa.String(length=20), nullable=True),
        sa.Column("classification_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("classification_source", sa.String(length=30), nullable=True),
        sa.Column("structure_issues", _JSON, nullable=True),
        sa.Column("exercises_detected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("theory_material_id", sa.Uuid(), nullable=True),
        sa.Column("theory_material_version_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_by_external_identity", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", _JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["ingestion_document_id"], ["ingestion_documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["theory_material_id"], ["theory_materials.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["theory_material_version_id"], ["theory_material_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("ingestion_document_id", name="uq_ingestion_material_reviews_document"),
        sa.CheckConstraint("origin_type IN ('AUTHORIAL', 'INSTITUTIONAL', 'EXTERNAL', 'OTHER')",
                           name="ck_ingestion_material_reviews_origin_type"),
        sa.CheckConstraint(
            "review_status IN ('PENDING_REVIEW', 'NEEDS_REVIEW', 'APPROVED', 'PUBLISHED', 'REJECTED')",
            name="ck_ingestion_material_reviews_review_status"),
        sa.CheckConstraint(
            "classification_state IS NULL OR classification_state IN ('MAPPED', 'TAXONOMY_GAP')",
            name="ck_ingestion_material_reviews_classification_state"),
    )
    op.create_index("ix_ingestion_material_reviews_document", "ingestion_material_reviews",
                    ["ingestion_document_id"])
    op.create_index("ix_ingestion_material_reviews_school", "ingestion_material_reviews",
                    ["school_id"])
    op.create_index("ix_ingestion_material_reviews_status", "ingestion_material_reviews",
                    ["review_status"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_material_reviews_status", table_name="ingestion_material_reviews")
    op.drop_index("ix_ingestion_material_reviews_school", table_name="ingestion_material_reviews")
    op.drop_index("ix_ingestion_material_reviews_document", table_name="ingestion_material_reviews")
    op.drop_table("ingestion_material_reviews")
