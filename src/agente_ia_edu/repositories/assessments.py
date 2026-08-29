from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.db.models.assessments import (
    Assessment,
    AssessmentAnswer,
    AssessmentAttempt,
    AssessmentItem,
    AssessmentPublication,
    AssessmentSelectionRequest,
    AssessmentVersion,
)
from agente_ia_edu.db.models.official import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    QuestionOption,
    QuestionVersion,
)


class AssessmentRepository:
    """Repository for assessment aggregate persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        title: str,
        description: str | None = None,
        institution_id: UUID | str | None = None,
        created_by_external_identity: str | None = None,
        status: str = "draft",
    ) -> Assessment:
        assessment = Assessment(
            institution_id=None if institution_id is None else UUID(str(institution_id)),
            created_by_external_identity=created_by_external_identity,
            title=title,
            description=description,
            status=status,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.session.add(assessment)
        await self.session.flush()
        return assessment

    async def get(self, assessment_id: UUID) -> Assessment | None:
        stmt = (
            select(Assessment)
            .where(Assessment.id == assessment_id)
            .options(selectinload(Assessment.versions))
        )
        return await self.session.scalar(stmt)

    async def list(self, *, limit: int = 20, offset: int = 0) -> list[Assessment]:
        stmt = (
            select(Assessment)
            .order_by(Assessment.created_at.desc())
            .offset(offset)
            .limit(limit)
            .options(selectinload(Assessment.versions))
        )
        result = await self.session.scalars(stmt)
        return list(result.all())


class AssessmentVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        assessment_id: UUID,
        version_number: int,
        title: str,
        description: str | None = None,
        status: str = "draft",
        created_by_external_identity: str | None = None,
    ) -> AssessmentVersion:
        version = AssessmentVersion(
            assessment_id=assessment_id,
            version_number=version_number,
            title=title,
            description=description,
            status=status,
            created_by_external_identity=created_by_external_identity,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.session.add(version)
        await self.session.flush()
        return version

    async def get(self, version_id: UUID) -> AssessmentVersion | None:
        stmt = (
            select(AssessmentVersion)
            .where(AssessmentVersion.id == version_id)
            .options(selectinload(AssessmentVersion.items))
        )
        return await self.session.scalar(stmt)

    async def list_by_assessment(self, assessment_id: UUID) -> list[AssessmentVersion]:
        stmt = (
            select(AssessmentVersion)
            .where(AssessmentVersion.assessment_id == assessment_id)
            .order_by(AssessmentVersion.version_number.asc())
            .options(selectinload(AssessmentVersion.items))
        )
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def ensure_mutable(self, version: AssessmentVersion) -> None:
        if version.status == "published":
            raise ValueError("Published assessment versions are immutable")


class AssessmentItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        assessment_version_id: UUID,
        question_version_id: UUID,
        position: int,
        points: int = 1,
        is_required: bool = True,
        selection_request_id: UUID | None = None,
    ) -> AssessmentItem:
        version = await self.session.get(AssessmentVersion, assessment_version_id)
        if version is None:
            raise ValueError("Assessment version does not exist")
        if version.status == "published":
            raise ValueError("Published versions cannot receive new items")

        existing_stmt = select(AssessmentItem).where(
            AssessmentItem.assessment_version_id == assessment_version_id,
            AssessmentItem.position == position,
        )
        if await self.session.scalar(existing_stmt) is not None:
            raise ValueError("Assessment item positions must be unique within a version")

        question_version = await self.session.get(QuestionVersion, question_version_id)
        if question_version is None:
            raise ValueError("Question version does not exist")

        item = AssessmentItem(
            assessment_version_id=assessment_version_id,
            question_version_id=question_version_id,
            selection_request_id=selection_request_id,
            position=position,
            points=points,
            is_required=is_required,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_by_version(self, assessment_version_id: UUID) -> list[AssessmentItem]:
        stmt = (
            select(AssessmentItem)
            .where(AssessmentItem.assessment_version_id == assessment_version_id)
            .order_by(AssessmentItem.position.asc())
        )
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def get(self, item_id: UUID) -> AssessmentItem | None:
        return await self.session.get(AssessmentItem, item_id)


class AssessmentSelectionRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        assessment_version_id: UUID,
        selection_type: str,
        original_prompt: str | None = None,
        requested_count: int | None = None,
        criteria: dict | None = None,
        status: str = "pending",
    ) -> AssessmentSelectionRequest:
        request = AssessmentSelectionRequest(
            assessment_version_id=assessment_version_id,
            selection_type=selection_type,
            original_prompt=original_prompt,
            requested_count=requested_count,
            criteria_=criteria or {},
            status=status,
            created_at=datetime.now(timezone.utc),
            completed_at=None,
        )
        self.session.add(request)
        await self.session.flush()
        return request


class AssessmentPublicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        assessment_version_id: UUID,
        publication_type: str,
        released_immediately: bool = False,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        time_limit_seconds: int | None = None,
        attempts_allowed: int | None = None,
        source_display: str = "none",
        bncc_display: str = "none",
        show_difficulty: bool = False,
    ) -> AssessmentPublication:
        version = await self.session.get(AssessmentVersion, assessment_version_id)
        if version is None:
            raise ValueError("Assessment version does not exist")
        if version.status == "published":
            raise ValueError("Published versions cannot be republished")
        if publication_type not in {"immediate", "scheduled"}:
            raise ValueError("Unsupported publication type")
        if starts_at is not None and ends_at is not None and ends_at <= starts_at:
            raise ValueError("Publication end time must be after start time")
        if time_limit_seconds is not None and time_limit_seconds <= 0:
            raise ValueError("time_limit_seconds must be greater than zero")
        if attempts_allowed is not None and attempts_allowed <= 0:
            raise ValueError("attempts_allowed must be greater than zero")

        publication = AssessmentPublication(
            assessment_version_id=assessment_version_id,
            publication_type=publication_type,
            status="draft",
            released_immediately=released_immediately,
            starts_at=starts_at,
            ends_at=ends_at,
            time_limit_seconds=time_limit_seconds,
            attempts_allowed=attempts_allowed,
            source_display=source_display,
            bncc_display=bncc_display,
            show_difficulty=show_difficulty,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.session.add(publication)
        await self.session.flush()
        return publication

    async def get(self, publication_id: UUID) -> AssessmentPublication | None:
        return await self.session.get(AssessmentPublication, publication_id)

    async def list_by_version(self, assessment_version_id: UUID) -> list[AssessmentPublication]:
        stmt = (
            select(AssessmentPublication)
            .where(AssessmentPublication.assessment_version_id == assessment_version_id)
            .order_by(AssessmentPublication.created_at.desc())
        )
        result = await self.session.scalars(stmt)
        return list(result.all())


class AssessmentAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        publication_id: UUID,
        external_identity_id: str,
        attempt_number: int,
        started_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> AssessmentAttempt:
        publication = await self.session.get(AssessmentPublication, publication_id)
        if publication is None:
            raise ValueError("Publication does not exist")
        if attempts_allowed := publication.attempts_allowed:
            if attempt_number > attempts_allowed:
                raise ValueError("Attempt number exceeds the number of attempts allowed")

        attempt = AssessmentAttempt(
            publication_id=publication_id,
            external_identity_id=external_identity_id,
            attempt_number=attempt_number,
            status="in_progress",
            started_at=started_at or datetime.now(timezone.utc),
            submitted_at=None,
            expires_at=expires_at,
            score=0,
            max_score=0,
            correct_answers=0,
            answered_count=0,
            duration_seconds=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.session.add(attempt)
        await self.session.flush()
        return attempt

    async def get(self, attempt_id: UUID) -> AssessmentAttempt | None:
        stmt = (
            select(AssessmentAttempt)
            .where(AssessmentAttempt.id == attempt_id)
            .options(selectinload(AssessmentAttempt.answers))
        )
        return await self.session.scalar(stmt)

    async def list_by_publication(
        self,
        *,
        publication_id: UUID,
        external_identity_id: str | None = None,
    ) -> list[AssessmentAttempt]:
        stmt = select(AssessmentAttempt).where(AssessmentAttempt.publication_id == publication_id)
        if external_identity_id is not None:
            stmt = stmt.where(AssessmentAttempt.external_identity_id == external_identity_id)
        stmt = stmt.order_by(AssessmentAttempt.attempt_number.asc())
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def get_next_attempt_number(
        self,
        *,
        publication_id: UUID,
        external_identity_id: str,
    ) -> int:
        """Get the next attempt number for a student.

        Returns the highest existing attempt number + 1, or 1 if no attempts exist.
        """
        stmt = (
            select(AssessmentAttempt)
            .where(
                AssessmentAttempt.publication_id == publication_id,
                AssessmentAttempt.external_identity_id == external_identity_id,
            )
            .order_by(AssessmentAttempt.attempt_number.desc())
            .limit(1)
        )
        last_attempt = await self.session.scalar(stmt)
        return (last_attempt.attempt_number + 1) if last_attempt else 1

    async def get_with_publication(
        self,
        attempt_id: UUID,
    ) -> tuple[AssessmentAttempt, AssessmentPublication] | None:
        """Get an attempt with its publication eagerly loaded."""
        stmt = (
            select(AssessmentAttempt)
            .where(AssessmentAttempt.id == attempt_id)
            .options(
                selectinload(AssessmentAttempt.publication),
                selectinload(AssessmentAttempt.answers),
            )
        )
        attempt = await self.session.scalar(stmt)
        if attempt is None:
            return None
        return (attempt, attempt.publication)

    async def finalize(
        self,
        attempt: AssessmentAttempt,
        *,
        submitted_at: datetime | None = None,
    ) -> AssessmentAttempt:
        """Mark an attempt as submitted."""
        attempt.status = "submitted"
        attempt.submitted_at = submitted_at or datetime.now(timezone.utc)
        attempt.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return attempt


class AssessmentAnswerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        attempt_id: UUID,
        assessment_item_id: UUID,
        selected_option_id: UUID | None = None,
        response_text: str | None = None,
        first_answered_at: datetime | None = None,
        submitted_at: datetime | None = None,
        response_time_ms: int | None = None,
        is_final: bool = False,
    ) -> AssessmentAnswer:
        item = await self.session.get(AssessmentItem, assessment_item_id)
        if item is None:
            raise ValueError("Assessment item does not exist")
        if selected_option_id is not None:
            stmt = select(QuestionOption).where(QuestionOption.id == selected_option_id)
            option = await self.session.scalar(stmt)
            if option is None:
                raise ValueError("Selected option does not exist")
            if option.question_version_id != item.question_version_id:
                raise ValueError("Selected option does not belong to the same question version")

        answer = AssessmentAnswer(
            attempt_id=attempt_id,
            assessment_item_id=assessment_item_id,
            selected_option_id=selected_option_id,
            response_text=response_text,
            first_answered_at=first_answered_at or datetime.now(timezone.utc),
            submitted_at=submitted_at,
            response_time_ms=response_time_ms,
            is_final=is_final,
            correction_status="pending",
            is_correct=None,
            points_awarded=0,
            corrected_at=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.session.add(answer)
        await self.session.flush()
        return answer

    async def get_by_attempt_and_item(
        self,
        *,
        attempt_id: UUID,
        assessment_item_id: UUID,
    ) -> AssessmentAnswer | None:
        stmt = select(AssessmentAnswer).where(
            AssessmentAnswer.attempt_id == attempt_id,
            AssessmentAnswer.assessment_item_id == assessment_item_id,
        )
        return await self.session.scalar(stmt)

    async def list_by_attempt(self, attempt_id: UUID) -> list[AssessmentAnswer]:
        stmt = select(AssessmentAnswer).where(AssessmentAnswer.attempt_id == attempt_id)
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def correct_objective(self, answer: AssessmentAnswer) -> AssessmentAnswer:
        item = await self.session.get(AssessmentItem, answer.assessment_item_id)
        if item is None:
            raise ValueError("Assessment item does not exist")

        if answer.selected_option_id is None:
            answer.is_correct = False
            answer.points_awarded = 0
            answer.correction_status = "incorrect"
            answer.corrected_at = datetime.now(timezone.utc)
            answer.updated_at = datetime.now(timezone.utc)
            return answer

        official_entry_stmt = (
            select(AnswerKeyEntry)
            .join(AnswerKeyEntry.booklet_question)
            .join(AnswerKeyRevision, AnswerKeyEntry.answer_key_revision_id == AnswerKeyRevision.id)
            .where(
                BookletQuestion.question_version_id == item.question_version_id,
                AnswerKeyRevision.is_official.is_(True),
            )
            .order_by(AnswerKeyRevision.revision_number.desc())
            .limit(1)
        )
        entry = await self.session.scalar(official_entry_stmt)
        if entry is None:
            raise ValueError("No official answer key found for the question version")

        correct_option_id = entry.resolved_option_id
        if correct_option_id is None:
            raise ValueError("Official answer key has no resolved option for this item")

        is_correct = answer.selected_option_id == correct_option_id
        answer.is_correct = is_correct
        answer.points_awarded = item.points if is_correct else 0
        answer.correction_status = "correct" if is_correct else "incorrect"
        answer.corrected_at = datetime.now(timezone.utc)
        answer.updated_at = datetime.now(timezone.utc)
        return answer

    async def update(
        self,
        answer: AssessmentAnswer,
        *,
        selected_option_id: UUID | None = None,
        response_text: str | None = None,
    ) -> AssessmentAnswer:
        """Update an existing answer.
        
        Allows changing response before submission.
        """
        if selected_option_id is not None:
            answer.selected_option_id = selected_option_id
        if response_text is not None:
            answer.response_text = response_text
        answer.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return answer
