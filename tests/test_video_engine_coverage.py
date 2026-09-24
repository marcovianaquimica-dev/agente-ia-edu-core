"""Coverage-focused tests for src/agente_ia_edu/services/video_engine.py.

Split out into its own file (not touching tests/test_video_engine.py, which
other concurrent workers may be editing) to close the gaps left in the
86%-covered baseline: several `VideoRecommendationPolicy.rank_videos`
scoring branches, and several DB-backed branches of
`VideoRecommendationEngine`.
"""

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    ContentResourceLink,
    EducationalResource,
    PedagogicalRecommendation,
    VideoInteractionEvent,
    VideoResourceDetail,
)
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.video_engine import (
    VideoRecommendationEngine,
    VideoRecommendationPolicy,
)


class TestVideoRecommendationPolicyScoringBranches(unittest.TestCase):
    """Pure, no-DB tests: prove the exact numeric contribution of each
    scoring branch in VideoRecommendationPolicy.rank_videos()."""

    def setUp(self):
        self.policy = VideoRecommendationPolicy()

    def test_direct_node_boost_when_content_node_id_matches_target(self):
        target_node_id = uuid4()
        rid = uuid4()
        video = {"resource_id": str(rid), "content_node_id": str(target_node_id)}

        ranked = self.policy.rank_videos([video], target_node_id=target_node_id)

        expected = self.policy.direct_node_boost + self.policy.unwatched_boost
        self.assertEqual(ranked[0]["video_score"], expected)

    def test_subcontent_boost_when_not_direct_match_but_flagged_subcontent(self):
        target_node_id = uuid4()
        rid = uuid4()
        # content_node_id deliberately does NOT match target_node_id, so the
        # `if` branch is skipped and the `elif is_subcontent_match` fires.
        video = {
            "resource_id": str(rid),
            "content_node_id": str(uuid4()),
            "is_subcontent_match": True,
        }

        ranked = self.policy.rank_videos([video], target_node_id=target_node_id)

        expected = self.policy.subcontent_boost + self.policy.unwatched_boost
        self.assertEqual(ranked[0]["video_score"], expected)

    def test_completed_penalty_when_progress_is_100(self):
        rid = uuid4()
        video = {"resource_id": str(rid)}

        ranked = self.policy.rank_videos([video], video_progress={rid: 100.0})

        self.assertEqual(ranked[0]["video_score"], self.policy.completed_penalty)

    def test_in_progress_boost_when_progress_between_0_and_100(self):
        rid = uuid4()
        video = {"resource_id": str(rid)}

        ranked = self.policy.rank_videos([video], video_progress={rid: 45.0})

        self.assertEqual(ranked[0]["video_score"], self.policy.in_progress_boost)

    def test_too_fast_penalty_from_direct_video_feedback_reason(self):
        rid = uuid4()
        video = {"resource_id": str(rid)}
        feedback = {rid: {"type": "", "reason": "TOO_FAST"}}

        ranked = self.policy.rank_videos([video], user_feedback=feedback)

        expected = self.policy.too_fast_penalty + self.policy.unwatched_boost
        self.assertEqual(ranked[0]["video_score"], expected)

    def test_student_preference_too_fast_penalizes_short_video(self):
        rid = uuid4()
        # duration kept outside the 180-900s sweet spot and below 1800s so
        # only the TOO_FAST preference branch contributes besides unwatched.
        video = {"resource_id": str(rid), "video_detail": {"duration_seconds": 100}}

        ranked = self.policy.rank_videos(
            [video], student_preferences={"TOO_FAST": 1}
        )

        expected = self.policy.too_fast_penalty + self.policy.unwatched_boost
        self.assertEqual(ranked[0]["video_score"], expected)

    def test_student_preference_too_fast_does_not_apply_to_long_video(self):
        rid = uuid4()
        video = {"resource_id": str(rid), "video_detail": {"duration_seconds": 400}}

        ranked = self.policy.rank_videos(
            [video], student_preferences={"TOO_FAST": 1}
        )

        # duration 400 is inside the 180-900 sweet spot but the TOO_FAST
        # preference branch requires dur < 300, so it must NOT apply here.
        expected = self.policy.duration_sweet_spot_boost + self.policy.unwatched_boost
        self.assertEqual(ranked[0]["video_score"], expected)

    def test_student_preference_too_basic_penalizes_easy_video(self):
        rid = uuid4()
        video = {"resource_id": str(rid), "recommended_level": "EASY"}

        ranked = self.policy.rank_videos(
            [video], student_preferences={"TOO_BASIC": 1}
        )

        expected = self.policy.too_basic_penalty + self.policy.unwatched_boost
        self.assertEqual(ranked[0]["video_score"], expected)

    def test_student_preference_too_advanced_penalizes_hard_video(self):
        rid = uuid4()
        video = {"resource_id": str(rid), "recommended_level": "HARD"}

        ranked = self.policy.rank_videos(
            [video], student_preferences={"TOO_ADVANCED": 1}
        )

        # The TOO_ADVANCED preference branch (video_engine.py line ~166)
        # reuses `too_basic_penalty` rather than a dedicated constant -
        # asserting the literal current behavior here.
        expected = self.policy.too_basic_penalty + self.policy.unwatched_boost
        self.assertEqual(ranked[0]["video_score"], expected)

    def test_student_preference_needs_examples_boosts_matching_title(self):
        rid = uuid4()
        video = {
            "resource_id": str(rid),
            "title": "Vídeo com exemplo prático",
            "description": "",
        }

        ranked = self.policy.rank_videos(
            [video], student_preferences={"NEEDS_EXAMPLES": 1}
        )

        expected = self.policy.prefers_examples_boost + self.policy.unwatched_boost
        self.assertEqual(ranked[0]["video_score"], expected)

    def test_student_preference_needs_examples_matches_exercicio_in_description(self):
        rid = uuid4()
        video = {
            "resource_id": str(rid),
            "title": "Aula de Diluição",
            "description": "Traz um exercício resolvido ao final.",
        }

        ranked = self.policy.rank_videos(
            [video], student_preferences={"NEEDS_EXAMPLES": 1}
        )

        expected = self.policy.prefers_examples_boost + self.policy.unwatched_boost
        self.assertEqual(ranked[0]["video_score"], expected)


class TestVideoRecommendationEngineDbBackedBranches(unittest.IsolatedAsyncioTestCase):
    """DB-backed tests mirroring tests/test_video_engine.py's fixture style
    (in-memory async SQLite, expire_on_commit=False)."""

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

    async def _seed_content_node(self, session: AsyncSession):
        root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id

        content_node = CatalogNode(
            parent_id=root.id,
            root_id=root.id,
            node_type="CONTENT",
            code="QUIM-COV",
            name="Cobertura de Vídeos",
            position=1,
            active=True,
        )
        session.add(content_node)
        await session.flush()
        return content_node

    async def _seed_resource_with_video(self, session: AsyncSession, content_node_id, title="Vídeo de teste"):
        res = EducationalResource(
            title=title,
            resource_type="VIDEO",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            source_url="https://youtube.com/watch?v=cov",
            status="active",
        )
        session.add(res)
        await session.flush()
        session.add(VideoResourceDetail(resource_id=res.id, platform="YOUTUBE", external_video_id="cov", duration_seconds=300))
        session.add(ContentResourceLink(content_node_id=content_node_id, resource_id=res.id, pedagogical_role="VIDEO", recommended_level="EASY"))
        await session.commit()
        return res

    # --- item 10 --------------------------------------------------------
    async def test_recommend_returns_no_video_available_when_content_node_missing(self):
        async with self.session_factory() as session:
            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)

            res = await video_engine.recommend_video_for_student(
                student_id="s_ghost_node",
                content_node_id=uuid4(),
            )

            self.assertEqual(res["status"], "NO_VIDEO_AVAILABLE")
            self.assertEqual(res["reason"], "Conteúdo não encontrado no catálogo.")
            self.assertIsNone(res["video"])

    # --- item 11 ----------------------------------------------------------
    async def test_recommend_gracefully_handles_ranked_candidate_missing_from_db(self):
        """A ranked candidate whose resource_id no longer exists as an
        EducationalResource row (e.g. deleted-but-still-indexed resource)
        must not crash the recommendation flow - it should degrade to a
        clean NO_VIDEO_AVAILABLE response."""
        async with self.session_factory() as session:
            content_node = await self._seed_content_node(session)
            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)

            ghost_resource_id = uuid4()
            fake_candidate = {
                "resource_id": str(ghost_resource_id),
                "title": "Vídeo fantasma",
                "resource_type": "VIDEO",
                "origin_type": "PLATFORM",
                "visibility_scope": "PUBLIC",
                "recommended_level": "EASY",
                "video_detail": None,
            }
            video_engine.knowledge_service.find_resources_by_content = AsyncMock(
                return_value=[fake_candidate]
            )

            res = await video_engine.recommend_video_for_student(
                student_id="s_ghost_resource",
                content_node_id=content_node.id,
            )

            self.assertEqual(res["status"], "NO_VIDEO_AVAILABLE")
            self.assertEqual(res["reason"], "Erro ao carregar detalhes do recurso de vídeo.")
            self.assertIsNone(res["video"])

    # --- item 12 ----------------------------------------------------------
    async def test_get_student_video_progress_in_progress_via_progress_percentage(self):
        async with self.session_factory() as session:
            content_node = await self._seed_content_node(session)
            res = await self._seed_resource_with_video(session, content_node.id)

            session.add(
                VideoInteractionEvent(
                    student_id="s_inprog_a",
                    resource_id=res.id,
                    content_node_id=content_node.id,
                    event_type="PROGRESS",
                    progress_percentage=Decimal("42.00"),
                )
            )
            await session.commit()

            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)
            progress = await video_engine.get_student_video_progress(
                student_id="s_inprog_a", resource_id=res.id
            )

            self.assertEqual(progress["status"], "IN_PROGRESS")
            self.assertEqual(progress["progress_percentage"], 42.0)

    async def test_get_student_video_progress_in_progress_via_event_type_only(self):
        async with self.session_factory() as session:
            content_node = await self._seed_content_node(session)
            res = await self._seed_resource_with_video(session, content_node.id)

            # OPENED event with no progress_percentage set at all: must still
            # be classified IN_PROGRESS (not UNWATCHED, not COMPLETED).
            session.add(
                VideoInteractionEvent(
                    student_id="s_inprog_b",
                    resource_id=res.id,
                    content_node_id=content_node.id,
                    event_type="OPENED",
                    progress_percentage=None,
                )
            )
            await session.commit()

            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)
            progress = await video_engine.get_student_video_progress(
                student_id="s_inprog_b", resource_id=res.id
            )

            self.assertEqual(progress["status"], "IN_PROGRESS")
            self.assertEqual(progress["progress_percentage"], 0.0)

    async def test_get_student_video_progress_completed_via_event_type(self):
        """Covers the COMPLETED branch (progress can stay below 100 on the
        raw events, but a COMPLETED event type still forces status/progress
        to COMPLETED/100.0) - not exercised by any existing test file."""
        async with self.session_factory() as session:
            content_node = await self._seed_content_node(session)
            res = await self._seed_resource_with_video(session, content_node.id)

            session.add(
                VideoInteractionEvent(
                    student_id="s_completed",
                    resource_id=res.id,
                    content_node_id=content_node.id,
                    event_type="PROGRESS",
                    progress_percentage=Decimal("80.00"),
                )
            )
            session.add(
                VideoInteractionEvent(
                    student_id="s_completed",
                    resource_id=res.id,
                    content_node_id=content_node.id,
                    event_type="COMPLETED",
                    progress_percentage=None,
                )
            )
            await session.commit()

            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)
            progress = await video_engine.get_student_video_progress(
                student_id="s_completed", resource_id=res.id
            )

            self.assertEqual(progress["status"], "COMPLETED")
            self.assertEqual(progress["progress_percentage"], 100.0)

    # --- item 13 ----------------------------------------------------------
    async def test_fetch_student_video_history_keeps_max_progress_across_events(self):
        async with self.session_factory() as session:
            content_node = await self._seed_content_node(session)
            res = await self._seed_resource_with_video(session, content_node.id)

            session.add(
                VideoInteractionEvent(
                    student_id="s_max_prog",
                    resource_id=res.id,
                    content_node_id=content_node.id,
                    event_type="PROGRESS",
                    progress_percentage=Decimal("30.00"),
                )
            )
            session.add(
                VideoInteractionEvent(
                    student_id="s_max_prog",
                    resource_id=res.id,
                    content_node_id=content_node.id,
                    event_type="PROGRESS",
                    progress_percentage=Decimal("70.00"),
                )
            )
            await session.commit()

            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)

            watched_ids, video_progress, user_feedback, preferences = (
                await video_engine._fetch_student_video_history("s_max_prog")
            )

            self.assertIn(res.id, watched_ids)
            self.assertEqual(video_progress[res.id], 70.0)

            # Confirm it flows into the ranked recommendation's audit trail
            # too: mastery is 0 -> EASY target, and the seeded resource is
            # EASY, in-progress (not fully watched), so it should still be
            # recommendable and carry an in-progress-consistent score.
            rec = await video_engine.recommend_video_for_student(
                student_id="s_max_prog", content_node_id=content_node.id
            )
            self.assertEqual(rec["status"], "OK")
            self.assertEqual(rec["video_resource_id"], str(res.id))

    # --- item 14 ----------------------------------------------------------
    async def test_fetch_student_video_history_pedagogical_recommendation_fallback(self):
        async with self.session_factory() as session:
            content_node = await self._seed_content_node(session)
            fallback_resource_id = uuid4()

            rec_row = PedagogicalRecommendation(
                student_id="s_pr_fallback",
                content_node_id=content_node.id,
                recommendation_type="WATCH_VIDEO",
                recommended_difficulty="EASY",
                priority_score=Decimal("50.00"),
                reason="Assistir vídeo recomendado.",
                context_source="AUTONOMOUS",
                status="ACTIVE",
                metadata_={
                    "interactions": [
                        {
                            "resource_id": str(fallback_resource_id),
                            "progress": 60,
                            "action": "FEEDBACK",
                            "feedback_type": "LIKED",
                            "feedback_reason": "NEEDS_EXAMPLES",
                        }
                    ]
                },
            )
            session.add(rec_row)
            await session.commit()

            ks = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, ks)

            watched_ids, video_progress, user_feedback, preferences = (
                await video_engine._fetch_student_video_history("s_pr_fallback")
            )

            self.assertIn(fallback_resource_id, watched_ids)
            self.assertEqual(video_progress[fallback_resource_id], 60.0)
            self.assertEqual(
                user_feedback[fallback_resource_id],
                {"type": "LIKED", "reason": "NEEDS_EXAMPLES"},
            )
            self.assertEqual(preferences.get("NEEDS_EXAMPLES"), 1)


if __name__ == "__main__":
    unittest.main()
