"""FastAPI dependency factories for AGENTE IA EDU.

This module provides dependencies that resolve external identity and other
shared resources for the API layer.
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request

from agente_ia_edu.db.session import create_session_factory
from agente_ia_edu.identity import (
    AuthenticatedUserContext,
    ExternalIdentityContext,
    ExternalIdentityProvider,
    ExternalIdentityRequest,
)
from agente_ia_edu.services.authorization import AuthorizationService

logger = logging.getLogger(__name__)

_warned_test_provider_in_use = False


class TestExternalIdentityProvider:
    """Test/development identity provider for non-production use.
    
    This provider allows deterministic testing by deriving identity context
    from request data (e.g., "student:alice", "teacher:bob" in auth header).
    
    DO NOT USE IN PRODUCTION. The host platform must provide a real provider
    that implements ExternalIdentityProvider protocol.
    
    Usage in development:
    - Set via set_identity_provider(TestExternalIdentityProvider())
    - Include "Authorization: Bearer student:alice" in test requests
    - Identity will be "alice" with role "student"
    """

    async def resolve(self, request: ExternalIdentityRequest) -> ExternalIdentityContext:
        """Return a test identity context from the request subject.
        
        Expects subject format: "role:identifier" (e.g., "student:alice", "teacher:bob")
        
        For requests without a subject, returns a generic test identity.
        This is deterministic and suitable for integration tests but must NOT
        be used in production.
        """
        # Loud, once-per-process, not once-per-request: a host that swaps the
        # provider before serving any request never triggers this, but a host
        # that forgets to (or serves one request before it does) gets exactly
        # one unmissable log line instead of silent, self-asserted identities
        # (e.g. "Authorization: Bearer platform_admin:anyone") being accepted
        # with no trace anywhere. Rate-limited so a real test/dev run - which
        # legitimately calls this thousands of times per suite - stays quiet.
        global _warned_test_provider_in_use
        if not _warned_test_provider_in_use:
            _warned_test_provider_in_use = True
            logger.warning(
                "TestExternalIdentityProvider resolved a real request's identity. "
                "This provider trusts the Authorization header's claimed role/id "
                "verbatim and must never be used in production - if this is a "
                "production process, call set_identity_provider() with a real "
                "provider before serving traffic."
            )
        subject = request.subject or request.external_user_id
        
        if subject and subject != "unknown" and ":" in subject:
            parts = subject.split(":", 1)
            role = parts[0]
            identifier = parts[1]
        else:
            # Fallback for non-formatted subjects
            role = "student"
            identifier = request.external_user_id or "test-user"

        student_id = identifier if role == "student" else None
        teacher_id = identifier if role == "teacher" else None

        return ExternalIdentityContext(
            provider="TestProvider",
            external_user_id=identifier,
            student_id=student_id,
            teacher_id=teacher_id,
            roles=(role,) if role else ("student",),
            metadata={
                "test_provider": True,
                "test_mode": True,
                "original_subject": subject,
            },
        )


# Global provider instance (can be replaced at startup by host)
_identity_provider: ExternalIdentityProvider | TestExternalIdentityProvider = (
    TestExternalIdentityProvider()
)


def set_identity_provider(provider: ExternalIdentityProvider) -> None:
    """Set the external identity provider for the application.
    
    This is called at FastAPI startup by the host platform to inject
    the real provider (OAuth, SAML, custom auth, etc.).
    
    Args:
        provider: Object implementing ExternalIdentityProvider protocol.
    """
    global _identity_provider
    _identity_provider = provider


def reset_identity_provider() -> None:
    """Reset the application identity provider to the deterministic default."""
    global _identity_provider
    _identity_provider = TestExternalIdentityProvider()


def get_identity_provider() -> ExternalIdentityProvider:
    """Get the current identity provider.
    
    This is a factory function for dependency injection.
    """
    return _identity_provider


async def get_current_identity(request: Request) -> ExternalIdentityContext:
    """FastAPI dependency that resolves the authenticated identity.
    
    This dependency:
    1. Gets the current provider via get_identity_provider()
    2. Extracts authentication data from the request (headers, cookies, etc.)
    3. Creates an ExternalIdentityRequest with the extracted data
    4. Calls provider.resolve() to get ExternalIdentityContext
    5. Returns the normalized identity
    
    The actual request context (headers, auth tokens, etc.) is extracted
    by this function and passed to the provider. The provider is responsible
    for validating and normalizing the identity information.
    
    In production:
    - The host injects a real provider at startup via set_identity_provider()
    - The provider knows how to validate/process its own authentication format
    - AGENTE IA EDU receives only the normalized ExternalIdentityContext
    
    In development:
    - TestExternalIdentityProvider is used by default
    - Allows deterministic testing without real authentication
    - Can be customized via set_identity_provider()
    
    Args:
        request: FastAPI request object (injected by FastAPI)
    
    Returns:
        ExternalIdentityContext: Frozen dataclass with normalized identity.
        
    Raises:
        HTTPException: If provider.resolve() fails or returns None.
    """
    provider = get_identity_provider()
    
    # Extract authentication information from request
    # The host platform will implement a provider that knows how to
    # validate and process this information (JWT, OAuth token, API key, etc.)
    
    # For development/test, we use a simple header extraction
    auth_header = request.headers.get("authorization", "")
    subject = auth_header.replace("Bearer ", "") if auth_header else None
    
    request_obj = ExternalIdentityRequest(
        provider=type(provider).__name__,
        external_user_id=subject or "unknown",
        subject=subject,
    )
    
    context = await provider.resolve(request_obj)
    return context


async def get_session_factory():
    """Get database session factory for dependency injection.
    
    This dependency provides an async session factory that can be used
    to create database sessions within endpoint handlers.
    
    Usage in endpoints:
        @router.get("/resource")
        async def get_resource(session_factory=Depends(get_session_factory)):
            async with session_factory() as session:
                # Use session for queries
    """
    return create_session_factory()


async def get_current_authenticated_context(
    request: Request,
    session_factory=Depends(get_session_factory),
) -> AuthenticatedUserContext:
    """Resolve the effective access context for the authenticated user.

    This is the compatibility layer that converts the existing external identity
    protocol into the richer platform access context used for multi-tenant auth.
    """
    identity = await get_current_identity(request)
    async with session_factory() as session:
        authz = AuthorizationService(session)
        context = await authz.resolve_context(identity)
        return context


async def reject_reception_only_role(
    identity: ExternalIdentityContext = Depends(get_current_identity),
) -> None:
    """Prevent reception-only identities from entering other business domains."""
    if "SECRETARY" in {role.upper() for role in identity.roles}:
        raise HTTPException(
            status_code=403,
            detail="SECRETARY access is restricted to the reception domain.",
        )


__all__ = [
    "TestExternalIdentityProvider",
    "get_current_authenticated_context",
    "get_current_identity",
    "get_identity_provider",
    "reject_reception_only_role",
    "reset_identity_provider",
    "set_identity_provider",
]
