"""Live-Postgres confirmation for the reception-candidates-listing N+1
(src/agente_ia_edu/api/routes/reception.py::list_reception_candidates).

Runs the REAL `GET /api/v1/reception/candidates` route end-to-end - the exact
call site flagged for this zone: `_candidate_response()` used to call
`ReceptionService.latest_diagnostic()` once PER candidate in the response
loop - against a disposable PostgreSQL database on port 5433, never the
shared dev `agente_ia_edu` database, counting real SQL statements with a
`before_cursor_execute` listener.

Scenario: one school with many DIAGNOSTIC_RELEASED reception candidates
(external_student_id set, so latest_diagnostic() actually queries), each with
a real InitialDiagnostic row. Two sizes are run back to back (SMALL and
LARGE) and the route's query count must not grow with the candidate count -
the signature of the fix.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
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
from agente_ia_edu.api.dependencies import (  # noqa: E402
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.db.base import Base  # noqa: E402
from agente_ia_edu.db.models import InitialDiagnostic, ReceptionCandidate, School  # noqa: E402
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext  # noqa: E402

DB_NAME = "agente_ia_edu_perf_reception_candidates_n1"
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


async def build_scenario(session_factory, num_candidates: int):
    async with session_factory() as session:
        school = School(code=f"PERF_{uuid.uuid4().hex[:8]}", name="Escola Perf Reception")
        session.add(school)
        await session.flush()

        candidates = []
        for i in range(num_candidates):
            student_id = f"perf_student_{uuid.uuid4().hex[:8]}"
            candidate = ReceptionCandidate(
                school_id=school.id,
                full_name=f"Candidato Perf {i}",
                phone=f"11999{i:06d}",
                email=f"perf.candidato.{i}.{uuid.uuid4().hex[:6]}@example.com",
                academic_year="2026",
                unit_id="UNIDADE_CENTRO",
                segment_id="ENSINO_MEDIO",
                grade_level="3_SERIE",
                classroom_id="TURMA_3A",
                status="DIAGNOSTIC_RELEASED",
                external_student_id=student_id,
                created_by_external_id="perf-director",
            )
            session.add(candidate)
            candidates.append((candidate, student_id))
        await session.flush()

        for candidate, student_id in candidates:
            session.add(InitialDiagnostic(
                student_id=student_id,
                school_id=school.id,
                academic_year="2026",
                status="COMPLETED",
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                total_questions_asked=10,
            ))
        await session.commit()
        return school.id, num_candidates


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
        for label, n in (("SMALL", 4), ("LARGE", 40)):
            school_id, expected_count = await build_scenario(session_factory, n)
            app.dependency_overrides[get_current_authenticated_context] = (
                lambda sid=school_id: AuthenticatedUserContext(
                    user_id="perf-director", external_identity_id="perf-director",
                    role="DIRECTOR", school_id=sid, scope_type="SCHOOL", scope_external_id=None,
                )
            )
            app.dependency_overrides[get_current_identity] = lambda: ExternalIdentityContext(
                provider="test", external_user_id="perf-director",
            )
            client = TestClient(app)
            with QueryCounter(engine) as counter:
                resp = client.get(
                    "/api/v1/reception/candidates",
                    params={"school_id": str(school_id)},
                    headers={"Authorization": "Bearer perf-director"},
                )
            assert resp.status_code == 200, (resp.status_code, resp.text)
            body = resp.json()
            assert len(body) == expected_count, f"{label}: expected {expected_count} candidates, got {len(body)}"
            assert all(item["diagnostic_status"] == "COMPLETED" for item in body), (
                f"{label}: every candidate should carry its diagnostic status "
                "(the batched lookup must preserve exact per-candidate results)"
            )
            results[label] = counter.count
            print(f"{label}: {n} candidates -> {counter.count} SQL statements, "
                  f"{len(body)} candidates returned (all with diagnostic resolved)")

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
