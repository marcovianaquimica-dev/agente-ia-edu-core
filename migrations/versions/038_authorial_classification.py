"""PHASE 30 - Authorial Question Pedagogical Classification Engine.

Revision ID: 038_authorial_classification
Revises: 037_authorial_question_review

Per spec s44 ("verificar se PedagogicalClassification já suporta... se
suportar, REUTILIZAR"): the audit confirmed ``pedagogical_classifications``
already has everything this phase needs to persist a classification -
confidence (0-1, CHECK-constrained), status (CLASSIFIED/NEEDS_REVIEW/DRAFT),
lifecycle (ACTIVE/SUPERSEDED, with the at-most-one-ACTIVE partial unique
index from migration 025), model/prompt/provider version columns, and a
JSONB ``metadata_`` catch-all already used for a ``taxonomy_version`` tag,
review reasons, evidence, and gap diagnostics. NOTHING is added to that
table, and NOTHING is added to ``catalog_nodes`` - no new taxonomy, no
duplicated hierarchy (spec s4/s44).

The ONE genuinely missing piece is a durable, append-only HUMAN audit trail
for review/approval/reclassification actions on a classification (spec
s27/s29: actor, actor_type, timestamp, previous_value, new_value, reason) -
``PedagogicalClassification`` itself has no reviewed_by/reviewed_at columns
(unlike its unused sibling ``QuestionClassification``), and squeezing an
open-ended, ever-growing audit log into its ``metadata_`` JSON would make
that column unbounded and un-indexable. This migration adds exactly ONE
new, additive table for that - the "smallest possible extension" the spec
asks for, not a parallel classification system.

Purely additive and reversible: creates one new table, touches zero
existing rows in any table, and does not alter any existing column,
constraint, or index.
"""

from alembic import op
import sqlalchemy as sa

revision = "038_authorial_classification"
down_revision = "037_authorial_question_review"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "pedagogical_classification_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "pedagogical_classification_id", sa.Uuid(),
            sa.ForeignKey("pedagogical_classifications.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("previous_value", _JSON, nullable=True),
        sa.Column("new_value", _JSON, nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("classifier_version", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('AI_CLASSIFY', 'MANUAL_CLASSIFY', 'APPROVE', 'RECLASSIFY', 'EDIT')",
            name="ck_pedagogical_classification_reviews_action",
        ),
        sa.CheckConstraint(
            "actor_type IN ('AI', 'TEACHER', 'COORDINATOR', 'DIRECTOR', 'PLATFORM_ADMIN', 'SYSTEM')",
            name="ck_pedagogical_classification_reviews_actor_type",
        ),
    )
    op.create_index(
        "ix_pedagogical_classification_reviews_classification_id",
        "pedagogical_classification_reviews", ["pedagogical_classification_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pedagogical_classification_reviews_classification_id",
        table_name="pedagogical_classification_reviews",
    )
    op.drop_table("pedagogical_classification_reviews")
