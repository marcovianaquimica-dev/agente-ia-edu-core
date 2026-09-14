"""Repoint student_content_mastery.content_node_id at catalog_nodes, not the
abandoned taxonomy_nodes table.

student_content_mastery was created in migration 004 (2026-08-29), before the
curriculum-v2 catalog_nodes system existed. Every consumer of this table
(TeacherPortalService, CoordinationPortalService, DomainMapService) has always
looked the id up against CatalogNode, never TaxonomyNode - taxonomy_nodes and
taxonomies are empty and unused. Because content_node_id is NOT NULL, the
stale FK made it impossible to insert a real (catalog-backed) mastery row at
all. The table is empty in every environment, so this is a pure schema fix
with nothing to migrate.

Revision ID: 039_student_mastery_catalog_fk
Revises: 038_authorial_classification
Create Date: 2026-09-15
"""

from alembic import op


revision = "039_student_mastery_catalog_fk"
down_revision = "038_authorial_classification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_student_content_mastery_content_node",
        "student_content_mastery",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_student_content_mastery_content_node",
        "student_content_mastery",
        "catalog_nodes",
        ["content_node_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_student_content_mastery_content_node",
        "student_content_mastery",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_student_content_mastery_content_node",
        "student_content_mastery",
        "taxonomy_nodes",
        ["content_node_id"],
        ["id"],
        ondelete="RESTRICT",
    )
