"""Production ExternalIdentityProvider backed by JWTTokenValidator.

api/dependencies.py's get_current_identity() puts the raw Bearer token in
ExternalIdentityRequest.subject and calls provider.resolve(request) - this is
the counterpart to TestExternalIdentityProvider that a host wires in for real
traffic via set_identity_provider(). Unlike the test provider, nothing here
is trusted except what the signed token itself asserts: request.roles and
every other field the caller could set are ignored, on purpose - a caller
that could set its own roles would defeat the point of verifying a signature
at all.
"""

from __future__ import annotations

from agente_ia_edu.auth.token import JWTTokenValidator
from agente_ia_edu.identity import ExternalIdentityContext, ExternalIdentityRequest


class JWTIdentityProvider:
    """Resolves a Bearer JWT into an ExternalIdentityContext.

    ``roles`` decides ``student_id``/``teacher_id`` the same way
    TestExternalIdentityProvider does, so authorization code that branches on
    those two fields keeps working unchanged under either provider.
    """

    def __init__(self, secret: str, issuer: str | None = None, audience: str | None = None):
        self._validator = JWTTokenValidator(secret=secret, issuer=issuer, audience=audience)

    async def resolve(self, request: ExternalIdentityRequest) -> ExternalIdentityContext:
        token = request.subject
        if not token:
            raise ValueError("Missing bearer token")

        payload = await self._validator.validate(token)

        role = payload.roles[0].lower() if payload.roles else None
        student_id = payload.subject if role == "student" else None
        teacher_id = payload.subject if role == "teacher" else None

        return ExternalIdentityContext(
            provider=payload.provider,
            external_user_id=payload.subject,
            student_id=student_id,
            teacher_id=teacher_id,
            roles=payload.roles,
            metadata={**payload.metadata, "email": payload.email, "name": payload.name},
        )


__all__ = ["JWTIdentityProvider"]
