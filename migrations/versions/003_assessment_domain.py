"""Create assessment domain.

Revision ID: 003_assessment_domain
Revises: 002_pedagogical_intelligence
Create Date: 2026-08-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "003_assessment_domain"
down_revision: Union[str, None] = "002_pedagogical_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("institution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_external_identity", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
            name="fk_assessments_institution",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assessments"),
    )

    op.create_table(
        "assessment_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_by_external_identity", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'review', 'published', 'archived')",
            name="ck_assessment_versions_status",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["assessments.id"],
            name="fk_assessment_versions_assessment",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assessment_versions"),
        sa.UniqueConstraint(
            "assessment_id",
            "version_number",
            name="uq_assessment_versions_assessment_version_number",
        ),
    )

    op.create_table(
        "assessment_selection_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selection_type", sa.String(length=20), nullable=False),
        sa.Column("original_prompt", sa.Text(), nullable=True),
        sa.Column("requested_count", sa.Integer(), nullable=True),
        sa.Column("criteria", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "selection_type IN ('manual', 'prompt')",
            name="ck_assessment_selection_requests_selection_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_assessment_selection_requests_status",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_version_id"],
            ["assessment_versions.id"],
            name="fk_assessment_selection_requests_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assessment_selection_requests"),
    )

    op.create_table(
        "assessment_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selection_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.CheckConstraint("points >= 0", name="ck_assessment_items_points_nonnegative"),
        sa.ForeignKeyConstraint(
            ["assessment_version_id"],
            ["assessment_versions.id"],
            name="fk_assessment_items_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["question_versions.id"],
            name="fk_assessment_items_question_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["selection_request_id"],
            ["assessment_selection_requests.id"],
            name="fk_assessment_items_selection_request",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assessment_items"),
        sa.UniqueConstraint(
            "assessment_version_id",
            "position",
            name="uq_assessment_items_version_position",
        ),
    )

    op.create_table(
        "assessment_publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("released_immediately", sa.Boolean(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_limit_seconds", sa.Integer(), nullable=True),
        sa.Column("attempts_allowed", sa.Integer(), nullable=True),
        sa.Column("source_display", sa.String(length=20), nullable=False),
        sa.Column("bncc_display", sa.String(length=30), nullable=False),
        sa.Column("show_difficulty", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "publication_type IN ('immediate', 'scheduled')",
            name="ck_assessment_publications_publication_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'closed', 'archived')",
            name="ck_assessment_publications_status",
        ),
        sa.CheckConstraint(
            "source_display IN ('none', 'exam', 'exam_year')",
            name="ck_assessment_publications_source_display",
        ),
        sa.CheckConstraint(
            "bncc_display IN ('none', 'competency', 'skill', 'competency_skill')",
            name="ck_assessment_publications_bncc_display",
        ),
        sa.CheckConstraint(
            "time_limit_seconds IS NULL OR time_limit_seconds > 0",
            name="ck_assessment_publications_time_limit_positive",
        ),
        sa.CheckConstraint(
            "attempts_allowed IS NULL OR attempts_allowed > 0",
            name="ck_assessment_publications_attempts_allowed_positive",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_version_id"],
            ["assessment_versions.id"],
            name="fk_assessment_publications_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assessment_publications"),
    )

    op.create_table(
        "assessment_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_identity_id", sa.String(length=255), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("max_score", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("correct_answers", sa.Integer(), nullable=True),
        sa.Column("answered_count", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('not_started', 'in_progress', 'submitted', 'expired', 'cancelled')",
            name="ck_assessment_attempts_status",
        ),
        sa.CheckConstraint(
            "attempt_number > 0",
            name="ck_assessment_attempts_attempt_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["assessment_publications.id"],
            name="fk_assessment_attempts_publication",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assessment_attempts"),
        sa.UniqueConstraint(
            "publication_id",
            "external_identity_id",
            "attempt_number",
            name="uq_assessment_attempts_publication_student_attempt_number",
        ),
    )

    op.create_table(
        "assessment_answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selected_option_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("first_answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("correction_status", sa.String(length=20), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("points_awarded", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "correction_status IN ('pending', 'correct', 'incorrect', 'ungraded')",
            name="ck_assessment_answers_correction_status",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["assessment_attempts.id"],
            name="fk_assessment_answers_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_item_id"],
            ["assessment_items.id"],
            name="fk_assessment_answers_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["selected_option_id"],
            ["question_options.id"],
            name="fk_assessment_answers_selected_option",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assessment_answers"),
        sa.UniqueConstraint(
            "attempt_id",
            "assessment_item_id",
            name="uq_assessment_answers_attempt_item",
        ),
    )

    op.create_index(
        "ix_assessment_attempts_external_identity_id",
        "assessment_attempts",
        ["external_identity_id"],
    )
    op.create_index(
        "ix_assessment_publications_assessment_version_id",
        "assessment_publications",
        ["assessment_version_id"],
    )
    op.create_index(
        "ix_assessment_items_assessment_version_position",
        "assessment_items",
        ["assessment_version_id", "position"],
    )


def downgrade() -> None:
    op.drop_index("ix_assessment_items_assessment_version_position", table_name="assessment_items")
    op.drop_index("ix_assessment_publications_assessment_version_id", table_name="assessment_publications")
    op.drop_index("ix_assessment_attempts_external_identity_id", table_name="assessment_attempts")
    op.drop_table("assessment_answers")
    op.drop_table("assessment_attempts")
    op.drop_table("assessment_publications")
    op.drop_table("assessment_items")
    op.drop_table("assessment_selection_requests")
    op.drop_table("assessment_versions")
    op.drop_table("assessments")
