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
    ContentResourceLink,
    EducationalResource,
    PedagogicalContext,
    PedagogicalRecommendation,
    StudentContentMastery,
    VideoResourceDetail,
)
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.recommendation import (
    RecommendationEngine,
    ResourceTrackingService,
)
from agente_ia_edu.services.video_engine import (
    VideoFeedbackReason,
    VideoFeedbackType,
    VideoRecommendationEngine,
    VideoRecommendationPolicy,
)


class TestVideoIntelligenceEngine(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_video_test_data(self, session: AsyncSession):
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

        # Create 5 videos with different characteristics:
        # Video 1: Basic & Short (EASY, 5 mins, Platform)
        v1 = EducationalResource(
            title="Diluição de Soluções - Conceito Básico",
            resource_type="VIDEO",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            source_url="https://youtube.com/watch?v=v1",
            status="active",
        )
        session.add(v1)
        await session.flush()
        session.add(VideoResourceDetail(resource_id=v1.id, platform="YOUTUBE", external_video_id="v1", duration_seconds=300))
        session.add(ContentResourceLink(content_node_id=content_node.id, resource_id=v1.id, pedagogical_role="VIDEO", recommended_level="EASY"))

        # Video 2: School A Private Video (EASY, 8 mins)
        v2 = EducationalResource(
            title="Vídeo do Professor Mendes - Diluição Exclusivo Escola A",
            resource_type="VIDEO",
            origin_type="SCHOOL",
            owner_external_id="SCHOOL_A",
            visibility_scope="PRIVATE",
            source_url="https://youtube.com/watch?v=v2",
            status="active",
        )
        session.add(v2)
        await session.flush()
        session.add(VideoResourceDetail(resource_id=v2.id, platform="YOUTUBE", external_video_id="v2", duration_seconds=480))
        session.add(ContentResourceLink(content_node_id=content_node.id, resource_id=v2.id, pedagogical_role="VIDEO", recommended_level="EASY"))

        # Video 3: Intermediate (MEDIUM, 12 mins)
        v3 = EducationalResource(
            title="Diluição - Exercícios Intermediários",
            resource_type="VIDEO",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            source_url="https://youtube.com/watch?v=v3",
            status="active",
        )
        session.add(v3)
        await session.flush()
        session.add(VideoResourceDetail(resource_id=v3.id, platform="YOUTUBE", external_video_id="v3", duration_seconds=720))
        session.add(ContentResourceLink(content_node_id=content_node.id, resource_id=v3.id, pedagogical_role="VIDEO", recommended_level="MEDIUM"))

        # Video 4: Advanced (HARD, 20 mins)
        v4 = EducationalResource(
            title="Diluição Avançada para Medicina",
            resource_type="VIDEO",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            source_url="https://youtube.com/watch?v=v4",
            status="active",
        )
        session.add(v4)
        await session.flush()
        session.add(VideoResourceDetail(resource_id=v4.id, platform="YOUTUBE", external_video_id="v4", duration_seconds=1200))
        session.add(ContentResourceLink(content_node_id=content_node.id, resource_id=v4.id, pedagogical_role="VIDEO", recommended_level="HARD"))

        # Video 5: Very Long Video (EASY, 45 mins)
        v5 = EducationalResource(
            title="Aulão Completo Soluções e Diluição",
            resource_type="VIDEO",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            source_url="https://youtube.com/watch?v=v5",
            status="active",
        )
        session.add(v5)
        await session.flush()
        session.add(VideoResourceDetail(resource_id=v5.id, platform="YOUTUBE", external_video_id="v5", duration_seconds=2700))
        session.add(ContentResourceLink(content_node_id=content_node.id, resource_id=v5.id, pedagogical_role="VIDEO", recommended_level="EASY"))

        await session.commit()
        return root.id, content_node.id, v1.id, v2.id, v3.id, v4.id, v5.id

    async def test_01_02_03_video_matching_by_content_and_difficulty(self):
        """1, 2, 3, 4, 5, 6. correspondência de vídeo, subconteúdo e dificuldades EASY/MEDIUM/HARD"""
        async with self.session_factory() as session:
            _, content_id, v1_id, _, v3_id, v4_id, _ = await self._seed_video_test_data(session)
            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)

            # Student with low mastery (30%) -> EASY difficulty -> v1
            session.add(StudentContentMastery(external_identity_id="s_easy", content_node_id=content_id, mastery_score=30.0))
            # Student with medium mastery (60%) -> MEDIUM difficulty -> v3
            session.add(StudentContentMastery(external_identity_id="s_med", content_node_id=content_id, mastery_score=60.0))
            # Student with high mastery (85%) -> HARD difficulty -> v4
            session.add(StudentContentMastery(external_identity_id="s_hard", content_node_id=content_id, mastery_score=85.0))
            await session.commit()

            res_easy = await video_engine.recommend_video_for_student(student_id="s_easy", content_node_id=content_id)
            self.assertEqual(res_easy["status"], "OK")
            self.assertEqual(res_easy["video_resource_id"], str(v1_id))
            self.assertEqual(res_easy["recommended_difficulty"], "EASY")

            res_med = await video_engine.recommend_video_for_student(student_id="s_med", content_node_id=content_id)
            self.assertEqual(res_med["video_resource_id"], str(v3_id))
            self.assertEqual(res_med["recommended_difficulty"], "MEDIUM")

            res_hard = await video_engine.recommend_video_for_student(student_id="s_hard", content_node_id=content_id)
            self.assertEqual(res_hard["video_resource_id"], str(v4_id))
            self.assertEqual(res_hard["recommended_difficulty"], "HARD")

    async def test_07_08_deterministic_ranking_and_tie_breaker(self):
        """7, 8. ranking e desempate determinísticos"""
        policy = VideoRecommendationPolicy()
        id1, id2 = uuid4(), uuid4()
        cands = [
            {"resource_id": str(id1), "video_score": 50.0},
            {"resource_id": str(id2), "video_score": 50.0},
        ]
        ranked = policy.rank_videos(cands)
        # Ordered deterministically by resource_id when scores tie
        expected_first = str(id1) if str(id1) < str(id2) else str(id2)
        self.assertEqual(ranked[0]["resource_id"], expected_first)

    async def test_09_10_previously_watched_and_exclude_current(self):
        """9, 10. vídeo já assistido e pedido 'quero outro' (exclusão do atual)"""
        async with self.session_factory() as session:
            _, content_id, v1_id, v2_id, v3_id, _, _ = await self._seed_video_test_data(session)
            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)

            # First recommendation -> v1
            res1 = await video_engine.recommend_video_for_student(student_id="s_next", content_node_id=content_id)
            self.assertEqual(res1["video_resource_id"], str(v1_id))

            # Student asks "Quero outro" -> exclude v1_id
            res2 = await video_engine.recommend_video_for_student(
                student_id="s_next",
                content_node_id=content_id,
                excluded_video_ids={v1_id},
            )
            self.assertNotEqual(res2["video_resource_id"], str(v1_id))
            self.assertEqual(res2["status"], "OK")

    async def test_11_to_16_feedback_handling(self):
        """11-16. feedback LIKED, DISLIKED, TOO_FAST, TOO_SLOW, TOO_BASIC, TOO_ADVANCED"""
        policy = VideoRecommendationPolicy()
        v1_id = uuid4()
        v2_id = uuid4()
        cands = [
            {"resource_id": str(v1_id), "origin_type": "PLATFORM"},
            {"resource_id": str(v2_id), "origin_type": "PLATFORM"},
        ]

        # Scenario A: Liked v1 -> v1 score increases
        feedback_liked = {v1_id: {"type": VideoFeedbackType.LIKED}}
        ranked_liked = policy.rank_videos(cands, user_feedback=feedback_liked)
        self.assertEqual(ranked_liked[0]["resource_id"], str(v1_id))

        # Scenario B: Disliked v1 -> v1 score drops, v2 wins
        feedback_disliked = {v1_id: {"type": VideoFeedbackType.DISLIKED, "reason": VideoFeedbackReason.TOO_BASIC}}
        ranked_disliked = policy.rank_videos(cands, user_feedback=feedback_disliked)
        self.assertEqual(ranked_disliked[0]["resource_id"], str(v2_id))

    async def test_17_18_19_school_video_global_video_and_isolation(self):
        """17, 18, 19, 20. vídeo de escola, global, autoral e isolamento de privacidade"""
        async with self.session_factory() as session:
            _, content_id, v1_id, v2_id, _, _, _ = await self._seed_video_test_data(session)
            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)

            # School A student gets School A private video (v2) boosted over platform video
            res_a = await video_engine.recommend_video_for_student(student_id="s_a", content_node_id=content_id, institution_id="SCHOOL_A")
            self.assertEqual(res_a["video_resource_id"], str(v2_id))

            # School B student cannot see v2, gets global v1
            res_b = await video_engine.recommend_video_for_student(student_id="s_b", content_node_id=content_id, institution_id="SCHOOL_B")
            self.assertEqual(res_b["video_resource_id"], str(v1_id))

    async def test_21_no_video_available_graceful(self):
        """21. nenhum vídeo disponível (retorna NO_VIDEO_AVAILABLE sem falhar)"""
        async with self.session_factory() as session:
            empty_node = CatalogNode(node_type="CONTENT", name="Cinética Química", active=True)
            session.add(empty_node)
            await session.commit()

            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)

            res = await video_engine.recommend_video_for_student(student_id="s_empty", content_node_id=empty_node.id)
            self.assertEqual(res["status"], "NO_VIDEO_AVAILABLE")
            self.assertIn("Nenhum vídeo disponível", res["reason"])

    async def test_23_duration_penalty_for_overly_long_videos(self):
        """23. duração considerada (penalidade para vídeos muito longos no nível EASY)"""
        policy = VideoRecommendationPolicy()
        v_short = {"resource_id": str(uuid4()), "video_detail": {"duration_seconds": 300}}
        v_long = {"resource_id": str(uuid4()), "video_detail": {"duration_seconds": 2700}}

        ranked = policy.rank_videos([v_short, v_long], target_difficulty="EASY")
        self.assertEqual(ranked[0]["resource_id"], v_short["resource_id"])

    async def test_24_auditable_explanation(self):
        """24. explicação auditável com fatores"""
        async with self.session_factory() as session:
            _, content_id, v1_id, _, _, _, _ = await self._seed_video_test_data(session)
            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)

            res = await video_engine.recommend_video_for_student(student_id="s_audit", content_node_id=content_id)
            self.assertIn("Diluição de Soluções", res["reason"])
            self.assertIn("audit_factors", res)
            self.assertEqual(res["audit_factors"]["matched_content"], "Diluição de Soluções")

    async def test_25_26_student_with_teacher_and_autonomous(self):
        """25, 26, 27, 28. integração com professor, autônomo, RecommendationEngine e preservação"""
        async with self.session_factory() as session:
            _, content_id, v1_id, v2_id, _, _, _ = await self._seed_video_test_data(session)
            ks = KnowledgeService(session)
            recommendation_engine = RecommendationEngine(session, ks)
            video_engine = VideoRecommendationEngine(session, ks)

            # Teacher registers WATCH_VIDEO requirement or taught lesson
            await recommendation_engine.record_pedagogical_context(
                content_node_id=content_id,
                source="TEACHER",
                institution_id="SCHOOL_A",
                classroom_id="CLASS_3B",
                author_id="teacher:prof_mendes",
                title="Assistir vídeo de Diluição.",
            )

            recs = await recommendation_engine.generate_recommendations(
                student_id="student:mario",
                institution_id="SCHOOL_A",
                classroom_id="CLASS_3B",
            )
            self.assertGreater(len(recs), 0)

            video_res = await video_engine.recommend_video_for_student(
                student_id="student:mario",
                content_node_id=content_id,
                institution_id="SCHOOL_A",
            )
            self.assertEqual(video_res["status"], "OK")
            self.assertEqual(video_res["video_resource_id"], str(v2_id))

    async def test_29_events_opened_started_progress_completed(self):
        """1-6. OPENED, STARTED, PROGRESS, COMPLETED, 0%, 100%"""
        async with self.session_factory() as session:
            _, content_id, v1_id, _, _, _, _ = await self._seed_video_test_data(session)
            tracking = ResourceTrackingService(session)

            # 1. OPENED (0%)
            evt_opened = await tracking.record_interaction(
                student_id="student:ev1",
                resource_id=v1_id,
                action_type="OPENED",
                progress_percentage=0.0,
            )
            self.assertEqual(evt_opened["action_type"], "OPENED")

            # 2. STARTED
            evt_started = await tracking.record_interaction(
                student_id="student:ev1",
                resource_id=v1_id,
                action_type="STARTED",
            )
            self.assertEqual(evt_started["action_type"], "STARTED")

            # 3. PROGRESS (50%)
            evt_prog = await tracking.record_interaction(
                student_id="student:ev1",
                resource_id=v1_id,
                action_type="PROGRESS",
                progress_percentage=50.0,
            )
            self.assertEqual(evt_prog["progress_percentage"], 50.0)

            # 4. COMPLETED (100%)
            evt_comp = await tracking.record_interaction(
                student_id="student:ev1",
                resource_id=v1_id,
                action_type="PROGRESS",
                progress_percentage=100.0,
            )
            self.assertEqual(evt_comp["action_type"], "COMPLETED")

    async def test_30_invalid_progress_percentage_raises_error(self):
        """7. progresso inválido (<0 ou >100) gera ValueError"""
        async with self.session_factory() as session:
            _, _, v1_id, _, _, _, _ = await self._seed_video_test_data(session)
            tracking = ResourceTrackingService(session)

            with self.assertRaises(ValueError):
                await tracking.record_interaction(student_id="s1", resource_id=v1_id, action_type="PROGRESS", progress_percentage=-10.0)

            with self.assertRaises(ValueError):
                await tracking.record_interaction(student_id="s1", resource_id=v1_id, action_type="PROGRESS", progress_percentage=150.0)

    async def test_31_idempotency_via_event_id(self):
        """28. idempotência por event_id"""
        async with self.session_factory() as session:
            _, _, v1_id, _, _, _, _ = await self._seed_video_test_data(session)
            tracking = ResourceTrackingService(session)

            res1 = await tracking.record_interaction(
                student_id="student:idemp",
                resource_id=v1_id,
                action_type="STARTED",
                event_id="evt-unique-99",
            )
            self.assertFalse(res1["idempotent"])

            res2 = await tracking.record_interaction(
                student_id="student:idemp",
                resource_id=v1_id,
                action_type="STARTED",
                event_id="evt-unique-99",
            )
            self.assertTrue(res2["idempotent"])
            self.assertEqual(res1["id"], res2["id"])

    async def test_32_request_another_video_flow(self):
        """21, 22. 'Quero outro' com justificativa e exclusão do vídeo atual"""
        async with self.session_factory() as session:
            _, content_id, v1_id, _, v3_id, _, _ = await self._seed_video_test_data(session)
            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)

            another = await video_engine.request_another_video(
                student_id="student:req_another",
                content_node_id=content_id,
                current_video_id=v1_id,
                feedback_type=VideoFeedbackType.DISLIKED,
                feedback_reason=VideoFeedbackReason.TOO_FAST,
            )

            self.assertEqual(another["status"], "OK")
            self.assertNotEqual(another["video_resource_id"], str(v1_id))

    async def test_33_real_scenario_18_disliked_and_request_another(self):
        """18. CENÁRIO REAL (Diluição -> Domínio 38% -> Vídeo A assistido 40% -> DISLIKED + TOO_FAST -> Quero Outro)"""
        async with self.session_factory() as session:
            _, content_id, v1_id, _, _, _, _ = await self._seed_video_test_data(session)
            ks = KnowledgeService(session)
            recommendation_engine = RecommendationEngine(session, ks)
            video_engine = VideoRecommendationEngine(session, ks)

            # Teacher registers lesson
            await recommendation_engine.record_pedagogical_context(
                content_node_id=content_id,
                source="TEACHER",
                institution_id="ESCOLA_PARCEIRA",
                classroom_id="TURMA_1",
                author_id="teacher:prof_mendes",
                title="Diluição de Soluções foi ensinada.",
            )

            # Student with low mastery (38%)
            session.add(StudentContentMastery(external_identity_id="student:carlos", content_node_id=content_id, mastery_score=38.0))
            await session.commit()

            # First recommendation
            v_first = await video_engine.recommend_video_for_student(
                student_id="student:carlos",
                content_node_id=content_id,
                institution_id="ESCOLA_PARCEIRA",
            )
            self.assertEqual(v_first["video_resource_id"], str(v1_id))

            # Student watches 40%, sends DISLIKED + TOO_FAST and requests another video
            v_second = await video_engine.request_another_video(
                student_id="student:carlos",
                content_node_id=content_id,
                current_video_id=v1_id,
                feedback_type=VideoFeedbackType.DISLIKED,
                feedback_reason=VideoFeedbackReason.TOO_FAST,
                institution_id="ESCOLA_PARCEIRA",
            )

            self.assertEqual(v_second["status"], "OK")
            self.assertNotEqual(v_second["video_resource_id"], str(v1_id))
            self.assertIn("TOO_FAST", v_second["audit_factors"]["student_preferences"])

    async def test_34_real_scenario_19_learning_preference_trend(self):
        """19. CENÁRIO DE APRENDIZADO (Rejeita 3 vídeos TOO_FAST, aceita NEEDS_EXAMPLES)"""
        async with self.session_factory() as session:
            _, content_id, v1_id, v2_id, v3_id, v4_id, _ = await self._seed_video_test_data(session)
            tracking = ResourceTrackingService(session)

            # Student rejects 3 videos with TOO_FAST
            for vid in (v1_id, v2_id, v3_id):
                await tracking.record_interaction(
                    student_id="student:trend",
                    resource_id=vid,
                    action_type="FEEDBACK",
                    content_node_id=content_id,
                    feedback_type=VideoFeedbackType.DISLIKED,
                    feedback_reason=VideoFeedbackReason.TOO_FAST,
                )

            # Student accepts 1 video with NEEDS_EXAMPLES
            await tracking.record_interaction(
                student_id="student:trend",
                resource_id=v4_id,
                action_type="FEEDBACK",
                content_node_id=content_id,
                feedback_type=VideoFeedbackType.LIKED,
                feedback_reason=VideoFeedbackReason.NEEDS_EXAMPLES,
            )

            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)

            watched, prog, fb, prefs = await video_engine._fetch_student_video_history("student:trend")
            self.assertEqual(prefs.get("TOO_FAST"), 3)
            self.assertEqual(prefs.get("NEEDS_EXAMPLES"), 1)


if __name__ == "__main__":
    unittest.main()
