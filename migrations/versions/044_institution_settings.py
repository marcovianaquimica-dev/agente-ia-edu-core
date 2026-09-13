"""R0 Fase 1 - per-institution settings and versioned visual identity.

Revision ID: 044_institution_settings
Revises: 043_user_school_link_entities

Purely additive: two new tables, no existing table touched.

Typed columns with CHECK constraints rather than JSON, because R1's final review
proved that validating only in code lets a wrong value through when the writer
hardcodes it. Here the stake is a school running in evaluative mode believing it
is formative (spec §3.5).
"""

from alembic import op
import sqlalchemy as sa

revision = "044_institution_settings"
down_revision = "043_user_school_link_entities"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "school_identity_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("logo_asset_uri", sa.String(1024)),
        sa.Column("primary_color", sa.String(9)),
        sa.Column("secondary_color", sa.String(9)),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by_user_id", sa.Uuid()),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "school_id", "version", name="uq_school_identity_versions_version"
        ),
        sa.CheckConstraint("version > 0", name="ck_school_identity_versions_version_positive"),
    )
    op.create_index(
        "ix_school_identity_versions_school_id", "school_identity_versions", ["school_id"]
    )

    op.create_table(
        "school_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column(
            "correction_mode", sa.String(20), nullable=False, server_default="FORMATIVO"
        ),
        sa.Column("validation_default", sa.String(20)),
        sa.Column(
            "validation_teacher_can_disable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("validation_threshold_points", sa.Integer()),
        sa.Column("current_identity_version_id", sa.Uuid()),
        sa.Column("metadata", _JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["current_identity_version_id"],
            ["school_identity_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("school_id", name="uq_school_settings_school"),
        sa.CheckConstraint(
            "correction_mode IN ('FORMATIVO', 'AVALIATIVO')",
            name="ck_school_settings_correction_mode",
        ),
        sa.CheckConstraint(
            "validation_default IS NULL OR validation_default IN "
            "('UMA_A_UMA', 'EM_LOTE', 'AUTOMATICA')",
            name="ck_school_settings_validation_default",
        ),
        sa.CheckConstraint(
            "correction_mode = 'AVALIATIVO' OR validation_default IS NULL",
            name="ck_school_settings_policy_requires_evaluative",
        ),
        sa.CheckConstraint(
            "correction_mode = 'AVALIATIVO' OR validation_threshold_points IS NULL",
            name="ck_school_settings_threshold_requires_evaluative",
        ),
        sa.CheckConstraint(
            "validation_threshold_points IS NULL OR "
            "(validation_threshold_points >= 0 AND validation_threshold_points <= 1000)",
            name="ck_school_settings_threshold_range",
        ),
    )


def downgrade() -> None:
    op.drop_table("school_settings")
    op.drop_index(
        "ix_school_identity_versions_school_id", table_name="school_identity_versions"
    )
    op.drop_table("school_identity_versions")
