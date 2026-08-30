"""
Repositories for the Pedagogical Catalog domain.

Query and persistence layer for the content taxonomy tree, educational
resources, resource<->content/question links, and authored materials.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    CatalogNode,
    ContentQuestionLink,
    ContentResourceLink,
    EducationalResource,
    MaterialExercise,
    MaterialSection,
    ResourceAccessGrant,
    ResourceQuestionLink,
    TheoryMaterial,
    TheoryMaterialVersion,
)


class CatalogNodeRepository:
    """Repository for the generic content taxonomy tree."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, node_id: UUID) -> Optional[CatalogNode]:
        return await self.session.get(CatalogNode, node_id)

    async def list_roots(self) -> list[CatalogNode]:
        """List top-level nodes (disciplines): parent_id IS NULL."""
        result = await self.session.execute(
            select(CatalogNode)
            .where(CatalogNode.parent_id.is_(None))
            .order_by(CatalogNode.position, CatalogNode.name)
        )
        return list(result.scalars().all())

    async def list_children(self, parent_id: UUID) -> list[CatalogNode]:
        result = await self.session.execute(
            select(CatalogNode)
            .where(CatalogNode.parent_id == parent_id)
            .order_by(CatalogNode.position, CatalogNode.name)
        )
        return list(result.scalars().all())

    async def list_by_root(self, root_id: UUID) -> list[CatalogNode]:
        """List every node under a given discipline (root), including itself."""
        result = await self.session.execute(
            select(CatalogNode)
            .where(CatalogNode.root_id == root_id)
            .order_by(CatalogNode.position, CatalogNode.name)
        )
        return list(result.scalars().all())


class EducationalResourceRepository:
    """Repository for generic educational resources."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, resource_id: UUID) -> Optional[EducationalResource]:
        return await self.session.get(EducationalResource, resource_id)

    async def list_by_type(self, resource_type: str) -> list[EducationalResource]:
        result = await self.session.execute(
            select(EducationalResource)
            .where(EducationalResource.resource_type == resource_type)
            .order_by(EducationalResource.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_by_owner(self, owner_external_id: str) -> list[EducationalResource]:
        result = await self.session.execute(
            select(EducationalResource)
            .where(EducationalResource.owner_external_id == owner_external_id)
            .order_by(EducationalResource.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_access_grants(self, resource_id: UUID) -> list[ResourceAccessGrant]:
        result = await self.session.execute(
            select(ResourceAccessGrant).where(ResourceAccessGrant.resource_id == resource_id)
        )
        return list(result.scalars().all())


class ContentResourceLinkRepository:
    """Repository for the Content <-> EducationalResource many-to-many link."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_content(self, content_node_id: UUID) -> list[ContentResourceLink]:
        result = await self.session.execute(
            select(ContentResourceLink)
            .where(ContentResourceLink.content_node_id == content_node_id)
            .order_by(ContentResourceLink.priority, ContentResourceLink.position)
        )
        return list(result.scalars().all())

    async def list_by_resource(self, resource_id: UUID) -> list[ContentResourceLink]:
        result = await self.session.execute(
            select(ContentResourceLink).where(ContentResourceLink.resource_id == resource_id)
        )
        return list(result.scalars().all())

    async def exists(
        self, content_node_id: UUID, resource_id: UUID, pedagogical_role: str
    ) -> bool:
        result = await self.session.execute(
            select(ContentResourceLink).where(
                ContentResourceLink.content_node_id == content_node_id,
                ContentResourceLink.resource_id == resource_id,
                ContentResourceLink.pedagogical_role == pedagogical_role,
            )
        )
        return result.scalar_one_or_none() is not None


class ContentQuestionLinkRepository:
    """Repository for the direct Content <-> QuestionVersion link."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_content(self, content_node_id: UUID) -> list[ContentQuestionLink]:
        result = await self.session.execute(
            select(ContentQuestionLink).where(
                ContentQuestionLink.content_node_id == content_node_id
            )
        )
        return list(result.scalars().all())

    async def exists(self, content_node_id: UUID, question_version_id: UUID) -> bool:
        result = await self.session.execute(
            select(ContentQuestionLink).where(
                ContentQuestionLink.content_node_id == content_node_id,
                ContentQuestionLink.question_version_id == question_version_id,
            )
        )
        return result.scalar_one_or_none() is not None


class ResourceQuestionLinkRepository:
    """Repository for QUESTION_SET resource <-> QuestionVersion ordering."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_resource(self, resource_id: UUID) -> list[ResourceQuestionLink]:
        result = await self.session.execute(
            select(ResourceQuestionLink)
            .where(ResourceQuestionLink.resource_id == resource_id)
            .order_by(ResourceQuestionLink.position)
        )
        return list(result.scalars().all())


class TheoryMaterialRepository:
    """Repository for authored theory materials and their versions."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, material_id: UUID) -> Optional[TheoryMaterial]:
        return await self.session.get(TheoryMaterial, material_id)

    async def get_version(self, version_id: UUID) -> Optional[TheoryMaterialVersion]:
        return await self.session.get(TheoryMaterialVersion, version_id)

    async def list_versions(self, material_id: UUID) -> list[TheoryMaterialVersion]:
        result = await self.session.execute(
            select(TheoryMaterialVersion)
            .where(TheoryMaterialVersion.material_id == material_id)
            .order_by(TheoryMaterialVersion.version_number)
        )
        return list(result.scalars().all())

    async def get_latest_version(self, material_id: UUID) -> Optional[TheoryMaterialVersion]:
        result = await self.session.execute(
            select(TheoryMaterialVersion)
            .where(TheoryMaterialVersion.material_id == material_id)
            .order_by(TheoryMaterialVersion.version_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_sections(self, material_version_id: UUID) -> list[MaterialSection]:
        result = await self.session.execute(
            select(MaterialSection)
            .where(MaterialSection.material_version_id == material_version_id)
            .order_by(MaterialSection.position)
        )
        return list(result.scalars().all())

    async def list_exercises(self, material_version_id: UUID) -> list[MaterialExercise]:
        result = await self.session.execute(
            select(MaterialExercise)
            .where(MaterialExercise.material_version_id == material_version_id)
            .order_by(MaterialExercise.position)
        )
        return list(result.scalars().all())
