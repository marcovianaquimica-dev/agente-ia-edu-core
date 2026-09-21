"""R2 - school_settings gains transcription_enabled.

Revision ID: 047_transcription_enabled
Revises: 046_practice_sessions_catalog_fk

Purely additive: one nullable-never column on an existing table, backfilled
via server_default so every existing row gets the conservative default
(disabled) without a data migration step.
"""

from alembic import op
import sqlalchemy as sa

revision = "047_transcription_enabled"
down_revision = "046_practice_sessions_catalog_fk"
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
