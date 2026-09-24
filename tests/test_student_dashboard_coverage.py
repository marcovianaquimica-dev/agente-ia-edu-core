"""Coverage-focused tests for StudentDashboardService.

New file (does not touch tests/test_student_experience.py, which other
concurrent sessions may be editing). Targets the three branches left
uncovered per the 86% baseline:

1. get_evolution(): the daily-grouping loop + per-day aggregation
   (only runs when `entries` is non-empty).
2. get_learning_path(): the "recs non-empty" branch (4-step sequence +
   final return dict), including both "video available" and
   "video not_available" sub-cases.
3. _get_period_start_date(): the "semester"/"semestre" branch.
"""

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    ContentQuestionLink,
    ContentResourceLink,
    EducationalResource,
    LearningHistory,
    Question,
    QuestionVersion,
    VideoResourceDetail,
)
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.recommendation import RecommendationEngine
from agente_ia_edu.services.student_dashboard import StudentDashboardService
from agente_ia_edu.services.video_engine import VideoRecommendationEngine


class TestStudentDashboardCoverage(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_catalog_data(self, session: AsyncSession):
        """Content node + a QuestionVersion (LearningHistory.question_version_id is NOT NULL)."""
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

        q = Question(validation_status="approved")
        session.add(q)
        await session.flush()

        v = QuestionVersion(
            question_id=q.id,
            version_kind="official_original",
            canonical_text="Questão Diluição",
            statement="Questão Diluição",
            content_hash="hdil-evo",
            recommended_difficulty="EASY",
        )
        session.add(v)
        await session.flush()
        session.add(ContentQuestionLink(content_node_id=content_node.id, question_version_id=v.id))

        await session.commit()
        return root.id, content_node.id, v.id

    async def _seed_full_content(self, session: AsyncSession, *, with_video: bool):
        """Content node with theory material + practice question, optionally + video."""
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

        material = EducationalResource(
            title="Apostila Diluição",
            resource_type="THEORY_MATERIAL",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            status="active",
        )
        session.add(material)
        await session.flush()
        session.add(ContentResourceLink(content_node_id=content_node.id, resource_id=material.id, pedagogical_role="THEORY"))

        q = Question(validation_status="approved")
        session.add(q)
        await session.flush()

        v = QuestionVersion(
            question_id=q.id,
            version_kind="official_original",
            canonical_text="Questão Diluição",
            statement="Questão Diluição",
            content_hash="hdil-cov",
            recommended_difficulty="EASY",
        )
        session.add(v)
        await session.flush()
        session.add(ContentQuestionLink(content_node_id=content_node.id, question_version_id=v.id))

        if with_video:
            video_res = EducationalResource(
                title="Vídeo Diluição",
                resource_type="VIDEO",
                origin_type="PLATFORM",
                visibility_scope="PUBLIC",
                status="active",
            )
            session.add(video_res)
            await session.flush()
            session.add(VideoResourceDetail(resource_id=video_res.id, platform="YOUTUBE", external_video_id="abc123", duration_seconds=400))
            session.add(ContentResourceLink(content_node_id=content_node.id, resource_id=video_res.id, pedagogical_role="VIDEO"))

        await session.commit()
        return content_node.id, v.id

    def _make_services(self, session: AsyncSession):
        ks = KnowledgeService(session)
        rec_e = RecommendationEngine(session, ks)
        vid_e = VideoRecommendationEngine(session, ks)
        dash_s = StudentDashboardService(session, ks, rec_e, vid_e)
        return ks, rec_e, vid_e, dash_s

    # ------------------------------------------------------------------
    # 1. get_evolution() daily grouping + per-day aggregation
    # ------------------------------------------------------------------

    async def test_evolution_same_day_entries_group_into_one_bucket(self):
        async with self.session_factory() as session:
            _, content_id, v_id = await self._seed_catalog_data(session)
            _, _, _, dash_s = self._make_services(session)

            base = datetime(2026, 9, 10, 8, 0, 0, tzinfo=timezone.utc)
            session.add_all([
                LearningHistory(
                    external_identity_id="student:same_day",
                    activity_type="INDIVIDUAL_PRACTICE",
                    question_version_id=v_id,
                    difficulty_level="EASY",
                    is_correct=True,
                    content_node_id=content_id,
                    created_at=base,
                ),
                LearningHistory(
                    external_identity_id="student:same_day",
                    activity_type="INDIVIDUAL_PRACTICE",
                    question_version_id=v_id,
                    difficulty_level="EASY",
                    is_correct=False,
                    content_node_id=content_id,
                    created_at=base + timedelta(hours=2),
                ),
                LearningHistory(
                    external_identity_id="student:same_day",
                    activity_type="INDIVIDUAL_PRACTICE",
                    question_version_id=v_id,
                    difficulty_level="EASY",
                    is_correct=True,
                    content_node_id=content_id,
                    created_at=base + timedelta(hours=4),
                ),
            ])
            await session.commit()

            evo = await dash_s.get_evolution(student_id="student:same_day", time_period="academic_year")

            self.assertTrue(evo["has_data"])
            self.assertEqual(len(evo["overall_evolution"]), 1)
            bucket = evo["overall_evolution"][0]
            self.assertEqual(bucket["date_label"], "10/09")
            self.assertEqual(bucket["questions_answered"], 3)
            self.assertEqual(bucket["questions_correct"], 2)
            self.assertAlmostEqual(bucket["average_score"], round(2 / 3 * 100.0, 1))

    async def test_evolution_multi_day_entries_produce_multiple_buckets(self):
        async with self.session_factory() as session:
            _, content_id, v_id = await self._seed_catalog_data(session)
            _, _, _, dash_s = self._make_services(session)

            day1 = datetime(2026, 9, 10, 9, 0, 0, tzinfo=timezone.utc)
            day2 = datetime(2026, 9, 12, 9, 0, 0, tzinfo=timezone.utc)
            session.add_all([
                LearningHistory(
                    external_identity_id="student:multi_day",
                    activity_type="INDIVIDUAL_PRACTICE",
                    question_version_id=v_id,
                    difficulty_level="EASY",
                    is_correct=True,
                    content_node_id=content_id,
                    created_at=day1,
                ),
                LearningHistory(
                    external_identity_id="student:multi_day",
                    activity_type="INDIVIDUAL_PRACTICE",
                    question_version_id=v_id,
                    difficulty_level="EASY",
                    is_correct=True,
                    content_node_id=content_id,
                    created_at=day2,
                ),
                LearningHistory(
                    external_identity_id="student:multi_day",
                    activity_type="INDIVIDUAL_PRACTICE",
                    question_version_id=v_id,
                    difficulty_level="EASY",
                    is_correct=False,
                    content_node_id=content_id,
                    created_at=day2 + timedelta(hours=1),
                ),
            ])
            await session.commit()

            evo = await dash_s.get_evolution(student_id="student:multi_day", time_period="academic_year")

            self.assertTrue(evo["has_data"])
            self.assertEqual(len(evo["overall_evolution"]), 2)
            by_label = {b["date_label"]: b for b in evo["overall_evolution"]}
            self.assertIn("10/09", by_label)
            self.assertIn("12/09", by_label)

            b1 = by_label["10/09"]
            self.assertEqual(b1["questions_answered"], 1)
            self.assertEqual(b1["questions_correct"], 1)
            self.assertEqual(b1["average_score"], 100.0)

            b2 = by_label["12/09"]
            self.assertEqual(b2["questions_answered"], 2)
            self.assertEqual(b2["questions_correct"], 1)
            self.assertEqual(b2["average_score"], 50.0)

    # ------------------------------------------------------------------
    # 2. get_learning_path() "recs non-empty" branch
    # ------------------------------------------------------------------

    async def test_learning_path_active_recommendation_video_available(self):
        async with self.session_factory() as session:
            content_id, _ = await self._seed_full_content(session, with_video=True)
            _, rec_e, _, dash_s = self._make_services(session)

            await rec_e.record_pedagogical_context(content_node_id=content_id, source="TEACHER", institution_id="SCHOOL_A")

            path = await dash_s.get_learning_path(student_id="student:lp_video", institution_id="SCHOOL_A")

            self.assertEqual(path["content_node_id"], str(content_id))
            self.assertEqual(path["content_name"], "Diluição de Soluções")
            self.assertEqual(path["active_step_index"], 0)

            steps = path["steps"]
            self.assertEqual(len(steps), 4)

            self.assertEqual(steps[0]["step_type"], "MATERIAL")
            self.assertEqual(steps[0]["status"], "in_progress")
            self.assertIn("Diluição de Soluções", steps[0]["description"])

            self.assertEqual(steps[1]["step_type"], "VIDEO")
            self.assertEqual(steps[1]["status"], "pending")
            self.assertEqual(steps[1]["description"], "Vídeo Diluição")
            self.assertIsNotNone(steps[1]["resource_id"])

            self.assertEqual(steps[2]["step_type"], "PRACTICE")
            self.assertEqual(steps[2]["status"], "pending")
            self.assertIsNotNone(steps[2]["question_version_id"])

            self.assertEqual(steps[3]["step_type"], "REEVALUATE")
            self.assertEqual(steps[3]["status"], "pending")
            self.assertEqual(steps[3]["description"], "Medição de maestria atualizada pós-treino")

    async def test_learning_path_active_recommendation_video_not_available(self):
        async with self.session_factory() as session:
            content_id, _ = await self._seed_full_content(session, with_video=False)
            _, rec_e, _, dash_s = self._make_services(session)

            await rec_e.record_pedagogical_context(content_node_id=content_id, source="TEACHER", institution_id="SCHOOL_B")

            path = await dash_s.get_learning_path(student_id="student:lp_novideo", institution_id="SCHOOL_B")

            steps = path["steps"]
            self.assertEqual(len(steps), 4)
            self.assertEqual(steps[0]["status"], "in_progress")  # material present

            self.assertEqual(steps[1]["step_type"], "VIDEO")
            self.assertEqual(steps[1]["status"], "not_available")
            self.assertEqual(steps[1]["description"], "Nenhum vídeo disponível")
            self.assertIsNone(steps[1]["resource_id"])

            self.assertEqual(steps[2]["status"], "pending")  # question present

    # ------------------------------------------------------------------
    # 3. _get_period_start_date() semester branch
    # ------------------------------------------------------------------

    def test_period_start_date_last_30_days(self):
        now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
        result = StudentDashboardService._get_period_start_date("last_30_days", now)
        self.assertEqual(result, now - timedelta(days=30))

    def test_period_start_date_semester(self):
        now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
        result = StudentDashboardService._get_period_start_date("semester", now)
        self.assertEqual(result, now - timedelta(days=180))

    def test_period_start_date_semestre_pt_alias(self):
        now = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
        result = StudentDashboardService._get_period_start_date("semestre", now)
        self.assertEqual(result, now - timedelta(days=180))


if __name__ == "__main__":
    unittest.main()
