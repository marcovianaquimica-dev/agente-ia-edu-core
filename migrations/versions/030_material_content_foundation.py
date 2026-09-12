"""PHASE 23 - Content & Question Bank Foundation.

Revision ID: 030_material_content_foundation
Revises: 029_domain_content_mastery

Purely ADDITIVE, fully reversible. It extends the ALREADY EXISTING authored
material stack (theory_materials / theory_material_versions / material_sections /
material_exercises, from migration 005 + 014) so it can carry:

  * the material identity a teacher needs (kind, authoring source, visibility,
    description) on the STABLE parent row;
  * a structured, versionable curriculum association at SECTION level
    (content_node_id + curriculum_relation_type) - codes are resolved from
    catalog_nodes, never copied;
  * a pedagogical relation_type on the material <-> question link
    (material_exercises already references question_versions and never copies a
    question);
  * the 4th structural level MaterialBlock (reusable content units under a
    section: TEXT / HEADING / DEFINITION / FORMULA / EXAMPLE / ...).

NOTHING here touches questions / question_versions / question_options /
answer_key_* / catalog_nodes / pedagogical_classifications / activity_* /
domain_content_mastery. The 332 official questions and every gabarito are
untouched. No data is migrated.
"""

from alembic import op
import sqlalchemy as sa

revision = "030_material_content_foundation"
down_revision = "029_domain_content_mastery"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")

# The live DB shipped ck_theory_material_versions_status with the original
# 3-value lowercase set (draft/published/archived) from migration 005, but the
# ORM model + the TheoryMaterialService lifecycle (submit/approve/reject) have
# long expected the 6-value UPPER set. This drift makes the authored-material
# path unusable on PostgreSQL (the tables are empty for exactly this reason).
# PHASE 23 reconciles the constraint to the model - a pure widening.
_STATUS_CK = "ck_theory_material_versions_status"
_STATUS_NEW = (
    "status IN ('DRAFT', 'PENDING_REVIEW', 'APPROVED', 'REJECTED', 'PUBLISHED', 'ARCHIVED')"
)
_STATUS_OLD = "status IN ('draft', 'published', 'archived')"


def _swap_status_check(new_expr: str, old_expr: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.drop_constraint(_STATUS_CK, "theory_material_versions", type_="check")
    op.create_check_constraint(_STATUS_CK, "theory_material_versions", new_expr)


def upgrade() -> None:
    _swap_status_check(_STATUS_NEW, _STATUS_OLD)

    # -- theory_materials: identity fields on the stable parent -------------
    op.add_column("theory_materials", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("theory_materials", sa.Column("material_kind", sa.String(length=30), nullable=True))
    op.add_column("theory_materials", sa.Column("authoring_source", sa.String(length=20), nullable=True))
    op.add_column(
        "theory_materials",
        sa.Column("visibility_scope", sa.String(length=20), nullable=False, server_default="PRIVATE"),
    )

    # -- material_sections: structured curriculum association --------------
    op.add_column("material_sections", sa.Column("content_node_id", sa.Uuid(), nullable=True))
    op.add_column(
        "material_sections",
        sa.Column("curriculum_relation_type", sa.String(length=30), nullable=True),
    )
    op.create_foreign_key(
        "fk_material_sections_content_node",
        "material_sections",
        "catalog_nodes",
        ["content_node_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_material_sections_content_node_id", "material_sections", ["content_node_id"]
    )

    # -- material_exercises: pedagogical relation on the question link -----
    op.add_column(
        "material_exercises", sa.Column("relation_type", sa.String(length=30), nullable=True)
    )

    # -- material_blocks: 4th structural level (reusable content units) ----
    op.create_table(
        "material_blocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("material_version_id", sa.Uuid(), nullable=False),
        sa.Column("block_type", sa.String(length=50), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("metadata", _JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["section_id"], ["material_sections.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["material_version_id"], ["theory_material_versions.id"],
                                ondelete="RESTRICT"),
        sa.UniqueConstraint("section_id", "position", name="uq_material_blocks_section_position"),
        sa.CheckConstraint("position > 0", name="ck_material_blocks_position_positive"),
    )
    op.create_index("ix_material_blocks_section_id", "material_blocks", ["section_id"])
    op.create_index("ix_material_blocks_material_version_id", "material_blocks", ["material_version_id"])


def downgrade() -> None:
    op.drop_index("ix_material_blocks_material_version_id", table_name="material_blocks")
    op.drop_index("ix_material_blocks_section_id", table_name="material_blocks")
    op.drop_table("material_blocks")

    op.drop_column("material_exercises", "relation_type")

    op.drop_index("ix_material_sections_content_node_id", table_name="material_sections")
    op.drop_constraint("fk_material_sections_content_node", "material_sections", type_="foreignkey")
    op.drop_column("material_sections", "curriculum_relation_type")
    op.drop_column("material_sections", "content_node_id")

    op.drop_column("theory_materials", "visibility_scope")
    op.drop_column("theory_materials", "authoring_source")
    op.drop_column("theory_materials", "material_kind")
    op.drop_column("theory_materials", "description")

    _swap_status_check(_STATUS_OLD, _STATUS_NEW)
