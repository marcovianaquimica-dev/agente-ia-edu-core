"""Real JWT validation (JWTTokenValidator) - the stub is filled here.

HS256 with a shared secret: the simplest mechanism that needs no external
identity provider decision (Auth0/Cognito/etc. use RS256 + JWKS, which is a
separate, provider-specific integration to build once a real provider is
chosen). Covers what the TokenValidator protocol promises: signature,
expiration, issuer and audience are all verified, and nothing is ever
accepted without validation.
"""

from __future__ import annotations

import time
import unittest

import jwt

from agente_ia_edu.auth.token import JWTTokenValidator, TokenPayload

SECRET = "test-shared-secret-do-not-use-in-prod-padded-to-32-bytes-plus"


def _make_token(secret: str = SECRET, **claim_overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": "external-user-123",
        "email": "prof@escola.example",
        "name": "Professor Teste",
        "roles": ["TEACHER"],
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, secret, algorithm="HS256")


class TestJWTTokenValidator(unittest.IsolatedAsyncioTestCase):
    async def test_validates_a_well_formed_token_and_extracts_claims(self):
        validator = JWTTokenValidator(secret=SECRET)
        token = _make_token()

        payload = await validator.validate(token)

        self.assertIsInstance(payload, TokenPayload)
        self.assertEqual(payload.subject, "external-user-123")
        self.assertEqual(payload.email, "prof@escola.example")
        self.assertEqual(payload.name, "Professor Teste")
        self.assertEqual(payload.roles, ("TEACHER",))
        self.assertEqual(payload.provider, "JWT")

    async def test_rejects_a_token_signed_with_the_wrong_secret(self):
        validator = JWTTokenValidator(secret=SECRET)
        token = _make_token(secret="a-different-secret")

        with self.assertRaises(ValueError):
            await validator.validate(token)

    async def test_rejects_an_expired_token(self):
        validator = JWTTokenValidator(secret=SECRET)
        past = int(time.time()) - 10
        token = _make_token(iat=past - 3600, exp=past)

        with self.assertRaises(ValueError):
            await validator.validate(token)

    async def test_rejects_a_token_missing_the_subject_claim(self):
        validator = JWTTokenValidator(secret=SECRET)
        now = int(time.time())
        token = jwt.encode({"iat": now, "exp": now + 3600}, SECRET, algorithm="HS256")

        with self.assertRaises(ValueError):
            await validator.validate(token)

    async def test_rejects_malformed_garbage_that_is_not_a_jwt_at_all(self):
        validator = JWTTokenValidator(secret=SECRET)

        with self.assertRaises(ValueError):
            await validator.validate("not-a-jwt-at-all")

    async def test_enforces_issuer_when_configured(self):
        validator = JWTTokenValidator(secret=SECRET, issuer="https://auth.agenteiaedu.example")
        wrong_issuer = _make_token(iss="https://some-other-issuer.example")

        with self.assertRaises(ValueError):
            await validator.validate(wrong_issuer)

        right_issuer = _make_token(iss="https://auth.agenteiaedu.example")
        payload = await validator.validate(right_issuer)
        self.assertEqual(payload.subject, "external-user-123")

    async def test_enforces_audience_when_configured(self):
        validator = JWTTokenValidator(secret=SECRET, audience="agente-ia-edu-api")
        wrong_audience = _make_token(aud="some-other-api")

        with self.assertRaises(ValueError):
            await validator.validate(wrong_audience)

        right_audience = _make_token(aud="agente-ia-edu-api")
        payload = await validator.validate(right_audience)
        self.assertEqual(payload.subject, "external-user-123")

    async def test_rejects_the_none_algorithm_attack(self):
        """A token that declares alg=none and carries no signature must never
        be accepted, regardless of what claims it asserts."""
        validator = JWTTokenValidator(secret=SECRET)
        now = int(time.time())
        forged = jwt.encode(
            {"sub": "attacker", "roles": ["PLATFORM_ADMIN"], "iat": now, "exp": now + 3600},
            key=None, algorithm="none",
        )

        with self.assertRaises(ValueError):
            await validator.validate(forged)

    async def test_roles_default_to_empty_tuple_when_claim_is_absent(self):
        validator = JWTTokenValidator(secret=SECRET)
        now = int(time.time())
        token = jwt.encode({"sub": "u1", "iat": now, "exp": now + 3600}, SECRET, algorithm="HS256")

        payload = await validator.validate(token)
        self.assertEqual(payload.roles, ())

    async def test_unknown_extra_claims_land_in_metadata(self):
        validator = JWTTokenValidator(secret=SECRET)
        token = _make_token(school_id="escola-partner-123")

        payload = await validator.validate(token)
        self.assertEqual(payload.metadata.get("school_id"), "escola-partner-123")
