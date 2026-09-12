"""User Invitation and Onboarding Service (Fase 16).

Handles:
1. Creating invitations
2. Validating invitation tokens
3. Accepting invitations
4. Creating UserSchoolLink when invitation is accepted
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import School, UserInvitation, UserSchoolLink
from agente_ia_edu.db.models.invitation import generate_invitation_token

logger = logging.getLogger(__name__)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class InvitationService:
    """Manage user invitations and onboarding flow."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_invitation(
        self,
        *,
        school_id: UUID,
        external_email: str,
        role: str,
        scope_type: str,
        scope_external_id: str | None = None,
        display_name: str | None = None,
        invited_by_external_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> UserInvitation:
        """Create a new user invitation for institutional onboarding.

        Args:
            school_id: School/tenant ID
            external_email: Email or external identifier for user
            role: User role (DIRECTOR, COORDINATOR, SECRETARY, TEACHER, STUDENT)
            scope_type: Scope (SCHOOL, UNIT, SEGMENT, GRADE_LEVEL, CLASSROOM)
            scope_external_id: Specific scope ID (e.g., CLASSROOM name)
            display_name: Optional display name
            invited_by_external_id: Who is sending the invitation
            metadata: Optional metadata

        Returns:
            UserInvitation: Created invitation record

        Raises:
            ValueError: If school doesn't exist or invitation conflicts
        """
        school = await self.session.get(School, school_id)
        if not school:
            raise ValueError(f"School {school_id} does not exist")

        token = generate_invitation_token()
        expires_at = datetime.now(timezone.utc) + timedelta(days=30)

        invitation = UserInvitation(
            school_id=school_id,
            token=token,
            external_email=external_email,
            external_user_id=None,
            display_name=display_name,
            role=role.upper(),
            scope_type=scope_type.upper(),
            scope_external_id=scope_external_id,
            status="PENDING",
            invited_by_external_id=invited_by_external_id,
            expires_at=expires_at,
            expired_at=None,
            metadata_=metadata or {},
        )
        self.session.add(invitation)
        await self.session.flush()

        logger.info(
            f"Invitation created for {external_email} to {school.code} "
            f"as {role} with scope {scope_type}:{scope_external_id}"
        )
        return invitation

    async def validate_token(self, token: str) -> UserInvitation:
        """Validate and retrieve invitation by token.

        Args:
            token: Invitation token

        Returns:
            UserInvitation: Valid invitation

        Raises:
            ValueError: If token is invalid, expired, or not found
        """
        stmt = select(UserInvitation).where(UserInvitation.token == token)
        result = await self.session.execute(stmt)
        invitation = result.scalar_one_or_none()

        if not invitation:
            raise ValueError("Invalid invitation token")

        now = datetime.now(timezone.utc)
        expires_at = _as_utc(invitation.expires_at)
        expired_at = _as_utc(invitation.expired_at)
        if expires_at and expires_at <= now:
            invitation.status = "EXPIRED"
            invitation.expired_at = invitation.expires_at
            invitation.updated_at = now
            raise ValueError("Invitation has expired")

        if expired_at and expired_at <= now:
            raise ValueError("Invitation has expired")

        if invitation.status in ("ACTIVATED", "EXPIRED", "CANCELLED"):
            raise ValueError(f"Invitation has already been {invitation.status.lower()}")

        if invitation.status not in ("PENDING", "ACCEPTED"):
            raise ValueError(f"Invitation is in an invalid status: {invitation.status}")

        return invitation

    async def accept_invitation(
        self,
        token: str,
        accepted_by_external_id: str,
    ) -> UserInvitation:
        """Accept invitation but do not create link yet.

        The external_user_id is typically set when the user activates
        their account (if it requires password/email verification).

        Args:
            token: Invitation token
            accepted_by_external_id: User accepting the invitation

        Returns:
            UserInvitation: Accepted invitation

        Raises:
            ValueError: If token is invalid or invitation cannot be accepted
        """
        invitation = await self.validate_token(token)

        invitation.status = "ACCEPTED"
        invitation.accepted_by_external_id = accepted_by_external_id
        invitation.external_user_id = accepted_by_external_id
        invitation.updated_at = datetime.now(timezone.utc)
        await self.session.flush()

        logger.info(f"Invitation {invitation.id} accepted by {accepted_by_external_id}")
        return invitation

    async def activate_invitation(
        self,
        token: str,
        external_user_id: str,
    ) -> tuple[UserInvitation, UserSchoolLink]:
        """Activate invitation and create UserSchoolLink.

        This is the final step: creates the actual role+scope binding
        and marks the invitation as ACTIVATED.

        Args:
            token: Invitation token
            external_user_id: User identifier (from authentication)

        Returns:
            Tuple of (UserInvitation, UserSchoolLink)

        Raises:
            ValueError: If token is invalid or link cannot be created
        """
        invitation = await self.validate_token(token)

        if invitation.status == "ACTIVATED":
            raise ValueError("Invitation has already been activated")

        if invitation.status not in ("PENDING", "ACCEPTED"):
            raise ValueError(f"Cannot activate invitation in {invitation.status.lower()} status")

        expires_at = _as_utc(invitation.expires_at)
        if expires_at and expires_at <= datetime.now(timezone.utc):
            invitation.status = "EXPIRED"
            invitation.expired_at = invitation.expires_at
            invitation.updated_at = datetime.now(timezone.utc)
            raise ValueError("Invitation has expired")

        # Create UserSchoolLink
        link = UserSchoolLink(
            external_user_id=external_user_id,
            school_id=invitation.school_id,
            role=invitation.role,
            scope_type=invitation.scope_type,
            scope_external_id=invitation.scope_external_id,
            active=True,
            metadata_={
                **(invitation.metadata_ or {}),
                "invited_via_token": invitation.token[:8] + "...",
                "invitation_id": str(invitation.id),
            },
        )
        self.session.add(link)
        await self.session.flush()

        # Update invitation
        invitation.status = "ACTIVATED"
        invitation.accepted_by_external_id = external_user_id
        invitation.external_user_id = external_user_id
        invitation.activated_at = datetime.now(timezone.utc)
        invitation.expires_at = invitation.expires_at or invitation.activated_at
        invitation.expired_at = None
        invitation.updated_at = datetime.now(timezone.utc)
        await self.session.flush()

        logger.info(
            f"Invitation {invitation.id} activated for {external_user_id} "
            f"in school {invitation.school_id}"
        )
        await self.session.commit()
        return invitation, link

    async def expire_invitation(self, invitation_id: UUID) -> UserInvitation:
        """Manually expire an invitation.

        Args:
            invitation_id: Invitation ID

        Returns:
            UserInvitation: Expired invitation

        Raises:
            ValueError: If invitation not found
        """
        invitation = await self.session.get(UserInvitation, invitation_id)
        if not invitation:
            raise ValueError(f"Invitation {invitation_id} not found")

        if invitation.status in ("ACTIVATED", "CANCELLED", "EXPIRED"):
            raise ValueError(f"Cannot expire invitation with status {invitation.status}")

        invitation.status = "EXPIRED"
        now = datetime.now(timezone.utc)
        invitation.expires_at = invitation.expires_at or now
        invitation.expired_at = now
        invitation.updated_at = now
        await self.session.flush()

        logger.info(f"Invitation {invitation_id} expired")
        return invitation

    async def list_pending_invitations(self, school_id: UUID) -> list[UserInvitation]:
        """List all pending invitations for a school.

        Args:
            school_id: School ID

        Returns:
            List of pending invitations
        """
        stmt = (
            select(UserInvitation)
            .where(
                UserInvitation.school_id == school_id,
                UserInvitation.status == "PENDING",
            )
            .order_by(UserInvitation.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


__all__ = [
    "InvitationService",
]
