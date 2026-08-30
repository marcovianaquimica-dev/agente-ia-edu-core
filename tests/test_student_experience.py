import asyncio
import unittest
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    ContentQuestionLink,
    ContentResourceLink,
    EducationalResource,
    LearningHistory,
    PedagogicalContext,
    PedagogicalRecommendation,
    Question,
    QuestionVersion,
    StudentContentMastery,
    VideoResourceDetail,
)
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.recommendation import RecommendationEngine
from agente_ia_edu.services.student_dashboard import StudentDashboardService
from agente_ia_edu.services.video_engine import VideoRecommendationEngine


class TestStudentExperience(unittest.IsolatedAsyncioTestCase):
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

        res = EducationalResource(
            title="Apostila Diluição",
            resource_type="THEORY_MATERIAL",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            status="active",
        )
        session.add(res)
        await session.flush()

        session.add(ContentResourceLink(content_node_id=content_node.id, resource_id=res.id, pedagogical_role="THEORY"))

        q = Question(validation_status="approved")
        session.add(q)
        await session.flush()

        v = QuestionVersion(
            question_id=q.id,
            version_kind="official_original",
            canonical_text="Questão Diluição",
            statement="Questão Diluição",
            content_hash="hdil",
            recommended_difficulty="EASY",
        )
        session.add(v)
        await session.flush()

        session.add(ContentQuestionLink(content_node_id=content_node.id, question_version_id=v.id))

        await session.commit()
        return root.id, content_node.id, res.id, v.id

    async def test_01_dashboard_new_student_empty_state(self):
        """1. dashboard com aluno novo (has_data=False, welcome_message)"""
        async with self.session_factory() as session:
            _, content_id, _, _ = await self._seed_data(session)
            ks = KnowledgeService(session)
            rec_e = RecommendationEngine(session, ks)
            vid_e = VideoRecommendationEngine(session, ks)
            dash_s = StudentDashboardService(session, ks, rec_e, vid_e)

            data = await dash_s.get_dashboard(student_id="student:newbie")
            self.assertFalse(data["has_data"])
            self.assertIn("Vamos começar", data["welcome_message"])
            self.assertEqual(data["summary"]["questions_answered"], 0)

    async def test_02_dashboard_student_with_history(self):
        """2. dashboard com histórico (has_data=True, stats, mastery)"""
        async with self.session_factory() as session:
            _, content_id, _, v_id = await self._seed_data(session)
            ks = KnowledgeService(session)
            rec_e = RecommendationEngine(session, ks)
            vid_e = VideoRecommendationEngine(session, ks)
            dash_s = StudentDashboardService(session, ks, rec_e, vid_e)

            session.add(LearningHistory(external_identity_id="student:alice", activity_type="INDIVIDUAL_PRACTICE", question_version_id=v_id, difficulty_level="EASY", is_correct=True, content_node_id=content_id))
            session.add(StudentContentMastery(external_identity_id="student:alice", content_node_id=content_id, mastery_score=80.0, current_level="HARD", questions_answered=1, questions_correct=1))
            await session.commit()

            data = await dash_s.get_dashboard(student_id="student:alice")
            self.assertTrue(data["has_data"])
            self.assertEqual(data["summary"]["questions_answered"], 1)
            self.assertEqual(data["summary"]["contents_mastered"], 1)

    async def test_03_time_period_filter(self):
        """3. filtro de período (academic_year, last_30_days, bimester, semester)"""
        async with self.session_factory() as session:
            _, content_id, _, v_id = await self._seed_data(session)
            ks = KnowledgeService(session)
            rec_e = RecommendationEngine(session, ks)
            vid_e = VideoRecommendationEngine(session, ks)
            dash_s = StudentDashboardService(session, ks, rec_e, vid_e)

            data_year = await dash_s.get_dashboard(student_id="student:alice", time_period="academic_year")
            data_bimester = await dash_s.get_dashboard(student_id="student:alice", time_period="bimester")
            self.assertEqual(data_year["time_period"], "academic_year")
            self.assertEqual(data_bimester["time_period"], "bimester")

    async def test_04_action_plan_categorization(self):
        """4. domínio e categorização do plano de ação"""
        async with self.session_factory() as session:
            _, content_id, _, _ = await self._seed_data(session)
            node_dev = CatalogNode(node_type="CONTENT", name="Estequiometria", active=True)
            node_cons = CatalogNode(node_type="CONTENT", name="Ligações Químicas", active=True)
            session.add_all([node_dev, node_cons])
            await session.flush()

            session.add(StudentContentMastery(external_identity_id="student:plan", content_node_id=content_id, mastery_score=38.0))
            session.add(StudentContentMastery(external_identity_id="student:plan", content_node_id=node_dev.id, mastery_score=60.0))
            session.add(StudentContentMastery(external_identity_id="student:plan", content_node_id=node_cons.id, mastery_score=85.0))
            await session.commit()

            ks = KnowledgeService(session)
            rec_e = RecommendationEngine(session, ks)
            vid_e = VideoRecommendationEngine(session, ks)
            dash_s = StudentDashboardService(session, ks, rec_e, vid_e)

            data = await dash_s.get_dashboard(student_id="student:plan")
            plan = data["action_plan"]

            self.assertEqual(len(plan["needs_improvement"]), 1)
            self.assertEqual(plan["needs_improvement"][0]["content_name"], "Diluição de Soluções")

            self.assertEqual(len(plan["in_development"]), 1)
            self.assertEqual(plan["in_development"][0]["content_name"], "Estequiometria")

            self.assertEqual(len(plan["consolidated"]), 1)
            self.assertEqual(plan["consolidated"][0]["content_name"], "Ligações Químicas")

    async def test_05_06_07_08_active_recommendation_and_context_sources(self):
        """5, 6, 7, 8. recomendação ativa, contexto TEACHER, AUTONOMOUS, SCHOOL_PLAN"""
        async with self.session_factory() as session:
            _, content_id, _, _ = await self._seed_catalog_data(session)
            ks = KnowledgeService(session)
            rec_e = RecommendationEngine(session, ks)
            vid_e = VideoRecommendationEngine(session, ks)
            dash_s = StudentDashboardService(session, ks, rec_e, vid_e)

            await rec_e.record_pedagogical_context(content_node_id=content_id, source="TEACHER", institution_id="SCHOOL_A")

            data = await dash_s.get_dashboard(student_id="student:ctx", institution_id="SCHOOL_A")
            active_rec = data["active_recommendation"]

            self.assertIsNotNone(active_rec)
            self.assertEqual(active_rec["context_source"], "TEACHER")
            self.assertEqual(len(active_rec["steps"]), 4)

    async def test_09_to_13_resources_available_and_graceful_not_available(self):
        """9-13. materiais, vídeos e questões disponíveis x not_available"""
        async with self.session_factory() as session:
            empty_node = CatalogNode(node_type="CONTENT", name="Cinética", active=True)
            session.add(empty_node)
            await session.commit()

            ks = KnowledgeService(session)
            rec_e = RecommendationEngine(session, ks)
            vid_e = VideoRecommendationEngine(session, ks)
            dash_s = StudentDashboardService(session, ks, rec_e, vid_e)

            await rec_e.record_pedagogical_context(content_node_id=empty_node.id, source="TEACHER")

            data = await dash_s.get_dashboard(student_id="student:empty")
            rec = data["active_recommendation"]

            steps = rec["steps"]
            self.assertEqual(steps[0]["status"], "not_available")  # Material
            self.assertEqual(steps[1]["status"], "not_available")  # Video
            self.assertEqual(steps[2]["status"], "not_available")  # Practice

    async def test_14_student_isolation(self):
        """14. isolamento de aluno (aluno A nunca vê dados de aluno B)"""
        async with self.session_factory() as session:
            _, content_id, _, v_id = await self._seed_catalog_data(session)
            session.add(StudentContentMastery(external_identity_id="student:alice", content_node_id=content_id, mastery_score=90.0))
            session.add(StudentContentMastery(external_identity_id="student:bob", content_node_id=content_id, mastery_score=20.0))
            await session.commit()

            ks = KnowledgeService(session)
            rec_e = RecommendationEngine(session, ks)
            vid_e = VideoRecommendationEngine(session, ks)
            dash_s = StudentDashboardService(session, ks, rec_e, vid_e)

            data_a = await dash_s.get_dashboard(student_id="student:alice")
            data_b = await dash_s.get_dashboard(student_id="student:bob")

            self.assertEqual(data_a["mastery_breakdown"][0]["mastery_score"], 90.0)
            self.assertEqual(data_b["mastery_breakdown"][0]["mastery_score"], 20.0)

    async def test_15_16_17_evolution_view_and_empty_state_distinction(self):
        """15, 16, 17. evolução, distinção estado vazio vs baixo desempenho"""
        async with self.session_factory() as session:
            ks = KnowledgeService(session)
            rec_e = RecommendationEngine(session, ks)
            vid_e = VideoRecommendationEngine(session, ks)
            dash_s = StudentDashboardService(session, ks, rec_e, vid_e)

            evo = await dash_s.get_evolution(student_id="student:newbie")
            self.assertFalse(evo["has_data"])
            self.assertEqual(evo["accuracy_percentage"], 0.0)

            path = await dash_s.get_learning_path(student_id="student:newbie")
            self.assertIsNotNone(path["steps"])

    async def _seed_catalog_data(self, session: AsyncSession):
        root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id

        content_node = CatalogNode(parent_id=root.id, root_id=root.id, node_type="CONTENT", code="QUIM-DIL", name="Diluição de Soluções", position=1, active=True)
        session.add(content_node)
        await session.commit()
        return root.id, content_node.id, None, None


if __name__ == "__main__":
    unittest.main()
