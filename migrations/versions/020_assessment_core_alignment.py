"""Align assessment persistence and freeze applied assessment evidence.

Revision ID: 020_assessment_core_alignment
Revises: 019_reception_candidates
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "020_assessment_core_alignment"
down_revision = "019_reception_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assessments", sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("assessments", sa.Column("owner_external_id", sa.String(length=255), nullable=True))
    op.add_column("assessments", sa.Column("visibility_scope", sa.String(length=30), nullable=False, server_default="SCHOOL"))
    op.add_column("assessments", sa.Column("origin_type", sa.String(length=30), nullable=False, server_default="SCHOOL"))
    op.add_column("assessments", sa.Column("scope_type", sa.String(length=30), nullable=True))
    op.add_column("assessments", sa.Column("scope_external_id", sa.String(length=255), nullable=True))
    op.add_column("assessments", sa.Column("metadata", postgresql.JSONB(), nullable=True))
    op.create_foreign_key("fk_assessments_school", "assessments", "schools", ["school_id"], ["id"], ondelete="RESTRICT")
    op.create_check_constraint("ck_assessments_visibility_scope", "assessments", "visibility_scope IN ('PRIVATE', 'CLASSROOM', 'SCHOOL', 'PUBLIC')")
    op.create_check_constraint("ck_assessments_origin_type", "assessments", "origin_type IN ('PLATFORM', 'SCHOOL', 'TEACHER', 'IMPORTED', 'GENERATED')")
    op.create_index("ix_assessments_school_id", "assessments", ["school_id"])

    op.drop_constraint("ck_assessment_versions_status", "assessment_versions", type_="check")
    op.create_check_constraint(
        "ck_assessment_versions_status",
        "assessment_versions",
        "status IN ('draft', 'review', 'approved', 'rejected', 'published', 'archived')",
    )

    op.add_column("assessment_items", sa.Column("frozen_correct_option_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("assessment_items", sa.Column("answer_key_revision_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_assessment_items_frozen_correct_option", "assessment_items", "question_options", ["frozen_correct_option_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_assessment_items_answer_key_revision", "assessment_items", "answer_key_revisions", ["answer_key_revision_id"], ["id"], ondelete="RESTRICT")

    op.drop_constraint("ck_assessment_assignments_recipient_type", "assessment_assignments", type_="check")
    op.create_check_constraint(
        "ck_assessment_assignments_recipient_type",
        "assessment_assignments",
        "recipient_type IN ('STUDENT', 'CLASS', 'GRADE', 'CLASSROOM', 'UNIT', 'SCHOOL', 'USER')",
    )
    op.add_column("assessment_assignments", sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_assessment_assignments_publication", "assessment_assignments", "assessment_publications", ["publication_id"], ["id"], ondelete="RESTRICT")
    op.drop_constraint("uq_assessment_assignments_assessment_recipient", "assessment_assignments", type_="unique")
    op.create_unique_constraint(
        "uq_assessment_assignments_publication_recipient",
        "assessment_assignments",
        ["publication_id", "recipient_type", "recipient_id"],
    )
    op.create_index("ix_assessment_assignments_publication_id", "assessment_assignments", ["publication_id"])

    op.add_column("assessment_attempts", sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("assessment_attempts", sa.Column("metadata", postgresql.JSONB(), nullable=True))
    op.create_foreign_key("fk_assessment_attempts_assignment", "assessment_attempts", "assessment_assignments", ["assignment_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_assessment_attempts_assignment_id", "assessment_attempts", ["assignment_id"])


def downgrade() -> None:
    op.drop_index("ix_assessment_attempts_assignment_id", table_name="assessment_attempts")
    op.drop_constraint("fk_assessment_attempts_assignment", "assessment_attempts", type_="foreignkey")
    op.drop_column("assessment_attempts", "metadata")
    op.drop_column("assessment_attempts", "assignment_id")

    op.drop_index("ix_assessment_assignments_publication_id", table_name="assessment_assignments")
    op.drop_constraint("uq_assessment_assignments_publication_recipient", "assessment_assignments", type_="unique")
    op.create_unique_constraint(
        "uq_assessment_assignments_assessment_recipient",
        "assessment_assignments",
        ["assessment_id", "recipient_type", "recipient_id"],
    )
    op.drop_constraint("fk_assessment_assignments_publication", "assessment_assignments", type_="foreignkey")
    op.drop_column("assessment_assignments", "publication_id")
    op.drop_constraint("ck_assessment_assignments_recipient_type", "assessment_assignments", type_="check")
    op.create_check_constraint(
        "ck_assessment_assignments_recipient_type",
        "assessment_assignments",
        "recipient_type IN ('STUDENT', 'CLASS', 'GRADE', 'CLASSROOM', 'UNIT', 'SCHOOL', 'USER')",
    )

    op.drop_constraint("fk_assessment_items_answer_key_revision", "assessment_items", type_="foreignkey")
    op.drop_constraint("fk_assessment_items_frozen_correct_option", "assessment_items", type_="foreignkey")
    op.drop_column("assessment_items", "answer_key_revision_id")
    op.drop_column("assessment_items", "frozen_correct_option_id")

    op.drop_constraint("ck_assessment_versions_status", "assessment_versions", type_="check")
    op.create_check_constraint(
        "ck_assessment_versions_status",
        "assessment_versions",
        "status IN ('draft', 'review', 'published', 'archived')",
    )

    op.drop_index("ix_assessments_school_id", table_name="assessments")
    op.drop_constraint("ck_assessments_origin_type", "assessments", type_="check")
    op.drop_constraint("ck_assessments_visibility_scope", "assessments", type_="check")
    op.drop_constraint("fk_assessments_school", "assessments", type_="foreignkey")
    op.drop_column("assessments", "metadata")
    op.drop_column("assessments", "scope_external_id")
    op.drop_column("assessments", "scope_type")
    op.drop_column("assessments", "origin_type")
    op.drop_column("assessments", "visibility_scope")
    op.drop_column("assessments", "owner_external_id")
    op.drop_column("assessments", "school_id")