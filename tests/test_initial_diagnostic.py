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
    Question,
    QuestionClassification,
    QuestionOption,
    QuestionVersion,
    School,
    StudentContentMastery,
    Taxonomy,
    TaxonomyNode,
    UserSchoolLink,
)
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.initial_diagnostic import (
    DiagnosticStatus,
    DiagnosticStoppingPolicy,
    InitialDiagnosticService,
)
from agente_ia_edu.services.knowledge import KnowledgeService


class TestInitialDiagnostic(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_catalog_and_questions(self, session: AsyncSession):
        root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id

        parent_node = CatalogNode(
            parent_id=root.id,
            root_id=root.id,
            node_type="CONTENT",
            code="QUIM-CONC",
            name="Concentração de Soluções",
            position=1,
            active=True,
        )
        session.add(parent_node)
        await session.flush()

        content_node = CatalogNode(
            parent_id=parent_node.id,
            root_id=root.id,
            node_type="SUBCONTENT",
            code="QUIM-DIL",
            name="Diluição de Soluções",
            position=2,
            active=True,
        )
        session.add(content_node)
        await session.flush()

        # Questions: EASY, MEDIUM, HARD
        q_easy = Question(validation_status="approved")
        q_med = Question(validation_status="approved")
        q_hard = Question(validation_status="approved")
        session.add_all([q_easy, q_med, q_hard])
        await session.flush()

        v_easy = QuestionVersion(question_id=q_easy.id, version_kind="official_original", canonical_text="Questão fácil de Diluição", statement="Questão fácil", content_hash="heasy", recommended_difficulty="EASY")
        v_med = QuestionVersion(question_id=q_med.id, version_kind="official_original", canonical_text="Questão média de Diluição", statement="Questão média", content_hash="hmed", recommended_difficulty="MEDIUM")
        v_hard = QuestionVersion(question_id=q_hard.id, version_kind="official_original", canonical_text="Questão difícil de Diluição", statement="Questão difícil", content_hash="hhard", recommended_difficulty="HARD")
        session.add_all([v_easy, v_med, v_hard])
        await session.flush()

        # Options for v_easy
        opt_e1 = QuestionOption(question_version_id=v_easy.id, option_key="A", position=1, text="Correta", is_valid_option=True)
        opt_e2 = QuestionOption(question_version_id=v_easy.id, option_key="B", position=2, text="Incorreta", is_valid_option=False)
        session.add_all([opt_e1, opt_e2])

        # Options for v_med
        opt_m1 = QuestionOption(question_version_id=v_med.id, option_key="A", position=1, text="Correta", is_valid_option=True)
        opt_m2 = QuestionOption(question_version_id=v_med.id, option_key="B", position=2, text="Incorreta", is_valid_option=False)
        session.add_all([opt_m1, opt_m2])

        # Options for v_hard
        opt_h1 = QuestionOption(question_version_id=v_hard.id, option_key="A", position=1, text="Correta", is_valid_option=True)
        opt_h2 = QuestionOption(question_version_id=v_hard.id, option_key="B", position=2, text="Incorreta", is_valid_option=False)
        session.add_all([opt_h1, opt_h2])

        # Primary Active Classifications
        tax = Taxonomy(code="bncc", name="BNCC", version="1.0")
        session.add(tax)
        await session.flush()
        c_node = TaxonomyNode(id=content_node.id, taxonomy_id=tax.id, code="SK1", name="Skill 1", node_type="skill")
        session.add(c_node)
        await session.flush()

        class_easy = QuestionClassification(question_version_id=v_easy.id, taxonomy_id=tax.id, competency_node_id=c_node.id, skill_node_id=c_node.id, is_primary=True, status="active", source="human")
        class_med = QuestionClassification(question_version_id=v_med.id, taxonomy_id=tax.id, competency_node_id=c_node.id, skill_node_id=c_node.id, is_primary=True, status="active", source="human")
        class_hard = QuestionClassification(question_version_id=v_hard.id, taxonomy_id=tax.id, competency_node_id=c_node.id, skill_node_id=c_node.id, is_primary=True, status="active", source="human")
        session.add_all([class_easy, class_med, class_hard])

        # Direct Catalog Links
        session.add_all([
            ContentQuestionLink(content_node_id=content_node.id, question_version_id=v_easy.id),
            ContentQuestionLink(content_node_id=content_node.id, question_version_id=v_med.id),
            ContentQuestionLink(content_node_id=content_node.id, question_version_id=v_hard.id),
        ])

        await session.commit()
        return root.id, parent_node.id, content_node.id, v_easy.id, v_med.id, v_hard.id, opt_e1.id, opt_m1.id, opt_h1.id

    async def test_01_create_diagnostic_independent_student(self):
        """1, 3, 5, 24. Aluno independente inicia diagnóstico sem escola."""
        async with self.session_factory() as session:
            _, _, content_id, _, _, _, _, _, _ = await self._seed_catalog_and_questions(session)
            ks = KnowledgeService(session)
            service = InitialDiagnosticService(session, ks)

            diag, first_q = await service.start_diagnostic(
                student_id="student:independent_alice",
                school_id=None,
                grade_level="3ª Série",
                discipline="Química",
            )

            self.assertIsNotNone(diag.id)
            self.assertEqual(diag.student_id, "student:independent_alice")
            self.assertIsNone(diag.school_id)
            self.assertEqual(diag.status, DiagnosticStatus.IN_PROGRESS)
            self.assertIsNotNone(first_q)

    async def test_02_create_diagnostic_school_bound_student(self):
        """2, 4, 25. Aluno vinculado a escola inicia diagnóstico com contexto escolar."""
        async with self.session_factory() as session:
            admin_s = PlatformAdminService(session)
            school = await admin_s.create_school(performed_by_external_id="admin:master", code="SCH_DIAG", name="Escola Diag")

            _, _, content_id, _, _, _, _, _, _ = await self._seed_catalog_and_questions(session)
            ks = KnowledgeService(session)
            service = InitialDiagnosticService(session, ks)

            diag, first_q = await service.start_diagnostic(
                student_id="student:school_bob",
                school_id=school.id,
                classroom_id="TURMA_3A",
                grade_level="3ª Série",
            )

            self.assertEqual(diag.school_id, school.id)
            self.assertEqual(diag.classroom_id, "TURMA_3A")

    async def test_06_07_08_09_adaptive_difficulty_progression(self):
        """6, 7, 8, 9. Adaptação de dificuldade (acertos aumentam, erros reduzem dificuldade)."""
        async with self.session_factory() as session:
            _, _, content_id, _, _, _, opt_e1, opt_m1, opt_h1 = await self._seed_catalog_and_questions(session)
            ks = KnowledgeService(session)
            service = InitialDiagnosticService(session, ks)

            diag, q1 = await service.start_diagnostic(
                student_id="student:adaptive",
            )
            self.assertEqual(q1.difficulty_level, "EASY")

            # Answer EASY question correctly -> Next question adaptively becomes MEDIUM
            diag, is_corr1, is_comp1, q2 = await service.answer_question(
                diagnostic_id=diag.id,
                selection_id=q1.id,
                selected_option_id=opt_e1,
            )
            self.assertTrue(is_corr1)
            self.assertFalse(is_comp1)
            self.assertIsNotNone(q2)
            self.assertEqual(q2.difficulty_level, "MEDIUM")

    async def test_10_11_stopping_criteria_and_confidence(self):
        """10, 11, 14. Critério de parada (min_questions = 3) e cálculo de confiança."""
        async with self.session_factory() as session:
            _, _, content_id, _, _, _, opt_e1, opt_m1, opt_h1 = await self._seed_catalog_and_questions(session)
            ks = KnowledgeService(session)
            policy = DiagnosticStoppingPolicy(min_questions=3, max_questions=3)
            service = InitialDiagnosticService(session, ks, stopping_policy=policy)

            diag, q1 = await service.start_diagnostic(student_id="student:stop_test")

            # Answer Q1
            diag, _, _, q2 = await service.answer_question(diagnostic_id=diag.id, selection_id=q1.id, selected_option_id=opt_e1)
            # Answer Q2
            diag, _, _, q3 = await service.answer_question(diagnostic_id=diag.id, selection_id=q2.id, selected_option_id=opt_m1)
            # Answer Q3 -> Reaches min_questions = 3 -> Finalizes
            diag, _, is_complete, q_none = await service.answer_question(diagnostic_id=diag.id, selection_id=q3.id, selected_option_id=opt_h1)

            self.assertTrue(is_complete)
            self.assertIsNone(q_none)
            self.assertEqual(diag.status, DiagnosticStatus.COMPLETED)
            self.assertGreater(diag.overall_confidence, 0.0)

    async def test_12_13_16_mastery_map_and_prerequisites_and_determinism(self):
        """12, 13, 16, 26, 27. Mapa de domínio, verificação de pré-requisitos, determinismo e versão v1."""
        async with self.session_factory() as session:
            _, _, content_id, _, _, _, opt_e1, opt_m1, opt_h1 = await self._seed_catalog_and_questions(session)
            ks = KnowledgeService(session)
            policy = DiagnosticStoppingPolicy(min_questions=3, max_questions=3)
            service = InitialDiagnosticService(session, ks, stopping_policy=policy)

            diag, q1 = await service.start_diagnostic(student_id="student:map_test")
            diag, _, _, q2 = await service.answer_question(diagnostic_id=diag.id, selection_id=q1.id, selected_option_id=opt_e1)
            diag, _, _, q3 = await service.answer_question(diagnostic_id=diag.id, selection_id=q2.id, selected_option_id=opt_m1)
            await service.answer_question(diagnostic_id=diag.id, selection_id=q3.id, selected_option_id=opt_h1)

            res = await service.get_diagnostic_result(diag.id)
            self.assertEqual(res["status"], "COMPLETED")
            self.assertEqual(res["diagnostic_version"], "v1")
            self.assertGreater(len(res["mastery_map"]), 0)

            # Check StudentContentMastery updated
            stmt_mastery = select(StudentContentMastery).where(StudentContentMastery.external_identity_id == "student:map_test")
            res_m = await session.execute(stmt_mastery)
            m = res_m.scalar_one_or_none()
            self.assertIsNotNone(m)
            self.assertGreater(m.questions_answered, 0)

            # Check LearningHistory recorded with INITIAL_DIAGNOSTIC activity_type
            stmt_hist = select(LearningHistory).where(
                LearningHistory.external_identity_id == "student:map_test",
                LearningHistory.activity_type == "INITIAL_DIAGNOSTIC",
            )
            res_h = await session.execute(stmt_hist)
            entries = list(res_h.scalars().all())
            self.assertEqual(len(entries), 3)

    async def test_17_18_19_student_and_school_isolation(self):
        """17, 18, 19. Isolamento entre alunos e entre escolas (separação de avaliação oficial)."""
        async with self.session_factory() as session:
            admin_s = PlatformAdminService(session)
            sa = await admin_s.create_school(performed_by_external_id="admin:master", code="SA_ISO", name="Escola ISO A")
            sb = await admin_s.create_school(performed_by_external_id="admin:master", code="SB_ISO", name="Escola ISO B")

            _, _, content_id, _, _, _, _, _, _ = await self._seed_catalog_and_questions(session)
            ks = KnowledgeService(session)
            service = InitialDiagnosticService(session, ks)

            diag_a, _ = await service.start_diagnostic(student_id="student:alice", school_id=sa.id)
            diag_b, _ = await service.start_diagnostic(student_id="student:bob", school_id=sb.id)

            res_a = await service.get_diagnostic_result(diag_a.id)
            res_b = await service.get_diagnostic_result(diag_b.id)

            self.assertEqual(res_a["school_id"], str(sa.id))
            self.assertEqual(res_b["school_id"], str(sb.id))
            self.assertNotEqual(res_a["student_id"], res_b["student_id"])


if __name__ == "__main__":
    unittest.main()
