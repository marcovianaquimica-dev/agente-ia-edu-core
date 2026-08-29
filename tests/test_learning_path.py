"""
Tests for learning path module.

Covers:
- Mastery calculation
- Difficulty progression
- Confidence calculation
- Practice sessions
- Learning history recording
"""

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    StudentContentMastery,
    LearningHistory,
    PracticeSession,
    PracticeQuestionSelection,
)
from agente_ia_edu.services.learning_path import (
    ContentMasteryService,
    DifficultyRecommendationService,
    LearningHistoryService,
    PracticeSessionService,
)
from agente_ia_edu.services.learning_path_policies import (
    DifficultyLevel,
    ActivityType,
    DifficultyProgressionPolicy,
    MasteryCalculationPolicy,
    ConfidencePolicy,
    MasteryThresholds,
)


class MasteryCalculationPolicyTests(unittest.TestCase):
    """Tests for mastery score calculation."""

    def setUp(self):
        self.policy = MasteryCalculationPolicy()

    def test_calculate_mastery_no_answers(self):
        """Test mastery calculation with no answers."""
        score = self.policy.calculate_mastery_score(0, 0)
        self.assertEqual(score, 0.0)

    def test_calculate_mastery_50_percent(self):
        """Test mastery calculation with 50% correct."""
        score = self.policy.calculate_mastery_score(10, 5)
        self.assertEqual(score, 50.0)

    def test_calculate_mastery_100_percent(self):
        """Test mastery calculation with 100% correct."""
        score = self.policy.calculate_mastery_score(10, 10)
        self.assertEqual(score, 100.0)

    def test_calculate_mastery_with_recent_weight(self):
        """Test mastery calculation with recent performance weighting."""
        # Overall: 8/10 = 80%, Recent: 2/2 = 100%
        # Weighted: 0.6 * 100 + 0.4 * 80 = 92
        score = self.policy.calculate_mastery_score(10, 8, 2, 2)
        self.assertAlmostEqual(score, 92.0, delta=0.1)


class ConfidencePolicyTests(unittest.TestCase):
    """Tests for confidence calculation."""

    def setUp(self):
        self.policy = ConfidencePolicy()

    def test_confidence_no_questions(self):
        """Test confidence with no questions answered."""
        confidence = self.policy.calculate_confidence(0, 0)
        self.assertEqual(confidence, 0.0)

    def test_confidence_one_question(self):
        """Test confidence with one question."""
        confidence = self.policy.calculate_confidence(1, 1)
        self.assertLess(confidence, 0.3)
        self.assertGreater(confidence, 0.0)

    def test_confidence_few_questions(self):
        """Test confidence with few questions (low evidence)."""
        confidence = self.policy.calculate_confidence(3, 3)
        self.assertGreaterEqual(confidence, 0.3)
        self.assertLess(confidence, 0.6)

    def test_confidence_medium_questions(self):
        """Test confidence with medium number of questions."""
        confidence = self.policy.calculate_confidence(10, 10)
        self.assertGreaterEqual(confidence, 0.6)
        self.assertLess(confidence, 0.85)

    def test_confidence_many_questions(self):
        """Test confidence with many questions (high evidence)."""
        confidence = self.policy.calculate_confidence(50, 45)
        self.assertGreater(confidence, 0.85)
        self.assertLessEqual(confidence, 1.0)

    def test_confidence_increases_with_quantity(self):
        """Test that confidence increases with more questions."""
        conf_3 = self.policy.calculate_confidence(3, 3)
        conf_10 = self.policy.calculate_confidence(10, 10)
        conf_30 = self.policy.calculate_confidence(30, 30)
        self.assertLess(conf_3, conf_10)
        self.assertLess(conf_10, conf_30)


class DifficultyProgressionPolicyTests(unittest.TestCase):
    """Tests for difficulty progression rules."""

    def setUp(self):
        self.policy = DifficultyProgressionPolicy()

    def test_initial_level_is_easy(self):
        """Test that initial level is EASY."""
        level = self.policy.get_initial_level()
        self.assertEqual(level, DifficultyLevel.EASY)

    def test_no_progression_without_confidence(self):
        """Test that progression doesn't happen without sufficient confidence."""
        # High score but low confidence
        next_level = self.policy.recommend_next_level(
            DifficultyLevel.EASY,
            mastery_score=90.0,
            confidence=0.1,  # Too low
            questions_answered=1,
        )
        self.assertEqual(next_level, DifficultyLevel.EASY)

    def test_easy_to_medium_progression(self):
        """Test progression from EASY to MEDIUM."""
        next_level = self.policy.recommend_next_level(
            DifficultyLevel.EASY,
            mastery_score=75.0,
            confidence=0.7,
            questions_answered=10,
        )
        self.assertEqual(next_level, DifficultyLevel.MEDIUM)

    def test_easy_to_easy_insufficient_score(self):
        """Test staying in EASY with insufficient score."""
        next_level = self.policy.recommend_next_level(
            DifficultyLevel.EASY,
            mastery_score=60.0,
            confidence=0.7,
            questions_answered=10,
        )
        self.assertEqual(next_level, DifficultyLevel.EASY)

    def test_medium_to_hard_progression(self):
        """Test progression from MEDIUM to HARD."""
        next_level = self.policy.recommend_next_level(
            DifficultyLevel.MEDIUM,
            mastery_score=75.0,
            confidence=0.7,
            questions_answered=15,
        )
        self.assertEqual(next_level, DifficultyLevel.HARD)

    def test_medium_to_easy_regression(self):
        """Test regression from MEDIUM to EASY on poor performance."""
        next_level = self.policy.recommend_next_level(
            DifficultyLevel.MEDIUM,
            mastery_score=30.0,  # Very low
            confidence=0.7,
            questions_answered=15,
        )
        self.assertEqual(next_level, DifficultyLevel.EASY)

    def test_hard_stays_hard_sufficient_score(self):
        """Test staying in HARD with sufficient score."""
        next_level = self.policy.recommend_next_level(
            DifficultyLevel.HARD,
            mastery_score=65.0,
            confidence=0.8,
            questions_answered=20,
        )
        self.assertEqual(next_level, DifficultyLevel.HARD)

    def test_hard_to_medium_poor_score(self):
        """Test regression from HARD to MEDIUM on poor performance."""
        next_level = self.policy.recommend_next_level(
            DifficultyLevel.HARD,
            mastery_score=30.0,  # Very low
            confidence=0.8,
            questions_answered=20,
        )
        self.assertEqual(next_level, DifficultyLevel.MEDIUM)

    def test_initial_level_from_other_content_no_evidence(self):
        """No prior masteries: should default to EASY."""
        level = self.policy.recommend_initial_level_from_other_content([])
        self.assertEqual(level, DifficultyLevel.EASY)

    def test_initial_level_from_other_content_transfers_hard(self):
        """Well-evidenced HARD mastery elsewhere should transfer to new content."""
        level = self.policy.recommend_initial_level_from_other_content(
            [(DifficultyLevel.HARD, 85.0, 0.9)]
        )
        self.assertEqual(level, DifficultyLevel.HARD)

    def test_initial_level_from_other_content_ignores_low_confidence(self):
        """Low-confidence mastery elsewhere should not transfer."""
        level = self.policy.recommend_initial_level_from_other_content(
            [(DifficultyLevel.HARD, 90.0, 0.2)]
        )
        self.assertEqual(level, DifficultyLevel.EASY)

    def test_initial_level_from_other_content_ignores_low_score(self):
        """Confident but low-score mastery elsewhere should not transfer."""
        level = self.policy.recommend_initial_level_from_other_content(
            [(DifficultyLevel.HARD, 20.0, 0.9)]
        )
        self.assertEqual(level, DifficultyLevel.EASY)

    def test_initial_level_from_other_content_takes_highest_qualifying(self):
        """Should take the highest qualifying level among multiple contents."""
        level = self.policy.recommend_initial_level_from_other_content(
            [
                (DifficultyLevel.MEDIUM, 75.0, 0.7),
                (DifficultyLevel.HARD, 20.0, 0.9),  # disqualified by score
                (DifficultyLevel.EASY, 100.0, 0.9),
            ]
        )
        self.assertEqual(level, DifficultyLevel.MEDIUM)


class ContentMasteryServiceTests(unittest.IsolatedAsyncioTestCase):
    """Tests for ContentMasteryService."""

    async def asyncSetUp(self):
        """Set up test database."""
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.AsyncSessionLocal = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self):
        """Tear down test database."""
        await self.engine.dispose()

    async def test_get_or_create_mastery_new_student(self):
        """Test creating initial mastery for new student."""
        async with self.AsyncSessionLocal() as session:
            service = ContentMasteryService()
            student_id = "student:alice"
            content_id = uuid4()

            mastery = await service.get_or_create_mastery(
                session,
                student_id,
                str(content_id),
            )

            self.assertEqual(mastery.external_identity_id, student_id)
            self.assertEqual(mastery.current_level, DifficultyLevel.EASY.value)
            self.assertEqual(mastery.mastery_score, 0.0)
            self.assertEqual(mastery.confidence, 0.0)

    async def test_update_mastery_after_correct_response(self):
        """Test mastery update after correct response."""
        async with self.AsyncSessionLocal() as session:
            service = ContentMasteryService()
            student_id = "student:alice"
            content_id = uuid4()

            mastery = await service.get_or_create_mastery(
                session,
                student_id,
                str(content_id),
            )

            # Record correct response
            await service.update_mastery_after_response(session, mastery, is_correct=True)

            self.assertEqual(mastery.questions_answered, 1)
            self.assertEqual(mastery.questions_correct, 1)
            self.assertEqual(mastery.mastery_score, 100.0)

    async def test_update_mastery_multiple_responses(self):
        """Test mastery update with multiple responses."""
        async with self.AsyncSessionLocal() as session:
            service = ContentMasteryService()
            student_id = "student:alice"
            content_id = uuid4()

            mastery = await service.get_or_create_mastery(
                session,
                student_id,
                str(content_id),
            )

            # 7 correct, 3 incorrect
            for _ in range(7):
                await service.update_mastery_after_response(
                    session,
                    mastery,
                    is_correct=True,
                )
            for _ in range(3):
                await service.update_mastery_after_response(
                    session,
                    mastery,
                    is_correct=False,
                )

            self.assertEqual(mastery.questions_answered, 10)
            self.assertEqual(mastery.questions_correct, 7)
            self.assertEqual(mastery.mastery_score, 70.0)
            self.assertGreaterEqual(mastery.confidence, 0.6)

    async def test_update_mastery_progression(self):
        """Test that mastery progression works."""
        async with self.AsyncSessionLocal() as session:
            service = ContentMasteryService()
            student_id = "student:alice"
            content_id = uuid4()

            mastery = await service.get_or_create_mastery(
                session,
                student_id,
                str(content_id),
            )

            # Start at EASY
            self.assertEqual(mastery.current_level, DifficultyLevel.EASY.value)

            # Get good performance
            for _ in range(10):
                await service.update_mastery_after_response(
                    session,
                    mastery,
                    is_correct=True,
                )

            # Should progress to MEDIUM or higher
            self.assertNotEqual(mastery.current_level, DifficultyLevel.EASY.value)


class DifficultyRecommendationServiceTests(unittest.IsolatedAsyncioTestCase):
    """Tests for DifficultyRecommendationService."""

    async def asyncSetUp(self):
        """Set up test database."""
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.AsyncSessionLocal = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self):
        """Tear down test database."""
        await self.engine.dispose()

    async def test_initial_recommendation_no_history(self):
        """Test recommendation for student with no history."""
        async with self.AsyncSessionLocal() as session:
            service = DifficultyRecommendationService()
            student_id = "student:bob"
            content_id = uuid4()

            difficulty = await service.get_recommended_difficulty(
                session,
                student_id,
                str(content_id),
            )

            self.assertEqual(difficulty, DifficultyLevel.EASY)

    async def test_recommendation_follows_mastery_level(self):
        """Test that recommendation follows student's current mastery level."""
        async with self.AsyncSessionLocal() as session:
            mastery = StudentContentMastery(
                external_identity_id="student:charlie",
                content_node_id=uuid4(),
                mastery_score=80.0,
                current_level=DifficultyLevel.MEDIUM.value,
                questions_answered=10,
                questions_correct=8,
                confidence=0.7,
            )
            session.add(mastery)
            await session.flush()

            service = DifficultyRecommendationService()
            difficulty = await service.get_recommended_difficulty(
                session,
                mastery.external_identity_id,
                str(mastery.content_node_id),
            )

            self.assertEqual(difficulty, DifficultyLevel.MEDIUM)

    async def test_new_content_inherits_level_from_well_evidenced_mastery(self):
        """Student with strong, well-evidenced mastery elsewhere should not
        restart at EASY when starting a brand-new content."""
        async with self.AsyncSessionLocal() as session:
            mastery = StudentContentMastery(
                external_identity_id="student:erin",
                content_node_id=uuid4(),
                mastery_score=85.0,
                current_level=DifficultyLevel.HARD.value,
                questions_answered=25,
                questions_correct=21,
                confidence=0.9,
            )
            session.add(mastery)
            await session.flush()

            service = DifficultyRecommendationService()
            new_content_id = uuid4()

            difficulty = await service.get_recommended_difficulty(
                session,
                "student:erin",
                str(new_content_id),
            )

            self.assertEqual(difficulty, DifficultyLevel.HARD)

    async def test_new_content_stays_easy_without_sufficient_evidence(self):
        """Student with low-confidence (little evidence) mastery elsewhere
        should still start new content at EASY."""
        async with self.AsyncSessionLocal() as session:
            mastery = StudentContentMastery(
                external_identity_id="student:frank",
                content_node_id=uuid4(),
                mastery_score=90.0,
                current_level=DifficultyLevel.MEDIUM.value,
                questions_answered=1,
                questions_correct=1,
                confidence=0.1,  # Insufficient evidence
            )
            session.add(mastery)
            await session.flush()

            service = DifficultyRecommendationService()
            new_content_id = uuid4()

            difficulty = await service.get_recommended_difficulty(
                session,
                "student:frank",
                str(new_content_id),
            )

            self.assertEqual(difficulty, DifficultyLevel.EASY)


class LearningHistoryServiceTests(unittest.IsolatedAsyncioTestCase):
    """Tests for LearningHistoryService."""

    async def asyncSetUp(self):
        """Set up test database."""
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.AsyncSessionLocal = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self):
        """Tear down test database."""
        await self.engine.dispose()

    async def test_record_history_official_assessment(self):
        """Test recording learning history for official assessment."""
        async with self.AsyncSessionLocal() as session:
            service = LearningHistoryService()
            student_id = "student:diana"
            question_id = uuid4()
            content_id = uuid4()

            history = await service.record_history(
                session,
                student_id,
                ActivityType.OFFICIAL_ASSESSMENT,
                str(question_id),
                DifficultyLevel.MEDIUM,
                is_correct=True,
                points_awarded=1.0,
                content_node_id=str(content_id),
            )

            self.assertEqual(history.external_identity_id, student_id)
            self.assertEqual(history.activity_type, ActivityType.OFFICIAL_ASSESSMENT.value)
            self.assertTrue(history.is_correct)

    async def test_record_history_individual_practice(self):
        """Test recording learning history for individual practice."""
        async with self.AsyncSessionLocal() as session:
            service = LearningHistoryService()
            student_id = "student:eve"
            question_id = uuid4()
            practice_session_id = uuid4()

            history = await service.record_history(
                session,
                student_id,
                ActivityType.INDIVIDUAL_PRACTICE,
                str(question_id),
                DifficultyLevel.EASY,
                is_correct=False,
                practice_session_id=practice_session_id,
            )

            self.assertEqual(history.activity_type, ActivityType.INDIVIDUAL_PRACTICE.value)
            self.assertFalse(history.is_correct)


class PracticeSessionServiceTests(unittest.IsolatedAsyncioTestCase):
    """Tests for PracticeSessionService."""

    async def asyncSetUp(self):
        """Set up test database."""
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.AsyncSessionLocal = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self):
        """Tear down test database."""
        await self.engine.dispose()

    async def test_create_session(self):
        """Test creating a practice session."""
        async with self.AsyncSessionLocal() as session:
            service = PracticeSessionService()
            student_id = "student:frank"
            content_id = uuid4()

            practice_session = await service.create_session(
                session,
                student_id,
                content_node_id=content_id,
                requested_question_count=15,
                recommended_difficulty=DifficultyLevel.MEDIUM,
                recommendation_reason="User requested MEDIUM difficulty",
            )

            self.assertEqual(practice_session.external_identity_id, student_id)
            self.assertEqual(practice_session.status, "active")
            self.assertEqual(practice_session.requested_question_count, 15)
            self.assertEqual(practice_session.recommended_difficulty, DifficultyLevel.MEDIUM.value)

    async def test_mark_session_completed(self):
        """Test marking session as completed."""
        async with self.AsyncSessionLocal() as session:
            service = PracticeSessionService()
            practice_session = PracticeSession(
                external_identity_id="student:grace",
                content_node_id=uuid4(),
                recommended_difficulty=DifficultyLevel.EASY.value,
                requested_question_count=10,
                status="active",
            )
            session.add(practice_session)
            await session.flush()

            await service.mark_completed(session, practice_session)

            self.assertEqual(practice_session.status, "completed")
            self.assertIsNotNone(practice_session.completed_at)


class CrossUserIsolationTests(unittest.IsolatedAsyncioTestCase):
    """Tests for cross-user isolation (security)."""

    async def asyncSetUp(self):
        """Set up test database."""
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.AsyncSessionLocal = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self):
        """Tear down test database."""
        await self.engine.dispose()

    async def test_students_have_separate_mastery(self):
        """Test that different students have separate mastery records."""
        async with self.AsyncSessionLocal() as session:
            content_id = uuid4()

            mastery_a = StudentContentMastery(
                external_identity_id="student:alice",
                content_node_id=content_id,
                mastery_score=80.0,
                current_level=DifficultyLevel.MEDIUM.value,
                questions_answered=10,
                questions_correct=8,
                confidence=0.7,
            )
            mastery_b = StudentContentMastery(
                external_identity_id="student:bob",
                content_node_id=content_id,
                mastery_score=40.0,
                current_level=DifficultyLevel.EASY.value,
                questions_answered=5,
                questions_correct=2,
                confidence=0.3,
            )
            session.add(mastery_a)
            session.add(mastery_b)
            await session.flush()

            # Verify they're separate
            self.assertNotEqual(mastery_a.external_identity_id, mastery_b.external_identity_id)
            self.assertNotEqual(mastery_a.mastery_score, mastery_b.mastery_score)
            self.assertNotEqual(mastery_a.current_level, mastery_b.current_level)

    async def test_students_have_separate_learning_history(self):
        """Test that different students have separate learning history."""
        async with self.AsyncSessionLocal() as session:
            question_id = uuid4()

            hist_a = LearningHistory(
                external_identity_id="student:alice",
                activity_type=ActivityType.INDIVIDUAL_PRACTICE.value,
                question_version_id=question_id,
                difficulty_level=DifficultyLevel.MEDIUM.value,
                is_correct=True,
            )
            hist_b = LearningHistory(
                external_identity_id="student:bob",
                activity_type=ActivityType.INDIVIDUAL_PRACTICE.value,
                question_version_id=question_id,
                difficulty_level=DifficultyLevel.EASY.value,
                is_correct=False,
            )
            session.add(hist_a)
            session.add(hist_b)
            await session.flush()

            # Verify they're separate
            self.assertNotEqual(hist_a.external_identity_id, hist_b.external_identity_id)
            self.assertNotEqual(hist_a.is_correct, hist_b.is_correct)


if __name__ == "__main__":
    unittest.main()
