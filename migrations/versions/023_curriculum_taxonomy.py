"""Add stable catalog codes and curriculum prerequisite relations.

Revision ID: 023_curriculum_taxonomy
Revises: 022_modification_proposals
"""

from alembic import op
import sqlalchemy as sa

revision = "023_curriculum_taxonomy"
down_revision = "022_modification_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_catalog_nodes_code", "catalog_nodes", ["code"])
    op.create_table(
        "catalog_node_prerequisites",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("content_node_id", sa.Uuid(), nullable=False),
        sa.Column("prerequisite_node_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("content_node_id", "prerequisite_node_id", name="uq_catalog_node_prerequisite"),
        sa.CheckConstraint("content_node_id <> prerequisite_node_id", name="ck_catalog_node_prerequisite_not_self"),
        sa.ForeignKeyConstraint(["content_node_id"], ["catalog_nodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["prerequisite_node_id"], ["catalog_nodes.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_catalog_node_prerequisites_content", "catalog_node_prerequisites", ["content_node_id"])
    op.create_index("ix_catalog_node_prerequisites_prerequisite", "catalog_node_prerequisites", ["prerequisite_node_id"])


def downgrade() -> None:
    op.drop_index("ix_catalog_node_prerequisites_prerequisite", table_name="catalog_node_prerequisites")
    op.drop_index("ix_catalog_node_prerequisites_content", table_name="catalog_node_prerequisites")
    op.drop_table("catalog_node_prerequisites")
    op.drop_constraint("uq_catalog_nodes_code", "catalog_nodes", type_="unique")