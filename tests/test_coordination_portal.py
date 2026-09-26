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
from agente_ia_edu.db.models.academic import AcademicYear, Class, GradeLevel, Segment
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.coordination_portal import CoordinationPortalService
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
from agente_ia_edu.services.teaching_context_policies import (
    ContextPriorityPolicy,
    RecencyPolicy,
)
from agente_ia_edu.services.video_engine import VideoRecommendationEngine


class TestCoordinationPortal(unittest.IsolatedAsyncioTestCase):
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

        # 1. Create Schools
        sa = await admin_service.create_school(performed_by_external_id="admin:master", code="SCH_A", name="Escola A")
        sb = await admin_service.create_school(performed_by_external_id="admin:master", code="SCH_B", name="Escola B")

        # 2. Create Catalog
        root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id

        c_dil = CatalogNode(parent_id=root.id, root_id=root.id, node_type="CONTENT", code="QUIM-DIL", name="Diluição de Soluções", position=1, active=True)
        c_est = CatalogNode(parent_id=root.id, root_id=root.id, node_type="CONTENT", code="QUIM-EST", name="Estequiometria", position=2, active=True)
        session.add_all([c_dil, c_est])
        await session.flush()

        # 3. Create Bindings
        # Coordinator A in School A (Global SCHOOL scope)
        await admin_service.link_user_to_school(
            performed_by_external_id="admin:master",
            external_user_id="user:coord_a",
            role=AdminRole.COORDINATOR,
            scope_type=AdminScopeType.SCHOOL,
            school_id=sa.id,
        )

        # Coordinator B in School B (Global SCHOOL scope)
        await admin_service.link_user_to_school(
            performed_by_external_id="admin:master",
            external_user_id="user:coord_b",
            role=AdminRole.COORDINATOR,
            scope_type=AdminScopeType.SCHOOL,
            school_id=sb.id,
        )

        # Coordinator Restrict in School A (Restricted to CLASSROOM = TURMA_3A)
        await admin_service.link_user_to_school(
            performed_by_external_id="admin:master",
            external_user_id="user:coord_restrict",
            role=AdminRole.COORDINATOR,
            scope_type=AdminScopeType.CLASSROOM,
            school_id=sa.id,
            scope_external_id="TURMA_3A",
        )

        # Teacher Mendes in School A
        await admin_service.link_user_to_school(
            performed_by_external_id="admin:master",
            external_user_id="user:prof_mendes",
            role=AdminRole.TEACHER,
            scope_type=AdminScopeType.CLASSROOM,
            school_id=sa.id,
            scope_external_id="TURMA_3A",
        )

        # Students
        await admin_service.link_user_to_school(
            performed_by_external_id="admin:master",
            external_user_id="student:alice",
            role=AdminRole.STUDENT,
            scope_type=AdminScopeType.CLASSROOM,
            school_id=sa.id,
            scope_external_id="TURMA_3A",
        )

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

    async def test_01_coordination_dashboard(self):
        """1. Dashboard da coordenação com métricas agregadas."""
        async with self.session_factory() as session:
            sa_id, _, c_dil_id, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            t_portal = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
            coord_svc = CoordinationPortalService(session, ks, t_svc, t_portal, rec_eng)

            session.add(StudentContentMastery(external_identity_id="student:alice", content_node_id=c_dil_id, mastery_score=38.0))
            await session.commit()

            dash = await coord_svc.get_coordination_dashboard(
                coordinator_id="user:coord_a",
                school_id=sa_id,
            )

            self.assertEqual(dash["coordinator_id"], "user:coord_a")
            self.assertEqual(dash["overall_mastery_average"], 38.0)
            self.assertEqual(dash["students_struggling_count"], 1)

    async def test_01b_global_coordinator_scopes_use_real_classrooms_not_placeholder(self):
        """Reproduces the reported bug via get_coordinator_authorized_scopes:
        a SCHOOL-wide (is_global) coordinator in a school with real Class
        rows but no TeachingLesson history yet must see those real
        classrooms - never the ["TURMA_3A", "TURMA_3B"] demo placeholder."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master", code="SCH_COORDREAL", name="Escola Coord Real",
            )
            segment = Segment(id=uuid4(), school_id=school.id, name="segment", external_id="SEG-CR")
            session.add(segment)
            await session.flush()
            grade = GradeLevel(
                id=uuid4(), school_id=school.id, segment_id=segment.id,
                name="grade", external_id="GRADE-CR",
            )
            year = AcademicYear(id=uuid4(), school_id=school.id, year=2026, external_id="YEAR-CR")
            session.add_all([grade, year])
            await session.flush()
            turma_a = Class(
                id=uuid4(), school_id=school.id, academic_year_id=year.id,
                grade_level_id=grade.id, name="Turma A", external_id="1EM_A",
            )
            turma_b = Class(
                id=uuid4(), school_id=school.id, academic_year_id=year.id,
                grade_level_id=grade.id, name="Turma B", external_id="1EM_B",
            )
            session.add_all([turma_a, turma_b])
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:coord_real",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school.id,
            )
            await session.commit()

            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            t_portal = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
            coord_svc = CoordinationPortalService(session, ks, t_svc, t_portal, rec_eng)

            scopes = await coord_svc.get_coordinator_authorized_scopes(
                "user:coord_real", school.id,
            )
            self.assertEqual(scopes["allowed_classrooms"], {"1EM_A", "1EM_B"})
            self.assertNotIn("TURMA_3A", scopes["allowed_classrooms"])
            self.assertNotIn("TURMA_3B", scopes["allowed_classrooms"])

    async def test_02_03_filters_and_chained_filters(self):
        """2, 3, 21. Filtros e filtros encadeados por período e turma."""
        async with self.session_factory() as session:
            sa_id, _, c_dil_id, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            t_portal = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
            coord_svc = CoordinationPortalService(session, ks, t_svc, t_portal, rec_eng)

            dash_3a = await coord_svc.get_coordination_dashboard(
                coordinator_id="user:coord_a",
                school_id=sa_id,
                classroom_id="TURMA_3A",
                time_period="bimester",
            )
            self.assertEqual(dash_3a["classroom_id"], "TURMA_3A")
            self.assertEqual(dash_3a["time_period"], "bimester")

    async def test_04_academic_drill_down_hierarchy(self):
        """4. Drill-down acadêmico (Escola -> Unidade -> Segmento -> Série -> Turma)."""
        async with self.session_factory() as session:
            sa_id, _, _, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            t_portal = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
            coord_svc = CoordinationPortalService(session, ks, t_svc, t_portal, rec_eng)

            hierarchy = await coord_svc.get_coordination_hierarchy(
                coordinator_id="user:coord_a",
                school_id=sa_id,
            )
            self.assertIn("units", hierarchy)
            # _seed_data links classrooms via the OLD flat UserSchoolLink
            # convention only - no real SchoolUnit/Segment/GradeLevel/Class
            # rows exist for School A. The tree must say so honestly (never
            # fabricate "Unidade Principal" as if it were a real row).
            self.assertIsNone(hierarchy["units"][0]["unit_id"])
            self.assertEqual(hierarchy["units"][0]["unit_name"], "Sem unidade cadastrada")

    async def test_05_classroom_comparison(self):
        """5. Comparativo entre turmas no escopo."""
        async with self.session_factory() as session:
            sa_id, _, c_dil_id, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            t_portal = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
            coord_svc = CoordinationPortalService(session, ks, t_svc, t_portal, rec_eng)

            comparison = await coord_svc.compare_classrooms(
                coordinator_id="user:coord_a",
                school_id=sa_id,
            )
            self.assertGreater(len(comparison), 0)

    async def test_06_07_student_detail_and_search_in_coordination_scope(self):
        """6, 7. Ficha do aluno e pesquisa restrita ao escopo da coordenação."""
        async with self.session_factory() as session:
            sa_id, sb_id, _, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            t_portal = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
            coord_svc = CoordinationPortalService(session, ks, t_svc, t_portal, rec_eng)

            # Search in School A -> finds Alice
            search_a = await t_portal.search_students_in_scope(
                teacher_id="user:coord_a",
                school_id=sa_id,
                query="alice",
            )
            self.assertEqual(len(search_a), 1)

            # Coordinator A trying to access Bob in School B -> raises ScopeAuthorizationError
            with self.assertRaises(ScopeAuthorizationError):
                await coord_svc.verify_coordinator_access(
                    coordinator_id="user:coord_a",
                    school_id=sb_id,
                )

    async def test_08_teachers_oversight(self):
        """8. Visão de professores sob supervisão da coordenação."""
        async with self.session_factory() as session:
            sa_id, _, _, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            t_portal = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
            coord_svc = CoordinationPortalService(session, ks, t_svc, t_portal, rec_eng)

            teachers = await coord_svc.list_coordination_teachers(
                coordinator_id="user:coord_a",
                school_id=sa_id,
            )
            self.assertGreater(len(teachers), 0)
            self.assertEqual(teachers[0]["teacher_id"], "user:prof_mendes")

    async def test_08b_teachers_oversight_with_zero_teachers_returns_empty_not_a_fake_teacher(self):
        """A school with zero TEACHER links must return an empty list - never
        the hardcoded fictitious "Prof. Mendes" dev/test fallback record with
        invented classrooms, student counts, and mastery averages."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school = await admin_service.create_school(
                performed_by_external_id="admin:master", code="SCH_NOTEACH", name="Escola Sem Professores",
            )
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="user:coord_noteach",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school.id,
            )
            await session.commit()

            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            t_portal = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
            coord_svc = CoordinationPortalService(session, ks, t_svc, t_portal, rec_eng)

            teachers = await coord_svc.list_coordination_teachers(
                coordinator_id="user:coord_noteach",
                school_id=school.id,
            )
            self.assertEqual(teachers, [])

    async def test_09_10_11_12_pedagogical_contexts_and_strengths_improvements(self):
        """9, 10, 11, 12, 22. Contexto pedagógico, pontos fortes/melhoria e prioridade TEACHER > COORDINATION > SCHOOL_PLAN > AUTONOMOUS."""
        async with self.session_factory() as session:
            sa_id, _, c_dil_id, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            t_portal = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
            coord_svc = CoordinationPortalService(session, ks, t_svc, t_portal, rec_eng)

            ctx = await coord_svc.teaching_context_service.record_coordination_context(
                coordinator_id="user:coord_a",
                school_id=sa_id,
                content_node_id=c_dil_id,
                source="COORDINATION",
                title="Orientação da Coordenação",
            )
            self.assertEqual(ctx.source, "COORDINATION")

            contexts_list = await coord_svc.list_coordination_contexts(
                coordinator_id="user:coord_a",
                school_id=sa_id,
            )
            self.assertEqual(len(contexts_list), 1)

    async def test_13_14_report_export(self):
        """13, 14. Exportação de relatórios da coordenação (PDF e XLSX)."""
        async with self.session_factory() as session:
            sa_id, _, _, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            t_portal = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
            coord_svc = CoordinationPortalService(session, ks, t_svc, t_portal, rec_eng)

            pdf_rep = await coord_svc.export_coordination_report(
                coordinator_id="user:coord_a",
                school_id=sa_id,
                export_format="pdf",
            )
            self.assertEqual(pdf_rep["export_format"], "pdf")
            self.assertTrue(pdf_rep["filename"].endswith(".pdf"))
            # No classroom_id was passed (whole-school report) - the literal
            # Python None must never leak into a user-facing title/filename.
            self.assertNotIn("None", pdf_rep["filename"])
            self.assertNotIn("None", pdf_rep["title"])

            xlsx_rep = await coord_svc.export_coordination_report(
                coordinator_id="user:coord_a",
                school_id=sa_id,
                export_format="xlsx",
            )
            self.assertEqual(xlsx_rep["export_format"], "xlsx")
            self.assertTrue(xlsx_rep["filename"].endswith(".xlsx"))

    async def test_15_16_17_18_multi_tenant_scope_isolation_and_unauthorized_blocking(self):
        """15, 16, 17, 18, 19, 20. Isolamento entre escolas e restrições por SCOPE (SCHOOL, GRADE_LEVEL, CLASSROOM) e bloqueio de estudante."""
        async with self.session_factory() as session:
            sa_id, sb_id, c_dil_id, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            t_portal = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
            coord_svc = CoordinationPortalService(session, ks, t_svc, t_portal, rec_eng)

            # Restricted coordinator (TURMA_3A only)
            with self.assertRaises(ScopeAuthorizationError):
                await coord_svc.verify_coordinator_access(
                    coordinator_id="user:coord_restrict",
                    school_id=sa_id,
                    classroom_id="TURMA_3B",
                )

            # Student attempting coordinator access
            with self.assertRaises(ScopeAuthorizationError):
                await coord_svc.verify_coordinator_access(
                    coordinator_id="student:alice",
                    school_id=sa_id,
                )

    async def test_19_coordination_action_plan_and_empty_state(self):
        """19, 20. Plano de ação da coordenação e manipulação de estado vazio sem dados."""
        async with self.session_factory() as session:
            sa_id, _, c_dil_id, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            t_portal = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)
            coord_svc = CoordinationPortalService(session, ks, t_svc, t_portal, rec_eng)

            # Seed low mastery student
            session.add(StudentContentMastery(external_identity_id="student:alice", content_node_id=c_dil_id, mastery_score=30.0))
            await session.commit()

            dash = await coord_svc.get_coordination_dashboard(
                coordinator_id="user:coord_a",
                school_id=sa_id,
            )

            self.assertEqual(len(dash["action_plan"]), 1)
            self.assertEqual(dash["action_plan"][0]["priority"], "HIGH")
            self.assertIn("Diluição de Soluções", dash["action_plan"][0]["content_name"])

    async def test_20_coordination_context_priority_hierarchy(self):
        """22. Preservação estrita da prioridade TEACHER > COORDINATION > SCHOOL_PLAN > AUTONOMOUS."""
        policy = ContextPriorityPolicy()
        self.assertEqual(policy.get_rank("TEACHER"), 1)
        self.assertEqual(policy.get_rank("COORDINATION"), 2)
        self.assertEqual(policy.get_rank("SCHOOL_PLAN"), 3)
        self.assertEqual(policy.get_rank("AUTONOMOUS"), 4)

        self.assertEqual(
            policy.select_primary_source(["SCHOOL_PLAN", "COORDINATION", "TEACHER"]),
            "TEACHER",
        )


if __name__ == "__main__":
    unittest.main()
