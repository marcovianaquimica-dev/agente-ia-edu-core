"""
API routes for Platform Administration & Multi-tenancy Management (Phase 12A).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_current_identity, get_session_factory
from ..schemas.admin import (
    AdminAuditLogResponse,
    SchoolCreateRequest,
    SchoolModuleConfigureRequest,
    SchoolModuleResponse,
    SchoolResponse,
    SchoolUpdateRequest,
    UserLinkCreateRequest,
    UserLinkResponse,
    PedagogicalUniverseBindingRequest,
    PedagogicalUniverseCatalogScopeRequest,
    PedagogicalUniverseConfigurationRequest,
    PedagogicalUniverseCreateRequest,
    PedagogicalUniverseResponse,
)
from ...db.models import UserSchoolLink
from ...identity import ExternalIdentityContext
from ...services.admin import AdminRole, PlatformAdminService
from ...services.pedagogical_universe import PedagogicalUniverseService

admin_router = APIRouter(
    prefix="/api/v1/admin",
    tags=["platform-administration"],
)


async def require_platform_admin(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> ExternalIdentityContext:
    """Dependency enforcing PLATFORM_ADMIN authorization."""
    user_roles = [r.upper() for r in identity.roles]
    if "PLATFORM_ADMIN" in user_roles or "ADMIN" in user_roles or identity.external_user_id.upper() in ("ADMIN", "PLATFORM_ADMIN"):
        return identity

    async with session_factory() as session:
        admin_service = PlatformAdminService(session)
        is_admin = await admin_service.is_platform_admin(identity.external_user_id)
        if is_admin:
            return identity

    raise HTTPException(
        status_code=403,
        detail="Access denied: PLATFORM_ADMIN role required.",
    )


def _to_school_response(school) -> SchoolResponse:
    modules = [
        SchoolModuleResponse(
            id=m.id,
            school_id=m.school_id,
            module_key=m.module_key,
            enabled=m.enabled,
            activated_at=m.activated_at,
            deactivated_at=m.deactivated_at,
        )
        for m in (school.modules or [])
    ]
    return SchoolResponse(
        id=school.id,
        code=school.code,
        name=school.name,
        short_name=school.short_name,
        external_identifier=school.external_identifier,
        status=school.status,
        modules=modules,
        created_at=school.created_at,
        updated_at=school.updated_at,
    )


@admin_router.post(
    "/schools",
    status_code=201,
    response_model=SchoolResponse,
    summary="Create a new school tenant",
)
async def create_school(
    request: SchoolCreateRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> SchoolResponse:
    async with session_factory() as session:
        admin_service = PlatformAdminService(session)
        try:
            school = await admin_service.create_school(
                performed_by_external_id=identity.external_user_id,
                code=request.code,
                name=request.name,
                short_name=request.short_name,
                external_identifier=request.external_identifier,
                status=request.status,
                metadata=request.metadata,
            )
            return _to_school_response(school)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@admin_router.get(
    "/schools",
    response_model=list[SchoolResponse],
    summary="List school tenants",
)
async def list_schools(
    status: str | None = Query(None, description="ACTIVE, INACTIVE, SUSPENDED"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> list[SchoolResponse]:
    async with session_factory() as session:
        admin_service = PlatformAdminService(session)
        schools = await admin_service.list_schools(
            status_filter=status,
            limit=limit,
            offset=offset,
        )
        return [_to_school_response(s) for s in schools]


@admin_router.get(
    "/schools/{school_id}",
    response_model=SchoolResponse,
    summary="Get school tenant details",
)
async def get_school(
    school_id: UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> SchoolResponse:
    async with session_factory() as session:
        admin_service = PlatformAdminService(session)
        school = await admin_service.get_school(school_id)
        if not school:
            raise HTTPException(status_code=404, detail="School tenant not found.")
        return _to_school_response(school)


def _to_universe_response(universe) -> PedagogicalUniverseResponse:
    return PedagogicalUniverseResponse(
        id=universe.id, external_id=universe.external_id, slug=universe.slug,
        name=universe.name, status=universe.status, owner_type=universe.owner_type,
        owner_external_id=universe.owner_external_id, configuration_version=universe.configuration_version,
    )


@admin_router.post("/pedagogical-universes", status_code=201, response_model=PedagogicalUniverseResponse)
async def create_pedagogical_universe(
    request: PedagogicalUniverseCreateRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> PedagogicalUniverseResponse:
    async with session_factory() as session:
        service = PedagogicalUniverseService(session)
        try:
            universe = await service.create_universe(performed_by_external_id=identity.external_user_id, **request.model_dump())
            return _to_universe_response(universe)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@admin_router.get("/pedagogical-universes/{universe_id}", response_model=PedagogicalUniverseResponse)
async def get_pedagogical_universe(
    universe_id: UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> PedagogicalUniverseResponse:
    async with session_factory() as session:
        try:
            return _to_universe_response(await PedagogicalUniverseService(session).require_universe(universe_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))


@admin_router.get("/pedagogical-universes", response_model=list[PedagogicalUniverseResponse])
async def list_pedagogical_universes(
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> list[PedagogicalUniverseResponse]:
    async with session_factory() as session:
        return [_to_universe_response(item) for item in await PedagogicalUniverseService(session).list_universes()]


@admin_router.patch("/pedagogical-universes/{universe_id}/status", response_model=PedagogicalUniverseResponse)
async def set_pedagogical_universe_status(
    universe_id: UUID, status: str = Query(...),
    identity: ExternalIdentityContext = Depends(require_platform_admin), session_factory=Depends(get_session_factory),
) -> PedagogicalUniverseResponse:
    async with session_factory() as session:
        try:
            universe = await PedagogicalUniverseService(session).set_status(universe_id=universe_id, status=status, performed_by_external_id=identity.external_user_id)
            return _to_universe_response(universe)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@admin_router.patch("/pedagogical-universes/{universe_id}/configuration", response_model=PedagogicalUniverseResponse)
async def update_pedagogical_universe_configuration(
    universe_id: UUID,
    request: PedagogicalUniverseConfigurationRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> PedagogicalUniverseResponse:
    async with session_factory() as session:
        try:
            universe = await PedagogicalUniverseService(session).update_configuration(universe_id=universe_id, performed_by_external_id=identity.external_user_id, **request.model_dump())
            return _to_universe_response(universe)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@admin_router.post("/pedagogical-universes/{universe_id}/catalog-scopes", status_code=201)
async def add_pedagogical_universe_catalog_scope(
    universe_id: UUID, request: PedagogicalUniverseCatalogScopeRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin), session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        try:
            scope = await PedagogicalUniverseService(session).add_catalog_scope(universe_id=universe_id, **request.model_dump())
            return {"id": str(scope.id)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@admin_router.post("/pedagogical-universes/{universe_id}/bindings", status_code=201)
async def bind_pedagogical_universe(
    universe_id: UUID, request: PedagogicalUniverseBindingRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin), session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        try:
            binding = await PedagogicalUniverseService(session).bind(universe_id=universe_id, **request.model_dump())
            return {"id": str(binding.id)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@admin_router.delete("/pedagogical-universes/catalog-scopes/{scope_id}", status_code=204)
async def remove_pedagogical_universe_catalog_scope(
    scope_id: UUID, identity: ExternalIdentityContext = Depends(require_platform_admin), session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        try:
            await PedagogicalUniverseService(session).remove_catalog_scope(scope_id=scope_id, performed_by_external_id=identity.external_user_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))


@admin_router.delete("/pedagogical-universes/bindings/{binding_id}", status_code=204)
async def remove_pedagogical_universe_binding(
    binding_id: UUID, identity: ExternalIdentityContext = Depends(require_platform_admin), session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        try:
            await PedagogicalUniverseService(session).remove_binding(binding_id=binding_id, performed_by_external_id=identity.external_user_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))


@admin_router.patch(
    "/schools/{school_id}",
    response_model=SchoolResponse,
    summary="Update school tenant details and status",
)
async def update_school(
    school_id: UUID,
    request: SchoolUpdateRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> SchoolResponse:
    async with session_factory() as session:
        admin_service = PlatformAdminService(session)
        try:
            school = await admin_service.update_school(
                performed_by_external_id=identity.external_user_id,
                school_id=school_id,
                name=request.name,
                short_name=request.short_name,
                status=request.status,
                metadata=request.metadata,
            )
            return _to_school_response(school)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@admin_router.post(
    "/schools/{school_id}/modules",
    status_code=201,
    response_model=SchoolModuleResponse,
    summary="Configure platform module for school",
)
async def configure_school_module(
    school_id: UUID,
    request: SchoolModuleConfigureRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> SchoolModuleResponse:
    async with session_factory() as session:
        admin_service = PlatformAdminService(session)
        try:
            mod = await admin_service.configure_school_module(
                performed_by_external_id=identity.external_user_id,
                school_id=school_id,
                module_key=request.module_key,
                enabled=request.enabled,
                metadata=request.metadata,
            )
            return SchoolModuleResponse(
                id=mod.id,
                school_id=mod.school_id,
                module_key=mod.module_key,
                enabled=mod.enabled,
                activated_at=mod.activated_at,
                deactivated_at=mod.deactivated_at,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@admin_router.patch(
    "/schools/{school_id}/modules/{module_key}",
    response_model=SchoolModuleResponse,
    summary="Toggle module enablement for school",
)
async def toggle_school_module(
    school_id: UUID,
    module_key: str,
    enabled: bool = Query(..., description="True to enable, False to disable"),
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> SchoolModuleResponse:
    async with session_factory() as session:
        admin_service = PlatformAdminService(session)
        try:
            mod = await admin_service.configure_school_module(
                performed_by_external_id=identity.external_user_id,
                school_id=school_id,
                module_key=module_key,
                enabled=enabled,
            )
            return SchoolModuleResponse(
                id=mod.id,
                school_id=mod.school_id,
                module_key=mod.module_key,
                enabled=mod.enabled,
                activated_at=mod.activated_at,
                deactivated_at=mod.deactivated_at,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@admin_router.post(
    "/schools/{school_id}/users",
    status_code=201,
    response_model=UserLinkResponse,
    summary="Bind a user to a school role and scope",
)
async def link_user_to_school(
    school_id: UUID,
    request: UserLinkCreateRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> UserLinkResponse:
    async with session_factory() as session:
        admin_service = PlatformAdminService(session)
        try:
            link = await admin_service.link_user_to_school(
                performed_by_external_id=identity.external_user_id,
                external_user_id=request.external_user_id,
                role=request.role,
                scope_type=request.scope_type,
                school_id=school_id,
                scope_external_id=request.scope_external_id,
                metadata=request.metadata,
            )
            return UserLinkResponse(
                id=link.id,
                external_user_id=link.external_user_id,
                school_id=link.school_id,
                role=link.role,
                scope_type=link.scope_type,
                scope_external_id=link.scope_external_id,
                active=link.active,
                created_at=link.created_at,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@admin_router.get(
    "/schools/{school_id}/users",
    response_model=list[UserLinkResponse],
    summary="List active user/role links for a school",
)
async def list_school_users(
    school_id: UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> list[UserLinkResponse]:
    async with session_factory() as session:
        admin_service = PlatformAdminService(session)
        links = await admin_service.list_school_users(school_id)
        return [
            UserLinkResponse(
                id=link.id,
                external_user_id=link.external_user_id,
                school_id=link.school_id,
                role=link.role,
                scope_type=link.scope_type,
                scope_external_id=link.scope_external_id,
                active=link.active,
                created_at=link.created_at,
            )
            for link in links
        ]


@admin_router.delete(
    "/schools/{school_id}/users/{link_id}",
    status_code=204,
    summary="Deactivate a user's role/scope link for a school",
)
async def deactivate_school_user_link(
    school_id: UUID,
    link_id: UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> None:
    async with session_factory() as session:
        admin_service = PlatformAdminService(session)
        existing = await session.get(UserSchoolLink, link_id)
        if existing is None or existing.school_id != school_id:
            raise HTTPException(status_code=404, detail="User link not found for this school.")
        try:
            await admin_service.deactivate_user_link(
                performed_by_external_id=identity.external_user_id,
                link_id=link_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))


@admin_router.get(
    "/audit",
    response_model=list[AdminAuditLogResponse],
    summary="List administrative audit log trail",
)
async def list_audit_logs(
    school_id: UUID | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> list[AdminAuditLogResponse]:
    async with session_factory() as session:
        admin_service = PlatformAdminService(session)
        logs = await admin_service.list_audit_logs(
            school_id=school_id,
            action_filter=action,
            limit=limit,
            offset=offset,
        )
        return [
            AdminAuditLogResponse(
                id=l.id,
                performed_by_external_id=l.performed_by_external_id,
                action=l.action,
                entity_type=l.entity_type,
                entity_id=l.entity_id,
                school_id=l.school_id,
                metadata=l.metadata_,
                created_at=l.created_at,
            )
            for l in logs
        ]
