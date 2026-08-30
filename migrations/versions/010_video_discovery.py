"""Create video discovery domain (Phase 10).

Revision ID: 010_video_discovery
Revises: 009_video_interactions
Create Date: 2026-08-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "010_video_discovery"
down_revision: Union[str, None] = "009_video_interactions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_video_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("channel_or_author", sa.String(length=255), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("thumbnail_url", sa.String(length=2048), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("language", sa.String(length=10), nullable=True, server_default="pt-BR"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="DISCOVERED"),
        sa.Column("classification_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("recommended_difficulty", sa.String(length=20), nullable=True),
        sa.Column("content_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("converted_resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('DISCOVERED', 'PENDING_REVIEW', 'CLASSIFIED', 'APPROVED', 'REJECTED', 'AVAILABLE')",
            name="ck_external_video_candidates_status",
        ),
        sa.ForeignKeyConstraint(
            ["content_node_id"],
            ["catalog_nodes.id"],
            name="fk_external_video_candidates_content_node_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["converted_resource_id"],
            ["educational_resources.id"],
            name="fk_external_video_candidates_converted_resource_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_external_video_candidates"),
        sa.UniqueConstraint("source", "external_id", name="uq_external_video_candidates_source_external_id"),
    )
    op.create_index(
        "ix_external_video_candidates_source",
        "external_video_candidates",
        ["source"],
    )
    op.create_index(
        "ix_external_video_candidates_status",
        "external_video_candidates",
        ["status"],
    )
    op.create_index(
        "ix_external_video_candidates_content_node_id",
        "external_video_candidates",
        ["content_node_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_external_video_candidates_content_node_id", table_name="external_video_candidates")
    op.drop_index("ix_external_video_candidates_status", table_name="external_video_candidates")
    op.drop_index("ix_external_video_candidates_source", table_name="external_video_candidates")
    op.drop_table("external_video_candidates")
