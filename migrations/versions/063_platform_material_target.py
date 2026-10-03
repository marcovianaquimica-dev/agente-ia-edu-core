"""PromptMaterial aceita anexo em proposta da plataforma, nao so proposta
normal de escola.

essay_prompt_id vira opcional; platform_essay_prompt_id novo (tambem
opcional); um CHECK garante exatamente um dos dois preenchido - nunca os
dois, nunca nenhum (mesmo espirito do CHECK de prompt_assignments na
migration 062: aqui tambem nao existe "nenhum dos dois e valido", todo
material pertence a exatamente uma proposta). Um segundo indice unico de
posicao cobre platform_essay_prompt_id - o indice antigo so protegia
essay_prompt_id, e sem este novo, duas pecas de material da MESMA proposta
da plataforma poderiam ficar as duas na posicao 0.

Puramente aditiva: todo material existente hoje ja tem essay_prompt_id
preenchido e seria valido pelo novo CHECK sem nenhum backfill.

Revision ID: 063_platform_material_target
Revises: 062_prompt_assignment_target
"""

from alembic import op
import sqlalchemy as sa

revision = "063_platform_material_target"
down_revision = "062_prompt_assignment_target"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prompt_materials", sa.Column("platform_essay_prompt_id", sa.Uuid(), nullable=True)
    )
    op.alter_column("prompt_materials", "essay_prompt_id", nullable=True)
    op.create_foreign_key(
        "fk_prompt_materials_platform_essay_prompt",
        "prompt_materials",
        "platform_essay_prompts",
        ["platform_essay_prompt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_prompt_materials_target",
        "prompt_materials",
        "(essay_prompt_id IS NOT NULL AND platform_essay_prompt_id IS NULL) OR "
        "(essay_prompt_id IS NULL AND platform_essay_prompt_id IS NOT NULL)",
    )
    op.create_index(
        "uq_prompt_materials_platform_position", "prompt_materials",
        ["platform_essay_prompt_id", "position"],
        unique=True, postgresql_where=sa.text("platform_essay_prompt_id IS NOT NULL"),
        sqlite_where=sa.text("platform_essay_prompt_id IS NOT NULL"),
    )
    op.create_index(
        "ix_prompt_materials_platform_essay_prompt_id",
        "prompt_materials", ["platform_essay_prompt_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_prompt_materials_platform_essay_prompt_id", table_name="prompt_materials"
    )
    op.drop_index(
        "uq_prompt_materials_platform_position", table_name="prompt_materials"
    )
    op.drop_constraint("ck_prompt_materials_target", "prompt_materials", type_="check")
    op.drop_constraint(
        "fk_prompt_materials_platform_essay_prompt", "prompt_materials", type_="foreignkey"
    )
    op.alter_column("prompt_materials", "essay_prompt_id", nullable=False)
    op.drop_column("prompt_materials", "platform_essay_prompt_id")
