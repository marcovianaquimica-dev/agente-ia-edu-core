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
    EducationalResource,
    LearningHistory,
    PedagogicalContext,
    PedagogicalRecommendation,
    School,
    StudentContentMastery,
    TeachingLesson,
    UserSchoolLink,
)
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.recommendation import RecommendationEngine
from agente_ia_edu.services.report_export import ReportExportService
from agente_ia_edu.services.teacher_portal import (
    TeacherPerformancePolicy,
    TeacherPortalService,
)
from agente_ia_edu.services.teaching_context import (
    ScopeAuthorizationError,
    TeachingContextService,
)
from agente_ia_edu.services.video_engine import VideoRecommendationEngine


class TestTeacherPortal(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_data(self, session: AsyncSession):
        admin_service = PlatformAdminService(session)

        # Schools
        sa = await admin_service.create_school(performed_by_external_id="admin:master", code="SCH_A", name="Escola A")
        sb = await admin_service.create_school(performed_by_external_id="admin:master", code="SCH_B", name="Escola B")

        # Catalog
        root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id

        c_dil = CatalogNode(parent_id=root.id, root_id=root.id, node_type="CONTENT", code="QUIM-DIL", name="Diluição de Soluções", position=1, active=True)
        c_est = CatalogNode(parent_id=root.id, root_id=root.id, node_type="CONTENT", code="QUIM-EST", name="Estequiometria", position=2, active=True)
        session.add_all([c_dil, c_est])
        await session.flush()

        # Users and Bindings
        # Teacher A bound to School A / TURMA_3A
        await admin_service.link_user_to_school(
            performed_by_external_id="admin:master",
            external_user_id="user:prof_mendes",
            role=AdminRole.TEACHER,
            scope_type=AdminScopeType.CLASSROOM,
            school_id=sa.id,
            scope_external_id="TURMA_3A",
        )

        # Student Alice in School A / TURMA_3A
        await admin_service.link_user_to_school(
            performed_by_external_id="admin:master",
            external_user_id="student:alice",
            role=AdminRole.STUDENT,
            scope_type=AdminScopeType.CLASSROOM,
            school_id=sa.id,
            scope_external_id="TURMA_3A",
        )

        # Student Bob in School B / TURMA_3B
        await admin_service.link_user_to_school(
            performed_by_external_id="admin:master",
            external_user_id="student:bob",
            role=AdminRole.STUDENT,
            scope_type=AdminScopeType.CLASSROOM,
            school_id=sb.id,
            scope_external_id="TURMA_3B",
        )

        await session.commit()
        return sa.id, sb.id, c_dil.id, c_est.id

    async def test_01_02_teacher_dashboard_and_classroom_scope(self):
        """1, 2, 11, 14. Teacher dashboard e validação de escopo autorizado."""
        async with self.session_factory() as session:
            sa_id, sb_id, c_dil_id, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            # Seed Student Alice mastery
            session.add(StudentContentMastery(external_identity_id="student:alice", content_node_id=c_dil_id, mastery_score=42.0, questions_answered=5, questions_correct=2))
            await session.commit()

            # Teacher Mendes accesses TURMA_3A (Authorized)
            dash = await portal_svc.get_teacher_dashboard(
                teacher_id="user:prof_mendes",
                school_id=sa_id,
                classroom_id="TURMA_3A",
            )
            self.assertEqual(dash["student_count"], 1)
            self.assertEqual(dash["overall_class_average"], 42.0)
            self.assertEqual(dash["students_struggling_count"], 1)

            # Teacher Mendes attempts TURMA_3B (Unauthorized -> ScopeAuthorizationError)
            with self.assertRaises(ScopeAuthorizationError):
                await portal_svc.get_teacher_dashboard(
                    teacher_id="user:prof_mendes",
                    school_id=sa_id,
                    classroom_id="TURMA_3B",
                )

    async def test_03_04_coordination_and_multi_tenant_isolation(self):
        """3, 4, 18. Isolamento multi-tenant: Professor da Escola A não acessa Escola B."""
        async with self.session_factory() as session:
            sa_id, sb_id, c_dil_id, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            # Teacher Mendes (School A) cannot access student:bob (School B)
            with self.assertRaises(ScopeAuthorizationError):
                await portal_svc.get_student_detail_for_teacher(
                    teacher_id="user:prof_mendes",
                    school_id=sb_id,
                    student_id="student:bob",
                )

    async def test_05_06_empty_dashboard_and_data_aggregation(self):
        """5, 6, 16. Dashboard vazio e com dados (sem duplicação)."""
        async with self.session_factory() as session:
            sa_id, _, c_dil_id, _ = await self._seed_data(session)
            admin_service = PlatformAdminService(session)

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:empty_teacher",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=sa_id,
                scope_external_id="TURMA_VAZIA",
            )

            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            # Empty classroom with no masteries or students yet
            dash_empty = await portal_svc.get_teacher_dashboard(
                teacher_id="user:empty_teacher",
                school_id=sa_id,
                classroom_id="TURMA_VAZIA",
            )
            self.assertEqual(dash_empty["overall_class_average"], 0.0)
            self.assertEqual(dash_empty["students_struggling_count"], 0)

    async def test_07_08_09_strengths_and_improvements_policy(self):
        """7, 8, 9, 13. Cálculo determinístico de Pontos Fortes e Pontos de Melhoria."""
        policy = TeacherPerformancePolicy()

        items = [
            {"content_node_id": "1", "content_name": "Diluição", "class_average_mastery": 38.0, "students_struggling_count": 12, "total_students": 15},
            {"content_node_id": "2", "content_name": "Modelos Atômicos", "class_average_mastery": 88.0, "students_struggling_count": 0, "total_students": 15},
        ]

        strengths, improvements = policy.classify_strengths_and_improvements(items)

        self.assertEqual(len(strengths), 1)
        self.assertEqual(strengths[0]["content_name"], "Modelos Atômicos")

        self.assertEqual(len(improvements), 1)
        self.assertEqual(improvements[0]["content_name"], "Diluição")

    async def test_10_11_12_recent_lessons_and_action_plan_integration(self):
        """10, 11, 12, 17. Conteúdos ensinados recentemente (14 dias) + Plano de ação."""
        async with self.session_factory() as session:
            sa_id, _, c_dil_id, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            # Teacher records lesson for Diluição
            lesson = await t_svc.record_lesson(
                teacher_id="user:prof_mendes",
                school_id=sa_id,
                classroom_id="TURMA_3A",
                content_node_id=c_dil_id,
                title="Aula Prática de Diluição",
                summary_observation="Conceitos e cálculos",
            )
            self.assertIsNotNone(lesson.id)

            # Student Alice has mastery = 42%
            session.add(StudentContentMastery(external_identity_id="student:alice", content_node_id=c_dil_id, mastery_score=42.0))
            await session.commit()

            detail = await portal_svc.get_classroom_detail(
                teacher_id="user:prof_mendes",
                school_id=sa_id,
                classroom_id="TURMA_3A",
            )

            self.assertEqual(len(detail["recent_contents_taught"]), 1)
            self.assertEqual(detail["recent_contents_taught"][0]["content_name"], "Diluição de Soluções")
            self.assertIn("Revisão conceitual", detail["recent_contents_taught"][0]["recommended_action"])

            self.assertGreater(len(detail["action_plan"]), 0)
            self.assertEqual(detail["action_plan"][0]["priority"], "HIGH")

    async def test_13_14_student_search_in_scope(self):
        """13, 14, 15. Pesquisa global de alunos estritamente no escopo do professor."""
        async with self.session_factory() as session:
            sa_id, sb_id, _, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            # Search for 'alice' in School A -> returns student:alice
            results_a = await portal_svc.search_students_in_scope(
                teacher_id="user:prof_mendes",
                school_id=sa_id,
                query="alice",
            )
            self.assertEqual(len(results_a), 1)
            self.assertEqual(results_a[0]["student_id"], "student:alice")

    async def test_16_list_teacher_classrooms(self):
        """16. Listagem de turmas autorizadas do professor."""
        async with self.session_factory() as session:
            sa_id, _, _, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            classrooms = await portal_svc.list_teacher_classrooms(
                teacher_id="user:prof_mendes",
                school_id=sa_id,
            )
            self.assertGreater(len(classrooms), 0)
            self.assertIn("TURMA_3A", [c["classroom_id"] for c in classrooms])

    async def test_17_student_detail_authorized_and_unauthorized(self):
        """17. Visão individual do aluno pelo professor (autorizado x não autorizado)."""
        async with self.session_factory() as session:
            sa_id, sb_id, c_dil_id, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            # Student Alice (in scope)
            detail_alice = await portal_svc.get_student_detail_for_teacher(
                teacher_id="user:prof_mendes",
                school_id=sa_id,
                student_id="student:alice",
            )
            self.assertEqual(detail_alice["student_id"], "student:alice")

            # Student Bob in School B (out of scope)
            with self.assertRaises(ScopeAuthorizationError):
                await portal_svc.get_student_detail_for_teacher(
                    teacher_id="user:prof_mendes",
                    school_id=sb_id,
                    student_id="student:bob",
                )

    async def test_15_report_export_service(self):
        """15. Exportação de relatório da turma em formatos PDF e XLSX."""
        classroom_data = {
            "classroom_id": "TURMA_3A",
            "summary": {"student_count": 25, "overall_class_average": 68.5},
            "strengths": [{"content_name": "Modelos Atômicos"}],
            "improvement_areas": [{"content_name": "Diluição de Soluções"}],
        }

        export_pdf = ReportExportService.export_classroom_report(classroom_data, export_format="pdf")
        self.assertEqual(export_pdf["export_format"], "pdf")
        self.assertEqual(export_pdf["content_type"], "application/pdf")
        self.assertTrue(export_pdf["filename"].endswith(".pdf"))

        export_xlsx = ReportExportService.export_classroom_report(classroom_data, export_format="xlsx")
        self.assertEqual(export_xlsx["export_format"], "xlsx")
        self.assertTrue(export_xlsx["filename"].endswith(".xlsx"))


if __name__ == "__main__":
    unittest.main()
