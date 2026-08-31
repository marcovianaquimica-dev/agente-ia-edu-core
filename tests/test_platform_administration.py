import asyncio
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AdminAuditLog,
    School,
    SchoolModule,
    UserSchoolLink,
)
from agente_ia_edu.services.admin import (
    AdminRole,
    AdminScopeType,
    PlatformAdminService,
    PlatformModuleKey,
    SchoolStatus,
)


class TestPlatformAdministration(unittest.IsolatedAsyncioTestCase):
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

    async def test_01_create_school_and_get_school(self):
        """1. Criar escola/tenant com módulo default AGENTE_IA_EDU ativado."""
        async with self.session_factory() as session:
            service = PlatformAdminService(session)

            school = await service.create_school(
                performed_by_external_id="admin:master",
                code="ESCOLA_ALPHA",
                name="Escola Alpha de Ensino Médio",
                short_name="Alpha",
                external_identifier="EXT_ALPHA_101",
            )

            self.assertIsNotNone(school.id)
            self.assertEqual(school.code, "ESCOLA_ALPHA")
            self.assertEqual(school.status, SchoolStatus.ACTIVE)

            fetched = await service.get_school(school.id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.name, "Escola Alpha de Ensino Médio")

            # Check default module enabled
            is_enabled = await service.is_module_enabled(school.id, PlatformModuleKey.AGENTE_IA_EDU)
            self.assertTrue(is_enabled)

    async def test_02_update_school_status(self):
        """2. Atualização de dados da escola e transição de status (SUSPENDED)."""
        async with self.session_factory() as session:
            service = PlatformAdminService(session)
            school = await service.create_school(
                performed_by_external_id="admin:master",
                code="ESCOLA_BETA",
                name="Escola Beta",
            )

            updated = await service.update_school(
                performed_by_external_id="admin:master",
                school_id=school.id,
                status=SchoolStatus.SUSPENDED,
            )
            self.assertEqual(updated.status, SchoolStatus.SUSPENDED)

            # Suspended school should not have active module operations
            is_enabled = await service.is_module_enabled(school.id, PlatformModuleKey.AGENTE_IA_EDU)
            self.assertFalse(is_enabled)

    async def test_03_configure_modules(self):
        """3. Habilitação/Desabilitação de módulos (AGENTE_IA_EDU, REDACAO_IA)."""
        async with self.session_factory() as session:
            service = PlatformAdminService(session)
            school = await service.create_school(
                performed_by_external_id="admin:master",
                code="ESCOLA_GAMMA",
                name="Escola Gamma",
            )

            # Enable REDACAO_IA
            mod = await service.configure_school_module(
                performed_by_external_id="admin:master",
                school_id=school.id,
                module_key=PlatformModuleKey.REDACAO_IA,
                enabled=True,
            )
            self.assertTrue(mod.enabled)
            self.assertTrue(await service.is_module_enabled(school.id, PlatformModuleKey.REDACAO_IA))

            # Disable REDACAO_IA
            mod_disabled = await service.configure_school_module(
                performed_by_external_id="admin:master",
                school_id=school.id,
                module_key=PlatformModuleKey.REDACAO_IA,
                enabled=False,
            )
            self.assertFalse(mod_disabled.enabled)
            self.assertFalse(await service.is_module_enabled(school.id, PlatformModuleKey.REDACAO_IA))

    async def test_04_user_link_roles_and_scopes(self):
        """4. Vínculo de usuários a papéis (DIRECTOR, TEACHER) e escopos (SCHOOL, CLASSROOM)."""
        async with self.session_factory() as session:
            service = PlatformAdminService(session)
            school = await service.create_school(
                performed_by_external_id="admin:master",
                code="ESCOLA_DELTA",
                name="Escola Delta",
            )

            link_dir = await service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:director_1",
                role=AdminRole.DIRECTOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school.id,
            )
            self.assertEqual(link_dir.role, AdminRole.DIRECTOR)

            link_teacher = await service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:prof_mendes",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_3A",
            )
            self.assertEqual(link_teacher.scope_external_id, "TURMA_3A")

            links = await service.get_user_active_links("user:prof_mendes")
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0].role, AdminRole.TEACHER)

    async def test_05_admin_audit_logs(self):
        """5. Rastreabilidade e logs de auditoria administrativa."""
        async with self.session_factory() as session:
            service = PlatformAdminService(session)
            school = await service.create_school(
                performed_by_external_id="admin:master",
                code="ESCOLA_EPSILON",
                name="Escola Epsilon",
            )

            logs = await service.list_audit_logs(school_id=school.id)
            self.assertGreater(len(logs), 0)
            self.assertEqual(logs[0].action, "SCHOOL_CREATED")
            self.assertEqual(logs[0].performed_by_external_id, "admin:master")

    async def test_06_multi_tenant_isolation_schools(self):
        """6. Isolamento multi-tenant: Escola A x Escola B x Admin Master."""
        async with self.session_factory() as session:
            service = PlatformAdminService(session)
            school_a = await service.create_school(
                performed_by_external_id="admin:master",
                code="ESCOLA_A",
                name="Escola A",
            )
            school_b = await service.create_school(
                performed_by_external_id="admin:master",
                code="ESCOLA_B",
                name="Escola B",
            )

            await service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:teacher_a",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )

            # Teacher A has active link in School A only
            links_a = await service.get_user_active_links("user:teacher_a")
            self.assertEqual(len(links_a), 1)
            self.assertEqual(links_a[0].school_id, school_a.id)
            self.assertNotEqual(links_a[0].school_id, school_b.id)

            # Platform Admin master has global PLATFORM_ADMIN role
            await service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="admin:master",
                role=AdminRole.PLATFORM_ADMIN,
                scope_type=AdminScopeType.PLATFORM,
            )
            is_master = await service.is_platform_admin("admin:master")
            self.assertTrue(is_master)

    async def test_07_deactivated_user_link(self):
        """7. Vínculo desativado bloqueia acesso do usuário."""
        async with self.session_factory() as session:
            service = PlatformAdminService(session)
            school = await service.create_school(
                performed_by_external_id="admin:master",
                code="ESCOLA_ZETA",
                name="Escola Zeta",
            )

            link = await service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:inactive_teacher",
                role=AdminRole.TEACHER,
                school_id=school.id,
            )

            await service.deactivate_user_link(
                performed_by_external_id="admin:master",
                link_id=link.id,
            )

            active_links = await service.get_user_active_links("user:inactive_teacher")
            self.assertEqual(len(active_links), 0)

    async def test_08_multiple_user_links_preserved(self):
        """8. Usuário com múltiplos vínculos em escolas diferentes."""
        async with self.session_factory() as session:
            service = PlatformAdminService(session)
            sa = await service.create_school(performed_by_external_id="admin:master", code="SCH_1", name="Escola 1")
            sb = await service.create_school(performed_by_external_id="admin:master", code="SCH_2", name="Escola 2")

            await service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:multi_teacher",
                role=AdminRole.TEACHER,
                school_id=sa.id,
            )
            await service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:multi_teacher",
                role=AdminRole.COORDINATOR,
                school_id=sb.id,
            )

            links = await service.get_user_active_links("user:multi_teacher")
            self.assertEqual(len(links), 2)
            roles = {l.role for l in links}
            self.assertEqual(roles, {AdminRole.TEACHER, AdminRole.COORDINATOR})


if __name__ == "__main__":
    unittest.main()
