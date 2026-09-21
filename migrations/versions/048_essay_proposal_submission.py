"""R2 - essay proposal and submission foundation.

Revision ID: 048_essay_proposal_submission
Revises: 047_transcription_enabled

Purely additive: five new tables, touches zero rows in any existing table.
"""

from alembic import op
import sqlalchemy as sa

revision = "048_essay_proposal_submission"
down_revision = "047_transcription_enabled"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "essay_prompts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("created_by_external_identity", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("school_id", "id", name="uq_essay_prompts_school_id_id"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED')", name="ck_essay_prompts_status"
        ),
    )
    op.create_index("ix_essay_prompts_school_id", "essay_prompts", ["school_id"])

    op.create_table(
        "prompt_materials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("essay_prompt_id", sa.Uuid(), nullable=False),
        sa.Column("material_type", sa.String(10), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column("storage_uri", sa.String(1024)),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["essay_prompt_id"], ["essay_prompts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "essay_prompt_id", "position", name="uq_prompt_materials_position"
        ),
        sa.CheckConstraint("material_type IN ('TEXT', 'IMAGE')", name="ck_prompt_materials_type"),
        sa.CheckConstraint(
            "(material_type = 'TEXT') = (content IS NOT NULL)",
            name="ck_prompt_materials_text_has_content",
        ),
        sa.CheckConstraint(
            "(material_type = 'IMAGE') = (storage_uri IS NOT NULL)",
            name="ck_prompt_materials_image_has_storage_uri",
        ),
    )
    op.create_index(
        "ix_prompt_materials_essay_prompt_id", "prompt_materials", ["essay_prompt_id"]
    )

    op.create_table(
        "prompt_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("essay_prompt_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_external_identity", sa.String(255), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("validation_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id", "essay_prompt_id"],
            ["essay_prompts.school_id", "essay_prompts.id"],
            ondelete="RESTRICT",
            name="fk_prompt_assignments_school_prompt",
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "class_id"],
            ["classes.school_id", "classes.id"],
            ondelete="RESTRICT",
            name="fk_prompt_assignments_school_class",
        ),
        sa.UniqueConstraint("school_id", "id", name="uq_prompt_assignments_school_id_id"),
        sa.UniqueConstraint(
            "essay_prompt_id", "class_id", name="uq_prompt_assignments_prompt_class"
        ),
        sa.CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_prompt_assignments_status"),
    )
    op.create_index("ix_prompt_assignments_school_id", "prompt_assignments", ["school_id"])
    op.create_index(
        "ix_prompt_assignments_essay_prompt_id", "prompt_assignments", ["essay_prompt_id"]
    )
    op.create_index("ix_prompt_assignments_class_id", "prompt_assignments", ["class_id"])

    op.create_table(
        "essay_submissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("essay_id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("prompt_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("anchor_mode", sa.String(20), nullable=False),
        sa.Column(
            "status", sa.String(30), nullable=False, server_default="PENDING_TRANSCRIPTION"
        ),
        sa.Column("canonical_text", sa.Text()),
        sa.Column("normalized_text_hash", sa.String(64)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id", "prompt_assignment_id"],
            ["prompt_assignments.school_id", "prompt_assignments.id"],
            ondelete="RESTRICT",
            name="fk_essay_submissions_school_assignment",
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "student_id"],
            ["students.school_id", "students.id"],
            ondelete="RESTRICT",
            name="fk_essay_submissions_school_student",
        ),
        sa.CheckConstraint("mode IN ('TYPED', 'PHOTO', 'PDF')", name="ck_essay_submissions_mode"),
        sa.CheckConstraint(
            "anchor_mode IN ('TEXT_OFFSET', 'IMAGE_REGION')",
            name="ck_essay_submissions_anchor_mode",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_TRANSCRIPTION', 'PENDING_CONFIRMATION', 'SUBMITTED', 'SUPERSEDED')",
            name="ck_essay_submissions_status",
        ),
        sa.CheckConstraint(
            "(canonical_text IS NULL) = (normalized_text_hash IS NULL)",
            name="ck_essay_submissions_canonical_text_hash_paired",
        ),
        sa.CheckConstraint(
            "canonical_text IS NULL OR anchor_mode = 'TEXT_OFFSET'",
            name="ck_essay_submissions_canonical_text_requires_text_offset",
        ),
        sa.CheckConstraint(
            "(status IN ('SUBMITTED', 'SUPERSEDED')) = (submitted_at IS NOT NULL)",
            name="ck_essay_submissions_submitted_at_presence",
        ),
    )
    op.create_index("ix_essay_submissions_school_id", "essay_submissions", ["school_id"])
    op.create_index(
        "ix_essay_submissions_prompt_assignment_id",
        "essay_submissions",
        ["prompt_assignment_id"],
    )
    op.create_index("ix_essay_submissions_student_id", "essay_submissions", ["student_id"])
    op.create_index("ix_essay_submissions_essay_id", "essay_submissions", ["essay_id"])

    op.create_table(
        "essay_submission_pages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("essay_submission_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("storage_uri", sa.String(1024), nullable=False),
        sa.Column("width", sa.Float()),
        sa.Column("height", sa.Float()),
        sa.Column("ocr_tokens", _JSON),
        sa.Column("reviewed_text", sa.Text()),
        sa.ForeignKeyConstraint(
            ["essay_submission_id"], ["essay_submissions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "essay_submission_id", "page_number", name="uq_essay_submission_pages_number"
        ),
        sa.CheckConstraint(
            "page_number >= 1", name="ck_essay_submission_pages_page_number_positive"
        ),
    )
    op.create_index(
        "ix_essay_submission_pages_essay_submission_id",
        "essay_submission_pages",
        ["essay_submission_id"],
    )


def downgrade() -> None:
    op.drop_table("essay_submission_pages")
    op.drop_table("essay_submissions")
    op.drop_table("prompt_assignments")
    op.drop_table("prompt_materials")
    op.drop_table("essay_prompts")
