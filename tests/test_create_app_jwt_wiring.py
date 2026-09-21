"""End-to-end proof that create_app() actually wires JWT_AUTH_SECRET through
to the real identity provider - not just that configure_identity_provider_
from_env() works in isolation (test_identity_provider_bootstrap.py already
covers that unit)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_identity_provider, reset_identity_provider
from agente_ia_edu.auth.jwt_identity_provider import JWTIdentityProvider


class TestCreateAppJWTWiring(unittest.TestCase):
    def tearDown(self):
        reset_identity_provider()

    def test_create_app_switches_to_jwt_when_the_env_var_is_set(self):
        with mock.patch.dict(os.environ, {"JWT_AUTH_SECRET": "a-real-secret-32-bytes-long-ok!!"}):
            create_app()

        self.assertIsInstance(get_identity_provider(), JWTIdentityProvider)

    def test_create_app_leaves_the_test_provider_when_unset(self):
        reset_identity_provider()
        before = get_identity_provider()

        with mock.patch.dict(os.environ):
            os.environ.pop("JWT_AUTH_SECRET", None)
            create_app()

        self.assertIs(get_identity_provider(), before)
