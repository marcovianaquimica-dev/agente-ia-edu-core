"""Live-Postgres confirmation for the admin/authorization/pedagogical-universe
N+1 audit (services/admin.py, authorization.py, discipline_gate.py,
pedagogical_universe.py).

Runs the REAL `GET /api/v1/catalog/nodes` cascade route end-to-end (the exact
call site flagged for this zone: catalog.py line ~443, which used to call
`PedagogicalUniverseService.contains_catalog_node()` once PER candidate node)
against a disposable PostgreSQL database on port 5433 - never the shared dev
`agente_ia_edu` database - counting real SQL statements with a
`before_cursor_execute` listener, exactly as measured live. The database is
created fresh and DROPPED at the end regardless of outcome, so it leaves no
residue in Postgres.

Scenario: one PLATFORM universe scoped (AREA, include_descendants=True) to a
DISCIPLINE holding many CONTENT children. Two sizes are run back to back
(SMALL and LARGE) and the query count for the route must not grow with the
candidate count - the signature of the fix.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _c in (Path.cwd(), _HERE.parents[1]):
    if (_c / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_c / "src"))
        break

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from agente_ia_edu.api.app import create_app  # noqa: E402
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory  # noqa: E402
from agente_ia_edu.db.base import Base  # noqa: E402
from agente_ia_edu.db.models import CatalogNode  # noqa: E402
from agente_ia_edu.identity import ExternalIdentityContext  # noqa: E402
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService  # noqa: E402

DB_NAME = "agente_ia_edu_perf_admin_zone_catalog_cascade"
PG_USER = os.getenv("POSTGRES_USER", "agenteedu")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
ADMIN_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@localhost:5433/postgres"
ASYNC_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@localhost:5433/{DB_NAME}"


def admin_execute(statement: str) -> None:
    engine = create_engine(ADMIN_URL, connect_args={"autocommit": True}, execution_options={"isolation_level": "AUTOCOMMIT"})
    try:
        with engine.connect() as conn:
            conn.execute(text(statement))
    finally:
        engine.dispose()


def drop_database() -> None:
    try:
        admin_execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{DB_NAME}' AND pid <> pg_backend_pid()"
        )
        admin_execute(f"DROP DATABASE IF EXISTS {DB_NAME}")
    except Exception as exc:  # pragma: no cover
        print(f"[cleanup warning] {exc}")


class QueryCounter:
    def __init__(self, engine):
        self.engine = engine
        self.count = 0
        self._listener = None

    def __enter__(self):
        def _count(conn, cursor, statement, parameters, context, executemany):
            self.count += 1
        self._listener = _count
        event.listen(self.engine.sync_engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine.sync_engine, "before_cursor_execute", self._listener)


async def build_scenario(session_factory, num_contents: int):
    async with session_factory() as session:
        area = CatalogNode(node_type="AREA", name="Ciências da Natureza", active=True)
        session.add(area)
        await session.flush()
        area.root_id = area.id
        discipline = CatalogNode(parent_id=area.id, root_id=area.id, node_type="DISCIPLINE", name="Química", active=True)
        session.add(discipline)
        await session.flush()
        contents = [
            CatalogNode(parent_id=discipline.id, root_id=area.id, node_type="CONTENT", name=f"Conteúdo {i}", active=True)
            for i in range(num_contents)
        ]
        session.add_all(contents)
        await session.commit()

        service = PedagogicalUniverseService(session)
        universe = await service.create_universe(
            external_id=f"PERF_{uuid.uuid4().hex[:8]}", slug=f"perf-{uuid.uuid4().hex[:8]}",
            name="Perf Universe", owner_type="PLATFORM", owner_external_id=None,
            performed_by_external_id="admin", status="ACTIVE",
        )
        await service.add_catalog_scope(universe_id=universe.id, catalog_node_id=area.id, scope_kind="AREA")
        identity_id = f"perf_user_{uuid.uuid4().hex[:8]}"
        await service.bind(universe_id=universe.id, subject_type="EXTERNAL_IDENTITY", subject_external_id=identity_id)
        return discipline.id, identity_id, len(contents)


async def main() -> int:
    try:
        admin_execute("SELECT 1")
    except Exception as exc:
        print(f"PostgreSQL on port 5433 unavailable, skipping live confirmation: {exc}")
        return 1

    drop_database()
    admin_execute(f"CREATE DATABASE {DB_NAME}")
    engine = create_async_engine(ASYNC_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        app = create_app()
        app.dependency_overrides[get_session_factory] = lambda: session_factory

        results = {}
        for label, n in (("SMALL", 4), ("LARGE", 80)):
            discipline_id, identity_id, expected_count = await build_scenario(session_factory, n)
            app.dependency_overrides[get_current_identity] = lambda uid=identity_id: ExternalIdentityContext(
                provider="test", external_user_id=uid,
            )
            client = TestClient(app)
            with QueryCounter(engine) as counter:
                resp = client.get(
                    "/api/v1/catalog/nodes",
                    params={"parent_id": str(discipline_id), "limit": 100},
                    headers={"Authorization": f"Bearer {identity_id}"},
                )
            assert resp.status_code == 200, (resp.status_code, resp.text)
            body = resp.json()
            assert len(body) == expected_count, f"{label}: expected {expected_count} nodes, got {len(body)}"
            results[label] = counter.count
            print(f"{label}: {n} candidate nodes -> {counter.count} SQL statements, "
                  f"{len(body)} nodes returned (all in scope, as expected)")

        print()
        if results["SMALL"] == results["LARGE"]:
            print(f"PASS: query count flat regardless of candidate count "
                  f"({results['SMALL']} queries both times) - the fix holds against real Postgres.")
            return 0
        else:
            print(f"FAIL: query count grew with candidate count "
                  f"(SMALL={results['SMALL']}, LARGE={results['LARGE']}) - N+1 still present.")
            return 1
    finally:
        await engine.dispose()
        drop_database()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
