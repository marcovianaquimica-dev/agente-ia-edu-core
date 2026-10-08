"""CEREBRO / Fase 6 passo 3 - ativacao, rollback e historico dos embeddings.

Revision ID: 063_embedding_activation
Revises: 062_editorial_role

ADITIVA. Um indice e uma tabela. Nenhuma coluna existente muda.

1. A TRAVA TOPOLOGICA: UM VETOR ATIVO POR CHUNK, NO ACERVO INTEIRO
==================================================================

    CREATE UNIQUE INDEX ... ON knowledge_chunk_embeddings (chunk_id)
    WHERE is_active

Repare no que ele NAO diz: nao diz ``space_id``. E proposital.

O requisito e que uma busca jamais observe duas representacoes ativas do
mesmo chunk em espacos diferentes. Um ``if`` no servico de busca nao
garantiria isso - garantiria apenas que o servico de busca de hoje nao erra.
Com este indice, o estado proibido deixa de ser representavel: nem a troca de
espaco, nem um backfill concorrente, nem um INSERT manual conseguem
cria-lo. Mesma disciplina de ``uq_knowledge_embedding_spaces_single_active``
e das travas da Fase 1.

Consequencia operacional, e e ela que define o protocolo de troca: indice
unico PARCIAL nao pode ser DEFERRABLE no PostgreSQL. Entao a troca e sempre
**desativa o antigo, depois ativa o novo**, nesta ordem, na MESMA transacao.
A ordem inversa violaria o indice no meio da transacao; a mesma transacao e o
que impede qualquer observador externo de ver a janela vazia entre as duas
instrucoes.

Seguro agora: nenhum embedding foi gerado ainda no piloto - a Fase 6 e a
primeira a gerar. Num acervo que ja tivesse dois ativos para o mesmo chunk,
esta migracao falharia, e falhar seria o comportamento correto.

2. O HISTORICO: ``knowledge_embedding_activations``
===================================================

Trocar de espaco de embedding muda o significado de TODA busca vetorial do
sistema. "Por que o ranking mudou na quinta-feira?" precisa ter resposta, e
``activated_at`` na linha do espaco nao responde - ele guarda apenas a ultima
vez, e e sobrescrito no rollback.

Esta tabela grava o evento, nao o estado: quem ativou o que, sobre qual
populacao, com que cobertura medida, e quais portoes foram eventualmente
aceitos degradados num rollback de emergencia. Append-only por natureza.

``previous_space_id`` e o que torna a cadeia reconstruivel: a sequencia de
ativacoes e uma lista ligada do historico do acervo.

3. O QUE ESTA MIGRACAO NAO FAZ
==============================

Nao apaga vetor nenhum, e nao cria caminho para apagar. Desativar e UPDATE;
o vetor antigo permanece, com o ``text_hash`` do texto que ele representa.
E isso que torna o rollback uma reativacao barata em vez de uma nova geracao
paga.
"""

import sqlalchemy as sa
from alembic import op

from agente_ia_edu.db.types import JSONBCompatible

revision = "063_embedding_activation"
down_revision = "062_editorial_role"
branch_labels = None
depends_on = None

ONE_ACTIVE_PER_CHUNK = "uq_knowledge_chunk_embeddings_one_active_per_chunk"


def upgrade() -> None:
    op.create_index(
        ONE_ACTIVE_PER_CHUNK,
        "knowledge_chunk_embeddings",
        ["chunk_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active"),
    )

    op.create_table(
        "knowledge_embedding_activations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # O espaco que PASSOU a valer neste evento.
        sa.Column("space_id", sa.Uuid(), nullable=False),
        # O que valia antes. NULL no primeiro evento do acervo.
        sa.Column("previous_space_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(20), nullable=False),
        # A politica de backfill sob a qual a prontidao foi avaliada. Sem
        # isto, "cobertura 100%" nao significa nada: 100% de QUAL populacao?
        sa.Column("policy", sa.String(40), nullable=False),
        sa.Column("expected_population", sa.Integer(), nullable=False),
        sa.Column("eligible_chunks", sa.Integer(), nullable=False),
        sa.Column("embedded", sa.Integer(), nullable=False),
        sa.Column("missing", sa.Integer(), nullable=False),
        sa.Column("stale", sa.Integer(), nullable=False),
        sa.Column("dimension_violations", sa.Integer(), nullable=False),
        sa.Column("activated_rows", sa.Integer(), nullable=False),
        sa.Column("deactivated_rows", sa.Integer(), nullable=False),
        # Rollback de emergencia que passou por cima de um portao. Falso e o
        # caso normal; verdadeiro TEM de deixar rastro, com os portoes
        # violados em ``violations``.
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("violations", JSONBCompatible, nullable=False),
        sa.Column("actor", sa.String(100), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "action IN ('ACTIVATE', 'ROLLBACK')",
            name="ck_knowledge_embedding_activations_action",
        ),
        # RESTRICT pela mesma razao do resto do subsistema: apagar um espaco
        # citado pelo historico apagaria a explicacao de uma mudanca de
        # ranking.
        sa.ForeignKeyConstraint(
            ["space_id"], ["knowledge_embedding_spaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["previous_space_id"], ["knowledge_embedding_spaces.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_knowledge_embedding_activations_created_at",
        "knowledge_embedding_activations",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_embedding_activations_created_at",
        table_name="knowledge_embedding_activations",
    )
    op.drop_table("knowledge_embedding_activations")
    op.drop_index(ONE_ACTIVE_PER_CHUNK, table_name="knowledge_chunk_embeddings")
