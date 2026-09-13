"""R0 Fase 1 - academic year, unit, segment, grade level and class.

Revision ID: 041_academic_hierarchy
Revises: 040_academic_identity

Purely additive: five new tables, no existing table touched.

``classes`` deliberately references ``academic_years``. A class is not a name
that persists across years; "1ª Série A - 2026" and "1ª Série A - 2027" are
different rows, because a student's history is only legible if the class carries
its year (REDAÇÃO spec §13).

Every table here carries ``school_id`` and every key between them is COMPOSITE,
so a class cannot take its year from one school and its grade level from
another. See the module docstring of ``db/models/academic.py``.
"""

from alembic import op
import sqlalchemy as sa

revision = "041_academic_hierarchy"
down_revision = "040_academic_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "academic_years",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("starts_on", sa.Date()),
        sa.Column("ends_on", sa.Date()),
        sa.Column("status", sa.String(20), nullable=False, server_default="PLANNED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("school_id", "year", name="uq_academic_years_school_year"),
        sa.UniqueConstraint(
            "school_id", "external_id", name="uq_academic_years_school_external_id"
        ),
        sa.UniqueConstraint("school_id", "id", name="uq_academic_years_school_id_id"),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'ACTIVE', 'CLOSED')", name="ck_academic_years_status"
        ),
    )
    op.create_index("ix_academic_years_school_id", "academic_years", ["school_id"])

    op.create_table(
        "school_units",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "school_id", "external_id", name="uq_school_units_school_external_id"
        ),
        sa.UniqueConstraint("school_id", "id", name="uq_school_units_school_id_id"),
    )
    op.create_index("ix_school_units_school_id", "school_units", ["school_id"])

    op.create_table(
        "segments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("school_id", "external_id", name="uq_segments_school_external_id"),
        sa.UniqueConstraint("school_id", "id", name="uq_segments_school_id_id"),
    )
    op.create_index("ix_segments_school_id", "segments", ["school_id"])

    op.create_table(
        "grade_levels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("segment_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["school_id", "segment_id"],
            ["segments.school_id", "segments.id"],
            ondelete="RESTRICT",
            name="fk_grade_levels_school_segment",
        ),
        sa.UniqueConstraint(
            "school_id", "external_id", name="uq_grade_levels_school_external_id"
        ),
        sa.UniqueConstraint("school_id", "id", name="uq_grade_levels_school_id_id"),
    )
    op.create_index("ix_grade_levels_segment_id", "grade_levels", ["segment_id"])
    op.create_index("ix_grade_levels_school_id", "grade_levels", ["school_id"])

    op.create_table(
        "classes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("academic_year_id", sa.Uuid(), nullable=False),
        sa.Column("grade_level_id", sa.Uuid(), nullable=False),
        sa.Column("school_unit_id", sa.Uuid()),
        sa.Column("external_id", sa.String(255)),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id", "academic_year_id"],
            ["academic_years.school_id", "academic_years.id"],
            ondelete="RESTRICT",
            name="fk_classes_school_academic_year",
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "grade_level_id"],
            ["grade_levels.school_id", "grade_levels.id"],
            ondelete="RESTRICT",
            name="fk_classes_school_grade_level",
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "school_unit_id"],
            ["school_units.school_id", "school_units.id"],
            ondelete="RESTRICT",
            name="fk_classes_school_unit",
        ),
        sa.UniqueConstraint(
            "academic_year_id", "grade_level_id", "name", name="uq_classes_year_grade_name"
        ),
        sa.UniqueConstraint("school_id", "external_id", name="uq_classes_school_external_id"),
        sa.UniqueConstraint("school_id", "id", name="uq_classes_school_id_id"),
    )
    op.create_index("ix_classes_academic_year_id", "classes", ["academic_year_id"])
    op.create_index("ix_classes_grade_level_id", "classes", ["grade_level_id"])
    op.create_index("ix_classes_school_id", "classes", ["school_id"])


def downgrade() -> None:
    op.drop_table("classes")
    op.drop_table("grade_levels")
    op.drop_table("segments")
    op.drop_table("school_units")
    op.drop_table("academic_years")
