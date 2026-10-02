"""Envio em lote aceita escopo por serie ou escola inteira, nao so turma.

class_id vira opcional; grade_level_id novo (tambem opcional); um CHECK
garante exatamente um dos dois preenchido, ou os dois NULL (escola
inteira) - nunca os dois setados ao mesmo tempo. Puramente aditiva: todo
lote existente hoje ja tem class_id preenchido e grade_level_id NULL, que
e exatamente um dos casos validos do novo CHECK - nenhuma linha existente
precisa de backfill nem fica invalida.

Revision ID: 061_essay_batch_scope
Revises: 060_mass_correction_cancelling
"""

from alembic import op
import sqlalchemy as sa

revision = "061_essay_batch_scope"
down_revision = "060_mass_correction_cancelling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "essay_batch_uploads", sa.Column("grade_level_id", sa.Uuid(), nullable=True)
    )
    op.alter_column("essay_batch_uploads", "class_id", nullable=True)
    op.create_foreign_key(
        "fk_essay_batch_uploads_school_grade_level",
        "essay_batch_uploads",
        "grade_levels",
        ["school_id", "grade_level_id"],
        ["school_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_essay_batch_uploads_scope",
        "essay_batch_uploads",
        "(class_id IS NOT NULL AND grade_level_id IS NULL) OR "
        "(class_id IS NULL AND grade_level_id IS NOT NULL) OR "
        "(class_id IS NULL AND grade_level_id IS NULL)",
    )
    op.create_index(
        "ix_essay_batch_uploads_grade_level_id", "essay_batch_uploads", ["grade_level_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_essay_batch_uploads_grade_level_id", table_name="essay_batch_uploads")
    op.drop_constraint(
        "ck_essay_batch_uploads_scope", "essay_batch_uploads", type_="check"
    )
    op.drop_constraint(
        "fk_essay_batch_uploads_school_grade_level", "essay_batch_uploads", type_="foreignkey"
    )
    op.alter_column("essay_batch_uploads", "class_id", nullable=False)
    op.drop_column("essay_batch_uploads", "grade_level_id")
