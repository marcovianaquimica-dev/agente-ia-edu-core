"""Create platform administration and multi-tenancy domain (Phase 12A).

Revision ID: 011_platform_administration
Revises: 010_video_discovery
Create Date: 2026-08-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "011_platform_administration"
down_revision: Union[str, None] = "010_video_discovery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create schools table
    op.create_table(
        "schools",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("short_name", sa.String(length=100), nullable=True),
        sa.Column("external_identifier", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="ACTIVE"),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')",
            name="ck_schools_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_schools"),
        sa.UniqueConstraint("code", name="uq_schools_code"),
    )
    op.create_index("ix_schools_code", "schools", ["code"])
    op.create_index("ix_schools_status", "schools", ["status"])

    # 2. Create school_modules table
    op.create_table(
        "school_modules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("module_key", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "module_key IN ('AGENTE_IA_EDU', 'REDACAO_IA')",
            name="ck_school_modules_module_key",
        ),
        sa.ForeignKeyConstraint(
            ["school_id"],
            ["schools.id"],
            name="fk_school_modules_school_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_school_modules"),
        sa.UniqueConstraint("school_id", "module_key", name="uq_school_modules_school_key"),
    )
    op.create_index("ix_school_modules_school_id", "school_modules", ["school_id"])

    # 3. Create user_school_links table
    op.create_table(
        "user_school_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_user_id", sa.String(length=255), nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("scope_type", sa.String(length=30), nullable=False),
        sa.Column("scope_external_id", sa.String(length=255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('PLATFORM_ADMIN', 'DIRECTOR', 'COORDINATOR', 'TEACHER', 'STUDENT')",
            name="ck_user_school_links_role",
        ),
        sa.CheckConstraint(
            "scope_type IN ('PLATFORM', 'SCHOOL', 'UNIT', 'SEGMENT', 'GRADE_LEVEL', 'CLASSROOM')",
            name="ck_user_school_links_scope_type",
        ),
        sa.ForeignKeyConstraint(
            ["school_id"],
            ["schools.id"],
            name="fk_user_school_links_school_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_school_links"),
    )
    op.create_index("ix_user_school_links_external_user_id", "user_school_links", ["external_user_id"])
    op.create_index("ix_user_school_links_school_id", "user_school_links", ["school_id"])
    op.create_index("ix_user_school_links_role", "user_school_links", ["role"])

    # 4. Create admin_audit_logs table
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("performed_by_external_id", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id"],
            ["schools.id"],
            name="fk_admin_audit_logs_school_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_admin_audit_logs"),
    )
    op.create_index("ix_admin_audit_logs_performed_by", "admin_audit_logs", ["performed_by_external_id"])
    op.create_index("ix_admin_audit_logs_school_id", "admin_audit_logs", ["school_id"])
    op.create_index("ix_admin_audit_logs_action", "admin_audit_logs", ["action"])
    op.create_index("ix_admin_audit_logs_created_at", "admin_audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_admin_audit_logs_created_at", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_action", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_school_id", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_performed_by", table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")

    op.drop_index("ix_user_school_links_role", table_name="user_school_links")
    op.drop_index("ix_user_school_links_school_id", table_name="user_school_links")
    op.drop_index("ix_user_school_links_external_user_id", table_name="user_school_links")
    op.drop_table("user_school_links")

    op.drop_index("ix_school_modules_school_id", table_name="school_modules")
    op.drop_table("school_modules")

    op.drop_index("ix_schools_status", table_name="schools")
    op.drop_index("ix_schools_code", table_name="schools")
    op.drop_table("schools")
