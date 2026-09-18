"""Race-hardened create/drop helpers shared by the "_postgresql" E2E test
classes (each owns a dedicated, disposable database on the shared Postgres
server at port 5433, dropped and recreated fresh per test method).

Found investigating recurring, non-deterministic failures in Postgres-backed
E2E tests that only appeared in full-suite runs (never in isolation), with a
different specific combination of failing tests each time - not cross-test
data pollution (every class already uses a distinct database name and drops/
recreates it per test), but a race in the drop/create sequence itself:
``pg_terminate_backend`` signals backends to close, it does not wait for them
to actually finish, so the ``DROP DATABASE`` that immediately follows can
transiently fail with "database ... is being accessed by other users" under
the connection churn of a big full-suite run. Every existing copy of this
logic wrapped that in a bare ``except Exception: pass``, silently leaving the
stale database in place - so the NEXT ``CREATE DATABASE`` (uncaught, in the
same class's next test or another class's ``setUp``) failed with "database
... already exists". Retrying the drop, and retrying create-with-a-fresh-drop
on collision, closes that race instead of swallowing it.
"""

from __future__ import annotations

import time

from sqlalchemy import create_engine, text


def _admin_engine(admin_url: str):
    return create_engine(
        admin_url,
        connect_args={"autocommit": True},
        execution_options={"isolation_level": "AUTOCOMMIT"},
    )


def drop_database(admin_url: str, database_name: str, *, retries: int = 5, retry_delay: float = 0.3) -> None:
    """Terminate active connections and drop ``database_name``, retrying on
    transient lock contention instead of giving up after one attempt."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        engine = _admin_engine(admin_url)
        try:
            with engine.connect() as connection:
                connection.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{database_name}' AND pid <> pg_backend_pid()"
                ))
                connection.execute(text(f"DROP DATABASE IF EXISTS {database_name}"))
            return
        except Exception as exc:  # noqa: BLE001 - retried below, never silently swallowed
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(retry_delay)
        finally:
            engine.dispose()
    raise RuntimeError(
        f"Could not drop database {database_name!r} after {retries} attempts"
    ) from last_exc


def create_database(admin_url: str, database_name: str, *, retries: int = 3, retry_delay: float = 0.3) -> None:
    """CREATE DATABASE, retrying (with a fresh drop first) if it still exists
    - a straggler from a still-in-flight teardown elsewhere in a heavily
    concurrent full-suite run."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        engine = _admin_engine(admin_url)
        try:
            with engine.connect() as connection:
                connection.execute(text(f"CREATE DATABASE {database_name}"))
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries - 1:
                drop_database(admin_url, database_name, retries=2, retry_delay=retry_delay)
                time.sleep(retry_delay)
        finally:
            engine.dispose()
    raise RuntimeError(
        f"Could not create database {database_name!r} after {retries} attempts"
    ) from last_exc
