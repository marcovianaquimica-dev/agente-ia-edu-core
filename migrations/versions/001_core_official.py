"""Create the official question core.

Revision ID: 001_core_official
Revises:
Create Date: 2026-08-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "001_core_official"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "institutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("website_url", sa.String(length=2048), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_institutions"),
        sa.UniqueConstraint("code", name="uq_institutions_code"),
    )

    op.create_table(
        "exams",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("institution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
            name="fk_exams_institution_id_institutions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_exams"),
        sa.UniqueConstraint(
            "institution_id",
            "code",
            name="uq_exams_institution_code",
        ),
    )

    op.create_table(
        "exam_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exam_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("application_type", sa.String(length=50), nullable=False),
        sa.Column("day", sa.Integer(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("official_identifier", sa.String(length=255), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint("year > 0", name="ck_exam_applications_year_positive"),
        sa.CheckConstraint(
            "day IS NULL OR day > 0",
            name="ck_exam_applications_day_positive",
        ),
        sa.ForeignKeyConstraint(
            ["exam_id"],
            ["exams.id"],
            name="fk_exam_applications_exam_id_exams",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_exam_applications"),
        sa.UniqueConstraint(
            "exam_id",
            "year",
            "application_type",
            "day",
            name="uq_exam_applications_identity",
        ),
    )

    op.create_table(
        "exam_booklets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exam_application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("color", sa.String(length=50), nullable=True),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("booklet_type", sa.String(length=50), nullable=True),
        sa.Column("official_identifier", sa.String(length=255), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["exam_application_id"],
            ["exam_applications.id"],
            name="fk_exam_booklets_exam_application_id_exam_applications",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_exam_booklets"),
        sa.UniqueConstraint(
            "exam_application_id",
            "code",
            name="uq_exam_booklets_application_code",
        ),
    )

    op.create_table(
        "source_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exam_application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exam_booklet_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_type", sa.String(length=50), nullable=False),
        sa.Column("official_identifier", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("storage_uri", sa.String(length=2048), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("extraction_method", sa.String(length=100), nullable=True),
        sa.Column("extractor_version", sa.String(length=100), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "page_count IS NULL OR page_count > 0",
            name="ck_source_documents_page_count_positive",
        ),
        sa.ForeignKeyConstraint(
            ["exam_application_id"],
            ["exam_applications.id"],
            name="fk_source_documents_exam_application_id_exam_applications",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["exam_booklet_id"],
            ["exam_booklets.id"],
            name="fk_source_documents_exam_booklet_id_exam_booklets",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_documents"),
    )
    op.create_index(
        "ix_source_documents_content_hash",
        "source_documents",
        ["content_hash"],
    )

    op.create_table(
        "questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("validation_status", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_questions"),
    )

    op.create_table(
        "question_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_kind", sa.String(length=50), nullable=False),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("canonical_text", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_type", sa.String(length=50), nullable=True),
        sa.Column("created_by_id", sa.String(length=255), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("is_immutable", sa.Boolean(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["question_versions.id"],
            name="fk_question_versions_parent_version_id_question_versions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["questions.id"],
            name="fk_question_versions_question_id_questions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_question_versions"),
    )
    # The partial unique index preserves one official original per question while
    # allowing any number of adapted versions.
    op.create_index(
        "uq_question_versions_official_original",
        "question_versions",
        ["question_id"],
        unique=True,
        postgresql_where=sa.text("version_kind = 'official_original'"),
    )
    op.create_index(
        "ix_question_versions_content_hash",
        "question_versions",
        ["content_hash"],
    )

    op.create_table(
        "question_options",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("option_key", sa.String(length=10), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_valid_option", sa.Boolean(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint("position > 0", name="ck_question_options_position_positive"),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["question_versions.id"],
            name="fk_question_options_question_version_id_question_versions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_question_options"),
        sa.UniqueConstraint(
            "question_version_id",
            "option_key",
            name="uq_question_options_version_key",
        ),
        sa.UniqueConstraint(
            "question_version_id",
            "position",
            name="uq_question_options_version_position",
        ),
    )

    op.create_table(
        "booklet_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exam_booklet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("official_number", sa.Integer(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("extraction_method", sa.String(length=100), nullable=True),
        sa.Column("extractor_version", sa.String(length=100), nullable=True),
        sa.Column("evidence_uri", sa.String(length=2048), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint("position > 0", name="ck_booklet_questions_position_positive"),
        sa.CheckConstraint(
            "official_number IS NULL OR official_number > 0",
            name="ck_booklet_questions_official_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["exam_booklet_id"],
            ["exam_booklets.id"],
            name="fk_booklet_questions_exam_booklet_id_exam_booklets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["question_versions.id"],
            name="fk_booklet_questions_question_version_id_question_versions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_booklet_questions"),
        sa.UniqueConstraint(
            "exam_booklet_id",
            "position",
            name="uq_booklet_questions_booklet_position",
        ),
    )
    # NULL official numbers are intentionally excluded so multiple unknown
    # numbers can coexist in the same booklet.
    op.create_index(
        "ix_booklet_questions_booklet_official_number_not_null",
        "booklet_questions",
        ["exam_booklet_id", "official_number"],
        unique=True,
        postgresql_where=sa.text("official_number IS NOT NULL"),
    )

    op.create_table(
        "answer_key_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_official", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_answer_key_revisions_revision_positive",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            name="fk_answer_key_revisions_source_document_id_source_documents",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["answer_key_revisions.id"],
            name="fk_answer_key_revisions_supersedes_id_answer_key_revisions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_answer_key_revisions"),
    )

    op.create_table(
        "answer_key_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("answer_key_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("booklet_question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("official_answer_label", sa.String(length=10), nullable=False),
        sa.Column("resolved_option_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["answer_key_revision_id"],
            ["answer_key_revisions.id"],
            name="fk_answer_key_entries_revision_id_answer_key_revisions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["booklet_question_id"],
            ["booklet_questions.id"],
            name="fk_answer_key_entries_booklet_question_id_booklet_questions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_option_id"],
            ["question_options.id"],
            name="fk_answer_key_entries_resolved_option_id_question_options",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_answer_key_entries"),
        sa.UniqueConstraint(
            "answer_key_revision_id",
            "booklet_question_id",
            name="uq_answer_key_entries_revision_question",
        ),
    )


def downgrade() -> None:
    op.drop_table("answer_key_entries")
    op.drop_table("answer_key_revisions")
    op.drop_index(
        "ix_booklet_questions_booklet_official_number_not_null",
        table_name="booklet_questions",
    )
    op.drop_table("booklet_questions")
    op.drop_table("question_options")
    op.drop_index(
        "ix_question_versions_content_hash",
        table_name="question_versions",
    )
    op.drop_index(
        "uq_question_versions_official_original",
        table_name="question_versions",
    )
    op.drop_table("question_versions")
    op.drop_table("questions")
    op.drop_index(
        "ix_source_documents_content_hash",
        table_name="source_documents",
    )
    op.drop_table("source_documents")
    op.drop_table("exam_booklets")
    op.drop_table("exam_applications")
    op.drop_table("exams")
    op.drop_table("institutions")
