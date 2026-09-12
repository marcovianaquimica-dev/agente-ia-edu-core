"""PHASE 18 - activity_results + activity_result_items: deterministic correction
of a COMPLETED activity attempt against the FROZEN answer key.

Revision ID: 028_activity_results
Revises: 027_activity_attempts

Separation of concerns:
    list content   = Assessment / AssessmentVersion / AssessmentItem   (PHASE 15)
    distribution   = ActivityAssignment                                (PHASE 16)
    execution      = ActivityAttempt + ActivityAnswer                  (PHASE 17)
    correction     = ActivityResult + ActivityResultItem               (this phase)
    domain map / trilha / TRI / notas                                  = FUTURE - NOT here

The correction is 100% deterministic: for every frozen AssessmentItem,
``ActivityAnswer.selected_option`` is compared to ``AssessmentItem.frozen_correct_option``
(the official key snapshotted at list-publication time). No LLM, no inference
from text, no pedagogical_classification, no official-table write.

``activity_results.attempt_id`` is UNIQUE -> a COMPLETED attempt has exactly one
result; re-running correction is an idempotent no-op that returns the existing
row. ``activity_result_items`` keeps the per-question outcome (selected key,
correct key, is_correct, answered, position, official_number) so a later phase
can analyse by content / discipline / difficulty without re-correcting and
without coupling the raw result to any taxonomy.

No official questions/versions/options/booklet/answer-key/classification/catalog
row is touched.
"""

from alembic import op
import sqlalchemy as sa

revision = "028_activity_results"
down_revision = "027_activity_attempts"
branch_labels = None
depends_on = None

RESULTS = "activity_results"
ITEMS = "activity_result_items"


def upgrade() -> None:
    op.create_table(
        RESULTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("student_external_id", sa.String(length=255), nullable=False),
        sa.Column("assessment_version_id", sa.Uuid(), nullable=False),
        sa.Column("selection_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("answered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("incorrect_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unanswered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_status", sa.String(length=20), nullable=False, server_default="COMPLETED"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["attempt_id"], ["activity_attempts.id"],
                                ondelete="RESTRICT", name="fk_activity_results_attempt"),
        sa.ForeignKeyConstraint(["assignment_id"], ["activity_assignments.id"],
                                ondelete="RESTRICT", name="fk_activity_results_assignment"),
        sa.ForeignKeyConstraint(["assessment_version_id"], ["assessment_versions.id"],
                                ondelete="RESTRICT", name="fk_activity_results_version"),
        # one result per attempt - correction is idempotent
        sa.UniqueConstraint("attempt_id", name="uq_activity_results_attempt"),
        sa.CheckConstraint(
            "correct_count >= 0 AND incorrect_count >= 0 AND unanswered_count >= 0 "
            "AND answered_count >= 0 AND question_count >= 0",
            name="ck_activity_results_counts_nonneg"),
        sa.CheckConstraint(
            "correct_count + incorrect_count + unanswered_count = question_count",
            name="ck_activity_results_counts_partition"),
    )
    op.create_index("ix_activity_results_assignment_id", RESULTS, ["assignment_id"])
    op.create_index("ix_activity_results_student", RESULTS, ["student_external_id"])
    op.create_index("ix_activity_results_version_id", RESULTS, ["assessment_version_id"])

    op.create_table(
        ITEMS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("result_id", sa.Uuid(), nullable=False),
        sa.Column("question_version_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("official_number", sa.Integer(), nullable=True),
        sa.Column("selected_option_key", sa.String(length=8), nullable=True),
        sa.Column("correct_option_key", sa.String(length=8), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("answered", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["result_id"], ["activity_results.id"],
                                ondelete="CASCADE", name="fk_activity_result_items_result"),
        sa.ForeignKeyConstraint(["question_version_id"], ["question_versions.id"],
                                ondelete="RESTRICT", name="fk_activity_result_items_qv"),
        sa.UniqueConstraint("result_id", "question_version_id",
                            name="uq_activity_result_items_result_qv"),
        sa.UniqueConstraint("result_id", "position",
                            name="uq_activity_result_items_result_position"),
        sa.CheckConstraint("position >= 1", name="ck_activity_result_items_position_positive"),
    )
    op.create_index("ix_activity_result_items_result_id", ITEMS, ["result_id"])


def downgrade() -> None:
    op.drop_index("ix_activity_result_items_result_id", table_name=ITEMS)
    op.drop_table(ITEMS)
    op.drop_index("ix_activity_results_version_id", table_name=RESULTS)
    op.drop_index("ix_activity_results_student", table_name=RESULTS)
    op.drop_index("ix_activity_results_assignment_id", table_name=RESULTS)
    op.drop_table(RESULTS)
