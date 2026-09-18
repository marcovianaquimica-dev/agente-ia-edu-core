"""Question Bank: staged capture + official storage for step-by-step
resolution text.

Purely additive: no existing column dropped or retyped, no data migrated.

``extracted_questions`` gains ``resolution_raw_text`` (verbatim from the
source PDF's own "Resolução"/"Gabarito comentado" section, when the
extraction engine can unambiguously associate it to this question number -
never invented), ``resolution_reviewed_text`` (a human reviewer's edited
version, same discipline as ``reviewed_text`` for the question statement),
and ``resolution_status`` (NONE/PENDING_REVIEW/APPROVED/REJECTED - mirrors
``review_status``'s pattern but tracks the resolution independently, since a
question can be APPROVED with its resolution still PENDING_REVIEW or absent
entirely).

``question_versions`` gains ``resolution_text``, populated only at
publication time from an APPROVED ``resolution_reviewed_text``. Real ENEM
source booklets (var/inep-pilot/*.pdf) carry no resolution content at all -
this column is expected to stay NULL for the entire current official corpus,
and the answer-key view already renders "unavailable" correctly for NULL, so
this is a forward-compatible capture path, not a retrofit of existing data.

Revision ID: 047_question_resolution_capture
Revises: 046_practice_sessions_catalog_fk
Create Date: 2026-09-18
"""

from alembic import op
import sqlalchemy as sa

revision = "047_question_resolution_capture"
down_revision = "046_practice_sessions_catalog_fk"
branch_labels = None
depends_on = None

_RESOLUTION_STATUSES = ("NONE", "PENDING_REVIEW", "APPROVED", "REJECTED")


def upgrade() -> None:
    op.add_column(
        "extracted_questions",
        sa.Column("resolution_raw_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "extracted_questions",
        sa.Column("resolution_reviewed_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "extracted_questions",
        sa.Column(
            "resolution_status",
            sa.String(20),
            nullable=False,
            server_default="NONE",
        ),
    )
    op.create_check_constraint(
        "ck_extracted_questions_resolution_status",
        "extracted_questions",
        f"resolution_status IN {_RESOLUTION_STATUSES!r}",
    )

    op.add_column(
        "question_versions",
        sa.Column("resolution_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("question_versions", "resolution_text")

    op.drop_constraint(
        "ck_extracted_questions_resolution_status",
        "extracted_questions",
        type_="check",
    )
    op.drop_column("extracted_questions", "resolution_status")
    op.drop_column("extracted_questions", "resolution_reviewed_text")
    op.drop_column("extracted_questions", "resolution_raw_text")
