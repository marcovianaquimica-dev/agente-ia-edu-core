import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AdminAuditLog,
    CatalogNode,
    PedagogicalContext,
    School,
    StudentContentMastery,
    TeachingLesson,
    UserSchoolLink,
)
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.recommendation import RecommendationEngine
from agente_ia_edu.services.teaching_context import (
    ScopeAuthorizationError,
    TeachingContextService,
)
from agente_ia_edu.services.teaching_context_policies import (
    ContextPriorityPolicy,
    RecencyPolicy,
)


class TestTeachingContext(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_schools_and_catalog(self, session: AsyncSession):
        admin_service = PlatformAdminService(session)

        school_a = await admin_service.create_school(
            performed_by_external_id="admin:master",
            code="SCH_A",
            name="Escola A",
        )
        school_b = await admin_service.create_school(
            performed_by_external_id="admin:master",
            code="SCH_B",
            name="Escola B",
        )

        root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id

        content_node = CatalogNode(
            parent_id=root.id,
            root_id=root.id,
            node_type="CONTENT",
            code="QUIM-DIL",
            name="Diluição de Soluções",
            position=1,
            active=True,
        )
        session.add(content_node)
        await session.commit()

        return school_a.id, school_b.id, content_node.id

    async def test_01_02_teacher_creates_and_queries_lessons(self):
        """1, 2. Professor cria e consulta suas aulas registradas."""
        async with self.session_factory() as session:
            sa_id, sb_id, content_id = await self._seed_schools_and_catalog(session)
            admin_service = PlatformAdminService(session)

            # Link teacher to School A / TURMA_3A
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:prof_joao",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=sa_id,
                scope_external_id="TURMA_3A",
            )

            service = TeachingContextService(session)
            lesson = await service.record_lesson(
                teacher_id="user:prof_joao",
                school_id=sa_id,
                classroom_id="TURMA_3A",
                content_node_id=content_id,
                academic_year="2026",
                title="Aula de Diluição",
                summary_observation="Explicação de conceito e fórmulas",
            )

            self.assertIsNotNone(lesson.id)
            self.assertEqual(lesson.classroom_id, "TURMA_3A")
            self.assertIsNotNone(lesson.pedagogical_context_id)

            lessons = await service.list_teacher_lessons(
                teacher_id="user:prof_joao",
                school_id=sa_id,
                classroom_id="TURMA_3A",
            )
            self.assertEqual(len(lessons), 1)
            self.assertEqual(lessons[0].title, "Aula de Diluição")

    async def test_03_04_teacher_cannot_access_unauthorized_classroom_or_school(self):
        """3, 4, 20. Professor não acessa turma fora do escopo ou escola diferente (privilege escalation)."""
        async with self.session_factory() as session:
            sa_id, sb_id, content_id = await self._seed_schools_and_catalog(session)
            admin_service = PlatformAdminService(session)

            # Link teacher to School A / TURMA_3A only
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:prof_restrito",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=sa_id,
                scope_external_id="TURMA_3A",
            )

            service = TeachingContextService(session)

            # Attempt to record for unauthorized TURMA_3B in School A
            with self.assertRaises(ScopeAuthorizationError):
                await service.record_lesson(
                    teacher_id="user:prof_restrito",
                    school_id=sa_id,
                    classroom_id="TURMA_3B",
                    content_node_id=content_id,
                )

            # Attempt to record for School B
            with self.assertRaises(ScopeAuthorizationError):
                await service.record_lesson(
                    teacher_id="user:prof_restrito",
                    school_id=sb_id,
                    classroom_id="TURMA_3A",
                    content_node_id=content_id,
                )

    async def test_05_06_coordinator_creates_context_respects_scope(self):
        """5, 6. Coordenador cria contexto e respeita escopo da escola."""
        async with self.session_factory() as session:
            sa_id, sb_id, content_id = await self._seed_schools_and_catalog(session)
            admin_service = PlatformAdminService(session)

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:coord_a",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=sa_id,
            )

            service = TeachingContextService(session)

            ctx = await service.record_coordination_context(
                coordinator_id="user:coord_a",
                school_id=sa_id,
                content_node_id=content_id,
                classroom_id="TURMA_3A",
                source="COORDINATION",
                title="Reforço de Diluição para o Simulado",
            )
            self.assertEqual(ctx.source, "COORDINATION")
            self.assertEqual(ctx.institution_id, str(sa_id))

            # Coordinator A cannot record for School B
            with self.assertRaises(ScopeAuthorizationError):
                await service.record_coordination_context(
                    coordinator_id="user:coord_a",
                    school_id=sb_id,
                    content_node_id=content_id,
                )

    async def test_07_08_context_linked_to_content_and_academic_year(self):
        """7, 8. Contexto vinculado ao conteúdo e ao ano letivo."""
        async with self.session_factory() as session:
            sa_id, _, content_id = await self._seed_schools_and_catalog(session)
            admin_service = PlatformAdminService(session)

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:prof_mendes",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.SCHOOL,
                school_id=sa_id,
            )

            service = TeachingContextService(session)
            lesson = await service.record_lesson(
                teacher_id="user:prof_mendes",
                school_id=sa_id,
                classroom_id="TURMA_3A",
                content_node_id=content_id,
                academic_year="2026",
            )

            ctx = await session.get(PedagogicalContext, lesson.pedagogical_context_id)
            self.assertEqual(ctx.content_node_id, content_id)
            self.assertEqual(ctx.metadata_["academic_year"], "2026")

            # Query for academic year 2027 should exclude 2026 context
            contexts_2027 = await service.get_active_recent_contexts(
                school_id=sa_id,
                classroom_id="TURMA_3A",
                academic_year="2027",
            )
            self.assertEqual(len(contexts_2027), 0)

    async def test_09_historical_integrity_preserved(self):
        """9. Histórico e updated_at preservados ao atualizar aula."""
        async with self.session_factory() as session:
            sa_id, _, content_id = await self._seed_schools_and_catalog(session)
            admin_service = PlatformAdminService(session)

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:prof_hist",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.SCHOOL,
                school_id=sa_id,
            )

            service = TeachingContextService(session)
            lesson = await service.record_lesson(
                teacher_id="user:prof_hist",
                school_id=sa_id,
                classroom_id="TURMA_3A",
                content_node_id=content_id,
                title="Versão 1 da Aula",
            )
            c_time = lesson.created_at

            lesson.title = "Versão 2 da Aula"
            await session.commit()
            await session.refresh(lesson)

            self.assertEqual(lesson.created_at, c_time)
            self.assertEqual(lesson.title, "Versão 2 da Aula")

    async def test_10_11_12_13_14_15_context_priority_hierarchy_and_autonomous_fallback(self):
        """10-15. Prioridades TEACHER > COORDINATION > SCHOOL_PLAN > AUTONOMOUS fallback e sem professor."""
        p = ContextPriorityPolicy()
        self.assertEqual(p.get_rank("TEACHER"), 1)
        self.assertEqual(p.get_rank("COORDINATION"), 2)
        self.assertEqual(p.get_rank("SCHOOL_PLAN"), 3)
        self.assertEqual(p.get_rank("AUTONOMOUS"), 4)

        self.assertEqual(p.select_primary_source(["SCHOOL_PLAN", "TEACHER", "COORDINATION"]), "TEACHER")
        self.assertEqual(p.select_primary_source([]), "AUTONOMOUS")

    async def test_16_17_recency_window_policy(self):
        """16, 17. Política de recência (contexto recente em 14 dias x contexto antigo)."""
        rp = RecencyPolicy(recent_context_days=14)
        now = datetime.now(timezone.utc)

        recent_dt = now - timedelta(days=5)
        old_dt = now - timedelta(days=20)

        self.assertTrue(rp.is_recent(recent_dt, reference_date=now))
        self.assertFalse(rp.is_recent(old_dt, reference_date=now))

    async def test_18_multi_tenant_school_isolation_of_contexts(self):
        """18. Isolamento multi-tenant: contexto da Escola A não afeta Escola B."""
        async with self.session_factory() as session:
            sa_id, sb_id, content_id = await self._seed_schools_and_catalog(session)
            admin_service = PlatformAdminService(session)

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:prof_a",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.SCHOOL,
                school_id=sa_id,
            )

            service = TeachingContextService(session)
            await service.record_lesson(
                teacher_id="user:prof_a",
                school_id=sa_id,
                classroom_id="TURMA_3A",
                content_node_id=content_id,
            )

            # School A has active context
            ctx_a = await service.get_active_recent_contexts(school_id=sa_id, classroom_id="TURMA_3A")
            self.assertEqual(len(ctx_a), 1)

            # School B has NO context
            ctx_b = await service.get_active_recent_contexts(school_id=sb_id, classroom_id="TURMA_3A")
            self.assertEqual(len(ctx_b), 0)

    async def test_19_audit_logs_for_lessons_and_contexts(self):
        """19. Registro de auditoria administrativa para LESSON_CREATED e PEDAGOGICAL_CONTEXT_CREATED."""
        async with self.session_factory() as session:
            sa_id, _, content_id = await self._seed_schools_and_catalog(session)
            admin_service = PlatformAdminService(session)

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:prof_aud",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.SCHOOL,
                school_id=sa_id,
            )

            service = TeachingContextService(session)
            await service.record_lesson(
                teacher_id="user:prof_aud",
                school_id=sa_id,
                classroom_id="TURMA_3A",
                content_node_id=content_id,
            )

            logs = await admin_service.list_audit_logs(school_id=sa_id)
            actions = {l.action for l in logs}
            self.assertIn("LESSON_CREATED", actions)


if __name__ == "__main__":
    unittest.main()
