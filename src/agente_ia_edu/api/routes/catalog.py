"""
API routes for the Pedagogical Catalog (Phase 2 foundation).

Generic endpoints for disciplines/content tree, educational resources,
content<->resource associations, and authored theory materials. No
recommendation or mastery logic lives here - see Learning Path for that.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_current_identity, get_session_factory
from ..schemas.catalog import (
    CatalogNodeCreateRequest,
    CatalogNodeResponse,
    CatalogNodeTreeResponse,
    ContentResourceLinkCreateRequest,
    ContentResourceLinkResponse,
    ContentResourcesResponse,
    EducationalResourceCreateRequest,
    EducationalResourceResponse,
    TheoryMaterialCreateRequest,
    TheoryMaterialDetailResponse,
    TheoryMaterialResponse,
    TheoryMaterialVersionCreateRequest,
    TheoryMaterialVersionResponse,
)
from ...identity import ExternalIdentityContext
from ...repositories.catalog import (
    CatalogNodeRepository,
    EducationalResourceRepository,
    TheoryMaterialRepository,
)
from ...services.catalog import (
    CatalogNodeService,
    ContentCatalogQueryService,
    ContentResourceLinkService,
    EducationalResourceService,
    TheoryMaterialService,
)

catalog_router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])

_ALL_RESOURCE_TYPES = [
    "THEORY_MATERIAL", "BOOK", "PDF", "VIDEO", "QUESTION_SET", "EXTERNAL_RESOURCE", "OTHER",
]


def _node_to_response(node) -> CatalogNodeResponse:
    return CatalogNodeResponse(
        id=node.id,
        parent_id=node.parent_id,
        root_id=node.root_id,
        node_type=node.node_type,
        code=node.code,
        name=node.name,
        description=node.description,
        position=node.position,
        active=node.active,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


def _resource_to_response(resource) -> EducationalResourceResponse:
    return EducationalResourceResponse(
        id=resource.id,
        title=resource.title,
        description=resource.description,
        resource_type=resource.resource_type,
        author=resource.author,
        origin_type=resource.origin_type,
        owner_external_id=resource.owner_external_id,
        license_reference=resource.license_reference,
        source_url=resource.source_url,
        storage_uri=resource.storage_uri,
        status=resource.status,
        visibility_scope=resource.visibility_scope,
        created_by_external_identity=resource.created_by_external_identity,
        created_at=resource.created_at,
        updated_at=resource.updated_at,
    )


def _link_to_response(link) -> ContentResourceLinkResponse:
    return ContentResourceLinkResponse(
        id=link.id,
        content_node_id=link.content_node_id,
        resource_id=link.resource_id,
        pedagogical_role=link.pedagogical_role,
        relevance=float(link.relevance) if link.relevance is not None else None,
        priority=link.priority,
        position=link.position,
        recommended_level=link.recommended_level,
        created_at=link.created_at,
    )


def _material_to_response(material) -> TheoryMaterialResponse:
    return TheoryMaterialResponse(
        id=material.id,
        title=material.title,
        primary_content_node_id=material.primary_content_node_id,
        created_by_external_identity=material.created_by_external_identity,
        created_at=material.created_at,
        updated_at=material.updated_at,
    )


def _version_to_response(version) -> TheoryMaterialVersionResponse:
    return TheoryMaterialVersionResponse(
        id=version.id,
        material_id=version.material_id,
        version_number=version.version_number,
        status=version.status,
        resource_id=version.resource_id,
        introduction=version.introduction,
        summary=version.summary,
        created_at=version.created_at,
        updated_at=version.updated_at,
        published_at=version.published_at,
    )


# ============================================================================
# Disciplines / Content Tree
# ============================================================================


@catalog_router.post(
    "/disciplines",
    status_code=201,
    response_model=CatalogNodeResponse,
    summary="Create a discipline (root content node)",
)
async def create_discipline(
    request: CatalogNodeCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> CatalogNodeResponse:
    """Create a discipline. A discipline is always a root (no parent_id)."""
    async with session_factory() as session:
        service = CatalogNodeService()
        node = await service.create_node(
            session,
            name=request.name,
            node_type=request.node_type,
            parent_id=None,
            code=request.code,
            description=request.description,
            position=request.position,
            metadata=request.metadata,
        )
        await session.commit()
        return _node_to_response(node)


@catalog_router.get(
    "/disciplines",
    response_model=list[CatalogNodeResponse],
    summary="List all disciplines (root content nodes)",
)
async def list_disciplines(
    session_factory=Depends(get_session_factory),
) -> list[CatalogNodeResponse]:
    async with session_factory() as session:
        repo = CatalogNodeRepository(session)
        roots = await repo.list_roots()
        return [_node_to_response(node) for node in roots]


@catalog_router.post(
    "/nodes",
    status_code=201,
    response_model=CatalogNodeResponse,
    summary="Create a content node (learning area, unit, content or subcontent)",
)
async def create_content_node(
    request: CatalogNodeCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> CatalogNodeResponse:
    """Create a non-root node. Requires an existing parent_id."""
    if request.parent_id is None:
        raise HTTPException(status_code=400, detail="parent_id is required for a content node")

    async with session_factory() as session:
        service = CatalogNodeService()
        try:
            node = await service.create_node(
                session,
                name=request.name,
                node_type=request.node_type,
                parent_id=request.parent_id,
                code=request.code,
                description=request.description,
                position=request.position,
                metadata=request.metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await session.commit()
        return _node_to_response(node)


@catalog_router.get(
    "/nodes/{root_id}/tree",
    response_model=CatalogNodeTreeResponse,
    summary="List the full content tree under a discipline",
)
async def get_content_tree(
    root_id: UUID,
    session_factory=Depends(get_session_factory),
) -> CatalogNodeTreeResponse:
    async with session_factory() as session:
        repo = CatalogNodeRepository(session)
        root = await repo.get_by_id(root_id)
        if root is None:
            raise HTTPException(status_code=404, detail="Root content node not found")

        nodes = await repo.list_by_root(root_id)
        return CatalogNodeTreeResponse(
            root=_node_to_response(root),
            nodes=[_node_to_response(node) for node in nodes],
        )


# ============================================================================
# Educational Resources
# ============================================================================


@catalog_router.post(
    "/resources",
    status_code=201,
    response_model=EducationalResourceResponse,
    summary="Create a generic educational resource",
)
async def create_resource(
    request: EducationalResourceCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EducationalResourceResponse:
    async with session_factory() as session:
        service = EducationalResourceService()
        resource = await service.create_resource(
            session,
            title=request.title,
            resource_type=request.resource_type,
            origin_type=request.origin_type,
            description=request.description,
            author=request.author,
            owner_external_id=request.owner_external_id,
            license_reference=request.license_reference,
            source_url=request.source_url,
            storage_uri=request.storage_uri,
            status=request.status,
            visibility_scope=request.visibility_scope,
            created_by_external_identity=identity.external_user_id,
            metadata=request.metadata,
        )
        await session.commit()
        return _resource_to_response(resource)


@catalog_router.get(
    "/resources",
    response_model=list[EducationalResourceResponse],
    summary="List educational resources, optionally filtered by type",
)
async def list_resources(
    resource_type: str | None = None,
    session_factory=Depends(get_session_factory),
) -> list[EducationalResourceResponse]:
    async with session_factory() as session:
        repo = EducationalResourceRepository(session)
        if resource_type:
            resources = await repo.list_by_type(resource_type)
        else:
            resources = []
            for a_type in _ALL_RESOURCE_TYPES:
                resources.extend(await repo.list_by_type(a_type))
        return [_resource_to_response(r) for r in resources]


# ============================================================================
# Content <-> Resource Association
# ============================================================================


@catalog_router.post(
    "/content-resource-links",
    status_code=201,
    response_model=ContentResourceLinkResponse,
    summary="Associate a resource with a content node",
)
async def create_content_resource_link(
    request: ContentResourceLinkCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> ContentResourceLinkResponse:
    async with session_factory() as session:
        service = ContentResourceLinkService()
        try:
            link = await service.link(
                session,
                content_node_id=request.content_node_id,
                resource_id=request.resource_id,
                pedagogical_role=request.pedagogical_role,
                relevance=request.relevance,
                priority=request.priority,
                position=request.position,
                recommended_level=request.recommended_level,
                metadata=request.metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return _link_to_response(link)


@catalog_router.get(
    "/nodes/{content_node_id}/resources",
    response_model=ContentResourcesResponse,
    summary="List resources associated with a content node",
)
async def get_resources_for_content(
    content_node_id: UUID,
    session_factory=Depends(get_session_factory),
) -> ContentResourcesResponse:
    async with session_factory() as session:
        query_service = ContentCatalogQueryService()
        links = await query_service.get_resources_for_content(session, content_node_id)
        return ContentResourcesResponse(
            content_node_id=content_node_id,
            links=[_link_to_response(link) for link in links],
        )


# ============================================================================
# Theory Materials
# ============================================================================


@catalog_router.post(
    "/materials",
    status_code=201,
    response_model=TheoryMaterialResponse,
    summary="Create a new authored theory material",
)
async def create_material(
    request: TheoryMaterialCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TheoryMaterialResponse:
    async with session_factory() as session:
        service = TheoryMaterialService()
        material = await service.create_material(
            session,
            title=request.title,
            created_by_external_identity=identity.external_user_id,
            primary_content_node_id=request.primary_content_node_id,
        )
        await session.commit()
        return _material_to_response(material)


@catalog_router.post(
    "/materials/{material_id}/versions",
    status_code=201,
    response_model=TheoryMaterialVersionResponse,
    summary="Create a new draft version of a material",
)
async def create_material_version(
    material_id: UUID,
    request: TheoryMaterialVersionCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TheoryMaterialVersionResponse:
    async with session_factory() as session:
        repo = TheoryMaterialRepository(session)
        material = await repo.get_by_id(material_id)
        if material is None:
            raise HTTPException(status_code=404, detail="Material not found")

        service = TheoryMaterialService()
        version = await service.create_version(
            session,
            material_id=material_id,
            created_by_external_identity=identity.external_user_id,
            introduction=request.introduction,
            summary=request.summary,
        )
        await session.commit()
        return _version_to_response(version)


@catalog_router.get(
    "/materials/{material_id}",
    response_model=TheoryMaterialDetailResponse,
    summary="Get a material with all of its versions",
)
async def get_material(
    material_id: UUID,
    session_factory=Depends(get_session_factory),
) -> TheoryMaterialDetailResponse:
    async with session_factory() as session:
        repo = TheoryMaterialRepository(session)
        material = await repo.get_by_id(material_id)
        if material is None:
            raise HTTPException(status_code=404, detail="Material not found")

        versions = await repo.list_versions(material_id)
        return TheoryMaterialDetailResponse(
            material=_material_to_response(material),
            versions=[_version_to_response(v) for v in versions],
        )
