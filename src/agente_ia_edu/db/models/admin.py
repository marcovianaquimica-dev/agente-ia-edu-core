"""
Platform Administration and Multi-tenancy domain models (Phase 12A).

Models for:
- School: Multi-tenant school/institution entity.
- SchoolModule: Platform module enablements per school (e.g., AGENTE_IA_EDU, REDACAO_IA).
- UserSchoolLink: Role and scope bindings for users, including reception-only SECRETARY.
- AdminAuditLog: Administrative audit trail for global platform changes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible


class School(Base):
    """
    SaaS tenant entity representing a school/institution.

    Isolated tenant root for platform administration, modules, roles, and scopes.
    """

    __tablename__ = "schools"
    __table_args__ = (
        UniqueConstraint("code", name="uq_schools_code"),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')",
            name="ck_schools_status",
        ),
        Index("ix_schools_code", "code"),
        Index("ix_schools_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(100))
    external_identifier: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE")
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    modules: Mapped[list["SchoolModule"]] = relationship(back_populates="school")
    user_links: Mapped[list["UserSchoolLink"]] = relationship(back_populates="school")
    audit_logs: Mapped[list["AdminAuditLog"]] = relationship(back_populates="school")


class SchoolModule(Base):
    """
    Platform module configuration per school (e.g. AGENTE_IA_EDU, REDACAO_IA).
    """

    __tablename__ = "school_modules"
    __table_args__ = (
        UniqueConstraint("school_id", "module_key", name="uq_school_modules_school_key"),
        CheckConstraint(
            "module_key IN ('AGENTE_IA_EDU', 'REDACAO_IA')",
            name="ck_school_modules_module_key",
        ),
        Index("ix_school_modules_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    module_key: Mapped[str] = mapped_column(String(50), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    school: Mapped["School"] = relationship("School", back_populates="modules")


class UserSchoolLink(Base):
    """
    Role and Scope bindings for users.

    Separates ROLE (PLATFORM_ADMIN, DIRECTOR, COORDINATOR, SECRETARY, TEACHER, STUDENT)
    from SCOPE (PLATFORM, SCHOOL, UNIT, SEGMENT, GRADE_LEVEL, CLASSROOM).
    """

    __tablename__ = "user_school_links"
    __table_args__ = (
        # R0: every bridge column is reached through (school_id, <entity>_id),
        # so a link belonging to school B cannot point at school A's class -
        # the row the authorisation service reads in Phase 3 (spec §3.2, §8).
        # ``school_id`` is nullable for PLATFORM-scope links; with MATCH SIMPLE
        # a NULL on either side leaves the key unenforced, which is exactly the
        # case where there is no school to be isolated from.
        ForeignKeyConstraint(
            ["school_id", "user_id"],
            ["users.school_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_user_school_links_school_user",
        ),
        ForeignKeyConstraint(
            ["school_id", "school_unit_id"],
            ["school_units.school_id", "school_units.id"],
            ondelete="RESTRICT",
            name="fk_user_school_links_school_unit",
        ),
        ForeignKeyConstraint(
            ["school_id", "segment_id"],
            ["segments.school_id", "segments.id"],
            ondelete="RESTRICT",
            name="fk_user_school_links_school_segment",
        ),
        ForeignKeyConstraint(
            ["school_id", "grade_level_id"],
            ["grade_levels.school_id", "grade_levels.id"],
            ondelete="RESTRICT",
            name="fk_user_school_links_school_grade_level",
        ),
        ForeignKeyConstraint(
            ["school_id", "class_id"],
            ["classes.school_id", "classes.id"],
            ondelete="RESTRICT",
            name="fk_user_school_links_school_class",
        ),
        CheckConstraint(
            "role IN ('PLATFORM_ADMIN', 'DIRECTOR', 'COORDINATOR', 'SECRETARY', 'TEACHER', 'STUDENT')",
            name="ck_user_school_links_role",
        ),
        CheckConstraint(
            "scope_type IN ('PLATFORM', 'SCHOOL', 'UNIT', 'SEGMENT', 'GRADE_LEVEL', 'CLASSROOM')",
            name="ck_user_school_links_scope_type",
        ),
        Index("ix_user_school_links_external_user_id", "external_user_id"),
        Index("ix_user_school_links_school_id", "school_id"),
        Index("ix_user_school_links_role", "role"),
        Index("ix_user_school_links_user_id", "user_id"),
        Index("ix_user_school_links_class_id", "class_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT")
    )
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(30), nullable=False)
    scope_external_id: Mapped[str | None] = mapped_column(String(255))
    # R0 bridge: the entity this scope points at, when it is already known.
    # Nullable on purpose - every row written before R0 has only the string,
    # and consumers migrate one at a time (spec §3.2, §7).
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    school_unit_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    segment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    grade_level_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    class_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    school: Mapped[Optional["School"]] = relationship("School", back_populates="user_links")


class AdminAuditLog(Base):
    """
    Administrative audit trail for global platform management.
    """

    __tablename__ = "admin_audit_logs"
    __table_args__ = (
        Index("ix_admin_audit_logs_performed_by", "performed_by_external_id"),
        Index("ix_admin_audit_logs_school_id", "school_id"),
        Index("ix_admin_audit_logs_action", "action"),
        Index("ix_admin_audit_logs_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    performed_by_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT")
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    school: Mapped[Optional["School"]] = relationship("School", back_populates="audit_logs")
