"""
HTTP-level test for GET /api/v1/student/modules (entrada-dois-ambientes Task 1).

Confirms the route reflects the real, seeded SchoolModule rows for the
caller's own school via AuthorizationService.resolve_context(...).modules -
never a fabricated/default-true value. Follows the same shared-app +
in-memory SQLite pattern as tests/test_student_route_coverage_http.py
(get_current_identity is NOT overridden, so requests go through the real
TestExternalIdentityProvider via the "Bearer student:<id>" header, exactly
like the existing /evolution and /learning-path HTTP tests).
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School, SchoolModule, UserSchoolLink


class StudentModulesHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _seed_school(self, *, modules: dict[str, bool], student_uid: str, link_active: bool = True):
        """Real seed: a School row, one SchoolModule row per entry in
        `modules`, and an active STUDENT UserSchoolLink for student_uid to
        that school - the minimal real fixture resolve_context() needs."""

        async def _do():
            async with self.factory() as s:
                school = School(code=f"SCH-{_uuid.uuid4().hex[:8]}", name="Escola Teste Modules")
                s.add(school)
                await s.flush()
                for key, enabled in modules.items():
                    s.add(SchoolModule(school_id=school.id, module_key=key, enabled=enabled))
                s.add(UserSchoolLink(
                    external_user_id=student_uid, school_id=school.id,
                    role="STUDENT", scope_type="SCHOOL", scope_external_id="turma-modules",
                    active=link_active,
                ))
                await s.commit()
                return school.id

        return self.loop.run_until_complete(_do())

    def test_modules_endpoint_reflects_real_school_modules(self):
        uid = "stu-modules-both"
        self._seed_school(modules={"AGENTE_IA_EDU": True, "REDACAO_IA": True}, student_uid=uid)
        response = self.client.get(
            "/api/v1/student/modules",
            headers={"Authorization": f"Bearer student:{uid}"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"AGENTE_IA_EDU": True, "REDACAO_IA": True})

    def test_modules_endpoint_only_one_enabled(self):
        uid = "stu-modules-one"
        self._seed_school(modules={"AGENTE_IA_EDU": True}, student_uid=uid)
        response = self.client.get(
            "/api/v1/student/modules",
            headers={"Authorization": f"Bearer student:{uid}"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"AGENTE_IA_EDU": True, "REDACAO_IA": False})

    def test_modules_endpoint_no_school_module_rows_defaults_to_all_false(self):
        uid = "stu-modules-none"
        self._seed_school(modules={}, student_uid=uid)
        response = self.client.get(
            "/api/v1/student/modules",
            headers={"Authorization": f"Bearer student:{uid}"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"AGENTE_IA_EDU": False, "REDACAO_IA": False})

    def test_modules_endpoint_disabled_module_reads_as_false(self):
        uid = "stu-modules-disabled"
        self._seed_school(modules={"AGENTE_IA_EDU": True, "REDACAO_IA": False}, student_uid=uid)
        response = self.client.get(
            "/api/v1/student/modules",
            headers={"Authorization": f"Bearer student:{uid}"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"AGENTE_IA_EDU": True, "REDACAO_IA": False})

    def test_modules_endpoint_no_school_link_at_all_defaults_to_all_false(self):
        # Independent student with zero UserSchoolLink rows: resolve_context's
        # fallback path returns school_id=None, so _school_modules() returns
        # an empty set - never a fabricated True.
        response = self.client.get(
            "/api/v1/student/modules",
            headers={"Authorization": "Bearer student:stu-modules-no-link"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"AGENTE_IA_EDU": False, "REDACAO_IA": False})


if __name__ == "__main__":
    unittest.main()
