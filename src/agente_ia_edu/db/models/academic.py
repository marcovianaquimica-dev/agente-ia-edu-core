"""R0 - Academic structure owned by the core.

Until R0 the core referenced students, classes and units by opaque strings
supplied by a hosting platform. These tables make them real, so that class
dashboards, teacher scope and enrollment history rest on data this system
controls rather than on identifiers it cannot validate.

Every table here carries an optional ``external_id``, unique per school, except
``User``, which is keyed by ``external_user_id`` alongside its identity
provider instead. That column is the bridge: what today is
``scope_external_id = "TURMA_3A"`` resolves to a real row while existing
consumers keep reading the string, and they migrate one at a time (spec §3.2,
§7).

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

    NO CREDENTIAL COLUMN EVER. ``tests/test_r0_identity_models.py`` fails the
    build if one appears, so this rule is enforced rather than remembered.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint(
            "external_identity_provider",
            "external_user_id",
            name="uq_users_provider_external_user_id",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED', 'INACTIVE')", name="ck_users_status"
        ),
        Index("ix_users_person_id", "person_id"),
        Index("ix_users_external_user_id", "external_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("persons.id", ondelete="RESTRICT"), nullable=False
    )
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

    classes: Mapped[list["Class"]] = relationship(back_populates="academic_year")


class SchoolUnit(Base):
    """A campus or building."""

    __tablename__ = "school_units"
    __table_args__ = (
        UniqueConstraint(
            "school_id", "external_id", name="uq_school_units_school_external_id"
        ),
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
        UniqueConstraint(
            "segment_id", "external_id", name="uq_grade_levels_segment_external_id"
        ),
        Index("ix_grade_levels_segment_id", "segment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    segment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("segments.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    segment: Mapped["Segment"] = relationship(back_populates="grade_levels")
    classes: Mapped[list["Class"]] = relationship(back_populates="grade_level")


class Class(Base):
    """A class, belonging to BOTH an academic year and a grade level.

    The year is not decoration: "1ª Série A - 2026" and "1ª Série A - 2027" are
    different entities (REDAÇÃO spec §13), and collapsing them would make every
    enrollment history ambiguous.
    """

    __tablename__ = "classes"
    __table_args__ = (
        UniqueConstraint(
            "academic_year_id", "grade_level_id", "name", name="uq_classes_year_grade_name"
        ),
        UniqueConstraint(
            "academic_year_id", "external_id", name="uq_classes_year_external_id"
        ),
        Index("ix_classes_academic_year_id", "academic_year_id"),
        Index("ix_classes_grade_level_id", "grade_level_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    academic_year_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("academic_years.id", ondelete="RESTRICT"), nullable=False
    )
    grade_level_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("grade_levels.id", ondelete="RESTRICT"), nullable=False
    )
    school_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("school_units.id", ondelete="RESTRICT")
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    academic_year: Mapped["AcademicYear"] = relationship(back_populates="classes")
    grade_level: Mapped["GradeLevel"] = relationship(back_populates="classes")


ENROLLMENT_STATUSES = ("ACTIVE", "TRANSFERRED", "EXITED", "COMPLETED")
TRANSITION_KINDS = ("PROMOTED", "RETAINED", "TRANSFERRED", "EXITED")


class Student(Base):
    """Binds a Person to a school as a student."""

    __tablename__ = "students"
    __table_args__ = (
        UniqueConstraint("school_id", "external_id", name="uq_students_school_external_id"),
        UniqueConstraint("school_id", "student_code", name="uq_students_school_code"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_students_status"),
        Index("ix_students_school_id", "school_id"),
        Index("ix_students_person_id", "person_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("persons.id", ondelete="RESTRICT"), nullable=False
    )
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
        UniqueConstraint("student_id", "class_id", name="uq_student_enrollments_student_class"),
        CheckConstraint(
            "status IN ('ACTIVE', 'TRANSFERRED', 'EXITED', 'COMPLETED')",
            name="ck_student_enrollments_status",
        ),
        Index("ix_student_enrollments_student_id", "student_id"),
        Index("ix_student_enrollments_class_id", "class_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("classes.id", ondelete="RESTRICT"), nullable=False
    )
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
    """

    __tablename__ = "enrollment_transitions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('PROMOTED', 'RETAINED', 'TRANSFERRED', 'EXITED')",
            name="ck_enrollment_transitions_kind",
        ),
        Index("ix_enrollment_transitions_from", "from_enrollment_id"),
        Index("ix_enrollment_transitions_to", "to_enrollment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    from_enrollment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("student_enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    to_enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("student_enrollments.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    reason: Mapped[str | None] = mapped_column(Text)
