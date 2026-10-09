"""Fase C - decisao auditavel do Zero Gate em essay_corrections (spec
2026-10-06, Fase C)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "066_essay_zero_gate"
down_revision = "065_essay_quality_gate"


def upgrade() -> None:
    op.add_column("essay_corrections", sa.Column("zero_gate_decision", JSONB, nullable=True))
    op.add_column("essay_corrections", sa.Column("zero_gate_version", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("essay_corrections", "zero_gate_version")
    op.drop_column("essay_corrections", "zero_gate_decision")
