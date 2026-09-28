"""Real LLM token usage per essay correction / OCR page.

Revision ID: 053_essay_token_usage
Revises: 052_essay_free_theme

Purely additive: four new nullable Integer columns, touches zero existing
rows' meaning. essay_corrections.input_tokens/output_tokens capture the AI
call that produced (or last attempted) that row's ai_output - see _run_ai in
services/essay_correction.py. essay_submission_pages.input_tokens/
output_tokens capture the sum of every transcribe_page call a page actually
triggered (retries/reconciliation included) - see _ocr_page in
services/essay_submission.py. Every already-existing row simply gets NULL in
these four columns, since the historical usage for those calls was never
captured.
"""

from alembic import op
import sqlalchemy as sa

revision = "053_essay_token_usage"
down_revision = "052_essay_free_theme"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "essay_corrections",
        sa.Column("input_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "essay_corrections",
        sa.Column("output_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "essay_submission_pages",
        sa.Column("input_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "essay_submission_pages",
        sa.Column("output_tokens", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("essay_submission_pages", "output_tokens")
    op.drop_column("essay_submission_pages", "input_tokens")
    op.drop_column("essay_corrections", "output_tokens")
    op.drop_column("essay_corrections", "input_tokens")
