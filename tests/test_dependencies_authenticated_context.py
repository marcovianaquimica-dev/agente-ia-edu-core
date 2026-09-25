"""Coverage for get_current_authenticated_context() in
src/agente_ia_edu/api/dependencies.py.

This is the compatibility layer that turns the external identity protocol into
the richer AuthenticatedUserContext used for multi-tenant auth. No existing
test file calls it directly or asserts on its wiring (it is normally invoked
transitively, deep inside FastAPI's dependency graph, by many HTTP-level
tests) - so its own five-line body (resolve identity, open a session, ask
AuthorizationService to resolve the context, return it) had no direct test.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    reset_identity_provider,
)
from agente_ia_edu.identity import AuthenticatedUserContext


class _FakeAsyncSession:
    """Minimal stand-in that only needs to work as `async with ... as session`."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestGetCurrentAuthenticatedContext(unittest.TestCase):
    def setUp(self):
        reset_identity_provider()

    def tearDown(self):
        reset_identity_provider()

    def test_resolves_identity_opens_a_session_and_delegates_to_authorization_service(self):
        request = SimpleNamespace(headers={"authorization": "Bearer student:alice"})
        fake_session = _FakeAsyncSession()
        session_factory = mock.Mock(return_value=fake_session)

        expected_context = AuthenticatedUserContext(
            user_id="alice",
            external_identity_id="alice",
            role="STUDENT",
            school_id=None,
        )

        with mock.patch(
            "agente_ia_edu.api.dependencies.AuthorizationService"
        ) as MockAuthorizationService:
            instance = MockAuthorizationService.return_value
            instance.resolve_context = mock.AsyncMock(return_value=expected_context)

            result = asyncio.run(
                get_current_authenticated_context(
                    request=request, session_factory=session_factory
                )
            )

        # The session factory was actually called to open a session, and
        # AuthorizationService was constructed with that exact session.
        session_factory.assert_called_once_with()
        MockAuthorizationService.assert_called_once_with(fake_session)

        # The identity handed to resolve_context() is the one TestExternal-
        # IdentityProvider derives from the "Bearer student:alice" header -
        # proving get_current_identity() really ran inside this function,
        # not just that the mocks were wired up.
        instance.resolve_context.assert_called_once()
        (identity_arg,), _ = instance.resolve_context.call_args
        self.assertEqual(identity_arg.external_user_id, "alice")
        self.assertEqual(identity_arg.student_id, "alice")
        self.assertIn("student", identity_arg.roles)

        # And the function returns exactly what AuthorizationService produced.
        self.assertIs(result, expected_context)

    def test_propagates_an_unauthenticated_request_as_a_401(self):
        """No Authorization header -> TestExternalIdentityProvider still resolves
        (it never raises), but a provider that CAN reject (e.g. JWTIdentityProvider,
        via a ValueError) must have that surface as the 401 get_current_identity()
        already promises - not an opaque 500 from inside this wrapper."""
        from fastapi import HTTPException

        request = SimpleNamespace(headers={})
        session_factory = mock.Mock(return_value=_FakeAsyncSession())

        class _RejectingProvider:
            async def resolve(self, request):
                raise ValueError("no credentials supplied")

        from agente_ia_edu.api.dependencies import set_identity_provider

        set_identity_provider(_RejectingProvider())
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    get_current_authenticated_context(
                        request=request, session_factory=session_factory
                    )
                )
            self.assertEqual(ctx.exception.status_code, 401)
        finally:
            reset_identity_provider()


if __name__ == "__main__":
    unittest.main()
