"""Repoint learning_history.content_node_id at catalog_nodes, not the
abandoned taxonomy_nodes table.

Same root cause and same fix as migration 039 (student_content_mastery):
learning_history was created in migration 004 (2026-08-29), before the
curriculum-v2 catalog_nodes system existed, and content_node_id (nullable)
still targets the empty, unused taxonomy_nodes table. The column is empty
in every environment (0 rows in learning_history), so this is a pure
schema fix with nothing to migrate. Needed to let
ActivityCorrectionStore.correct() record real per-question evidence
(content_node_id resolved via ContentQuestionLink) for the domain-map
view, which previously had no evidence-writing path at all.

Revision ID: 045_learning_history_catalog_fk
Revises: 044_institution_settings
Create Date: 2026-09-14
"""

from alembic import op


revision = "045_learning_history_catalog_fk"
down_revision = "044_institution_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_learning_history_content_node",
        "learning_history",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_learning_history_content_node",
        "learning_history",
        "catalog_nodes",
        ["content_node_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_learning_history_content_node",
        "learning_history",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_learning_history_content_node",
        "learning_history",
        "taxonomy_nodes",
        ["content_node_id"],
        ["id"],
        ondelete="RESTRICT",
    )
