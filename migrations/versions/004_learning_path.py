"""Create learning path domain.

Revision ID: 004_learning_path
Revises: 003_assessment_domain
Create Date: 2026-08-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "004_learning_path"
down_revision: Union[str, None] = "003_assessment_domain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add difficulty column to question_versions
    op.add_column(
        "question_versions",
        sa.Column("recommended_difficulty", sa.String(length=20), nullable=True),
    )

    # Create practice_sessions table (must exist before practice_question_selections)
    op.create_table(
        "practice_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_identity_id", sa.String(length=255), nullable=False),
        sa.Column("content_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recommended_difficulty", sa.String(length=20), nullable=False),
        sa.Column("requested_question_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recommendation_reason", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'abandoned')",
            name="ck_practice_sessions_status",
        ),
        sa.ForeignKeyConstraint(
            ["content_node_id"],
            ["taxonomy_nodes.id"],
            name="fk_practice_sessions_content_node",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_practice_sessions"),
    )

    op.create_index(
        "ix_practice_sessions_external_identity_id",
        "practice_sessions",
        ["external_identity_id"],
    )
    op.create_index(
        "ix_practice_sessions_content_node_id",
        "practice_sessions",
        ["content_node_id"],
    )
    op.create_index(
        "ix_practice_sessions_status",
        "practice_sessions",
        ["status"],
    )

    # Create practice_question_selections table (must exist before learning_history)
    op.create_table(
        "practice_question_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("practice_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("difficulty_level", sa.String(length=20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("selected_option_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("points_awarded", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "position > 0",
            name="ck_practice_question_selections_position_positive",
        ),
        sa.ForeignKeyConstraint(
            ["practice_session_id"],
            ["practice_sessions.id"],
            name="fk_practice_question_selections_practice_session",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["question_versions.id"],
            name="fk_practice_question_selections_question_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_practice_question_selections"),
        sa.UniqueConstraint(
            "practice_session_id",
            "position",
            name="uq_practice_question_selections_session_position",
        ),
    )

    op.create_index(
        "ix_practice_question_selections_practice_session_id",
        "practice_question_selections",
        ["practice_session_id"],
    )
    op.create_index(
        "ix_practice_question_selections_question_version_id",
        "practice_question_selections",
        ["question_version_id"],
    )

    # Create learning_history table
    op.create_table(
        "learning_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_identity_id", sa.String(length=255), nullable=False),
        sa.Column("activity_type", sa.String(length=20), nullable=False),
        sa.Column("assessment_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("practice_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "practice_question_selection_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selected_option_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("difficulty_level", sa.String(length=20), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("points_awarded", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("content_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["practice_question_selection_id"],
            ["practice_question_selections.id"],
            name="fk_learning_history_practice_question_selection",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["question_versions.id"],
            name="fk_learning_history_question_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["content_node_id"],
            ["taxonomy_nodes.id"],
            name="fk_learning_history_content_node",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_learning_history"),
    )

    op.create_index(
        "ix_learning_history_external_identity_id",
        "learning_history",
        ["external_identity_id"],
    )
    op.create_index(
        "ix_learning_history_content_node_id",
        "learning_history",
        ["content_node_id"],
    )
    op.create_index(
        "ix_learning_history_activity_type",
        "learning_history",
        ["activity_type"],
    )
    op.create_index(
        "ix_learning_history_identity_activity_created",
        "learning_history",
        ["external_identity_id", "activity_type", "created_at"],
    )

    # Create student_content_mastery table
    op.create_table(
        "student_content_mastery",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_identity_id", sa.String(length=255), nullable=False),
        sa.Column("content_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mastery_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("current_level", sa.String(length=20), nullable=False),
        sa.Column("questions_answered", sa.Integer(), nullable=False),
        sa.Column("questions_correct", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mastery_score >= 0 AND mastery_score <= 100",
            name="ck_student_content_mastery_score_range",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_student_content_mastery_confidence_range",
        ),
        sa.CheckConstraint(
            "questions_correct <= questions_answered",
            name="ck_student_content_mastery_correct_lte_answered",
        ),
        sa.ForeignKeyConstraint(
            ["content_node_id"],
            ["taxonomy_nodes.id"],
            name="fk_student_content_mastery_content_node",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_student_content_mastery"),
        sa.UniqueConstraint(
            "external_identity_id",
            "content_node_id",
            name="uq_student_content_mastery_identity_node",
        ),
    )

    op.create_index(
        "ix_student_content_mastery_external_identity_id",
        "student_content_mastery",
        ["external_identity_id"],
    )
    op.create_index(
        "ix_student_content_mastery_content_node_id",
        "student_content_mastery",
        ["content_node_id"],
    )
    op.create_index(
        "ix_student_content_mastery_current_level",
        "student_content_mastery",
        ["current_level"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_student_content_mastery_current_level",
        table_name="student_content_mastery",
    )
    op.drop_index(
        "ix_student_content_mastery_content_node_id",
        table_name="student_content_mastery",
    )
    op.drop_index(
        "ix_student_content_mastery_external_identity_id",
        table_name="student_content_mastery",
    )
    op.drop_table("student_content_mastery")

    op.drop_index(
        "ix_learning_history_identity_activity_created",
        table_name="learning_history",
    )
    op.drop_index(
        "ix_learning_history_activity_type",
        table_name="learning_history",
    )
    op.drop_index(
        "ix_learning_history_content_node_id",
        table_name="learning_history",
    )
    op.drop_index(
        "ix_learning_history_external_identity_id",
        table_name="learning_history",
    )
    op.drop_table("learning_history")

    op.drop_index(
        "ix_practice_question_selections_question_version_id",
        table_name="practice_question_selections",
    )
    op.drop_index(
        "ix_practice_question_selections_practice_session_id",
        table_name="practice_question_selections",
    )
    op.drop_table("practice_question_selections")

    op.drop_index(
        "ix_practice_sessions_status",
        table_name="practice_sessions",
    )
    op.drop_index(
        "ix_practice_sessions_content_node_id",
        table_name="practice_sessions",
    )
    op.drop_index(
        "ix_practice_sessions_external_identity_id",
        table_name="practice_sessions",
    )
    op.drop_table("practice_sessions")

    op.drop_column("question_versions", "recommended_difficulty")
