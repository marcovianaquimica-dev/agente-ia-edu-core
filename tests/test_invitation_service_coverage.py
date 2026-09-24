"""Service-layer coverage for agente_ia_edu.services.invitation.

Targets branches left uncovered by test_phase16_onboarding.py:
- create_invitation against a school that doesn't exist.
- validate_token's own expiry detection, exercised the way production
  actually hits it: a *freshly loaded* invitation whose expires_at round-
  tripped through the DB. SQLite's DateTime(timezone=True) columns lose
  tzinfo on read-back (verified empirically: a value written as
  timezone-aware comes back naive), which is exactly why _as_utc() exists -
  so this also covers _as_utc's naive-datetime branch honestly, instead of
  mutating an in-session object that never loses tzinfo.
- expire_invitation on a missing invitation_id, and on an invitation
  that's already in a terminal status.

NOT covered here, deliberately, because they're unreachable through any
legitimate path:
- validate_token's final "invalid status" branch (the status column has a
  DB CHECK constraint restricting it to exactly
  PENDING/ACCEPTED/ACTIVATED/EXPIRED/CANCELLED - see
  db/models/invitation.py's ck_user_invitations_status - and the two
  preceding branches already cover ACTIVATED/EXPIRED/CANCELLED, so nothing
  can reach the "not in (PENDING, ACCEPTED)" check).
- activate_invitation's own "already ACTIVATED" / "invalid status for
  activation" / expiry re-check (its first line calls validate_token(token),
  which already enforces all three of those same conditions - status
  ACTIVATED/EXPIRED/CANCELLED and expiry - and raises before
  activate_invitation's own copies of those checks can ever run). Confirmed
  by running test_06_invitation_cannot_be_used_twice (already in
  test_phase16_onboarding.py) under coverage: the raise it triggers is
  validate_token's, not activate_invitation's own "already been activated"
  line - that line stays uncovered even with that test present.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import UserInvitation
from agente_ia_edu.services.admin import PlatformAdminService
from agente_ia_edu.services.invitation import InvitationService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


class InvitationServiceCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False,
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_create_invitation_for_nonexistent_school_raises(self):
        async with self.session_factory() as session:
            inv_service = InvitationService(session)
            with self.assertRaises(ValueError) as ctx:
                await inv_service.create_invitation(
                    school_id=uuid4(),
                    external_email="ghost@example.com",
                    role="TEACHER",
                    scope_type="SCHOOL",
                    invited_by_external_id="director:alice",
                )
            self.assertIn("does not exist", str(ctx.exception))

    async def test_validate_token_detects_expiry_after_a_fresh_reload(self):
        # Create + backdate expires_at + commit in one session (mirrors a
        # real invitation created 31 days ago), then validate from a BRAND
        # NEW session/object - the same shape a real request handles it in,
        # so the DateTime(timezone=True)-loses-tzinfo-on-SQLite behavior is
        # actually exercised rather than assumed away.
        async with self.session_factory() as session:
            admin = PlatformAdminService(session)
            school = await admin.create_school(
                performed_by_external_id="admin:master", code="INV_EXPIRY_01", name="Expiry School",
            )
            inv_service = InvitationService(session)
            invitation = await inv_service.create_invitation(
                school_id=school.id,
                external_email="late@example.com",
                role="TEACHER",
                scope_type="SCHOOL",
                invited_by_external_id="director:alice",
            )
            invitation.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            await session.commit()
            token = invitation.token
            invitation_id = invitation.id

        async with self.session_factory() as fresh_session:
            reloaded = await fresh_session.get(UserInvitation, invitation_id)
            # Confirms the realistic trigger for _as_utc's naive branch:
            # SQLite round-tripped this DateTime(timezone=True) value naive.
            self.assertIsNone(reloaded.expires_at.tzinfo)

            inv_service = InvitationService(fresh_session)
            with self.assertRaises(ValueError) as ctx:
                await inv_service.validate_token(token)
            self.assertIn("expired", str(ctx.exception).lower())

            # validate_token marks it EXPIRED as a side effect before raising.
            await fresh_session.commit()

        async with self.session_factory() as verify_session:
            persisted = await verify_session.get(UserInvitation, invitation_id)
            self.assertEqual(persisted.status, "EXPIRED")

    async def test_expire_invitation_missing_id_raises(self):
        async with self.session_factory() as session:
            inv_service = InvitationService(session)
            with self.assertRaises(ValueError) as ctx:
                await inv_service.expire_invitation(uuid4())
            self.assertIn("not found", str(ctx.exception))

    async def test_expire_invitation_already_activated_raises(self):
        async with self.session_factory() as session:
            admin = PlatformAdminService(session)
            school = await admin.create_school(
                performed_by_external_id="admin:master", code="INV_EXPIRY_02", name="Expiry School 2",
            )
            inv_service = InvitationService(session)
            invitation = await inv_service.create_invitation(
                school_id=school.id,
                external_email="activated@example.com",
                role="TEACHER",
                scope_type="SCHOOL",
                invited_by_external_id="director:alice",
            )
            await inv_service.activate_invitation(
                token=invitation.token, external_user_id="teacher:someone",
            )

            with self.assertRaises(ValueError) as ctx:
                await inv_service.expire_invitation(invitation.id)
            self.assertIn("Cannot expire invitation with status ACTIVATED", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
