"""Add persisted teacher material types and list item de-duplication.

Revision ID: 021_teacher_list_builder
Revises: 020_assessment_core_alignment
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa


revision = "021_teacher_list_builder"
down_revision = "020_assessment_core_alignment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessments",
        sa.Column("material_type", sa.String(length=30), nullable=False, server_default="ASSESSMENT"),
    )
    op.create_check_constraint(
        "ck_assessments_material_type",
        "assessments",
        "material_type IN ('EXERCISE_LIST', 'ASSESSMENT', 'SIMULATION')",
    )
    op.create_unique_constraint(
        "uq_assessment_items_version_question_version",
        "assessment_items",
        ["assessment_version_id", "question_version_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_assessment_items_version_question_version",
        "assessment_items",
        type_="unique",
    )
    op.drop_constraint("ck_assessments_material_type", "assessments", type_="check")
    op.drop_column("assessments", "material_type")