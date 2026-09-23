"""HTTP-layer coverage audit for catalog.py (2026-09).

catalog.py is 641 statements and, at the time of this audit, most existing
tests either call the SERVICE layer directly (services/catalog.py) or only
exercise a handful of HTTP happy-paths - the error/403/404/422 branches of
many endpoints (materials CRUD, sections, blocks, question links, resource
grants, editorial history) were never driven through a real HTTP request.
This file adds exactly that: real fastapi.testclient.TestClient calls that
assert on actual status codes/bodies, following the SAME app-singleton +
dependency_overrides + "Bearer role:user" auth pattern already used by
tests/test_catalog.py's CatalogApiTests (this domain's established HTTP
fixture) so both files can run in the same session safely.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import app
from agente_ia_edu.api.dependencies import get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    EducationalResource,
    MaterialSection,
    Question,
    QuestionVersion,
    School,
    TheoryMaterial,
    TheoryMaterialVersion,
    UserSchoolLink,
)
from agente_ia_edu.services.catalog import CatalogNodeService, TheoryMaterialService


def _hdr(role: str, user: str) -> dict:
    return {"Authorization": f"Bearer {role}:{user}"}


def _admin() -> dict:
    return _hdr("platform_admin", "admin_cov")


class CatalogHTTPCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_async_engine(
            "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
        )
        cls.session_factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _init():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(_init())
        app.dependency_overrides[get_session_factory] = lambda: cls.session_factory
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        asyncio.run(cls.engine.dispose())

    # -- small seeding helpers ---------------------------------------------
    def _run(self, coro):
        return asyncio.run(coro)

    def _make_discipline_and_content(self, tag: str) -> tuple[str, str]:
        async def go():
            async with self.session_factory() as session:
                svc = CatalogNodeService()
                disc = await svc.create_node(session, name=f"Disciplina {tag}", node_type="DISCIPLINE")
                content = await svc.create_node(
                    session, name=f"Conteudo {tag}", node_type="CONTENT", parent_id=disc.id)
                await session.commit()
                return str(disc.id), str(content.id)
        return self._run(go())

    def _create_material_via_api(self, user: str, *, title: str, primary_content_node_id: str | None = None) -> dict:
        payload = {"title": title}
        if primary_content_node_id:
            payload["primary_content_node_id"] = primary_content_node_id
        r = self.client.post("/api/v1/catalog/materials", json=payload, headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def _delete_all_versions(self, material_id: str) -> None:
        """Force a material into the 'has no versions' edge case - the API
        itself can never do this (create_material always makes version 1);
        this simulates a data anomaly to exercise the route's own defensive
        'version is None' branch."""
        async def go():
            async with self.session_factory() as session:
                await session.execute(
                    delete(TheoryMaterialVersion).where(
                        TheoryMaterialVersion.material_id == uuid.UUID(material_id)))
                await session.commit()
        self._run(go())

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
                await session.commit()
                return str(version.id)
        return self._run(go())

    # ======================================================================
    # Disciplines / content tree
    # ======================================================================
    def test_create_content_node_invalid_parent_404(self):
        r = self.client.post(
            "/api/v1/catalog/nodes",
            json={"name": "Orfao", "node_type": "CONTENT", "parent_id": str(uuid4())},
            headers=_admin(),
        )
        self.assertEqual(r.status_code, 404, r.text)

    def test_get_content_tree_not_found_404(self):
        r = self.client.get(f"/api/v1/catalog/nodes/{uuid4()}/tree")
        self.assertEqual(r.status_code, 404, r.text)

    def test_list_catalog_children_no_pedagogical_universe_is_403(self):
        # No PedagogicalUniverseBinding at all exists for this brand-new
        # identity - resolve_active_universe must raise PermissionError,
        # and the route must translate that into 403, not a 500.
        r = self.client.get(
            "/api/v1/catalog/nodes", headers=_hdr("teacher", "no_universe_teacher"))
        self.assertEqual(r.status_code, 403, r.text)

    # ======================================================================
    # Educational resources
    # ======================================================================
    def test_create_resource_platform_origin_denied_for_non_admin(self):
        r = self.client.post(
            "/api/v1/catalog/resources",
            json={"title": "Video X", "resource_type": "VIDEO", "origin_type": "PLATFORM"},
            headers=_hdr("teacher", "res_teacher_1"),
        )
        self.assertEqual(r.status_code, 403, r.text)

    def test_create_resource_licensed_origin_denied_for_non_admin(self):
        r = self.client.post(
            "/api/v1/catalog/resources",
            json={"title": "Livro licenciado", "resource_type": "BOOK", "origin_type": "LICENSED"},
            headers=_hdr("teacher", "res_teacher_licensed"),
        )
        self.assertEqual(r.status_code, 403, r.text)

    def test_list_resources_invalid_sort_field_rejected_by_pydantic_422(self):
        # The route body has its own "sort not in sort_map -> 400" guard
        # (catalog.py ~579-580), but `sort`'s Query(pattern=...) already
        # restricts it to exactly the 4 keys in sort_map - so that guard is
        # actually unreachable through the HTTP API today; Pydantic rejects
        # an invalid value first, with 422. Documented here rather than
        # silently left uncovered.
        r = self.client.get(
            "/api/v1/catalog/resources", params={"sort": "not_a_real_field"},
            headers=_hdr("teacher", "res_teacher_2"))
        self.assertEqual(r.status_code, 422, r.text)

    def test_list_resources_school_id_mismatch_returns_empty(self):
        # Caller has no school context at all (no UserSchoolLink seeded);
        # asking for a specific school_id must short-circuit to [] rather
        # than leaking any cross-school listing.
        r = self.client.get(
            "/api/v1/catalog/resources", params={"school_id": str(uuid4())},
            headers=_hdr("teacher", "res_teacher_noschool"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), [])

    def test_list_resources_school_id_mismatch_with_real_school_context_returns_empty(self):
        # Distinct from test_list_resources_school_id_mismatch_returns_empty:
        # THIS caller has a real school context (via UserSchoolLink) that
        # differs from the requested school_id - a different branch
        # (catalog.py ~567-569) than the "caller has no school at all" case.
        async def _seed():
            async with self.session_factory() as session:
                school = School(code="RESSCHCTX", name="Escola Res Ctx")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="res_ctx_teacher", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True))
                await session.commit()
        self._run(_seed())

        r = self.client.get(
            "/api/v1/catalog/resources", params={"school_id": str(uuid4())},
            headers=_hdr("teacher", "res_ctx_teacher"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), [])

    def test_list_resources_filters_visibility_scope_and_origin_type_and_owner(self):
        create_resp = self.client.post(
            "/api/v1/catalog/resources",
            json={
                "title": "Recurso filtro fino", "resource_type": "PDF", "origin_type": "PLATFORM",
                "status": "active", "visibility_scope": "PUBLIC",
            },
            headers=_admin(),
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        resource_id = create_resp.json()["id"]

        # visibility_scope mismatch excludes it (line ~618)
        r1 = self.client.get(
            "/api/v1/catalog/resources", params={"visibility_scope": "PRIVATE"}, headers=_admin())
        self.assertNotIn(resource_id, [r["id"] for r in r1.json()])

        # origin_type mismatch excludes it (line ~620)
        r2 = self.client.get(
            "/api/v1/catalog/resources", params={"origin_type": "AUTHOR"}, headers=_admin())
        self.assertNotIn(resource_id, [r["id"] for r in r2.json()])

        # owner_external_id mismatch excludes it (line ~622) - this PLATFORM
        # resource has owner_external_id=None, so any non-empty filter excludes it
        r3 = self.client.get(
            "/api/v1/catalog/resources", params={"owner_external_id": "someone-else"}, headers=_admin())
        self.assertNotIn(resource_id, [r["id"] for r in r3.json()])

        # matches on the correct filters
        r4 = self.client.get(
            "/api/v1/catalog/resources",
            params={"visibility_scope": "PUBLIC", "origin_type": "PLATFORM"}, headers=_admin())
        self.assertIn(resource_id, [r["id"] for r in r4.json()])

    def test_list_resources_filters_by_content_node_and_type(self):
        _disc_id, content_id = self._make_discipline_and_content("ResFilter")
        create_resp = self.client.post(
            "/api/v1/catalog/resources",
            json={
                "title": "PDF filtrável", "resource_type": "PDF", "origin_type": "PLATFORM",
                "status": "active",
            },
            headers=_admin(),
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        resource_id = create_resp.json()["id"]
        link_resp = self.client.post(
            "/api/v1/catalog/content-resource-links",
            json={"content_node_id": content_id, "resource_id": resource_id, "pedagogical_role": "REFERENCE"},
            headers=_admin(),
        )
        self.assertEqual(link_resp.status_code, 201, link_resp.text)

        matching = self.client.get(
            "/api/v1/catalog/resources",
            params={"content_node_id": content_id, "resource_type": "PDF"},
            headers=_admin())
        self.assertEqual(matching.status_code, 200)
        self.assertIn(resource_id, [r["id"] for r in matching.json()])

        non_matching = self.client.get(
            "/api/v1/catalog/resources",
            params={"content_node_id": content_id, "resource_type": "VIDEO"},
            headers=_admin())
        self.assertEqual(non_matching.status_code, 200)
        self.assertNotIn(resource_id, [r["id"] for r in non_matching.json()])

    def test_get_resource_detail_not_found_404(self):
        r = self.client.get(
            f"/api/v1/catalog/resources/{uuid4()}", headers=_hdr("teacher", "res_teacher_3"))
        self.assertEqual(r.status_code, 404, r.text)

    def test_get_resource_detail_inactive_is_403(self):
        create_resp = self.client.post(
            "/api/v1/catalog/resources",
            json={
                "title": "Recurso rascunho", "resource_type": "PDF", "origin_type": "PLATFORM",
                "status": "draft",
            },
            headers=_admin(),
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        resource_id = create_resp.json()["id"]
        detail = self.client.get(
            f"/api/v1/catalog/resources/{resource_id}", headers=_hdr("teacher", "res_teacher_4"))
        self.assertEqual(detail.status_code, 403, detail.text)

    # ======================================================================
    # Content <-> resource links
    # ======================================================================
    def test_create_content_resource_link_resource_not_found_404(self):
        _disc_id, content_id = self._make_discipline_and_content("LinkNotFound")
        async def _seed_link_teacher():
            async with self.session_factory() as session:
                school = School(code="LNF1", name="Escola LNF1")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="link_nf_teacher", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True))
                await session.commit()
        self._run(_seed_link_teacher())

        r = self.client.post(
            "/api/v1/catalog/content-resource-links",
            json={"content_node_id": content_id, "resource_id": str(uuid4()), "pedagogical_role": "THEORY"},
            headers=_hdr("teacher", "link_nf_teacher"),
        )
        self.assertEqual(r.status_code, 404, r.text)

    def test_create_content_resource_link_duplicate_is_400(self):
        _disc_id, content_id = self._make_discipline_and_content("LinkDup")
        resource_resp = self.client.post(
            "/api/v1/catalog/resources",
            json={"title": "Recurso duplicado", "resource_type": "PDF", "origin_type": "PLATFORM"},
            headers=_admin(),
        )
        resource_id = resource_resp.json()["id"]
        first = self.client.post(
            "/api/v1/catalog/content-resource-links",
            json={"content_node_id": content_id, "resource_id": resource_id, "pedagogical_role": "THEORY"},
            headers=_admin(),
        )
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post(
            "/api/v1/catalog/content-resource-links",
            json={"content_node_id": content_id, "resource_id": resource_id, "pedagogical_role": "THEORY"},
            headers=_admin(),
        )
        self.assertEqual(second.status_code, 400, second.text)

    # ======================================================================
    # Materials: listing + filters
    # ======================================================================
    def test_list_materials_denies_student(self):
        r = self.client.get("/api/v1/catalog/materials", headers=_hdr("student", "mat_student_1"))
        self.assertEqual(r.status_code, 403, r.text)

    def test_list_materials_filters_by_material_id_author_and_status(self):
        user = "mat_filter_teacher"
        m1 = self._create_material_via_api(user, title="Material Filtro 1")
        m2 = self._create_material_via_api(user, title="Material Filtro 2")
        other_user_material = self._create_material_via_api("mat_filter_other", title="Material de outro autor")

        # material_id filter: only m1
        r = self.client.get(
            "/api/v1/catalog/materials", params={"material_id": m1["id"]}, headers=_hdr("teacher", user))
        ids = [m["id"] for m in r.json()]
        self.assertEqual(ids, [m1["id"]])

        # author_id filter: excludes the other author's material
        r2 = self.client.get(
            "/api/v1/catalog/materials", params={"author_id": user}, headers=_hdr("teacher", user))
        ids2 = {m["id"] for m in r2.json()}
        self.assertIn(m1["id"], ids2)
        self.assertIn(m2["id"], ids2)
        self.assertNotIn(other_user_material["id"], ids2)

        # status filter: DRAFT matches (freshly created materials are DRAFT)
        r3 = self.client.get(
            "/api/v1/catalog/materials", params={"status": "DRAFT"}, headers=_hdr("teacher", user))
        self.assertIn(m1["id"], {m["id"] for m in r3.json()})

        # status filter: no version is PUBLISHED yet, so this must exclude m1
        r4 = self.client.get(
            "/api/v1/catalog/materials", params={"status": "PUBLISHED"}, headers=_hdr("teacher", user))
        self.assertNotIn(m1["id"], {m["id"] for m in r4.json()})

    def test_list_materials_filters_by_version_id(self):
        user = "mat_version_filter_teacher"
        m1 = self._create_material_via_api(user, title="Material com versao")
        detail = self.client.get(f"/api/v1/catalog/materials/{m1['id']}", headers=_hdr("teacher", user))
        version_id = detail.json()["versions"][0]["id"]

        r = self.client.get(
            "/api/v1/catalog/materials", params={"version_id": version_id}, headers=_hdr("teacher", user))
        self.assertIn(m1["id"], {m["id"] for m in r.json()})

        r2 = self.client.get(
            "/api/v1/catalog/materials", params={"version_id": str(uuid4())}, headers=_hdr("teacher", user))
        self.assertNotIn(m1["id"], {m["id"] for m in r2.json()})

    # ======================================================================
    # Materials: create / version / review / approve / reject / publish / archive
    # ======================================================================
    def test_create_material_invalid_content_node_422(self):
        r = self.client.post(
            "/api/v1/catalog/materials",
            json={"title": "Material invalido", "primary_content_node_id": str(uuid4())},
            headers=_hdr("teacher", "mat_invalid_teacher"),
        )
        self.assertEqual(r.status_code, 422, r.text)

    def test_create_material_version_material_not_found_404(self):
        r = self.client.post(
            f"/api/v1/catalog/materials/{uuid4()}/versions", json={},
            headers=_hdr("teacher", "mat_version_nf_teacher"),
        )
        self.assertEqual(r.status_code, 404, r.text)

    def test_review_material_not_found_404(self):
        r = self.client.post(
            f"/api/v1/catalog/materials/{uuid4()}/review", json={"action": "submit"},
            headers=_hdr("teacher", "review_nf_teacher"),
        )
        self.assertEqual(r.status_code, 404, r.text)

    def test_review_material_no_versions_404(self):
        user = "review_noversion_teacher"
        m = self._create_material_via_api(user, title="Material sem versao")
        self._delete_all_versions(m["id"])
        r = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/review", json={"action": "submit"},
            headers=_hdr("teacher", user),
        )
        self.assertEqual(r.status_code, 404, r.text)

    def test_review_material_unsupported_action_400(self):
        user = "review_badaction_teacher"
        m = self._create_material_via_api(user, title="Material acao invalida")
        r = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/review", json={"action": "teleport"},
            headers=_hdr("teacher", user),
        )
        self.assertEqual(r.status_code, 400, r.text)

    def test_review_material_full_workflow_submit_reject_then_resubmit_approve_publish_archive(self):
        """Drives submit -> reject -> submit -> approve -> publish -> archive
        through the SAME generic /review endpoint, exercising every action
        branch (lines 936-947) with real state transitions, not just the
        first one."""
        user = "review_full_teacher"
        m = self._create_material_via_api(user, title="Material fluxo completo")
        mid = m["id"]
        hdr = _hdr("teacher", user)

        submit1 = self.client.post(f"/api/v1/catalog/materials/{mid}/review", json={"action": "submit"}, headers=hdr)
        self.assertEqual(submit1.status_code, 200, submit1.text)
        self.assertEqual(submit1.json()["status"], "PENDING_REVIEW")

        reject1 = self.client.post(
            f"/api/v1/catalog/materials/{mid}/review",
            json={"action": "reject", "reason": "precisa de ajustes"}, headers=hdr)
        self.assertEqual(reject1.status_code, 200, reject1.text)
        self.assertEqual(reject1.json()["status"], "REJECTED")

        submit2 = self.client.post(f"/api/v1/catalog/materials/{mid}/review", json={"action": "submit"}, headers=hdr)
        self.assertEqual(submit2.status_code, 200, submit2.text)

        approve1 = self.client.post(f"/api/v1/catalog/materials/{mid}/review", json={"action": "approve"}, headers=hdr)
        self.assertEqual(approve1.status_code, 200, approve1.text)
        self.assertEqual(approve1.json()["status"], "APPROVED")

        publish1 = self.client.post(f"/api/v1/catalog/materials/{mid}/review", json={"action": "publish"}, headers=hdr)
        self.assertEqual(publish1.status_code, 200, publish1.text)
        self.assertEqual(publish1.json()["status"], "PUBLISHED")

        archive1 = self.client.post(f"/api/v1/catalog/materials/{mid}/review", json={"action": "archive"}, headers=hdr)
        self.assertEqual(archive1.status_code, 200, archive1.text)
        self.assertEqual(archive1.json()["status"], "ARCHIVED")

        # -- editorial audit history must reflect every real transition ----
        history = self.client.get(f"/api/v1/catalog/materials/{mid}/history", headers=hdr)
        self.assertEqual(history.status_code, 200, history.text)
        actions = [row["action"] for row in history.json()]
        for expected in (
            "MATERIAL_CREATED", "MATERIAL_SUBMITTED_FOR_REVIEW", "MATERIAL_REJECTED",
            "MATERIAL_APPROVED", "MATERIAL_PUBLISHED", "MATERIAL_ARCHIVED",
        ):
            self.assertIn(expected, actions, f"{expected} missing from {actions}")

    # -- dedicated approve/reject/publish/archive endpoints -----------------
    def test_approve_material_not_found_404(self):
        r = self.client.post(f"/api/v1/catalog/materials/{uuid4()}/approve", headers=_hdr("teacher", "appr_nf"))
        self.assertEqual(r.status_code, 404, r.text)

    def test_approve_material_no_versions_404(self):
        user = "appr_noversion_teacher"
        m = self._create_material_via_api(user, title="Aprovar sem versao")
        self._delete_all_versions(m["id"])
        r = self.client.post(f"/api/v1/catalog/materials/{m['id']}/approve", headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 404, r.text)

    def test_approve_material_wrong_state_400(self):
        user = "appr_wrongstate_teacher"
        m = self._create_material_via_api(user, title="Aprovar direto do draft")
        r = self.client.post(f"/api/v1/catalog/materials/{m['id']}/approve", headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 400, r.text)

    def test_reject_material_not_found_404(self):
        r = self.client.post(f"/api/v1/catalog/materials/{uuid4()}/reject", headers=_hdr("teacher", "rej_nf"))
        self.assertEqual(r.status_code, 404, r.text)

    def test_reject_material_no_versions_404(self):
        user = "rej_noversion_teacher"
        m = self._create_material_via_api(user, title="Rejeitar sem versao")
        self._delete_all_versions(m["id"])
        r = self.client.post(f"/api/v1/catalog/materials/{m['id']}/reject", headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 404, r.text)

    def test_reject_material_wrong_state_400(self):
        user = "rej_wrongstate_teacher"
        m = self._create_material_via_api(user, title="Rejeitar direto do draft")
        r = self.client.post(f"/api/v1/catalog/materials/{m['id']}/reject", headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 400, r.text)

    def test_reject_material_success_with_reason(self):
        user = "rej_success_teacher"
        m = self._create_material_via_api(user, title="Rejeitar com sucesso")
        hdr = _hdr("teacher", user)
        self.client.post(f"/api/v1/catalog/materials/{m['id']}/review", json={"action": "submit"}, headers=hdr)
        r = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/reject", params={"reason": "faltam referencias"}, headers=hdr)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "REJECTED")

    def test_publish_material_not_found_404(self):
        r = self.client.post(f"/api/v1/catalog/materials/{uuid4()}/publish", headers=_hdr("teacher", "pub_nf"))
        self.assertEqual(r.status_code, 404, r.text)

    def test_publish_material_no_versions_404(self):
        user = "pub_noversion_teacher"
        m = self._create_material_via_api(user, title="Publicar sem versao")
        self._delete_all_versions(m["id"])
        r = self.client.post(f"/api/v1/catalog/materials/{m['id']}/publish", headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 404, r.text)

    def test_publish_material_wrong_state_400(self):
        user = "pub_wrongstate_teacher"
        m = self._create_material_via_api(user, title="Publicar direto do draft")
        r = self.client.post(f"/api/v1/catalog/materials/{m['id']}/publish", headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 400, r.text)

    def test_publish_material_success(self):
        user = "pub_success_teacher"
        m = self._create_material_via_api(user, title="Publicar com sucesso")
        hdr = _hdr("teacher", user)
        self.client.post(f"/api/v1/catalog/materials/{m['id']}/review", json={"action": "submit"}, headers=hdr)
        self.client.post(f"/api/v1/catalog/materials/{m['id']}/approve", headers=hdr)
        r = self.client.post(f"/api/v1/catalog/materials/{m['id']}/publish", headers=hdr)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "PUBLISHED")

    def test_archive_material_not_found_404(self):
        r = self.client.post(f"/api/v1/catalog/materials/{uuid4()}/archive", headers=_hdr("teacher", "arch_nf"))
        self.assertEqual(r.status_code, 404, r.text)

    def test_archive_material_no_versions_404(self):
        user = "arch_noversion_teacher"
        m = self._create_material_via_api(user, title="Arquivar sem versao")
        self._delete_all_versions(m["id"])
        r = self.client.post(f"/api/v1/catalog/materials/{m['id']}/archive", headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 404, r.text)

    def test_archive_material_already_archived_400(self):
        # Unlike approve/publish, archive_version accepts DRAFT/PENDING_
        # REVIEW/APPROVED/REJECTED/PUBLISHED - the only rejected transition
        # is re-archiving an already-ARCHIVED version.
        user = "arch_wrongstate_teacher"
        m = self._create_material_via_api(user, title="Arquivar direto do draft")
        hdr = _hdr("teacher", user)
        first = self.client.post(f"/api/v1/catalog/materials/{m['id']}/archive", headers=hdr)
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post(f"/api/v1/catalog/materials/{m['id']}/archive", headers=hdr)
        self.assertEqual(second.status_code, 400, second.text)

    def test_archive_material_success(self):
        user = "arch_success_teacher"
        m = self._create_material_via_api(user, title="Arquivar com sucesso")
        hdr = _hdr("teacher", user)
        self.client.post(f"/api/v1/catalog/materials/{m['id']}/review", json={"action": "submit"}, headers=hdr)
        self.client.post(f"/api/v1/catalog/materials/{m['id']}/approve", headers=hdr)
        self.client.post(f"/api/v1/catalog/materials/{m['id']}/publish", headers=hdr)
        r = self.client.post(f"/api/v1/catalog/materials/{m['id']}/archive", headers=hdr)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "ARCHIVED")

    # -- patch_material -------------------------------------------------------
    def test_patch_material_not_found_404(self):
        r = self.client.patch(
            f"/api/v1/catalog/materials/{uuid4()}", json={"title": "novo titulo"},
            headers=_hdr("teacher", "patch_nf_teacher"),
        )
        self.assertEqual(r.status_code, 404, r.text)

    def test_patch_material_invalid_content_node_422(self):
        user = "patch_invalid_teacher"
        m = self._create_material_via_api(user, title="Patch invalido")
        r = self.client.patch(
            f"/api/v1/catalog/materials/{m['id']}",
            json={"primary_content_node_id": str(uuid4())},
            headers=_hdr("teacher", user),
        )
        self.assertEqual(r.status_code, 422, r.text)

    # ======================================================================
    # Sections / blocks
    # ======================================================================
    def test_list_material_sections_not_found_404(self):
        r = self.client.get(
            f"/api/v1/catalog/materials/{uuid4()}/sections", headers=_hdr("teacher", "sec_nf"))
        self.assertEqual(r.status_code, 404, r.text)

    def test_list_material_sections_no_versions_empty_list(self):
        user = "sec_noversion_teacher"
        m = self._create_material_via_api(user, title="Secoes sem versao")
        self._delete_all_versions(m["id"])
        r = self.client.get(
            f"/api/v1/catalog/materials/{m['id']}/sections", headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), [])

    def test_add_material_section_no_versions_409(self):
        user = "sec_noversion409_teacher"
        m = self._create_material_via_api(user, title="Secao sem versao 409")
        self._delete_all_versions(m["id"])
        r = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/sections",
            json={"section_type": "THEORY", "position": 1}, headers=_hdr("teacher", user),
        )
        self.assertEqual(r.status_code, 409, r.text)

    def test_add_material_section_blocked_after_publish_422(self):
        user = "sec_blocked_teacher"
        m = self._create_material_via_api(user, title="Secao bloqueada")
        hdr = _hdr("teacher", user)
        self.client.post(f"/api/v1/catalog/materials/{m['id']}/review", json={"action": "submit"}, headers=hdr)
        self.client.post(f"/api/v1/catalog/materials/{m['id']}/approve", headers=hdr)
        self.client.post(f"/api/v1/catalog/materials/{m['id']}/publish", headers=hdr)
        r = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/sections",
            json={"section_type": "THEORY", "position": 1}, headers=hdr,
        )
        self.assertEqual(r.status_code, 422, r.text)

    def test_list_section_blocks_material_not_found_404(self):
        r = self.client.get(
            f"/api/v1/catalog/materials/{uuid4()}/sections/{uuid4()}/blocks",
            headers=_hdr("teacher", "blk_mat_nf"))
        self.assertEqual(r.status_code, 404, r.text)

    def test_list_section_blocks_section_not_found_404(self):
        user = "blk_sec_nf_teacher"
        m = self._create_material_via_api(user, title="Blocos secao inexistente")
        r = self.client.get(
            f"/api/v1/catalog/materials/{m['id']}/sections/{uuid4()}/blocks",
            headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 404, r.text)

    def test_add_section_block_material_not_found_404(self):
        r = self.client.post(
            f"/api/v1/catalog/materials/{uuid4()}/sections/{uuid4()}/blocks",
            json={"block_type": "TEXT", "position": 1}, headers=_hdr("teacher", "blkadd_mat_nf"))
        self.assertEqual(r.status_code, 404, r.text)

    def test_add_section_block_section_not_found_404(self):
        user = "blkadd_sec_nf_teacher"
        m = self._create_material_via_api(user, title="Add bloco secao inexistente")
        r = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/sections/{uuid4()}/blocks",
            json={"block_type": "TEXT", "position": 1}, headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 404, r.text)

    def test_add_section_block_blocked_after_publish_422(self):
        user = "blkadd_blocked_teacher"
        m = self._create_material_via_api(user, title="Bloco bloqueado apos publicar")
        hdr = _hdr("teacher", user)
        section_resp = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/sections",
            json={"section_type": "THEORY", "position": 1}, headers=hdr,
        )
        self.assertEqual(section_resp.status_code, 201, section_resp.text)
        section_id = section_resp.json()["id"]

        self.client.post(f"/api/v1/catalog/materials/{m['id']}/review", json={"action": "submit"}, headers=hdr)
        self.client.post(f"/api/v1/catalog/materials/{m['id']}/approve", headers=hdr)
        self.client.post(f"/api/v1/catalog/materials/{m['id']}/publish", headers=hdr)

        r = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/sections/{section_id}/blocks",
            json={"block_type": "TEXT", "position": 1}, headers=hdr,
        )
        self.assertEqual(r.status_code, 422, r.text)

    def test_add_section_block_success_and_list(self):
        user = "blkadd_success_teacher"
        m = self._create_material_via_api(user, title="Bloco com sucesso")
        hdr = _hdr("teacher", user)
        section_resp = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/sections",
            json={"section_type": "THEORY", "position": 1}, headers=hdr,
        )
        section_id = section_resp.json()["id"]
        block_resp = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/sections/{section_id}/blocks",
            json={"block_type": "TEXT", "position": 1, "body": "conteudo"}, headers=hdr,
        )
        self.assertEqual(block_resp.status_code, 201, block_resp.text)
        list_resp = self.client.get(
            f"/api/v1/catalog/materials/{m['id']}/sections/{section_id}/blocks", headers=hdr)
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(len(list_resp.json()), 1)

    # ======================================================================
    # Question links
    # ======================================================================
    def test_list_material_questions_not_found_404(self):
        r = self.client.get(
            f"/api/v1/catalog/materials/{uuid4()}/questions", headers=_hdr("teacher", "mq_nf"))
        self.assertEqual(r.status_code, 404, r.text)

    def test_list_material_questions_no_versions_empty(self):
        user = "mq_noversion_teacher"
        m = self._create_material_via_api(user, title="Questoes sem versao")
        self._delete_all_versions(m["id"])
        r = self.client.get(f"/api/v1/catalog/materials/{m['id']}/questions", headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), [])

    def test_link_material_question_not_found_404(self):
        qv = self._make_question_version()
        r = self.client.post(
            f"/api/v1/catalog/materials/{uuid4()}/questions",
            json={"question_version_id": qv}, headers=_hdr("teacher", "mqlink_nf"))
        self.assertEqual(r.status_code, 404, r.text)

    def test_link_material_question_no_versions_409(self):
        user = "mqlink_noversion_teacher"
        m = self._create_material_via_api(user, title="Link questao sem versao")
        self._delete_all_versions(m["id"])
        qv = self._make_question_version()
        r = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/questions",
            json={"question_version_id": qv}, headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 409, r.text)

    def test_link_material_question_section_not_found_404(self):
        user = "mqlink_secnf_teacher"
        m = self._create_material_via_api(user, title="Link questao secao inexistente")
        qv = self._make_question_version()
        r = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/questions",
            json={"question_version_id": qv, "section_id": str(uuid4())},
            headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 404, r.text)

    def test_link_material_question_nonexistent_question_version_404(self):
        user = "mqlink_qvnf_teacher"
        m = self._create_material_via_api(user, title="Link questao inexistente")
        r = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/questions",
            json={"question_version_id": str(uuid4())}, headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 404, r.text)

    def test_link_and_unlink_material_question_success(self):
        user = "mqlink_success_teacher"
        m = self._create_material_via_api(user, title="Link questao com sucesso")
        qv = self._make_question_version()
        hdr = _hdr("teacher", user)
        link_resp = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/questions",
            json={"question_version_id": qv}, headers=hdr)
        self.assertEqual(link_resp.status_code, 201, link_resp.text)

        list_resp = self.client.get(f"/api/v1/catalog/materials/{m['id']}/questions", headers=hdr)
        self.assertEqual(len(list_resp.json()), 1)

        unlink_resp = self.client.delete(
            f"/api/v1/catalog/materials/{m['id']}/questions/{qv}", headers=hdr)
        self.assertEqual(unlink_resp.status_code, 204, unlink_resp.text)

        list_resp2 = self.client.get(f"/api/v1/catalog/materials/{m['id']}/questions", headers=hdr)
        self.assertEqual(list_resp2.json(), [])

    def test_unlink_material_question_not_found_404(self):
        r = self.client.delete(
            f"/api/v1/catalog/materials/{uuid4()}/questions/{uuid4()}", headers=_hdr("teacher", "mqunlink_nf"))
        self.assertEqual(r.status_code, 404, r.text)

    def test_unlink_material_question_no_versions_404(self):
        user = "mqunlink_noversion_teacher"
        m = self._create_material_via_api(user, title="Unlink sem versao")
        self._delete_all_versions(m["id"])
        r = self.client.delete(
            f"/api/v1/catalog/materials/{m['id']}/questions/{uuid4()}", headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 404, r.text)

    def test_unlink_material_question_link_not_found_404(self):
        user = "mqunlink_linknf_teacher"
        m = self._create_material_via_api(user, title="Unlink link inexistente")
        r = self.client.delete(
            f"/api/v1/catalog/materials/{m['id']}/questions/{uuid4()}", headers=_hdr("teacher", user))
        self.assertEqual(r.status_code, 404, r.text)

    def test_unlink_material_question_blocked_after_publish_422(self):
        user = "mqunlink_blocked_teacher"
        m = self._create_material_via_api(user, title="Unlink bloqueado apos publicar")
        qv = self._make_question_version()
        hdr = _hdr("teacher", user)
        link_resp = self.client.post(
            f"/api/v1/catalog/materials/{m['id']}/questions",
            json={"question_version_id": qv}, headers=hdr)
        self.assertEqual(link_resp.status_code, 201, link_resp.text)

        self.client.post(f"/api/v1/catalog/materials/{m['id']}/review", json={"action": "submit"}, headers=hdr)
        self.client.post(f"/api/v1/catalog/materials/{m['id']}/approve", headers=hdr)
        self.client.post(f"/api/v1/catalog/materials/{m['id']}/publish", headers=hdr)

        r = self.client.delete(
            f"/api/v1/catalog/materials/{m['id']}/questions/{qv}", headers=hdr)
        self.assertEqual(r.status_code, 422, r.text)

    # ======================================================================
    # Resource access grants
    # ======================================================================
    def test_list_resource_grants_not_found_404(self):
        r = self.client.get(
            f"/api/v1/catalog/resources/{uuid4()}/grants", headers=_hdr("teacher", "grant_list_nf"))
        self.assertEqual(r.status_code, 404, r.text)

    def test_create_resource_grant_resource_not_found_404(self):
        r = self.client.post(
            f"/api/v1/catalog/resources/{uuid4()}/grants",
            json={"grantee_type": "SCHOOL", "grantee_external_id": str(uuid4())},
            headers=_hdr("teacher", "grant_create_nf"),
        )
        self.assertEqual(r.status_code, 404, r.text)

    def test_create_resource_grant_denies_student(self):
        create_resp = self.client.post(
            "/api/v1/catalog/resources",
            json={"title": "Recurso para grant", "resource_type": "PDF", "origin_type": "PLATFORM"},
            headers=_admin(),
        )
        resource_id = create_resp.json()["id"]
        r = self.client.post(
            f"/api/v1/catalog/resources/{resource_id}/grants",
            json={"grantee_type": "SCHOOL", "grantee_external_id": str(uuid4())},
            headers=_hdr("student", "grant_student"),
        )
        self.assertEqual(r.status_code, 403, r.text)

    # ======================================================================
    # Editorial history
    # ======================================================================
    def test_get_material_history_not_found_404(self):
        r = self.client.get(
            f"/api/v1/catalog/materials/{uuid4()}/history", headers=_hdr("teacher", "hist_nf"))
        self.assertEqual(r.status_code, 404, r.text)


if __name__ == "__main__":
    unittest.main()
