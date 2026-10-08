"""CEREBRO / Knowledge Engine - espacos e vetores de embedding (Fase 1, 2/2).

Revision ID: 058_knowledge_engine_embeddings
Revises: 057_knowledge_engine_corpus

Cria a extensao pgvector (so no PostgreSQL), knowledge_embedding_spaces,
knowledge_chunk_embeddings, semeia o espaco inicial e cria o indice HNSW
parcial desse espaco. Puramente aditiva.

A DIMENSAO NAO APARECE NO SCHEMA. A coluna ``embedding`` e ``vector`` sem
tamanho; 1536 e um valor de linha em knowledge_embedding_spaces. Isso e o que
impede que trocar de modelo de embedding vire migracao de tabela.

O indice ANN, que o pgvector exige dimensionado, resolve isso com um indice
PARCIAL por espaco, com cast explicito:

    USING hnsw ((embedding::vector(1536)) vector_cosine_ops) WHERE space_id = ...

Um espaco de 1024 dimensoes depois = uma linha nova + um indice parcial novo.
Nenhuma coluna muda, nenhum dado e reescrito.

O UUID do espaco inicial e um literal fixo nesta migracao, e nao um uuid4
gerado em tempo de execucao, para que o indice parcial possa referencia-lo e
para que a migracao seja reproduzivel em qualquer ambiente.
"""

import uuid

import sqlalchemy as sa
from alembic import op

from agente_ia_edu.db.types import VectorCompatible

revision = "058_knowledge_engine_embeddings"
down_revision = "057_knowledge_engine_corpus"
branch_labels = None
depends_on = None

# Espaco inicial do piloto. UUID literal e deliberado - ver docstring.
INITIAL_SPACE_ID = "0197e5a0-0000-7000-8000-000000000001"
INITIAL_PROVIDER = "openai"
INITIAL_MODEL = "text-embedding-3-small"
INITIAL_DIMENSIONS = 1536


def upgrade() -> None:
    bind = op.get_bind()
    is_postgresql = bind.dialect.name == "postgresql"

    if is_postgresql:
        # Aditiva: nenhuma tabela, coluna ou linha existente e afetada.
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_embedding_spaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("distance_metric", sa.String(20), nullable=False, server_default="cosine"),
        sa.Column("status", sa.String(20), nullable=False, server_default="BACKFILLING"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "provider", "model", "dimensions", name="uq_knowledge_embedding_spaces_identity"
        ),
        sa.CheckConstraint(
            "dimensions > 0", name="ck_knowledge_embedding_spaces_dimensions_positive"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'BACKFILLING', 'RETIRED')",
            name="ck_knowledge_embedding_spaces_status",
        ),
        sa.CheckConstraint(
            "distance_metric IN ('cosine', 'l2', 'inner_product')",
            name="ck_knowledge_embedding_spaces_distance_metric",
        ),
    )
    op.create_index(
        "uq_knowledge_embedding_spaces_single_active",
        "knowledge_embedding_spaces",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        "knowledge_chunk_embeddings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("embedding", VectorCompatible()),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["knowledge_chunks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["space_id"], ["knowledge_embedding_spaces.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "chunk_id", "space_id", "text_hash", name="uq_knowledge_chunk_embeddings_identity"
        ),
    )
    op.create_index(
        "ix_knowledge_chunk_embeddings_active",
        "knowledge_chunk_embeddings",
        ["space_id"],
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_knowledge_chunk_embeddings_chunk_id", "knowledge_chunk_embeddings", ["chunk_id"]
    )

    # O id viaja como bindparam TIPADO, nunca como string crua: psycopg tipa
    # str como VARCHAR e o PostgreSQL nao faz cast implicito para uuid. Com
    # sa.Uuid() o valor sai certo nos dois dialetos (uuid nativo no PG,
    # CHAR(32) no SQLite).
    op.execute(
        sa.text(
            "INSERT INTO knowledge_embedding_spaces "
            "(id, provider, model, dimensions, distance_metric, status, notes, "
            " created_at, activated_at) "
            "VALUES (:id, :provider, :model, :dimensions, 'cosine', 'ACTIVE', :notes, "
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ).bindparams(
            sa.bindparam("id", uuid.UUID(INITIAL_SPACE_ID), type_=sa.Uuid()),
            provider=INITIAL_PROVIDER,
            model=INITIAL_MODEL,
            dimensions=INITIAL_DIMENSIONS,
            notes="Espaco inicial do piloto CEREBRO (Fase 1).",
        )
    )

    if is_postgresql:
        # Indice ANN por espaco. O cast explicito e o que permite que a coluna
        # permaneca sem dimensao: o indice sabe o tamanho, a tabela nao.
        op.execute(
            f"CREATE INDEX ix_kce_hnsw_{INITIAL_SPACE_ID.replace('-', '')} "
            "ON knowledge_chunk_embeddings "
            f"USING hnsw ((embedding::vector({INITIAL_DIMENSIONS})) vector_cosine_ops) "
            f"WHERE space_id = '{INITIAL_SPACE_ID}'"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP INDEX IF EXISTS ix_kce_hnsw_{INITIAL_SPACE_ID.replace('-', '')}")
    op.drop_index(
        "ix_knowledge_chunk_embeddings_chunk_id", table_name="knowledge_chunk_embeddings"
    )
    op.drop_index("ix_knowledge_chunk_embeddings_active", table_name="knowledge_chunk_embeddings")
    op.drop_table("knowledge_chunk_embeddings")
    op.drop_index(
        "uq_knowledge_embedding_spaces_single_active", table_name="knowledge_embedding_spaces"
    )
    op.drop_table("knowledge_embedding_spaces")
    # A extensao vector NAO e removida: outros objetos podem passar a depender
    # dela, e derrubar uma extensao num downgrade e destrutivo demais para o
    # que esta migracao criou.
