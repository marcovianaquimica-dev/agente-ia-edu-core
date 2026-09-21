"""get_current_identity() must turn a provider's ValueError into a clean 401,
not let it escape as an uncaught exception (FastAPI's default handling of an
unhandled exception raised inside a dependency is a raw 500).

This was already the function's own documented contract ("Raises:
HTTPException: If provider.resolve() fails") but nothing enforced it - the
gap was invisible until a provider that can actually reject a request
(JWTIdentityProvider) existed. TestExternalIdentityProvider never raises, so
no existing test exercised this path.
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from agente_ia_edu.api.app import app
from agente_ia_edu.api.dependencies import reset_identity_provider, set_identity_provider
from agente_ia_edu.identity import ExternalIdentityContext, ExternalIdentityRequest


class _RejectingProvider:
    async def resolve(self, request: ExternalIdentityRequest) -> ExternalIdentityContext:
        raise ValueError("token signature invalid")


class TestGetCurrentIdentityErrorHandling(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        reset_identity_provider()

    def test_a_provider_value_error_becomes_a_clean_401_not_a_500(self):
        set_identity_provider(_RejectingProvider())

        response = self.client.get(
            "/api/v1/student/dashboard", headers={"Authorization": "Bearer whatever"}
        )

        self.assertEqual(response.status_code, 401, response.text)
        self.assertNotEqual(response.status_code, 500)

    def test_the_default_test_provider_is_unaffected(self):
        reset_identity_provider()

        response = self.client.get(
            "/api/v1/student/dashboard", headers={"Authorization": "Bearer student:unlinked-test-user"}
        )

        self.assertNotEqual(response.status_code, 401)
        self.assertNotEqual(response.status_code, 500)
