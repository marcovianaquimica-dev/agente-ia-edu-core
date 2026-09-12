"""PHASE 15 - persistence + retrieval for professor-authored question lists.

REUSES the existing Assessment domain tables - ``assessments`` /
``assessment_versions`` / ``assessment_items`` - rather than creating a parallel
schema (they already carry exactly the shape PHASE 14 pointed at:
``AssessmentItem(question_version_id, position, frozen_correct_option_id,
answer_key_revision_id)`` with UNIQUE ``(version, position)`` and UNIQUE
``(version, question_version_id)``). NO migration is added.

A stored list is:  Assessment(material_type='EXERCISE_LIST')
                     -> AssessmentVersion(version_number=1)
                        -> AssessmentItem[]  (ordered, one per question_version)

The PHASE 14 configuration (activity mode, answer-key presentation, resolution
style, selection_fingerprint, the exact question_version_id order) is stored in
``Assessment.metadata_``. ``question_version_id`` is the canonical identity -
statements/options are never copied as a new source of truth. The official answer
key is snapshotted per item (``frozen_correct_option_id`` + ``answer_key_revision_id``)
from the authoritative source so a finalized list stays reproducible.

Lifecycle:  draft  -> editable / deletable
            published -> immutable (finalize)

AI-agnostic: imports no provider, no OpenAI SDK. Validation and retrieval delegate
to :class:`ListGeneratorService` / :class:`QuestionBankService`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
)
from agente_ia_edu.db.models.assessments import (
    Assessment,
    AssessmentItem,
    AssessmentVersion,
)
from agente_ia_edu.services.list_generator import (
    GeneratedListDefinition,
    ListConfiguration,
    ListGenerationError,
    ListGeneratorService,
)

LIST_MATERIAL_TYPE = "EXERCISE_LIST"
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
_PRIVILEGED_ROLES = {"COORDINATION", "COORDINATOR", "COORDENACAO", "DIRECTOR",
                     "DIRETOR", "PRINCIPAL", "ADMIN", "MANAGER", "GESTOR"}


class ListAuthorizationError(PermissionError):
    """Maps to HTTP 403."""


class ListNotFoundError(LookupError):
    """Maps to HTTP 404."""


class ListStateError(ValueError):
    """Edit/delete on a published list -> HTTP 409."""


@dataclass(frozen=True)
class Requester:
    external_user_id: str
    school_id: str | None
    role: str
    is_platform_admin: bool = False

    @property
    def is_privileged(self) -> bool:
        return self.is_platform_admin or (self.role or "").upper() in _PRIVILEGED_ROLES


@dataclass(frozen=True)
class StoredListSummary:
    id: str
    title: str
    instructions: str | None
    status: str
    activity_mode: str
    answer_key_presentation: str
    resolution_style: str | None
    question_count: int
    selection_fingerprint: str
    owner_external_id: str | None
    school_id: str | None
    created_at: str
    updated_at: str
    finalized_at: str | None


class QuestionListStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._generator = ListGeneratorService(session)

    # -- helpers -------------------------------------------------------

    async def _load(self, list_id: UUID) -> tuple[Assessment, AssessmentVersion]:
        result = await self._session.execute(
            select(Assessment)
            .where(Assessment.id == list_id)
            .options(selectinload(Assessment.versions).selectinload(AssessmentVersion.items))
        )
        assessment = result.unique().scalar_one_or_none()
        if assessment is None or assessment.material_type != LIST_MATERIAL_TYPE:
            raise ListNotFoundError(str(list_id))
        version = assessment.versions[0] if assessment.versions else None
        if version is None:
            raise ListNotFoundError(str(list_id))
        return assessment, version

    @staticmethod
    def _can_view(assessment: Assessment, who: Requester) -> bool:
        if assessment.owner_external_id == who.external_user_id:
            return True
        if who.is_platform_admin:
            return True
        if who.is_privileged and assessment.school_id and who.school_id \
                and str(assessment.school_id) == str(who.school_id):
            return True
        return False

    @staticmethod
    def _can_manage(assessment: Assessment, who: Requester) -> bool:
        return assessment.owner_external_id == who.external_user_id or who.is_platform_admin

    def _summary(self, assessment: Assessment, version: AssessmentVersion) -> StoredListSummary:
        md = assessment.metadata_ or {}
        return StoredListSummary(
            id=str(assessment.id),
            title=assessment.title,
            instructions=assessment.description,
            status=version.status,
            activity_mode=md.get("activity_mode", "EXERCISE_LIST"),
            answer_key_presentation=md.get("answer_key_presentation", "NONE"),
            resolution_style=md.get("resolution_style"),
            question_count=len(version.items),
            selection_fingerprint=md.get("selection_fingerprint", ""),
            owner_external_id=assessment.owner_external_id,
            school_id=str(assessment.school_id) if assessment.school_id else None,
            created_at=assessment.created_at.isoformat() if assessment.created_at else "",
            updated_at=assessment.updated_at.isoformat() if assessment.updated_at else "",
            finalized_at=version.published_at.isoformat() if version.published_at else None,
        )

    # -- create -----------------------------------------------------

    async def create(
        self,
        *,
        configuration: ListConfiguration,
        question_version_ids: Sequence[UUID],
        requester: Requester,
        institution_id: str | None = None,
    ) -> StoredListSummary:
        # full PHASE 14 validation: existence, official_original, no dup, order, config,
        # gabarito from the official source.
        definition = await self._generator.generate(
            question_version_ids, configuration, source_label="phase15-persist"
        )
        assessment = Assessment(
            institution_id=UUID(str(institution_id)) if institution_id else None,
            school_id=UUID(str(requester.school_id)) if requester.school_id else None,
            created_by_external_identity=requester.external_user_id,
            owner_external_id=requester.external_user_id,
            visibility_scope="SCHOOL" if requester.school_id else "PRIVATE",
            origin_type="TEACHER",
            material_type=LIST_MATERIAL_TYPE,
            title=definition.configuration.title,
            description=definition.configuration.instructions,
            status=STATUS_DRAFT,
            metadata_={
                "activity_mode": definition.configuration.activity_mode,
                "answer_key_presentation": definition.configuration.answer_key_presentation,
                "resolution_style": definition.configuration.resolution_style,
                "selection_fingerprint": definition.selection_fingerprint,
                "question_version_ids": definition.question_version_ids,
                "phase": "15",
            },
        )
        self._session.add(assessment)
        await self._session.flush()
        version = AssessmentVersion(
            assessment_id=assessment.id, version_number=1, title=assessment.title,
            description=assessment.description, status=STATUS_DRAFT,
            created_by_external_identity=requester.external_user_id,
        )
        self._session.add(version)
        await self._session.flush()
        await self._add_items(version.id, definition)
        list_id = assessment.id
        await self._session.commit()
        self._session.expire_all()
        loaded, ver = await self._load(list_id)
        return self._summary(loaded, ver)

    async def _add_items(self, version_id: UUID, definition: GeneratedListDefinition) -> None:
        # authoritative official answer key snapshot, keyed by question_version_id
        keys = await self._official_keys([UUID(v) for v in definition.question_version_ids])
        for position, vid in enumerate(definition.question_version_ids, start=1):
            snap = keys.get(UUID(vid), {})
            self._session.add(AssessmentItem(
                assessment_version_id=version_id,
                question_version_id=UUID(vid),
                position=position,
                points=1,
                is_required=True,
                frozen_correct_option_id=snap.get("option_id"),
                answer_key_revision_id=snap.get("revision_id"),
            ))
        await self._session.flush()

    async def _official_keys(self, version_ids: Sequence[UUID]) -> dict[UUID, dict]:
        ids = list(version_ids)
        if not ids:
            return {}
        q = (
            select(BookletQuestion.question_version_id, AnswerKeyEntry.resolved_option_id,
                   AnswerKeyEntry.answer_key_revision_id, AnswerKeyRevision.revision_number)
            .join(AnswerKeyEntry, AnswerKeyEntry.booklet_question_id == BookletQuestion.id)
            .join(AnswerKeyRevision, AnswerKeyRevision.id == AnswerKeyEntry.answer_key_revision_id)
            .where(BookletQuestion.question_version_id.in_(ids),
                   AnswerKeyRevision.is_official.is_(True))
            .order_by(AnswerKeyRevision.revision_number.desc())
        )
        out: dict[UUID, dict] = {}
        for vid, option_id, revision_id, _rev in (await self._session.execute(q)).all():
            out.setdefault(vid, {"option_id": option_id, "revision_id": revision_id})
        return out

    # -- read -----------------------------------------------------

    async def list_for_scope(
        self, *, requester: Requester, status: str | None = None,
        query: str | None = None, order_by: str = "created_at", order_direction: str = "desc",
        page: int = 1, page_size: int = 20,
    ) -> tuple[list[StoredListSummary], int]:
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        base = (
            select(Assessment)
            .where(Assessment.material_type == LIST_MATERIAL_TYPE)
            .options(selectinload(Assessment.versions).selectinload(AssessmentVersion.items))
        )
        if requester.is_privileged and requester.school_id:
            base = base.where(
                (Assessment.owner_external_id == requester.external_user_id)
                | (Assessment.school_id == UUID(str(requester.school_id)))
            )
        else:
            base = base.where(Assessment.owner_external_id == requester.external_user_id)
        if query:
            base = base.where(Assessment.title.ilike(f"%{query}%"))

        rows = list((await self._session.scalars(base)).unique().all())
        if status:
            want = status.lower()
            rows = [a for a in rows if (a.versions[0].status if a.versions else "") == want]
        col = {"created_at": lambda a: a.created_at, "updated_at": lambda a: a.updated_at,
               "title": lambda a: (a.title or "").lower()}.get(order_by, lambda a: a.created_at)
        rows.sort(key=col, reverse=(order_direction.lower() != "asc"))
        total = len(rows)
        window = rows[(page - 1) * page_size: (page - 1) * page_size + page_size]
        return [self._summary(a, a.versions[0]) for a in window], total

    async def get_definition(self, list_id: UUID, *, requester: Requester) -> dict:
        assessment, version = await self._load(list_id)
        if not self._can_view(assessment, requester):
            raise ListAuthorizationError("not authorised to view this list")
        md = assessment.metadata_ or {}
        ordered_ids = [it.question_version_id
                       for it in sorted(version.items, key=lambda x: x.position)]
        config = ListConfiguration(
            title=assessment.title,
            instructions=assessment.description,
            activity_mode=md.get("activity_mode", "EXERCISE_LIST"),
            answer_key_presentation=md.get("answer_key_presentation", "NONE"),
            resolution_style=md.get("resolution_style"),
        )
        # rebuild the exact GeneratedListDefinition from the stored references (batched)
        definition = await self._generator.generate(
            ordered_ids, config, source_label="phase15-retrieve"
        )
        payload = self._definition_dict(definition)
        payload["persisted"] = {
            "list_id": str(assessment.id),
            "status": version.status,
            "editable": version.status == STATUS_DRAFT,
            "owner_external_id": assessment.owner_external_id,
            "school_id": str(assessment.school_id) if assessment.school_id else None,
            "created_at": assessment.created_at.isoformat() if assessment.created_at else None,
            "updated_at": assessment.updated_at.isoformat() if assessment.updated_at else None,
            "finalized_at": version.published_at.isoformat() if version.published_at else None,
            "stored_fingerprint": md.get("selection_fingerprint"),
            "fingerprint_matches": md.get("selection_fingerprint") == definition.selection_fingerprint,
        }
        return payload

    @staticmethod
    def _definition_dict(definition: GeneratedListDefinition) -> dict:
        from dataclasses import asdict
        return asdict(definition)

    # -- update / delete / finalize ----------------------------------

    async def update_draft(
        self, list_id: UUID, *, requester: Requester,
        configuration: ListConfiguration | None = None,
        question_version_ids: Sequence[UUID] | None = None,
    ) -> StoredListSummary:
        assessment, version = await self._load(list_id)
        if not self._can_manage(assessment, requester):
            raise ListAuthorizationError("only the owner can edit this list")
        if version.status != STATUS_DRAFT:
            raise ListStateError("a finalized list is immutable")

        md = dict(assessment.metadata_ or {})
        new_config = configuration or ListConfiguration(
            title=assessment.title, instructions=assessment.description,
            activity_mode=md.get("activity_mode", "EXERCISE_LIST"),
            answer_key_presentation=md.get("answer_key_presentation", "NONE"),
            resolution_style=md.get("resolution_style"),
        )
        new_ids = (list(question_version_ids) if question_version_ids is not None
                   else [it.question_version_id for it in sorted(version.items, key=lambda x: x.position)])
        definition = await self._generator.generate(new_ids, new_config, source_label="phase15-update")

        assessment.title = definition.configuration.title
        assessment.description = definition.configuration.instructions
        version.title = definition.configuration.title
        version.description = definition.configuration.instructions
        md.update(
            activity_mode=definition.configuration.activity_mode,
            answer_key_presentation=definition.configuration.answer_key_presentation,
            resolution_style=definition.configuration.resolution_style,
            selection_fingerprint=definition.selection_fingerprint,
            question_version_ids=definition.question_version_ids,
        )
        assessment.metadata_ = md
        for it in list(version.items):
            await self._session.delete(it)
        await self._session.flush()
        await self._add_items(version.id, definition)
        assessment.updated_at = datetime.now(timezone.utc)
        await self._session.commit()
        self._session.expire_all()
        loaded, ver = await self._load(list_id)
        return self._summary(loaded, ver)

    async def delete(self, list_id: UUID, *, requester: Requester) -> None:
        assessment, version = await self._load(list_id)
        if not self._can_manage(assessment, requester):
            raise ListAuthorizationError("only the owner can delete this list")
        if version.status != STATUS_DRAFT:
            raise ListStateError("a finalized list cannot be deleted")
        for it in list(version.items):
            await self._session.delete(it)
        await self._session.delete(version)
        await self._session.delete(assessment)
        await self._session.commit()

    async def finalize(self, list_id: UUID, *, requester: Requester) -> StoredListSummary:
        assessment, version = await self._load(list_id)
        if not self._can_manage(assessment, requester):
            raise ListAuthorizationError("only the owner can finalize this list")
        if version.status == STATUS_PUBLISHED:
            return self._summary(assessment, version)  # idempotent
        if not version.items:
            raise ListStateError("cannot finalize an empty list")
        version.status = STATUS_PUBLISHED
        version.published_at = datetime.now(timezone.utc)
        assessment.status = STATUS_PUBLISHED
        assessment.updated_at = datetime.now(timezone.utc)
        await self._session.commit()
        self._session.expire_all()
        loaded, ver = await self._load(list_id)
        return self._summary(loaded, ver)


__all__ = [
    "LIST_MATERIAL_TYPE",
    "ListAuthorizationError",
    "ListNotFoundError",
    "ListStateError",
    "QuestionListStore",
    "Requester",
    "StoredListSummary",
]
