"""Segmentacao + envio em lote (2026-09-28) - material_assignments: distribute
a theory material VERSION to a recipient.

Revision ID: 056_material_assignments
Revises: 055_essay_prompt_soft_delete

Mirrors ``activity_assignments`` (PHASE 16, migration 026) but for theory
material instead of a published question list - a deliberately separate,
parallel table (not a generalization of activity_assignments to cover both
content types). ``TheoryMaterial``/``TheoryMaterialVersion`` (migration for
PHASE 23) model the material catalog with no concept of "assigned to a
student/class" - ``StudentMaterialService.list_materials`` reads it as a
purely general catalog filtered by ``visibility_scope``. This table only
records WHO was assigned a given material version and WHEN.

Recipients are referenced institutionally (target_type STUDENT | CLASS +
target_id), never copied rosters - same convention as activity_assignments.
GRADE distribution is intentionally not enabled here either (same
pre-existing limitation activity_assignments already has).

No existing table is touched.
"""

from alembic import op
import sqlalchemy as sa

revision = "056_material_assignments"
down_revision = "055_essay_prompt_soft_delete"
branch_labels = None
depends_on = None

TABLE = "material_assignments"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_version_id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_external_id", sa.String(length=255), nullable=True),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("metadata", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["material_version_id"], ["theory_material_versions.id"],
                                ondelete="RESTRICT", name="fk_material_assignments_material_version"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_material_assignments_school"),
        sa.CheckConstraint("target_type IN ('STUDENT', 'CLASS')",
                           name="ck_material_assignments_target_type"),
        sa.CheckConstraint("status IN ('ACTIVE', 'CANCELLED')",
                           name="ck_material_assignments_status"),
    )
    op.create_index("ix_material_assignments_school_id", TABLE, ["school_id"])
    op.create_index("ix_material_assignments_material_version_id", TABLE, ["material_version_id"])
    op.create_index("ix_material_assignments_target", TABLE, ["target_type", "target_id"])
    # duplicate control (spec s2.2): at most one ACTIVE assignment of a given
    # published material version to the same recipient.
    op.create_index(
        "uq_material_assignments_active_target", TABLE,
        ["material_version_id", "target_type", "target_id"],
        unique=True, postgresql_where=sa.text("status = 'ACTIVE'"),
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    for ix in ("uq_material_assignments_active_target",
               "ix_material_assignments_target",
               "ix_material_assignments_material_version_id",
               "ix_material_assignments_school_id"):
        op.drop_index(ix, table_name=TABLE)
    op.drop_table(TABLE)
