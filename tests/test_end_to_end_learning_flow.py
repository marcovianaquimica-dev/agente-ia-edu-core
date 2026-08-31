import asyncio
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AdminAuditLog,
    CatalogNode,
    ContentQuestionLink,
    ContentResourceLink,
    EducationalResource,
    InitialDiagnostic,
    LearningHistory,
    PedagogicalClassification,
    PedagogicalContext,
    PedagogicalRecommendation,
    PracticeSession,
    Question,
    QuestionClassification,
    QuestionOption,
    QuestionVersion,
    School,
    StudentContentMastery,
    Taxonomy,
    TaxonomyNode,
    TeachingLesson,
    UserSchoolLink,
    VideoResourceDetail,
)
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.initial_diagnostic import (
    DiagnosticStatus,
    DiagnosticStoppingPolicy,
    InitialDiagnosticService,
)
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.learning_path import PracticeSessionService, QuestionSelectionService
from agente_ia_edu.services.recommendation import RecommendationEngine
from agente_ia_edu.services.teaching_context import TeachingContextService
from agente_ia_edu.services.video_engine import VideoRecommendationEngine


class TestEndToEndLearningFlow(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_infrastructure(self, session: AsyncSession):
        admin_service = PlatformAdminService(session)

        # 1. School Tenant
        school = await admin_service.create_school(
            performed_by_external_id="admin:master",
            code="SCH_E2E",
            name="Escola E2E Partner",
        )

        # 2. Catalog Nodes
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
        await session.flush()

        # 3. Educational Resources (Theory Material & Video)
        res_material = EducationalResource(
            title="Apostila Completa - Diluição de Soluções",
            resource_type="THEORY_MATERIAL",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            status="active",
        )
        res_video = EducationalResource(
            title="Videoaula: Conceito de Diluição",
            resource_type="VIDEO",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            source_url="https://youtube.com/watch?v=dil_e2e",
            status="active",
        )
        session.add_all([res_material, res_video])
        await session.flush()

        session.add(VideoResourceDetail(resource_id=res_video.id, platform="YOUTUBE", external_video_id="dil_e2e", duration_seconds=600))
        session.add_all([
            ContentResourceLink(content_node_id=content_node.id, resource_id=res_material.id, pedagogical_role="THEORY", recommended_level="EASY"),
            ContentResourceLink(content_node_id=content_node.id, resource_id=res_video.id, pedagogical_role="VIDEO", recommended_level="EASY"),
        ])

        # 4. Questions (EASY & MEDIUM)
        q_easy = Question(validation_status="approved")
        q_med = Question(validation_status="approved")
        session.add_all([q_easy, q_med])
        await session.flush()

        v_easy = QuestionVersion(question_id=q_easy.id, version_kind="official_original", canonical_text="Questão de Diluição fácil E2E", statement="Questão fácil", content_hash="h-e2e-easy", recommended_difficulty="EASY")
        v_med = QuestionVersion(question_id=q_med.id, version_kind="official_original", canonical_text="Questão de Diluição média E2E", statement="Questão média", content_hash="h-e2e-med", recommended_difficulty="MEDIUM")
        session.add_all([v_easy, v_med])
        await session.flush()

        opt_e1 = QuestionOption(question_version_id=v_easy.id, option_key="A", position=1, text="Correta", is_valid_option=True)
        opt_e2 = QuestionOption(question_version_id=v_easy.id, option_key="B", position=2, text="Incorreta", is_valid_option=False)
        opt_m1 = QuestionOption(question_version_id=v_med.id, option_key="A", position=1, text="Correta", is_valid_option=True)
        opt_m2 = QuestionOption(question_version_id=v_med.id, option_key="B", position=2, text="Incorreta", is_valid_option=False)
        session.add_all([opt_e1, opt_e2, opt_m1, opt_m2])

        session.add_all([
            ContentQuestionLink(content_node_id=content_node.id, question_version_id=v_easy.id),
            ContentQuestionLink(content_node_id=content_node.id, question_version_id=v_med.id),
        ])

        # Taxonomy and Classifications for candidate lookup
        tax = Taxonomy(code="bncc", name="BNCC", version="1.0")
        session.add(tax)
        await session.flush()

        t_node = TaxonomyNode(id=content_node.id, taxonomy_id=tax.id, code="SK_E2E", name="Skill E2E", node_type="skill")
        session.add(t_node)
        await session.flush()

        class_easy = QuestionClassification(question_version_id=v_easy.id, taxonomy_id=tax.id, competency_node_id=t_node.id, skill_node_id=t_node.id, is_primary=True, status="active", source="human")
        class_med = QuestionClassification(question_version_id=v_med.id, taxonomy_id=tax.id, competency_node_id=t_node.id, skill_node_id=t_node.id, is_primary=True, status="active", source="human")
        session.add_all([class_easy, class_med])

        # 5. User Bindings
        await admin_service.link_user_to_school(
            performed_by_external_id="admin:master",
            external_user_id="user:prof_mendes",
            role=AdminRole.TEACHER,
            scope_type=AdminScopeType.CLASSROOM,
            school_id=school.id,
            scope_external_id="TURMA_3A",
        )
        await admin_service.link_user_to_school(
            performed_by_external_id="admin:master",
            external_user_id="student:e2e_school",
            role=AdminRole.STUDENT,
            scope_type=AdminScopeType.CLASSROOM,
            school_id=school.id,
            scope_external_id="TURMA_3A",
        )

        await session.commit()
        return school.id, content_node.id, res_material.id, res_video.id, v_easy.id, v_med.id, opt_e1.id, opt_m1.id

    async def test_01_e2e_flow_school_student(self):
        """CENÁRIO A — ALUNO VINCULADO A ESCOLA
        Admin Master -> Escola -> Professor -> Aula -> Diagnóstico -> Domínio -> Trilha -> Prática -> Novo Domínio -> Nova Recomendação
        """
        async with self.session_factory() as session:
            school_id, content_id, res_mat_id, res_vid_id, v_easy_id, v_med_id, opt_e1_id, opt_m1_id = await self._seed_infrastructure(session)

            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_engine = RecommendationEngine(session, ks)
            diag_svc = InitialDiagnosticService(session, ks, stopping_policy=DiagnosticStoppingPolicy(min_questions=1, max_questions=1))
            practice_svc = PracticeSessionService()
            selection_svc = QuestionSelectionService()

            # 1. Teacher records a lesson for Diluição
            lesson = await t_svc.record_lesson(
                teacher_id="user:prof_mendes",
                school_id=school_id,
                classroom_id="TURMA_3A",
                content_node_id=content_id,
                title="Aula sobre Diluição de Soluções",
            )
            self.assertIsNotNone(lesson.id)

            # 2. Student starts Initial Diagnostic
            diag, q1 = await diag_svc.start_diagnostic(
                student_id="student:e2e_school",
                school_id=school_id,
                classroom_id="TURMA_3A",
            )
            self.assertEqual(diag.school_id, school_id)

            # Student answers question incorrectly -> Low initial estimated mastery
            diag, is_corr, is_comp, _ = await diag_svc.answer_question(
                diagnostic_id=diag.id,
                selection_id=q1.id,
                selected_option_id=uuid4(),  # wrong option
            )
            self.assertTrue(is_comp)

            # Check mastery map updated
            stmt_m = select(StudentContentMastery).where(StudentContentMastery.external_identity_id == "student:e2e_school")
            res_m = await session.execute(stmt_m)
            mastery_initial = res_m.scalar_one()
            self.assertEqual(mastery_initial.mastery_score, 0.0)

            # 3. Learning Path / Recommendation Engine generates recommendation aligned with TEACHER context
            recs = await rec_engine.generate_and_resolve_recommendations(
                student_id="student:e2e_school",
                institution_id=str(school_id),
                classroom_id="TURMA_3A",
            )
            self.assertGreater(len(recs), 0)
            rec = recs[0]
            self.assertEqual(rec["context_source"], "TEACHER")
            self.assertEqual(rec["recommendation_type"], "STUDY_MATERIAL")
            self.assertEqual(rec["primary_resource"]["resource_id"], str(res_mat_id))

            # 4. Student initiates Practice Session
            p_session = await practice_svc.create_session(
                session,
                external_identity_id="student:e2e_school",
                content_node_id=content_id,
                requested_question_count=1,
                recommended_difficulty="EASY",
            )
            await selection_svc.populate_session(session, p_session, "student:e2e_school", "EASY")
            await session.commit()

            # Student answers practice question correctly
            q_selections = p_session.question_selections
            self.assertGreater(len(q_selections), 0)
            sel = q_selections[0]
            sel.selected_option_id = opt_e1_id
            sel.is_correct = True
            sel.answered_at = datetime.now(timezone.utc)

            # Complete practice session and update mastery
            await practice_svc.complete_session(session, p_session, "student:e2e_school")

            # 5. Verify Mastery and New Recommendation updated
            res_m2 = await session.execute(stmt_m)
            mastery_updated = res_m2.scalar_one()
            self.assertGreater(mastery_updated.mastery_score, 0.0)

    async def test_02_e2e_flow_independent_student(self):
        """CENÁRIO B — ALUNO INDEPENDENTE/AUTÔNOMO (Sem Escola/Sem Professor)
        Cadastro -> Escolha de Disciplina -> Diagnóstico -> Mapa de Domínio -> Trilha Autônoma -> Prática -> Novo Domínio -> Nova Recomendação
        """
        async with self.session_factory() as session:
            _, content_id, res_mat_id, res_vid_id, v_easy_id, v_med_id, opt_e1_id, opt_m1_id = await self._seed_infrastructure(session)

            ks = KnowledgeService(session)
            rec_engine = RecommendationEngine(session, ks)
            diag_svc = InitialDiagnosticService(session, ks, stopping_policy=DiagnosticStoppingPolicy(min_questions=1, max_questions=1))
            practice_svc = PracticeSessionService()
            selection_svc = QuestionSelectionService()

            # 1. Independent Student starts Initial Diagnostic without school
            diag, q1 = await diag_svc.start_diagnostic(
                student_id="student:e2e_independent",
                school_id=None,  # Independent
                discipline="Química",
            )
            self.assertIsNone(diag.school_id)

            # Student answers question correctly
            diag, is_corr, is_comp, _ = await diag_svc.answer_question(
                diagnostic_id=diag.id,
                selection_id=q1.id,
                selected_option_id=opt_e1_id,
            )
            self.assertTrue(is_comp)

            # 2. Mastery Map estimated
            stmt_m = select(StudentContentMastery).where(StudentContentMastery.external_identity_id == "student:e2e_independent")
            res_m = await session.execute(stmt_m)
            mastery_initial = res_m.scalar_one()
            self.assertEqual(mastery_initial.mastery_score, 100.0)

            # 3. Autonomous Recommendation generated
            recs = await rec_engine.generate_and_resolve_recommendations(
                student_id="student:e2e_independent",
            )
            self.assertGreater(len(recs), 0)
            rec = recs[0]
            self.assertEqual(rec["context_source"], "AUTONOMOUS")


if __name__ == "__main__":
    unittest.main()
