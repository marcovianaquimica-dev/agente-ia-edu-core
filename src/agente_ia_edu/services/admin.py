"""
Platform Administration Service for Multi-tenant SaaS Management (Phase 12A).

Handles:
- School / Tenant lifecycle (CREATE, UPDATE, ACTIVATE, DEACTIVATE, SUSPEND).
- Platform Module Management per school (AGENTE_IA_EDU, REDACAO_IA).
- User Role & Scope bindings (PLATFORM_ADMIN, DIRECTOR, COORDINATOR, TEACHER, STUDENT).
- Multi-tenant Isolation Enforcement.
- Administrative Audit Logging.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.db.models import (
    AdminAuditLog,
    School,
    SchoolModule,
    UserSchoolLink,
)

logger = logging.getLogger(__name__)


class AdminRole:
    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    DIRECTOR = "DIRECTOR"
    COORDINATOR = "COORDINATOR"
    TEACHER = "TEACHER"
    STUDENT = "STUDENT"

    ALL_ROLES = {PLATFORM_ADMIN, DIRECTOR, COORDINATOR, TEACHER, STUDENT}


class AdminScopeType:
    PLATFORM = "PLATFORM"
    SCHOOL = "SCHOOL"
    UNIT = "UNIT"
    SEGMENT = "SEGMENT"
    GRADE_LEVEL = "GRADE_LEVEL"
    CLASSROOM = "CLASSROOM"

    ALL_SCOPES = {PLATFORM, SCHOOL, UNIT, SEGMENT, GRADE_LEVEL, CLASSROOM}


class SchoolStatus:
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    SUSPENDED = "SUSPENDED"

    ALL_STATUSES = {ACTIVE, INACTIVE, SUSPENDED}


class PlatformModuleKey:
    AGENTE_IA_EDU = "AGENTE_IA_EDU"
    REDACAO_IA = "REDACAO_IA"

    ALL_MODULES = {AGENTE_IA_EDU, REDACAO_IA}


class PlatformAdminService:
    """Core administrative service for platform-wide SaaS management."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # -------------------------------------------------------------------------
    # 1. SCHOOL / TENANT MANAGEMENT
    # -------------------------------------------------------------------------

    async def create_school(
        self,
        *,
        performed_by_external_id: str,
        code: str,
        name: str,
        short_name: str | None = None,
        external_identifier: str | None = None,
        status: str = SchoolStatus.ACTIVE,
        metadata: dict[str, Any] | None = None,
    ) -> School:
        """Create a new school tenant."""
        clean_code = code.strip().upper()
        norm_status = status.upper()

        if norm_status not in SchoolStatus.ALL_STATUSES:
            raise ValueError(f"Invalid school status: {status}")

        stmt_existing = select(School).where(School.code == clean_code)
        res_existing = await self.session.execute(stmt_existing)
        if res_existing.scalar_one_or_none():
            raise ValueError(f"School with code '{clean_code}' already exists.")

        school = School(
            code=clean_code,
            name=name.strip(),
            short_name=short_name.strip() if short_name else None,
            external_identifier=external_identifier.strip() if external_identifier else None,
            status=norm_status,
            metadata_=metadata,
        )
        self.session.add(school)
        await self.session.flush()

        # Enable default AGENTE_IA_EDU module for the new school
        default_module = SchoolModule(
            school_id=school.id,
            module_key=PlatformModuleKey.AGENTE_IA_EDU,
            enabled=True,
            activated_at=datetime.now(timezone.utc),
        )
        self.session.add(default_module)

        # Record audit log
        await self.log_action(
            performed_by_external_id=performed_by_external_id,
            action="SCHOOL_CREATED",
            entity_type="SCHOOL",
            entity_id=str(school.id),
            school_id=school.id,
            metadata={"code": clean_code, "name": school.name, "status": norm_status},
        )

        await self.session.commit()
        await self.session.refresh(school)
        return school

    async def update_school(
        self,
        *,
        performed_by_external_id: str,
        school_id: uuid.UUID,
        name: str | None = None,
        short_name: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> School:
        """Update an existing school tenant."""
        school = await self.session.get(School, school_id)
        if not school:
            raise ValueError(f"School not found: {school_id}")

        changes = {}
        if name and name.strip():
            school.name = name.strip()
            changes["name"] = school.name

        if short_name is not None:
            school.short_name = short_name.strip() if short_name else None
            changes["short_name"] = school.short_name

        if status:
            norm_status = status.upper()
            if norm_status not in SchoolStatus.ALL_STATUSES:
                raise ValueError(f"Invalid school status: {status}")
            school.status = norm_status
            changes["status"] = norm_status

        if metadata is not None:
            school.metadata_ = metadata
            changes["metadata"] = metadata

        school.updated_at = datetime.now(timezone.utc)

        await self.log_action(
            performed_by_external_id=performed_by_external_id,
            action="SCHOOL_UPDATED",
            entity_type="SCHOOL",
            entity_id=str(school.id),
            school_id=school.id,
            metadata=changes,
        )

        await self.session.commit()
        await self.session.refresh(school)
        return school

    async def get_school(self, school_id_or_code: uuid.UUID | str) -> School | None:
        """Fetch school by UUID or unique code."""
        if isinstance(school_id_or_code, uuid.UUID):
            stmt = select(School).where(School.id == school_id_or_code).options(selectinload(School.modules))
        else:
            try:
                parsed_uuid = uuid.UUID(school_id_or_code)
                stmt = select(School).where(School.id == parsed_uuid).options(selectinload(School.modules))
            except ValueError:
                stmt = select(School).where(School.code == school_id_or_code.strip().upper()).options(selectinload(School.modules))

        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_schools(
        self,
        *,
        status_filter: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[School]:
        """List schools with optional status filter."""
        stmt = select(School).options(selectinload(School.modules))
        if status_filter:
            stmt = stmt.where(School.status == status_filter.upper())

        stmt = stmt.order_by(School.created_at.desc()).limit(limit).offset(offset)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    # -------------------------------------------------------------------------
    # 2. MODULE MANAGEMENT
    # -------------------------------------------------------------------------

    async def configure_school_module(
        self,
        *,
        performed_by_external_id: str,
        school_id: uuid.UUID,
        module_key: str,
        enabled: bool,
        metadata: dict[str, Any] | None = None,
    ) -> SchoolModule:
        """Enable or disable a platform module for a school tenant."""
        school = await self.session.get(School, school_id)
        if not school:
            raise ValueError(f"School not found: {school_id}")

        norm_key = module_key.upper()
        if norm_key not in PlatformModuleKey.ALL_MODULES:
            raise ValueError(f"Invalid module key: {module_key}")

        stmt = select(SchoolModule).where(
            SchoolModule.school_id == school_id,
            SchoolModule.module_key == norm_key,
        )
        res = await self.session.execute(stmt)
        mod = res.scalar_one_or_none()

        now = datetime.now(timezone.utc)
        if mod:
            mod.enabled = enabled
            if enabled:
                mod.deactivated_at = None
            else:
                mod.deactivated_at = now
            if metadata is not None:
                mod.metadata_ = metadata
        else:
            mod = SchoolModule(
                school_id=school_id,
                module_key=norm_key,
                enabled=enabled,
                activated_at=now,
                deactivated_at=None if enabled else now,
                metadata_=metadata,
            )
            self.session.add(mod)

        action_name = "MODULE_ENABLED" if enabled else "MODULE_DISABLED"
        await self.log_action(
            performed_by_external_id=performed_by_external_id,
            action=action_name,
            entity_type="MODULE",
            entity_id=norm_key,
            school_id=school_id,
            metadata={"module_key": norm_key, "enabled": enabled},
        )

        await self.session.commit()
        await self.session.refresh(mod)
        return mod

    async def is_module_enabled(self, school_id: uuid.UUID, module_key: str) -> bool:
        """Check if a specific module is active for a school."""
        school = await self.session.get(School, school_id)
        if not school or school.status != SchoolStatus.ACTIVE:
            return False

        stmt = select(SchoolModule).where(
            SchoolModule.school_id == school_id,
            SchoolModule.module_key == module_key.upper(),
            SchoolModule.enabled.is_(True),
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none() is not None

    # -------------------------------------------------------------------------
    # 3. USER ROLE & SCOPE LINK BINDINGS
    # -------------------------------------------------------------------------

    async def link_user_to_school(
        self,
        *,
        performed_by_external_id: str,
        external_user_id: str,
        role: str,
        scope_type: str = AdminScopeType.SCHOOL,
        school_id: uuid.UUID | None = None,
        scope_external_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UserSchoolLink:
        """Binds a user to a Role and Scope (e.g. TEACHER at SCHOOL A / CLASS_3B)."""
        norm_role = role.upper()
        norm_scope = scope_type.upper()

        if norm_role not in AdminRole.ALL_ROLES:
            raise ValueError(f"Invalid role: {role}")
        if norm_scope not in AdminScopeType.ALL_SCOPES:
            raise ValueError(f"Invalid scope type: {scope_type}")

        if norm_role != AdminRole.PLATFORM_ADMIN and not school_id:
            raise ValueError(f"school_id is required for role '{norm_role}'.")

        if school_id:
            school = await self.session.get(School, school_id)
            if not school:
                raise ValueError(f"School not found: {school_id}")

        link = UserSchoolLink(
            external_user_id=external_user_id.strip(),
            school_id=school_id,
            role=norm_role,
            scope_type=norm_scope,
            scope_external_id=scope_external_id.strip() if scope_external_id else None,
            active=True,
            metadata_=metadata,
        )
        self.session.add(link)
        await self.session.flush()

        await self.log_action(
            performed_by_external_id=performed_by_external_id,
            action="USER_LINKED",
            entity_type="USER_LINK",
            entity_id=str(link.id),
            school_id=school_id,
            metadata={
                "external_user_id": external_user_id,
                "role": norm_role,
                "scope_type": norm_scope,
                "scope_external_id": scope_external_id,
            },
        )

        await self.session.commit()
        await self.session.refresh(link)
        return link

    async def deactivate_user_link(
        self,
        *,
        performed_by_external_id: str,
        link_id: uuid.UUID,
    ) -> UserSchoolLink:
        """Deactivates a user role/scope link."""
        link = await self.session.get(UserSchoolLink, link_id)
        if not link:
            raise ValueError(f"UserSchoolLink not found: {link_id}")

        link.active = False

        await self.log_action(
            performed_by_external_id=performed_by_external_id,
            action="USER_UNLINKED",
            entity_type="USER_LINK",
            entity_id=str(link.id),
            school_id=link.school_id,
            metadata={"external_user_id": link.external_user_id, "role": link.role},
        )

        await self.session.commit()
        await self.session.refresh(link)
        return link

    async def get_user_active_links(self, external_user_id: str) -> list[UserSchoolLink]:
        """Fetch all active role/scope links for a user."""
        stmt = (
            select(UserSchoolLink)
            .where(
                UserSchoolLink.external_user_id == external_user_id,
                UserSchoolLink.active.is_(True),
            )
            .options(selectinload(UserSchoolLink.school))
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def is_platform_admin(self, external_user_id: str) -> bool:
        """Check if user has active PLATFORM_ADMIN binding."""
        links = await self.get_user_active_links(external_user_id)
        return any(l.role == AdminRole.PLATFORM_ADMIN for l in links)

    # -------------------------------------------------------------------------
    # 4. AUDIT LOGGING
    # -------------------------------------------------------------------------

    async def log_action(
        self,
        *,
        performed_by_external_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        school_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AdminAuditLog:
        """Record an administrative audit log entry."""
        log = AdminAuditLog(
            performed_by_external_id=performed_by_external_id,
            action=action.upper(),
            entity_type=entity_type.upper(),
            entity_id=entity_id,
            school_id=school_id,
            metadata_=metadata,
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def list_audit_logs(
        self,
        *,
        school_id: uuid.UUID | None = None,
        action_filter: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AdminAuditLog]:
        """Query audit log trail."""
        stmt = select(AdminAuditLog)
        if school_id:
            stmt = stmt.where(AdminAuditLog.school_id == school_id)
        if action_filter:
            stmt = stmt.where(AdminAuditLog.action == action_filter.upper())

        stmt = stmt.order_by(AdminAuditLog.created_at.desc()).limit(limit).offset(offset)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
