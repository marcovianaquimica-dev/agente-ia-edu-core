"""Token authentication and validation abstractions for Fase 16.

This module provides:
1. TokenPayload: structured token data
2. TokenValidator: abstract protocol for token validation
3. TestTokenValidator: test implementation for development/testing
4. Real JWT/OIDC validation can be plugged in without changing the rest of the app

IMPORTANT: Do NOT implement custom cryptography. Use proven libraries only.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenPayload:
    """Validated token data extracted from JWT or other auth mechanism."""

    subject: str  # sub claim (user identifier)
    email: str | None = None
    name: str | None = None
    provider: str = "unknown"
    roles: tuple[str, ...] = ()
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


@runtime_checkable
class TokenValidator(Protocol):
    """Abstract protocol for token validation.

    Implementations must validate:
    - Token signature (if applicable)
    - Token expiration
    - Issuer (if applicable)
    - Audience (if applicable)

    Must NOT accept invalid tokens silently.
    Must NOT trust token content without validation.
    """

    async def validate(self, token: str) -> TokenPayload:
        """Validate and extract payload from token.

        Args:
            token: Raw token string (JWT, API key, etc.)

        Returns:
            TokenPayload: Validated payload

        Raises:
            ValueError: If token is invalid, expired, or validation fails
        """
        ...


class TestTokenValidator:
    """Test token validator for development and automated testing.

    This validator:
    1. Does NOT perform real cryptographic validation
    2. Uses a simple format: "test:<role>:<subject>" or just "<subject>"
    3. Allows deterministic testing without external dependencies
    4. IS CLEARLY MARKED AS TEST-ONLY
    5. MUST NOT be used in production

    Example token formats:
    - "test:student:alice" -> role=STUDENT, subject=alice
    - "test:teacher:bob" -> role=TEACHER, subject=bob
    - "test:admin:master" -> role=PLATFORM_ADMIN, subject=master
    - "alice" -> role=STUDENT (default), subject=alice
    """

    def __init__(self, default_role: str = "STUDENT"):
        self.default_role = default_role.upper()
        self.provider = "TestProvider"

    async def validate(self, token: str) -> TokenPayload:
        """Parse test token into payload.

        Format: "test:<role>:<subject>" or "<subject>"

        Args:
            token: Token string

        Returns:
            TokenPayload with parsed role and subject

        Raises:
            ValueError: If token is empty or invalid format
        """
        if not token or not isinstance(token, str):
            raise ValueError("Token must be a non-empty string")

        token = token.strip()

        # Only accept alphanumeric + colon + underscore + hyphen
        if not all(c.isalnum() or c in (':_-') for c in token):
            raise ValueError(f"Invalid token format: contains invalid characters")

        if token.startswith("test:"):
            parts = token[5:].split(":", 1)
            if len(parts) == 2:
                role, subject = parts
            else:
                raise ValueError("Invalid test token format. Expected 'test:<role>:<subject>'")
        else:
            role = self.default_role
            subject = token

        if not subject:
            raise ValueError("Token subject cannot be empty")

        role = role.upper()
        if role not in {"PLATFORM_ADMIN", "DIRECTOR", "COORDINATOR", "SECRETARY", "TEACHER", "STUDENT", "ADMIN"}:
            role = self.default_role

        # Normalize ADMIN to PLATFORM_ADMIN
        if role == "ADMIN":
            role = "PLATFORM_ADMIN"

        return TokenPayload(
            subject=subject,
            provider=self.provider,
            roles=(role,),
            metadata={
                "test_mode": True,
                "parsed_from_test_token": True,
            },
        )


class JWTTokenValidator:
    """JWT token validator for production use.

    This validator:
    1. Validates JWT signature
    2. Checks token expiration
    3. Verifies issuer and audience
    4. Extracts standard claims
    5. Requires external library (PyJWT)

    This is a STUB for future implementation.
    For now, projects using real JWT should inject their own validator.
    """

    def __init__(self, secret: str | None = None, issuer: str | None = None, audience: str | None = None):
        """Initialize JWT validator.

        Args:
            secret: Secret key for HMAC validation (if not using public key)
            issuer: Expected issuer claim
            audience: Expected audience claim

        Note: This is a stub. Real implementation will use PyJWT library.
        """
        self.secret = secret
        self.issuer = issuer
        self.audience = audience
        self.provider = "JWT"

    async def validate(self, token: str) -> TokenPayload:
        """Validate JWT token.

        Args:
            token: JWT token string

        Returns:
            TokenPayload: Extracted and validated payload

        Raises:
            ValueError: If JWT is invalid, expired, or validation fails

        Note: This is a stub that always raises NotImplementedError.
        """
        raise NotImplementedError(
            "JWTTokenValidator is a stub. "
            "For production JWT validation, inject a real validator implementing TokenValidator protocol."
        )


__all__ = [
    "JWTTokenValidator",
    "TestTokenValidator",
    "TokenPayload",
    "TokenValidator",
]
