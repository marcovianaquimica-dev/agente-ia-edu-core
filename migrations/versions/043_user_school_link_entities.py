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

That MATCH SIMPLE gap is closed by a CHECK: a link with ``school_id IS NULL``
must have every bridge column NULL too, so a PLATFORM-scope link can no longer
name an entity that does not exist at all - which is worse than the simple
foreign key this migration replaces. ``users`` is included because it now
carries ``school_id`` as well.

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

# Kept byte-identical to the CheckConstraint text in
# src/agente_ia_edu/db/models/admin.py - the re-review verifies them by
# textual diff.
_BRIDGE_REQUIRES_SCHOOL_CHECK = (
    "school_id IS NOT NULL OR (user_id IS NULL AND school_unit_id IS NULL "
    "AND segment_id IS NULL AND grade_level_id IS NULL AND class_id IS NULL)"
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
    op.create_check_constraint(
        "ck_user_school_links_bridge_requires_school",
        "user_school_links",
        _BRIDGE_REQUIRES_SCHOOL_CHECK,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_user_school_links_bridge_requires_school",
        "user_school_links",
        type_="check",
    )
    op.drop_index("ix_user_school_links_class_id", table_name="user_school_links")
    op.drop_index("ix_user_school_links_user_id", table_name="user_school_links")
    for column, _, constraint in reversed(_COLUMNS):
        op.drop_constraint(constraint, "user_school_links", type_="foreignkey")
        op.drop_column("user_school_links", column)
