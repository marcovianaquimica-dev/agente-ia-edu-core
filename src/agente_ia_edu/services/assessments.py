from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select


@dataclass
class Assessment:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    institution_id: str | None = None
    created_by_external_identity: str | None = None
    title: str = ""
    description: str | None = None
    status: str = "draft"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    versions: list["AssessmentVersion"] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.updated_at = datetime.now(timezone.utc)


@dataclass
class AssessmentVersion:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    assessment_id: uuid.UUID | None = None
    version_number: int = 1
    title: str = ""
    description: str | None = None
    status: str = "draft"
    created_by_external_identity: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: datetime | None = None
    items: list["AssessmentItem"] = field(default_factory=list)
    selection_requests: list["AssessmentSelectionRequest"] = field(default_factory=list)
    publications: list["AssessmentPublication"] = field(default_factory=list)

    def __setattr__(self, name, value):
        if name == "status" and self.__dict__.get("status") == "published" and value != "published":
            raise ValueError("Published assessment versions are immutable")
        super().__setattr__(name, value)

    def publish(self) -> None:
        self.status = "published"
        self.published_at = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)


@dataclass
class AssessmentSelectionRequest:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    assessment_version_id: uuid.UUID | None = None
    selection_type: str = "manual"
    original_prompt: str | None = None
    requested_count: int | None = None
    criteria: dict = field(default_factory=dict)
    status: str = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


@dataclass
class AssessmentItem:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    assessment_version_id: uuid.UUID | None = None
    question_version_id: uuid.UUID | None = None
    selection_request_id: uuid.UUID | None = None
    position: int = 1
    points: int = 1
    is_required: bool = True

    def __setattr__(self, name, value):
        if (
            name in {"position", "points", "question_version_id"}
            and name in self.__dict__
            and self.__dict__[name] is not None
        ):
            raise ValueError("Assessment items are immutable after creation")
        super().__setattr__(name, value)


@dataclass
class AssessmentPublication:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    assessment_version_id: uuid.UUID | None = None
    publication_type: str = "immediate"
    status: str = "draft"
    released_immediately: bool = False
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    time_limit_seconds: int | None = None
    attempts_allowed: int | None = None
    source_display: str = "none"
    bncc_display: str = "none"
    show_difficulty: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AssessmentAttempt:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    publication_id: uuid.UUID | None = None
    external_identity_id: str = ""
    attempt_number: int = 1
    status: str = "not_started"
    started_at: datetime | None = None
    submitted_at: datetime | None = None
    expires_at: datetime | None = None
    score: float | None = None
    max_score: float | None = None
    correct_answers: int = 0
    answered_count: int = 0
    duration_seconds: int | None = None
    answers: list["AssessmentAnswer"] = field(default_factory=list)


@dataclass
class AssessmentAnswer:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    attempt_id: uuid.UUID | None = None
    assessment_item_id: uuid.UUID | None = None
    selected_option_id: uuid.UUID | None = None
    response_text: str | None = None
    first_answered_at: datetime | None = None
    submitted_at: datetime | None = None
    response_time_ms: int | None = None
    is_final: bool = False
    correction_status: str = "pending"
    is_correct: bool | None = None
    points_awarded: int = 0
    corrected_at: datetime | None = None


class AssessmentPersistenceService:
    def __init__(self, session) -> None:
        self.session = session

    async def create_assessment(self, *, title: str, description: str | None = None, institution_id: str | None = None, created_by_external_identity: str | None = None) -> Assessment:
        from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel

        model = AssessmentModel(
            institution_id=None if institution_id is None else uuid.UUID(str(institution_id)),
            created_by_external_identity=created_by_external_identity,
            title=title,
            description=description,
            status="draft",
        )
        self.session.add(model)
        await self.session.flush()
        return Assessment(
            id=model.id,
            institution_id=institution_id,
            created_by_external_identity=created_by_external_identity,
            title=model.title,
            description=model.description,
            status=model.status,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def list_assessments(self, *, page: int = 1, limit: int = 20) -> list[Assessment]:
        from sqlalchemy import select
        from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel

        stmt = select(AssessmentModel).order_by(AssessmentModel.created_at.desc()).offset((page - 1) * limit).limit(limit)
        result = await self.session.scalars(stmt)
        records = result.all()
        return [
            Assessment(
                id=item.id,
                institution_id=str(item.institution_id) if item.institution_id is not None else None,
                created_by_external_identity=item.created_by_external_identity,
                title=item.title,
                description=item.description,
                status=item.status,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in records
        ]

    async def get_assessment(self, assessment_id: uuid.UUID) -> Assessment | None:
        from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel

        model = await self.session.get(AssessmentModel, assessment_id)
        if model is None:
            return None
        return Assessment(
            id=model.id,
            institution_id=str(model.institution_id) if model.institution_id is not None else None,
            created_by_external_identity=model.created_by_external_identity,
            title=model.title,
            description=model.description,
            status=model.status,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def create_version(
        self,
        *,
        assessment_id: uuid.UUID,
        version_number: int | None = None,
        title: str,
        description: str | None = None,
        status: str = "draft",
        created_by_external_identity: str | None = None,
    ) -> AssessmentVersion:
        from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel
        from agente_ia_edu.db.models.assessments import AssessmentVersion as AssessmentVersionModel

        assessment = await self.session.get(AssessmentModel, assessment_id)
        if assessment is None:
            raise ValueError("Assessment does not exist")

        resolved_version_number = version_number or (len(assessment.versions) + 1 if assessment.versions else 1)
        model = AssessmentVersionModel(
            assessment_id=assessment_id,
            version_number=resolved_version_number,
            title=title,
            description=description,
            status=status,
            created_by_external_identity=created_by_external_identity,
        )
        self.session.add(model)
        await self.session.flush()
        return AssessmentVersion(
            id=model.id,
            assessment_id=model.assessment_id,
            version_number=model.version_number,
            title=model.title,
            description=model.description,
            status=model.status,
            created_by_external_identity=model.created_by_external_identity,
            created_at=model.created_at,
            updated_at=model.updated_at,
            published_at=model.published_at,
        )

    async def add_item(
        self,
        *,
        assessment_version_id: uuid.UUID,
        question_version_id: uuid.UUID,
        position: int,
        points: int = 1,
        selection_request_id: uuid.UUID | None = None,
        is_required: bool = True,
    ) -> AssessmentItem:
        from agente_ia_edu.db.models.assessments import AssessmentItem as AssessmentItemModel
        from agente_ia_edu.db.models.assessments import AssessmentVersion as AssessmentVersionModel

        version = await self.session.get(AssessmentVersionModel, assessment_version_id)
        if version is None:
            raise ValueError("Assessment version does not exist")
        if version.status == "published":
            raise ValueError("Published versions cannot receive new items")

        existing = await self.session.scalar(
            select(AssessmentItemModel).where(
                AssessmentItemModel.assessment_version_id == assessment_version_id,
                AssessmentItemModel.position == position,
            )
        )
        if existing is not None:
            raise ValueError("Assessment item positions must be unique within a version")

        item = AssessmentItemModel(
            assessment_version_id=assessment_version_id,
            question_version_id=question_version_id,
            selection_request_id=selection_request_id,
            position=position,
            points=points,
            is_required=is_required,
        )
        self.session.add(item)
        await self.session.flush()
        return AssessmentItem(
            id=item.id,
            assessment_version_id=item.assessment_version_id,
            question_version_id=item.question_version_id,
            selection_request_id=item.selection_request_id,
            position=item.position,
            points=item.points,
            is_required=item.is_required,
        )

    async def create_publication(
        self,
        *,
        assessment_version_id: uuid.UUID,
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
        from agente_ia_edu.db.models.assessments import AssessmentPublication as AssessmentPublicationModel
        from agente_ia_edu.db.models.assessments import AssessmentVersion as AssessmentVersionModel

        version = await self.session.get(AssessmentVersionModel, assessment_version_id)
        if version is None:
            raise ValueError("Assessment version does not exist")
        if publication_type not in {"immediate", "scheduled"}:
            raise ValueError("Unsupported publication type")
        if starts_at is not None and ends_at is not None and ends_at < starts_at:
            raise ValueError("Publication end time must be after start time")

        publication = AssessmentPublicationModel(
            assessment_version_id=assessment_version_id,
            publication_type=publication_type,
            status="draft",
            released_immediately=released_immediately or publication_type == "immediate",
            starts_at=starts_at or (datetime.now(timezone.utc) if publication_type == "immediate" else None),
            ends_at=ends_at,
            time_limit_seconds=time_limit_seconds,
            attempts_allowed=attempts_allowed,
            source_display=source_display,
            bncc_display=bncc_display,
            show_difficulty=show_difficulty,
        )
        self.session.add(publication)
        await self.session.flush()
        return AssessmentPublication(
            id=publication.id,
            assessment_version_id=publication.assessment_version_id,
            publication_type=publication.publication_type,
            status=publication.status,
            released_immediately=publication.released_immediately,
            starts_at=publication.starts_at,
            ends_at=publication.ends_at,
            time_limit_seconds=publication.time_limit_seconds,
            attempts_allowed=publication.attempts_allowed,
            source_display=publication.source_display,
            bncc_display=publication.bncc_display,
            show_difficulty=publication.show_difficulty,
            created_at=publication.created_at,
            updated_at=publication.updated_at,
        )

    async def list_publications(self, assessment_id: uuid.UUID) -> list[AssessmentPublication]:
        from sqlalchemy import select
        from agente_ia_edu.db.models.assessments import AssessmentPublication as AssessmentPublicationModel
        from agente_ia_edu.db.models.assessments import AssessmentVersion as AssessmentVersionModel

        stmt = (
            select(AssessmentPublicationModel)
            .join(AssessmentVersionModel, AssessmentPublicationModel.assessment_version_id == AssessmentVersionModel.id)
            .where(AssessmentVersionModel.assessment_id == assessment_id)
            .order_by(AssessmentPublicationModel.created_at.desc())
        )
        result = await self.session.scalars(stmt)
        records = result.all()
        return [
            AssessmentPublication(
                id=item.id,
                assessment_version_id=item.assessment_version_id,
                publication_type=item.publication_type,
                status=item.status,
                released_immediately=item.released_immediately,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
                time_limit_seconds=item.time_limit_seconds,
                attempts_allowed=item.attempts_allowed,
                source_display=item.source_display,
                bncc_display=item.bncc_display,
                show_difficulty=item.show_difficulty,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in records
        ]


class AssessmentFactory:
    def create_assessment(
        self,
        *,
        title: str,
        description: str | None = None,
        created_by_external_identity: str | None = None,
        institution_id: str | None = None,
    ) -> Assessment:
        assessment = Assessment(
            institution_id=institution_id,
            created_by_external_identity=created_by_external_identity,
            title=title,
            description=description,
        )
        version = self.create_version(
            assessment=assessment,
            version_number=1,
            title=title,
            description=description,
            status="draft",
            created_by_external_identity=created_by_external_identity,
        )
        assessment.versions.append(version)
        return assessment

    def create_version(
        self,
        *,
        assessment: Assessment,
        version_number: int,
        title: str,
        description: str | None = None,
        status: str = "draft",
        created_by_external_identity: str | None = None,
    ) -> AssessmentVersion:
        version = AssessmentVersion(
            assessment_id=assessment.id,
            version_number=version_number,
            title=title,
            description=description,
            status=status,
            created_by_external_identity=created_by_external_identity,
        )
        if status == "published":
            version.publish()
        return version

    def add_item(
        self,
        version: AssessmentVersion,
        *,
        question_version_id: uuid.UUID,
        position: int,
        points: int = 1,
        selection_request_id: uuid.UUID | None = None,
        is_required: bool = True,
    ) -> AssessmentItem:
        if any(item.position == position for item in version.items):
            raise ValueError("Assessment item positions must be unique within a version")
        item = AssessmentItem(
            assessment_version_id=version.id,
            question_version_id=question_version_id,
            selection_request_id=selection_request_id,
            position=position,
            points=points,
            is_required=is_required,
        )
        version.items.append(item)
        return item

    def publish(
        self,
        version: AssessmentVersion,
        *,
        publication_type: str,
        released_immediately: bool = False,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        time_limit_seconds: int | None = None,
        attempts_allowed: int | None = None,
    ) -> AssessmentPublication:
        publication = AssessmentPublicationService.build_publication(
            assessment_version=version,
            publication_type=publication_type,
            released_immediately=released_immediately,
            starts_at=starts_at,
            ends_at=ends_at,
            time_limit_seconds=time_limit_seconds,
            attempts_allowed=attempts_allowed,
        )
        version.publications.append(publication)
        return publication


class AssessmentPublicationService:
    @staticmethod
    def build_publication(
        *,
        assessment_version: AssessmentVersion,
        publication_type: str,
        released_immediately: bool = False,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        time_limit_seconds: int | None = None,
        attempts_allowed: int | None = None,
        source_display: str = "none",
        bncc_display: str = "none",
        show_difficulty: bool = False,
        **kwargs,
    ) -> AssessmentPublication:
        if "started_at" in kwargs and starts_at is None:
            starts_at = kwargs["started_at"]
        if publication_type not in {"immediate", "scheduled"}:
            raise ValueError("Unsupported publication type")
        if publication_type == "immediate":
            released_immediately = True
            starts_at = starts_at or datetime.now(timezone.utc)
        if starts_at is not None and ends_at is not None and ends_at < starts_at:
            raise ValueError("Publication end time must be after start time")
        return AssessmentPublication(
            assessment_version_id=assessment_version.id,
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
        )


class AssessmentService:
    @staticmethod
    def start_attempt(
        *,
        publication: AssessmentPublication,
        external_identity_id: str,
        attempt_number: int,
        started_at: datetime | None = None,
    ) -> AssessmentAttempt:
        started_at = started_at or datetime.now(timezone.utc)
        expires_at = AssessmentService._compute_expires_at(
            publication=publication,
            started_at=started_at,
        )
        attempt = AssessmentAttempt(
            publication_id=publication.id,
            external_identity_id=external_identity_id,
            attempt_number=attempt_number,
            status="in_progress",
            started_at=started_at,
            expires_at=expires_at,
            score=0,
            max_score=0,
            correct_answers=0,
            answered_count=0,
        )
        if publication.attempts_allowed is not None and attempt_number > publication.attempts_allowed:
            raise ValueError("Attempt number exceeds attempts allowed")
        return attempt

    def register_answer(
        self,
        *,
        attempt: AssessmentAttempt,
        assessment_item: AssessmentItem,
        selected_option_id: uuid.UUID | None,
        response_text: str | None,
        first_answered_at: datetime | None,
        submitted_at: datetime | None,
        response_time_ms: int | None,
        is_final: bool,
        question_correct_option_id: uuid.UUID | None = None,
        question_points: int | None = None,
    ) -> AssessmentAnswer:
        is_correct = (
            selected_option_id is not None
            and question_correct_option_id is not None
            and selected_option_id == question_correct_option_id
        )
        points_awarded = int(question_points or 0) if is_correct else 0
        answer = AssessmentAnswer(
            attempt_id=attempt.id,
            assessment_item_id=assessment_item.id,
            selected_option_id=selected_option_id,
            response_text=response_text,
            first_answered_at=first_answered_at,
            submitted_at=submitted_at,
            response_time_ms=response_time_ms,
            is_final=is_final,
            correction_status="correct" if is_correct else "incorrect" if selected_option_id is not None else "pending",
            is_correct=is_correct,
            points_awarded=points_awarded,
            corrected_at=datetime.now(timezone.utc),
        )
        attempt.answers.append(answer)
        attempt.answered_count = len(attempt.answers)
        if is_correct:
            attempt.correct_answers += 1
        attempt.score = self.calculate_score(attempt)
        attempt.max_score = self.calculate_max_score(attempt)
        return answer

    def calculate_score(self, attempt: AssessmentAttempt) -> float:
        return float(sum(int(answer.points_awarded) for answer in attempt.answers))

    def calculate_max_score(self, attempt: AssessmentAttempt) -> float:
        return float(sum(int(answer.points_awarded) for answer in attempt.answers if answer.is_correct))

    @staticmethod
    def _compute_expires_at(
        *,
        publication: AssessmentPublication,
        started_at: datetime,
    ) -> datetime | None:
        deadline = None
        if publication.time_limit_seconds is not None:
            deadline = started_at + timedelta(seconds=publication.time_limit_seconds)
        if publication.ends_at is not None:
            if deadline is None or publication.ends_at < deadline:
                deadline = publication.ends_at
        return deadline
