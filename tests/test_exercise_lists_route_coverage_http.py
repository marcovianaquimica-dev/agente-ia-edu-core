"""
HTTP-level route-handler coverage for src/agente_ia_edu/api/routes/exercise_lists.py.

Overnight bug-hunt campaign, "smaller route files" zone. Unlike
tests/test_exercise_lists_http.py (which builds its session factory with
expire_on_commit=False and therefore cannot reproduce the MissingGreenlet /
phantom-commit bug class found repeatedly in assessments.py), this file uses
a production-fidelity session factory: async_sessionmaker(engine,
class_=AsyncSession) with NO expire_on_commit override, matching
create_session_factory() in src/agente_ia_edu/db/session.py (called with no
options in api/dependencies.py, so SQLAlchemy's async default
expire_on_commit=True applies).

Initial suspect (later disproven with real evidence, per this campaign's
verification rule - see below): create_exercise_list calls
`list_obj = await service.create_list(...)`, then `await session.commit()`,
then immediately reads ten synchronous attributes off `list_obj` (id, title,
description, status, school_id, institution_id,
created_by_external_identity, owner_external_id, visibility_scope,
origin_type) with no intervening `session.refresh()` - which matches the
commit-then-read-on-a-loaded-object shape that was a real bug 3x in
assessments.py.

It is NOT a bug here: `ExerciseListPersistenceService.create_list()` (via
`create_assessment()` in services/assessments.py) returns a plain
`@dataclass Assessment` (services/assessments.py ~line 12), not the
SQLAlchemy-mapped `agente_ia_edu.db.models.Assessment` ORM entity. That
dataclass is a detached snapshot, never added to the session's identity map,
so `session.commit()`'s expire_on_commit=True has nothing to expire on it -
attribute reads afterward are always plain in-memory reads, never a lazy
DB reload. Confirmed empirically: this file's production-fidelity fixture
(expire_on_commit=True, matching prod) exercises this exact path and it
passes cleanly with no MissingGreenlet. Kept as a regression test / as
documentation of why this particular commit site is safe despite looking
like the buggy pattern at a glance.
"""

import asyncio
import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_authenticated_context, get_session_factory
from agente_ia_edu.api.routes.exercise_lists import router as exercise_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.identity import AuthenticatedUserContext


class ExerciseListsRouteCoverageHTTP(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            # expire_on_commit=True mirrors production (create_session_factory()
            # is called with no options) - deliberately NOT relaxed to False,
            # unlike tests/test_exercise_lists_http.py's fixture, because that
            # relaxation is exactly what papers over the MissingGreenlet bug
            # class this file is hunting for.
            factory = async_sessionmaker(engine, class_=AsyncSession)
            return engine, factory

        self.engine, self.session_factory = asyncio.run(setup())
        self.school_a = uuid4()

        self.context = {
            "value": AuthenticatedUserContext(
                user_id="teacher-a",
                external_identity_id="teacher-a",
                role="TEACHER",
                school_id=str(self.school_a),
                scope_type="SCHOOL",
                scope_external_id=str(self.school_a),
                is_active=True,
            )
        }
        app = FastAPI()
        app.include_router(exercise_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_authenticated_context] = lambda: self.context["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    # -- create_exercise_list (POST) -------------------------------------------

    def test_create_exercise_list_returns_clean_200_not_500(self):
        """Reproduces (pre-fix) / guards against (post-fix) the
        commit-then-read-expired-attribute bug in create_exercise_list."""
        response = self.client.post(
            "/api/v1/exercise-lists",
            json={
                "title": "Lista de Exercicios",
                "description": "Descricao da lista",
                "visibility_scope": "SCHOOL",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["title"], "Lista de Exercicios")
        self.assertEqual(body["description"], "Descricao da lista")
        self.assertEqual(body["school_id"], str(self.school_a))
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["created_by_external_identity"], "teacher-a")
        self.assertEqual(body["owner_external_id"], "teacher-a")
        self.assertEqual(body["visibility_scope"], "SCHOOL")
        self.assertEqual(body["origin_type"], "SCHOOL")

    def test_create_exercise_list_row_is_actually_persisted(self):
        """Confirms the write really landed (independent of the response body),
        i.e. a fix must not silently drop the commit while avoiding the crash."""
        response = self.client.post(
            "/api/v1/exercise-lists",
            json={"title": "Lista Persistida"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        list_id = response.json()["id"]

        fetch = self.client.get(f"/api/v1/exercise-lists/{list_id}")
        self.assertEqual(fetch.status_code, 200, fetch.text)
        self.assertEqual(fetch.json()["title"], "Lista Persistida")


if __name__ == "__main__":
    unittest.main()
