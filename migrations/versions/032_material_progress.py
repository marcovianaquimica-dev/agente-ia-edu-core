"""PHASE 25 - Material Delivery & Study Integration.

Revision ID: 032_material_progress
Revises: 031_study_sessions

ONE new table: the SMALLEST structure that answers "where did this student
leave off inside this published material version?" so the Material Player can
resume. Audited first (spec s10): no existing structure already does this -
StudySession.plan (PHASE 24) tracks session-scoped block completion, not a
session-independent material reading position.

One row per (student, material_version) - upsert/idempotent, never a history
log. Explicitly NOT evidence of domain mastery: nothing here is read by
domain_content_mastery (PHASE 20) or the Adaptive Learning Path priority
algorithm (PHASE 21). Touches no official table, no answer key, no
ActivityResult/ActivityResultItem. Fully reversible.
"""

from alembic import op
import sqlalchemy as sa

revision = "032_material_progress"
down_revision = "031_study_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "material_progress",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("student_external_id", sa.String(length=255), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("material_version_id", sa.Uuid(), nullable=False),
        sa.Column("current_section_id", sa.Uuid(), nullable=True),
        sa.Column("current_block_id", sa.Uuid(), nullable=True),
        sa.Column("sections_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["material_id"], ["theory_materials.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["material_version_id"], ["theory_material_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["current_section_id"], ["material_sections.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["current_block_id"], ["material_blocks.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("student_external_id", "material_version_id",
                            name="uq_material_progress_student_version"),
        sa.CheckConstraint("status IN ('IN_PROGRESS', 'COMPLETED')",
                           name="ck_material_progress_status"),
    )
    op.create_index("ix_material_progress_student_material", "material_progress",
                    ["student_external_id", "material_id"])


def downgrade() -> None:
    op.drop_index("ix_material_progress_student_material", table_name="material_progress")
    op.drop_table("material_progress")
