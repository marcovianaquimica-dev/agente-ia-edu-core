"""PromptAssignment aceita atribuicao a um aluno especifico, nao so turma;
registro de uso de atribuicoes.

class_id vira opcional; student_id novo (tambem opcional); um CHECK
garante exatamente um dos dois preenchido - nunca os dois, nunca nenhum
(diferente do envio em lote: aqui nao existe "escola inteira implicita",
toda atribuicao precisa de um alvo explicito). Um indice unico parcial
impede o mesmo aluno ser atribuido diretamente 2x a mesma proposta (a
unique de turma, uq_prompt_assignments_prompt_class, ja existe e
continua intocada). Tabela nova prompt_assignment_logs: 1 linha por
atribuicao feita pelo professor (nao por turma/aluno dentro dela), com um
retrato historico (target_summary) de quem foi alcancado.

Puramente aditiva: toda atribuicao existente hoje ja tem class_id
preenchido e seria valida pelo novo CHECK sem nenhum backfill.

Revision ID: 062_prompt_assignment_target
Revises: 061_essay_batch_scope
"""

from alembic import op
import sqlalchemy as sa

revision = "062_prompt_assignment_target"
down_revision = "061_essay_batch_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prompt_assignments", sa.Column("student_id", sa.Uuid(), nullable=True)
    )
    op.alter_column("prompt_assignments", "class_id", nullable=True)
    op.create_foreign_key(
        "fk_prompt_assignments_school_student",
        "prompt_assignments",
        "students",
        ["school_id", "student_id"],
        ["school_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_prompt_assignments_target",
        "prompt_assignments",
        "(class_id IS NOT NULL AND student_id IS NULL) OR "
        "(class_id IS NULL AND student_id IS NOT NULL)",
    )
    op.create_index(
        "uq_prompt_assignments_prompt_student", "prompt_assignments",
        ["essay_prompt_id", "student_id"],
        unique=True, postgresql_where=sa.text("student_id IS NOT NULL"),
        sqlite_where=sa.text("student_id IS NOT NULL"),
    )
    op.create_index(
        "ix_prompt_assignments_student_id", "prompt_assignments", ["student_id"]
    )

    op.create_table(
        "prompt_assignment_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("essay_prompt_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_external_identity", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_summary", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id", "essay_prompt_id"],
            ["essay_prompts.school_id", "essay_prompts.id"],
            ondelete="RESTRICT",
            name="fk_prompt_assignment_logs_school_prompt",
        ),
    )
    op.create_index(
        "ix_prompt_assignment_logs_essay_prompt_id",
        "prompt_assignment_logs", ["essay_prompt_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_prompt_assignment_logs_essay_prompt_id", table_name="prompt_assignment_logs")
    op.drop_table("prompt_assignment_logs")

    op.drop_index("ix_prompt_assignments_student_id", table_name="prompt_assignments")
    op.drop_index("uq_prompt_assignments_prompt_student", table_name="prompt_assignments")
    op.drop_constraint("ck_prompt_assignments_target", "prompt_assignments", type_="check")
    op.drop_constraint(
        "fk_prompt_assignments_school_student", "prompt_assignments", type_="foreignkey"
    )
    op.alter_column("prompt_assignments", "class_id", nullable=False)
    op.drop_column("prompt_assignments", "student_id")
