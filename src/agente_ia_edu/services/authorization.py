"""Authorization and access-context resolution for the AGENTE IA EDU platform.

This module centralizes the mapping between an authenticated identity and the
effective role/scope used by the educational domain. It intentionally keeps a
compatibility layer around the already-existing external_user_id style while
providing a more explicit context object for future SaaS multi-tenant auth.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import SchoolModule, UserSchoolLink
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext

from .discipline_gate import DisciplineGate


@dataclass(frozen=True, slots=True)
class AuthorizationCheckResult:
    allowed: bool
    reason: str | None = None


class AuthorizationService:
    """Resolve the effective access context for a user.

    Compatibility strategy:
    - Existing external_user_id identifiers remain the source of truth.
    - PlatformAdmin is recognized as a distinct role but does not automatically
      grant pedagogical access unless an explicit school/classroom scope exists.
    - If the user has no active school links, the default independent-student
      context is used with school_id=None.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def _active_links(self, external_user_id: str) -> list[UserSchoolLink]:
        stmt = (
            select(UserSchoolLink)
            .where(
                UserSchoolLink.external_user_id == external_user_id,
                UserSchoolLink.active.is_(True),
            )
            .order_by(UserSchoolLink.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def _school_modules(self, school_id: UUID | str | None) -> set[str]:
        if school_id is None:
            return set()

        stmt = (
            select(SchoolModule.module_key)
            .where(
                SchoolModule.school_id == school_id,
                SchoolModule.enabled.is_(True),
            )
        )
        result = await self.session.execute(stmt)
        return {row[0] for row in result.all()}

    async def resolve_context(
        self,
        identity: ExternalIdentityContext,
        *,
        school_id: UUID | str | None = None,
        scope_external_id: str | None = None,
        role_hint: str | None = None,
    ) -> AuthenticatedUserContext:
        """Return the effective authenticated context.

        If the user has active school links, the highest-priority role wins unless
        a more specific explicit context is passed. Otherwise, a safe independent
        student context is returned with school_id=None.
        """
        external_user_id = identity.external_user_id
        links = await self._active_links(external_user_id)

        if links:
            ordered = sorted(
                links,
                key=lambda link: self._role_rank(link.role),
                reverse=True,
            )
            selected = ordered[0]

            if role_hint:
                normalized_role = role_hint.upper()
                explicit = next((link for link in links if link.role == normalized_role), None)
                if explicit is not None:
                    selected = explicit

            if school_id is not None and selected.school_id != school_id:
                explicit_school = next(
                    (link for link in links if link.school_id == school_id),
                    None,
                )
                if explicit_school is not None:
                    selected = explicit_school

            school_id_value = selected.school_id
            scope_type = selected.scope_type
            context_scope = selected.scope_external_id or scope_external_id
            modules = tuple(sorted(await self._school_modules(school_id_value)))
            # Whether this person holds a real platform-admin link at all -
            # not whether the link an explicit school_id/role_hint narrowed
            # `selected` to happens to be one. A real admin who also holds
            # another link (e.g. a TEACHER link for testing) must not lose
            # is_platform_admin just because a caller passed that other
            # link's school_id (PlatformAdminService.is_platform_admin
            # already checks this the same way, via any(...)).
            is_platform_admin = any(link.role == "PLATFORM_ADMIN" for link in links)

            return AuthenticatedUserContext(
                user_id=external_user_id,
                external_identity_id=external_user_id,
                role=selected.role,
                school_id=school_id_value,
                scope_type=scope_type,
                scope_external_id=context_scope,
                modules=modules,
                is_active=True,
                is_platform_admin=is_platform_admin,
                metadata={
                    "provider": identity.provider,
                    "roles": identity.roles,
                    "selected_from_link": True,
                    "school_link_metadata": selected.metadata_ or {},
                },
            )

        fallback_role = (role_hint or (identity.roles[0] if identity.roles else "STUDENT")).upper()
        if fallback_role == "ADMIN":
            fallback_role = "PLATFORM_ADMIN"
        if fallback_role not in {"PLATFORM_ADMIN", "DIRECTOR", "COORDINATOR", "SECRETARY", "TEACHER", "STUDENT"}:
            fallback_role = "STUDENT"

        modules = tuple(sorted(await self._school_modules(school_id)))
        return AuthenticatedUserContext(
            user_id=external_user_id,
            external_identity_id=external_user_id,
            role=fallback_role,
            school_id=None if school_id is None else school_id,
            scope_type="PLATFORM",
            scope_external_id=scope_external_id,
            modules=modules,
            is_active=True,
            is_platform_admin=fallback_role == "PLATFORM_ADMIN",
            metadata={
                "provider": identity.provider,
                "roles": identity.roles,
                "selected_from_link": False,
                "independent_student": school_id is None,
            },
        )

    @staticmethod
    def _role_rank(role: str) -> int:
        precedence = {
            "PLATFORM_ADMIN": 50,
            "DIRECTOR": 40,
            "COORDINATOR": 30,
            "TEACHER": 20,
            "SECRETARY": 15,
            "STUDENT": 10,
        }
        return precedence.get(role.upper(), 0)

    async def require_role(
        self,
        context: AuthenticatedUserContext,
        *allowed_roles: str,
    ) -> AuthorizationCheckResult:
        allowed = {role.upper() for role in allowed_roles}
        if context.role.upper() in allowed:
            return AuthorizationCheckResult(True, None)
        return AuthorizationCheckResult(False, f"Role required: {', '.join(sorted(allowed))}")

    async def require_school_access(
        self,
        context: AuthenticatedUserContext,
        school_id: UUID | str,
    ) -> AuthorizationCheckResult:
        if not context.is_active:
            return AuthorizationCheckResult(False, "User account is inactive.")
        if context.school_id is None:
            return AuthorizationCheckResult(False, "This user does not belong to a school context.")
        if str(context.school_id) != str(school_id):
            return AuthorizationCheckResult(False, "User does not have access to the requested school.")
        return AuthorizationCheckResult(True, None)

    async def require_module(
        self,
        context: AuthenticatedUserContext,
        module_key: str,
    ) -> AuthorizationCheckResult:
        if context.school_id is None:
            # Independent students may use platform features without an explicit school
            return AuthorizationCheckResult(True, None)
        if module_key.upper() in set(context.modules):
            return AuthorizationCheckResult(True, None)
        return AuthorizationCheckResult(False, f"Module '{module_key}' is not enabled for the current school.")

    async def require_discipline(
        self,
        context: AuthenticatedUserContext,
        catalog_node_id: uuid.UUID | None,
    ) -> AuthorizationCheckResult:
        """Refuse content outside the school's declared pedagogical scope.

        A school that declared no scope is unrestricted - which is every school
        in production today, so this must never refuse on absence.
        """
        scope = await DisciplineGate(self.session).scope_for_school(context.school_id)
        if scope.permits(catalog_node_id):
            return AuthorizationCheckResult(True, None)
        return AuthorizationCheckResult(
            False, "This discipline is outside the school's pedagogical scope."
        )

    async def require_scope(
        self,
        context: AuthenticatedUserContext,
        *,
        scope_type: str | None = None,
        scope_external_id: str | None = None,
    ) -> AuthorizationCheckResult:
        if scope_type and context.scope_type.upper() != scope_type.upper():
            return AuthorizationCheckResult(False, f"Scope type mismatch: expected {scope_type}")
        if scope_external_id and context.scope_external_id != scope_external_id:
            return AuthorizationCheckResult(False, "Scope mismatch for the requested context.")
        return AuthorizationCheckResult(True, None)


__all__ = [
    "AuthorizationCheckResult",
    "AuthorizationService",
]
