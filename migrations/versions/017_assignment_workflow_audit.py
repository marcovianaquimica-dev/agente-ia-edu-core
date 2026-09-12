"""Add assessment assignment and workflow audit tables for Block C.

Revision ID: 017_assignment_workflow_audit
Revises: 016_question_governance
Create Date: 2026-08-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "017_assignment_workflow_audit"
down_revision = "016_question_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recipient_type", sa.String(length=30), nullable=False),
        sa.Column("recipient_id", sa.String(length=255), nullable=False),
        sa.Column("assigned_by_external_id", sa.String(length=255), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="PENDING"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "recipient_type IN ('STUDENT', 'CLASS', 'GRADE', 'CLASSROOM', 'UNIT', 'SCHOOL', 'USER')",
            name="ck_assessment_assignments_recipient_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
            name="ck_assessment_assignments_status",
        ),
        sa.UniqueConstraint("assessment_id", "recipient_type", "recipient_id", name="uq_assessment_assignments_assessment_recipient"),
    )
    op.create_index("ix_assessment_assignments_assessment_id", "assessment_assignments", ["assessment_id"])
    op.create_index("ix_assessment_assignments_school_id", "assessment_assignments", ["school_id"])
    op.create_index("ix_assessment_assignments_recipient_type", "assessment_assignments", ["recipient_type"])
    op.create_index("ix_assessment_assignments_status", "assessment_assignments", ["status"])

    op.create_table(
        "assessment_workflow_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("previous_status", sa.String(length=30), nullable=True),
        sa.Column("new_status", sa.String(length=30), nullable=True),
        sa.Column("performed_by_external_id", sa.String(length=255), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata_", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_assessment_workflow_audit_assessment_id", "assessment_workflow_audit", ["assessment_id"])
    op.create_index("ix_assessment_workflow_audit_action", "assessment_workflow_audit", ["action"])
    op.create_index("ix_assessment_workflow_audit_created_at", "assessment_workflow_audit", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_assessment_workflow_audit_created_at", table_name="assessment_workflow_audit")
    op.drop_index("ix_assessment_workflow_audit_action", table_name="assessment_workflow_audit")
    op.drop_index("ix_assessment_workflow_audit_assessment_id", table_name="assessment_workflow_audit")
    op.drop_table("assessment_workflow_audit")

    op.drop_index("ix_assessment_assignments_status", table_name="assessment_assignments")
    op.drop_index("ix_assessment_assignments_recipient_type", table_name="assessment_assignments")
    op.drop_index("ix_assessment_assignments_school_id", table_name="assessment_assignments")
    op.drop_index("ix_assessment_assignments_assessment_id", table_name="assessment_assignments")
    op.drop_table("assessment_assignments")
