"""N+1 query regression test for reception.py's candidate listing.

`GET /api/v1/reception/candidates` (src/agente_ia_edu/api/routes/reception.py)
lists candidates with one batched query
(`ReceptionService.list_candidates`), then builds each item's response with
`_candidate_response()`, which called `ReceptionService.latest_diagnostic()`
- one extra SELECT per candidate - for every candidate in the page. A
receptionist's list of 30-40 pre-registrations therefore cost 30-40 extra
round-trips instead of one.

Proven here with a real in-process query counter (SQLAlchemy
`before_cursor_execute`), the same technique used for the live-Postgres
confirmation, against a disposable sqlite engine:

1. RED: calling `ReceptionService.latest_diagnostic()` once per candidate (the
   exact old `_candidate_response` pattern) grows the query count with the
   candidate count.
2. GREEN: the real HTTP route, after being switched to a batched
   `latest_diagnostics_for_candidates()` lookup, issues a FLAT query count
   regardless of how many candidates are returned.
"""

from __future__ import annotations

import asyncio
import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.routes.reception import reception_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import ReceptionCandidate, School
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.services.reception import ReceptionService


class QueryCounter:
    """Counts SQL statements executed against `engine` for the duration of a
    `with` block - the same before_cursor_execute instrumentation used
    throughout this audit, including for the live Postgres confirmation."""

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


class ReceptionCandidatesN1Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_candidates(self, session, school_id, count: int) -> list[ReceptionCandidate]:
        """`count` candidates whose diagnostic has already been released and
        activated (external_student_id set) - the case that actually drives a
        `latest_diagnostic()` query, unlike a bare PRE_REGISTRATION row where
        the method short-circuits without touching the database."""
        candidates = [
            ReceptionCandidate(
                school_id=school_id,
                full_name=f"Candidato {i}",
                phone=f"1198887{i:04d}",
                email=f"candidato{i}@example.com",
                academic_year="2026",
                unit_id="UNIDADE_CENTRO",
                segment_id="ENSINO_MEDIO",
                grade_level="3_SERIE",
                classroom_id="TURMA_3A",
                status="DIAGNOSTIC_RELEASED",
                external_student_id=f"student_{uuid4().hex[:8]}",
                created_by_external_id="secretary-a",
            )
            for i in range(count)
        ]
        session.add_all(candidates)
        await session.commit()
        return candidates

    def _app(self, session_factory, school_id):
        app = FastAPI()
        app.include_router(reception_router)
        app.dependency_overrides[get_session_factory] = lambda: session_factory
        app.dependency_overrides[get_current_authenticated_context] = (
            lambda: AuthenticatedUserContext(
                user_id="director-a",
                external_identity_id="director-a",
                role="DIRECTOR",
                school_id=school_id,
                scope_type="SCHOOL",
                scope_external_id=None,
            )
        )
        app.dependency_overrides[get_current_identity] = lambda: ExternalIdentityContext(
            provider="test", external_user_id="director-a", roles=("director",)
        )
        return app

    # -- RED: the old per-candidate `latest_diagnostic()` pattern -----------

    async def test_looping_latest_diagnostic_grows_with_candidate_count(self):
        """RED-style proof: this is exactly what the old `_candidate_response`
        did per item in the list-candidates loop. Query count must grow with
        the candidate count."""
        async with self.session_factory() as session:
            school = School(code="REC_RED", name="Escola RED")
            session.add(school)
            await session.commit()
            small = await self._seed_candidates(session, school.id, 3)
            service = ReceptionService(session)
            with QueryCounter(self.engine) as counter:
                for candidate in small:
                    await service.latest_diagnostic(candidate)
            small_count = counter.count

        async with self.session_factory() as session:
            school = School(code="REC_RED2", name="Escola RED 2")
            session.add(school)
            await session.commit()
            large = await self._seed_candidates(session, school.id, 30)
            service = ReceptionService(session)
            with QueryCounter(self.engine) as counter:
                for candidate in large:
                    await service.latest_diagnostic(candidate)
            large_count = counter.count

        self.assertGreater(
            large_count, small_count * 5,
            "latest_diagnostic() called once per candidate (the old "
            f"_candidate_response pattern) should scale with candidate count: "
            f"3 candidates -> {small_count} queries, 30 candidates -> {large_count} queries.",
        )

    # -- GREEN: the real HTTP route stays flat after the batched fix --------

    async def test_list_candidates_route_query_count_does_not_grow(self):
        async with self.session_factory() as session:
            school = School(code="REC_GREEN", name="Escola GREEN")
            session.add(school)
            await session.commit()
            school_id = school.id
            await self._seed_candidates(session, school_id, 3)

        app = self._app(self.session_factory, school_id)
        client = TestClient(app)
        with QueryCounter(self.engine) as counter:
            resp = client.get("/api/v1/reception/candidates", params={"school_id": str(school_id)})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(resp.json()), 3)
        small_count = counter.count
        client.close()
        app.dependency_overrides.clear()

        async with self.session_factory() as session:
            school = School(code="REC_GREEN2", name="Escola GREEN 2")
            session.add(school)
            await session.commit()
            school_id_2 = school.id
            await self._seed_candidates(session, school_id_2, 40)

        app2 = self._app(self.session_factory, school_id_2)
        client2 = TestClient(app2)
        with QueryCounter(self.engine) as counter:
            resp = client2.get("/api/v1/reception/candidates", params={"school_id": str(school_id_2)})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(resp.json()), 40)
        large_count = counter.count
        client2.close()
        app2.dependency_overrides.clear()

        self.assertEqual(
            small_count, large_count,
            "GET /api/v1/reception/candidates must issue the SAME number of "
            f"queries regardless of page size: 3 candidates -> {small_count} "
            f"queries, 40 candidates -> {large_count} queries.",
        )


if __name__ == "__main__":
    unittest.main()
