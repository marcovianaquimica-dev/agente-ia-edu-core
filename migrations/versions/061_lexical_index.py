"""CEREBRO / Fase 5 - indice lexical: campos, posicoes e geracao.

Revision ID: 061_lexical_index
Revises: 060_curriculum_bncc_links

ADITIVA, e com uma precondicao verificada: ``knowledge_chunk_terms`` esta
VAZIA em todo ambiente. A tabela nasceu na migracao 057 e nunca teve escritor -
a Fase 5 e o primeiro. A migracao CONFERE isso antes de alterar a tabela e
falha em voz alta se houver linha, em vez de reinterpretar dados existentes
com um significado novo de ``term_frequency``.

O que muda em ``knowledge_chunk_terms``
=======================================

``heading_frequency`` separa corpo de titulo. Indexar a concatenacao que
``build_retrieval_text`` produz tornaria impossivel pesar titulo, explicar o
score e diagnosticar o ``Chapter N`` que e 70,5% dos headings reais.

``positions`` guarda a posicao no fluxo normalizado INTEGRO - stopword nao
gera posting mas ocupa posicao. Medido no livro real: para "concentracao das
solucoes", 217 chunks contem os dois termos e apenas 43 os tem em sequencia.
Sem posicoes, a consulta de duas palavras comuns devolve qualquer chunk que
mencione as duas coisas em paragrafos distantes.

O CHECK antigo ``term_frequency > 0`` TEM de sair: ele proibia indexar um
termo que aparece so no titulo, que e justamente o caso que o campo novo
existe para tratar.

Tabelas novas
=============

``knowledge_chunk_lexical_index`` - estado por chunk: ``token_count`` (o
``dl`` do BM25), ``text_hash`` indexado, ``normalizer_version`` e geracao.
Estatistica POR CHUNK, escrita na mesma transacao dos postings. A estatistica
GLOBAL (``df``, ``N``, ``avgdl``) continua saindo de agregacao na hora da
busca - o que poderia derivar nao foi materializado.

``knowledge_lexical_index_state`` - a geracao do indice, que entra no
``query_fingerprint`` para que mudanca de postings entre paginas da mesma
consulta seja detectavel.

Id de revisao: "061_lexical_index" tem 17 caracteres, dentro do VARCHAR(32) de
``alembic_version``. Ver ``tests/test_migration_revision_ids.py``, que existe
porque a 059 nasceu com 38 e derrubou 46 testes.
"""

import sqlalchemy as sa
from alembic import op

from agente_ia_edu.db.types import JSONBCompatible

revision = "061_lexical_index"
down_revision = "060_curriculum_bncc_links"
branch_labels = None
depends_on = None


def _assert_terms_table_is_empty() -> None:
    existing = op.get_bind().execute(
        sa.text("SELECT count(*) FROM knowledge_chunk_terms")
    ).scalar()
    if existing:
        raise RuntimeError(
            f"knowledge_chunk_terms tem {existing} linhas, e esta migracao assume a "
            "tabela vazia: ela redefine o significado de term_frequency (corpo) e "
            "acrescenta heading_frequency. Reindexe o corpus apos aplicar, em vez de "
            "deixar postings com semantica antiga"
        )


def upgrade() -> None:
    _assert_terms_table_is_empty()

    op.add_column(
        "knowledge_chunk_terms",
        sa.Column("heading_frequency", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "knowledge_chunk_terms",
        sa.Column("positions", JSONBCompatible(), nullable=True),
    )
    with op.batch_alter_table("knowledge_chunk_terms") as batch:
        batch.drop_constraint(
            "ck_knowledge_chunk_terms_frequency_positive", type_="check"
        )
        batch.create_check_constraint(
            "ck_knowledge_chunk_terms_any_frequency",
            "term_frequency >= 0 AND heading_frequency >= 0 "
            "AND term_frequency + heading_frequency > 0",
        )
    op.create_index(
        "ix_knowledge_chunk_terms_term_chunk",
        "knowledge_chunk_terms",
        ["term", "chunk_id"],
    )

    op.create_table(
        "knowledge_chunk_lexical_index",
        sa.Column("chunk_id", sa.Uuid(), primary_key=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("heading_token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("normalizer_version", sa.String(20), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["knowledge_chunks.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "token_count >= 0 AND heading_token_count >= 0",
            name="ck_knowledge_chunk_lexical_index_counts_not_negative",
        ),
    )
    op.create_index(
        "ix_knowledge_chunk_lexical_index_generation",
        "knowledge_chunk_lexical_index",
        ["generation"],
    )

    op.create_table(
        "knowledge_lexical_index_state",
        sa.Column("scope", sa.String(40), primary_key=True),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("normalizer_version", sa.String(20), nullable=False),
        sa.Column("policy_version", sa.String(20), nullable=False),
        sa.Column("last_operation", sa.String(40), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "generation > 0", name="ck_knowledge_lexical_index_state_generation"
        ),
    )


def downgrade() -> None:
    op.drop_table("knowledge_lexical_index_state")
    op.drop_index(
        "ix_knowledge_chunk_lexical_index_generation",
        table_name="knowledge_chunk_lexical_index",
    )
    op.drop_table("knowledge_chunk_lexical_index")
    op.drop_index("ix_knowledge_chunk_terms_term_chunk", table_name="knowledge_chunk_terms")
    with op.batch_alter_table("knowledge_chunk_terms") as batch:
        batch.drop_constraint("ck_knowledge_chunk_terms_any_frequency", type_="check")
        batch.create_check_constraint(
            "ck_knowledge_chunk_terms_frequency_positive", "term_frequency > 0"
        )
    op.drop_column("knowledge_chunk_terms", "positions")
    op.drop_column("knowledge_chunk_terms", "heading_frequency")
