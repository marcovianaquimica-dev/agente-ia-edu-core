"""Enhance Question model for Fase 17: governance, multi-tenant, classification.

Revision ID: 016_question_governance
Revises: 015_user_invitations
Create Date: 2026-08-31

This migration:
1. Adds critical fields to questions table for multi-tenant, governance, origin tracking
2. Creates question_status_transitions table for audit trail
3. Creates question_approvals table for approval workflow
4. Creates question_eligibility table for eligibility caching
5. Enhances question_classifications with new fields
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '016_question_governance'
down_revision = '015_user_invitations'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add columns to questions table
    op.add_column('questions', sa.Column('external_id', sa.String(255), nullable=True))
    op.add_column('questions', sa.Column('question_type', sa.String(50), nullable=False, server_default='MULTIPLE_CHOICE'))
    op.add_column('questions', sa.Column('school_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('questions', sa.Column('author_external_id', sa.String(255), nullable=True))
    op.add_column('questions', sa.Column('owner_external_id', sa.String(255), nullable=True))
    op.add_column('questions', sa.Column('origin_type', sa.String(50), nullable=False, server_default='PLATFORM'))
    op.add_column('questions', sa.Column('status', sa.String(30), nullable=False, server_default='DRAFT'))
    op.add_column('questions', sa.Column('visibility_scope', sa.String(50), nullable=False, server_default='PRIVATE'))
    op.add_column('questions', sa.Column('created_by_external_identity', sa.String(255), nullable=True))
    op.add_column('questions', sa.Column('metadata_', postgresql.JSONB(), nullable=False, server_default='{}'))
    op.add_column('questions', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))

    # 2. Add foreign key from questions.school_id to schools.id
    op.create_foreign_key(
        'fk_questions_school',
        'questions',
        'schools',
        ['school_id'],
        ['id'],
        ondelete='RESTRICT',
    )

    # 3. Add indexes to questions table
    op.create_index('ix_questions_school_id', 'questions', ['school_id'])
    op.create_index('ix_questions_status', 'questions', ['status'])
    op.create_index('ix_questions_origin_type', 'questions', ['origin_type'])
    op.create_index('ix_questions_created_by', 'questions', ['created_by_external_identity'])
    op.create_index('ix_questions_external_id', 'questions', ['external_id'])

    # 4. Add check constraints to questions table
    op.create_check_constraint(
        'ck_questions_origin_type',
        'questions',
        "origin_type IN ('PLATFORM', 'SCHOOL', 'TEACHER', 'IMPORTED', 'GENERATED')"
    )
    op.create_check_constraint(
        'ck_questions_status',
        'questions',
        "status IN ('DRAFT', 'REVIEW', 'APPROVED', 'PUBLISHED', 'ARCHIVED', 'REJECTED')"
    )
    op.create_check_constraint(
        'ck_questions_visibility_scope',
        'questions',
        "visibility_scope IN ('PRIVATE', 'CLASSROOM', 'SCHOOL', 'PUBLIC')"
    )

    # 5. Create question_status_transitions table
    op.create_table(
        'question_status_transitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('question_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('from_status', sa.String(30), nullable=True),
        sa.Column('to_status', sa.String(30), nullable=False),
        sa.Column('performed_by_external_id', sa.String(255), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('metadata_', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='RESTRICT'),
    )
    op.create_index('ix_question_status_transitions_question_id', 'question_status_transitions', ['question_id'])
    op.create_index('ix_question_status_transitions_to_status', 'question_status_transitions', ['to_status'])
    op.create_index('ix_question_status_transitions_created_at', 'question_status_transitions', ['created_at'])

    # 6. Create question_approvals table
    op.create_table(
        'question_approvals',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('question_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('version_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('reviewer_external_id', sa.String(255), nullable=False),
        sa.Column('decision', sa.String(20), nullable=False),
        sa.Column('feedback_text', sa.Text(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('metadata_', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['version_id'], ['question_versions.id'], ondelete='SET NULL'),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED', 'FEEDBACK')",
            name='ck_question_approvals_decision'
        ),
    )
    op.create_index('ix_question_approvals_question_id', 'question_approvals', ['question_id'])
    op.create_index('ix_question_approvals_decision', 'question_approvals', ['decision'])
    op.create_index('ix_question_approvals_created_at', 'question_approvals', ['created_at'])

    # 7. Create question_eligibility table
    op.create_table(
        'question_eligibility',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('question_id', postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column('is_eligible', sa.Boolean(), nullable=False, default=False),
        sa.Column('reasons', postgresql.ARRAY(sa.String(255)), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('valid_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('metadata_', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='RESTRICT'),
    )
    op.create_index('ix_question_eligibility_question_id', 'question_eligibility', ['question_id'])
    op.create_index('ix_question_eligibility_is_eligible', 'question_eligibility', ['is_eligible'])
    op.create_index('ix_question_eligibility_valid_until', 'question_eligibility', ['valid_until'])

    # 8. Enhance question_classifications with new fields
    op.add_column('question_classifications', sa.Column('classification_confidence', sa.Numeric(3, 2), nullable=True))
    op.add_column('question_classifications', sa.Column('classified_by', sa.String(50), nullable=False, server_default='MANUAL'))
    op.add_column('question_classifications', sa.Column('ai_model', sa.String(255), nullable=True))
    op.add_column('question_classifications', sa.Column('human_verified', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('question_classifications', sa.Column('approval_status', sa.String(20), nullable=False, server_default='APPROVED'))

    # Add constraint for classification_confidence
    op.create_check_constraint(
        'ck_question_classifications_confidence',
        'question_classifications',
        'classification_confidence IS NULL OR (classification_confidence >= 0.0 AND classification_confidence <= 1.0)'
    )

    # Add constraint for classified_by
    op.create_check_constraint(
        'ck_question_classifications_classified_by',
        'question_classifications',
        "classified_by IN ('TEACHER', 'PLATFORM', 'AI', 'MANUAL')"
    )

    # Add constraint for approval_status
    op.create_check_constraint(
        'ck_question_classifications_approval_status',
        'question_classifications',
        "approval_status IN ('PENDING', 'APPROVED', 'REJECTED')"
    )


def downgrade() -> None:
    # Downgrade in reverse order

    # 1. Remove constraints from question_classifications
    op.drop_constraint('ck_question_classifications_approval_status', 'question_classifications', type_='check')
    op.drop_constraint('ck_question_classifications_classified_by', 'question_classifications', type_='check')
    op.drop_constraint('ck_question_classifications_confidence', 'question_classifications', type_='check')

    # 2. Drop columns from question_classifications
    op.drop_column('question_classifications', 'approval_status')
    op.drop_column('question_classifications', 'human_verified')
    op.drop_column('question_classifications', 'ai_model')
    op.drop_column('question_classifications', 'classified_by')
    op.drop_column('question_classifications', 'classification_confidence')

    # 3. Drop question_eligibility table
    op.drop_table('question_eligibility')

    # 4. Drop question_approvals table
    op.drop_table('question_approvals')

    # 5. Drop question_status_transitions table
    op.drop_table('question_status_transitions')

    # 6. Drop constraints from questions
    op.drop_constraint('ck_questions_visibility_scope', 'questions', type_='check')
    op.drop_constraint('ck_questions_status', 'questions', type_='check')
    op.drop_constraint('ck_questions_origin_type', 'questions', type_='check')

    # 7. Drop indexes from questions
    op.drop_index('ix_questions_external_id', 'questions')
    op.drop_index('ix_questions_created_by', 'questions')
    op.drop_index('ix_questions_origin_type', 'questions')
    op.drop_index('ix_questions_status', 'questions')
    op.drop_index('ix_questions_school_id', 'questions')

    # 8. Drop foreign key from questions
    op.drop_constraint('fk_questions_school', 'questions', type_='foreignkey')

    # 9. Drop columns from questions
    op.drop_column('questions', 'updated_at')
    op.drop_column('questions', 'metadata_')
    op.drop_column('questions', 'created_by_external_identity')
    op.drop_column('questions', 'visibility_scope')
    op.drop_column('questions', 'status')
    op.drop_column('questions', 'origin_type')
    op.drop_column('questions', 'owner_external_id')
    op.drop_column('questions', 'author_external_id')
    op.drop_column('questions', 'school_id')
    op.drop_column('questions', 'question_type')
    op.drop_column('questions', 'external_id')
