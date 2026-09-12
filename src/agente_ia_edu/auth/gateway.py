"""Authentication module for Fase 16.

Bridges token validation with identity resolution.
Ensures that authentication is separate from the pedagogical engine.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from agente_ia_edu.auth.token import TokenPayload, TokenValidator
from agente_ia_edu.identity import ExternalIdentityContext, ExternalIdentityRequest

logger = logging.getLogger(__name__)


@runtime_checkable
class AuthenticationGateway(Protocol):
    """Gateway that validates token and converts it to identity context.

    This is the entry point for authentication.
    It uses a TokenValidator to validate the raw token,
    then converts the TokenPayload into an ExternalIdentityContext.
    """

    async def authenticate(self, token: str) -> ExternalIdentityContext:
        """Authenticate a token and return identity context.

        Args:
            token: Raw authentication token (Bearer token, API key, etc.)

        Returns:
            ExternalIdentityContext: Normalized identity

        Raises:
            ValueError: If token is invalid or authentication fails
        """
        ...


class SimpleAuthenticationGateway:
    """Simple implementation of AuthenticationGateway for development/testing.

    This gateway:
    1. Uses a TokenValidator to validate the token
    2. Extracts subject from TokenPayload
    3. Converts to ExternalIdentityContext
    4. Does NOT persist identity in database (that happens in AuthorizationService)
    """

    def __init__(self, token_validator: TokenValidator):
        """Initialize gateway with a token validator.

        Args:
            token_validator: Object implementing TokenValidator protocol
        """
        self.token_validator = token_validator

    async def authenticate(self, token: str) -> ExternalIdentityContext:
        """Validate token and create identity context.

        Args:
            token: Raw token string

        Returns:
            ExternalIdentityContext with subject from token

        Raises:
            ValueError: If token validation fails
        """
        if not token:
            raise ValueError("Authentication token is required")

        payload = await self.token_validator.validate(token)

        context = ExternalIdentityContext(
            provider=payload.provider,
            external_user_id=payload.subject,
            roles=payload.roles,
            metadata={
                **(payload.metadata or {}),
                "email": payload.email,
                "name": payload.name,
            },
        )

        logger.debug(f"Authenticated user: {payload.subject} from provider {payload.provider}")
        return context


__all__ = [
    "AuthenticationGateway",
    "SimpleAuthenticationGateway",
]
