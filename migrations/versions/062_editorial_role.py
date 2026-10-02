"""CEREBRO / Fase 5.1b - papel editorial do chunk na obra.

Revision ID: 062_editorial_role
Revises: 061_lexical_index

ADITIVA. Tres colunas em ``knowledge_chunks``, nenhuma tabela nova.

ORTOGONAL A ``chunk_type``
==========================

    chunk_type      forma e funcao pedagogica LOCAL  (prosa, exercicio, tabela)
    editorial_role  funcao EDITORIAL na obra         (conteudo, gabarito, sumario)

Um gabarito pode ser ``PROSE``, ``EXERCISE`` ou ``SOLUTION`` - e e
``ANSWER_KEY`` nos tres casos. ``SOLUTION`` + ``ANSWER_KEY`` e combinacao
legitima e esperada, e e por isso que sao duas colunas e nao uma.

Sem CheckConstraint de vocabulario, pela mesma razao de ``chunk_type``: papel
novo nao deve exigir migracao.

NAO CLASSIFICADO x UNKNOWN
==========================

``editorial_detector_version`` NULO significa **nao processado por versao
alguma do detector**. Com versao preenchida e ``editorial_role = 'UNKNOWN'``,
significa **classificado, e a evidencia nao bastou**.

Sao estados diferentes e nao podem se confundir - ``UNKNOWN`` escondendo
ausencia de processamento faria o relatorio de cobertura mentir. Uma coluna de
versao resolve isso sem maquina de estados: e o mecanismo mais simples que
distingue os dois casos e ainda diz QUAL versao julgou cada chunk.

O default ``'UNKNOWN'`` com versao nula e o estado correto para o corpus
existente: ele nao foi classificado, e ``UNKNOWN`` e ELEGIVEL - nada
desaparece da busca por causa desta migracao.
"""

import sqlalchemy as sa
from alembic import op

revision = "062_editorial_role"
down_revision = "061_lexical_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_chunks",
        sa.Column(
            "editorial_role",
            sa.String(30),
            nullable=False,
            server_default="UNKNOWN",
        ),
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column("editorial_role_confidence", sa.Numeric(4, 3), nullable=True),
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column("editorial_detector_version", sa.String(20), nullable=True),
    )
    op.create_index(
        "ix_knowledge_chunks_editorial_role",
        "knowledge_chunks",
        ["editorial_role"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_chunks_editorial_role", table_name="knowledge_chunks"
    )
    op.drop_column("knowledge_chunks", "editorial_detector_version")
    op.drop_column("knowledge_chunks", "editorial_role_confidence")
    op.drop_column("knowledge_chunks", "editorial_role")
