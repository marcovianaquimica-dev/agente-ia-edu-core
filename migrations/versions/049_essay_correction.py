"""R3 - essay correction foundation.

Revision ID: 049_essay_correction
Revises: 048_essay_proposal_submission

Purely additive: one new table, touches zero rows in any existing table.
"""

from alembic import op
import sqlalchemy as sa

revision = "049_essay_correction"
down_revision = "048_essay_proposal_submission"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "essay_corrections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("essay_submission_id", sa.Uuid(), nullable=False),
        sa.Column("correction_key", sa.String(64)),
        sa.Column("rubric_version", sa.String(50), nullable=False),
        sa.Column("model_version", sa.String(100)),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("engine_version", sa.String(50), nullable=False),
        sa.Column("ai_output", _JSON),
        sa.Column("final_scores", _JSON),
        sa.Column("final_feedback", _JSON),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING_REVIEW"),
        sa.Column("failure_reason", sa.Text()),
        sa.Column("reviewed_by_external_identity", sa.String(255)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["essay_submission_id"], ["essay_submissions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "essay_submission_id", name="uq_essay_corrections_submission"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_REVIEW', 'NEEDS_REVIEW', 'APPROVED', 'REJECTED')",
            name="ck_essay_corrections_status",
        ),
        sa.CheckConstraint(
            "(status = 'APPROVED') = (published_at IS NOT NULL)",
            name="ck_essay_corrections_approved_has_published_at",
        ),
        sa.CheckConstraint(
            "(status IN ('APPROVED', 'REJECTED')) = (reviewed_at IS NOT NULL)",
            name="ck_essay_corrections_terminal_has_reviewed_at",
        ),
        sa.CheckConstraint(
            "status = 'NEEDS_REVIEW' OR failure_reason IS NULL",
            name="ck_essay_corrections_failure_reason_requires_needs_review",
        ),
        sa.CheckConstraint(
            "status = 'NEEDS_REVIEW' OR ai_output IS NOT NULL",
            name="ck_essay_corrections_non_failed_has_ai_output",
        ),
        sa.CheckConstraint(
            "(ai_output IS NULL OR ai_output = 'null') OR (correction_key IS NOT NULL AND model_version IS NOT NULL)",
            name="ck_essay_corrections_success_has_key_and_model",
        ),
    )
    op.create_index("ix_essay_corrections_school_id", "essay_corrections", ["school_id"])
    op.create_index("ix_essay_corrections_status", "essay_corrections", ["status"])


def downgrade() -> None:
    op.drop_table("essay_corrections")
