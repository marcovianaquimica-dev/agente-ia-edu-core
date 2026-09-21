"""Wire a real identity provider into api/dependencies.py from environment
configuration, if the host has configured one.

Opt-in by design: the default (JWT_AUTH_SECRET unset, which is every
existing dev/test run) leaves TestExternalIdentityProvider - or whatever
provider a host already set some other way - completely untouched. Only a
non-empty JWT_AUTH_SECRET switches the app to real JWT verification. This is
the only safe default for a codebase where set_identity_provider() with a
real provider has never been called anywhere: an opt-OUT design (real
verification unless some flag disables it) would risk quietly demanding a
secret that doesn't exist yet in every environment that imports api/app.py,
including this repository's own ~2000-test suite.
"""

from __future__ import annotations

import os
from typing import Mapping

from agente_ia_edu.api.dependencies import set_identity_provider
from agente_ia_edu.auth.jwt_identity_provider import JWTIdentityProvider


def configure_identity_provider_from_env(env: Mapping[str, str] | None = None) -> None:
    """Call once at app startup. Reads ``JWT_AUTH_SECRET``/``JWT_AUTH_ISSUER``/
    ``JWT_AUTH_AUDIENCE`` from ``env`` (``os.environ`` by default) and, only
    when a non-empty secret is present, switches the application's identity
    provider to a real ``JWTIdentityProvider``.
    """
    source = env if env is not None else os.environ
    secret = source.get("JWT_AUTH_SECRET", "")
    if not secret:
        return

    set_identity_provider(
        JWTIdentityProvider(
            secret=secret,
            issuer=source.get("JWT_AUTH_ISSUER") or None,
            audience=source.get("JWT_AUTH_AUDIENCE") or None,
        )
    )


__all__ = ["configure_identity_provider_from_env"]
