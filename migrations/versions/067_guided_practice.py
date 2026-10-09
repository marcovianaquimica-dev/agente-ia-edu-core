"""Pratica guiada: a interacao assistida, deliberadamente FORA da evidencia.

Revision ID: 067_guided_practice
Revises: 066_merge_063_lineages

ADITIVA. Uma tabela nova. Nenhuma coluna existente muda, nada e apagado.

POR QUE UMA TABELA, E NAO UM CAMPO EM ALGO QUE JA EXISTE
=========================================================
Auditei antes de criar:

    activity_attempts     nao tem metadata, e e o caminho da EVIDENCIA
    activity_answers      idem
    material_progress     uma linha por (aluno, versao), sem onde caber
    study_sessions        tem `metadata`, mas usar JSON como deposito para
                          evitar modelagem e exatamente o que nao se deve

A informacao que a pratica guiada precisa responder - quantas tentativas,
quantos niveis de ajuda, qual o maior nivel alcancado, se resolveu sozinho -
tem forma propria e e consultada por aluno/conteudo/habilidade. Isso e uma
tabela.

E MAIS IMPORTANTE: PENDURAR ISSO NO CAMINHO DA EVIDENCIA SERIA O RISCO
=======================================================================
`_Grain.add`, no mapa de dominio, conta TODA resposta respondida em
`answered`/`correct`; a origem so detalha o `origin_breakdown`. Nao existe
ponderacao de evidencia assistida.

Entao acertar com ajuda maxima entraria como acerto igual a acertar sozinho.
"Conseguiu com ajuda" viraria "domina" sem ninguem decidir isso.

Esta tabela e a resposta fail-closed: a interacao assistida fica num lugar
que o motor de dominio NAO le - a mesma escolha que `material_progress` ja
fez para a leitura. A comprovacao continua exigindo uma pratica autonoma.

    ler material        -> material_progress      -> nao e evidencia
    pratica guiada      -> guided_practice_items  -> nao e evidencia
    pratica autonoma    -> activity_*             -> E evidencia

UMA LINHA POR (ALUNO, ITEM)
============================
Idempotente, como `material_progress`: a retomada reabre a mesma linha em vez
de criar uma segunda tentativa que zeraria os contadores pedagogicos.

`content_code`, `skill` e `item_key` sao TEXTO, sem FK, pelo mesmo motivo que
`study_sessions.objective_assignment_id` nao tem: isto e registro historico
do que aconteceu com o aluno. Apagar um item do conteudo nao pode apagar nem
travar o fato de que ele praticou.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "067_guided_practice"
down_revision = "066_merge_063_lineages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guided_practice_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("student_external_id", sa.String(length=255), nullable=False),
        # O item do conteudo estruturado do Nucleo. Sem FK: ver docstring.
        sa.Column("item_key", sa.String(length=100), nullable=False),
        sa.Column("content_code", sa.String(length=100), nullable=False),
        # A micro-habilidade que a intervencao atacou, quando a amostra
        # sustentou identifica-la. NULL = o diagnostico nao distinguiu, e
        # inventar uma seria pior que admitir.
        sa.Column("skill", sa.String(length=100), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hints_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_hint_level", sa.Integer(), nullable=False, server_default="0"),
        # Resolveu ANTES de qualquer ajuda. E a unica coluna que distingue
        # "conseguiu" de "conseguiu sozinho" - e e por isso que ela existe.
        sa.Column("solved_unaided", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("completed", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("student_external_id", "item_key",
                            name="uq_guided_practice_student_item"),
        sa.CheckConstraint("attempts >= 0", name="ck_guided_practice_attempts"),
        sa.CheckConstraint("hints_used >= 0", name="ck_guided_practice_hints"),
        sa.CheckConstraint("max_hint_level >= 0",
                           name="ck_guided_practice_max_hint"),
        # Resolver sem ajuda e incompativel com ter usado ajuda. Sem esta
        # trava, um erro de escrita poderia produzir "resolveu sozinho" num
        # registro com quatro dicas - e seria lido como dominio.
        sa.CheckConstraint("NOT solved_unaided OR hints_used = 0",
                           name="ck_guided_practice_unaided_has_no_hints"),
    )
    op.create_index("ix_guided_practice_student_content",
                    "guided_practice_items",
                    ["student_external_id", "content_code"])


def downgrade() -> None:
    op.drop_index("ix_guided_practice_student_content",
                  table_name="guided_practice_items")
    op.drop_table("guided_practice_items")
