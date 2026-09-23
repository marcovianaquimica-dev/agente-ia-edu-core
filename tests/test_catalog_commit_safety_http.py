"""
HTTP-level commit-safety audit for src/agente_ia_edu/api/routes/catalog.py.

catalog.py has 17 separate `await session.commit()` call sites - the most of
any route file touched in the overnight coverage campaign. Wave 2 found the
same real bug three times, independently, in assessments.py: an ORM object
loaded early in a request handler is read again (a synchronous attribute,
e.g. `.id`) AFTER `await session.commit()`, with no intervening
`session.refresh()` and no value captured into a plain Python variable
beforehand. Because `create_session_factory()` is called with no options in
production (see db/session.py), SQLAlchemy's async default
`expire_on_commit=True` applies - `commit()` expires every object loaded or
added in that session, and the next synchronous attribute read on it
triggers a background refresh query outside the async greenlet bridge,
raising MissingGreenlet. Worse, the commit has already happened by the time
the crash occurs, so the row is persisted even though the client gets a raw
500.

This file drives every one of catalog.py's 17 commit sites through a real
HTTP request (FastAPI TestClient, real router, real dependency-injected
session) using a session fixture that matches production exactly:
`async_sessionmaker(engine, class_=AsyncSession)` with NO expire_on_commit
override (defaults to True). This matters: several of this repo's existing
SQLite test fixtures (e.g. test_catalog_http_coverage.py) set
expire_on_commit=False, which would silently hide this exact bug class.

Conclusion up front (see PR/session notes for the full per-site table):
static reading of every commit site in catalog.py showed each one already
follows one of two safe idioms - build the response object (or capture the
needed scalar) BEFORE the commit line, or call `await session.refresh(obj)`
immediately after commit and before any attribute read. These HTTP tests
exist to verify that conclusion empirically rather than trust the reading:
every test below must get a clean 2xx (not a 500/MissingGreenlet) to confirm
its commit site is safe.
"""

from __future__ import annotations

import asyncio
import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_session_factory, reset_identity_provider
from agente_ia_edu.api.routes.catalog import catalog_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import Question, QuestionVersion


def _hdr(role: str, user: str) -> dict:
    return {"Authorization": f"Bearer {role}:{user}"}


class CatalogCommitSafetyHTTPTests(unittest.TestCase):
    """One test per `await session.commit()` call site in catalog.py.

    17 sites total (grep line numbers as of this audit): 348, 397, 535, 741,
    876, 908, 952, 982, 1013, 1043, 1073, 1169, 1249, 1326, 1425, 1458, 1568.
    """

    def setUp(self):
        reset_identity_provider()

        async def setup_database():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            # expire_on_commit=True (the default, left unset here) mirrors
            # production's create_session_factory() exactly - this is the
            # setting under which a phantom-commit/MissingGreenlet bug would
            # actually manifest. Do NOT relax this to False.
            factory = async_sessionmaker(engine, class_=AsyncSession)
            return engine, factory

        self.engine, self.session_factory = asyncio.run(setup_database())

        app = FastAPI()
        app.include_router(catalog_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def _run(self, coro):
        return asyncio.run(coro)

    # ------------------------------------------------------------------
    # seeding helpers
    # ------------------------------------------------------------------

    def _make_discipline(self, name: str, user: str = "disc_admin") -> str:
        r = self.client.post(
            "/api/v1/catalog/disciplines",
            json={"name": name, "node_type": "DISCIPLINE"},
            headers=_hdr("platform_admin", user),
        )
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def _make_resource_via_api(
        self, user: str, *, role: str = "teacher", origin_type: str = "AUTHOR",
        visibility_scope: str | None = None,
    ) -> str:
        payload = {"title": f"Recurso {user}", "resource_type": "PDF", "origin_type": origin_type}
        if visibility_scope:
            payload["visibility_scope"] = visibility_scope
        r = self.client.post(
            "/api/v1/catalog/resources", json=payload, headers=_hdr(role, user)
        )
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def _create_material(self, user: str, title: str) -> str:
        r = self.client.post(
            "/api/v1/catalog/materials", json={"title": title}, headers=_hdr("teacher", user)
        )
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def _make_question_version(self) -> str:
        async def go():
            async with self.session_factory() as session:
                question = Question(validation_status="validated")
                session.add(question)
                await session.flush()
                version = QuestionVersion(
                    question_id=question.id, version_kind="official_original",
                    canonical_text="2 + 2?", content_hash=uuid4().hex,
                )
                session.add(version)
                await session.flush()
                version_id = str(version.id)
                await session.commit()
                return version_id
        return self._run(go())

    # ==================================================================
    # commit site: line 348 - create_discipline
    # ==================================================================
    def test_create_discipline_clean_2xx_after_commit(self):
        r = self.client.post(
            "/api/v1/catalog/disciplines",
            json={"name": "Quimica", "node_type": "DISCIPLINE"},
            headers=_hdr("platform_admin", "admin_disc"),
        )
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["id"])
        self.assertEqual(r.json()["name"], "Quimica")

    # ==================================================================
    # commit site: line 397 - create_content_node
    # ==================================================================
    def test_create_content_node_clean_2xx_after_commit(self):
        root_id = self._make_discipline("Fisica")
        r = self.client.post(
            "/api/v1/catalog/nodes",
            json={"name": "Mecanica", "node_type": "LEARNING_AREA", "parent_id": root_id},
            headers=_hdr("platform_admin", "admin_node"),
        )
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["id"])

    # ==================================================================
    # commit site: line 535 - create_resource
    # ==================================================================
    def test_create_resource_clean_2xx_after_commit(self):
        r = self.client.post(
            "/api/v1/catalog/resources",
            json={"title": "Livro X", "resource_type": "PDF", "origin_type": "AUTHOR"},
            headers=_hdr("teacher", "res_teacher"),
        )
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["id"])

    # ==================================================================
    # commit site: line 741 - create_content_resource_link
    # ==================================================================
    def test_create_content_resource_link_clean_2xx_after_commit(self):
        node_id = self._make_discipline("Biologia")
        resource_id = self._make_resource_via_api(
            "link_admin", role="platform_admin", origin_type="PLATFORM"
        )
        r = self.client.post(
            "/api/v1/catalog/content-resource-links",
            json={"content_node_id": node_id, "resource_id": resource_id, "pedagogical_role": "THEORY"},
            headers=_hdr("platform_admin", "link_admin"),
        )
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["id"])

    # ==================================================================
    # commit site: line 876 - create_material
    # ==================================================================
    def test_create_material_clean_2xx_after_commit(self):
        r = self.client.post(
            "/api/v1/catalog/materials", json={"title": "Apostila 1"},
            headers=_hdr("teacher", "mat_teacher1"),
        )
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["id"])

    # ==================================================================
    # commit site: line 908 - create_material_version
    # ==================================================================
    def test_create_material_version_clean_2xx_after_commit(self):
        material_id = self._create_material("mat_teacher2", "Apostila V")
        r = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/versions",
            json={"introduction": "Intro"},
            headers=_hdr("teacher", "mat_teacher2"),
        )
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["version_number"], 2)

    # ==================================================================
    # commit site: line 952 - review_material (action=submit)
    # ==================================================================
    def test_review_material_submit_clean_2xx_after_commit(self):
        material_id = self._create_material("mat_teacher3", "Apostila R")
        r = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/review",
            json={"action": "submit"},
            headers=_hdr("teacher", "mat_teacher3"),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "PENDING_REVIEW")

    # ==================================================================
    # commit site: line 982 - approve_material
    # ==================================================================
    def test_approve_material_clean_2xx_after_commit(self):
        material_id = self._create_material("mat_teacher4", "Apostila A")
        sub = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/review", json={"action": "submit"},
            headers=_hdr("teacher", "mat_teacher4"),
        )
        self.assertEqual(sub.status_code, 200, sub.text)
        r = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/approve",
            headers=_hdr("teacher", "mat_teacher4"),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "APPROVED")

    # ==================================================================
    # commit site: line 1013 - reject_material
    # ==================================================================
    def test_reject_material_clean_2xx_after_commit(self):
        material_id = self._create_material("mat_teacher5", "Apostila Rej")
        sub = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/review", json={"action": "submit"},
            headers=_hdr("teacher", "mat_teacher5"),
        )
        self.assertEqual(sub.status_code, 200, sub.text)
        r = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/reject",
            params={"reason": "precisa de ajustes"},
            headers=_hdr("teacher", "mat_teacher5"),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "REJECTED")

    # ==================================================================
    # commit site: line 1043 - publish_material
    # ==================================================================
    def test_publish_material_clean_2xx_after_commit(self):
        material_id = self._create_material("mat_teacher6", "Apostila Pub")
        sub = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/review", json={"action": "submit"},
            headers=_hdr("teacher", "mat_teacher6"),
        )
        self.assertEqual(sub.status_code, 200, sub.text)
        appr = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/approve",
            headers=_hdr("teacher", "mat_teacher6"),
        )
        self.assertEqual(appr.status_code, 200, appr.text)
        r = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/publish",
            headers=_hdr("teacher", "mat_teacher6"),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "PUBLISHED")

    # ==================================================================
    # commit site: line 1073 - archive_material
    # ==================================================================
    def test_archive_material_clean_2xx_after_commit(self):
        material_id = self._create_material("mat_teacher7", "Apostila Arch")
        r = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/archive",
            headers=_hdr("teacher", "mat_teacher7"),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "ARCHIVED")

    # ==================================================================
    # commit site: line 1169 - patch_material
    # ==================================================================
    def test_patch_material_clean_2xx_after_commit(self):
        material_id = self._create_material("mat_teacher8", "Apostila Patch")
        r = self.client.patch(
            f"/api/v1/catalog/materials/{material_id}",
            json={"title": "Novo titulo"},
            headers=_hdr("teacher", "mat_teacher8"),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["title"], "Novo titulo")

    # ==================================================================
    # commit site: line 1249 - add_material_section
    # ==================================================================
    def test_add_material_section_clean_2xx_after_commit(self):
        material_id = self._create_material("mat_teacher9", "Apostila Sec")
        r = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/sections",
            json={"section_type": "CHAPTER", "position": 1, "title": "Cap 1"},
            headers=_hdr("teacher", "mat_teacher9"),
        )
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["id"])

    # ==================================================================
    # commit site: line 1326 - add_section_block
    # ==================================================================
    def test_add_section_block_clean_2xx_after_commit(self):
        material_id = self._create_material("mat_teacher10", "Apostila Blk")
        sec = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/sections",
            json={"section_type": "CHAPTER", "position": 1},
            headers=_hdr("teacher", "mat_teacher10"),
        )
        self.assertEqual(sec.status_code, 201, sec.text)
        section_id = sec.json()["id"]
        r = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/sections/{section_id}/blocks",
            json={"block_type": "TEXT", "position": 1, "body": "Conteudo"},
            headers=_hdr("teacher", "mat_teacher10"),
        )
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["id"])

    # ==================================================================
    # commit site: line 1425 - link_material_question
    # ==================================================================
    def test_link_material_question_clean_2xx_after_commit(self):
        material_id = self._create_material("mat_teacher11", "Apostila Q")
        qv_id = self._make_question_version()
        r = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/questions",
            json={"question_version_id": qv_id},
            headers=_hdr("teacher", "mat_teacher11"),
        )
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["id"])

    # ==================================================================
    # commit site: line 1458 - unlink_material_question
    # ==================================================================
    def test_unlink_material_question_clean_204_after_commit(self):
        material_id = self._create_material("mat_teacher12", "Apostila Unlink")
        qv_id = self._make_question_version()
        link_resp = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/questions",
            json={"question_version_id": qv_id},
            headers=_hdr("teacher", "mat_teacher12"),
        )
        self.assertEqual(link_resp.status_code, 201, link_resp.text)
        r = self.client.delete(
            f"/api/v1/catalog/materials/{material_id}/questions/{qv_id}",
            headers=_hdr("teacher", "mat_teacher12"),
        )
        self.assertEqual(r.status_code, 204, r.text)

    # ==================================================================
    # commit site: line 1568 - create_resource_grant
    # ==================================================================
    def test_create_resource_grant_clean_2xx_after_commit(self):
        resource_id = self._make_resource_via_api("grant_teacher")
        r = self.client.post(
            f"/api/v1/catalog/resources/{resource_id}/grants",
            json={"grantee_type": "SCHOOL", "grantee_external_id": str(uuid4())},
            headers=_hdr("teacher", "grant_teacher"),
        )
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["id"])


if __name__ == "__main__":
    unittest.main()
