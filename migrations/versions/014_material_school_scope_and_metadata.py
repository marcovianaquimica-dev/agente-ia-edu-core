"""Fix material schema and add real school scoping.

Revision ID: 014_material_school_scope_and_metadata
Revises: 013_initial_diagnostic
Create Date: 2026-08-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "014_material_school_scope_and_metadata"
down_revision: Union[str, None] = "013_initial_diagnostic"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("theory_materials", schema=None) as batch_op:
        batch_op.add_column(sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True))
        batch_op.create_index(batch_op.f("ix_theory_materials_school_id"), ["school_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_theory_materials_school",
            "schools",
            ["school_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("theory_material_versions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("metadata", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("theory_material_versions", schema=None) as batch_op:
        batch_op.drop_column("metadata")

    with op.batch_alter_table("theory_materials", schema=None) as batch_op:
        batch_op.drop_constraint("fk_theory_materials_school", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_theory_materials_school_id"))
        batch_op.drop_column("school_id")
