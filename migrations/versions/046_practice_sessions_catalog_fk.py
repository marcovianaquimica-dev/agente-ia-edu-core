"""Repoint practice_sessions.content_node_id at catalog_nodes, not the
abandoned taxonomy_nodes table.

Same root cause and same fix as migrations 039 (student_content_mastery)
and 045 (learning_history): practice_sessions was created before the
curriculum-v2 catalog_nodes system existed, and content_node_id (nullable)
still targets the empty, unused taxonomy_nodes table. This broke
POST /api/v1/practice/sessions with a real ForeignKeyViolation for any
real content node id (all catalog_nodes rows), including the recommended
node on the student dashboard and any node the student manually picks.
The only existing row in every environment has content_node_id NULL (every
attempt with a real node failed at INSERT and rolled back), so this is a
pure schema fix with nothing to migrate.

Revision ID: 046_practice_sessions_catalog_fk
Revises: 045_learning_history_catalog_fk
Create Date: 2026-09-15
"""

from alembic import op


revision = "046_practice_sessions_catalog_fk"
down_revision = "045_learning_history_catalog_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_practice_sessions_content_node",
        "practice_sessions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_practice_sessions_content_node",
        "practice_sessions",
        "catalog_nodes",
        ["content_node_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_practice_sessions_content_node",
        "practice_sessions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_practice_sessions_content_node",
        "practice_sessions",
        "taxonomy_nodes",
        ["content_node_id"],
        ["id"],
        ondelete="RESTRICT",
    )
