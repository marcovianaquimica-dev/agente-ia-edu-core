"""R0 - Academic structure owned by the core.

Until R0 the core referenced students, classes and units by opaque strings
supplied by a hosting platform. These tables make them real, so that class
dashboards, teacher scope and enrollment history rest on data this system
controls rather than on identifiers it cannot validate.

Every table here carries an optional ``external_id``, unique per school. That
column is the bridge: what today is ``scope_external_id = "TURMA_3A"`` resolves
to a real row while existing consumers keep reading the string, and they migrate
one at a time (spec §3.2, §7).

Credentials live nowhere in this module. The hosting platform stays the source
of truth for authentication; the core only needs a stable local identity for the
person it was told about (spec §3.1).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
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
