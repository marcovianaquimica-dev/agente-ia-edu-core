"""Create school-scoped reception pre-registration records.

Revision ID: 019_reception_candidates
Revises: 018_pedagogical_universe
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "019_reception_candidates"
down_revision = "018_pedagogical_universe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_user_school_links_role", "user_school_links", type_="check")
    op.create_check_constraint(
        "ck_user_school_links_role",
        "user_school_links",
        "role IN ('PLATFORM_ADMIN', 'DIRECTOR', 'COORDINATOR', 'SECRETARY', 'TEACHER', 'STUDENT')",
    )
    op.drop_constraint("ck_user_invitations_role", "user_invitations", type_="check")
    op.create_check_constraint(
        "ck_user_invitations_role",
        "user_invitations",
        "role IN ('PLATFORM_ADMIN', 'DIRECTOR', 'COORDINATOR', 'SECRETARY', 'TEACHER', 'STUDENT')",
    )
    op.alter_column(
        "user_invitations",
        "metadata_",
        new_column_name="metadata",
        existing_type=postgresql.JSON(),
        type_=postgresql.JSONB(),
        existing_nullable=False,
        postgresql_using="metadata_::jsonb",
    )
    op.create_table(
        "reception_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("preferred_name", sa.String(length=255), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("guardian_name", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("academic_year", sa.String(length=10), nullable=False),
        sa.Column("unit_id", sa.String(length=255), nullable=False),
        sa.Column("segment_id", sa.String(length=255), nullable=False),
        sa.Column("grade_level", sa.String(length=255), nullable=False),
        sa.Column("classroom_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="PRE_REGISTRATION"),
        sa.Column("invitation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_student_id", sa.String(length=255), nullable=True),
        sa.Column("released_by_external_id", sa.String(length=255), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_external_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('PRE_REGISTRATION', 'DIAGNOSTIC_RELEASED', 'CONVERTED')",
            name="ck_reception_candidates_status",
        ),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invitation_id"], ["user_invitations.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("invitation_id", name="uq_reception_candidates_invitation_id"),
        sa.UniqueConstraint("school_id", "email", name="uq_reception_candidates_school_email"),
        sa.UniqueConstraint("school_id", "phone", name="uq_reception_candidates_school_phone"),
    )
    op.create_index("ix_reception_candidates_school_created", "reception_candidates", ["school_id", "created_at"])
    op.create_index("ix_reception_candidates_school_name", "reception_candidates", ["school_id", "full_name"])
    op.create_index("ix_reception_candidates_email", "reception_candidates", ["email"])
    op.create_index("ix_reception_candidates_phone", "reception_candidates", ["phone"])


def downgrade() -> None:
    op.drop_table("reception_candidates")
    op.alter_column(
        "user_invitations",
        "metadata",
        new_column_name="metadata_",
        existing_type=postgresql.JSONB(),
        type_=postgresql.JSON(),
        existing_nullable=False,
        postgresql_using="metadata::json",
    )
    op.drop_constraint("ck_user_invitations_role", "user_invitations", type_="check")
    op.create_check_constraint(
        "ck_user_invitations_role",
        "user_invitations",
        "role IN ('PLATFORM_ADMIN', 'DIRECTOR', 'COORDINATOR', 'TEACHER', 'STUDENT')",
    )
    op.drop_constraint("ck_user_school_links_role", "user_school_links", type_="check")
    op.create_check_constraint(
        "ck_user_school_links_role",
        "user_school_links",
        "role IN ('PLATFORM_ADMIN', 'DIRECTOR', 'COORDINATOR', 'TEACHER', 'STUDENT')",
    )