"""Create pedagogical classifier domain (Phase 4).

Revision ID: 007_pedagogical_classifier
Revises: 006_ingestion_engine
Create Date: 2026-08-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "007_pedagogical_classifier"
down_revision: Union[str, None] = "006_ingestion_engine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pedagogical_classifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("discipline", sa.String(length=255), nullable=False),
        sa.Column("content", sa.String(length=255), nullable=False),
        sa.Column("subcontent", sa.String(length=255), nullable=False),
        sa.Column("difficulty", sa.String(length=30), nullable=False),
        sa.Column("classification_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("difficulty_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("reasoning_type", sa.String(length=100), nullable=False),
        sa.Column("prerequisites", postgresql.JSONB(), nullable=True),
        sa.Column("keywords", postgresql.JSONB(), nullable=True),
        sa.Column("competencies", postgresql.JSONB(), nullable=True),
        sa.Column("skills", postgresql.JSONB(), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("model_version", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("provider_name", sa.String(length=100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="ai"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "status IN ('CLASSIFIED', 'NEEDS_REVIEW', 'DRAFT')",
            name="ck_pedagogical_classifications_status",
        ),
        sa.CheckConstraint(
            "classification_confidence IS NULL OR classification_confidence BETWEEN 0 AND 1",
            name="ck_pedagogical_classifications_confidence_range",
        ),
        sa.CheckConstraint(
            "difficulty_confidence IS NULL OR difficulty_confidence BETWEEN 0 AND 1",
            name="ck_pedagogical_classifications_difficulty_confidence_range",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["question_versions.id"],
            name="fk_pedagogical_classifications_question_version_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pedagogical_classifications"),
    )
    op.create_index(
        "ix_pedagogical_classifications_question_version_id",
        "pedagogical_classifications",
        ["question_version_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pedagogical_classifications_question_version_id",
        table_name="pedagogical_classifications",
    )
    op.drop_table("pedagogical_classifications")
