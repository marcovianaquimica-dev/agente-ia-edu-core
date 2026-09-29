"""Envio em lote de redacoes fisicas: duas tabelas novas + logo da escola.

Revision ID: 057_essay_batch_upload
Revises: 055_essay_prompt_soft_delete

Puramente aditiva: duas tabelas que nao existiam e uma coluna nullable em
schools (default NULL = toda escola atual continua sem logo, e a folha de
resposta gerada sai sem logo em vez de falhar). Nenhuma linha existente e
tocada, nenhuma tabela existente muda de forma alem dessa coluna.

As FKs compostas (school_id, essay_prompt_id) e (school_id, class_id) sao o que
impede no banco um lote cuja proposta e de uma escola e cuja turma e de outra -
mesma convencao de prompt_assignments.

Numerada 057 em 2026-09-29 pra nao colidir com uma migration de outra sessao
concorrente trabalhando na mesma arvore de checkouts (056_material_assignments,
tambem filha de 055) - mas encadeada direto apos 055, nao apos ela: esta branch
nao usa nada do que 056_material_assignments cria, entao nao faz sentido esta
migration depender de codigo de outra leva que pode mudar/renumerar antes de
mergear. Quando as duas branches se juntarem, o Alembic resolve as duas pontas
(055->056 e 055->057) como um merge point normal.
"""

from alembic import op
import sqlalchemy as sa

revision = "057_essay_batch_upload"
down_revision = "055_essay_prompt_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schools", sa.Column("logo_storage_uri", sa.String(length=500), nullable=True))

    op.create_table(
        "essay_batch_uploads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("essay_prompt_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by_external_identity", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total_pages", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["school_id", "essay_prompt_id"],
            ["essay_prompts.school_id", "essay_prompts.id"],
            ondelete="RESTRICT",
            name="fk_essay_batch_uploads_school_prompt",
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "class_id"],
            ["classes.school_id", "classes.id"],
            ondelete="RESTRICT",
            name="fk_essay_batch_uploads_school_class",
        ),
        sa.UniqueConstraint("school_id", "id", name="uq_essay_batch_uploads_school_id_id"),
        sa.CheckConstraint("status IN ('PROCESSING', 'DONE')", name="ck_essay_batch_uploads_status"),
        sa.CheckConstraint(
            "total_pages >= 0", name="ck_essay_batch_uploads_total_pages_non_negative"
        ),
    )
    op.create_index("ix_essay_batch_uploads_school_id", "essay_batch_uploads", ["school_id"])
    op.create_index(
        "ix_essay_batch_uploads_essay_prompt_id", "essay_batch_uploads", ["essay_prompt_id"]
    )
    op.create_index("ix_essay_batch_uploads_class_id", "essay_batch_uploads", ["class_id"])

    op.create_table(
        "essay_batch_pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("storage_uri", sa.String(length=500), nullable=False),
        sa.Column("ocr_name_raw", sa.String(length=255), nullable=True),
        sa.Column("ocr_cpf_raw", sa.String(length=20), nullable=True),
        sa.Column("ocr_body_text", sa.Text(), nullable=True),
        sa.Column("matched_student_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("essay_submission_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["essay_batch_uploads.id"], ondelete="CASCADE",
            name="fk_essay_batch_pages_batch",
        ),
        sa.ForeignKeyConstraint(
            ["matched_student_id"], ["students.id"], ondelete="RESTRICT",
            name="fk_essay_batch_pages_student",
        ),
        sa.ForeignKeyConstraint(
            ["essay_submission_id"], ["essay_submissions.id"], ondelete="RESTRICT",
            name="fk_essay_batch_pages_submission",
        ),
        sa.UniqueConstraint("batch_id", "page_number", name="uq_essay_batch_pages_number"),
        sa.CheckConstraint("page_number >= 1", name="ck_essay_batch_pages_page_number_positive"),
        sa.CheckConstraint(
            "status IN ('MATCHED_AUTO', 'NEEDS_REVIEW', 'RESOLVED_MANUAL')",
            name="ck_essay_batch_pages_status",
        ),
    )
    op.create_index("ix_essay_batch_pages_batch_id", "essay_batch_pages", ["batch_id"])
    op.create_index(
        "ix_essay_batch_pages_matched_student_id", "essay_batch_pages", ["matched_student_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_essay_batch_pages_matched_student_id", table_name="essay_batch_pages")
    op.drop_index("ix_essay_batch_pages_batch_id", table_name="essay_batch_pages")
    op.drop_table("essay_batch_pages")
    op.drop_index("ix_essay_batch_uploads_class_id", table_name="essay_batch_uploads")
    op.drop_index("ix_essay_batch_uploads_essay_prompt_id", table_name="essay_batch_uploads")
    op.drop_index("ix_essay_batch_uploads_school_id", table_name="essay_batch_uploads")
    op.drop_table("essay_batch_uploads")
    op.drop_column("schools", "logo_storage_uri")
