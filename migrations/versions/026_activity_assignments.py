"""PHASE 16 - activity_assignments: distribute a PUBLISHED question list to recipients.

Revision ID: 026_activity_assignments
Revises: 025_classification_lifecycle

Separation of concerns (PHASE 16 principle):
    Assessment / AssessmentVersion / AssessmentItem  = list content (PHASE 15)
    activity_assignments                              = DISTRIBUTION (this phase)
    attempts / responses                             = execution (PHASE 17, NOT here)

Why a new small table rather than reusing ``assessment_assignments``:
``assessment_assignments`` is attempt-flavoured (status PENDING/IN_PROGRESS/
COMPLETED/CANCELLED), is tied to an ``assessment_publications`` row and carries
no ``assessment_version_id`` or availability window. PHASE 16 needs
ACTIVE/CLOSED/CANCELLED + available_from/due_at + a hard reference to the
published ``assessment_version_id`` + a frozen content fingerprint. This is one
minimal table; nothing existing is altered.

The canonical activity identity is ``(assessment_id, assessment_version_id)``.
``selection_fingerprint`` / ``question_count`` / ``answer_key_presentation`` are
frozen snapshots so a distribution can be shown to a student without ever
rebuilding the list from a mutable selection.

Recipients are referenced institutionally (target_type STUDENT | CLASS +
target_id) - students are NEVER copied into the row. GRADE distribution is
intentionally not enabled: no grade -> roster relation exists in the current
schema (only ``user_school_links`` scope rows), so it cannot be resolved
safely; it is a documented future integration point.

No official questions/versions/options/classifications/catalog table is touched.
"""

from alembic import op
import sqlalchemy as sa

revision = "026_activity_assignments"
down_revision = "025_classification_lifecycle"
branch_labels = None
depends_on = None

TABLE = "activity_assignments"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_version_id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_external_id", sa.String(length=255), nullable=True),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=False),
        sa.Column("available_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("selection_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("answer_key_presentation", sa.String(length=40), nullable=True),
        sa.Column("metadata", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="RESTRICT",
                                name="fk_activity_assignments_assessment"),
        sa.ForeignKeyConstraint(["assessment_version_id"], ["assessment_versions.id"],
                                ondelete="RESTRICT", name="fk_activity_assignments_version"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_activity_assignments_school"),
        sa.CheckConstraint("target_type IN ('STUDENT', 'CLASS')",
                           name="ck_activity_assignments_target_type"),
        sa.CheckConstraint("status IN ('ACTIVE', 'CLOSED', 'CANCELLED')",
                           name="ck_activity_assignments_status"),
        sa.CheckConstraint("question_count >= 0",
                           name="ck_activity_assignments_question_count_nonnegative"),
        sa.CheckConstraint("due_at IS NULL OR available_from IS NULL OR due_at >= available_from",
                           name="ck_activity_assignments_window_order"),
    )
    op.create_index("ix_activity_assignments_school_id", TABLE, ["school_id"])
    op.create_index("ix_activity_assignments_assessment_id", TABLE, ["assessment_id"])
    op.create_index("ix_activity_assignments_version_id", TABLE, ["assessment_version_id"])
    op.create_index("ix_activity_assignments_target", TABLE, ["target_type", "target_id"])
    op.create_index("ix_activity_assignments_status", TABLE, ["status"])
    op.create_index("ix_activity_assignments_available_from", TABLE, ["available_from"])
    op.create_index("ix_activity_assignments_due_at", TABLE, ["due_at"])
    # duplicate control (PHASE 16 s15): at most one ACTIVE distribution of a
    # given published version to the same recipient.
    op.create_index(
        "uq_activity_assignments_active_target", TABLE,
        ["assessment_version_id", "target_type", "target_id"],
        unique=True, postgresql_where=sa.text("status = 'ACTIVE'"),
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    for ix in ("uq_activity_assignments_active_target",
               "ix_activity_assignments_due_at", "ix_activity_assignments_available_from",
               "ix_activity_assignments_status", "ix_activity_assignments_target",
               "ix_activity_assignments_version_id", "ix_activity_assignments_assessment_id",
               "ix_activity_assignments_school_id"):
        op.drop_index(ix, table_name=TABLE)
    op.drop_table(TABLE)
