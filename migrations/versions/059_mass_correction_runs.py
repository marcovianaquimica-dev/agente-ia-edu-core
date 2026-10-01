"""Acompanhamento de lotes de correcao em massa via Batch API.

Revision ID: 059_mass_correction_runs
Revises: 058_platform_essay_prompts

Puramente aditiva: uma tabela nova, nao mexe em nenhuma tabela existente.
Guarda so o progresso junto a OpenAI (id do lote, status, arquivos de
entrada/saida) - os dados em si continuam em essay_batch_pages/
essay_submissions/essay_corrections, ja existentes.
"""

from alembic import op
import sqlalchemy as sa

revision = "059_mass_correction_runs"
down_revision = "058_platform_essay_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mass_correction_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("openai_batch_id", sa.String(length=64), nullable=True),
        sa.Column("input_file_id", sa.String(length=64), nullable=True),
        sa.Column("output_file_id", sa.String(length=64), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_mass_correction_runs"),
        sa.CheckConstraint(
            "stage IN ('OCR', 'CORRECTION', 'SCORING')",
            name="ck_mass_correction_runs_stage",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'validating', 'in_progress', 'finalizing', "
            "'completed', 'failed', 'expired', 'cancelled')",
            name="ck_mass_correction_runs_status",
        ),
        sa.CheckConstraint(
            "request_count >= 0", name="ck_mass_correction_runs_request_count_non_negative"
        ),
    )
    op.create_index(
        "ix_mass_correction_runs_school_id", "mass_correction_runs", ["school_id"]
    )
    op.create_index(
        "ix_mass_correction_runs_stage_status", "mass_correction_runs", ["stage", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_mass_correction_runs_stage_status", table_name="mass_correction_runs")
    op.drop_index("ix_mass_correction_runs_school_id", table_name="mass_correction_runs")
    op.drop_table("mass_correction_runs")
