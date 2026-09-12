"""Persist teacher question modification proposals.

Revision ID: 022_modification_proposals
Revises: 021_teacher_list_builder
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "022_modification_proposals"
down_revision = "021_teacher_list_builder"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "modification_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_external_id", sa.String(255), nullable=False),
        sa.Column("modification_type", sa.String(40), nullable=False),
        sa.Column("instruction", sa.Text()),
        sa.Column("proposed_content", postgresql.JSONB(), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_by_external_id", sa.String(255)),
        sa.Column("accepted_question_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_by_external_id", sa.String(255)),
        sa.CheckConstraint("status IN ('PENDING', 'ACCEPTED', 'CANCELLED', 'REJECTED')", name="ck_modification_proposals_status"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assessment_item_id"], ["assessment_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["original_question_version_id"], ["question_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["accepted_question_version_id"], ["question_versions.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_modification_proposals_original_version", "modification_proposals", ["original_question_version_id"])
    op.create_index("ix_modification_proposals_requester", "modification_proposals", ["requested_by_external_id"])


def downgrade() -> None:
    op.drop_index("ix_modification_proposals_requester", table_name="modification_proposals")
    op.drop_index("ix_modification_proposals_original_version", table_name="modification_proposals")
    op.drop_table("modification_proposals")