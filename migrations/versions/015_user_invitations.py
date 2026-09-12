"""Add UserInvitation model for onboarding (Fase 16)

Revision ID: 015_user_invitations
Revises: 014_material_school_scope
Create Date: 2025-08-31 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '015_user_invitations'
down_revision = '014_material_school_scope'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_invitations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('school_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('token', sa.String(255), nullable=False),
        sa.Column('external_email', sa.String(255), nullable=False),
        sa.Column('external_user_id', sa.String(255), nullable=True),
        sa.Column('display_name', sa.String(255), nullable=True),
        sa.Column('role', sa.String(50), nullable=False),
        sa.Column('scope_type', sa.String(50), nullable=False),
        sa.Column('scope_external_id', sa.String(255), nullable=True),
        sa.Column('status', sa.String(30), nullable=False, server_default='PENDING'),
        sa.Column('invited_by_external_id', sa.String(255), nullable=False),
        sa.Column('accepted_by_external_id', sa.String(255), nullable=True),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expired_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('metadata_', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('PLATFORM_ADMIN', 'DIRECTOR', 'COORDINATOR', 'TEACHER', 'STUDENT')",
            name='ck_user_invitations_role',
        ),
        sa.CheckConstraint(
            "scope_type IN ('PLATFORM', 'SCHOOL', 'UNIT', 'SEGMENT', 'GRADE_LEVEL', 'CLASSROOM')",
            name='ck_user_invitations_scope_type',
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'ACTIVATED', 'EXPIRED', 'CANCELLED')",
            name='ck_user_invitations_status',
        ),
        sa.ForeignKeyConstraint(['school_id'], ['schools.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token', name='uq_user_invitations_token'),
        sa.UniqueConstraint(
            'school_id', 'external_email', 'role',
            name='uq_user_invitations_school_email_role',
        ),
    )

    op.create_index('ix_user_invitations_school_id', 'user_invitations', ['school_id'])
    op.create_index('ix_user_invitations_status', 'user_invitations', ['status'])
    op.create_index('ix_user_invitations_expires_at', 'user_invitations', ['expires_at'])
    op.create_index('ix_user_invitations_created_at', 'user_invitations', ['created_at'])


def downgrade() -> None:
    op.drop_table('user_invitations')
