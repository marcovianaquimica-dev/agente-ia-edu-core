"""Create video interaction events domain (Phase 9).

Revision ID: 009_video_interactions
Revises: 008_pedagogical_recommendations
Create Date: 2026-08-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "009_video_interactions"
down_revision: Union[str, None] = "008_pedagogical_recommendations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "video_interaction_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", sa.String(length=255), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("progress_percentage", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("feedback_type", sa.String(length=20), nullable=True),
        sa.Column("feedback_reason", sa.String(length=50), nullable=True),
        sa.Column("event_id", sa.String(length=128), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('OPENED', 'STARTED', 'PROGRESS', 'COMPLETED', 'FEEDBACK')",
            name="ck_video_interaction_events_type",
        ),
        sa.CheckConstraint(
            "progress_percentage IS NULL OR (progress_percentage >= 0 AND progress_percentage <= 100)",
            name="ck_video_interaction_events_progress",
        ),
        sa.CheckConstraint(
            "feedback_type IS NULL OR feedback_type IN ('LIKED', 'DISLIKED')",
            name="ck_video_interaction_events_feedback_type",
        ),
        sa.CheckConstraint(
            "feedback_reason IS NULL OR feedback_reason IN "
            "('TOO_FAST', 'TOO_SLOW', 'TOO_BASIC', 'TOO_ADVANCED', 'NOT_CLEAR', "
            "'TOO_MUCH_THEORY', 'NEEDS_EXAMPLES', 'NEEDS_QUESTIONS', 'OTHER')",
            name="ck_video_interaction_events_feedback_reason",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["educational_resources.id"],
            name="fk_video_interaction_events_resource_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["pedagogical_recommendations.id"],
            name="fk_video_interaction_events_recommendation_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["content_node_id"],
            ["catalog_nodes.id"],
            name="fk_video_interaction_events_content_node_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_video_interaction_events"),
        sa.UniqueConstraint("event_id", name="uq_video_interaction_events_event_id"),
    )
    op.create_index(
        "ix_video_interaction_events_student_id",
        "video_interaction_events",
        ["student_id"],
    )
    op.create_index(
        "ix_video_interaction_events_resource_id",
        "video_interaction_events",
        ["resource_id"],
    )
    op.create_index(
        "ix_video_interaction_events_event_type",
        "video_interaction_events",
        ["event_type"],
    )
    op.create_index(
        "ix_video_interaction_events_created_at",
        "video_interaction_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_video_interaction_events_created_at", table_name="video_interaction_events")
    op.drop_index("ix_video_interaction_events_event_type", table_name="video_interaction_events")
    op.drop_index("ix_video_interaction_events_resource_id", table_name="video_interaction_events")
    op.drop_index("ix_video_interaction_events_student_id", table_name="video_interaction_events")
    op.drop_table("video_interaction_events")
