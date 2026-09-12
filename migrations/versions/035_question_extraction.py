"""PHASE 27 - Question Extraction Engine.

Revision ID: 035_question_extraction
Revises: 034_ingestion_section_text

FOUR new, additive tables for the staging pipeline DOCUMENT -> QUESTIONS
(spec s20 - decoupled from PHASE 26's DOCUMENT -> MATERIAL and from the
official Question Bank/ENEM pipeline, all completely untouched by this
migration). One run row per extraction attempt, one question row per
detected question, one option row per alternative, one asset row per
associated visual. Touches no official table (questions/question_versions/
question_options/answer_key_entries/catalog_nodes/pedagogical_classifications)
and no existing ingestion/authorial-review table. Fully reversible.
"""

from alembic import op
import sqlalchemy as sa

revision = "035_question_extraction"
down_revision = "034_ingestion_section_text"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
_RUN_STATUSES = "'PENDING', 'PROCESSING', 'COMPLETED', 'FAILED'"
_REVIEW_STATUSES = "'DISCOVERED', 'EXTRACTED', 'VALIDATED', 'REVIEW_REQUIRED', 'APPROVED', 'PUBLISHED', 'REJECTED'"
_QUESTION_TYPES = "'multiple_choice', 'discursive', 'numeric', 'true_false', 'unknown'"


def upgrade() -> None:
    op.create_table(
        "question_extraction_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ingestion_document_id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=True),
        sa.Column("engine_version", sa.String(length=50), nullable=False),
        sa.Column("document_hash", sa.String(length=128), nullable=False),
        sa.Column("run_status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("expected_question_count", sa.Integer(), nullable=True),
        sa.Column("detected_question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("validated_question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_required_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_numbers", _JSON, nullable=True),
        sa.Column("duplicated_numbers", _JSON, nullable=True),
        sa.Column("sequence_gaps", _JSON, nullable=True),
        sa.Column("validated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_by_external_identity", sa.String(length=255), nullable=True),
        sa.Column("metadata", _JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ingestion_document_id"], ["ingestion_documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("ingestion_document_id", "engine_version",
                            name="uq_question_extraction_runs_document_engine"),
        sa.CheckConstraint(f"run_status IN ({_RUN_STATUSES})",
                           name="ck_question_extraction_runs_status"),
    )
    op.create_index("ix_question_extraction_runs_document", "question_extraction_runs",
                    ["ingestion_document_id"])
    op.create_index("ix_question_extraction_runs_school", "question_extraction_runs", ["school_id"])

    op.create_table(
        "extracted_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("question_number", sa.Integer(), nullable=False),
        sa.Column("question_type", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("extraction_confidence", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.Column("flags", _JSON, nullable=True),
        sa.Column("review_status", sa.String(length=20), nullable=False, server_default="DISCOVERED"),
        sa.Column("source_page_start", sa.Integer(), nullable=True),
        sa.Column("source_page_end", sa.Integer(), nullable=True),
        sa.Column("cross_page", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("school_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_by_external_identity", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", _JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["run_id"], ["question_extraction_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(f"review_status IN ({_REVIEW_STATUSES})",
                           name="ck_extracted_questions_review_status"),
        sa.CheckConstraint(f"question_type IN ({_QUESTION_TYPES})",
                           name="ck_extracted_questions_question_type"),
        sa.CheckConstraint("question_number > 0", name="ck_extracted_questions_number_positive"),
        sa.CheckConstraint("extraction_confidence >= 0 AND extraction_confidence <= 1",
                           name="ck_extracted_questions_confidence_range"),
    )
    op.create_index("ix_extracted_questions_run", "extracted_questions", ["run_id"])
    op.create_index("ix_extracted_questions_school", "extracted_questions", ["school_id"])
    op.create_index("ix_extracted_questions_status", "extracted_questions", ["review_status"])
    op.create_index("ix_extracted_questions_fingerprint", "extracted_questions", ["fingerprint"])

    op.create_table(
        "extracted_question_options",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=1), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["question_id"], ["extracted_questions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("question_id", "label", name="uq_extracted_question_options_label"),
        sa.CheckConstraint("position > 0", name="ck_extracted_question_options_position_positive"),
    )
    op.create_index("ix_extracted_question_options_question", "extracted_question_options", ["question_id"])

    op.create_table(
        "extracted_question_assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("asset_type", sa.String(length=20), nullable=False),
        sa.Column("source_page", sa.Integer(), nullable=False),
        sa.Column("bbox", _JSON, nullable=True),
        sa.Column("digest", sa.String(length=128), nullable=False),
        sa.Column("extraction_confidence", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["question_id"], ["extracted_questions.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("asset_type IN ('IMAGE', 'TABLE', 'GRAPH', 'FORMULA', 'OTHER')",
                           name="ck_extracted_question_assets_type"),
    )
    op.create_index("ix_extracted_question_assets_question", "extracted_question_assets", ["question_id"])


def downgrade() -> None:
    op.drop_index("ix_extracted_question_assets_question", table_name="extracted_question_assets")
    op.drop_table("extracted_question_assets")
    op.drop_index("ix_extracted_question_options_question", table_name="extracted_question_options")
    op.drop_table("extracted_question_options")
    op.drop_index("ix_extracted_questions_fingerprint", table_name="extracted_questions")
    op.drop_index("ix_extracted_questions_status", table_name="extracted_questions")
    op.drop_index("ix_extracted_questions_school", table_name="extracted_questions")
    op.drop_index("ix_extracted_questions_run", table_name="extracted_questions")
    op.drop_table("extracted_questions")
    op.drop_index("ix_question_extraction_runs_school", table_name="question_extraction_runs")
    op.drop_index("ix_question_extraction_runs_document", table_name="question_extraction_runs")
    op.drop_table("question_extraction_runs")
