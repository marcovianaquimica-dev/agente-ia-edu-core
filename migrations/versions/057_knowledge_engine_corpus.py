"""CEREBRO / Knowledge Engine - corpus de conhecimento (Fase 1, parte 1/2).

Revision ID: 057_knowledge_engine_corpus
Revises: 055_essay_prompt_soft_delete

Quatro tabelas novas: knowledge_sources, knowledge_documents,
knowledge_chunks, knowledge_chunk_terms. Puramente aditiva - nenhuma tabela
existente e tocada, nenhuma linha existente e lida ou alterada.

Encadeada em 055 DELIBERADAMENTE, e nao na 056_material_assignments, que na
data desta migracao ainda nao estava commitada em ramo algum: encadear nela
criaria dependencia dura entre dois fluxos de trabalho nao relacionados. A
divergencia de heads que isso produz sera resolvida por uma merge revision do
Alembic no momento da integracao dos branches - o padrao normal para ramos
paralelos.

A trava central do subsistema esta aqui:
``ck_knowledge_sources_commercial_has_no_resource``. Sem EducationalResource,
uma fonte COMMERCIAL_REFERENCE nao tem caminho ate o aluno - nao por um `if`
num servico, mas porque a topologia nao permite.
"""

import sqlalchemy as sa
from alembic import op

from agente_ia_edu.db.types import JSONBCompatible

revision = "057_knowledge_engine_corpus"
down_revision = "055_essay_prompt_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("authors", sa.String(500)),
        sa.Column("publisher", sa.String(255)),
        sa.Column("edition", sa.String(100)),
        sa.Column("publication_year", sa.Integer()),
        sa.Column("isbn", sa.String(20)),
        sa.Column("source_kind", sa.String(40), nullable=False),
        sa.Column("rights_class", sa.String(30), nullable=False),
        sa.Column("authority_level", sa.String(30), nullable=False),
        sa.Column("source_quality_score", sa.Numeric(4, 3)),
        sa.Column("license_reference", sa.Text()),
        sa.Column("rights_notes", sa.Text()),
        sa.Column("educational_resource_id", sa.Uuid()),
        sa.Column("status", sa.String(20), nullable=False, server_default="REGISTERED"),
        sa.Column("created_by_external_identity", sa.String(255)),
        sa.Column("metadata", JSONBCompatible()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["educational_resource_id"], ["educational_resources.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "rights_class IN ('COMMERCIAL_REFERENCE', 'LICENSED', 'OWN', "
            "'PUBLIC_DOMAIN', 'OFFICIAL_PUBLIC')",
            name="ck_knowledge_sources_rights_class",
        ),
        sa.CheckConstraint(
            "status IN ('REGISTERED', 'EXTRACTING', 'EXTRACTED', 'CHUNKED', "
            "'EMBEDDED', 'READY', 'FAILED', 'ARCHIVED')",
            name="ck_knowledge_sources_status",
        ),
        sa.CheckConstraint(
            "rights_class <> 'COMMERCIAL_REFERENCE' OR educational_resource_id IS NULL",
            name="ck_knowledge_sources_commercial_has_no_resource",
        ),
    )
    op.create_index("ix_knowledge_sources_rights_class", "knowledge_sources", ["rights_class"])
    op.create_index("ix_knowledge_sources_status", "knowledge_sources", ["status"])

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("storage_uri", sa.String(2048), nullable=False),
        sa.Column("document_hash", sa.String(128), nullable=False),
        sa.Column("file_size_bytes", sa.Integer()),
        sa.Column("mime_type", sa.String(100)),
        sa.Column("page_count", sa.Integer()),
        sa.Column("page_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extraction_method", sa.String(30)),
        sa.Column("extraction_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("extraction_error", sa.Text()),
        sa.Column("metadata", JSONBCompatible()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("source_id", "document_hash", name="uq_knowledge_documents_source_hash"),
        sa.CheckConstraint(
            "extraction_method IS NULL OR extraction_method IN "
            "('PDF_TEXT_LAYER', 'PDF_TEXT_LAYER_PYMUPDF', 'VISION_OCR', 'DOCX', 'MARKDOWN')",
            name="ck_knowledge_documents_extraction_method",
        ),
        sa.CheckConstraint(
            "extraction_status IN ('PENDING', 'EXTRACTING', 'EXTRACTED', 'FAILED')",
            name="ck_knowledge_documents_extraction_status",
        ),
    )
    op.create_index("ix_knowledge_documents_source_id", "knowledge_documents", ["source_id"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("chunk_type", sa.String(30), nullable=False),
        sa.Column("heading_path", JSONBCompatible()),
        sa.Column("page_start", sa.Integer()),
        sa.Column("page_end", sa.Integer()),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("char_count", sa.Integer()),
        sa.Column("token_estimate", sa.Integer()),
        sa.Column("content_node_id", sa.Uuid()),
        sa.Column("curriculum_match_confidence", sa.Numeric(4, 3)),
        sa.Column("curriculum_match_source", sa.String(30)),
        sa.Column("bncc_node_codes", JSONBCompatible()),
        sa.Column("metadata", JSONBCompatible()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["content_node_id"], ["catalog_nodes.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_knowledge_chunks_document_ordinal"),
        sa.CheckConstraint("ordinal > 0", name="ck_knowledge_chunks_ordinal_positive"),
        sa.CheckConstraint(
            "curriculum_match_source IS NULL OR curriculum_match_source IN "
            "('DETERMINISTIC', 'MANUAL', 'INHERITED')",
            name="ck_knowledge_chunks_curriculum_match_source",
        ),
    )
    op.create_index("ix_knowledge_chunks_source_id", "knowledge_chunks", ["source_id"])
    op.create_index(
        "ix_knowledge_chunks_document_ordinal", "knowledge_chunks", ["document_id", "ordinal"]
    )
    op.create_index("ix_knowledge_chunks_content_node_id", "knowledge_chunks", ["content_node_id"])
    op.create_index("ix_knowledge_chunks_text_hash", "knowledge_chunks", ["text_hash"])

    op.create_table(
        "knowledge_chunk_terms",
        sa.Column("chunk_id", sa.Uuid(), primary_key=True),
        sa.Column("term", sa.String(80), primary_key=True),
        sa.Column("term_frequency", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["knowledge_chunks.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("term_frequency > 0", name="ck_knowledge_chunk_terms_frequency_positive"),
    )
    op.create_index("ix_knowledge_chunk_terms_term", "knowledge_chunk_terms", ["term"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunk_terms_term", table_name="knowledge_chunk_terms")
    op.drop_table("knowledge_chunk_terms")
    op.drop_index("ix_knowledge_chunks_text_hash", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_content_node_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_document_ordinal", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_source_id", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_knowledge_documents_source_id", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    op.drop_index("ix_knowledge_sources_status", table_name="knowledge_sources")
    op.drop_index("ix_knowledge_sources_rights_class", table_name="knowledge_sources")
    op.drop_table("knowledge_sources")
