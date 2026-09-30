"""Tela de status minima da correcao em massa (spec 2026-09-29 Task 8).

TestClient + dependency_overrides, mesmo padrao de
tests/test_r5_platform_essay_prompts_admin_routes.py e tests/test_admin_http.py
para a dependency require_platform_admin (definida em api/routes/admin.py e
reusada por api/routes/admin_essay_prompts.py) - nao um mecanismo inventado.
"""

import asyncio
import unittest
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.mass_correction_status import mass_correction_status_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import MassCorrectionRun, School
from agente_ia_edu.identity import ExternalIdentityContext

BASE = "/api/v1/admin/mass-correction-runs"


class MassCorrectionStatusRouteTests(unittest.TestCase):
    def setUp(self):
        async def _setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            return engine, factory

        self.engine, self.session_factory = asyncio.run(_setup())
        self.identity = {
            "value": ExternalIdentityContext(
                provider="test", external_user_id="admin", roles=("PLATFORM_ADMIN",)
            )
        }
        app = FastAPI()
        app.include_router(mass_correction_status_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def as_identity(self, **kwargs):
        defaults = {"provider": "test", "external_user_id": "someone", "roles": ()}
        defaults.update(kwargs)
        self.identity["value"] = ExternalIdentityContext(**defaults)

    def _seed_runs(self):
        async def _seed():
            async with self.session_factory() as session:
                school = School(id=uuid.uuid4(), code="EST", name="Rede")
                session.add(school)
                await session.flush()
                session.add(MassCorrectionRun(
                    id=uuid.uuid4(), school_id=school.id, stage="OCR",
                    sequence_number=1, request_count=100, status="completed",
                ))
                session.add(MassCorrectionRun(
                    id=uuid.uuid4(), school_id=school.id, stage="CORRECTION",
                    sequence_number=1, request_count=50, status="in_progress",
                ))
                await session.commit()
                return school.id

        return asyncio.run(_seed())

    def test_status_reports_counts_per_stage(self):
        school_id = self._seed_runs()
        response = self.client.get(f"{BASE}?school_id={school_id}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["OCR"]["completed"], 100)
        self.assertEqual(body["CORRECTION"]["in_progress"], 50)

    def test_status_denies_non_admin(self):
        self.as_identity(external_user_id="teacher-x", roles=("TEACHER",))
        response = self.client.get(f"{BASE}?school_id={uuid.uuid4()}")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("PLATFORM_ADMIN role required", response.json()["detail"])

    def test_status_returns_empty_dict_when_no_runs_for_school(self):
        response = self.client.get(f"{BASE}?school_id={uuid.uuid4()}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {})

    def test_status_scopes_by_school_id(self):
        school_id = self._seed_runs()
        other_school_id = uuid.uuid4()

        response_other = self.client.get(f"{BASE}?school_id={other_school_id}")
        self.assertEqual(response_other.status_code, 200, response_other.text)
        self.assertEqual(response_other.json(), {})

        response_mine = self.client.get(f"{BASE}?school_id={school_id}")
        self.assertEqual(response_mine.json()["OCR"]["completed"], 100)


if __name__ == "__main__":
    unittest.main()
