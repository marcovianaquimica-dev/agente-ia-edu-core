"""Create initial diagnostic domain (Phase 13).

Revision ID: 013_initial_diagnostic
Revises: 012_teaching_context
Create Date: 2026-08-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "013_initial_diagnostic"
down_revision: Union[str, None] = "012_teaching_context"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create initial_diagnostics table
    op.create_table(
        "initial_diagnostics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", sa.String(length=255), nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("classroom_id", sa.String(length=255), nullable=True),
        sa.Column("academic_year", sa.String(length=10), nullable=False, server_default="2026"),
        sa.Column("grade_level", sa.String(length=255), nullable=True),
        sa.Column("discipline", sa.String(length=255), nullable=False, server_default="Química"),
        sa.Column("diagnostic_version", sa.String(length=20), nullable=False, server_default="v1"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="NOT_STARTED"),
        sa.Column("total_questions_asked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_correct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overall_confidence", sa.Numeric(precision=5, scale=4), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED', 'INSUFFICIENT_EVIDENCE', 'CANCELLED')",
            name="ck_initial_diagnostics_status",
        ),
        sa.ForeignKeyConstraint(
            ["school_id"],
            ["schools.id"],
            name="fk_initial_diagnostics_school_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_initial_diagnostics"),
    )
    op.create_index("ix_initial_diagnostics_student_id", "initial_diagnostics", ["student_id"])
    op.create_index("ix_initial_diagnostics_school_id", "initial_diagnostics", ["school_id"])
    op.create_index("ix_initial_diagnostics_status", "initial_diagnostics", ["status"])
    op.create_index("ix_initial_diagnostics_created_at", "initial_diagnostics", ["created_at"])

    # 2. Create diagnostic_question_selections table
    op.create_table(
        "diagnostic_question_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("diagnostic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("difficulty_level", sa.String(length=20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("selected_option_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("position > 0", name="ck_diagnostic_selections_position_positive"),
        sa.ForeignKeyConstraint(
            ["diagnostic_id"],
            ["initial_diagnostics.id"],
            name="fk_diagnostic_selections_diagnostic_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["question_versions.id"],
            name="fk_diagnostic_selections_question_version_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["content_node_id"],
            ["catalog_nodes.id"],
            name="fk_diagnostic_selections_content_node_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_diagnostic_question_selections"),
        sa.UniqueConstraint("diagnostic_id", "position", name="uq_diagnostic_selections_diagnostic_position"),
    )
    op.create_index("ix_diagnostic_selections_diagnostic_id", "diagnostic_question_selections", ["diagnostic_id"])
    op.create_index("ix_diagnostic_selections_question_version_id", "diagnostic_question_selections", ["question_version_id"])


def downgrade() -> None:
    op.drop_index("ix_diagnostic_selections_question_version_id", table_name="diagnostic_question_selections")
    op.drop_index("ix_diagnostic_selections_diagnostic_id", table_name="diagnostic_question_selections")
    op.drop_table("diagnostic_question_selections")

    op.drop_index("ix_initial_diagnostics_created_at", table_name="initial_diagnostics")
    op.drop_index("ix_initial_diagnostics_status", table_name="initial_diagnostics")
    op.drop_index("ix_initial_diagnostics_school_id", table_name="initial_diagnostics")
    op.drop_index("ix_initial_diagnostics_student_id", table_name="initial_diagnostics")
    op.drop_table("initial_diagnostics")
