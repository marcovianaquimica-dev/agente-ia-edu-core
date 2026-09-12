"""User invitation and activation models (Fase 16).

Supports onboarding flow:
DIRECTOR/COORDINATION
→ Invite teacher/coordinator
→ Teacher receives code
→ Teacher activates account
→ Teacher is ACTIVE

IMPORTANT:
- Do NOT store passwords in database
- Do NOT send emails in this phase if not configured
- Use invitation codes for activation
- Status transitions: INVITED → ACTIVE or DISABLED
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible


class UserInvitation(Base):
    """
    Invitation records for institutional onboarding.

    Flow:
    1. DIRECTOR/COORDINATION creates invitation
    2. Invitation token is generated
    3. User receives token (not via email in this phase)
    4. User activates account with token
    5. UserSchoolLink is created, invitation is ACTIVATED
    """

    __tablename__ = "user_invitations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'ACTIVATED', 'EXPIRED', 'CANCELLED')",
            name="ck_user_invitations_status",
        ),
        CheckConstraint(
            "role IN ('PLATFORM_ADMIN', 'DIRECTOR', 'COORDINATOR', 'SECRETARY', 'TEACHER', 'STUDENT')",
            name="ck_user_invitations_role",
        ),
        CheckConstraint(
            "scope_type IN ('SCHOOL', 'UNIT', 'SEGMENT', 'GRADE_LEVEL', 'CLASSROOM')",
            name="ck_user_invitations_scope_type",
        ),
        UniqueConstraint(
            "school_id", "external_email", "role",
            name="uq_user_invitations_school_email_role",
        ),
        Index("ix_user_invitations_school_id", "school_id"),
        Index("ix_user_invitations_token", "token"),
        Index("ix_user_invitations_status", "status"),
        Index("ix_user_invitations_external_email", "external_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    token: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    external_email: Mapped[str] = mapped_column(String(255), nullable=False)
    external_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(30), nullable=False)
    scope_external_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    invited_by_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    accepted_by_external_id: Mapped[str | None] = mapped_column(String(255))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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

    school: Mapped["School"] = relationship("School", foreign_keys=[school_id])


def generate_invitation_token() -> str:
    """Generate a cryptographically secure invitation token.

    Token format: random 32 bytes, hex-encoded for URL safety.
    """
    return secrets.token_urlsafe(32)


__all__ = [
    "UserInvitation",
    "generate_invitation_token",
]
