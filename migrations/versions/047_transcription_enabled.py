"""R2 - school_settings gains transcription_enabled.

Revision ID: 047_transcription_enabled
Revises: 047_question_resolution_capture

Purely additive: one nullable-never column on an existing table, backfilled
via server_default so every existing row gets the conservative default
(disabled) without a data migration step.

Chained after 047_question_resolution_capture instead of 046 directly: main
gained that migration (a different phase, unrelated tables - extracted_
questions/question_versions) while this R2 branch was being built in
parallel, so both files declared down_revision = 046, leaving the chain with
two heads. The two migrations are independent, so the join order is
arbitrary; stacking this branch on top of main's already-landed migration is
the least surprising fix and keeps the revision ids as originally written -
same resolution as the 039_essay_rubric_foundation / 039_student_mastery_
catalog_fk collision earlier in this project's history.
"""

from alembic import op
import sqlalchemy as sa

revision = "047_transcription_enabled"
down_revision = "047_question_resolution_capture"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "school_settings",
        sa.Column(
            "transcription_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("school_settings", "transcription_enabled")
