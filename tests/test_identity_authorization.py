import unittest
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School, SchoolModule, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.authorization import AuthorizationService


class TestIdentityAuthorization(unittest.IsolatedAsyncioTestCase):
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

    async def test_01_independent_student_without_school_context(self):
        async with self.session_factory() as session:
            authz = AuthorizationService(session)
            identity = ExternalIdentityContext(
                provider="TestProvider",
                external_user_id="student:independent_01",
                roles=("STUDENT",),
            )

            context = await authz.resolve_context(identity)
            self.assertEqual(context.role, "STUDENT")
            self.assertIsNone(context.school_id)
            self.assertEqual(context.scope_type, "PLATFORM")

    async def test_02_school_bound_teacher_context_is_resolved(self):
        async with self.session_factory() as session:
            admin = PlatformAdminService(session)
            school = await admin.create_school(
                performed_by_external_id="admin:master",
                code="AUTH_SCHOOL",
                name="Auth School",
            )
            await admin.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="teacher:prof_auth",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_3A",
            )

            authz = AuthorizationService(session)
            identity = ExternalIdentityContext(
                provider="TestProvider",
                external_user_id="teacher:prof_auth",
                roles=("TEACHER",),
            )

            context = await authz.resolve_context(identity)
            self.assertEqual(context.role, "TEACHER")
            self.assertEqual(str(context.school_id), str(school.id))
            self.assertEqual(context.scope_external_id, "TURMA_3A")

    async def test_03_platform_admin_is_isolated_from_pedagogical_scope(self):
        async with self.session_factory() as session:
            authz = AuthorizationService(session)
            admin_identity = ExternalIdentityContext(
                provider="TestProvider",
                external_user_id="admin:master",
                roles=("PLATFORM_ADMIN",),
            )

            context = await authz.resolve_context(admin_identity)
            self.assertTrue(context.is_platform_admin)
            self.assertEqual(context.role, "PLATFORM_ADMIN")
            self.assertIsNone(context.school_id)

            allowed = await authz.require_role(context, "PLATFORM_ADMIN")
            self.assertTrue(allowed.allowed)


if __name__ == "__main__":
    unittest.main()
