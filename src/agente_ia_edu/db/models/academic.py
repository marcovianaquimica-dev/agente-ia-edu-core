"""R0 - Academic structure owned by the core.

Until R0 the core referenced students, classes and units by opaque strings
supplied by a hosting platform. These tables make them real, so that class
dashboards, teacher scope and enrollment history rest on data this system
controls rather than on identifiers it cannot validate.

Every table here carries an optional ``external_id``, unique per school, except
``EnrollmentTransition``, which has no ``external_id`` at all because it records
a decision taken in this system — promote, retain, transfer, exit — rather than
mirroring an entity a hosting platform owns. That column is the bridge: what
today is ``scope_external_id = "TURMA_3A"`` resolves to a real row while
existing consumers keep reading the string, and they migrate one at a time
(spec §3.2, §7). Per school and not per parent: the bridge resolves a string
plus a ``school_id`` to one entity, so an ``external_id`` that could name two
rows in the same school would need a tie-break rule nobody has written. Names
may still repeat — "1ª Série A" exists in 2026 and in 2027 — because a name is
ours and an ``external_id`` is the host's (spec §4, §9).

Every table here also carries ``school_id`` itself, and every foreign key
between them is COMPOSITE — ``(school_id, parent_id)`` referencing
``parent(school_id, id)``, which each parent makes possible with a
``UNIQUE(school_id, id)``. The column is redundant with what the chain of keys
already implies, and that is the point: without it the database has no way to
refuse a class whose academic year belongs to school A and whose grade level
belongs to school B. Tenant isolation that depends on a service remembering to
check is not isolation; here a row that crosses a school boundary cannot be
written at all (spec §3.5, §8). A ``school_id`` added by this rule carries no
separate foreign key to ``schools``: it is already constrained to match a
parent whose own ``school_id`` points there, so a second key would restate what
the composite one guarantees.

Credentials live nowhere in this module. The hosting platform stays the source
of truth for authentication; the core only needs a stable local identity for the
person it was told about (spec §3.1).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base

PERSON_STATUSES = ("ACTIVE", "INACTIVE")
USER_STATUSES = ("ACTIVE", "SUSPENDED", "INACTIVE")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Person(Base):
    """A human being, scoped to one school.

    Scoped on purpose: the same physical person enrolled at two schools has two
    rows. Tenant isolation is worth more than de-duplicating people, and a shared
    person table would leak who studies where (spec §4.1).
    """

    __tablename__ = "persons"
    __table_args__ = (
        UniqueConstraint("school_id", "external_id", name="uq_persons_school_external_id"),
        # Redundant with the primary key, and required: it is what lets a child
        # table name (school_id, person_id) as a composite foreign key.
        UniqueConstraint("school_id", "id", name="uq_persons_school_id_id"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_persons_status"),
        Index("ix_persons_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_number: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    users: Mapped[list["User"]] = relationship(back_populates="person")


class User(Base):
    """An account, pointing at a Person and at the host's identity.

    Scoped to one school, like the Person it points at. A teacher who works at
    two schools has two rows, one per school, and the host identity — the same
    ``(provider, external_user_id)`` pair in both — is unique WITHIN a school
    rather than globally. A global uniqueness would have made that teacher
    unrepresentable: her single host account could belong to exactly one
    school's Person, and resolving it in the other school's context would hand
    back a Person from the first, which is the leak that scoping ``persons``
    exists to prevent. ``user_school_links`` already models one
    ``external_user_id`` linked to several schools, so this is the shape the
    system already has (spec §4.1).

    NO CREDENTIAL COLUMN EVER. ``tests/test_r0_identity_models.py`` fails the
    build if one appears, so this rule is enforced rather than remembered.
    """

    __tablename__ = "users"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "person_id"],
            ["persons.school_id", "persons.id"],
            ondelete="RESTRICT",
            name="fk_users_school_person",
        ),
        UniqueConstraint(
            "school_id",
            "external_identity_provider",
            "external_user_id",
            name="uq_users_school_provider_external_user_id",
        ),
        UniqueConstraint("school_id", "id", name="uq_users_school_id_id"),
        CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED', 'INACTIVE')", name="ck_users_status"
        ),
        Index("ix_users_person_id", "person_id"),
        Index("ix_users_school_id", "school_id"),
        Index("ix_users_external_user_id", "external_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    external_identity_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    person: Mapped["Person"] = relationship(back_populates="users")


ACADEMIC_YEAR_STATUSES = ("PLANNED", "ACTIVE", "CLOSED")


class AcademicYear(Base):
    """A school year. Classes belong to one, which is what keeps a student's
    history legible across years (spec §4.2)."""

    __tablename__ = "academic_years"
    __table_args__ = (
        UniqueConstraint("school_id", "year", name="uq_academic_years_school_year"),
        UniqueConstraint(
            "school_id", "external_id", name="uq_academic_years_school_external_id"
        ),
        UniqueConstraint("school_id", "id", name="uq_academic_years_school_id_id"),
        CheckConstraint(
            "status IN ('PLANNED', 'ACTIVE', 'CLOSED')", name="ck_academic_years_status"
        ),
        Index("ix_academic_years_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PLANNED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # ``overlaps``: classes.school_id is written by both parent links, on
    # purpose - see the note on Class.academic_year.
    classes: Mapped[list["Class"]] = relationship(
        back_populates="academic_year", overlaps="classes,grade_level"
    )


class SchoolUnit(Base):
    """A campus or building."""

    __tablename__ = "school_units"
    __table_args__ = (
        UniqueConstraint(
            "school_id", "external_id", name="uq_school_units_school_external_id"
        ),
        UniqueConstraint("school_id", "id", name="uq_school_units_school_id_id"),
        Index("ix_school_units_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class Segment(Base):
    """Fundamental I, Fundamental II, Médio."""

    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("school_id", "external_id", name="uq_segments_school_external_id"),
        UniqueConstraint("school_id", "id", name="uq_segments_school_id_id"),
        Index("ix_segments_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    grade_levels: Mapped[list["GradeLevel"]] = relationship(back_populates="segment")


class GradeLevel(Base):
    """1ª, 2ª, 3ª série, inside a segment."""

    __tablename__ = "grade_levels"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "segment_id"],
            ["segments.school_id", "segments.id"],
            ondelete="RESTRICT",
            name="fk_grade_levels_school_segment",
        ),
        UniqueConstraint(
            "school_id", "external_id", name="uq_grade_levels_school_external_id"
        ),
        UniqueConstraint("school_id", "id", name="uq_grade_levels_school_id_id"),
        Index("ix_grade_levels_segment_id", "segment_id"),
        Index("ix_grade_levels_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    segment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    segment: Mapped["Segment"] = relationship(back_populates="grade_levels")
    classes: Mapped[list["Class"]] = relationship(
        back_populates="grade_level", overlaps="classes,academic_year"
    )


class Class(Base):
    """A class, belonging to BOTH an academic year and a grade level.

    The year is not decoration: "1ª Série A - 2026" and "1ª Série A - 2027" are
    different entities (REDAÇÃO spec §13), and collapsing them would make every
    enrollment history ambiguous.

    Three independent parents — year, grade level, unit — is the worst case for
    tenant leakage, because each could legitimately come from a different
    school if nothing tied them together. ``school_id`` is that tie: all three
    foreign keys carry it, so the three parents are forced to agree.
    """

    __tablename__ = "classes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "academic_year_id"],
            ["academic_years.school_id", "academic_years.id"],
            ondelete="RESTRICT",
            name="fk_classes_school_academic_year",
        ),
        ForeignKeyConstraint(
            ["school_id", "grade_level_id"],
            ["grade_levels.school_id", "grade_levels.id"],
            ondelete="RESTRICT",
            name="fk_classes_school_grade_level",
        ),
        ForeignKeyConstraint(
            ["school_id", "school_unit_id"],
            ["school_units.school_id", "school_units.id"],
            ondelete="RESTRICT",
            name="fk_classes_school_unit",
        ),
        UniqueConstraint(
            "academic_year_id", "grade_level_id", "name", name="uq_classes_year_grade_name"
        ),
        UniqueConstraint("school_id", "external_id", name="uq_classes_school_external_id"),
        UniqueConstraint("school_id", "id", name="uq_classes_school_id_id"),
        Index("ix_classes_academic_year_id", "academic_year_id"),
        Index("ix_classes_grade_level_id", "grade_level_id"),
        Index("ix_classes_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    academic_year_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    grade_level_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    school_unit_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Both composite keys write ``classes.school_id``; ``overlaps`` tells the
    # ORM that this is deliberate, not two relationships fighting over a column.
    academic_year: Mapped["AcademicYear"] = relationship(
        back_populates="classes", overlaps="grade_level,classes"
    )
    grade_level: Mapped["GradeLevel"] = relationship(
        back_populates="classes", overlaps="academic_year,classes"
    )


ENROLLMENT_STATUSES = ("ACTIVE", "TRANSFERRED", "EXITED", "COMPLETED")
TRANSITION_KINDS = ("PROMOTED", "RETAINED", "TRANSFERRED", "EXITED")


class Student(Base):
    """Binds a Person to a school as a student."""

    __tablename__ = "students"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "person_id"],
            ["persons.school_id", "persons.id"],
            ondelete="RESTRICT",
            name="fk_students_school_person",
        ),
        UniqueConstraint("school_id", "external_id", name="uq_students_school_external_id"),
        UniqueConstraint("school_id", "student_code", name="uq_students_school_code"),
        UniqueConstraint("school_id", "id", name="uq_students_school_id_id"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_students_status"),
        Index("ix_students_school_id", "school_id"),
        Index("ix_students_person_id", "person_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    student_code: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    enrollments: Mapped[list["StudentEnrollment"]] = relationship(back_populates="student")


class StudentEnrollment(Base):
    """One student in one class. At most one row per pair."""

    __tablename__ = "student_enrollments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "student_id"],
            ["students.school_id", "students.id"],
            ondelete="RESTRICT",
            name="fk_student_enrollments_school_student",
        ),
        ForeignKeyConstraint(
            ["school_id", "class_id"],
            ["classes.school_id", "classes.id"],
            ondelete="RESTRICT",
            name="fk_student_enrollments_school_class",
        ),
        UniqueConstraint("student_id", "class_id", name="uq_student_enrollments_student_class"),
        UniqueConstraint(
            "school_id", "external_id", name="uq_student_enrollments_school_external_id"
        ),
        UniqueConstraint("school_id", "id", name="uq_student_enrollments_school_id_id"),
        CheckConstraint(
            "status IN ('ACTIVE', 'TRANSFERRED', 'EXITED', 'COMPLETED')",
            name="ck_student_enrollments_status",
        ),
        Index("ix_student_enrollments_student_id", "student_id"),
        Index("ix_student_enrollments_class_id", "class_id"),
        Index("ix_student_enrollments_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    class_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    enrolled_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    student: Mapped["Student"] = relationship(back_populates="enrollments")


class EnrollmentTransition(Base):
    """Moving between years is a recorded fact, never an UPDATE that erases the
    prior state (spec §4.3). ``to_enrollment_id`` is NULL when the student left.

    Same principle that versions the rubric in R1: what happened has to stay
    readable after things change.

    ``school_id`` forces both ends and the deciding user into one school: a
    transition out of school A's enrollment into school B's is not a transfer
    this table can record, and a promotion signed by a user from another school
    is not signed at all.
    """

    __tablename__ = "enrollment_transitions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "from_enrollment_id"],
            ["student_enrollments.school_id", "student_enrollments.id"],
            ondelete="RESTRICT",
            name="fk_enrollment_transitions_school_from",
        ),
        ForeignKeyConstraint(
            ["school_id", "to_enrollment_id"],
            ["student_enrollments.school_id", "student_enrollments.id"],
            ondelete="RESTRICT",
            name="fk_enrollment_transitions_school_to",
        ),
        ForeignKeyConstraint(
            ["school_id", "decided_by_user_id"],
            ["users.school_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_enrollment_transitions_school_decided_by",
        ),
        CheckConstraint(
            "kind IN ('PROMOTED', 'RETAINED', 'TRANSFERRED', 'EXITED')",
            name="ck_enrollment_transitions_kind",
        ),
        Index("ix_enrollment_transitions_from", "from_enrollment_id"),
        Index("ix_enrollment_transitions_to", "to_enrollment_id"),
        Index("ix_enrollment_transitions_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    from_enrollment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    to_enrollment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    reason: Mapped[str | None] = mapped_column(Text)
