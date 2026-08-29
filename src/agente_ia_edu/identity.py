"""External identity contracts for AGENTE IA EDU.

This module intentionally avoids any local user table or host-specific
credential contract. The hosting platform is the source of truth for
authentication and credentials. AGENTE IA EDU only receives a verified
identity context that contains stable external identifiers for the student,
teacher, institution, unit, grade level and classroom.

The goal is to support multiple host providers in the future without coupling
this package to JWT, OAuth, SSO, or any other protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ExternalIdentityRequest:
    """Authenticated identity request as received from a host platform.

    The request is intentionally limited to provider metadata and an external
    subject identifier. AGENTE IA EDU never stores or requests passwords,
    secret tokens, or other credentials from the host platform.
    """

    provider: str
    external_user_id: str
    subject: str | None = None
    roles: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExternalIdentityContext:
    """Normalized identity context used by AGENTE IA EDU.

    The identifiers below are stable IDs from the host platform. They are used
    to relate the learner, teacher, institution, unit, grade and class without
    creating a local user table as the primary source of identity.
    """

    provider: str
    external_user_id: str
    student_id: str | None = None
    teacher_id: str | None = None
    institution_id: str | None = None
    unit_id: str | None = None
    grade_level: str | None = None
    classroom_id: str | None = None
    roles: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ExternalIdentityProvider(Protocol):
    """Resolve an authenticated host identity into AGENTE IA EDU identifiers."""

    async def resolve(self, request: ExternalIdentityRequest) -> ExternalIdentityContext:
        """Return a provider-neutral identity context without persisting credentials."""


__all__ = [
    "ExternalIdentityContext",
    "ExternalIdentityProvider",
    "ExternalIdentityRequest",
]
