"""R0 Fase 1 - Person and User.

Revision ID: 040_academic_identity
Revises: 039_essay_rubric_foundation

Purely additive: two new tables, no existing table touched.

Audit of reuse: ``user_school_links`` already binds an ``external_user_id`` to a
role and a scope, but it stores no person, no name and no contact, and it is a
binding rather than an identity. Nothing in this schema represents a human being
today. These tables add that and change nothing about how links behave.

Both tables are scoped to one school, and ``users`` reaches its person through
the COMPOSITE key (school_id, person_id) - see the module docstring of
``db/models/academic.py``. The host identity is therefore unique per school
rather than globally, which is what makes a teacher who works at two schools
representable at all (spec §4.1).

NO CREDENTIAL COLUMN. See spec §3.1 and the guard test in
``tests/test_r0_identity_models.py``.
"""

from alembic import op
import sqlalchemy as sa

revision = "040_academic_identity"
down_revision = "039_essay_rubric_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persons",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("document_number", sa.String(50)),
        sa.Column("email", sa.String(255)),
        sa.Column("phone", sa.String(50)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("school_id", "external_id", name="uq_persons_school_external_id"),
        sa.UniqueConstraint("school_id", "id", name="uq_persons_school_id_id"),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_persons_status"),
    )
    op.create_index("ix_persons_school_id", "persons", ["school_id"])

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("external_identity_provider", sa.String(100), nullable=False),
        sa.Column("external_user_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id", "person_id"],
            ["persons.school_id", "persons.id"],
            ondelete="RESTRICT",
            name="fk_users_school_person",
        ),
        sa.UniqueConstraint(
            "school_id",
            "external_identity_provider",
            "external_user_id",
            name="uq_users_school_provider_external_user_id",
        ),
        sa.UniqueConstraint("school_id", "id", name="uq_users_school_id_id"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED', 'INACTIVE')", name="ck_users_status"
        ),
    )
    op.create_index("ix_users_person_id", "users", ["person_id"])
    op.create_index("ix_users_school_id", "users", ["school_id"])
    op.create_index("ix_users_external_user_id", "users", ["external_user_id"])


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("persons")
