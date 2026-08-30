"""
006_ingestion_engine - Ingestion pipeline infrastructure (Phase 3 MVP).

Tracks document ingestion, preserves original files, extracts structure
deterministically with full traceability. No LLM-based extraction in this phase.

Tables:
- ingestion_documents: Tracks uploaded/ingested documents with file preservation
- ingestion_runs: Individual parse runs with extraction statistics
- ingestion_sections: Hierarchical structure (seasons, episodes, chapters)
- ingestion_questions: Extracted questions with alternatives and answer keys
- ingestion_assets: Images, tables, graphics with context preservation

Foreign key dependency order:
1. ingestion_documents (independent)
2. ingestion_runs (FK -> ingestion_documents)
3. ingestion_sections (FK -> ingestion_documents, self-reference parent_id)
4. ingestion_questions (FK -> ingestion_documents, ingestion_sections, question_versions)
5. ingestion_assets (FK -> ingestion_documents, ingestion_sections, ingestion_questions)

Downgrade reverses exactly in reverse order.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "006_ingestion_engine"
down_revision: Union[str, None] = "005_pedagogical_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Upgrade


def upgrade() -> None:
    # Create ingestion_documents table
    op.create_table(
        "ingestion_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("document_type", sa.String(20), nullable=False),
        sa.Column("document_hash", sa.String(128), nullable=False),
        sa.Column("storage_uri", sa.String(2048), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column("author", sa.String(500)),
        sa.Column("page_count", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("ingested_by_external_identity", sa.String(255)),
        sa.Column("ingestion_error", sa.Text()),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "document_type IN ('DOCX', 'PDF', 'OTHER')",
            name="ck_ingestion_documents_document_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'processed', 'failed', 'archived')",
            name="ck_ingestion_documents_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ingestion_documents_document_hash",
        "ingestion_documents",
        ["document_hash"],
    )
    op.create_index(
        "ix_ingestion_documents_status", "ingestion_documents", ["status"]
    )

    # Create ingestion_runs table
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("run_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("parser_version", sa.String(50), nullable=False),
        sa.Column("sections_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("questions_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("images_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tables_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "run_status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_ingestion_runs_run_status",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["ingestion_documents.id"],
            ondelete="RESTRICT",
            name="fk_ingestion_runs_document_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ingestion_runs_document_id", "ingestion_runs", ["document_id"]
    )

    # Create ingestion_sections table
    op.create_table(
        "ingestion_sections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid()),
        sa.Column("section_type", sa.String(50), nullable=False),
        sa.Column("section_number", sa.String(50)),
        sa.Column("title", sa.String(500)),
        sa.Column("description", sa.Text()),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer()),
        sa.Column("page_end", sa.Integer()),
        sa.Column("content_preview", sa.Text()),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_ingestion_sections_parent_not_self",
        ),
        sa.CheckConstraint(
            "section_type IN ('SEASON', 'EPISODE', 'CHAPTER', 'SUBSECTION', 'SECTION', 'OTHER')",
            name="ck_ingestion_sections_section_type",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["ingestion_documents.id"],
            ondelete="RESTRICT",
            name="fk_ingestion_sections_document_id",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["ingestion_sections.id"],
            ondelete="RESTRICT",
            name="fk_ingestion_sections_parent_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ingestion_sections_document_id", "ingestion_sections", ["document_id"]
    )
    op.create_index(
        "ix_ingestion_sections_parent_id", "ingestion_sections", ["parent_id"]
    )

    # Create ingestion_questions table
    op.create_table(
        "ingestion_questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid()),
        sa.Column("question_number", sa.Integer(), nullable=False),
        sa.Column("question_type", sa.String(30), nullable=False),
        sa.Column("statement_text", sa.Text(), nullable=False),
        sa.Column("alternatives_text", sa.Text()),
        sa.Column("correct_answer", sa.String(10)),
        sa.Column("answer_explanation", sa.Text()),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer()),
        sa.Column("page_end", sa.Integer()),
        sa.Column("status", sa.String(20), nullable=False, server_default="extracted"),
        sa.Column("question_version_id", sa.Uuid()),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "question_type IN ('MULTIPLE_CHOICE', 'TRUE_FALSE', 'SHORT_ANSWER', 'ESSAY', 'OTHER')",
            name="ck_ingestion_questions_question_type",
        ),
        sa.CheckConstraint(
            "status IN ('extracted', 'validated', 'imported', 'rejected')",
            name="ck_ingestion_questions_status",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["ingestion_documents.id"],
            ondelete="RESTRICT",
            name="fk_ingestion_questions_document_id",
        ),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["ingestion_sections.id"],
            ondelete="RESTRICT",
            name="fk_ingestion_questions_section_id",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id"],
            ["question_versions.id"],
            ondelete="RESTRICT",
            name="fk_ingestion_questions_question_version_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ingestion_questions_document_id", "ingestion_questions", ["document_id"]
    )
    op.create_index(
        "ix_ingestion_questions_section_id", "ingestion_questions", ["section_id"]
    )

    # Create ingestion_assets table
    op.create_table(
        "ingestion_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("asset_type", sa.String(20), nullable=False),
        sa.Column("asset_name", sa.String(500)),
        sa.Column("storage_uri", sa.String(2048), nullable=False),
        sa.Column("section_id", sa.Uuid()),
        sa.Column("question_id", sa.Uuid()),
        sa.Column("page", sa.Integer()),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "asset_type IN ('IMAGE', 'TABLE', 'FORMULA', 'DIAGRAM', 'OTHER')",
            name="ck_ingestion_assets_asset_type",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["ingestion_documents.id"],
            ondelete="RESTRICT",
            name="fk_ingestion_assets_document_id",
        ),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["ingestion_sections.id"],
            ondelete="RESTRICT",
            name="fk_ingestion_assets_section_id",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["ingestion_questions.id"],
            ondelete="RESTRICT",
            name="fk_ingestion_assets_question_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ingestion_assets_document_id", "ingestion_assets", ["document_id"]
    )
    op.create_index(
        "ix_ingestion_assets_question_id", "ingestion_assets", ["question_id"]
    )


# Downgrade


def downgrade() -> None:
    # Drop ingestion_assets table
    op.drop_index("ix_ingestion_assets_question_id", table_name="ingestion_assets")
    op.drop_index("ix_ingestion_assets_document_id", table_name="ingestion_assets")
    op.drop_table("ingestion_assets")

    # Drop ingestion_questions table
    op.drop_index("ix_ingestion_questions_section_id", table_name="ingestion_questions")
    op.drop_index("ix_ingestion_questions_document_id", table_name="ingestion_questions")
    op.drop_table("ingestion_questions")

    # Drop ingestion_sections table
    op.drop_index("ix_ingestion_sections_parent_id", table_name="ingestion_sections")
    op.drop_index("ix_ingestion_sections_document_id", table_name="ingestion_sections")
    op.drop_table("ingestion_sections")

    # Drop ingestion_runs table
    op.drop_index("ix_ingestion_runs_document_id", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")

    # Drop ingestion_documents table
    op.drop_index(
        "ix_ingestion_documents_status", table_name="ingestion_documents"
    )
    op.drop_index(
        "ix_ingestion_documents_document_hash", table_name="ingestion_documents"
    )
    op.drop_table("ingestion_documents")
