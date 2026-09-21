"""JWTIdentityProvider - bridges JWTTokenValidator into the
ExternalIdentityProvider protocol that api/dependencies.py actually calls.

get_current_identity() passes the raw Bearer token as
ExternalIdentityRequest.subject - this provider's job is to validate that
token for real and never fall back to trusting it verbatim, unlike
TestExternalIdentityProvider.
"""

from __future__ import annotations

import time
import unittest

import jwt

from agente_ia_edu.auth.jwt_identity_provider import JWTIdentityProvider
from agente_ia_edu.identity import ExternalIdentityContext, ExternalIdentityRequest

SECRET = "test-shared-secret-do-not-use-in-prod-padded-to-32-bytes-plus"


def _make_token(**claim_overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": "ext-teacher-42",
        "roles": ["TEACHER"],
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, SECRET, algorithm="HS256")


class TestJWTIdentityProvider(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_a_valid_token_into_an_identity_context(self):
        provider = JWTIdentityProvider(secret=SECRET)
        token = _make_token()
        request = ExternalIdentityRequest(provider="JWT", external_user_id=token, subject=token)

        context = await provider.resolve(request)

        self.assertIsInstance(context, ExternalIdentityContext)
        self.assertEqual(context.external_user_id, "ext-teacher-42")
        self.assertEqual(context.roles, ("TEACHER",))
        self.assertEqual(context.provider, "JWT")
        self.assertEqual(context.teacher_id, "ext-teacher-42")
        self.assertIsNone(context.student_id)

    async def test_sets_student_id_for_a_student_role(self):
        provider = JWTIdentityProvider(secret=SECRET)
        token = _make_token(sub="ext-student-7", roles=["STUDENT"])
        request = ExternalIdentityRequest(provider="JWT", external_user_id=token, subject=token)

        context = await provider.resolve(request)

        self.assertEqual(context.student_id, "ext-student-7")
        self.assertIsNone(context.teacher_id)

    async def test_rejects_a_missing_token(self):
        provider = JWTIdentityProvider(secret=SECRET)
        request = ExternalIdentityRequest(provider="JWT", external_user_id="unknown", subject=None)

        with self.assertRaises(ValueError):
            await provider.resolve(request)

    async def test_rejects_a_tampered_token(self):
        provider = JWTIdentityProvider(secret=SECRET)
        token = _make_token()
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        request = ExternalIdentityRequest(provider="JWT", external_user_id=tampered, subject=tampered)

        with self.assertRaises(ValueError):
            await provider.resolve(request)

    async def test_never_trusts_a_role_claimed_outside_the_signed_token(self):
        """Unlike TestExternalIdentityProvider, nothing in the request other
        than the signed token itself may influence roles or identity."""
        provider = JWTIdentityProvider(secret=SECRET)
        token = _make_token(sub="ext-teacher-42", roles=["TEACHER"])
        request = ExternalIdentityRequest(
            provider="JWT", external_user_id=token, subject=token,
            roles=("PLATFORM_ADMIN",),
        )

        context = await provider.resolve(request)

        self.assertEqual(context.roles, ("TEACHER",))
