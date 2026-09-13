"""R0 Fase 1 - student, enrollment and enrollment transition.

Revision ID: 042_student_enrollment
Revises: 041_academic_hierarchy

Purely additive: three new tables, no existing table touched.

``enrollment_transitions`` exists so that moving between years is a recorded
fact rather than an UPDATE that erases the previous state. It is the same
principle that versions the essay rubric in R1: what happened has to stay
readable after things change.

Every table here carries ``school_id`` and every key between them is COMPOSITE,
so an enrollment cannot bind a student of one school to a class of another, and
a transition cannot span two schools. See ``db/models/academic.py``.
"""

from alembic import op
import sqlalchemy as sa

revision = "042_student_enrollment"
down_revision = "041_academic_hierarchy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "students",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("student_code", sa.String(50)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["school_id", "person_id"],
            ["persons.school_id", "persons.id"],
            ondelete="RESTRICT",
            name="fk_students_school_person",
        ),
        sa.UniqueConstraint("school_id", "external_id", name="uq_students_school_external_id"),
        sa.UniqueConstraint("school_id", "student_code", name="uq_students_school_code"),
        sa.UniqueConstraint("school_id", "id", name="uq_students_school_id_id"),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_students_status"),
    )
    op.create_index("ix_students_school_id", "students", ["school_id"])
    op.create_index("ix_students_person_id", "students", ["person_id"])

    op.create_table(
        "student_enrollments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("enrolled_on", sa.Date()),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id", "student_id"],
            ["students.school_id", "students.id"],
            ondelete="RESTRICT",
            name="fk_student_enrollments_school_student",
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "class_id"],
            ["classes.school_id", "classes.id"],
            ondelete="RESTRICT",
            name="fk_student_enrollments_school_class",
        ),
        sa.UniqueConstraint(
            "student_id", "class_id", name="uq_student_enrollments_student_class"
        ),
        sa.UniqueConstraint(
            "school_id", "external_id", name="uq_student_enrollments_school_external_id"
        ),
        sa.UniqueConstraint("school_id", "id", name="uq_student_enrollments_school_id_id"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'TRANSFERRED', 'EXITED', 'COMPLETED')",
            name="ck_student_enrollments_status",
        ),
    )
    op.create_index("ix_student_enrollments_student_id", "student_enrollments", ["student_id"])
    op.create_index("ix_student_enrollments_class_id", "student_enrollments", ["class_id"])
    op.create_index("ix_student_enrollments_school_id", "student_enrollments", ["school_id"])

    op.create_table(
        "enrollment_transitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("from_enrollment_id", sa.Uuid(), nullable=False),
        sa.Column("to_enrollment_id", sa.Uuid()),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.ForeignKeyConstraint(
            ["school_id", "from_enrollment_id"],
            ["student_enrollments.school_id", "student_enrollments.id"],
            ondelete="RESTRICT",
            name="fk_enrollment_transitions_school_from",
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "to_enrollment_id"],
            ["student_enrollments.school_id", "student_enrollments.id"],
            ondelete="RESTRICT",
            name="fk_enrollment_transitions_school_to",
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "decided_by_user_id"],
            ["users.school_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_enrollment_transitions_school_decided_by",
        ),
        sa.CheckConstraint(
            "kind IN ('PROMOTED', 'RETAINED', 'TRANSFERRED', 'EXITED')",
            name="ck_enrollment_transitions_kind",
        ),
    )
    op.create_index(
        "ix_enrollment_transitions_from", "enrollment_transitions", ["from_enrollment_id"]
    )
    op.create_index(
        "ix_enrollment_transitions_to", "enrollment_transitions", ["to_enrollment_id"]
    )
    op.create_index(
        "ix_enrollment_transitions_school_id", "enrollment_transitions", ["school_id"]
    )


def downgrade() -> None:
    op.drop_table("enrollment_transitions")
    op.drop_table("student_enrollments")
    op.drop_table("students")
