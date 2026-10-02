"""Propostas de redacao da plataforma (admin, cross-escola).

Revision ID: 058_platform_essay_prompts
Revises: 057_essay_batch_upload

Puramente aditiva: uma tabela que nao existia e uma coluna NULLABLE em
essay_prompts (default NULL = toda proposta ja cadastrada continua sendo uma
proposta normal de professor, sem nenhuma mudanca de comportamento). Nenhuma
linha existente e tocada.

A UNIQUE (school_id, materialized_from_platform_prompt_id) e o que impede no
banco duas materializacoes da mesma proposta da plataforma na mesma escola -
inclusive numa corrida de duas requisicoes simultaneas, que e exatamente o
caso que o buscar-ou-criar do servico trata capturando IntegrityError. UNIQUE
comum basta, sem indice parcial: linhas com NULL nessa coluna (as propostas
normais) nunca conflitam entre si, porque NULL nunca e igual a NULL para fins
de unicidade.

A FK ON DELETE RESTRICT e defensiva: o admin arquiva, nunca apaga, entao ela
nao deve ser exercitada no fluxo normal.
"""

from alembic import op
import sqlalchemy as sa

revision = "058_platform_essay_prompts"
down_revision = "057_essay_batch_upload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_essay_prompts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by_external_identity", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')", name="ck_platform_essay_prompts_status"
        ),
    )

    op.add_column(
        "essay_prompts",
        sa.Column("materialized_from_platform_prompt_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_essay_prompts_materialized_from_platform_prompt",
        "essay_prompts",
        "platform_essay_prompts",
        ["materialized_from_platform_prompt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_essay_prompts_school_materialized_from",
        "essay_prompts",
        ["school_id", "materialized_from_platform_prompt_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_essay_prompts_school_materialized_from", "essay_prompts", type_="unique"
    )
    op.drop_constraint(
        "fk_essay_prompts_materialized_from_platform_prompt",
        "essay_prompts",
        type_="foreignkey",
    )
    op.drop_column("essay_prompts", "materialized_from_platform_prompt_id")
    op.drop_table("platform_essay_prompts")
