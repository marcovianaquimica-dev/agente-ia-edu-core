"""
Domain services for the Pedagogical Catalog.

Keeps catalog responsibilities narrow: "what resources/content exist?" -
no mastery calculation, no recommendation logic (those live in the
Learning Path / mastery engine and stay untouched here).
"""

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    AdminAuditLog,
    CatalogNode,
    ContentQuestionLink,
    ContentResourceLink,
    EducationalResource,
    MaterialExercise,
    MaterialSection,
    TheoryMaterial,
    TheoryMaterialVersion,
)
from ..repositories.catalog import (
    CatalogNodeRepository,
    ContentQuestionLinkRepository,
    ContentResourceLinkRepository,
    TheoryMaterialRepository,
)


class CatalogNodeService:
    """Creates and organizes the generic content taxonomy tree."""

    def __init__(self, repository: Optional[CatalogNodeRepository] = None):
        self._repository_override = repository

    def _repository(self, session: AsyncSession) -> CatalogNodeRepository:
        return self._repository_override or CatalogNodeRepository(session)

    async def create_node(
        self,
        session: AsyncSession,
        *,
        name: str,
        node_type: str,
        parent_id: Optional[UUID] = None,
        code: Optional[str] = None,
        description: Optional[str] = None,
        position: int = 0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> CatalogNode:
        """
        Create a node. If parent_id is given, root_id is inherited from the
        parent's root_id (or the parent's own id, if the parent IS a root).
        A node with no parent is itself a root (root_id = its own id).
        """
        root_id = None
        if parent_id is not None:
            parent = await session.get(CatalogNode, parent_id)
            if parent is None:
                raise ValueError("Parent catalog node does not exist")
            root_id = parent.root_id or parent.id

        node = CatalogNode(
            parent_id=parent_id,
            root_id=root_id,
            node_type=node_type,
            code=code,
            name=name,
            description=description,
            position=position,
            metadata_=metadata,
        )
        session.add(node)
        await session.flush()

        if parent_id is None:
            # Root node: points to itself for efficient discipline-scoped queries.
            node.root_id = node.id
            await session.flush()

        return node


class EducationalResourceService:
    """Creates generic educational resources with origin/ownership traceability."""

    async def create_resource(
        self,
        session: AsyncSession,
        *,
        title: str,
        resource_type: str,
        origin_type: str,
        description: Optional[str] = None,
        author: Optional[str] = None,
        owner_external_id: Optional[str] = None,
        license_reference: Optional[str] = None,
        source_url: Optional[str] = None,
        storage_uri: Optional[str] = None,
        status: str = "draft",
        visibility_scope: str = "PRIVATE",
        created_by_external_identity: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> EducationalResource:
        resource = EducationalResource(
            title=title,
            description=description,
            resource_type=resource_type,
            author=author,
            origin_type=origin_type,
            owner_external_id=owner_external_id,
            license_reference=license_reference,
            source_url=source_url,
            storage_uri=storage_uri,
            status=status,
            visibility_scope=visibility_scope,
            created_by_external_identity=created_by_external_identity,
            metadata_=metadata,
        )
        session.add(resource)
        await session.flush()
        return resource


class ContentResourceLinkService:
    """Links resources to content nodes (many-to-many)."""

    def __init__(self, repository: Optional[ContentResourceLinkRepository] = None):
        self._repository_override = repository

    def _repository(self, session: AsyncSession) -> ContentResourceLinkRepository:
        return self._repository_override or ContentResourceLinkRepository(session)

    async def link(
        self,
        session: AsyncSession,
        *,
        content_node_id: UUID,
        resource_id: UUID,
        pedagogical_role: str,
        relevance: Optional[float] = None,
        priority: Optional[int] = None,
        position: Optional[int] = None,
        recommended_level: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ContentResourceLink:
        repo = self._repository(session)
        if await repo.exists(content_node_id, resource_id, pedagogical_role):
            raise ValueError(
                "This resource is already linked to this content with the same role"
            )

        link = ContentResourceLink(
            content_node_id=content_node_id,
            resource_id=resource_id,
            pedagogical_role=pedagogical_role,
            relevance=relevance,
            priority=priority,
            position=position,
            recommended_level=recommended_level,
            metadata_=metadata,
        )
        session.add(link)
        await session.flush()
        return link


class ContentQuestionLinkService:
    """Links EXISTING questions to content nodes, never duplicating them."""

    def __init__(self, repository: Optional[ContentQuestionLinkRepository] = None):
        self._repository_override = repository

    def _repository(self, session: AsyncSession) -> ContentQuestionLinkRepository:
        return self._repository_override or ContentQuestionLinkRepository(session)

    async def link(
        self,
        session: AsyncSession,
        *,
        content_node_id: UUID,
        question_version_id: UUID,
    ) -> ContentQuestionLink:
        repo = self._repository(session)
        if await repo.exists(content_node_id, question_version_id):
            raise ValueError("This question is already linked to this content")

        link = ContentQuestionLink(
            content_node_id=content_node_id,
            question_version_id=question_version_id,
        )
        session.add(link)
        await session.flush()
        return link


class TheoryMaterialService:
    """
    Manages authored theory materials: creation, versioning, sections,
    exercises, review, approval, rejection, publication and archiving.
    """

    _ALLOWED_EDIT_STATUSES = {"DRAFT", "REJECTED"}
    _REVIEWABLE_STATUSES = {"DRAFT", "REJECTED"}
    _APPROVABLE_STATUSES = {"PENDING_REVIEW"}
    _PUBLISHABLE_STATUSES = {"APPROVED"}

    def __init__(self, repository: Optional[TheoryMaterialRepository] = None):
        self._repository_override = repository

    def _repository(self, session: AsyncSession) -> TheoryMaterialRepository:
        return self._repository_override or TheoryMaterialRepository(session)

    @staticmethod
    def _normalize_status(value: Optional[str]) -> str:
        return (value or "").upper()

    async def _get_version(self, session: AsyncSession, material_version_id: UUID) -> TheoryMaterialVersion:
        version = await session.get(TheoryMaterialVersion, material_version_id)
        if version is None:
            raise ValueError("Material version does not exist")
        return version

    async def _log_material_event(
        self,
        session: AsyncSession,
        *,
        material_id: UUID,
        actor_external_id: Optional[str],
        version_id: Optional[UUID] = None,
        from_status: Optional[str] = None,
        to_status: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        school_id: Optional[UUID] = None,
        event_type: str = "MATERIAL_EVENT",
    ) -> AdminAuditLog:
        audit = AdminAuditLog(
            performed_by_external_id=actor_external_id or "system",
            action=event_type,
            entity_type="THEORY_MATERIAL",
            entity_id=str(material_id),
            school_id=school_id,
            metadata_={
                **(metadata or {}),
                "material_id": str(material_id),
                **({"version_id": str(version_id)} if version_id is not None else {}),
                **({"from_status": from_status} if from_status is not None else {}),
                **({"to_status": to_status} if to_status is not None else {}),
            },
        )
        session.add(audit)
        await session.flush()
        return audit

    async def create_material(
        self,
        session: AsyncSession,
        *,
        title: str,
        created_by_external_identity: Optional[str] = None,
        primary_content_node_id: Optional[UUID] = None,
        school_id: Optional[UUID] = None,
    ) -> TheoryMaterial:
        material = TheoryMaterial(
            title=title,
            created_by_external_identity=created_by_external_identity,
            primary_content_node_id=primary_content_node_id,
            school_id=school_id,
        )
        session.add(material)
        await session.flush()

        version = await self.create_version(
            session,
            material_id=material.id,
            created_by_external_identity=created_by_external_identity,
            introduction=None,
            summary=None,
        )
        await self._log_material_event(
            session,
            material_id=material.id,
            actor_external_id=created_by_external_identity,
            version_id=version.id,
            to_status="DRAFT",
            school_id=school_id,
            event_type="MATERIAL_CREATED",
            metadata={"version_number": version.version_number, "status": "DRAFT"},
        )
        return material

    async def create_version(
        self,
        session: AsyncSession,
        *,
        material_id: UUID,
        created_by_external_identity: Optional[str] = None,
        introduction: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> TheoryMaterialVersion:
        repo = self._repository(session)
        latest = await repo.get_latest_version(material_id)
        next_number = (latest.version_number + 1) if latest else 1

        version = TheoryMaterialVersion(
            material_id=material_id,
            version_number=next_number,
            status="DRAFT",
            introduction=introduction,
            summary=summary,
            created_by_external_identity=created_by_external_identity,
        )
        session.add(version)
        await session.flush()

        material = await session.get(TheoryMaterial, material_id)
        if material is not None:
            await self._log_material_event(
                session,
                material_id=material_id,
                school_id=material.school_id,
                event_type="MATERIAL_VERSION_CREATED",
                actor_external_id=created_by_external_identity,
                version_id=version.id,
                from_status=None,
                to_status="DRAFT",
                metadata={
                    "version_number": version.version_number,
                    "previous_version_number": latest.version_number if latest else None,
                },
            )
        return version

    async def submit_for_review(
        self,
        session: AsyncSession,
        *,
        material_version_id: UUID,
    ) -> TheoryMaterialVersion:
        version = await self._get_version(session, material_version_id)
        status = self._normalize_status(version.status)
        if status not in self._REVIEWABLE_STATUSES:
            raise ValueError("Only draft or rejected materials can be submitted for review.")
        previous_status = version.status
        version.status = "PENDING_REVIEW"
        await session.flush()

        material = await session.get(TheoryMaterial, version.material_id)
        if material is not None:
            await self._log_material_event(
                session,
                material_id=material.id,
                school_id=material.school_id,
                event_type="MATERIAL_SUBMITTED_FOR_REVIEW",
                actor_external_id=version.created_by_external_identity,
                version_id=version.id,
                from_status=previous_status,
                to_status="PENDING_REVIEW",
                metadata={"version_number": version.version_number},
            )
        return version

    async def approve_version(
        self,
        session: AsyncSession,
        *,
        material_version_id: UUID,
    ) -> TheoryMaterialVersion:
        version = await self._get_version(session, material_version_id)
        status = self._normalize_status(version.status)
        if status not in self._APPROVABLE_STATUSES:
            raise ValueError("Only materials under review can be approved.")
        previous_status = version.status
        version.status = "APPROVED"
        await session.flush()

        material = await session.get(TheoryMaterial, version.material_id)
        if material is not None:
            await self._log_material_event(
                session,
                material_id=material.id,
                school_id=material.school_id,
                event_type="MATERIAL_APPROVED",
                actor_external_id=version.created_by_external_identity,
                version_id=version.id,
                from_status=previous_status,
                to_status="APPROVED",
                metadata={"version_number": version.version_number},
            )
        return version

    async def reject_version(
        self,
        session: AsyncSession,
        *,
        material_version_id: UUID,
        reason: Optional[str] = None,
    ) -> TheoryMaterialVersion:
        version = await self._get_version(session, material_version_id)
        status = self._normalize_status(version.status)
        if status not in self._APPROVABLE_STATUSES:
            raise ValueError("Only materials under review can be rejected.")

        previous_status = version.status
        version.status = "REJECTED"
        metadata = dict(version.metadata_ or {})
        if reason:
            metadata["rejection_reason"] = reason
            metadata["rejected_at"] = datetime.now(timezone.utc).isoformat()
        elif "rejection_reason" in metadata:
            metadata.pop("rejection_reason", None)
            metadata.pop("rejected_at", None)
        version.metadata_ = metadata or None
        await session.flush()

        material = await session.get(TheoryMaterial, version.material_id)
        if material is not None:
            await self._log_material_event(
                session,
                material_id=material.id,
                school_id=material.school_id,
                event_type="MATERIAL_REJECTED",
                actor_external_id=version.created_by_external_identity,
                version_id=version.id,
                from_status=previous_status,
                to_status="REJECTED",
                metadata={
                    "version_number": version.version_number,
                    "rejection_reason": reason,
                },
            )
        return version

    async def add_section(
        self,
        session: AsyncSession,
        *,
        material_version_id: UUID,
        section_type: str,
        position: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> MaterialSection:
        version = await self._get_version(session, material_version_id)
        if self._normalize_status(version.status) not in self._ALLOWED_EDIT_STATUSES:
            raise ValueError("Only draft or rejected material versions can be edited.")

        section = MaterialSection(
            material_version_id=material_version_id,
            section_type=section_type,
            position=position,
            title=title,
            body=body,
            metadata_=metadata,
        )
        session.add(section)
        await session.flush()
        return section

    async def add_exercise(
        self,
        session: AsyncSession,
        *,
        material_version_id: UUID,
        source_type: str,
        position: int,
        section_id: Optional[UUID] = None,
        question_version_id: Optional[UUID] = None,
        authored_text: Optional[str] = None,
        is_required: bool = True,
        points: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> MaterialExercise:
        version = await self._get_version(session, material_version_id)
        if self._normalize_status(version.status) not in self._ALLOWED_EDIT_STATUSES:
            raise ValueError("Only draft or rejected material versions can be edited.")
        if source_type == "EXISTING_QUESTION" and question_version_id is None:
            raise ValueError("question_version_id is required for EXISTING_QUESTION exercises")

        exercise = MaterialExercise(
            material_version_id=material_version_id,
            section_id=section_id,
            source_type=source_type,
            question_version_id=question_version_id,
            authored_text=authored_text,
            position=position,
            is_required=is_required,
            points=points,
            metadata_=metadata,
        )
        session.add(exercise)
        await session.flush()
        return exercise

    async def publish_version(
        self,
        session: AsyncSession,
        *,
        material_version_id: UUID,
        owner_external_id: Optional[str] = None,
        visibility_scope: str = "PRIVATE",
        origin_type: Optional[str] = None,
    ) -> TheoryMaterialVersion:
        """Publish an approved material as a generic resource.

        Reuses the existing EducationalResource representation instead of creating a
        separate publication abstraction. The same version is idempotent: repeated
        publication calls return the same published resource and do not create a
        duplicate resource record.
        """
        version = await self._get_version(session, material_version_id)
        status = self._normalize_status(version.status)
        if status == "PUBLISHED":
            if version.resource_id is not None:
                return version
            raise ValueError("Material version is already published")
        if status not in self._PUBLISHABLE_STATUSES:
            raise ValueError("Only approved materials can be published.")

        material = await session.get(TheoryMaterial, version.material_id)
        if material is None:
            raise ValueError("Material does not exist")

        effective_visibility = (visibility_scope or "PRIVATE").upper()
        resource_owner = owner_external_id or version.created_by_external_identity
        if material.school_id is not None:
            resource_owner = str(material.school_id)
            effective_origin = (origin_type or "SCHOOL").upper()
            if effective_visibility == "PRIVATE":
                effective_visibility = "SCHOOL"
        else:
            effective_origin = (origin_type or ("AUTHOR" if resource_owner else "PLATFORM")).upper()
            if effective_origin == "PLATFORM":
                effective_visibility = "PUBLIC"

        existing_resource = None
        if version.resource_id is not None:
            existing_resource = await session.get(EducationalResource, version.resource_id)

        if existing_resource is None:
            existing_resource = await session.execute(
                select(EducationalResource)
                .where(
                    EducationalResource.resource_type == "THEORY_MATERIAL",
                    EducationalResource.title == material.title,
                    EducationalResource.owner_external_id == resource_owner,
                    EducationalResource.origin_type == effective_origin,
                )
                .order_by(EducationalResource.created_at.desc())
                .limit(1)
            )
            existing_resource = existing_resource.scalar_one_or_none()

        if existing_resource is not None:
            version.resource_id = existing_resource.id
            version.status = "PUBLISHED"
            version.published_at = datetime.now(timezone.utc)
            if existing_resource.metadata_ is None:
                existing_resource.metadata_ = {}
            existing_resource.metadata_["material_id"] = str(material.id)
            existing_resource.metadata_["material_version_id"] = str(version.id)
            existing_resource.visibility_scope = effective_visibility
            existing_resource.origin_type = effective_origin
            existing_resource.owner_external_id = resource_owner
            existing_resource.created_by_external_identity = version.created_by_external_identity
            existing_resource.status = "active"
            await session.flush()
            await self._log_material_event(
                session,
                material_id=material.id,
                school_id=material.school_id,
                event_type="MATERIAL_PUBLISHED",
                actor_external_id=version.created_by_external_identity,
                version_id=version.id,
                from_status="APPROVED",
                to_status="PUBLISHED",
                metadata={
                    "version_number": version.version_number,
                    "resource_id": str(existing_resource.id),
                    "origin_type": effective_origin,
                    "visibility_scope": effective_visibility,
                },
            )
            return version

        resource = EducationalResource(
            title=material.title,
            description=version.summary or version.introduction,
            resource_type="THEORY_MATERIAL",
            origin_type=effective_origin,
            owner_external_id=resource_owner,
            visibility_scope=effective_visibility,
            status="active",
            created_by_external_identity=version.created_by_external_identity,
            metadata_={
                "material_id": str(material.id),
                "material_version_id": str(version.id),
                "school_id": str(material.school_id) if material.school_id is not None else None,
            },
        )
        session.add(resource)
        await session.flush()

        version.resource_id = resource.id
        version.status = "PUBLISHED"
        version.published_at = datetime.now(timezone.utc)
        await session.flush()
        await self._log_material_event(
            session,
            material_id=material.id,
            school_id=material.school_id,
            event_type="MATERIAL_PUBLISHED",
            actor_external_id=version.created_by_external_identity,
            version_id=version.id,
            from_status="APPROVED",
            to_status="PUBLISHED",
            metadata={
                "version_number": version.version_number,
                "resource_id": str(resource.id),
                "origin_type": effective_origin,
                "visibility_scope": effective_visibility,
            },
        )
        return version

    async def archive_version(
        self,
        session: AsyncSession,
        *,
        material_version_id: UUID,
    ) -> TheoryMaterialVersion:
        version = await self._get_version(session, material_version_id)
        status = self._normalize_status(version.status)
        if status == "ARCHIVED":
            raise ValueError("Material version is already archived")
        if status not in {"DRAFT", "PENDING_REVIEW", "APPROVED", "REJECTED", "PUBLISHED"}:
            raise ValueError("This material version cannot be archived in its current state.")
        previous_status = version.status
        version.status = "ARCHIVED"
        await session.flush()

        material = await session.get(TheoryMaterial, version.material_id)
        if material is not None:
            await self._log_material_event(
                session,
                material_id=material.id,
                school_id=material.school_id,
                event_type="MATERIAL_ARCHIVED",
                actor_external_id=version.created_by_external_identity,
                version_id=version.id,
                from_status=previous_status,
                to_status="ARCHIVED",
                metadata={"version_number": version.version_number},
            )
        return version


class ContentCatalogQueryService:
    """
    Answers "what exists for this content?" (section 12) - resources and
    linked questions. Does NOT rank, recommend, or evaluate mastery; that
    is the Learning Path / mastery engine's responsibility.
    """

    def __init__(
        self,
        resource_link_repository: Optional[ContentResourceLinkRepository] = None,
        question_link_repository: Optional[ContentQuestionLinkRepository] = None,
    ):
        self._resource_link_repository = resource_link_repository
        self._question_link_repository = question_link_repository

    async def get_resources_for_content(
        self, session: AsyncSession, content_node_id: UUID
    ) -> list[ContentResourceLink]:
        repo = self._resource_link_repository or ContentResourceLinkRepository(session)
        return await repo.list_by_content(content_node_id)

    async def get_questions_for_content(
        self, session: AsyncSession, content_node_id: UUID
    ) -> list[ContentQuestionLink]:
        repo = self._question_link_repository or ContentQuestionLinkRepository(session)
        return await repo.list_by_content(content_node_id)

