"""
Learning path repositories for database persistence.

Handles queries and updates to learning history, mastery, and practice sessions.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    LearningHistory,
    StudentContentMastery,
    PracticeSession,
    PracticeQuestionSelection,
    QuestionClassification,
    QuestionVersion,
)


class LearningHistoryRepository:
    """Repository for learning history queries."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, history_id: UUID) -> Optional[LearningHistory]:
        """Get learning history record by ID."""
        result = await self.session.execute(
            select(LearningHistory).where(LearningHistory.id == history_id)
        )
        return result.scalar_one_or_none()

    async def list_by_student(
        self,
        external_identity_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LearningHistory]:
        """List recent learning history for a student."""
        result = await self.session.execute(
            select(LearningHistory)
            .where(LearningHistory.external_identity_id == external_identity_id)
            .order_by(LearningHistory.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def list_by_content(
        self,
        external_identity_id: str,
        content_node_id: UUID,
        limit: int = 100,
    ) -> list[LearningHistory]:
        """List learning history for a student in a specific content."""
        result = await self.session.execute(
            select(LearningHistory).where(
                LearningHistory.external_identity_id == external_identity_id,
                LearningHistory.content_node_id == content_node_id,
            )
            .order_by(LearningHistory.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def count_by_student_content(
        self,
        external_identity_id: str,
        content_node_id: UUID,
    ) -> int:
        """Count number of learning attempts in a content."""
        result = await self.session.execute(
            select(LearningHistory).where(
                LearningHistory.external_identity_id == external_identity_id,
                LearningHistory.content_node_id == content_node_id,
            )
        )
        return len(result.scalars().all())


class StudentContentMasteryRepository:
    """Repository for student content mastery queries."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_student_content(
        self,
        external_identity_id: str,
        content_node_id: UUID,
    ) -> Optional[StudentContentMastery]:
        """Get mastery record for student in specific content."""
        result = await self.session.execute(
            select(StudentContentMastery).where(
                StudentContentMastery.external_identity_id == external_identity_id,
                StudentContentMastery.content_node_id == content_node_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_student(
        self,
        external_identity_id: str,
    ) -> list[StudentContentMastery]:
        """List all mastery records for a student."""
        result = await self.session.execute(
            select(StudentContentMastery).where(
                StudentContentMastery.external_identity_id == external_identity_id,
            )
            .order_by(StudentContentMastery.mastery_score.desc())
        )
        return result.scalars().all()

    async def list_by_level(
        self,
        external_identity_id: str,
        difficulty_level: str,
    ) -> list[StudentContentMastery]:
        """List mastery records at specific difficulty level."""
        result = await self.session.execute(
            select(StudentContentMastery).where(
                StudentContentMastery.external_identity_id == external_identity_id,
                StudentContentMastery.current_level == difficulty_level,
            )
        )
        return result.scalars().all()

    async def get_lowest_mastery(
        self,
        external_identity_id: str,
    ) -> Optional[StudentContentMastery]:
        """Get content with lowest mastery score for a student."""
        result = await self.session.execute(
            select(StudentContentMastery)
            .where(StudentContentMastery.external_identity_id == external_identity_id)
            .order_by(StudentContentMastery.mastery_score.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class PracticeSessionRepository:
    """Repository for practice session queries."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, session_id: UUID) -> Optional[PracticeSession]:
        """Get practice session by ID."""
        result = await self.session.execute(
            select(PracticeSession).where(PracticeSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def list_by_student(
        self,
        external_identity_id: str,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[PracticeSession]:
        """List practice sessions for a student."""
        query = select(PracticeSession).where(
            PracticeSession.external_identity_id == external_identity_id,
        )
        if status:
            query = query.where(PracticeSession.status == status)

        result = await self.session.execute(
            query.order_by(PracticeSession.started_at.desc()).limit(limit)
        )
        return result.scalars().all()

    async def get_active_session(
        self,
        external_identity_id: str,
    ) -> Optional[PracticeSession]:
        """Get active practice session for student (if any)."""
        result = await self.session.execute(
            select(PracticeSession).where(
                PracticeSession.external_identity_id == external_identity_id,
                PracticeSession.status == "active",
            )
        )
        return result.scalar_one_or_none()


class PracticeQuestionSelectionRepository:
    """Repository for practice question selection queries."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, selection_id: UUID) -> Optional[PracticeQuestionSelection]:
        """Get practice question selection by ID."""
        result = await self.session.execute(
            select(PracticeQuestionSelection).where(
                PracticeQuestionSelection.id == selection_id
            )
        )
        return result.scalar_one_or_none()

    async def list_by_session(
        self,
        practice_session_id: UUID,
    ) -> list[PracticeQuestionSelection]:
        """List all questions in a practice session."""
        result = await self.session.execute(
            select(PracticeQuestionSelection)
            .where(PracticeQuestionSelection.practice_session_id == practice_session_id)
            .order_by(PracticeQuestionSelection.position.asc())
        )
        return result.scalars().all()

    async def count_by_session(self, practice_session_id: UUID) -> int:
        """Count questions in a practice session."""
        result = await self.session.execute(
            select(PracticeQuestionSelection).where(
                PracticeQuestionSelection.practice_session_id == practice_session_id,
            )
        )
        return len(result.scalars().all())

    async def count_answered(self, practice_session_id: UUID) -> int:
        """Count answered questions in a practice session."""
        result = await self.session.execute(
            select(PracticeQuestionSelection).where(
                PracticeQuestionSelection.practice_session_id == practice_session_id,
                PracticeQuestionSelection.answered_at.isnot(None),
            )
        )
        return len(result.scalars().all())


class QuestionSelectionRepository:
    """Repository for locating candidate questions for a practice session."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_candidate_versions(
        self,
        content_node_id: UUID,
        difficulty_level: Optional[str] = None,
    ) -> list[QuestionVersion]:
        """
        List active QuestionVersions classified under a content node.

        Matches either the competency or skill node of the active, primary
        classification. Optionally filters by recommended_difficulty.

        Ordered deterministically by id so the same DB state always yields
        the same candidate order.
        """
        query = (
            select(QuestionVersion)
            .join(
                QuestionClassification,
                QuestionClassification.question_version_id == QuestionVersion.id,
            )
            .where(
                QuestionClassification.status == "active",
                QuestionClassification.is_primary.is_(True),
                or_(
                    QuestionClassification.competency_node_id == content_node_id,
                    QuestionClassification.skill_node_id == content_node_id,
                ),
            )
        )
        if difficulty_level:
            query = query.where(QuestionVersion.recommended_difficulty == difficulty_level)

        query = query.distinct().order_by(QuestionVersion.id)
        result = await self.session.execute(query)
        return list(result.scalars().unique().all())

    async def recently_answered_version_ids(
        self,
        external_identity_id: str,
        content_node_id: Optional[UUID] = None,
        lookback: int = 50,
    ) -> set[UUID]:
        """
        Return the set of question_version_ids the student answered recently.

        Used to avoid repeating the same questions when enough fresh
        candidates are available.
        """
        query = select(LearningHistory.question_version_id).where(
            LearningHistory.external_identity_id == external_identity_id,
        )
        if content_node_id:
            query = query.where(LearningHistory.content_node_id == content_node_id)

        query = query.order_by(LearningHistory.created_at.desc()).limit(lookback)
        result = await self.session.execute(query)
        return set(result.scalars().all())

