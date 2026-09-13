"""R0 Fase 1 - bridge columns on user_school_links.

Revision ID: 043_user_school_link_entities
Revises: 042_student_enrollment

Additive and non-destructive: five NULLABLE columns on an existing table. No
row is altered, no existing column or constraint is touched, and no behaviour
changes - nothing reads these columns until Phase 3.

Deliberately NOT a new table. ``user_school_links`` already carries role and
scope and is already consulted by the authorisation service; a parallel
``staff_assignments`` would create two ways to say who sees what, and the one
that actually decided would be the older one (spec §4.4).
"""

from alembic import op
import sqlalchemy as sa

revision = "043_user_school_link_entities"
down_revision = "042_student_enrollment"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("user_id", "users"),
    ("school_unit_id", "school_units"),
    ("segment_id", "segments"),
    ("grade_level_id", "grade_levels"),
    ("class_id", "classes"),
)


def upgrade() -> None:
    for column, target in _COLUMNS:
        op.add_column("user_school_links", sa.Column(column, sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_user_school_links_{column}",
            "user_school_links",
            target,
            [column],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_user_school_links_user_id", "user_school_links", ["user_id"])
    op.create_index("ix_user_school_links_class_id", "user_school_links", ["class_id"])


def downgrade() -> None:
    op.drop_index("ix_user_school_links_class_id", table_name="user_school_links")
    op.drop_index("ix_user_school_links_user_id", table_name="user_school_links")
    for column, _ in reversed(_COLUMNS):
        op.drop_constraint(
            f"fk_user_school_links_{column}", "user_school_links", type_="foreignkey"
        )
        op.drop_column("user_school_links", column)
