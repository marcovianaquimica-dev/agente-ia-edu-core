"""HTTP-layer tests for router (src/agente_ia_edu/api/routes/exercise_lists.py).

tests/test_exercise_lists.py only calls ExerciseListPersistenceService
directly. tests/test_bloco_c_http.py drives the real ASGI route for
create-success (200) and the cross-school 403 on get - but several of the
route handler's own branches were still never exercised through HTTP:
- create_exercise_list's "no school_id -> 403" guard
- list_exercise_lists entirely (both the "no school_id -> empty list" early
  return and the real listing/filtering body)
- get_exercise_list's "no school_id -> 403" guard, its 404, and its success
  (200) return path
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


class ExerciseListsHTTP(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            return engine, factory

        self.engine, self.session_factory = asyncio.run(setup())
        self.school_a = uuid4()
        self.school_b = uuid4()

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

    def _no_school_context(self):
        self.context["value"] = AuthenticatedUserContext(
            user_id="independent-student",
            external_identity_id="independent-student",
            role="STUDENT",
            school_id=None,
            scope_type="PLATFORM",
            is_active=True,
        )

    # -- create_exercise_list (POST) -------------------------------------------

    def test_create_without_school_context_returns_403(self):
        self._no_school_context()
        response = self.client.post(
            "/api/v1/exercise-lists",
            json={"title": "Lista sem escola"},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("school context", response.json()["detail"])

    # -- list_exercise_lists (GET) ----------------------------------------------

    def test_list_without_school_context_returns_empty(self):
        self._no_school_context()
        response = self.client.get("/api/v1/exercise-lists")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"items": [], "total": 0})

    def test_list_returns_only_own_school_lists(self):
        create_a = self.client.post(
            "/api/v1/exercise-lists", json={"title": "Lista da Escola A"}
        )
        self.assertEqual(create_a.status_code, 200, create_a.text)

        # A list belonging to a different school must not leak into school A's listing.
        self.context["value"] = AuthenticatedUserContext(
            user_id="teacher-b", external_identity_id="teacher-b", role="TEACHER",
            school_id=str(self.school_b), scope_type="SCHOOL", scope_external_id=str(self.school_b),
            is_active=True,
        )
        self.client.post("/api/v1/exercise-lists", json={"title": "Lista da Escola B"})

        self.context["value"] = AuthenticatedUserContext(
            user_id="teacher-a", external_identity_id="teacher-a", role="TEACHER",
            school_id=str(self.school_a), scope_type="SCHOOL", scope_external_id=str(self.school_a),
            is_active=True,
        )
        response = self.client.get("/api/v1/exercise-lists")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["title"], "Lista da Escola A")

    # -- get_exercise_list (GET /{id}) -------------------------------------------

    def test_get_without_school_context_returns_403(self):
        self._no_school_context()
        response = self.client.get(f"/api/v1/exercise-lists/{uuid4()}")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("No school context", response.json()["detail"])

    def test_get_nonexistent_returns_404(self):
        response = self.client.get(f"/api/v1/exercise-lists/{uuid4()}")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertIn("not found", response.json()["detail"])

    def test_get_own_school_list_returns_200(self):
        create_resp = self.client.post(
            "/api/v1/exercise-lists", json={"title": "Lista para leitura"}
        )
        list_id = create_resp.json()["id"]
        response = self.client.get(f"/api/v1/exercise-lists/{list_id}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["id"], list_id)
        self.assertEqual(body["title"], "Lista para leitura")
        self.assertEqual(body["school_id"], str(self.school_a))


if __name__ == "__main__":
    unittest.main()
