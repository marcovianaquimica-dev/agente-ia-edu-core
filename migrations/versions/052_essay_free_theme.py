"""R2 - "tema livre": a student-chosen essay theme.

Revision ID: 052_essay_free_theme
Revises: 051_extracted_option_correct

Purely additive: two new nullable/defaulted columns, touches zero existing
rows' meaning. essay_prompts.is_free_theme defaults to false, so every
already-seeded prompt keeps behaving exactly as it did (a fixed statement,
no special pinning/coloring on the student's list). essay_submissions.
student_declared_theme stays null for every submission against a normal
(non-free-theme) prompt - only a free-theme submission ever sets it, and
only at creation time (see services/essay_submission.py).
"""

from alembic import op
import sqlalchemy as sa

revision = "052_essay_free_theme"
down_revision = "051_extracted_option_correct"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "essay_prompts",
        sa.Column(
            "is_free_theme", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "essay_submissions",
        sa.Column("student_declared_theme", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("essay_submissions", "student_declared_theme")
    op.drop_column("essay_prompts", "is_free_theme")
