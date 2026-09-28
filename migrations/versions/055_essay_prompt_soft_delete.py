"""Add deleted_at to essay_prompts for the teacher "lixeira" (trash).

Revision ID: 055_essay_prompt_soft_delete
Revises: 054_prompt_material_file_type

A teacher can now soft-delete an essay proposal instead of it just sitting
in the list forever - it moves to a "Lixeira" view for 30 days (see
services/essay_proposal.py's TRASH_RETENTION_DAYS), restorable with all its
materials/assignments/submissions/corrections untouched, since only this
one column changes. Purely additive: one nullable column, one index for the
trash-listing query (school_id, deleted_at IS NOT NULL), no existing row is
touched (default NULL = every current prompt stays active/visible).
"""

from alembic import op
import sqlalchemy as sa

revision = "055_essay_prompt_soft_delete"
down_revision = "054_prompt_material_file_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "essay_prompts",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_essay_prompts_school_id_deleted_at",
        "essay_prompts",
        ["school_id", "deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_essay_prompts_school_id_deleted_at", table_name="essay_prompts")
    op.drop_column("essay_prompts", "deleted_at")
