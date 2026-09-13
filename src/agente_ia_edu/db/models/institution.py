"""R0 - per-institution configuration.

Typed columns with CHECK constraints rather than a free JSON blob, and the
reason is a lesson R1 paid for: its final review found ``provenance`` guarded on
one column of three, and validating in the loader was not enough because the
seeder wrote a hardcoded value and the constraint never fired. What has a rule,
the database refuses (spec §3.5).

The stake here is higher than a rubric's. A school whose mode is stored wrong is
not ugly data; it is a school running in evaluative mode believing it is
formative, in the exact control that exists for regulatory reasons.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..types import JSONBCompatible

CORRECTION_MODES = ("FORMATIVO", "AVALIATIVO")
VALIDATION_MODES = ("UMA_A_UMA", "EM_LOTE", "AUTOMATICA")

_HEX_DIGITS = "0123456789abcdef"
HEX_COLOR_LENGTHS = (4, 7, 9)  # #RGB, #RRGGBB, #RRGGBBAA


def hex_color_check(column: str) -> str:
    """CHECK that ``column`` is NULL or a hexadecimal colour (spec §5.2).

    Written without a regular expression on purpose. PostgreSQL's ``~`` is not
    parseable by SQLite and SQLite's ``GLOB`` does not exist in PostgreSQL,
    while every test in this phase builds the schema on SQLite - so a regex
    CHECK would be a constraint that only one of the two databases has.
    ``substr``, ``length``, ``lower`` and ``replace`` exist in both: strip every
    hex digit from the body and what remains must be empty.
    """
    stripped = f"lower(substr({column}, 2))"
    for digit in _HEX_DIGITS:
        stripped = f"replace({stripped}, '{digit}', '')"
    lengths = ", ".join(str(n) for n in HEX_COLOR_LENGTHS)
    return (
        f"{column} IS NULL OR ("
        f"substr({column}, 1, 1) = '#' "
        f"AND length({column}) IN ({lengths}) "
        f"AND {stripped} = ''"
        f")"
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SchoolSetting(Base):
    """One row per school.

    ``correction_mode`` says whether a score exists at all. The validation policy
    says who approves it, and only means something when scores exist - which is
    what the two CHECK constraints below enforce.

    The policy stored here is the DEFAULT and the PERMITTED, not the decision:
    the real choice happens per class when a teacher sends a prompt, in R3. A
    school may authorise teachers to skip validation, or fix that they may not.
    """

    __tablename__ = "school_settings"
    __table_args__ = (
        # Composite: the identity a school points at must be that school's own.
        # This is the row R4 stamps onto a devolutiva's PDF, so a cross-school
        # pointer here is school B's mark on school A's document (spec §8).
        ForeignKeyConstraint(
            ["school_id", "current_identity_version_id"],
            ["school_identity_versions.school_id", "school_identity_versions.id"],
            ondelete="RESTRICT",
            name="fk_school_settings_school_identity_version",
        ),
        UniqueConstraint("school_id", name="uq_school_settings_school"),
        CheckConstraint(
            "correction_mode IN ('FORMATIVO', 'AVALIATIVO')",
            name="ck_school_settings_correction_mode",
        ),
        CheckConstraint(
            "validation_default IS NULL OR validation_default IN "
            "('UMA_A_UMA', 'EM_LOTE', 'AUTOMATICA')",
            name="ck_school_settings_validation_default",
        ),
        CheckConstraint(
            "correction_mode = 'AVALIATIVO' OR validation_default IS NULL",
            name="ck_school_settings_policy_requires_evaluative",
        ),
        CheckConstraint(
            "correction_mode = 'AVALIATIVO' OR validation_threshold_points IS NULL",
            name="ck_school_settings_threshold_requires_evaluative",
        ),
        CheckConstraint(
            "validation_threshold_points IS NULL OR "
            "(validation_threshold_points >= 0 AND validation_threshold_points <= 1000)",
            name="ck_school_settings_threshold_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    correction_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="FORMATIVO"
    )
    validation_default: Mapped[str | None] = mapped_column(String(20))
    validation_teacher_can_disable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    validation_threshold_points: Mapped[int | None] = mapped_column(Integer)
    current_identity_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class SchoolIdentityVersion(Base):
    """The school's visual identity, versioned.

    Versioned for the same reason the rubric is: REDAÇÃO spec §8 requires that
    regenerating an old devolutiva's PDF must not rewrite the document the family
    already received. If the school changes its logo in March, February's
    devolutiva keeps February's mark - so the devolutiva stamps the version id,
    exactly as it stamps ``rubric_version``.

    A published version is immutable. Changing the identity creates a new one.

    Who published it is mandatory, and the composite key makes that author a
    user OF THIS SCHOOL. The colours are checked as hexadecimal by the database
    rather than by whoever writes them (spec §3.5, §5.2).
    """

    __tablename__ = "school_identity_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "published_by_user_id"],
            ["users.school_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_school_identity_versions_school_published_by",
        ),
        UniqueConstraint("school_id", "version", name="uq_school_identity_versions_version"),
        UniqueConstraint("school_id", "id", name="uq_school_identity_versions_school_id_id"),
        CheckConstraint("version > 0", name="ck_school_identity_versions_version_positive"),
        CheckConstraint(
            hex_color_check("primary_color"),
            name="ck_school_identity_versions_primary_color_hex",
        ),
        CheckConstraint(
            hex_color_check("secondary_color"),
            name="ck_school_identity_versions_secondary_color_hex",
        ),
        Index("ix_school_identity_versions_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    logo_asset_uri: Mapped[str | None] = mapped_column(String(1024))
    primary_color: Mapped[str | None] = mapped_column(String(9))
    secondary_color: Mapped[str | None] = mapped_column(String(9))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    # NOT NULL: the only writer is ``InstitutionSettingsService.publish_identity``
    # and it requires the author. Declared nullable, the column was structurally
    # unreachable - never written by anyone, permanently NULL, indistinguishable
    # from "not informed" to whoever reads it next.
    published_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
