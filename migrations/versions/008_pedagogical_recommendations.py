"""Create pedagogical recommendation and context domain (Phase 6).

Revision ID: 008_pedagogical_recommendations
Revises: 007_pedagogical_classifier
Create Date: 2026-08-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "008_pedagogical_recommendations"
down_revision: Union[str, None] = "007_pedagogical_classifier"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create pedagogical_contexts table
    op.create_table(
        "pedagogical_contexts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("institution_id", sa.String(length=255), nullable=True),
        sa.Column("classroom_id", sa.String(length=255), nullable=True),
        sa.Column("author_id", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('TEACHER', 'COORDINATION', 'SCHOOL_PLAN')",
            name="ck_pedagogical_contexts_source",
        ),
        sa.ForeignKeyConstraint(
            ["content_node_id"],
            ["catalog_nodes.id"],
            name="fk_pedagogical_contexts_content_node_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pedagogical_contexts"),
    )
    op.create_index(
        "ix_pedagogical_contexts_content_node_id",
        "pedagogical_contexts",
        ["content_node_id"],
    )
    op.create_index(
        "ix_pedagogical_contexts_institution_id",
        "pedagogical_contexts",
        ["institution_id"],
    )
    op.create_index(
        "ix_pedagogical_contexts_classroom_id",
        "pedagogical_contexts",
        ["classroom_id"],
    )
    op.create_index(
        "ix_pedagogical_contexts_source",
        "pedagogical_contexts",
        ["source"],
    )

    # 2. Create pedagogical_recommendations table
    op.create_table(
        "pedagogical_recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", sa.String(length=255), nullable=False),
        sa.Column("institution_id", sa.String(length=255), nullable=True),
        sa.Column("classroom_id", sa.String(length=255), nullable=True),
        sa.Column("content_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_type", sa.String(length=50), nullable=False),
        sa.Column("recommended_difficulty", sa.String(length=20), nullable=False, server_default="EASY"),
        sa.Column("priority_score", sa.Numeric(precision=7, scale=2), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("context_source", sa.String(length=30), nullable=False),
        sa.Column("mastery_score_at_recommendation", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "recommendation_type IN ('STUDY_MATERIAL', 'PRACTICE', 'REVIEW', 'WATCH_VIDEO', 'REVIEW_PREREQUISITE')",
            name="ck_pedagogical_recommendations_type",
        ),
        sa.CheckConstraint(
            "recommended_difficulty IN ('EASY', 'MEDIUM', 'HARD')",
            name="ck_pedagogical_recommendations_difficulty",
        ),
        sa.CheckConstraint(
            "context_source IN ('TEACHER', 'COORDINATION', 'SCHOOL_PLAN', 'AUTONOMOUS')",
            name="ck_pedagogical_recommendations_context_source",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'ACCEPTED', 'COMPLETED', 'SUPERSEDED', 'DISMISSED')",
            name="ck_pedagogical_recommendations_status",
        ),
        sa.ForeignKeyConstraint(
            ["content_node_id"],
            ["catalog_nodes.id"],
            name="fk_pedagogical_recommendations_content_node_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["educational_resources.id"],
            name="fk_pedagogical_recommendations_resource_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["question_versions.id"],
            name="fk_pedagogical_recommendations_question_version_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pedagogical_recommendations"),
    )
    op.create_index(
        "ix_pedagogical_recommendations_student_id",
        "pedagogical_recommendations",
        ["student_id"],
    )
    op.create_index(
        "ix_pedagogical_recommendations_content_node_id",
        "pedagogical_recommendations",
        ["content_node_id"],
    )
    op.create_index(
        "ix_pedagogical_recommendations_created_at",
        "pedagogical_recommendations",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pedagogical_recommendations_created_at", table_name="pedagogical_recommendations")
    op.drop_index("ix_pedagogical_recommendations_content_node_id", table_name="pedagogical_recommendations")
    op.drop_index("ix_pedagogical_recommendations_student_id", table_name="pedagogical_recommendations")
    op.drop_table("pedagogical_recommendations")

    op.drop_index("ix_pedagogical_contexts_source", table_name="pedagogical_contexts")
    op.drop_index("ix_pedagogical_contexts_classroom_id", table_name="pedagogical_contexts")
    op.drop_index("ix_pedagogical_contexts_institution_id", table_name="pedagogical_contexts")
    op.drop_index("ix_pedagogical_contexts_content_node_id", table_name="pedagogical_contexts")
    op.drop_table("pedagogical_contexts")
