"""PHASE 20 - domain_content_mastery: the first PERSISTENT, curriculum-v2,
activity-derived Domain Map grain (ALUNO x TAXONOMY x CONTENT[/SUBCONTENT]).

Revision ID: 029_domain_content_mastery
Revises: 028_activity_results

Separation of concerns:
    correction  = ActivityResult + ActivityResultItem       (PHASE 18, immutable)
    analysis    = PedagogicalAnalysisService                 (PHASE 19, READ-ONLY)
    domain map  = domain_content_mastery                     (this phase, DERIVED cache)
    adaptive learning path / recommendation / TRI            = FUTURE - NOT here

This is a DERIVED, fully recomputable projection of the immutable
ActivityResult / ActivityResultItem history onto the ACTIVE curriculum-v2
catalog. It is NOT a new source of truth: ``rebuild_student_domain`` reconstructs
every row deterministically from the results, so any drift is self-healing.

It is DISTINCT from the legacy ``student_content_mastery`` (taxonomy_nodes,
diagnostic/practice-derived, feeds the "Minha Evolução" screen) - the two
measurement substrates are kept separate on purpose (spec s8/s19: official
activities and individual practice must not be silently merged).

No score / grade / TRI / MIRT / ranking / recommendation / "next action" column:
those belong to later phases. discipline_code / area_code are intentionally NOT
persisted - they are resolved from the catalog at read time. No official
questions / versions / options / answer-key / classification / catalog /
ActivityResult row is written by this phase.
"""

from alembic import op
import sqlalchemy as sa

revision = "029_domain_content_mastery"
down_revision = "028_activity_results"
branch_labels = None
depends_on = None

TABLE = "domain_content_mastery"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("student_external_id", sa.String(length=255), nullable=False),
        sa.Column("taxonomy_version", sa.String(length=40), nullable=False,
                  server_default="curriculum-v2"),
        sa.Column("content_code", sa.String(length=100), nullable=False),
        sa.Column("subcontent_code", sa.String(length=100), nullable=True),
        # observed evidence (answered questions only)
        sa.Column("questions_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("questions_answered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("questions_correct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("questions_incorrect", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accuracy", sa.Numeric(6, 4), nullable=True),          # null when answered == 0
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_state", sa.String(length=30), nullable=False,
                  server_default="INSUFFICIENT_EVIDENCE"),
        # classification-quality breakdown (never hide uncertainty)
        sa.Column("definitive_evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provisional_evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("forced_closure_evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visual_dependency_evidence_count", sa.Integer(), nullable=False, server_default="0"),
        # evidence origin breakdown - {"OFFICIAL_ACTIVITY": n, ...}; column shape
        # keeps PRACTICE / INITIAL_DIAGNOSTIC / SIMULADO separable in the future.
        sa.Column("origin_breakdown",
                  sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
                  nullable=True),
        sa.Column("first_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("questions_correct <= questions_answered",
                           name="ck_domain_content_mastery_correct_lte_answered"),
        sa.CheckConstraint("questions_correct + questions_incorrect = questions_answered",
                           name="ck_domain_content_mastery_counts_partition"),
        sa.CheckConstraint("questions_answered <= questions_seen",
                           name="ck_domain_content_mastery_answered_lte_seen"),
        sa.CheckConstraint(
            "questions_seen >= 0 AND questions_answered >= 0 AND questions_correct >= 0 "
            "AND questions_incorrect >= 0 AND evidence_count >= 0",
            name="ck_domain_content_mastery_nonneg"),
        sa.CheckConstraint(
            "evidence_state IN ('INSUFFICIENT_EVIDENCE', 'OBSERVED')",
            name="ck_domain_content_mastery_evidence_state"),
    )
    op.create_index("ix_domain_content_mastery_student", TABLE, ["student_external_id"])
    op.create_index("ix_domain_content_mastery_student_tv", TABLE,
                    ["student_external_id", "taxonomy_version"])
    op.create_index("ix_domain_content_mastery_content_code", TABLE, ["content_code"])
    op.create_index("ix_domain_content_mastery_last_activity_at", TABLE, ["last_activity_at"])
    # exactly one CONTENT-grain row and one row per SUBCONTENT per (student, taxonomy, content)
    op.create_index(
        "uq_domain_content_mastery_content_grain", TABLE,
        ["student_external_id", "taxonomy_version", "content_code"],
        unique=True, postgresql_where=sa.text("subcontent_code IS NULL"),
        sqlite_where=sa.text("subcontent_code IS NULL"))
    op.create_index(
        "uq_domain_content_mastery_subcontent_grain", TABLE,
        ["student_external_id", "taxonomy_version", "content_code", "subcontent_code"],
        unique=True, postgresql_where=sa.text("subcontent_code IS NOT NULL"),
        sqlite_where=sa.text("subcontent_code IS NOT NULL"))


def downgrade() -> None:
    for ix in ("uq_domain_content_mastery_subcontent_grain",
               "uq_domain_content_mastery_content_grain",
               "ix_domain_content_mastery_last_activity_at",
               "ix_domain_content_mastery_content_code",
               "ix_domain_content_mastery_student_tv",
               "ix_domain_content_mastery_student"):
        op.drop_index(ix, table_name=TABLE)
    op.drop_table(TABLE)
