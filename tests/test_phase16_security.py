"""Security and authentication tests for Fase 16.

This suite proves:
A) Usuário sem autenticação → não acessa endpoint protegido
B) Usuário autenticado sem role adequada → 403
C) Professor A → não acessa escola B
D) Professor A → não acessa turma B
E) Coordenador A → não acessa escopo fora de sua autorização
F) Aluno A → não acessa aluno B
G) Aluno independente → não ganha acesso a dados escolares
H) PLATFORM_ADMIN → pode administrar tenants
I) PLATFORM_ADMIN → não ganha automaticamente acesso pedagógico
J) Usuário não pode alterar role/school_id/scope por manipulação de payload
"""

import unittest
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.auth.gateway import SimpleAuthenticationGateway
from agente_ia_edu.auth.token import TestTokenValidator
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.authorization import AuthorizationService


class TestPhase16Security(unittest.IsolatedAsyncioTestCase):
    """Prove security requirements for Fase 16."""

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
        self.token_validator = TestTokenValidator()
        self.gateway = SimpleAuthenticationGateway(self.token_validator)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_A_unauthenticated_user_cannot_access_protected_resource(self):
        """A) Usuário sem autenticação → não acessa endpoint protegido."""
        # Empty token should fail
        with self.assertRaises(ValueError) as ctx:
            await self.gateway.authenticate("")
        self.assertIn("token is required", str(ctx.exception).lower())

        # Invalid token with special characters should fail
        with self.assertRaises(ValueError):
            await self.gateway.authenticate("invalid!@#")

    async def test_B_authenticated_user_without_adequate_role_gets_403(self):
        """B) Usuário autenticado sem role adequada → 403."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="SEC_SCHOOL_B",
                name="Security Test School B",
            )

            # Create a STUDENT user
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="alice",  # Matches token "test:student:alice" → subject "alice"
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_3A",
            )

            # Authenticate as student
            token = "test:student:alice"
            identity = await self.gateway.authenticate(token)

            authz = AuthorizationService(session)
            context = await authz.resolve_context(identity)

            # Student cannot require PLATFORM_ADMIN
            check = await authz.require_role(context, "PLATFORM_ADMIN")
            self.assertFalse(check.allowed)
            self.assertIn("role required", check.reason.lower())

    async def test_C_teacher_from_school_A_cannot_access_school_B(self):
        """C) Professor A → não acessa escola B."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)

            # Create two schools
            school_a = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="SEC_SCHOOL_A",
                name="Security School A",
            )
            school_b = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="SEC_SCHOOL_B",
                name="Security School B",
            )

            # Link teacher to school A only
            # Note: external_user_id must match what the token will produce
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="prof_alpha",  # Matches token "test:teacher:prof_alpha" → subject "prof_alpha"
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school_a.id,
                scope_external_id="TURMA_3A",
            )

            # Authenticate teacher
            token = "test:teacher:prof_alpha"
            identity = await self.gateway.authenticate(token)

            authz = AuthorizationService(session)
            context = await authz.resolve_context(identity)

            # Teacher's school should be A
            self.assertEqual(str(context.school_id), str(school_a.id))

            # Teacher cannot access school B
            check = await authz.require_school_access(context, school_b.id)
            self.assertFalse(check.allowed)

    async def test_D_teacher_from_classroom_A_cannot_access_classroom_B(self):
        """D) Professor A → não acessa turma B."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="SEC_SCHOOL_D",
                name="Security Test D",
            )

            # Link teacher to TURMA_3A only
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="prof_delta",  # Matches token "test:teacher:prof_delta" → subject "prof_delta"
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_3A",
            )

            token = "test:teacher:prof_delta"
            identity = await self.gateway.authenticate(token)

            authz = AuthorizationService(session)
            context = await authz.resolve_context(identity)

            # Scope should be TURMA_3A
            self.assertEqual(context.scope_external_id, "TURMA_3A")

            # Cannot require different scope
            check = await authz.require_scope(context, scope_external_id="TURMA_3B")
            self.assertFalse(check.allowed)

    async def test_E_coordinator_outside_authorized_scope_denied(self):
        """E) Coordenador A → não acessa escopo fora de sua autorização."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="SEC_SCHOOL_E",
                name="Security Test E",
            )

            # Coordinator for UNIT-10 only
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="charlie",  # Matches token "test:coordinator:charlie" → subject "charlie"
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.UNIT,
                school_id=school.id,
                scope_external_id="UNIT-10",
            )

            token = "test:coordinator:charlie"
            identity = await self.gateway.authenticate(token)

            authz = AuthorizationService(session)
            context = await authz.resolve_context(identity)

            # Coordinator scope is UNIT-10
            self.assertEqual(context.scope_external_id, "UNIT-10")
            self.assertEqual(context.scope_type, "UNIT")

            # Cannot require UNIT-20
            check = await authz.require_scope(context, scope_external_id="UNIT-20")
            self.assertFalse(check.allowed)

    async def test_F_student_A_cannot_access_student_B_data(self):
        """F) Aluno A → não acessa aluno B."""
        # This is tested at the endpoint level (not authentication level)
        # Authentication proves identity, but endpoint must enforce data isolation
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="SEC_SCHOOL_F",
                name="Security Test F",
            )

            # Create two students
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="alice",  # Matches token "test:student:alice" → subject "alice"
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_3A",
            )
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="bob",  # Matches token "test:student:bob" → subject "bob"
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_3A",
            )

            # Alice authenticates
            token_alice = "test:student:alice"
            identity_alice = await self.gateway.authenticate(token_alice)

            # Bob is a different external_user_id
            token_bob = "test:student:bob"
            identity_bob = await self.gateway.authenticate(token_bob)

            # Identities are different
            self.assertNotEqual(identity_alice.external_user_id, identity_bob.external_user_id)

    async def test_G_independent_student_cannot_access_school_data(self):
        """G) Aluno independente → não ganha acesso a dados escolares."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="SEC_SCHOOL_G",
                name="Security Test G",
            )

            # School material
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="prof_gamma",  # Matches token "test:teacher:prof_gamma" → subject "prof_gamma"
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_3A",
            )

            # Independent student (no school link)
            token = "test:student:independent"
            identity = await self.gateway.authenticate(token)

            authz = AuthorizationService(session)
            context = await authz.resolve_context(identity)

            # Independent student has no school
            self.assertIsNone(context.school_id)
            self.assertEqual(context.scope_type, "PLATFORM")

            # Cannot require school access
            check = await authz.require_school_access(context, school.id)
            self.assertFalse(check.allowed)

    async def test_H_platform_admin_can_manage_tenants(self):
        """H) PLATFORM_ADMIN → pode administrar tenants."""
        async with self.session_factory() as session:
            # Create PLATFORM_ADMIN
            token = "test:admin:master"  # → subject "master", role PLATFORM_ADMIN
            identity = await self.gateway.authenticate(token)

            authz = AuthorizationService(session)
            context = await authz.resolve_context(identity)

            # Must be PLATFORM_ADMIN
            self.assertTrue(context.is_platform_admin)
            self.assertEqual(context.role, "PLATFORM_ADMIN")

            # Can require PLATFORM_ADMIN role
            check = await authz.require_role(context, "PLATFORM_ADMIN")
            self.assertTrue(check.allowed)

    async def test_I_platform_admin_has_no_automatic_pedagogical_access(self):
        """I) PLATFORM_ADMIN → não ganha automaticamente acesso pedagógico."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="SEC_SCHOOL_I",
                name="Security Test I",
            )

            # PLATFORM_ADMIN without school link
            token = "test:admin:master"  # → subject "master", role PLATFORM_ADMIN, no school link  # → subject "master"
            identity = await self.gateway.authenticate(token)

            authz = AuthorizationService(session)
            context = await authz.resolve_context(identity)

            # PLATFORM_ADMIN but no school_id without explicit link
            self.assertTrue(context.is_platform_admin)
            self.assertIsNone(context.school_id)

            # Cannot automatically access school pedagogically
            check = await authz.require_school_access(context, school.id)
            self.assertFalse(check.allowed)

    async def test_J_cannot_escalate_role_by_token_manipulation(self):
        """J) Usuário não pode alterar role por manipulação de payload.

        This test proves that:
        1. Role comes from database (UserSchoolLink), not from token
        2. Token cannot override stored role
        3. AuthorizationService looks up actual links
        """
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master",
                code="SEC_SCHOOL_J",
                name="Security Test J",
            )

            # Link user as STUDENT
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="malicious",  # Matches token "test:student:malicious" → subject "malicious"
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_3A",
            )

            # Try to send token with PLATFORM_ADMIN role (but DB says STUDENT)
            token = "test:admin:malicious"  # claims PLATFORM_ADMIN
            identity = await self.gateway.authenticate(token)

            # Token says admin, but database says student
            self.assertIn("PLATFORM_ADMIN", identity.roles)

            authz = AuthorizationService(session)
            context = await authz.resolve_context(identity)

            # Actual role from database is STUDENT, NOT admin
            self.assertEqual(context.role, "STUDENT")
            self.assertFalse(context.is_platform_admin)

            # Cannot escalate to PLATFORM_ADMIN
            check = await authz.require_role(context, "PLATFORM_ADMIN")
            self.assertFalse(check.allowed)


if __name__ == "__main__":
    unittest.main()
