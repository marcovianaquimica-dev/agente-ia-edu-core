"""Attempt execution and student workflow services for assessments."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from agente_ia_edu.services.assessments import AssessmentAttempt, AssessmentAnswer


class PublicationAvailabilityService:
    """Validates publication availability based on temporal constraints.
    
    The server is the authority for time. Client-submitted times are ignored.
    """

    @staticmethod
    def is_available(
        *,
        publication_status: str,
        starts_at: datetime | None,
        ends_at: datetime | None,
        assessment_version_status: str,
        now: datetime | None = None,
    ) -> bool:
        """Check if a publication is available for student attempts.

        Args:
            publication_status: Status of the publication (draft, active, paused, closed).
            starts_at: When the publication becomes available (UTC).
            ends_at: When the publication closes (UTC).
            assessment_version_status: Status of the assessment version.
            now: Current time (defaults to now in UTC).

        Returns:
            True if the publication is available, False otherwise.
        """
        now = now or datetime.now(timezone.utc)

        # Publication must be active
        if publication_status != "active":
            return False

        # Assessment version must be published
        if assessment_version_status != "published":
            return False

        # Before starts_at → unavailable
        if starts_at is not None and now < starts_at:
            return False

        # After ends_at → unavailable
        if ends_at is not None and now > ends_at:
            return False

        return True

    @staticmethod
    def get_publication_status(
        *,
        starts_at: datetime | None,
        ends_at: datetime | None,
        now: datetime | None = None,
    ) -> str:
        """Determine publication status based on time window.

        Args:
            starts_at: When the publication becomes available.
            ends_at: When the publication closes.
            now: Current time (defaults to now in UTC).

        Returns:
            'pending', 'active', or 'closed'.
        """
        now = now or datetime.now(timezone.utc)

        if starts_at is not None and now < starts_at:
            return "pending"
        if ends_at is not None and now > ends_at:
            return "closed"
        return "active"


class AttemptExecutionService:
    """Manages attempt lifecycle and student interactions."""

    @staticmethod
    def compute_expires_at(
        *,
        started_at: datetime,
        time_limit_seconds: int | None = None,
        publication_ends_at: datetime | None = None,
    ) -> datetime | None:
        """Calculate when an attempt expires.

        The expiration is the earliest of:
        1. started_at + time_limit_seconds
        2. publication_ends_at

        Args:
            started_at: When the attempt started.
            time_limit_seconds: Time limit for the attempt in seconds.
            publication_ends_at: When the publication closes.

        Returns:
            The expiration datetime, or None if no limit.
        """
        expiration = None

        if time_limit_seconds is not None:
            expiration = started_at + timedelta(seconds=time_limit_seconds)

        if publication_ends_at is not None:
            if expiration is None or publication_ends_at < expiration:
                expiration = publication_ends_at

        return expiration

    @staticmethod
    def is_attempt_expired(
        *,
        expires_at: datetime | None,
        now: datetime | None = None,
    ) -> bool:
        """Check if an attempt has expired.

        Args:
            expires_at: When the attempt expires.
            now: Current time (defaults to now in UTC).

        Returns:
            True if the attempt is expired, False otherwise.
        """
        if expires_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        return now > expires_at

    @staticmethod
    def get_remaining_seconds(
        *,
        expires_at: datetime | None,
        now: datetime | None = None,
    ) -> int | None:
        """Calculate remaining seconds until expiration.

        Args:
            expires_at: When the attempt expires.
            now: Current time (defaults to now in UTC).

        Returns:
            Remaining seconds, or None if no expiration.
        """
        if expires_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        remaining = expires_at - now
        return max(0, int(remaining.total_seconds()))

    @staticmethod
    def calculate_duration_seconds(
        *,
        started_at: datetime,
        submitted_at: datetime,
    ) -> int:
        """Calculate duration of an attempt.

        Args:
            started_at: When the attempt started.
            submitted_at: When the attempt was submitted.

        Returns:
            Duration in seconds.
        """
        return int((submitted_at - started_at).total_seconds())


class AnswerCorrectionService:
    """Corrects student answers based on official answer keys."""

    @staticmethod
    def score_answer(
        *,
        is_correct: bool,
        points: int,
    ) -> int:
        """Calculate points for a single answer.

        Args:
            is_correct: Whether the answer is correct.
            points: Points available for the item.

        Returns:
            Points awarded (0 or points value).
        """
        return points if is_correct else 0

    @staticmethod
    def calculate_total_score(
        *,
        correct_answers: int,
        points_per_answer: dict[UUID, int],
    ) -> float:
        """Calculate total score for an attempt.

        Args:
            correct_answers: Number of correct answers.
            points_per_answer: Mapping of answer item ID to points.

        Returns:
            Total score.
        """
        return float(sum(points_per_answer.values()) if points_per_answer else 0)

    @staticmethod
    def calculate_percentage(
        *,
        score: float,
        max_score: float,
    ) -> float | None:
        """Calculate score percentage.

        Args:
            score: Actual score.
            max_score: Maximum possible score.

        Returns:
            Percentage (0-100), or None if max_score is 0.
        """
        if max_score == 0:
            return None
        return (score / max_score) * 100


class AttemptResultService:
    """Computes and formats attempt results."""

    @staticmethod
    def build_result_summary(
        *,
        score: float | None,
        max_score: float | None,
        correct_answers: int | None,
        answered_count: int | None,
        total_items: int,
        duration_seconds: int | None,
    ) -> dict:
        """Build a result summary for an attempt.

        Args:
            score: Actual score.
            max_score: Maximum possible score.
            correct_answers: Number of correct answers.
            answered_count: Number of answered items.
            total_items: Total number of items in the assessment.
            duration_seconds: Duration of the attempt.

        Returns:
            Dictionary with result summary including percentage and unanswered count.
        """
        percentage = None
        if score is not None and max_score is not None and max_score > 0:
            percentage = (score / max_score) * 100

        unanswered = 0
        if answered_count is not None:
            unanswered = total_items - answered_count

        incorrect_answers = 0
        if correct_answers is not None and answered_count is not None:
            incorrect_answers = answered_count - correct_answers

        return {
            "score": score,
            "max_score": max_score,
            "percentage": percentage,
            "correct_answers": correct_answers,
            "incorrect_answers": incorrect_answers,
            "unanswered": unanswered,
            "answered_count": answered_count,
            "total_items": total_items,
            "duration_seconds": duration_seconds,
        }


__all__ = [
    "PublicationAvailabilityService",
    "AttemptExecutionService",
    "AnswerCorrectionService",
    "AttemptResultService",
]
