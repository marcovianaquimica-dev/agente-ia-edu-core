"""HTTP-layer tests for teacher_router / coordination_router in
src/agente_ia_edu/api/routes/teaching_context.py.

These exercise the FastAPI route handlers themselves - request parsing, the
ScopeAuthorizationError -> HTTPException(403) mapping, the ValueError ->
HTTPException(400) mapping, and 404 handling - which the existing test suite
never touched: tests/test_teaching_context.py calls TeachingContextService
directly, and tests/test_r0_pedagogical_context_route_authorization.py only
covers get_classroom_pedagogical_context (pedagogical_context_router), not
the teacher_router (/lessons) or coordination_router (/pedagogical-context)
endpoints exercised here.
"""

import asyncio
import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.teaching_context import (
    coordination_router,
    teacher_router,
)
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService


class TeachingContextHTTP(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                admin_service = PlatformAdminService(session)
                school_a = await admin_service.create_school(
                    performed_by_external_id="admin:master", code="TCH-A", name="Escola A"
                )
                school_b = await admin_service.create_school(
                    performed_by_external_id="admin:master", code="TCH-B", name="Escola B"
                )

                content = CatalogNode(node_type="DISCIPLINE", name="Quimica", position=1, active=True)
                session.add(content)
                await session.flush()
                content.root_id = content.id
                topic = CatalogNode(
                    parent_id=content.id, root_id=content.id, node_type="CONTENT",
                    name="Diluicao", position=1, active=True,
                )
                session.add(topic)
                await session.flush()
                topic_id = topic.id

                await admin_service.link_user_to_school(
                    performed_by_external_id="admin:master", external_user_id="teacher-a",
                    role=AdminRole.TEACHER, scope_type=AdminScopeType.CLASSROOM,
                    school_id=school_a.id, scope_external_id="TURMA_3A",
                )
                await admin_service.link_user_to_school(
                    performed_by_external_id="admin:master", external_user_id="coordinator-a",
                    role=AdminRole.COORDINATOR, scope_type=AdminScopeType.SCHOOL,
                    school_id=school_a.id, scope_external_id=str(school_a.id),
                )
                await session.commit()
                return engine, factory, school_a.id, school_b.id, topic_id

        (
            self.engine,
            self.session_factory,
            self.school_a,
            self.school_b,
            self.topic_id,
        ) = asyncio.run(setup())

        self.identity = {
            "value": ExternalIdentityContext(provider="test", external_user_id="teacher-a", roles=("teacher",))
        }
        app = FastAPI()
        app.include_router(teacher_router)
        app.include_router(coordination_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def _set_identity(self, external_user_id: str):
        self.identity["value"] = ExternalIdentityContext(provider="test", external_user_id=external_user_id)

    # -- record_teacher_lesson (POST /lessons) --------------------------------

    def test_record_lesson_authorized_returns_201(self):
        response = self.client.post(
            "/api/v1/teacher/lessons",
            json={
                "school_id": str(self.school_a),
                "classroom_id": "TURMA_3A",
                "content_node_id": str(self.topic_id),
                "title": "Aula de diluicao",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["teacher_id"], "teacher-a")
        self.assertEqual(body["classroom_id"], "TURMA_3A")
        self.assertEqual(body["content_node_id"], str(self.topic_id))
        self.assertIsNotNone(body["pedagogical_context_id"])

    def test_record_lesson_unauthorized_classroom_returns_403(self):
        response = self.client.post(
            "/api/v1/teacher/lessons",
            json={
                "school_id": str(self.school_b),
                "classroom_id": "TURMA_UNKNOWN",
                "content_node_id": str(self.topic_id),
            },
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("detail", response.json())

    def test_record_lesson_unknown_content_node_returns_400(self):
        response = self.client.post(
            "/api/v1/teacher/lessons",
            json={
                "school_id": str(self.school_a),
                "classroom_id": "TURMA_3A",
                "content_node_id": str(uuid4()),
            },
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("not found", response.json()["detail"])

    # -- list_teacher_lessons (GET /lessons) -----------------------------------

    def test_list_lessons_authorized_returns_200(self):
        self.client.post(
            "/api/v1/teacher/lessons",
            json={
                "school_id": str(self.school_a),
                "classroom_id": "TURMA_3A",
                "content_node_id": str(self.topic_id),
            },
        )
        response = self.client.get(
            f"/api/v1/teacher/lessons?school_id={self.school_a}&classroom_id=TURMA_3A"
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["classroom_id"], "TURMA_3A")

    def test_list_lessons_unauthorized_classroom_returns_403(self):
        response = self.client.get(
            f"/api/v1/teacher/lessons?school_id={self.school_b}&classroom_id=TURMA_UNKNOWN"
        )
        self.assertEqual(response.status_code, 403, response.text)

    # -- get_teacher_lesson (GET /lessons/{id}) --------------------------------

    def test_get_lesson_not_found_returns_404(self):
        response = self.client.get(f"/api/v1/teacher/lessons/{uuid4()}")
        self.assertEqual(response.status_code, 404, response.text)

    def test_get_lesson_authorized_returns_200(self):
        create_resp = self.client.post(
            "/api/v1/teacher/lessons",
            json={
                "school_id": str(self.school_a),
                "classroom_id": "TURMA_3A",
                "content_node_id": str(self.topic_id),
            },
        )
        lesson_id = create_resp.json()["id"]
        response = self.client.get(f"/api/v1/teacher/lessons/{lesson_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], lesson_id)

    def test_get_lesson_unauthorized_teacher_returns_403(self):
        create_resp = self.client.post(
            "/api/v1/teacher/lessons",
            json={
                "school_id": str(self.school_a),
                "classroom_id": "TURMA_3A",
                "content_node_id": str(self.topic_id),
            },
        )
        lesson_id = create_resp.json()["id"]
        self._set_identity("teacher-stranger")
        response = self.client.get(f"/api/v1/teacher/lessons/{lesson_id}")
        self.assertEqual(response.status_code, 403, response.text)

    # -- record_coordination_context (POST /coordination/pedagogical-context) -

    def test_record_coordination_context_authorized_returns_201(self):
        self._set_identity("coordinator-a")
        response = self.client.post(
            "/api/v1/coordination/pedagogical-context",
            json={
                "school_id": str(self.school_a),
                "content_node_id": str(self.topic_id),
                "source": "COORDINATION",
                "title": "Orientacao da coordenacao",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["source"], "COORDINATION")
        self.assertEqual(body["author_id"], "coordinator-a")

    def test_record_coordination_context_unauthorized_returns_403(self):
        self._set_identity("teacher-a")
        response = self.client.post(
            "/api/v1/coordination/pedagogical-context",
            json={
                "school_id": str(self.school_b),
                "content_node_id": str(self.topic_id),
            },
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_record_coordination_context_invalid_source_returns_400(self):
        self._set_identity("coordinator-a")
        response = self.client.post(
            "/api/v1/coordination/pedagogical-context",
            json={
                "school_id": str(self.school_a),
                "content_node_id": str(self.topic_id),
                "source": "BOGUS_SOURCE",
            },
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("Invalid coordination context source", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
