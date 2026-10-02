"""Rotas de admin das propostas da plataforma.

TestClient + dependency_overrides, mesmo padrao de tests/test_admin_http.py
(inclusive a forma de trocar a identidade por teste para exercitar o 403).
"""

import asyncio
import unittest
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.admin_essay_prompts import admin_essay_prompts_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import EssayPrompt, School
from agente_ia_edu.identity import ExternalIdentityContext

BASE = "/api/v1/admin/platform-essay-prompts"


class PlatformEssayPromptsAdminRoutesTests(unittest.TestCase):
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
        app.include_router(admin_essay_prompts_router)
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

    def _create(self, title="Mobilidade urbana"):
        response = self.client.post(
            BASE, json={"title": title, "statement": "A partir dos textos, disserte."}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _materialize(self, origin_id, school_code):
        async def _seed():
            async with self.session_factory() as session:
                school = School(id=uuid.uuid4(), code=school_code, name=school_code)
                session.add(school)
                await session.flush()
                session.add(EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Mobilidade urbana",
                    statement="A partir dos textos, disserte.", year=2026, status="ACTIVE",
                    created_by_external_identity="teacher:p1",
                    materialized_from_platform_prompt_id=uuid.UUID(origin_id),
                ))
                await session.commit()

        asyncio.run(_seed())

    def test_create_returns_201_with_an_active_prompt(self):
        body = self._create()
        self.assertEqual(body["status"], "ACTIVE")
        self.assertEqual(body["title"], "Mobilidade urbana")
        self.assertEqual(body["materialized_school_count"], 0)
        self.assertTrue(body["created_at"])

    def test_create_rejects_an_empty_title(self):
        response = self.client.post(BASE, json={"title": "", "statement": "s"})
        self.assertEqual(response.status_code, 422, response.text)

    def test_list_returns_every_prompt_with_its_materialization_count(self):
        created = self._create()
        self._materialize(created["id"], "SCH-COUNT-A")
        self._materialize(created["id"], "SCH-COUNT-B")

        response = self.client.get(BASE)
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["materialized_school_count"], 2)

    def test_archive_flips_the_status_and_is_listed_as_archived(self):
        created = self._create()
        response = self.client.post(f"{BASE}/{created['id']}/archive")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ARCHIVED")

        listed = self.client.get(BASE).json()
        self.assertEqual(listed[0]["status"], "ARCHIVED")

    def test_archive_unknown_prompt_returns_404(self):
        response = self.client.post(f"{BASE}/{uuid.uuid4()}/archive")
        self.assertEqual(response.status_code, 404, response.text)

    def test_teacher_director_and_coordinator_are_all_denied(self):
        created = self._create()
        for role in ("TEACHER", "DIRECTOR", "COORDINATOR"):
            with self.subTest(role=role):
                self.as_identity(external_user_id=f"user-{role.lower()}", roles=(role,))
                self.assertEqual(self.client.get(BASE).status_code, 403)
                self.assertEqual(
                    self.client.post(BASE, json={"title": "x", "statement": "y"}).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(f"{BASE}/{created['id']}/archive").status_code, 403
                )


if __name__ == "__main__":
    unittest.main()
