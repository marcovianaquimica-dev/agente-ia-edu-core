"""Student attempt execution tests."""

import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from agente_ia_edu.services.attempt_execution import (
    AttemptExecutionService,
    AttemptResultService,
    AnswerCorrectionService,
    PublicationAvailabilityService,
)


class PublicationAvailabilityServiceTests(unittest.TestCase):
    """Test publication availability rules."""

    def test_available_when_active_and_in_time_window(self):
        """Publication is available when active and within time window."""
        now = datetime.now(timezone.utc)
        starts_at = now - timedelta(hours=1)
        ends_at = now + timedelta(hours=1)

        available = PublicationAvailabilityService.is_available(
            publication_status="active",
            starts_at=starts_at,
            ends_at=ends_at,
            assessment_version_status="published",
            now=now,
        )
        self.assertTrue(available)

    def test_unavailable_before_starts_at(self):
        """Publication is unavailable before start time."""
        now = datetime.now(timezone.utc)
        starts_at = now + timedelta(hours=1)
        ends_at = now + timedelta(hours=2)

        available = PublicationAvailabilityService.is_available(
            publication_status="active",
            starts_at=starts_at,
            ends_at=ends_at,
            assessment_version_status="published",
            now=now,
        )
        self.assertFalse(available)

    def test_unavailable_after_ends_at(self):
        """Publication is unavailable after end time."""
        now = datetime.now(timezone.utc)
        starts_at = now - timedelta(hours=2)
        ends_at = now - timedelta(hours=1)

        available = PublicationAvailabilityService.is_available(
            publication_status="active",
            starts_at=starts_at,
            ends_at=ends_at,
            assessment_version_status="published",
            now=now,
        )
        self.assertFalse(available)

    def test_unavailable_when_not_active(self):
        """Publication is unavailable if status is not active."""
        now = datetime.now(timezone.utc)
        starts_at = now - timedelta(hours=1)
        ends_at = now + timedelta(hours=1)

        available = PublicationAvailabilityService.is_available(
            publication_status="draft",
            starts_at=starts_at,
            ends_at=ends_at,
            assessment_version_status="published",
            now=now,
        )
        self.assertFalse(available)

    def test_unavailable_when_version_not_published(self):
        """Publication is unavailable if version is not published."""
        now = datetime.now(timezone.utc)
        starts_at = now - timedelta(hours=1)
        ends_at = now + timedelta(hours=1)

        available = PublicationAvailabilityService.is_available(
            publication_status="active",
            starts_at=starts_at,
            ends_at=ends_at,
            assessment_version_status="draft",
            now=now,
        )
        self.assertFalse(available)

    def test_available_without_end_time(self):
        """Publication is available indefinitely without end time."""
        now = datetime.now(timezone.utc)
        starts_at = now - timedelta(hours=1)

        available = PublicationAvailabilityService.is_available(
            publication_status="active",
            starts_at=starts_at,
            ends_at=None,
            assessment_version_status="published",
            now=now,
        )
        self.assertTrue(available)

    def test_available_without_start_time(self):
        """Publication is available immediately without start time."""
        now = datetime.now(timezone.utc)
        ends_at = now + timedelta(hours=1)

        available = PublicationAvailabilityService.is_available(
            publication_status="active",
            starts_at=None,
            ends_at=ends_at,
            assessment_version_status="published",
            now=now,
        )
        self.assertTrue(available)

    def test_get_publication_status_pending(self):
        """Status is 'pending' before start time."""
        now = datetime.now(timezone.utc)
        starts_at = now + timedelta(hours=1)

        status = PublicationAvailabilityService.get_publication_status(
            starts_at=starts_at,
            ends_at=None,
            now=now,
        )
        self.assertEqual(status, "pending")

    def test_get_publication_status_active(self):
        """Status is 'active' within time window."""
        now = datetime.now(timezone.utc)
        starts_at = now - timedelta(hours=1)
        ends_at = now + timedelta(hours=1)

        status = PublicationAvailabilityService.get_publication_status(
            starts_at=starts_at,
            ends_at=ends_at,
            now=now,
        )
        self.assertEqual(status, "active")

    def test_get_publication_status_closed(self):
        """Status is 'closed' after end time."""
        now = datetime.now(timezone.utc)
        ends_at = now - timedelta(hours=1)

        status = PublicationAvailabilityService.get_publication_status(
            starts_at=None,
            ends_at=ends_at,
            now=now,
        )
        self.assertEqual(status, "closed")


class AttemptExecutionServiceTests(unittest.TestCase):
    """Test attempt execution and timing."""

    def test_compute_expires_at_with_time_limit_only(self):
        """Expiration is start + time_limit_seconds when no publication deadline."""
        started_at = datetime.now(timezone.utc)
        time_limit_seconds = 3600

        expires_at = AttemptExecutionService.compute_expires_at(
            started_at=started_at,
            time_limit_seconds=time_limit_seconds,
            publication_ends_at=None,
        )
        expected = started_at + timedelta(seconds=time_limit_seconds)
        self.assertEqual(expires_at, expected)

    def test_compute_expires_at_with_publication_deadline_only(self):
        """Expiration is publication deadline when no time limit."""
        started_at = datetime.now(timezone.utc)
        ends_at = started_at + timedelta(hours=2)

        expires_at = AttemptExecutionService.compute_expires_at(
            started_at=started_at,
            time_limit_seconds=None,
            publication_ends_at=ends_at,
        )
        self.assertEqual(expires_at, ends_at)

    def test_compute_expires_at_uses_earliest_deadline(self):
        """Expiration is the earliest of time_limit and publication deadline."""
        started_at = datetime.now(timezone.utc)
        time_limit_expires = started_at + timedelta(minutes=30)
        publication_ends = started_at + timedelta(hours=2)

        expires_at = AttemptExecutionService.compute_expires_at(
            started_at=started_at,
            time_limit_seconds=1800,  # 30 minutes
            publication_ends_at=publication_ends,
        )
        self.assertEqual(expires_at, time_limit_expires)

    def test_compute_expires_at_prefers_earlier_publication_deadline(self):
        """Expiration uses publication deadline if earlier than time limit."""
        started_at = datetime.now(timezone.utc)
        time_limit_expires = started_at + timedelta(hours=2)
        publication_ends = started_at + timedelta(minutes=30)

        expires_at = AttemptExecutionService.compute_expires_at(
            started_at=started_at,
            time_limit_seconds=7200,  # 2 hours
            publication_ends_at=publication_ends,
        )
        self.assertEqual(expires_at, publication_ends)

    def test_compute_expires_at_returns_none_without_limits(self):
        """Expiration is None when no limits are set."""
        started_at = datetime.now(timezone.utc)

        expires_at = AttemptExecutionService.compute_expires_at(
            started_at=started_at,
            time_limit_seconds=None,
            publication_ends_at=None,
        )
        self.assertIsNone(expires_at)

    def test_is_attempt_expired_when_past_deadline(self):
        """Attempt is expired when now is past deadline."""
        now = datetime.now(timezone.utc)
        expires_at = now - timedelta(seconds=1)

        expired = AttemptExecutionService.is_attempt_expired(
            expires_at=expires_at,
            now=now,
        )
        self.assertTrue(expired)

    def test_is_attempt_not_expired_before_deadline(self):
        """Attempt is not expired when now is before deadline."""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=60)

        expired = AttemptExecutionService.is_attempt_expired(
            expires_at=expires_at,
            now=now,
        )
        self.assertFalse(expired)

    def test_is_attempt_not_expired_when_no_deadline(self):
        """Attempt is not expired when no deadline is set."""
        expired = AttemptExecutionService.is_attempt_expired(
            expires_at=None,
            now=datetime.now(timezone.utc),
        )
        self.assertFalse(expired)

    def test_get_remaining_seconds_positive(self):
        """Remaining seconds is positive before deadline."""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=3600)

        remaining = AttemptExecutionService.get_remaining_seconds(
            expires_at=expires_at,
            now=now,
        )
        self.assertIsNotNone(remaining)
        self.assertGreater(remaining, 3590)
        self.assertLessEqual(remaining, 3600)

    def test_get_remaining_seconds_zero_after_deadline(self):
        """Remaining seconds is 0 after deadline."""
        now = datetime.now(timezone.utc)
        expires_at = now - timedelta(seconds=1)

        remaining = AttemptExecutionService.get_remaining_seconds(
            expires_at=expires_at,
            now=now,
        )
        self.assertEqual(remaining, 0)

    def test_get_remaining_seconds_none_without_deadline(self):
        """Remaining seconds is None when no deadline."""
        remaining = AttemptExecutionService.get_remaining_seconds(
            expires_at=None,
            now=datetime.now(timezone.utc),
        )
        self.assertIsNone(remaining)

    def test_calculate_duration_seconds(self):
        """Duration is calculated correctly."""
        started_at = datetime.now(timezone.utc)
        submitted_at = started_at + timedelta(minutes=5, seconds=30)

        duration = AttemptExecutionService.calculate_duration_seconds(
            started_at=started_at,
            submitted_at=submitted_at,
        )
        self.assertEqual(duration, 330)  # 5 * 60 + 30


class AnswerCorrectionServiceTests(unittest.TestCase):
    """Test answer correction and scoring."""

    def test_score_answer_correct(self):
        """Correct answer awards full points."""
        points = AnswerCorrectionService.score_answer(
            is_correct=True,
            points=10,
        )
        self.assertEqual(points, 10)

    def test_score_answer_incorrect(self):
        """Incorrect answer awards zero points."""
        points = AnswerCorrectionService.score_answer(
            is_correct=False,
            points=10,
        )
        self.assertEqual(points, 0)

    def test_calculate_total_score(self):
        """Total score sums points from correct answers."""
        item_id_1 = uuid4()
        item_id_2 = uuid4()
        item_id_3 = uuid4()

        score = AnswerCorrectionService.calculate_total_score(
            correct_answers=2,
            points_per_answer={
                item_id_1: 10,
                item_id_2: 10,
                item_id_3: 0,  # incorrect
            },
        )
        self.assertEqual(score, 20.0)

    def test_calculate_percentage_valid(self):
        """Percentage is calculated correctly."""
        percentage = AnswerCorrectionService.calculate_percentage(
            score=75.0,
            max_score=100.0,
        )
        self.assertEqual(percentage, 75.0)

    def test_calculate_percentage_zero_max_score(self):
        """Percentage is None when max_score is 0."""
        percentage = AnswerCorrectionService.calculate_percentage(
            score=0,
            max_score=0,
        )
        self.assertIsNone(percentage)


class AttemptResultServiceTests(unittest.TestCase):
    """Test result calculation and formatting."""

    def test_build_result_summary_complete(self):
        """Result summary includes all calculated fields."""
        result = AttemptResultService.build_result_summary(
            score=80.0,
            max_score=100.0,
            correct_answers=8,
            answered_count=10,
            total_items=10,
            duration_seconds=600,
        )

        self.assertEqual(result["score"], 80.0)
        self.assertEqual(result["max_score"], 100.0)
        self.assertEqual(result["percentage"], 80.0)
        self.assertEqual(result["correct_answers"], 8)
        self.assertEqual(result["incorrect_answers"], 2)
        self.assertEqual(result["unanswered"], 0)
        self.assertEqual(result["answered_count"], 10)
        self.assertEqual(result["total_items"], 10)
        self.assertEqual(result["duration_seconds"], 600)

    def test_build_result_summary_with_unanswered(self):
        """Result summary calculates unanswered items."""
        result = AttemptResultService.build_result_summary(
            score=50.0,
            max_score=100.0,
            correct_answers=5,
            answered_count=7,
            total_items=10,
            duration_seconds=300,
        )

        self.assertEqual(result["unanswered"], 3)
        self.assertEqual(result["incorrect_answers"], 2)

    def test_build_result_summary_zero_max_score(self):
        """Result summary handles zero max_score."""
        result = AttemptResultService.build_result_summary(
            score=0,
            max_score=0,
            correct_answers=0,
            answered_count=0,
            total_items=10,
            duration_seconds=60,
        )

        self.assertIsNone(result["percentage"])
        self.assertEqual(result["unanswered"], 10)


if __name__ == "__main__":
    unittest.main()
