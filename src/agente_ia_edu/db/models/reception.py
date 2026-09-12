"""School-scoped pre-registration records for reception workflows."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class ReceptionCandidate(Base):
    """A lead received by a school before any enrollment is created."""

    __tablename__ = "reception_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PRE_REGISTRATION', 'DIAGNOSTIC_RELEASED', 'CONVERTED')",
            name="ck_reception_candidates_status",
        ),
        Index("ix_reception_candidates_school_created", "school_id", "created_at"),
        Index("ix_reception_candidates_school_name", "school_id", "full_name"),
        Index("ix_reception_candidates_email", "email"),
        Index("ix_reception_candidates_phone", "phone"),
        UniqueConstraint("school_id", "email", name="uq_reception_candidates_school_email"),
        UniqueConstraint("school_id", "phone", name="uq_reception_candidates_school_phone"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    preferred_name: Mapped[str | None] = mapped_column(String(255))
    birth_date: Mapped[date | None] = mapped_column(Date)
    guardian_name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(30), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    academic_year: Mapped[str] = mapped_column(String(10), nullable=False)
    unit_id: Mapped[str] = mapped_column(String(255), nullable=False)
    segment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    grade_level: Mapped[str] = mapped_column(String(255), nullable=False)
    classroom_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PRE_REGISTRATION"
    )
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("user_invitations.id", ondelete="RESTRICT"), unique=True
    )
    external_student_id: Mapped[str | None] = mapped_column(String(255))
    released_by_external_id: Mapped[str | None] = mapped_column(String(255))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    school: Mapped["School"] = relationship("School", foreign_keys=[school_id])
    invitation: Mapped["UserInvitation | None"] = relationship(
        "UserInvitation", foreign_keys=[invitation_id]
    )


__all__ = ["ReceptionCandidate"]