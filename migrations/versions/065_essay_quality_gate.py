"""Fase B do Quality Gate - novo campo quality_gate_status em essay_corrections,
e a versao do Quality Gate que produziu a decisao (spec 2026-10-06, Fase B)."""
from alembic import op
import sqlalchemy as sa

revision = "065_essay_quality_gate"
down_revision = "064_essay_batch_extracted_text"


def upgrade() -> None:
    op.add_column("essay_corrections", sa.Column("quality_gate_status", sa.String(30), nullable=True))
    op.add_column("essay_corrections", sa.Column("quality_gate_version", sa.String(50), nullable=True))
    op.create_check_constraint(
        "ck_essay_corrections_quality_gate_status",
        "essay_corrections",
        "quality_gate_status IS NULL OR quality_gate_status IN "
        "('RELIABLE', 'USABLE_WITH_WARNING', 'UNRELIABLE_NEEDS_REVIEW')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_essay_corrections_quality_gate_status", "essay_corrections", type_="check")
    op.drop_column("essay_corrections", "quality_gate_version")
    op.drop_column("essay_corrections", "quality_gate_status")
