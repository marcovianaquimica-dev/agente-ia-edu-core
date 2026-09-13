"""R0 Fase 1 - bridge columns on user_school_links.

Revision ID: 043_user_school_link_entities
Revises: 042_student_enrollment

Additive and non-destructive: five NULLABLE columns on an existing table. No
row is altered, no existing column or constraint is touched, and no behaviour
changes - nothing reads these columns until Phase 3.

Each bridge foreign key is COMPOSITE - ``(school_id, <entity>_id)`` against
``<entity>(school_id, id)`` - so a link belonging to school B cannot point at
school A's class. This is the row the authorisation service reads in Phase 3,
which makes it the one place where a cross-school pointer would be data from
one school visible in another (spec §8). ``school_id`` is itself nullable here
(PLATFORM-scope links have no school); under MATCH SIMPLE a NULL on either side
leaves the key unenforced, which is the case where there is no tenant to
isolate. Existing rows have all six columns NULL, so no row is validated.

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

# (column, target table, constraint name)
_COLUMNS = (
    ("user_id", "users", "fk_user_school_links_school_user"),
    ("school_unit_id", "school_units", "fk_user_school_links_school_unit"),
    ("segment_id", "segments", "fk_user_school_links_school_segment"),
    ("grade_level_id", "grade_levels", "fk_user_school_links_school_grade_level"),
    ("class_id", "classes", "fk_user_school_links_school_class"),
)


def upgrade() -> None:
    for column, target, constraint in _COLUMNS:
        op.add_column("user_school_links", sa.Column(column, sa.Uuid(), nullable=True))
        op.create_foreign_key(
            constraint,
            "user_school_links",
            target,
            ["school_id", column],
            ["school_id", "id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_user_school_links_user_id", "user_school_links", ["user_id"])
    op.create_index("ix_user_school_links_class_id", "user_school_links", ["class_id"])


def downgrade() -> None:
    op.drop_index("ix_user_school_links_class_id", table_name="user_school_links")
    op.drop_index("ix_user_school_links_user_id", table_name="user_school_links")
    for column, _, constraint in reversed(_COLUMNS):
        op.drop_constraint(constraint, "user_school_links", type_="foreignkey")
        op.drop_column("user_school_links", column)
