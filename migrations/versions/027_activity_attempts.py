"""PHASE 17 - activity_attempts + activity_answers: a student's execution of a
distributed activity (PHASE 16). Pure execution state - NO correction, score,
percentage, ranking or pedagogical result (those are later phases).

Revision ID: 027_activity_attempts
Revises: 026_activity_assignments

Separation of concerns:
    Assessment / AssessmentVersion / AssessmentItem  = list content (PHASE 15)
    activity_assignments                             = distribution (PHASE 16)
    activity_attempts / activity_answers             = execution     (this phase)
    correction / result / domain map                 = FUTURE - not here

Why not reuse ``assessment_attempts`` / ``assessment_answers``:
those rows are bound to an ``assessment_publications`` row (NOT NULL FK) - PHASE 16
deliberately distributes without a publication - carry a correction-shaped status
set (``submitted``/``expired``) plus score / max_score / correct_answers /
points_awarded / correction_status / is_correct / corrected_at columns this phase
must never populate, and their ``assignment_id`` FK points at the legacy
``assessment_assignments`` table, not ``activity_assignments``. They are not an
equivalent structure. This is one minimal pair of tables; nothing existing is
altered.

``activity_attempts`` is intentionally generic (an ``assignment`` anchor + a
lifecycle) so a future correction engine, simulado or diagnostic can build on it
without a schema change.

No official questions/versions/options/booklet/classification/catalog table is
touched.
"""

from alembic import op
import sqlalchemy as sa

revision = "027_activity_attempts"
down_revision = "026_activity_assignments"
branch_labels = None
depends_on = None

ATTEMPTS = "activity_attempts"
ANSWERS = "activity_answers"


def upgrade() -> None:
    op.create_table(
        ATTEMPTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("student_external_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("current_position", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["assignment_id"], ["activity_assignments.id"],
                                ondelete="RESTRICT", name="fk_activity_attempts_assignment"),
        sa.CheckConstraint("status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED')",
                           name="ck_activity_attempts_status"),
        sa.CheckConstraint("current_position IS NULL OR current_position >= 0",
                           name="ck_activity_attempts_current_position_nonneg"),
        # one execution per (assignment, student) in this phase - no multiple attempts
        sa.UniqueConstraint("assignment_id", "student_external_id",
                            name="uq_activity_attempts_assignment_student"),
    )
    op.create_index("ix_activity_attempts_assignment_id", ATTEMPTS, ["assignment_id"])
    op.create_index("ix_activity_attempts_student", ATTEMPTS, ["student_external_id"])
    op.create_index("ix_activity_attempts_status", ATTEMPTS, ["status"])

    op.create_table(
        ANSWERS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("question_version_id", sa.Uuid(), nullable=False),
        sa.Column("selected_option_id", sa.Uuid(), nullable=True),
        sa.Column("selected_option_key", sa.String(length=8), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["attempt_id"], ["activity_attempts.id"],
                                ondelete="CASCADE", name="fk_activity_answers_attempt"),
        sa.ForeignKeyConstraint(["question_version_id"], ["question_versions.id"],
                                ondelete="RESTRICT", name="fk_activity_answers_question_version"),
        sa.ForeignKeyConstraint(["selected_option_id"], ["question_options.id"],
                                ondelete="RESTRICT", name="fk_activity_answers_option"),
        # the current answer for a question in an attempt is UNIQUE - changing the
        # choice UPDATEs this row, it never inserts a second one.
        sa.UniqueConstraint("attempt_id", "question_version_id",
                            name="uq_activity_answers_attempt_question"),
    )
    op.create_index("ix_activity_answers_attempt_id", ANSWERS, ["attempt_id"])


def downgrade() -> None:
    op.drop_index("ix_activity_answers_attempt_id", table_name=ANSWERS)
    op.drop_table(ANSWERS)
    op.drop_index("ix_activity_attempts_status", table_name=ATTEMPTS)
    op.drop_index("ix_activity_attempts_student", table_name=ATTEMPTS)
    op.drop_index("ix_activity_attempts_assignment_id", table_name=ATTEMPTS)
    op.drop_table(ATTEMPTS)
