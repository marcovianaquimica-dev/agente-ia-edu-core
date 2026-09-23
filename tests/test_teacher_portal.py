import asyncio
import unittest
import unittest.mock
import uuid
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
from agente_ia_edu.db.models.academic import AcademicYear, Class, GradeLevel, Segment, SchoolUnit
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

    async def test_02b_struggling_developing_mastered_count_distinct_students_not_mastery_rows(self):
        """A student with mastery in multiple contents must be counted once in
        the dashboard's struggling/developing/mastered buckets (by their
        average across contents), not once per content row - otherwise the
        three counts can sum to more than student_count."""
        async with self.session_factory() as session:
            sa_id, _, c_dil_id, c_est_id = await self._seed_data(session)
            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            # Alice has TWO mastery rows: struggling in one content (30),
            # mastered in another (90) - average is 60 (developing).
            session.add(StudentContentMastery(external_identity_id="student:alice", content_node_id=c_dil_id, mastery_score=30.0, questions_answered=5, questions_correct=1))
            session.add(StudentContentMastery(external_identity_id="student:alice", content_node_id=c_est_id, mastery_score=90.0, questions_answered=5, questions_correct=5))
            await session.commit()

            dash = await portal_svc.get_teacher_dashboard(
                teacher_id="user:prof_mendes",
                school_id=sa_id,
                classroom_id="TURMA_3A",
            )
            self.assertEqual(dash["student_count"], 1)
            total_bucketed = (
                dash["students_struggling_count"]
                + dash["students_developing_count"]
                + dash["students_mastered_count"]
            )
            self.assertEqual(
                total_bucketed, dash["student_count"],
                "struggling+developing+mastered must not exceed the real student count",
            )
            self.assertEqual(dash["students_developing_count"], 1)

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

    async def test_16b_list_teacher_classrooms_resolves_class_id_for_matching_code(self):
        """A classroom code with a real matching Class row (same school_id,
        same external_id) must carry that row's UUID as class_id - this is
        what lets a caller (e.g. the essay-review assign-to-class form) POST
        a real class_id FK instead of the legacy free-text classroom_id."""
        async with self.session_factory() as session:
            sa_id, _, _, _ = await self._seed_data(session)

            unit = SchoolUnit(id=uuid.uuid4(), school_id=sa_id, name="unit", external_id="UNIT-1")
            segment = Segment(id=uuid.uuid4(), school_id=sa_id, name="segment", external_id="SEG-1")
            session.add_all([unit, segment])
            await session.flush()
            grade = GradeLevel(
                id=uuid.uuid4(), school_id=sa_id, segment_id=segment.id,
                name="grade", external_id="GRADE-1",
            )
            year = AcademicYear(id=uuid.uuid4(), school_id=sa_id, year=2026, external_id="YEAR-1")
            session.add_all([grade, year])
            await session.flush()
            klass = Class(
                id=uuid.uuid4(), school_id=sa_id, academic_year_id=year.id,
                grade_level_id=grade.id, name="3ª Série A", external_id="TURMA_3A",
            )
            session.add(klass)
            await session.commit()

            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            classrooms = await portal_svc.list_teacher_classrooms(
                teacher_id="user:prof_mendes",
                school_id=sa_id,
            )
            turma_3a = next(c for c in classrooms if c["classroom_id"] == "TURMA_3A")
            self.assertEqual(turma_3a["class_id"], klass.id)

    async def test_16c_list_teacher_classrooms_class_id_none_when_unresolved(self):
        """A classroom code with NO matching Class row (school hierarchy not
        backfilled yet, or a stale/placeholder code) must degrade gracefully:
        class_id comes back None and the call still succeeds - it must never
        raise or block the classroom list."""
        async with self.session_factory() as session:
            sa_id, _, _, _ = await self._seed_data(session)
            # No Class rows seeded at all: TURMA_3A has no matching hierarchy row.

            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            classrooms = await portal_svc.list_teacher_classrooms(
                teacher_id="user:prof_mendes",
                school_id=sa_id,
            )
            turma_3a = next(c for c in classrooms if c["classroom_id"] == "TURMA_3A")
            self.assertIsNone(turma_3a["class_id"])

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

    async def test_verify_student_access_denies_unlinked_student_regardless_of_teacher_scope(self):
        """A student_id with ZERO active UserSchoolLink rows - e.g. mastery/
        history data orphaned by a pipeline that never linked the student -
        must be denied, not granted just because the teacher happens to have
        at least one authorized classroom somewhere in school_id."""
        async with self.session_factory() as session:
            sa_id, _, c_dil_id, _ = await self._seed_data(session)
            session.add(StudentContentMastery(
                external_identity_id="orphan-student",
                content_node_id=c_dil_id,
                mastery_score=91.0,
            ))
            await session.commit()

            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            with self.assertRaises(ScopeAuthorizationError):
                await portal_svc.get_student_detail_for_teacher(
                    teacher_id="user:prof_mendes",
                    school_id=sa_id,
                    student_id="orphan-student",
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

    async def test_fetch_students_in_classrooms_returns_empty_not_other_schools_data(self):
        """When the school-scoped query finds zero students, the function
        must return [] - not fall through to an unfiltered query across
        every school's StudentContentMastery rows."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school_a = School(id=uuid4(), code="SCH_MASTERY_A", name="Escola A Mastery")
            school_b = School(id=uuid4(), code="SCH_MASTERY_B", name="Escola B Mastery")
            session.add_all([school_a, school_b])
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="student:alice-A",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            session.add(StudentContentMastery(
                external_identity_id="student:alice-A",
                content_node_id=uuid4(),
                mastery_score=80.0,
            ))
            await session.commit()

            portal = TeacherPortalService(session, None, None, None)
            students = await portal._fetch_students_in_classrooms(school_b.id, [], school_wide=False)

        self.assertEqual(students, [])

    async def test_verify_student_access_denies_school_scoped_student_for_unauthorized_teacher(self):
        """A SCHOOL-scoped student link must not bypass the teacher-side check.
        Before this fix, ANY teacher (even one with zero authorization in
        school_id) was granted access to a student holding a SCHOOL or
        PLATFORM scoped link there."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school_a = School(id=uuid4(), code="SCH_SCOPE_A", name="Escola A Scope")
            school_b = School(id=uuid4(), code="SCH_SCOPE_B", name="Escola B Scope")
            session.add_all([school_a, school_b])
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="teacher-b-only",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school_b.id,
                scope_external_id="TURMA_B1",
            )
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="student-school-scoped-a",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            await session.commit()

            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            with self.assertRaises(ScopeAuthorizationError):
                await portal_svc.verify_student_access(
                    teacher_id="teacher-b-only",
                    school_id=school_a.id,
                    student_id="student-school-scoped-a",
                )

    async def test_verify_student_access_allows_school_scoped_student_for_real_director(self):
        """A real DIRECTOR of school_id, with no TeachingLesson rows yet (a
        fresh school), must still be allowed. This no longer depends on
        get_teacher_authorized_classrooms's ["TURMA_3A"] placeholder fallback -
        see _teacher_is_school_wide_authorized - but the end-to-end outcome
        must not regress either way."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school_a = School(id=uuid4(), code="SCH_SCOPE_C", name="Escola C Scope")
            session.add(school_a)
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="director-a",
                role=AdminRole.DIRECTOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="student-school-scoped-c",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            await session.commit()

            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            allowed = await portal_svc.verify_student_access(
                teacher_id="director-a",
                school_id=school_a.id,
                student_id="student-school-scoped-c",
            )
            self.assertTrue(allowed)

    async def test_verify_student_access_survives_removal_of_the_turma_3a_placeholder(self):
        """Proves the decoupling directly: even if get_teacher_authorized_classrooms
        returned [] (e.g. once the R0 spec's step 6 removes the ["TURMA_3A"]
        placeholder fallback), a real DIRECTOR must still be authorized for a
        SCHOOL-scoped student - because verify_student_access's SCHOOL/PLATFORM
        branch now checks the teacher's own UserSchoolLink role directly,
        never the classroom list's emptiness."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school_a = School(id=uuid4(), code="SCH_SCOPE_NOPLACEHOLDER", name="Escola Sem Placeholder")
            session.add(school_a)
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="director-noplaceholder",
                role=AdminRole.DIRECTOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="student-noplaceholder",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            await session.commit()

            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            with unittest.mock.patch.object(
                TeacherPortalService, "get_teacher_authorized_classrooms",
                return_value=[],
            ):
                allowed = await portal_svc.verify_student_access(
                    teacher_id="director-noplaceholder",
                    school_id=school_a.id,
                    student_id="student-noplaceholder",
                )
            self.assertTrue(allowed)

    async def test_fetch_students_in_classrooms_denies_school_scoped_student_with_no_authorization(self):
        """The SCHOOL leg of the query's or_ must not match when classrooms
        is empty - an empty list means the caller has zero authorization in
        school_id, and a SCHOOL-scoped student must not leak through anyway."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school_a = School(id=uuid4(), code="SCH_SCOPE_D", name="Escola D Scope")
            session.add(school_a)
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="student-school-scoped-d",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            await session.commit()

            portal = TeacherPortalService(session, None, None, None)
            students = await portal._fetch_students_in_classrooms(school_a.id, [], school_wide=False)

        self.assertEqual(students, [])

    async def test_fetch_students_in_classrooms_allows_school_scoped_student_for_teacher_with_classroom(self):
        """A genuinely school-wide caller (school_wide=True) with a real,
        non-empty classroom list must still see SCHOOL-scoped students
        alongside their own classroom's - the SCHOOL leg is gated on
        school_wide AND classrooms being non-empty, not on either alone."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school_a = School(id=uuid4(), code="SCH_SCOPE_E", name="Escola E Scope")
            session.add(school_a)
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="student-school-scoped-e",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            await session.commit()

            portal = TeacherPortalService(session, None, None, None)
            students = await portal._fetch_students_in_classrooms(
                school_a.id, ["TURMA_E1"], school_wide=True,
            )

        self.assertEqual(students, ["student-school-scoped-e"])

    async def test_fetch_students_in_classrooms_includes_platform_scoped_student(self):
        """verify_student_access already authorizes a teacher to see a
        PLATFORM-scoped student (same as SCHOOL-scoped); the aggregate query
        must include them too, not just the individual-lookup path - when
        the caller is genuinely school_wide."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school_a = School(id=uuid4(), code="SCH_SCOPE_F", name="Escola F Scope")
            session.add(school_a)
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="student-platform-scoped-f",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.PLATFORM,
                school_id=school_a.id,
            )
            await session.commit()

            portal = TeacherPortalService(session, None, None, None)
            students = await portal._fetch_students_in_classrooms(
                school_a.id, ["TURMA_F1"], school_wide=True,
            )

        self.assertEqual(students, ["student-platform-scoped-f"])

    async def test_fetch_students_in_classrooms_excludes_school_scoped_student_when_not_school_wide(self):
        """The actual regression this onda fixes: a CLASSROOM-scoped teacher
        (school_wide=False) with a real, non-empty classroom list must NOT
        see SCHOOL-scoped students who aren't in that classroom -
        verify_student_access already denies them this exact student
        one-by-one; the aggregate/roster/search path must agree, not leak
        them back in just because the classroom list was non-empty."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school_a = School(id=uuid4(), code="SCH_SCOPE_G", name="Escola G Scope")
            session.add(school_a)
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="student-school-scoped-g",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            await session.commit()

            portal = TeacherPortalService(session, None, None, None)
            students = await portal._fetch_students_in_classrooms(
                school_a.id, ["TURMA_G1"], school_wide=False,
            )

        self.assertEqual(students, [])


if __name__ == "__main__":
    unittest.main()
