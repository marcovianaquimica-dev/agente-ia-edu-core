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

import jwt

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

    HS256 with a shared secret. This is the mechanism to reach for when the
    core itself (or a login service under the same operator's control) issues
    the tokens - no external identity provider has been chosen yet. A future
    RS256/JWKS validator for a specific provider (Auth0, Cognito, ...) is a
    different class, not an extension of this one: the two verify against a
    different kind of key entirely.

    Every claim PyJWT can check is turned on: signature, expiration, and
    issuer/audience whenever those are configured. ``alg`` is pinned to
    ``["HS256"]`` explicitly - never derived from the token's own header -
    which is what closes the classic "alg=none" forgery (a token that drops
    the signature and asks the verifier to trust it anyway).
    """

    def __init__(self, secret: str, issuer: str | None = None, audience: str | None = None):
        """Initialize JWT validator.

        Args:
            secret: Shared HMAC secret. Required - there is no anonymous mode.
            issuer: Expected ``iss`` claim. When set, a token without a
                matching issuer is rejected; when ``None``, the issuer is not
                checked at all.
            audience: Expected ``aud`` claim, same on/off behaviour as issuer.
        """
        if not secret:
            raise ValueError("JWTTokenValidator requires a non-empty secret")
        self.secret = secret
        self.issuer = issuer
        self.audience = audience
        self.provider = "JWT"

    async def validate(self, token: str) -> TokenPayload:
        """Validate a JWT and extract its claims.

        Args:
            token: JWT token string

        Returns:
            TokenPayload: Extracted and validated payload

        Raises:
            ValueError: If the token is malformed, unsigned, signed with the
                wrong key, expired, or fails an issuer/audience check that was
                configured. Never returns a payload for a token it could not
                fully verify.
        """
        try:
            claims = jwt.decode(
                token,
                self.secret,
                algorithms=["HS256"],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "require": ["sub", "exp"],
                    "verify_iss": self.issuer is not None,
                    "verify_aud": self.audience is not None,
                },
            )
        except jwt.PyJWTError as exc:
            raise ValueError(f"Invalid JWT: {exc}") from exc

        subject = claims.pop("sub")
        email = claims.pop("email", None)
        name = claims.pop("name", None)
        roles = tuple(claims.pop("roles", ()) or ())
        for standard_claim in ("iat", "exp", "iss", "aud", "nbf", "jti"):
            claims.pop(standard_claim, None)

        return TokenPayload(
            subject=subject,
            email=email,
            name=name,
            provider=self.provider,
            roles=roles,
            metadata=claims,
        )


__all__ = [
    "JWTTokenValidator",
    "TestTokenValidator",
    "TokenPayload",
    "TokenValidator",
]
