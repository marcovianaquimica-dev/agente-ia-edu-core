"""PHASE 28 - Question Reconstruction & Review Quality.

Revision ID: 036_question_reconstruction
Revises: 035_question_extraction

FOUR additive, nullable columns on the EXISTING ``extracted_questions``
table (PHASE 27, unchanged otherwise): ``reconstructed_text`` (the LOCAL,
verified column-reconstruction result - raw_text/normalized_text are never
overwritten), ``reconstruction_applied`` (bool, default false),
``review_reasons`` (structured spec-s12 reason codes, replacing a bare
"REVIEW_REQUIRED"), and ``status_history`` (a full audit trail of status
transitions - actor/timestamp/previous-new state - spec s19). No new table
was needed: PHASE 27's staging model already holds everything else this
phase needs. Touches no official table and no PHASE 26 table. Fully
reversible.
"""

from alembic import op
import sqlalchemy as sa

revision = "036_question_reconstruction"
down_revision = "035_question_extraction"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("extracted_questions", sa.Column("reconstructed_text", sa.Text(), nullable=True))
    op.add_column("extracted_questions", sa.Column(
        "reconstruction_applied", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("extracted_questions", sa.Column("review_reasons", _JSON, nullable=True))
    op.add_column("extracted_questions", sa.Column("status_history", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("extracted_questions", "status_history")
    op.drop_column("extracted_questions", "review_reasons")
    op.drop_column("extracted_questions", "reconstruction_applied")
    op.drop_column("extracted_questions", "reconstructed_text")
