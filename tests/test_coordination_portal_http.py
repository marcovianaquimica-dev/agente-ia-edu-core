"""HTTP-layer tests for coordination_portal_router
(src/agente_ia_edu/api/routes/coordination_portal.py).

Wave 1 (commit 703661f) proved that route HANDLER code - request parsing,
dependency injection, exception -> status mapping, response serialization -
is much less tested than the SERVICE layer it calls, because existing tests
call the service directly and skip the HTTP layer entirely. That is exactly
the shape of tests/test_coordination_portal.py (calls CoordinationPortalService
methods directly) and tests/test_coordination_narrow_scope_student_leak.py /
tests/test_r0_scope_validation_coordination.py (call the route FUNCTIONS
directly, bypassing FastAPI's routing/dependency-injection/response-model
machinery entirely).

These tests instead go through the real HTTP path (TestClient.get/post),
covering: the ScopeAuthorizationError -> HTTPException(403) mapping on every
endpoint that has one, the ValueError -> HTTPException(400) mapping on
export, the StudySessionAuthError -> 403 / StudySessionError -> 422 mapping
for the PHASE 24 study-session endpoints, and the coordinator-scoping IDOR
history this file has (commit ec25ca7) - a classroom-restricted coordinator
must not reach a classroom outside their own scope.
"""

import asyncio
import unittest
import uuid as _uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.coordination_portal import coordination_portal_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, StudentContentMastery
from agente_ia_edu.db.models.assessments import DomainContentMastery
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService

CONTENT_CODE = "CPH-DIL"


class CoordinationPortalHTTP(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                admin_service = PlatformAdminService(session)
                school_a = await admin_service.create_school(
                    performed_by_external_id="admin:master", code="CPH-A", name="Escola A")
                school_b = await admin_service.create_school(
                    performed_by_external_id="admin:master", code="CPH-B", name="Escola B")

                root = CatalogNode(node_type="DISCIPLINE", name="Quimica", position=1, active=True)
                session.add(root)
                await session.flush()
                root.root_id = root.id
                content = CatalogNode(
                    parent_id=root.id, root_id=root.id, node_type="CONTENT",
                    code=CONTENT_CODE, name="Diluicao", position=1, active=True,
                )
                session.add(content)
                await session.flush()

                # coord-a: school-wide coordinator in school A
                await admin_service.link_user_to_school(
                    performed_by_external_id="admin:master", external_user_id="coord-a",
                    role=AdminRole.COORDINATOR, scope_type=AdminScopeType.SCHOOL,
                    school_id=school_a.id,
                )
                # coord-b: school-wide coordinator in school B (used to probe cross-school 403s)
                await admin_service.link_user_to_school(
                    performed_by_external_id="admin:master", external_user_id="coord-b",
                    role=AdminRole.COORDINATOR, scope_type=AdminScopeType.SCHOOL,
                    school_id=school_b.id,
                )
                # coord-restrict: coordinator in school A, scoped to TURMA_3A only
                await admin_service.link_user_to_school(
                    performed_by_external_id="admin:master", external_user_id="coord-restrict",
                    role=AdminRole.COORDINATOR, scope_type=AdminScopeType.CLASSROOM,
                    school_id=school_a.id, scope_external_id="TURMA_3A",
                )
                await admin_service.link_user_to_school(
                    performed_by_external_id="admin:master", external_user_id="teacher-a",
                    role=AdminRole.TEACHER, scope_type=AdminScopeType.CLASSROOM,
                    school_id=school_a.id, scope_external_id="TURMA_3A",
                )
                await admin_service.link_user_to_school(
                    performed_by_external_id="admin:master", external_user_id="student-a",
                    role=AdminRole.STUDENT, scope_type=AdminScopeType.CLASSROOM,
                    school_id=school_a.id, scope_external_id="TURMA_3A",
                )
                session.add(StudentContentMastery(
                    external_identity_id="student-a", content_node_id=content.id, mastery_score=40.0))
                # Pre-existing DomainContentMastery so create_coordination_sessions' AI
                # planning path (AdaptiveLearningPathService.build_path) takes its plain
                # read path instead of the first-time rebuild_student() write path -
                # mirrors tests/test_phase24_study_session_n1.py's working recipe.
                session.add(DomainContentMastery(
                    student_external_id="student-a", taxonomy_version="curriculum-v2",
                    content_code=CONTENT_CODE, subcontent_code=None,
                    questions_seen=4, questions_answered=4, questions_correct=2,
                    questions_incorrect=2, accuracy=0.5, evidence_count=4,
                    evidence_state="OBSERVED", definitive_evidence_count=4,
                    provisional_evidence_count=0, forced_closure_evidence_count=0,
                    visual_dependency_evidence_count=0,
                    origin_breakdown={"OFFICIAL_ACTIVITY": 4},
                    last_evaluated_at=datetime.now(timezone.utc)))
                await session.commit()
                return engine, factory, school_a.id, school_b.id

        self.engine, self.session_factory, self.school_a, self.school_b = asyncio.run(setup())
        self.identity = {"value": ExternalIdentityContext(provider="test", external_user_id="coord-a", roles=("coordinator",))}
        app = FastAPI()
        app.include_router(coordination_portal_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def _as(self, external_user_id: str) -> None:
        self.identity["value"] = ExternalIdentityContext(
            provider="test", external_user_id=external_user_id, roles=("coordinator",))

    # ---------------------------------------------------------------- dashboard

    def test_dashboard_authorized_returns_200(self):
        response = self.client.get(f"/api/v1/coordination/dashboard?school_id={self.school_a}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["coordinator_id"], "coord-a")
        self.assertEqual(body["school_id"], str(self.school_a))

    def test_dashboard_unauthorized_school_returns_403(self):
        response = self.client.get(f"/api/v1/coordination/dashboard?school_id={self.school_b}")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("detail", response.json())

    # ---------------------------------------------------------------- hierarchy

    def test_hierarchy_authorized_returns_200(self):
        response = self.client.get(f"/api/v1/coordination/hierarchy?school_id={self.school_a}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("units", response.json())

    def test_hierarchy_unauthorized_returns_403(self):
        self._as("nobody-with-no-links")
        response = self.client.get(f"/api/v1/coordination/hierarchy?school_id={self.school_a}")
        self.assertEqual(response.status_code, 403, response.text)

    # ---------------------------------------------------------------- classrooms/compare

    def test_compare_classrooms_authorized_returns_200(self):
        response = self.client.get(f"/api/v1/coordination/classrooms/compare?school_id={self.school_a}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsInstance(response.json(), list)

    def test_compare_classrooms_unauthorized_returns_403(self):
        self._as("coord-b")
        response = self.client.get(f"/api/v1/coordination/classrooms/compare?school_id={self.school_a}")
        self.assertEqual(response.status_code, 403, response.text)

    # ---------------------------------------------------------------- classrooms/{id}

    def test_classroom_detail_authorized_returns_200(self):
        response = self.client.get(
            f"/api/v1/coordination/classrooms/TURMA_3A?school_id={self.school_a}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("action_plan", response.json())

    def test_classroom_detail_unauthorized_classroom_returns_403(self):
        # IDOR-shaped: coord-restrict is only authorized for TURMA_3A in
        # school A (see commit ec25ca7 - the same "restricted-coordinator
        # reaches another turma" family of bug) - it must not be able to
        # pull the detail of TURMA_3B, a different classroom in the SAME
        # school it does have some access to.
        self._as("coord-restrict")
        response = self.client.get(
            f"/api/v1/coordination/classrooms/TURMA_3B?school_id={self.school_a}")
        self.assertEqual(response.status_code, 403, response.text)

    # ---------------------------------------------------------------- teachers

    def test_teachers_oversight_authorized_returns_200(self):
        response = self.client.get(f"/api/v1/coordination/teachers?school_id={self.school_a}")
        self.assertEqual(response.status_code, 200, response.text)
        teacher_ids = [t["teacher_id"] for t in response.json()]
        self.assertIn("teacher-a", teacher_ids)

    def test_teachers_oversight_unauthorized_returns_403(self):
        self._as("coord-b")
        response = self.client.get(f"/api/v1/coordination/teachers?school_id={self.school_a}")
        self.assertEqual(response.status_code, 403, response.text)

    # ---------------------------------------------------------------- search

    def test_search_students_authorized_returns_200(self):
        response = self.client.get(f"/api/v1/coordination/search?q=student&school_id={self.school_a}")
        self.assertEqual(response.status_code, 200, response.text)
        ids = [s["student_id"] for s in response.json()]
        self.assertIn("student-a", ids)

    def test_search_students_unauthorized_returns_403(self):
        # Covers the route's except ScopeAuthorizationError branch, which no
        # existing test (they all use in-scope or narrowing scenarios) hits.
        self._as("nobody-with-no-links")
        response = self.client.get(f"/api/v1/coordination/search?q=student&school_id={self.school_a}")
        self.assertEqual(response.status_code, 403, response.text)

    # ---------------------------------------------------------------- contexts

    def test_list_contexts_authorized_returns_200(self):
        response = self.client.get(f"/api/v1/coordination/contexts?school_id={self.school_a}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsInstance(response.json(), list)

    def test_list_contexts_unauthorized_classroom_returns_403(self):
        self._as("coord-restrict")
        response = self.client.get(
            f"/api/v1/coordination/contexts?school_id={self.school_a}&classroom_id=TURMA_3B")
        self.assertEqual(response.status_code, 403, response.text)

    # ---------------------------------------------------------------- export

    def test_export_report_pdf_returns_200(self):
        response = self.client.get(f"/api/v1/coordination/export?school_id={self.school_a}&format=pdf")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["export_format"], "pdf")
        self.assertEqual(body["content_type"], "application/pdf")

    def test_export_report_xlsx_returns_200(self):
        response = self.client.get(f"/api/v1/coordination/export?school_id={self.school_a}&format=xlsx")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["export_format"], "xlsx")

    def test_export_report_invalid_format_returns_400(self):
        response = self.client.get(f"/api/v1/coordination/export?school_id={self.school_a}&format=docx")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("Unsupported export format", response.json()["detail"])

    def test_export_report_unauthorized_returns_403(self):
        self._as("coord-b")
        response = self.client.get(f"/api/v1/coordination/export?school_id={self.school_a}&format=pdf")
        self.assertEqual(response.status_code, 403, response.text)

    # ---------------------------------------------------------------- study-sessions (POST)

    def test_create_study_session_unauthorized_returns_403(self):
        self._as("nobody-with-no-links")
        response = self.client.post(
            "/api/v1/coordination/study-sessions",
            json={
                "school_id": str(self.school_a),
                "target_type": "CLASSROOM",
                "target_id": "TURMA_3A",
                "session_date": "2026-09-21",
                "start_at": "2026-09-21T08:00:00Z",
                "end_at": "2026-09-21T09:00:00Z",
            },
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("detail", response.json())

    def test_create_study_session_invalid_window_returns_422(self):
        response = self.client.post(
            "/api/v1/coordination/study-sessions",
            json={
                "school_id": str(self.school_a),
                "target_type": "CLASSROOM",
                "target_id": "TURMA_3A",
                "session_date": "2026-09-21",
                "start_at": "2026-09-21T09:00:00Z",
                "end_at": "2026-09-21T09:00:00Z",  # end == start -> invalid window
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("Janela inválida", response.json()["detail"]["message"])

    def test_create_study_session_success_returns_200(self):
        response = self.client.post(
            "/api/v1/coordination/study-sessions",
            json={
                "school_id": str(self.school_a),
                "target_type": "CLASSROOM",
                "target_id": "TURMA_3A",
                "session_date": "2026-09-21",
                "start_at": "2026-09-21T08:00:00Z",
                "end_at": "2026-09-21T09:00:00Z",
                "content_codes": [CONTENT_CODE],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["created_or_updated"], 1)
        self.assertEqual(body["sessions"][0]["student_external_id"], "student-a")

    # ---------------------------------------------------------------- study-sessions (GET)

    def test_list_study_sessions_authorized_returns_200(self):
        response = self.client.get(f"/api/v1/coordination/study-sessions?school_id={self.school_a}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["school_id"], str(self.school_a))
        self.assertIn("sessions", body)

    def test_list_study_sessions_unauthorized_returns_403(self):
        self._as("nobody-with-no-links")
        response = self.client.get(f"/api/v1/coordination/study-sessions?school_id={self.school_a}")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("detail", response.json())


if __name__ == "__main__":
    unittest.main()
