"""PHASE 24 - Study Session / Momento de Aprendizado.

Revision ID: 031_study_sessions
Revises: 030_material_content_foundation

ONE new table. The Study Session is an ORCHESTRATION layer over PHASE 20-23 - it
recreates none of them. It is persisted (not derived) for exactly two reasons the
spec makes mandatory:

  * s16 "Retomada" - a student who closes the browser must recover the session,
    the current block, completed blocks and progress;
  * s24 idempotency - a refresh / retry must not create a second session, and a
    PRACTICE block must not create a second AdaptivePractice activity (the
    practice_id is pinned onto the block inside `plan`).

The generated ordered plan and the coordination break configuration live as JSON
on the row (blocks + breaks) - no second table is needed. NOTHING here touches
questions / question_versions / question_options / answer_key_* / catalog_nodes /
pedagogical_classifications / activity_* / domain_content_mastery / theory_* .
Fully reversible.
"""

from alembic import op
import sqlalchemy as sa

revision = "031_study_sessions"
down_revision = "030_material_content_foundation"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "study_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("student_external_id", sa.String(length=255), nullable=False),
        # STUDENT_DEFINED | SCHOOL_DEFINED
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=True),
        sa.Column("scope_type", sa.String(length=20), nullable=True),        # CLASSROOM | STUDENT
        sa.Column("scope_external_id", sa.String(length=255), nullable=True),
        sa.Column("created_by_external_id", sa.String(length=255), nullable=True),
        sa.Column("session_date", sa.String(length=10), nullable=False),     # ISO YYYY-MM-DD
        sa.Column("timer_mode", sa.String(length=20), nullable=False, server_default="TIMED"),  # TIMED | UNTIMED
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_minutes", sa.Integer(), nullable=True),
        sa.Column("break_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("effective_study_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target_content_codes", _JSON, nullable=True),
        sa.Column("config", _JSON, nullable=True),
        sa.Column("plan", _JSON, nullable=True),
        # SCHEDULED | READY | IN_PROGRESS | COMPLETED | CANCELLED
        sa.Column("status", sa.String(length=20), nullable=False, server_default="SCHEDULED"),
        sa.Column("current_block_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", _JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("source IN ('STUDENT_DEFINED', 'SCHOOL_DEFINED')",
                           name="ck_study_sessions_source"),
        sa.CheckConstraint(
            "status IN ('SCHEDULED', 'READY', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
            name="ck_study_sessions_status"),
        sa.CheckConstraint("timer_mode IN ('TIMED', 'UNTIMED')", name="ck_study_sessions_timer_mode"),
    )
    op.create_index("ix_study_sessions_student_date", "study_sessions",
                    ["student_external_id", "session_date"])
    op.create_index("ix_study_sessions_school_scope_date", "study_sessions",
                    ["school_id", "scope_external_id", "session_date"])
    op.create_index("ix_study_sessions_status", "study_sessions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_study_sessions_status", table_name="study_sessions")
    op.drop_index("ix_study_sessions_school_scope_date", table_name="study_sessions")
    op.drop_index("ix_study_sessions_student_date", table_name="study_sessions")
    op.drop_table("study_sessions")
