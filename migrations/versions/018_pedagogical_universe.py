"""Create pedagogical universe authorization infrastructure.

Revision ID: 018_pedagogical_universe
Revises: 017_assignment_workflow_audit
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "018_pedagogical_universe"
down_revision = "017_assignment_workflow_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pedagogical_universes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="DRAFT"),
        sa.Column("owner_type", sa.String(length=20), nullable=False),
        sa.Column("owner_external_id", sa.String(length=255), nullable=True),
        sa.Column("default_for_context", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("configuration_version", sa.String(length=50), nullable=False, server_default="v1"),
        sa.Column("configuration", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("external_id", name="uq_pedagogical_universes_external_id"),
        sa.UniqueConstraint("slug", name="uq_pedagogical_universes_slug"),
        sa.CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')", name="ck_pedagogical_universes_status"),
        sa.CheckConstraint("owner_type IN ('PLATFORM', 'SCHOOL', 'PARTNER', 'PRODUCT', 'COURSE')", name="ck_pedagogical_universes_owner_type"),
    )
    op.create_index("ix_pedagogical_universes_owner_status", "pedagogical_universes", ["owner_type", "owner_external_id", "status"])
    op.create_table(
        "pedagogical_universe_catalog_scopes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_kind", sa.String(length=20), nullable=False),
        sa.Column("include_descendants", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["universe_id"], ["pedagogical_universes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["catalog_node_id"], ["catalog_nodes.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("universe_id", "catalog_node_id", name="uq_pedagogical_universe_catalog_scope"),
        sa.CheckConstraint("scope_kind IN ('AREA', 'DISCIPLINE', 'CONTENT')", name="ck_pedagogical_universe_catalog_scope_kind"),
    )
    op.create_index("ix_pedagogical_universe_catalog_scopes_node", "pedagogical_universe_catalog_scopes", ["catalog_node_id"])
    op.create_table(
        "pedagogical_universe_academic_scopes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("segment", sa.String(length=100), nullable=True),
        sa.Column("grade_level", sa.String(length=100), nullable=True),
        sa.Column("unit_id", sa.String(length=255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["universe_id"], ["pedagogical_universes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("universe_id", "segment", "grade_level", "unit_id", name="uq_pedagogical_universe_academic_scope"),
    )
    op.create_index("ix_pedagogical_universe_academic_scope_context", "pedagogical_universe_academic_scopes", ["universe_id", "segment", "grade_level", "active"])
    op.create_table(
        "pedagogical_universe_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("universe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_type", sa.String(length=30), nullable=False),
        sa.Column("subject_external_id", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["universe_id"], ["pedagogical_universes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("universe_id", "subject_type", "subject_external_id", name="uq_pedagogical_universe_binding"),
        sa.CheckConstraint("subject_type IN ('SCHOOL', 'EXTERNAL_IDENTITY', 'PRODUCT_CONTEXT')", name="ck_pedagogical_universe_binding_subject_type"),
    )
    op.create_index("ix_pedagogical_universe_bindings_subject", "pedagogical_universe_bindings", ["subject_type", "subject_external_id", "active"])


def downgrade() -> None:
    op.drop_index("ix_pedagogical_universe_bindings_subject", table_name="pedagogical_universe_bindings")
    op.drop_table("pedagogical_universe_bindings")
    op.drop_index("ix_pedagogical_universe_academic_scope_context", table_name="pedagogical_universe_academic_scopes")
    op.drop_table("pedagogical_universe_academic_scopes")
    op.drop_index("ix_pedagogical_universe_catalog_scopes_node", table_name="pedagogical_universe_catalog_scopes")
    op.drop_table("pedagogical_universe_catalog_scopes")
    op.drop_index("ix_pedagogical_universes_owner_status", table_name="pedagogical_universes")
    op.drop_table("pedagogical_universes")