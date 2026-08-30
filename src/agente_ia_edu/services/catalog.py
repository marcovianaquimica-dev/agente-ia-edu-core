"""
Domain services for the Pedagogical Catalog.

Keeps catalog responsibilities narrow: "what resources/content exist?" -
no mastery calculation, no recommendation logic (those live in the
Learning Path / mastery engine and stay untouched here).
"""

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
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
    exercises, and publication.

    A published version is immutable: sections/exercises can no longer be
    added or changed once published_at is set.
    """

    def __init__(self, repository: Optional[TheoryMaterialRepository] = None):
        self._repository_override = repository

    def _repository(self, session: AsyncSession) -> TheoryMaterialRepository:
        return self._repository_override or TheoryMaterialRepository(session)

    async def create_material(
        self,
        session: AsyncSession,
        *,
        title: str,
        created_by_external_identity: Optional[str] = None,
        primary_content_node_id: Optional[UUID] = None,
    ) -> TheoryMaterial:
        material = TheoryMaterial(
            title=title,
            created_by_external_identity=created_by_external_identity,
            primary_content_node_id=primary_content_node_id,
        )
        session.add(material)
        await session.flush()
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
            status="draft",
            introduction=introduction,
            summary=summary,
            created_by_external_identity=created_by_external_identity,
        )
        session.add(version)
        await session.flush()
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
        version = await session.get(TheoryMaterialVersion, material_version_id)
        if version is None:
            raise ValueError("Material version does not exist")
        if version.status == "published":
            raise ValueError("Cannot modify a published material version")

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
        version = await session.get(TheoryMaterialVersion, material_version_id)
        if version is None:
            raise ValueError("Material version does not exist")
        if version.status == "published":
            raise ValueError("Cannot modify a published material version")
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
    ) -> TheoryMaterialVersion:
        """
        Publish a version: materializes it as an EducationalResource
        (resource_type=THEORY_MATERIAL) and freezes it (immutable).
        """
        version = await session.get(TheoryMaterialVersion, material_version_id)
        if version is None:
            raise ValueError("Material version does not exist")
        if version.status == "published":
            raise ValueError("Material version is already published")

        material = await session.get(TheoryMaterial, version.material_id)

        resource = EducationalResource(
            title=material.title,
            resource_type="THEORY_MATERIAL",
            origin_type="AUTHOR",
            owner_external_id=owner_external_id or version.created_by_external_identity,
            visibility_scope=visibility_scope,
            status="active",
            created_by_external_identity=version.created_by_external_identity,
        )
        session.add(resource)
        await session.flush()

        version.resource_id = resource.id
        version.status = "published"
        version.published_at = datetime.now(timezone.utc)
        await session.flush()
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

