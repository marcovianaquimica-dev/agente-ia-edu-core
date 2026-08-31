"""
API routes for the Pedagogical Catalog (Phase 2 foundation).

Generic endpoints for disciplines/content tree, educational resources,
content<->resource associations, and authored theory materials. No
recommendation or mastery logic lives here - see Learning Path for that.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sqlalchemy import asc, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..dependencies import get_current_identity, get_session_factory
from ..schemas.admin import AdminAuditLogResponse
from ..schemas.catalog import (
    CatalogNodeCreateRequest,
    CatalogNodeResponse,
    CatalogNodeTreeResponse,
    ContentResourceLinkCreateRequest,
    ContentResourceLinkResponse,
    ContentResourcesResponse,
    EducationalResourceCreateRequest,
    EducationalResourceResponse,
    MaterialReviewRequest,
    TheoryMaterialCreateRequest,
    TheoryMaterialDetailResponse,
    TheoryMaterialResponse,
    TheoryMaterialVersionCreateRequest,
    TheoryMaterialVersionResponse,
)
from ...identity import ExternalIdentityContext
from ...db.models import (
    AdminAuditLog,
    EducationalResource,
    TheoryMaterial as TheoryMaterialModel,
    TheoryMaterial,
)
from ...repositories.catalog import (
    CatalogNodeRepository,
    EducationalResourceRepository,
    TheoryMaterialRepository,
)
from ...services.authorization import AuthorizationService
from ...services.catalog import (
    CatalogNodeService,
    ContentCatalogQueryService,
    ContentResourceLinkService,
    EducationalResourceService,
    TheoryMaterialService,
)
from ...services.knowledge import KnowledgeService

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


async def _require_material_access(
    identity: ExternalIdentityContext,
    session: AsyncSession,
    material: TheoryMaterialModel,
    *,
    require_edit_role: bool = True,
) -> None:
    authz = AuthorizationService(session)
    context = await authz.resolve_context(identity, school_id=material.school_id)

    role_check = await authz.require_role(
        context,
        "TEACHER",
        "COORDINATOR",
        "DIRECTOR",
        "PLATFORM_ADMIN",
    )
    if not role_check.allowed:
        raise HTTPException(
            status_code=403,
            detail="Material access requires a teacher, coordinator, director, or platform admin role.",
        )

    if material.school_id is not None:
        school_check = await authz.require_school_access(context, material.school_id)
        if not school_check.allowed:
            raise HTTPException(
                status_code=403,
                detail="User does not have access to this material's school context.",
            )

    if require_edit_role and material.school_id is None and material.created_by_external_identity is not None:
        if identity.external_user_id != material.created_by_external_identity:
            raise HTTPException(
                status_code=403,
                detail="Only the material creator can manage an unscoped material.",
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
    summary="List only visible educational resources for the authenticated user",
)
async def list_resources(
    q: str | None = None,
    resource_type: str | None = None,
    visibility_scope: str | None = None,
    origin_type: str | None = None,
    owner_external_id: str | None = None,
    content_node_id: UUID | None = None,
    school_id: UUID | None = None,
    sort: str = Query(default="created_at", pattern="^(title|created_at|updated_at|status)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EducationalResourceResponse]:
    async with session_factory() as session:
        authz = AuthorizationService(session)
        context = await authz.resolve_context(identity)
        school_context_id = str(context.school_id) if context.school_id is not None else None

        if school_id is not None and school_context_id is not None:
            if str(school_id) != school_context_id:
                return []
        elif school_id is not None and school_context_id is None:
            return []

        sort_map = {
            "title": EducationalResource.title,
            "created_at": EducationalResource.created_at,
            "updated_at": EducationalResource.updated_at,
            "status": EducationalResource.status,
        }
        sort_column = sort_map.get(sort)
        if sort_column is None:
            raise HTTPException(status_code=400, detail="Invalid sort field.")

        order_func = desc if order.lower() == "desc" else asc
        stmt = (
            select(EducationalResource)
            .options(selectinload(EducationalResource.access_grants))
            .where(EducationalResource.status == "active")
        )

        if content_node_id is not None:
            stmt = stmt.where(
                EducationalResource.id.in_(
                    select(ContentResourceLink.resource_id).where(ContentResourceLink.content_node_id == content_node_id)
                )
            )

        result = await session.execute(stmt.order_by(order_func(sort_column)).offset(offset).limit(limit))
        resources = list(result.scalars().all())

        visible: list[EducationalResource] = []
        for resource in resources:
            if q:
                needle = q.strip().lower()
                haystack = " ".join(
                    [
                        resource.title or "",
                        resource.description or "",
                        resource.author or "",
                        resource.origin_type or "",
                        resource.visibility_scope or "",
                    ]
                ).lower()
                if needle not in haystack:
                    continue

            if resource_type and resource.resource_type.upper() != resource_type.upper():
                continue
            if visibility_scope and resource.visibility_scope.upper() != visibility_scope.upper():
                continue
            if origin_type and resource.origin_type.upper() != origin_type.upper():
                continue
            if owner_external_id and resource.owner_external_id != owner_external_id:
                continue
            if school_id and resource.owner_external_id != str(school_id):
                continue
            if not KnowledgeService._is_resource_visible(resource, school_context_id):
                continue
            visible.append(resource)

        return [_resource_to_response(r) for r in visible]


@catalog_router.get(
    "/resources/{resource_id}",
    response_model=EducationalResourceResponse,
    summary="Get a single visible educational resource by id",
)
async def get_resource_detail(
    resource_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EducationalResourceResponse:
    async with session_factory() as session:
        authz = AuthorizationService(session)
        context = await authz.resolve_context(identity)
        resource = (
            await session.execute(
                select(EducationalResource)
                .options(selectinload(EducationalResource.access_grants))
                .where(EducationalResource.id == resource_id)
            )
        ).scalar_one_or_none()
        if resource is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        if resource.status != "active":
            raise HTTPException(status_code=403, detail="Resource is not available for consumption")
        school_context_id = str(context.school_id) if context.school_id is not None else None
        if not KnowledgeService._is_resource_visible(resource, school_context_id):
            raise HTTPException(status_code=403, detail="Resource is not visible to this user")
        return _resource_to_response(resource)


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


@catalog_router.get(
    "/materials",
    response_model=list[TheoryMaterialResponse],
    summary="List authored theory materials with minimal filtering",
)
async def list_materials(
    status: str | None = Query(default=None),
    author_id: str | None = Query(default=None),
    material_id: UUID | None = Query(default=None),
    version_id: UUID | None = Query(default=None),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[TheoryMaterialResponse]:
    async with session_factory() as session:
        authz = AuthorizationService(session)
        context = await authz.resolve_context(identity)
        role_check = await authz.require_role(
            context,
            "TEACHER",
            "COORDINATOR",
            "DIRECTOR",
            "PLATFORM_ADMIN",
        )
        if not role_check.allowed:
            raise HTTPException(
                status_code=403,
                detail="Material listing requires a teacher, coordinator, director, or platform admin role.",
            )

        result = await session.execute(select(TheoryMaterial).order_by(TheoryMaterial.created_at.desc()))
        materials = list(result.scalars().all())
        repo = TheoryMaterialRepository(session)

        filtered: list[TheoryMaterial] = []
        for material in materials:
            if material_id is not None and material.id != material_id:
                continue
            if author_id is not None and (material.created_by_external_identity or "") != author_id:
                continue
            if version_id is not None:
                version = await repo.get_version(version_id)
                if version is None or version.material_id != material.id:
                    continue
            if status is not None:
                versions = await repo.list_versions(material.id)
                if not any(v.status.upper() == status.upper() for v in versions):
                    continue
            if material.school_id is not None:
                if context.school_id is None or str(context.school_id) != str(material.school_id):
                    continue
            elif material.created_by_external_identity is not None and identity.external_user_id != material.created_by_external_identity:
                continue
            filtered.append(material)

        return [_material_to_response(material) for material in filtered]


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
        authz = AuthorizationService(session)
        context = await authz.resolve_context(identity)
        role_check = await authz.require_role(context, "TEACHER", "COORDINATOR", "DIRECTOR", "PLATFORM_ADMIN")
        if not role_check.allowed:
            raise HTTPException(
                status_code=403,
                detail="Material workflow actions require a teacher, coordinator, director, or platform admin role.",
            )
        service = TheoryMaterialService()
        material = await service.create_material(
            session,
            title=request.title,
            created_by_external_identity=identity.external_user_id,
            primary_content_node_id=request.primary_content_node_id,
            school_id=context.school_id,
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
        await _require_material_access(identity, session, material)

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


@catalog_router.post(
    "/materials/{material_id}/review",
    response_model=TheoryMaterialVersionResponse,
    summary="Submit, approve, reject, publish or archive a material version",
)
async def review_material(
    material_id: UUID,
    request: MaterialReviewRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TheoryMaterialVersionResponse:
    async with session_factory() as session:
        repo = TheoryMaterialRepository(session)
        material = await repo.get_by_id(material_id)
        if material is None:
            raise HTTPException(status_code=404, detail="Material not found")
        await _require_material_access(identity, session, material)

        version = await repo.get_latest_version(material_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Material has no versions")

        service = TheoryMaterialService()
        action = request.action.lower()
        try:
            if action == "submit":
                version = await service.submit_for_review(session, material_version_id=version.id)
            elif action == "approve":
                version = await service.approve_version(session, material_version_id=version.id)
            elif action == "reject":
                version = await service.reject_version(session, material_version_id=version.id, reason=request.reason)
            elif action == "publish":
                version = await service.publish_version(session, material_version_id=version.id)
            elif action == "archive":
                version = await service.archive_version(session, material_version_id=version.id)
            else:
                raise ValueError("Unsupported action.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return _version_to_response(version)


@catalog_router.post(
    "/materials/{material_id}/approve",
    response_model=TheoryMaterialVersionResponse,
    summary="Approve the latest version of a material",
)
async def approve_material(
    material_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TheoryMaterialVersionResponse:
    async with session_factory() as session:
        repo = TheoryMaterialRepository(session)
        material = await repo.get_by_id(material_id)
        if material is None:
            raise HTTPException(status_code=404, detail="Material not found")
        await _require_material_access(identity, session, material)

        version = await repo.get_latest_version(material_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Material has no versions")
        service = TheoryMaterialService()
        try:
            version = await service.approve_version(session, material_version_id=version.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return _version_to_response(version)


@catalog_router.post(
    "/materials/{material_id}/reject",
    response_model=TheoryMaterialVersionResponse,
    summary="Reject the latest version of a material",
)
async def reject_material(
    material_id: UUID,
    reason: str | None = None,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TheoryMaterialVersionResponse:
    async with session_factory() as session:
        repo = TheoryMaterialRepository(session)
        material = await repo.get_by_id(material_id)
        if material is None:
            raise HTTPException(status_code=404, detail="Material not found")
        await _require_material_access(identity, session, material)

        version = await repo.get_latest_version(material_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Material has no versions")
        service = TheoryMaterialService()
        try:
            version = await service.reject_version(session, material_version_id=version.id, reason=reason)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return _version_to_response(version)


@catalog_router.post(
    "/materials/{material_id}/publish",
    response_model=TheoryMaterialVersionResponse,
    summary="Publish the latest approved version of a material",
)
async def publish_material(
    material_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TheoryMaterialVersionResponse:
    async with session_factory() as session:
        repo = TheoryMaterialRepository(session)
        material = await repo.get_by_id(material_id)
        if material is None:
            raise HTTPException(status_code=404, detail="Material not found")
        await _require_material_access(identity, session, material)

        version = await repo.get_latest_version(material_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Material has no versions")
        service = TheoryMaterialService()
        try:
            version = await service.publish_version(session, material_version_id=version.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return _version_to_response(version)


@catalog_router.post(
    "/materials/{material_id}/archive",
    response_model=TheoryMaterialVersionResponse,
    summary="Archive the latest version of a material",
)
async def archive_material(
    material_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TheoryMaterialVersionResponse:
    async with session_factory() as session:
        repo = TheoryMaterialRepository(session)
        material = await repo.get_by_id(material_id)
        if material is None:
            raise HTTPException(status_code=404, detail="Material not found")
        await _require_material_access(identity, session, material)

        version = await repo.get_latest_version(material_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Material has no versions")
        service = TheoryMaterialService()
        try:
            version = await service.archive_version(session, material_version_id=version.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return _version_to_response(version)


@catalog_router.get(
    "/materials/{material_id}",
    response_model=TheoryMaterialDetailResponse,
    summary="Get a material with all of its versions",
)
async def get_material(
    material_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TheoryMaterialDetailResponse:
    async with session_factory() as session:
        repo = TheoryMaterialRepository(session)
        material = await repo.get_by_id(material_id)
        if material is None:
            raise HTTPException(status_code=404, detail="Material not found")
        await _require_material_access(identity, session, material)

        versions = await repo.list_versions(material_id)
        return TheoryMaterialDetailResponse(
            material=_material_to_response(material),
            versions=[_version_to_response(v) for v in versions],
        )


@catalog_router.get(
    "/materials/{material_id}/history",
    response_model=list[AdminAuditLogResponse],
    summary="Get material editorial audit history",
)
async def get_material_history(
    material_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[AdminAuditLogResponse]:
    async with session_factory() as session:
        repo = TheoryMaterialRepository(session)
        material = await repo.get_by_id(material_id)
        if material is None:
            raise HTTPException(status_code=404, detail="Material not found")
        await _require_material_access(identity, session, material)

        stmt = (
            select(AdminAuditLog)
            .where(
                AdminAuditLog.entity_type == "THEORY_MATERIAL",
                AdminAuditLog.entity_id == str(material_id),
            )
            .order_by(desc(AdminAuditLog.created_at))
        )
        result = await session.execute(stmt)
        logs = list(result.scalars().all())
        return [
            AdminAuditLogResponse(
                id=log.id,
                performed_by_external_id=log.performed_by_external_id,
                action=log.action,
                entity_type=log.entity_type,
                entity_id=log.entity_id,
                school_id=log.school_id,
                metadata=log.metadata_,
                created_at=log.created_at,
            )
            for log in logs
        ]
