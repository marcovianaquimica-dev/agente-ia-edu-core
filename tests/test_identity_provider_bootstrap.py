"""configure_identity_provider_from_env() - the opt-in switch between
TestExternalIdentityProvider (default, every dev/test run today) and a real
JWTIdentityProvider (only when a host explicitly configures JWT_AUTH_SECRET).

Read at CALL time, not at module-import time: create_app() is executed once
at import (``app = create_app()`` in api/app.py), so anything read only once
at import could never be exercised by a test that sets the env var
afterwards. This function is the seam that makes the wiring testable without
reloading the whole app module.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.api.dependencies import get_identity_provider, reset_identity_provider
from agente_ia_edu.auth.bootstrap import configure_identity_provider_from_env
from agente_ia_edu.auth.jwt_identity_provider import JWTIdentityProvider


class TestConfigureIdentityProviderFromEnv(unittest.TestCase):
    def tearDown(self):
        reset_identity_provider()

    def test_no_secret_in_env_leaves_the_default_test_provider_in_place(self):
        reset_identity_provider()
        before = get_identity_provider()

        configure_identity_provider_from_env(env={})

        self.assertIs(get_identity_provider(), before)

    def test_a_configured_secret_switches_to_a_real_jwt_provider(self):
        configure_identity_provider_from_env(env={"JWT_AUTH_SECRET": "a-real-secret-32-bytes-long-ok!!"})

        self.assertIsInstance(get_identity_provider(), JWTIdentityProvider)

    def test_issuer_and_audience_are_passed_through_when_present(self):
        configure_identity_provider_from_env(env={
            "JWT_AUTH_SECRET": "a-real-secret-32-bytes-long-ok!!",
            "JWT_AUTH_ISSUER": "https://auth.agenteiaedu.example",
            "JWT_AUTH_AUDIENCE": "agente-ia-edu-api",
        })

        provider = get_identity_provider()
        self.assertEqual(provider._validator.issuer, "https://auth.agenteiaedu.example")
        self.assertEqual(provider._validator.audience, "agente-ia-edu-api")

    def test_an_empty_secret_is_treated_the_same_as_no_secret(self):
        reset_identity_provider()
        before = get_identity_provider()

        configure_identity_provider_from_env(env={"JWT_AUTH_SECRET": ""})

        self.assertIs(get_identity_provider(), before)
