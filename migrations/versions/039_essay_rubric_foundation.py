"""R1 - ENEM essay rubric foundation.

Revision ID: 039_essay_rubric_foundation
Revises: 038_authorial_classification

Purely additive: creates five new tables, touches zero rows in any existing
table, and alters no existing column, constraint or index. Fully reversible.

Audit of reuse (spec §5): no table in this schema stores rubric text or scoring
levels. ``pedagogical_classifications`` carries model/prompt versioning for
QUESTION classification and is unrelated to essay scoring; reusing it would
overload a table that already has a distinct lifecycle. Nothing is added to it.
"""

from alembic import op
import sqlalchemy as sa

revision = "039_essay_rubric_foundation"
down_revision = "038_authorial_classification"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "essay_rubrics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rubric_version", sa.String(50), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("effective_year", sa.Integer()),
        sa.Column("max_total_points", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("official_source_title", sa.Text()),
        sa.Column("official_source_url", sa.Text()),
        sa.Column("official_source_sha256", sa.String(64)),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", _JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rubric_version", name="uq_essay_rubrics_version"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED')", name="ck_essay_rubrics_status"
        ),
    )
    op.create_index("ix_essay_rubrics_status", "essay_rubrics", ["status"])

    op.create_table(
        "essay_rubric_competencies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rubric_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(4), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("official_title", sa.Text(), nullable=False),
        sa.Column("max_points", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("source_page", sa.Integer()),
        sa.ForeignKeyConstraint(["rubric_id"], ["essay_rubrics.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("rubric_id", "code", name="uq_essay_rubric_competencies_code"),
        sa.CheckConstraint(
            "code IN ('C1', 'C2', 'C3', 'C4', 'C5')",
            name="ck_essay_rubric_competencies_code",
        ),
    )
    op.create_index(
        "ix_essay_rubric_competencies_rubric_id", "essay_rubric_competencies", ["rubric_id"]
    )

    op.create_table(
        "essay_rubric_levels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("competency_id", sa.Uuid(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("descriptor", sa.Text(), nullable=False),
        sa.Column("source_page", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.String(30), nullable=False, server_default="OFICIAL_INEP"),
        sa.ForeignKeyConstraint(
            ["competency_id"], ["essay_rubric_competencies.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("competency_id", "points", name="uq_essay_rubric_levels_points"),
        sa.CheckConstraint(
            "points IN (0, 40, 80, 120, 160, 200)", name="ck_essay_rubric_levels_points"
        ),
    )
    op.create_index(
        "ix_essay_rubric_levels_competency_id", "essay_rubric_levels", ["competency_id"]
    )

    op.create_table(
        "essay_rubric_signals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("competency_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("provenance", sa.String(30), nullable=False),
        sa.Column("source_ref", sa.Text()),
        sa.Column("rationale", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(
            ["competency_id"], ["essay_rubric_competencies.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("competency_id", "key", name="uq_essay_rubric_signals_key"),
        sa.CheckConstraint(
            "provenance IN ('OFICIAL_INEP', 'INTERPRETACAO_PEDAGOGICA', 'HEURISTICA_MOTOR')",
            name="ck_essay_rubric_signals_provenance",
        ),
        sa.CheckConstraint(
            "provenance = 'HEURISTICA_MOTOR' OR source_ref IS NOT NULL",
            name="ck_essay_rubric_signals_source_ref",
        ),
        sa.CheckConstraint(
            "provenance <> 'HEURISTICA_MOTOR' OR rationale IS NOT NULL",
            name="ck_essay_rubric_signals_rationale",
        ),
    )
    op.create_index(
        "ix_essay_rubric_signals_competency_id", "essay_rubric_signals", ["competency_id"]
    )

    op.create_table(
        "essay_rubric_zero_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rubric_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("effect", sa.String(30), nullable=False),
        sa.Column("competency_code", sa.String(4)),
        sa.Column("source_page", sa.Integer()),
        sa.Column("provenance", sa.String(30), nullable=False, server_default="OFICIAL_INEP"),
        sa.ForeignKeyConstraint(["rubric_id"], ["essay_rubrics.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("rubric_id", "key", name="uq_essay_rubric_zero_rules_key"),
        sa.CheckConstraint(
            "effect IN ('ANULA_REDACAO', 'ZERA_COMPETENCIA')",
            name="ck_essay_rubric_zero_rules_effect",
        ),
        sa.CheckConstraint(
            "competency_code IS NULL OR competency_code IN ('C1', 'C2', 'C3', 'C4', 'C5')",
            name="ck_essay_rubric_zero_rules_competency_code",
        ),
    )
    op.create_index(
        "ix_essay_rubric_zero_rules_rubric_id", "essay_rubric_zero_rules", ["rubric_id"]
    )


def downgrade() -> None:
    op.drop_table("essay_rubric_zero_rules")
    op.drop_table("essay_rubric_signals")
    op.drop_table("essay_rubric_levels")
    op.drop_table("essay_rubric_competencies")
    op.drop_table("essay_rubrics")
