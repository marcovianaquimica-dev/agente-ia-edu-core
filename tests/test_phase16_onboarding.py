"""Tests for user invitation and onboarding (Fase 16)."""

import unittest
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.invitation import InvitationService


class TestPhase16Onboarding(unittest.IsolatedAsyncioTestCase):
    """Test user invitation and onboarding flow."""

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=False,
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_01_create_invitation(self):
        """01. criar convite para professor"""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="INV_SCHOOL_01",
                name="Invitation Test School 01",
            )

            inv_service = InvitationService(session)
            invitation = await inv_service.create_invitation(
                school_id=school.id,
                external_email="teacher@example.com",
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
                display_name="Prof. Silva",
                invited_by_external_id="director:alice",
            )

            self.assertIsNotNone(invitation.token)
            self.assertEqual(invitation.status, "PENDING")
            self.assertEqual(invitation.role, "TEACHER")
            self.assertEqual(invitation.scope_type, "CLASSROOM")

    async def test_02_validate_token(self):
        """02. validar token de convite"""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="INV_SCHOOL_02",
                name="Invitation Test School 02",
            )

            inv_service = InvitationService(session)
            invitation = await inv_service.create_invitation(
                school_id=school.id,
                external_email="teacher@example.com",
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
                invited_by_external_id="director:alice",
            )

            # Valid token
            validated = await inv_service.validate_token(invitation.token)
            self.assertEqual(validated.id, invitation.id)

            # Invalid token
            with self.assertRaises(ValueError):
                await inv_service.validate_token("invalid_token_xyz")

    async def test_03_accept_invitation(self):
        """03. aceitar convite"""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="INV_SCHOOL_03",
                name="Invitation Test School 03",
            )

            inv_service = InvitationService(session)
            invitation = await inv_service.create_invitation(
                school_id=school.id,
                external_email="teacher@example.com",
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
                invited_by_external_id="director:alice",
            )

            # Accept invitation
            accepted = await inv_service.accept_invitation(
                token=invitation.token,
                accepted_by_external_id="teacher:silva",
            )
            self.assertEqual(accepted.status, "ACCEPTED")

    async def test_04_activate_invitation_creates_link(self):
        """04. ativar convite cria vínculo UserSchoolLink"""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="INV_SCHOOL_04",
                name="Invitation Test School 04",
            )

            inv_service = InvitationService(session)
            invitation = await inv_service.create_invitation(
                school_id=school.id,
                external_email="teacher@example.com",
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
                invited_by_external_id="director:alice",
            )

            # Activate invitation (creates link)
            activated_inv, link = await inv_service.activate_invitation(
                token=invitation.token,
                external_user_id="teacher:silva",
            )

            self.assertEqual(activated_inv.status, "ACTIVATED")
            self.assertIsNotNone(link.id)
            self.assertEqual(link.external_user_id, "teacher:silva")
            self.assertEqual(link.role, "TEACHER")
            self.assertTrue(link.active)

    async def test_05_invalid_token_on_activation(self):
        """05. token inválido não pode ser ativado"""
        async with self.session_factory() as session:
            inv_service = InvitationService(session)

            # Try to activate with invalid token
            with self.assertRaises(ValueError):
                await inv_service.activate_invitation(
                    token="invalid_token",
                    external_user_id="teacher:silva",
                )

    async def test_06_invitation_cannot_be_used_twice(self):
        """06. convite não pode ser usado duas vezes"""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="INV_SCHOOL_06",
                name="Invitation Test School 06",
            )

            inv_service = InvitationService(session)
            invitation = await inv_service.create_invitation(
                school_id=school.id,
                external_email="teacher@example.com",
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
                invited_by_external_id="director:alice",
            )

            # First activation
            await inv_service.activate_invitation(
                token=invitation.token,
                external_user_id="teacher:silva",
            )

            # Second activation should fail
            with self.assertRaises(ValueError) as ctx:
                await inv_service.activate_invitation(
                    token=invitation.token,
                    external_user_id="teacher:outro",
                )
            self.assertIn("already been activated", str(ctx.exception).lower())

    async def test_07_expire_invitation(self):
        """07. expirar convite"""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="INV_SCHOOL_07",
                name="Invitation Test School 07",
            )

            inv_service = InvitationService(session)
            invitation = await inv_service.create_invitation(
                school_id=school.id,
                external_email="teacher@example.com",
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
                invited_by_external_id="director:alice",
            )

            # Expire invitation
            expired = await inv_service.expire_invitation(invitation.id)
            self.assertEqual(expired.status, "EXPIRED")

            # Cannot use expired invitation
            with self.assertRaises(ValueError):
                await inv_service.validate_token(invitation.token)

    async def test_08_list_pending_invitations(self):
        """08. listar convites pendentes"""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="INV_SCHOOL_08",
                name="Invitation Test School 08",
            )

            inv_service = InvitationService(session)

            # Create 3 pending invitations
            inv1 = await inv_service.create_invitation(
                school_id=school.id,
                external_email="teacher1@example.com",
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
                invited_by_external_id="director:alice",
            )
            inv2 = await inv_service.create_invitation(
                school_id=school.id,
                external_email="teacher2@example.com",
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3B",
                invited_by_external_id="director:alice",
            )
            await inv_service.create_invitation(
                school_id=school.id,
                external_email="coord@example.com",
                role="COORDINATOR",
                scope_type="SCHOOL",
                invited_by_external_id="director:alice",
            )

            # List pending
            pending = await inv_service.list_pending_invitations(school.id)
            self.assertEqual(len(pending), 3)


if __name__ == "__main__":
    unittest.main()
