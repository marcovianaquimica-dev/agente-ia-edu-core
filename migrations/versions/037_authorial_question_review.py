"""PHASE 29 - Authorial Question Review, Approval & Publication.

Revision ID: 037_authorial_question_review
Revises: 036_question_reconstruction

Three additive, backward-compatible changes:

1. ``extracted_questions`` (PHASE 27/28 staging table, unchanged otherwise):
   - ``reviewed_text`` (nullable) - the professor's edited statement, kept
     SEPARATE from ``raw_text`` (immutable, PHASE 27) and ``normalized_text``/
     ``reconstructed_text`` (engine output, PHASE 27/28). Human review is a
     LAYER over extraction, never a replacement of it (spec s24).
   - ``rejection_reason`` (nullable, structured - spec s12) and
     ``published_question_id``/``published_version_id`` (nullable FKs into
     the OFFICIAL ``questions``/``question_versions`` tables, SET NULL on
     delete) - the staging row's own record of WHERE it was promoted to,
     never the other direction.
   - the ``review_status`` CHECK widens to add ``IN_REVIEW`` (professor is
     actively editing - spec s3) and ``DUPLICATE_REVIEW`` (publish-time
     content-hash collision found - spec s19, never silently discarded).

2. ``extracted_question_assets`` (PHASE 27 staging table): ``question_id``
   becomes nullable and a new ``run_id``/``status`` pair is added, so an
   image detected in the document but NOT auto-associated to any question
   can be persisted and later explicitly associated or ignored by a human
   (spec s8) instead of being silently dropped as before this phase.

3. ``questions`` (the OFFICIAL Question Bank table) - ``origin_type`` CHECK
   widens to add ``AUTHORIAL`` (spec s20: authorial-material questions must
   carry a clearly identifiable origin). This is the ONLY touch to an
   official table in this phase, and it is a pure, reversible WIDENING of an
   allowed-values list - it creates, modifies, and deletes ZERO existing
   rows, and does not change the behaviour of any existing origin_type value
   (PLATFORM/SCHOOL/TEACHER/IMPORTED/GENERATED all keep their exact prior
   meaning). No official row is inserted, updated, or removed by this
   migration itself - only a NEW publish flow (question_publication_service.py)
   will later INSERT new rows tagged 'AUTHORIAL'.
"""

from alembic import op
import sqlalchemy as sa

revision = "037_authorial_question_review"
down_revision = "036_question_reconstruction"
branch_labels = None
depends_on = None

_REVIEW_STATUS_CK = "ck_extracted_questions_review_status"
_REVIEW_STATUS_OLD = (
    "review_status IN ('DISCOVERED', 'EXTRACTED', 'VALIDATED', 'REVIEW_REQUIRED', "
    "'APPROVED', 'PUBLISHED', 'REJECTED')"
)
_REVIEW_STATUS_NEW = (
    "review_status IN ('DISCOVERED', 'EXTRACTED', 'VALIDATED', 'REVIEW_REQUIRED', "
    "'IN_REVIEW', 'APPROVED', 'PUBLISHED', 'REJECTED', 'DUPLICATE_REVIEW')"
)
_REJECTION_REASON_CK = "ck_extracted_questions_rejection_reason"
_REJECTION_REASON_EXPR = (
    "rejection_reason IS NULL OR rejection_reason IN "
    "('DUPLICATE', 'CORRUPTED_SOURCE', 'INCOMPLETE_SOURCE', 'NOT_A_QUESTION', "
    "'UNUSABLE_CONTENT', 'OTHER')"
)
_ASSET_STATUS_CK = "ck_extracted_question_assets_status"
_ASSET_STATUS_EXPR = "status IN ('ASSOCIATED', 'UNASSOCIATED', 'IGNORED')"

_ORIGIN_TYPE_CK = "ck_questions_origin_type"
_ORIGIN_TYPE_OLD = "origin_type IN ('PLATFORM', 'SCHOOL', 'TEACHER', 'IMPORTED', 'GENERATED')"
_ORIGIN_TYPE_NEW = (
    "origin_type IN ('PLATFORM', 'SCHOOL', 'TEACHER', 'IMPORTED', 'GENERATED', 'AUTHORIAL')"
)


def _swap_check(name: str, table: str, new_expr: str, old_expr: str | None) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if old_expr is not None:
        op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, new_expr)


def _drop_check(name: str, table: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.drop_constraint(name, table, type_="check")


def upgrade() -> None:
    # -- 1. extracted_questions: review-layer columns + widened status ------
    op.add_column("extracted_questions", sa.Column("reviewed_text", sa.Text(), nullable=True))
    op.add_column("extracted_questions", sa.Column("rejection_reason", sa.String(length=30), nullable=True))
    op.add_column("extracted_questions", sa.Column("published_question_id", sa.Uuid(), nullable=True))
    op.add_column("extracted_questions", sa.Column("published_version_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_extracted_questions_published_question", "extracted_questions", "questions",
        ["published_question_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_extracted_questions_published_version", "extracted_questions", "question_versions",
        ["published_version_id"], ["id"], ondelete="SET NULL",
    )
    _swap_check(_REVIEW_STATUS_CK, "extracted_questions", _REVIEW_STATUS_NEW, _REVIEW_STATUS_OLD)
    op.create_check_constraint(_REJECTION_REASON_CK, "extracted_questions", _REJECTION_REASON_EXPR)

    # -- 2. extracted_question_assets: nullable question_id + status --------
    op.alter_column("extracted_question_assets", "question_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column("extracted_question_assets", sa.Column("run_id", sa.Uuid(), nullable=True))
    op.add_column(
        "extracted_question_assets",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ASSOCIATED"),
    )
    op.create_foreign_key(
        "fk_extracted_question_assets_run", "extracted_question_assets", "question_extraction_runs",
        ["run_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_check_constraint(_ASSET_STATUS_CK, "extracted_question_assets", _ASSET_STATUS_EXPR)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "UPDATE extracted_question_assets a SET run_id = q.run_id "
            "FROM extracted_questions q WHERE a.question_id = q.id AND a.run_id IS NULL"
        )

    # -- 3. questions (OFFICIAL): widen origin_type only - no data touched --
    _swap_check(_ORIGIN_TYPE_CK, "questions", _ORIGIN_TYPE_NEW, _ORIGIN_TYPE_OLD)


def downgrade() -> None:
    _swap_check(_ORIGIN_TYPE_CK, "questions", _ORIGIN_TYPE_OLD, _ORIGIN_TYPE_NEW)

    _drop_check(_ASSET_STATUS_CK, "extracted_question_assets")
    op.drop_constraint("fk_extracted_question_assets_run", "extracted_question_assets", type_="foreignkey")
    op.drop_column("extracted_question_assets", "status")
    op.drop_column("extracted_question_assets", "run_id")
    op.alter_column("extracted_question_assets", "question_id", existing_type=sa.Uuid(), nullable=False)

    _drop_check(_REJECTION_REASON_CK, "extracted_questions")
    _swap_check(_REVIEW_STATUS_CK, "extracted_questions", _REVIEW_STATUS_OLD, _REVIEW_STATUS_NEW)
    op.drop_constraint("fk_extracted_questions_published_version", "extracted_questions", type_="foreignkey")
    op.drop_constraint("fk_extracted_questions_published_question", "extracted_questions", type_="foreignkey")
    op.drop_column("extracted_questions", "published_version_id")
    op.drop_column("extracted_questions", "published_question_id")
    op.drop_column("extracted_questions", "rejection_reason")
    op.drop_column("extracted_questions", "reviewed_text")
