"""
Learning path domain services.

Handles learning history recording, mastery calculation, and difficulty progression.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, Union

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from ..db.models import (
    StudentContentMastery,
    LearningHistory,
    PracticeSession,
    PracticeQuestionSelection,
    QuestionVersion,
    TaxonomyNode,
)
from ..repositories.learning_path import QuestionSelectionRepository
from .learning_path_policies import (
    DifficultyLevel,
    ActivityType,
    DifficultyProgressionPolicy,
    MasteryCalculationPolicy,
    ConfidencePolicy,
)

UuidLike = Union[uuid.UUID, str]


def _as_uuid(value: Optional[UuidLike]) -> Optional[uuid.UUID]:
    """Normalize a UUID-like value (UUID or str) into a real uuid.UUID.

    All UUID columns use SQLAlchemy's native Uuid type, which requires actual
    uuid.UUID instances (it does not silently accept plain strings).
    """
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _as_difficulty_level(value: Optional[Union[DifficultyLevel, str]]) -> DifficultyLevel:
    """Normalize a difficulty input to the canonical DifficultyLevel enum."""
    if value is None:
        return DifficultyLevel.EASY
    if isinstance(value, DifficultyLevel):
        return value
    return DifficultyLevel(str(value).upper())


class ContentMasteryService:
    """
    Calculates and updates mastery metrics for a student in a specific content node.

    Mastery is deterministic, based on:
    - Performance history (correct/total)
    - Confidence (based on evidence quantity)
    - Current difficulty level
    """

    def __init__(
        self,
        progression_policy: Optional[DifficultyProgressionPolicy] = None,
        mastery_policy: Optional[MasteryCalculationPolicy] = None,
        confidence_policy: Optional[ConfidencePolicy] = None,
    ):
        self.progression_policy = progression_policy or DifficultyProgressionPolicy()
        self.mastery_policy = mastery_policy or MasteryCalculationPolicy()
        self.confidence_policy = confidence_policy or ConfidencePolicy()

    async def get_or_create_mastery(
        self,
        session: AsyncSession,
        external_identity_id: str,
        content_node_id: UuidLike,
    ) -> StudentContentMastery:
        """Get existing mastery record or create initial one."""
        from sqlalchemy import select

        content_node_id = _as_uuid(content_node_id)

        result = await session.execute(
            select(StudentContentMastery).where(
                StudentContentMastery.external_identity_id == external_identity_id,
                StudentContentMastery.content_node_id == content_node_id,
            )
        )
        mastery = result.scalar_one_or_none()

        if mastery:
            return mastery

        # Create initial mastery (EASY level, no performance)
        mastery = StudentContentMastery(
            external_identity_id=external_identity_id,
            content_node_id=content_node_id,
            mastery_score=0.0,
            current_level=DifficultyLevel.EASY.value,
            questions_answered=0,
            questions_correct=0,
            confidence=0.0,
            last_activity_at=None,
        )
        session.add(mastery)
        await session.flush()
        return mastery

    async def update_mastery_after_response(
        self,
        session: AsyncSession,
        mastery: StudentContentMastery,
        is_correct: bool,
    ) -> None:
        """Update mastery after recording a single response."""
        # Increment counters
        mastery.questions_answered += 1
        if is_correct:
            mastery.questions_correct += 1

        # Recalculate metrics
        mastery.mastery_score = self.mastery_policy.calculate_mastery_score(
            mastery.questions_answered,
            mastery.questions_correct,
        )
        mastery.confidence = self.confidence_policy.calculate_confidence(
            mastery.questions_answered,
            mastery.questions_correct,
        )

        # Update timestamp
        mastery.last_activity_at = datetime.now(timezone.utc)

        # Recommend next level
        current_level = DifficultyLevel(mastery.current_level)
        next_level = self.progression_policy.recommend_next_level(
            current_level,
            mastery.mastery_score,
            mastery.confidence,
            mastery.questions_answered,
        )
        mastery.current_level = next_level.value

        await session.flush()


class DifficultyRecommendationService:
    """
    Determines recommended difficulty level for practice based on student's mastery.
    """

    def __init__(self, progression_policy: Optional[DifficultyProgressionPolicy] = None):
        self.progression_policy = progression_policy or DifficultyProgressionPolicy()

    async def get_recommended_difficulty(
        self,
        session: AsyncSession,
        external_identity_id: str,
        content_node_id: Optional[UuidLike] = None,
    ) -> DifficultyLevel:
        """
        Get recommended difficulty for a student in a content.

        If content_node_id is None, this can be used to determine initial difficulty
        before content selection.

        Algorithm:
        1. If student has mastery in this content: return current_level
        2. If student has no mastery in this content, but demonstrates
           well-evidenced mastery in OTHER contents: transfer the highest
           qualifying level (evidence-based, deterministic)
        3. Otherwise: return EASY
        """
        from sqlalchemy import select

        content_node_id = _as_uuid(content_node_id)

        if content_node_id:
            result = await session.execute(
                select(StudentContentMastery).where(
                    StudentContentMastery.external_identity_id == external_identity_id,
                    StudentContentMastery.content_node_id == content_node_id,
                )
            )
            mastery = result.scalar_one_or_none()

            if mastery:
                return DifficultyLevel(mastery.current_level)

        # No mastery record for this content (or no content specified yet):
        # check for transferable evidence from other contents.
        other_result = await session.execute(
            select(StudentContentMastery).where(
                StudentContentMastery.external_identity_id == external_identity_id,
            )
        )
        other_masteries = [
            (DifficultyLevel(m.current_level), float(m.mastery_score), float(m.confidence))
            for m in other_result.scalars().all()
        ]

        if not other_masteries:
            return self.progression_policy.get_initial_level()

        return self.progression_policy.recommend_initial_level_from_other_content(
            other_masteries
        )


class LearningHistoryService:
    """
    Records learning activities (assessments and practices) in learning history.

    Does NOT calculate mastery; that's the ContentMasteryService's responsibility.
    """

    async def record_history(
        self,
        session: AsyncSession,
        external_identity_id: str,
        activity_type: ActivityType,
        question_version_id: UuidLike,
        difficulty_level: DifficultyLevel,
        is_correct: Optional[bool] = None,
        selected_option_id: Optional[UuidLike] = None,
        response_text: Optional[str] = None,
        points_awarded: Optional[float] = None,
        response_time_ms: Optional[int] = None,
        content_node_id: Optional[UuidLike] = None,
        assessment_attempt_id: Optional[UuidLike] = None,
        practice_session_id: Optional[UuidLike] = None,
        practice_question_selection_id: Optional[UuidLike] = None,
    ) -> LearningHistory:
        """Record a single learning activity."""
        history = LearningHistory(
            external_identity_id=external_identity_id,
            activity_type=activity_type.value,
            question_version_id=_as_uuid(question_version_id),
            selected_option_id=_as_uuid(selected_option_id),
            response_text=response_text,
            difficulty_level=difficulty_level.value,
            is_correct=is_correct,
            points_awarded=points_awarded,
            response_time_ms=response_time_ms,
            content_node_id=_as_uuid(content_node_id),
            assessment_attempt_id=_as_uuid(assessment_attempt_id),
            practice_session_id=_as_uuid(practice_session_id),
            practice_question_selection_id=_as_uuid(practice_question_selection_id),
        )
        session.add(history)
        await session.flush()
        return history


class PracticeSessionService:
    """
    Manages practice session lifecycle.

    Handles:
    - Creating sessions with difficulty recommendations
    - Selecting questions for practice
    - Tracking completion
    """

    async def create_session(
        self,
        session: AsyncSession,
        external_identity_id: str,
        content_node_id: Optional[UuidLike] = None,
        requested_question_count: int = 10,
        recommended_difficulty: Optional[Union[DifficultyLevel, str]] = None,
        recommendation_reason: Optional[str] = None,
    ) -> PracticeSession:
        """
        Create a new practice session.

        If content_node_id is not provided, it will be recommended later
        based on lowest mastery.
        """
        difficulty = _as_difficulty_level(recommended_difficulty)

        practice_session = PracticeSession(
            external_identity_id=external_identity_id,
            content_node_id=_as_uuid(content_node_id),
            recommended_difficulty=difficulty.value,
            requested_question_count=requested_question_count,
            status="active",
            recommendation_reason=recommendation_reason,
        )
        session.add(practice_session)
        await session.flush()
        return practice_session

    async def mark_completed(
        self,
        session: AsyncSession,
        practice_session: PracticeSession,
    ) -> None:
        """Mark practice session as completed."""
        practice_session.status = "completed"
        practice_session.completed_at = datetime.now(timezone.utc)
        await session.flush()

    async def complete_session(
        self,
        session: AsyncSession,
        practice_session: PracticeSession,
        external_identity_id: Optional[str] = None,
    ) -> PracticeSession:
        """Complete a practice session and update mastery/history for answered questions."""
        from sqlalchemy import select

        if external_identity_id is None:
            external_identity_id = practice_session.external_identity_id

        result = await session.execute(
            select(PracticeQuestionSelection).where(
                PracticeQuestionSelection.practice_session_id == practice_session.id,
            )
        )
        selections = list(result.scalars().all())

        for selection in selections:
            if selection.answered_at is None:
                continue

            if selection.is_correct is None:
                if selection.selected_option_id is None:
                    continue

                from ..repositories.questions import resolve_official_correct_option_id

                correct_option_id = await resolve_official_correct_option_id(
                    session, selection.question_version_id
                )
                if correct_option_id is None:
                    continue

                selection.is_correct = selection.selected_option_id == correct_option_id
                selection.points_awarded = 1.0 if selection.is_correct else 0.0
            elif selection.points_awarded is None and selection.selected_option_id is not None:
                selection.points_awarded = 1.0 if selection.is_correct else 0.0

        await session.flush()

        if practice_session.content_node_id and external_identity_id:
            mastery_service = ContentMasteryService()
            mastery = await mastery_service.get_or_create_mastery(
                session,
                external_identity_id,
                practice_session.content_node_id,
            )
            history_service = LearningHistoryService()

            for selection in selections:
                if selection.answered_at is None or selection.is_correct is None:
                    continue
                await history_service.record_history(
                    session,
                    external_identity_id,
                    ActivityType.INDIVIDUAL_PRACTICE,
                    selection.question_version_id,
                    DifficultyLevel(selection.difficulty_level),
                    is_correct=selection.is_correct,
                    selected_option_id=selection.selected_option_id,
                    response_text=selection.response_text,
                    points_awarded=selection.points_awarded,
                    response_time_ms=selection.response_time_ms,
                    content_node_id=practice_session.content_node_id,
                    practice_session_id=practice_session.id,
                    practice_question_selection_id=selection.id,
                )
                await mastery_service.update_mastery_after_response(
                    session,
                    mastery,
                    selection.is_correct,
                )

        await self.mark_completed(session, practice_session)
        await session.commit()
        return practice_session

    async def mark_abandoned(
        self,
        session: AsyncSession,
        practice_session: PracticeSession,
    ) -> None:
        """Mark practice session as abandoned (student quit early)."""
        practice_session.status = "abandoned"
        practice_session.completed_at = datetime.now(timezone.utc)
        await session.flush()


class QuestionSelectionService:
    """
    Selects real questions for a practice session.

    Deterministic: given the same content, difficulty, requested count, and
    database state (including the student's answer history), the same
    ordered list of question versions is returned every time.
    """

    def __init__(self, repository: Optional[QuestionSelectionRepository] = None):
        self._repository_override = repository

    def _repository(self, session: AsyncSession) -> QuestionSelectionRepository:
        return self._repository_override or QuestionSelectionRepository(session)

    async def select_for_session(
        self,
        session: AsyncSession,
        external_identity_id: str,
        content_node_id: Optional[UuidLike],
        difficulty_level: Union[DifficultyLevel, str],
        requested_question_count: int,
    ) -> list[QuestionVersion]:
        """
        Select up to `requested_question_count` question versions.

        Rules (in order):
        1. Match content node (competency or skill) and requested difficulty.
        2. If no questions match that exact difficulty, fall back to the
           content node alone (difficulty ignored) rather than returning
           nothing.
        3. Prefer questions the student has NOT answered recently.
        4. If there aren't enough fresh questions to fill the request,
           fill the remainder with previously-seen questions (repeats),
           still bounded by how many questions actually exist.
        5. No content_node_id resolved: nothing to select (no catalog yet).

        Never duplicates a question_version_id within the same result.
        """
        difficulty = _as_difficulty_level(difficulty_level)
        content_node_id = _as_uuid(content_node_id)
        if content_node_id is None or requested_question_count <= 0:
            return []

        repo = self._repository(session)

        candidates = await repo.list_candidate_versions(
            content_node_id, difficulty.value
        )
        if not candidates:
            # Fall back: ignore difficulty filter rather than yield nothing.
            candidates = await repo.list_candidate_versions(content_node_id, None)

        if not candidates:
            return []

        recent_ids = await repo.recently_answered_version_ids(
            external_identity_id, content_node_id
        )

        fresh = [c for c in candidates if c.id not in recent_ids]
        chosen = fresh[:requested_question_count]

        if len(chosen) < requested_question_count:
            remaining = requested_question_count - len(chosen)
            repeats = [c for c in candidates if c.id in recent_ids][:remaining]
            chosen = chosen + repeats

        return chosen[:requested_question_count]

    async def populate_session(
        self,
        session: AsyncSession,
        practice_session: PracticeSession,
        external_identity_id: str,
        difficulty_level: Union[DifficultyLevel, str],
    ) -> list[PracticeQuestionSelection]:
        """Select questions and persist them as PracticeQuestionSelection rows."""
        difficulty = _as_difficulty_level(difficulty_level)
        versions = await self.select_for_session(
            session,
            external_identity_id,
            practice_session.content_node_id,
            difficulty,
            practice_session.requested_question_count,
        )

        set_committed_value(practice_session, "question_selections", [])
        selections: list[PracticeQuestionSelection] = []
        for position, version in enumerate(versions, start=1):
            selection = PracticeQuestionSelection(
                practice_session_id=practice_session.id,
                question_version_id=version.id,
                difficulty_level=difficulty.value,
                position=position,
            )
            practice_session.question_selections.append(selection)
            session.add(selection)
            selections.append(selection)

        await session.flush()
        return selections
