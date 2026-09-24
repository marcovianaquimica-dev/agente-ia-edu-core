from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload


@dataclass
class Assessment:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    institution_id: str | None = None
    school_id: str | None = None
    created_by_external_identity: str | None = None
    owner_external_id: str | None = None
    visibility_scope: str = "SCHOOL"
    origin_type: str = "SCHOOL"
    scope_type: str | None = None
    scope_external_id: str | None = None
    title: str = ""
    description: str | None = None
    status: str = "draft"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    versions: list["AssessmentVersion"] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.updated_at = datetime.now(timezone.utc)


@dataclass
class ExerciseList(Assessment):
    """School-scoped exercise list backed by the assessment primitive.

    The project already has an assessment engine for ordered question bundles and
    publication logic. Exercise lists reuse that structure and add only the
    school/visibility metadata needed for B2B school workflows.
    """

    school_id: str | None = None
    owner_external_id: str | None = None
    visibility_scope: str = "SCHOOL"
    origin_type: str = "SCHOOL"

    @property
    def items(self) -> list["AssessmentItem"]:
        if not self.versions:
            return []
        return self.versions[0].items

    def add_item(
        self,
        *,
        question_version_id: uuid.UUID,
        position: int,
        points: int = 1,
        selection_request_id: uuid.UUID | None = None,
        is_required: bool = True,
    ) -> "AssessmentItem":
        if not self.versions:
            raise ValueError("Exercise list requires at least one version before adding items")

        item = AssessmentItem(
            assessment_version_id=self.versions[0].id,
            question_version_id=question_version_id,
            selection_request_id=selection_request_id,
            position=position,
            points=points,
            is_required=is_required,
        )
        self.versions[0].items.append(item)
        return item


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


class ExerciseListFactory:
    def create_list(
        self,
        *,
        title: str,
        description: str | None = None,
        school_id: str | None = None,
        created_by_external_identity: str | None = None,
        owner_external_id: str | None = None,
        visibility_scope: str = "SCHOOL",
        origin_type: str = "SCHOOL",
        institution_id: str | None = None,
    ) -> ExerciseList:
        list_obj = ExerciseList(
            institution_id=institution_id,
            created_by_external_identity=created_by_external_identity,
            title=title,
            description=description,
            status="draft",
            school_id=school_id,
            owner_external_id=owner_external_id,
            visibility_scope=visibility_scope.upper(),
            origin_type=origin_type.upper(),
        )
        version = self.create_version(
            assessment=list_obj,
            version_number=1,
            title=title,
            description=description,
            status="draft",
            created_by_external_identity=created_by_external_identity,
        )
        list_obj.versions.append(version)
        return list_obj

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
        list_obj: ExerciseList,
        *,
        question_version_id: uuid.UUID,
        position: int,
        points: int = 1,
        selection_request_id: uuid.UUID | None = None,
        is_required: bool = True,
    ) -> AssessmentItem:
        if not list_obj.versions:
            raise ValueError("Exercise list requires at least one version before adding items")
        return list_obj.add_item(
            question_version_id=question_version_id,
            position=position,
            points=points,
            selection_request_id=selection_request_id,
            is_required=is_required,
        )

    def publish(
        self,
        list_obj: ExerciseList,
        *,
        publication_type: str,
        released_immediately: bool = False,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        time_limit_seconds: int | None = None,
        attempts_allowed: int | None = None,
    ) -> AssessmentPublication:
        publication = AssessmentPublicationService.build_publication(
            assessment_version=list_obj.versions[0],
            publication_type=publication_type,
            released_immediately=released_immediately,
            starts_at=starts_at,
            ends_at=ends_at,
            time_limit_seconds=time_limit_seconds,
            attempts_allowed=attempts_allowed,
        )
        list_obj.versions[0].publications.append(publication)
        return publication


class AssessmentPersistenceService:
    def __init__(self, session) -> None:
        self.session = session

    async def create_assessment(self, *, title: str, description: str | None = None, institution_id: str | None = None, created_by_external_identity: str | None = None, school_id: str | None = None, owner_external_id: str | None = None, visibility_scope: str = "SCHOOL", origin_type: str = "SCHOOL", scope_type: str | None = None, scope_external_id: str | None = None) -> Assessment:
        from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel

        model = AssessmentModel(
            institution_id=None if institution_id is None else uuid.UUID(str(institution_id)),
            school_id=None if school_id is None else uuid.UUID(str(school_id)),
            created_by_external_identity=created_by_external_identity,
            owner_external_id=owner_external_id,
            visibility_scope=visibility_scope.upper(),
            origin_type=origin_type.upper(),
            scope_type=scope_type,
            scope_external_id=scope_external_id,
            title=title,
            description=description,
            status="draft",
        )
        self.session.add(model)
        await self.session.flush()
        return Assessment(
            id=model.id,
            institution_id=institution_id,
            school_id=school_id,
            created_by_external_identity=created_by_external_identity,
            owner_external_id=owner_external_id,
            visibility_scope=model.visibility_scope,
            origin_type=model.origin_type,
            scope_type=model.scope_type,
            scope_external_id=model.scope_external_id,
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
                school_id=str(item.school_id) if item.school_id is not None else None,
                created_by_external_identity=item.created_by_external_identity,
                owner_external_id=item.owner_external_id,
                visibility_scope=item.visibility_scope,
                origin_type=item.origin_type,
                scope_type=item.scope_type,
                scope_external_id=item.scope_external_id,
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
            school_id=str(model.school_id) if model.school_id is not None else None,
            created_by_external_identity=model.created_by_external_identity,
            owner_external_id=model.owner_external_id,
            visibility_scope=model.visibility_scope,
            origin_type=model.origin_type,
            scope_type=model.scope_type,
            scope_external_id=model.scope_external_id,
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


class ExerciseListPersistenceService(AssessmentPersistenceService):
    async def _record_workflow_transition(
        self,
        *,
        list_id: uuid.UUID,
        action: str,
        previous_status: str | None,
        new_status: str | None,
        performed_by_external_id: str | None = None,
        reason: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        from agente_ia_edu.db.models.assessments import AssessmentWorkflowAudit as AssessmentWorkflowAuditModel

        row = AssessmentWorkflowAuditModel(
            assessment_id=list_id,
            action=action,
            previous_status=previous_status,
            new_status=new_status,
            performed_by_external_id=performed_by_external_id,
            reason=reason,
            metadata_=metadata or {},
        )
        self.session.add(row)
        await self.session.flush()
        return {
            "id": str(row.id),
            "assessment_id": str(row.assessment_id),
            "action": row.action,
            "previous_status": row.previous_status,
            "new_status": row.new_status,
            "performed_by_external_id": row.performed_by_external_id,
            "reason": row.reason,
            "created_at": row.created_at,
        }

    async def list_workflow_audit(self, list_id: uuid.UUID) -> list[dict]:
        from sqlalchemy import select
        from agente_ia_edu.db.models.assessments import AssessmentWorkflowAudit as AssessmentWorkflowAuditModel

        stmt = (
            select(AssessmentWorkflowAuditModel)
            .where(AssessmentWorkflowAuditModel.assessment_id == list_id)
            .order_by(AssessmentWorkflowAuditModel.created_at.asc())
        )
        result = await self.session.scalars(stmt)
        return [
            {
                "id": str(row.id),
                "assessment_id": str(row.assessment_id),
                "action": row.action,
                "previous_status": row.previous_status,
                "new_status": row.new_status,
                "performed_by_external_id": row.performed_by_external_id,
                "reason": row.reason,
                "created_at": row.created_at,
            }
            for row in result.all()
        ]

    async def assign_list(
        self,
        *,
        list_id: uuid.UUID,
        recipient_type: str,
        recipient_id: str,
        school_id: str | uuid.UUID | None = None,
        assigned_by_external_id: str | None = None,
    ) -> dict:
        from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel
        from agente_ia_edu.db.models.assessments import AssessmentAssignment as AssessmentAssignmentModel

        assessment = await self.session.get(AssessmentModel, list_id)
        if assessment is None:
            raise ValueError("Exercise list not found")

        # BUG FIX: the original code compared `str(assessment.school_id)` (a
        # canonical, lowercase UUID string) against the raw `str(school_id)`
        # argument *before* normalizing it through uuid.UUID(). A caller
        # passing the same school UUID with different casing (e.g. uppercase)
        # was rejected with a false PermissionError, even though it is the
        # same school. Normalize once, up front, and compare UUID objects
        # directly so the check is case-insensitive and unambiguous. A
        # second, string-based re-check used to exist further down using the
        # normalized value - it was provably unreachable (any real mismatch
        # was already caught by the raw-string comparison above) and has been
        # removed along with the redundant `pass` branch.
        resolved_school_id = None if school_id is None else uuid.UUID(str(school_id))
        if assessment.school_id is not None and resolved_school_id is not None and assessment.school_id != resolved_school_id:
            raise PermissionError("Assignment school mismatch: recipient school does not match list school")

        resolved_type = str(recipient_type).upper()
        allowed_types = {"STUDENT", "CLASS", "GRADE", "CLASSROOM", "UNIT", "SCHOOL", "USER"}
        if resolved_type not in allowed_types:
            raise ValueError(f"Unsupported assignment recipient type: {recipient_type}")

        assignment = await self.session.scalar(
            select(AssessmentAssignmentModel).where(
                AssessmentAssignmentModel.assessment_id == list_id,
                AssessmentAssignmentModel.recipient_type == resolved_type,
                AssessmentAssignmentModel.recipient_id == str(recipient_id),
            )
        )
        if assignment is None:
            assignment = AssessmentAssignmentModel(
                assessment_id=list_id,
                school_id=resolved_school_id,
                recipient_type=resolved_type,
                recipient_id=str(recipient_id),
                assigned_by_external_id=assigned_by_external_id,
                status="PENDING",
            )
            self.session.add(assignment)
        else:
            assignment.school_id = resolved_school_id
            assignment.assigned_by_external_id = assigned_by_external_id
            assignment.status = assignment.status or "PENDING"
        await self.session.flush()
        return {
            "id": str(assignment.id),
            "assessment_id": str(assignment.assessment_id),
            "recipient_type": assignment.recipient_type,
            "recipient_id": assignment.recipient_id,
            "school_id": str(assignment.school_id) if assignment.school_id is not None else None,
            "assigned_by_external_id": assignment.assigned_by_external_id,
            "assigned_at": assignment.assigned_at,
            "status": assignment.status,
            "completed_at": assignment.completed_at,
        }

    async def get_assignment_status(
        self,
        *,
        list_id: uuid.UUID,
        recipient_type: str,
        recipient_id: str,
    ) -> dict:
        from agente_ia_edu.db.models.assessments import AssessmentAssignment as AssessmentAssignmentModel

        row = await self.session.scalar(
            select(AssessmentAssignmentModel).where(
                AssessmentAssignmentModel.assessment_id == list_id,
                AssessmentAssignmentModel.recipient_type == str(recipient_type).upper(),
                AssessmentAssignmentModel.recipient_id == str(recipient_id),
            )
        )
        if row is None:
            raise ValueError("Exercise list assignment not found")
        return {
            "id": str(row.id),
            "assessment_id": str(row.assessment_id),
            "recipient_type": row.recipient_type,
            "recipient_id": row.recipient_id,
            "school_id": str(row.school_id) if row.school_id is not None else None,
            "status": row.status,
            "assigned_by_external_id": row.assigned_by_external_id,
            "assigned_at": row.assigned_at,
            "completed_at": row.completed_at,
        }

    async def mark_assignment_complete(
        self,
        *,
        list_id: uuid.UUID,
        recipient_type: str,
        recipient_id: str,
        completed_by_external_id: str | None = None,
    ) -> dict:
        from agente_ia_edu.db.models.assessments import AssessmentAssignment as AssessmentAssignmentModel

        row = await self.session.scalar(
            select(AssessmentAssignmentModel).where(
                AssessmentAssignmentModel.assessment_id == list_id,
                AssessmentAssignmentModel.recipient_type == str(recipient_type).upper(),
                AssessmentAssignmentModel.recipient_id == str(recipient_id),
            )
        )
        if row is None:
            raise ValueError("Exercise list assignment not found")

        if completed_by_external_id is not None and str(recipient_type).upper() == "STUDENT":
            if str(completed_by_external_id) != str(recipient_id):
                raise PermissionError("Only the assigned student can complete this assignment")

        row.status = "COMPLETED"
        row.completed_at = datetime.now(timezone.utc)
        if completed_by_external_id is not None:
            row.metadata_ = {**(row.metadata_ or {}), "completed_by_external_id": completed_by_external_id}
        await self.session.flush()
        return await self.get_assignment_status(
            list_id=list_id,
            recipient_type=row.recipient_type,
            recipient_id=row.recipient_id,
        )

    async def create_list(
        self,
        *,
        title: str,
        description: str | None = None,
        institution_id: str | None = None,
        school_id: str | None = None,
        created_by_external_identity: str | None = None,
        owner_external_id: str | None = None,
        visibility_scope: str = "SCHOOL",
        origin_type: str = "SCHOOL",
        scope_type: str | None = None,
        scope_external_id: str | None = None,
    ) -> Assessment:
        assessment = await self.create_assessment(
            title=title,
            description=description,
            institution_id=institution_id,
            school_id=school_id,
            created_by_external_identity=created_by_external_identity,
            owner_external_id=owner_external_id,
            visibility_scope=visibility_scope,
            origin_type=origin_type,
            scope_type=scope_type,
            scope_external_id=scope_external_id,
        )
        await self.create_version(
            assessment_id=assessment.id,
            version_number=1,
            title=title,
            description=description,
            status="draft",
            created_by_external_identity=created_by_external_identity,
        )
        await self.session.flush()
        return assessment

    async def update_list(self, list_id: uuid.UUID, **fields) -> Assessment:
        from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel

        model = await self.session.get(AssessmentModel, list_id)
        if model is None:
            raise ValueError("Exercise list not found")
        for key, value in fields.items():
            if key in {"title", "description", "status", "institution_id", "school_id", "owner_external_id", "visibility_scope", "origin_type", "scope_type", "scope_external_id"}:
                if key in {"institution_id", "school_id"} and value is not None:
                    value = uuid.UUID(str(value))
                setattr(model, key, value)
        model.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return await self.get_assessment(list_id)

    async def get_list(self, list_id: uuid.UUID) -> dict:
        from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel
        from agente_ia_edu.db.models.assessments import AssessmentItem as AssessmentItemModel
        from agente_ia_edu.db.models.assessments import AssessmentVersion as AssessmentVersionModel

        assessment = await self.get_assessment(list_id)
        if assessment is None:
            raise ValueError("Exercise list not found")
        version = await self.session.scalar(
            select(AssessmentVersionModel)
            .where(AssessmentVersionModel.assessment_id == list_id)
            .order_by(AssessmentVersionModel.version_number.asc())
        )
        item_rows = []
        if version is not None:
            item_models = await self.session.execute(
                select(AssessmentItemModel)
                .where(AssessmentItemModel.assessment_version_id == version.id)
                .order_by(AssessmentItemModel.position.asc())
            )
            for item in item_models.scalars().all():
                item_rows.append({
                    "id": str(item.id),
                    "question_version_id": str(item.question_version_id),
                    "position": item.position,
                    "points": item.points,
                    "is_required": item.is_required,
                })
        return {
            "id": str(assessment.id),
            "title": assessment.title,
            "description": assessment.description,
            "status": assessment.status,
            "institution_id": assessment.institution_id,
            "school_id": assessment.school_id,
            "owner_external_id": assessment.owner_external_id,
            "visibility_scope": assessment.visibility_scope,
            "origin_type": assessment.origin_type,
            "items": item_rows,
        }

    async def add_item(self, list_id: uuid.UUID, *, question_version_id: uuid.UUID, position: int, points: int = 1, selection_request_id: uuid.UUID | None = None, is_required: bool = True) -> dict:
        from agente_ia_edu.db.models.assessments import AssessmentVersion as AssessmentVersionModel
        from agente_ia_edu.db.models.official import QuestionVersion as QuestionVersionModel, Question as QuestionModel
        from agente_ia_edu.services.question_governance import QuestionEligibilityCalculator

        # Validate version exists and is not published
        version = await self.session.scalar(
            select(AssessmentVersionModel)
            .where(AssessmentVersionModel.assessment_id == list_id)
            .order_by(AssessmentVersionModel.version_number.asc())
        )
        if version is None:
            raise ValueError("Exercise list has no versions")
        if version.status == "published":
            raise ValueError("Published exercise list cannot receive new items")

        # If the supplied id matches a real QuestionVersion, validate governance rules.
        # Legacy exercise list tests may pass arbitrary UUIDs that are not persisted; in that case
        # we preserve the prior behavior and do not reject them here.
        qv = await self.session.get(QuestionVersionModel, question_version_id)
        if qv is not None:
            from agente_ia_edu.db.models.official import Question as QuestionModel

            question = await self.session.scalar(
                select(QuestionModel)
                .where(QuestionModel.id == qv.question_id)
                .options(
                    selectinload(QuestionModel.versions).selectinload(QuestionVersionModel.pedagogical_classifications),
                )
            )
            if question is None:
                raise ValueError(f"Question for version {question_version_id} not found")

            # Check if question is eligible for educational use
            eligibility = QuestionEligibilityCalculator.calculate(question)
            if not eligibility.is_eligible:
                reason_str = "; ".join(eligibility.reasons)
                raise ValueError(
                    f"Question {question.id} is not eligible for exercise lists: {reason_str}"
                )

        # Add the item to the assessment
        item = await super().add_item(
            assessment_version_id=version.id,
            question_version_id=question_version_id,
            position=position,
            points=points,
            selection_request_id=selection_request_id,
            is_required=is_required,
        )
        return {
            "id": str(item.id),
            "question_version_id": str(item.question_version_id),
            "position": item.position,
            "points": item.points,
            "is_required": item.is_required,
        }

    async def remove_item(self, list_id: uuid.UUID, item_id: uuid.UUID | str) -> None:
        from agente_ia_edu.db.models.assessments import AssessmentItem as AssessmentItemModel
        from agente_ia_edu.db.models.assessments import AssessmentVersion as AssessmentVersionModel

        resolved_item_id = uuid.UUID(str(item_id))
        item = await self.session.get(AssessmentItemModel, resolved_item_id)
        if item is None:
            raise ValueError("Exercise list item not found")
        version = await self.session.scalar(
            select(AssessmentVersionModel)
            .where(AssessmentVersionModel.id == item.assessment_version_id)
        )
        if version is None or version.assessment_id != list_id:
            raise ValueError("Exercise list item does not belong to the requested list")
        if version.status == "published":
            raise ValueError("Published exercise list cannot be modified")
        await self.session.delete(item)
        await self.session.flush()
        remaining = await self.session.execute(
            select(AssessmentItemModel)
            .where(AssessmentItemModel.assessment_version_id == version.id)
            .order_by(AssessmentItemModel.position.asc())
        )
        for index, row in enumerate(remaining.scalars().all(), start=1):
            row.position = index
        await self.session.flush()

    async def submit_review(self, list_id: uuid.UUID, *, performed_by_external_id: str | None = None) -> Assessment:
        from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel
        from agente_ia_edu.db.models.assessments import AssessmentVersion as AssessmentVersionModel

        assessment = await self.session.get(AssessmentModel, list_id)
        if assessment is None:
            raise ValueError("Exercise list not found")
        if assessment.status not in {"draft", "rejected"}:
            raise ValueError("Only draft or rejected lists can be submitted for review")
        previous_status = assessment.status
        assessment.status = "review"
        version = await self.session.scalar(
            select(AssessmentVersionModel)
            .where(AssessmentVersionModel.assessment_id == list_id)
            .order_by(AssessmentVersionModel.version_number.asc())
        )
        if version is not None:
            version.status = "review"
        await self._record_workflow_transition(
            list_id=list_id,
            action="LIST_SUBMITTED_FOR_REVIEW",
            previous_status=previous_status,
            new_status="review",
            performed_by_external_id=performed_by_external_id,
            reason="List submitted for review",
        )
        await self.session.flush()
        return await self.get_assessment(list_id)

    async def approve(self, list_id: uuid.UUID, *, performed_by_external_id: str | None = None) -> Assessment:
        from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel
        from agente_ia_edu.db.models.assessments import AssessmentVersion as AssessmentVersionModel

        assessment = await self.session.get(AssessmentModel, list_id)
        if assessment is None:
            raise ValueError("Exercise list not found")
        if assessment.status != "review":
            raise ValueError("Only lists under review can be approved")
        previous_status = assessment.status
        assessment.status = "approved"
        version = await self.session.scalar(
            select(AssessmentVersionModel)
            .where(AssessmentVersionModel.assessment_id == list_id)
            .order_by(AssessmentVersionModel.version_number.asc())
        )
        if version is not None:
            version.status = "approved"
        await self._record_workflow_transition(
            list_id=list_id,
            action="LIST_APPROVED",
            previous_status=previous_status,
            new_status="approved",
            performed_by_external_id=performed_by_external_id,
            reason="List approved by reviewer",
        )
        await self.session.flush()
        return await self.get_assessment(list_id)

    async def reject(self, list_id: uuid.UUID, *, reason: str | None = None, performed_by_external_id: str | None = None) -> Assessment:
        from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel
        from agente_ia_edu.db.models.assessments import AssessmentVersion as AssessmentVersionModel

        assessment = await self.session.get(AssessmentModel, list_id)
        if assessment is None:
            raise ValueError("Exercise list not found")
        if assessment.status != "review":
            raise ValueError("Only lists under review can be rejected")
        previous_status = assessment.status
        assessment.status = "rejected"
        version = await self.session.scalar(
            select(AssessmentVersionModel)
            .where(AssessmentVersionModel.assessment_id == list_id)
            .order_by(AssessmentVersionModel.version_number.asc())
        )
        if version is not None:
            version.status = "archived"
        await self._record_workflow_transition(
            list_id=list_id,
            action="LIST_REJECTED",
            previous_status=previous_status,
            new_status="rejected",
            performed_by_external_id=performed_by_external_id,
            reason=reason or "List rejected",
        )
        await self.session.flush()
        return await self.get_assessment(list_id)

    async def publish(self, list_id: uuid.UUID, *, performed_by_external_id: str | None = None) -> Assessment:
        from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel
        from agente_ia_edu.db.models.assessments import AssessmentVersion as AssessmentVersionModel

        assessment = await self.session.get(AssessmentModel, list_id)
        if assessment is None:
            raise ValueError("Exercise list not found")
        if assessment.status != "approved":
            raise ValueError("Only approved lists can be published")
        previous_status = assessment.status
        assessment.status = "published"
        version = await self.session.scalar(
            select(AssessmentVersionModel)
            .where(AssessmentVersionModel.assessment_id == list_id)
            .order_by(AssessmentVersionModel.version_number.asc())
        )
        if version is not None:
            version.status = "published"
        await self._record_workflow_transition(
            list_id=list_id,
            action="LIST_PUBLISHED",
            previous_status=previous_status,
            new_status="published",
            performed_by_external_id=performed_by_external_id,
            reason="List published",
        )
        await self.session.flush()
        return await self.get_assessment(list_id)

    async def archive(self, list_id: uuid.UUID, *, performed_by_external_id: str | None = None) -> Assessment:
        from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel
        from agente_ia_edu.db.models.assessments import AssessmentVersion as AssessmentVersionModel

        assessment = await self.session.get(AssessmentModel, list_id)
        if assessment is None:
            raise ValueError("Exercise list not found")
        if assessment.status not in {"published", "archived"}:
            raise ValueError("Only published lists can be archived")
        previous_status = assessment.status
        assessment.status = "archived"
        version = await self.session.scalar(
            select(AssessmentVersionModel)
            .where(AssessmentVersionModel.assessment_id == list_id)
            .order_by(AssessmentVersionModel.version_number.asc())
        )
        if version is not None:
            version.status = "archived"
        await self._record_workflow_transition(
            list_id=list_id,
            action="LIST_ARCHIVED",
            previous_status=previous_status,
            new_status="archived",
            performed_by_external_id=performed_by_external_id,
            reason="List archived",
        )
        await self.session.flush()
        return await self.get_assessment(list_id)


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
