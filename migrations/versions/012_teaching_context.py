"""Create teaching context and lesson registration domain (Phase 12B.1).

Revision ID: 012_teaching_context
Revises: 011_platform_administration
Create Date: 2026-08-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "012_teaching_context"
down_revision: Union[str, None] = "011_platform_administration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teaching_lessons",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("academic_year", sa.String(length=10), nullable=False, server_default="2026"),
        sa.Column("unit_id", sa.String(length=255), nullable=True),
        sa.Column("segment_id", sa.String(length=255), nullable=True),
        sa.Column("grade_level", sa.String(length=255), nullable=True),
        sa.Column("classroom_id", sa.String(length=255), nullable=False),
        sa.Column("teacher_id", sa.String(length=255), nullable=False),
        sa.Column("content_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subcontent_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lesson_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("summary_observation", sa.Text(), nullable=True),
        sa.Column("pedagogical_context_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes > 0",
            name="ck_teaching_lessons_duration_positive",
        ),
        sa.ForeignKeyConstraint(
            ["school_id"],
            ["schools.id"],
            name="fk_teaching_lessons_school_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["content_node_id"],
            ["catalog_nodes.id"],
            name="fk_teaching_lessons_content_node_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subcontent_node_id"],
            ["catalog_nodes.id"],
            name="fk_teaching_lessons_subcontent_node_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pedagogical_context_id"],
            ["pedagogical_contexts.id"],
            name="fk_teaching_lessons_pedagogical_context_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_teaching_lessons"),
    )
    op.create_index("ix_teaching_lessons_school_id", "teaching_lessons", ["school_id"])
    op.create_index("ix_teaching_lessons_classroom_id", "teaching_lessons", ["classroom_id"])
    op.create_index("ix_teaching_lessons_teacher_id", "teaching_lessons", ["teacher_id"])
    op.create_index("ix_teaching_lessons_content_node_id", "teaching_lessons", ["content_node_id"])
    op.create_index("ix_teaching_lessons_academic_year", "teaching_lessons", ["academic_year"])
    op.create_index("ix_teaching_lessons_lesson_date", "teaching_lessons", ["lesson_date"])


def downgrade() -> None:
    op.drop_index("ix_teaching_lessons_lesson_date", table_name="teaching_lessons")
    op.drop_index("ix_teaching_lessons_academic_year", table_name="teaching_lessons")
    op.drop_index("ix_teaching_lessons_content_node_id", table_name="teaching_lessons")
    op.drop_index("ix_teaching_lessons_teacher_id", table_name="teaching_lessons")
    op.drop_index("ix_teaching_lessons_classroom_id", table_name="teaching_lessons")
    op.drop_index("ix_teaching_lessons_school_id", table_name="teaching_lessons")
    op.drop_table("teaching_lessons")
