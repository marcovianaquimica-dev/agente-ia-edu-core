"""Create pedagogical taxonomies and estimates.

Revision ID: 002_pedagogical_intelligence
Revises: 001_core_official
Create Date: 2026-08-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "002_pedagogical_intelligence"
down_revision: Union[str, None] = "001_core_official"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "taxonomies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_taxonomies"),
        sa.UniqueConstraint(
            "code",
            "version",
            name="uq_taxonomies_code_version",
        ),
    )

    op.create_table(
        "taxonomy_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taxonomy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("node_type", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_taxonomy_nodes_parent_not_self",
        ),
        sa.CheckConstraint(
            "node_type IN ('competency', 'skill', 'subject', 'subsubject')",
            name="ck_taxonomy_nodes_node_type",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["taxonomy_nodes.id"],
            name="fk_tn_parent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["taxonomy_id"],
            ["taxonomies.id"],
            name="fk_tn_taxonomy",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_taxonomy_nodes"),
        sa.UniqueConstraint(
            "taxonomy_id",
            "code",
            name="uq_taxonomy_nodes_taxonomy_code",
        ),
        sa.UniqueConstraint(
            "taxonomy_id",
            "id",
            name="uq_taxonomy_nodes_taxonomy_id_id",
        ),
    )

    op.create_table(
        "question_classifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taxonomy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("competency_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("skill_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("classifier_version", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'superseded')",
            name="ck_question_classifications_status",
        ),
        sa.CheckConstraint(
            "source IN ('ai', 'human', 'hybrid', 'rule')",
            name="ck_question_classifications_source",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_question_classifications_confidence_range",
        ),
        sa.CheckConstraint(
            "NOT is_primary OR competency_node_id IS NOT NULL",
            name="ck_question_classifications_primary_competency_required",
        ),
        sa.CheckConstraint(
            "NOT is_primary OR skill_node_id IS NOT NULL",
            name="ck_question_classifications_primary_skill_required",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["question_versions.id"],
            name="fk_qc_question_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["question_classifications.id"],
            name="fk_qc_supersedes",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["taxonomy_id"],
            ["taxonomies.id"],
            name="fk_qc_taxonomy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["taxonomy_id", "competency_node_id"],
            ["taxonomy_nodes.taxonomy_id", "taxonomy_nodes.id"],
            name="fk_qc_competency_node",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["taxonomy_id", "skill_node_id"],
            ["taxonomy_nodes.taxonomy_id", "taxonomy_nodes.id"],
            name="fk_qc_skill_node",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_question_classifications"),
    )
    op.create_index(
        "uq_question_classifications_active_primary",
        "question_classifications",
        ["question_version_id", "taxonomy_id"],
        unique=True,
        postgresql_where=sa.text("is_primary = true AND status = 'active'"),
    )

    op.create_table(
        "difficulty_estimates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("band", sa.String(length=20), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("method", sa.String(length=100), nullable=False),
        sa.Column("method_version", sa.String(length=100), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "score BETWEEN 0 AND 100",
            name="ck_difficulty_estimates_score_range",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_difficulty_estimates_confidence_range",
        ),
        sa.CheckConstraint(
            "sample_size IS NULL OR sample_size >= 0",
            name="ck_difficulty_estimates_sample_size_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'superseded')",
            name="ck_difficulty_estimates_status",
        ),
        sa.CheckConstraint(
            "source IN ('ai', 'human', 'empirical', 'heuristic')",
            name="ck_difficulty_estimates_source",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["question_versions.id"],
            name="fk_de_question_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["difficulty_estimates.id"],
            name="fk_de_supersedes",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_difficulty_estimates"),
    )
    op.create_index(
        "uq_difficulty_estimates_active",
        "difficulty_estimates",
        ["question_version_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_difficulty_estimates_question_version_id",
        "difficulty_estimates",
        ["question_version_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_difficulty_estimates_question_version_id",
        table_name="difficulty_estimates",
    )
    op.drop_index(
        "uq_difficulty_estimates_active",
        table_name="difficulty_estimates",
    )
    op.drop_table("difficulty_estimates")
    op.drop_index(
        "uq_question_classifications_active_primary",
        table_name="question_classifications",
    )
    op.drop_table("question_classifications")
    op.drop_table("taxonomy_nodes")
    op.drop_table("taxonomies")
