"""Test-session bootstrap: DATABASE_URL is required by every test that goes
through the real FastAPI app (TestClient(app)) or opens a real DB session,
but nothing in this project auto-loads .env - a bare `pytest` invocation
silently fails ~70 of those tests with a DatabaseConfigurationError instead
of a clear "you forgot to configure your environment" message. This fixture
builds DATABASE_URL from the same .env POSTGRES_* pieces docker-compose.yml
uses, matching its "5433:5432" host-port mapping, so `pytest` works out of
the box for local development against the docker-compose Postgres container.

Never overrides an already-exported DATABASE_URL (CI, or a developer's own
setup, always wins).
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_ENV_FILE = _ROOT / ".env"
_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = "5433"  # docker-compose.yml maps "5433:5432"


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


if "DATABASE_URL" not in os.environ:
    env_values = _load_env_file(_ENV_FILE)
    user = env_values.get("POSTGRES_USER")
    password = env_values.get("POSTGRES_PASSWORD")
    database = env_values.get("POSTGRES_DB")
    if user and password and database:
        os.environ["DATABASE_URL"] = (
            f"postgresql+psycopg://{user}:{password}@{_DEFAULT_HOST}:{_DEFAULT_PORT}/{database}"
        )
