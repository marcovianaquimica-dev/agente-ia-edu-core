"""HTTP-layer tests for admin_router (src/agente_ia_edu/api/routes/admin.py).

No prior test exercised this module via HTTP at all - tests/test_platform_
administration.py and tests/test_pedagogical_universe*.py call
PlatformAdminService / PedagogicalUniverseService directly, so the route
handlers (request parsing, the require_platform_admin dependency's three
authorization paths, and every ValueError -> HTTPException(400/404) mapping)
had zero coverage. This file exercises the routes themselves via
fastapi.testclient.TestClient.
"""

import asyncio
import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.admin import admin_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService


class AdminHTTP(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            return engine, factory

        self.engine, self.session_factory = asyncio.run(setup())
        self.identity = {"value": ExternalIdentityContext(provider="test", external_user_id="admin", roles=("PLATFORM_ADMIN",))}
        app = FastAPI()
        app.include_router(admin_router)
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

    def create_school(self, code="SCH-A", name="Escola A"):
        response = self.client.post("/api/v1/admin/schools", json={"code": code, "name": name})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    # ---- require_platform_admin dependency: all three authorization paths ----

    def test_require_platform_admin_denies_non_admin_role(self):
        self.as_identity(external_user_id="teacher-x", roles=("TEACHER",))
        response = self.client.get("/api/v1/admin/schools")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("PLATFORM_ADMIN role required", response.json()["detail"])

    def test_require_platform_admin_allows_admin_role(self):
        self.as_identity(external_user_id="someone", roles=("ADMIN",))
        response = self.client.get("/api/v1/admin/schools")
        self.assertEqual(response.status_code, 200, response.text)

    def test_require_platform_admin_allows_external_id_shortcut(self):
        self.as_identity(external_user_id="ADMIN", roles=())
        response = self.client.get("/api/v1/admin/schools")
        self.assertEqual(response.status_code, 200, response.text)

    def test_require_platform_admin_allows_db_backed_platform_admin_link(self):
        async def seed_db_admin():
            async with self.session_factory() as session:
                admin_service = PlatformAdminService(session)
                await admin_service.link_user_to_school(
                    performed_by_external_id="admin:master",
                    external_user_id="db-admin",
                    role=AdminRole.PLATFORM_ADMIN,
                    scope_type=AdminScopeType.PLATFORM,
                )
                await session.commit()

        asyncio.run(seed_db_admin())
        self.as_identity(external_user_id="db-admin", roles=())
        response = self.client.get("/api/v1/admin/schools")
        self.assertEqual(response.status_code, 200, response.text)

    def test_require_platform_admin_denies_when_no_role_and_no_db_link(self):
        self.as_identity(external_user_id="nobody", roles=())
        response = self.client.get("/api/v1/admin/schools")
        self.assertEqual(response.status_code, 403, response.text)

    # ---- Schools ----

    def test_create_school_success(self):
        school = self.create_school()
        self.assertEqual(school["code"], "SCH-A")
        self.assertEqual(school["status"], "ACTIVE")
        self.assertEqual(school["modules"], [] if not school["modules"] else school["modules"])

    def test_create_school_duplicate_code_returns_400(self):
        self.create_school(code="SCH-DUP")
        response = self.client.post("/api/v1/admin/schools", json={"code": "SCH-DUP", "name": "Outra"})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("already exists", response.json()["detail"])

    def test_list_schools_filters_by_status(self):
        self.create_school(code="SCH-LIST-A", name="Lista A")
        inactive = self.create_school(code="SCH-LIST-B", name="Lista B")
        self.client.patch(f"/api/v1/admin/schools/{inactive['id']}", json={"status": "INACTIVE"})

        active_only = self.client.get("/api/v1/admin/schools?status=ACTIVE")
        self.assertEqual(active_only.status_code, 200, active_only.text)
        codes = {s["code"] for s in active_only.json()}
        self.assertIn("SCH-LIST-A", codes)
        self.assertNotIn("SCH-LIST-B", codes)

    def test_get_school_not_found_returns_404(self):
        response = self.client.get(f"/api/v1/admin/schools/{uuid4()}")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "School tenant not found.")

    def test_get_school_success(self):
        school = self.create_school(code="SCH-GET", name="Escola Get")
        response = self.client.get(f"/api/v1/admin/schools/{school['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["code"], "SCH-GET")

    def test_update_school_success(self):
        school = self.create_school(code="SCH-UPD", name="Antigo Nome")
        response = self.client.patch(f"/api/v1/admin/schools/{school['id']}", json={"name": "Novo Nome"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["name"], "Novo Nome")

    def test_update_school_not_found_returns_400(self):
        response = self.client.patch(f"/api/v1/admin/schools/{uuid4()}", json={"name": "X"})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("School not found", response.json()["detail"])

    # ---- Modules ----

    def test_configure_school_module_success(self):
        school = self.create_school(code="SCH-MOD", name="Escola Mod")
        response = self.client.post(
            f"/api/v1/admin/schools/{school['id']}/modules",
            json={"module_key": "REDACAO_IA", "enabled": True},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["module_key"], "REDACAO_IA")
        self.assertTrue(response.json()["enabled"])

    def test_configure_school_module_invalid_key_returns_400(self):
        school = self.create_school(code="SCH-MOD-BAD", name="Escola Mod Ruim")
        response = self.client.post(
            f"/api/v1/admin/schools/{school['id']}/modules",
            json={"module_key": "NOT_A_MODULE", "enabled": True},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("Invalid module key", response.json()["detail"])

    def test_toggle_school_module_success(self):
        school = self.create_school(code="SCH-TOGGLE", name="Escola Toggle")
        self.client.post(f"/api/v1/admin/schools/{school['id']}/modules", json={"module_key": "REDACAO_IA", "enabled": True})
        response = self.client.patch(
            f"/api/v1/admin/schools/{school['id']}/modules/REDACAO_IA?enabled=false"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["enabled"])

    def test_toggle_school_module_school_not_found_returns_400(self):
        response = self.client.patch(
            f"/api/v1/admin/schools/{uuid4()}/modules/REDACAO_IA?enabled=true"
        )
        self.assertEqual(response.status_code, 400, response.text)

    # ---- User links ----

    def test_link_user_to_school_success(self):
        school = self.create_school(code="SCH-USR", name="Escola User")
        response = self.client.post(
            f"/api/v1/admin/schools/{school['id']}/users",
            json={"external_user_id": "user:novo", "role": "TEACHER", "scope_type": "SCHOOL"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["role"], "TEACHER")
        self.assertTrue(response.json()["active"])

    def test_link_user_to_school_invalid_role_returns_400(self):
        school = self.create_school(code="SCH-USR-BAD", name="Escola User Ruim")
        response = self.client.post(
            f"/api/v1/admin/schools/{school['id']}/users",
            json={"external_user_id": "user:x", "role": "NOT_A_ROLE"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("Invalid role", response.json()["detail"])

    def test_list_school_users_returns_only_that_school(self):
        school_a = self.create_school(code="SCH-USRS-A", name="Escola Usuarios A")
        school_b = self.create_school(code="SCH-USRS-B", name="Escola Usuarios B")
        self.client.post(f"/api/v1/admin/schools/{school_a['id']}/users", json={"external_user_id": "user:a1", "role": "TEACHER"})
        self.client.post(f"/api/v1/admin/schools/{school_b['id']}/users", json={"external_user_id": "user:b1", "role": "TEACHER"})

        response = self.client.get(f"/api/v1/admin/schools/{school_a['id']}/users")
        self.assertEqual(response.status_code, 200, response.text)
        user_ids = {u["external_user_id"] for u in response.json()}
        self.assertEqual(user_ids, {"user:a1"})

    def test_deactivate_school_user_link_success(self):
        school = self.create_school(code="SCH-DEACT", name="Escola Deact")
        link = self.client.post(
            f"/api/v1/admin/schools/{school['id']}/users",
            json={"external_user_id": "user:deact", "role": "TEACHER"},
        ).json()
        response = self.client.delete(f"/api/v1/admin/schools/{school['id']}/users/{link['id']}")
        self.assertEqual(response.status_code, 204, response.text)
        remaining = self.client.get(f"/api/v1/admin/schools/{school['id']}/users").json()
        self.assertEqual(remaining, [])

    def test_deactivate_school_user_link_not_found_returns_404(self):
        school = self.create_school(code="SCH-DEACT-404", name="Escola Deact 404")
        response = self.client.delete(f"/api/v1/admin/schools/{school['id']}/users/{uuid4()}")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "User link not found for this school.")

    def test_deactivate_school_user_link_wrong_school_returns_404(self):
        school_a = self.create_school(code="SCH-DEACT-WRONG-A", name="Escola Wrong A")
        school_b = self.create_school(code="SCH-DEACT-WRONG-B", name="Escola Wrong B")
        link = self.client.post(
            f"/api/v1/admin/schools/{school_a['id']}/users",
            json={"external_user_id": "user:wrong", "role": "TEACHER"},
        ).json()
        # link belongs to school_a - deactivating it via school_b's path must 404
        response = self.client.delete(f"/api/v1/admin/schools/{school_b['id']}/users/{link['id']}")
        self.assertEqual(response.status_code, 404, response.text)

    # ---- Audit ----

    def test_list_audit_logs_returns_school_creation_entry(self):
        school = self.create_school(code="SCH-AUDIT", name="Escola Audit")
        response = self.client.get(f"/api/v1/admin/audit?school_id={school['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        actions = [entry["action"] for entry in response.json()]
        self.assertIn("SCHOOL_CREATED", actions)

    # ---- Pedagogical universes ----

    def create_universe(self, external_id="univ-a", slug="univ-a", name="Universo A"):
        response = self.client.post(
            "/api/v1/admin/pedagogical-universes",
            json={"external_id": external_id, "slug": slug, "name": name, "owner_type": "SCHOOL"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_create_pedagogical_universe_success(self):
        universe = self.create_universe()
        self.assertEqual(universe["slug"], "univ-a")
        self.assertEqual(universe["status"], "DRAFT")

    def test_create_pedagogical_universe_duplicate_slug_returns_400_not_500(self):
        """Regression test for a real bug found in this audit: the route's
        `except ValueError` never fired for a duplicate external_id/slug,
        because PedagogicalUniverseService.create_universe relied solely on
        the DB's UniqueConstraint and let sqlalchemy.exc.IntegrityError
        propagate uncaught, producing a raw 500 instead of a 400. Fixed by
        adding an explicit pre-check in create_universe, mirroring the
        existing PlatformAdminService.create_school pattern."""
        self.create_universe(external_id="dup-1", slug="dup-slug", name="Primeiro")
        response = self.client.post(
            "/api/v1/admin/pedagogical-universes",
            json={"external_id": "dup-2", "slug": "dup-slug", "name": "Segundo", "owner_type": "SCHOOL"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("slug", response.json()["detail"].lower())

    def test_create_pedagogical_universe_duplicate_external_id_returns_400_not_500(self):
        self.create_universe(external_id="dup-ext", slug="slug-1", name="Primeiro")
        response = self.client.post(
            "/api/v1/admin/pedagogical-universes",
            json={"external_id": "dup-ext", "slug": "slug-2", "name": "Segundo", "owner_type": "SCHOOL"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("external_id", response.json()["detail"].lower())

    def test_get_pedagogical_universe_not_found_returns_404(self):
        response = self.client.get(f"/api/v1/admin/pedagogical-universes/{uuid4()}")
        self.assertEqual(response.status_code, 404, response.text)

    def test_get_pedagogical_universe_success(self):
        universe = self.create_universe(external_id="univ-get", slug="univ-get", name="Universo Get")
        response = self.client.get(f"/api/v1/admin/pedagogical-universes/{universe['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["slug"], "univ-get")

    def test_list_pedagogical_universes(self):
        self.create_universe(external_id="univ-list-1", slug="univ-list-1", name="Lista 1")
        self.create_universe(external_id="univ-list-2", slug="univ-list-2", name="Lista 2")
        response = self.client.get("/api/v1/admin/pedagogical-universes")
        self.assertEqual(response.status_code, 200, response.text)
        slugs = {u["slug"] for u in response.json()}
        self.assertTrue({"univ-list-1", "univ-list-2"}.issubset(slugs))

    def test_set_pedagogical_universe_status_success(self):
        universe = self.create_universe(external_id="univ-status", slug="univ-status", name="Universo Status")
        response = self.client.patch(
            f"/api/v1/admin/pedagogical-universes/{universe['id']}/status?status=ACTIVE"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ACTIVE")

    def test_set_pedagogical_universe_status_invalid_returns_400(self):
        universe = self.create_universe(external_id="univ-status-bad", slug="univ-status-bad", name="Universo Status Ruim")
        response = self.client.patch(
            f"/api/v1/admin/pedagogical-universes/{universe['id']}/status?status=NOT_A_STATUS"
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_update_pedagogical_universe_configuration_success(self):
        universe = self.create_universe(external_id="univ-cfg", slug="univ-cfg", name="Universo Cfg")
        response = self.client.patch(
            f"/api/v1/admin/pedagogical-universes/{universe['id']}/configuration",
            json={"configuration": {"foo": "bar"}, "configuration_version": "v2"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["configuration_version"], "v2")

    def test_update_pedagogical_universe_configuration_archived_returns_400(self):
        universe = self.create_universe(external_id="univ-cfg-arch", slug="univ-cfg-arch", name="Universo Cfg Arquivado")
        self.client.patch(f"/api/v1/admin/pedagogical-universes/{universe['id']}/status?status=ARCHIVED")
        response = self.client.patch(
            f"/api/v1/admin/pedagogical-universes/{universe['id']}/configuration",
            json={"configuration": {"foo": "bar"}, "configuration_version": "v2"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("Archived", response.json()["detail"])

    def seed_catalog_node(self):
        async def seed():
            async with self.session_factory() as session:
                node = CatalogNode(node_type="DISCIPLINE", name="Quimica", position=1, active=True)
                session.add(node)
                await session.flush()
                node.root_id = node.id
                await session.commit()
                return node.id
        return asyncio.run(seed())

    def test_add_pedagogical_universe_catalog_scope_success(self):
        universe = self.create_universe(external_id="univ-scope", slug="univ-scope", name="Universo Escopo")
        node_id = self.seed_catalog_node()
        response = self.client.post(
            f"/api/v1/admin/pedagogical-universes/{universe['id']}/catalog-scopes",
            json={"catalog_node_id": str(node_id), "scope_kind": "DISCIPLINE"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIn("id", response.json())

    def test_add_pedagogical_universe_catalog_scope_invalid_kind_returns_400(self):
        universe = self.create_universe(external_id="univ-scope-bad", slug="univ-scope-bad", name="Universo Escopo Ruim")
        node_id = self.seed_catalog_node()
        response = self.client.post(
            f"/api/v1/admin/pedagogical-universes/{universe['id']}/catalog-scopes",
            json={"catalog_node_id": str(node_id), "scope_kind": "NOT_A_KIND"},
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_add_pedagogical_universe_catalog_scope_node_not_found_returns_400(self):
        universe = self.create_universe(external_id="univ-scope-nf", slug="univ-scope-nf", name="Universo Escopo NF")
        response = self.client.post(
            f"/api/v1/admin/pedagogical-universes/{universe['id']}/catalog-scopes",
            json={"catalog_node_id": str(uuid4()), "scope_kind": "DISCIPLINE"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("Catalog node not found", response.json()["detail"])

    def test_list_pedagogical_universe_catalog_scopes(self):
        universe = self.create_universe(external_id="univ-scope-list", slug="univ-scope-list", name="Universo Escopo Lista")
        node_id = self.seed_catalog_node()
        self.client.post(
            f"/api/v1/admin/pedagogical-universes/{universe['id']}/catalog-scopes",
            json={"catalog_node_id": str(node_id), "scope_kind": "DISCIPLINE"},
        )
        response = self.client.get(f"/api/v1/admin/pedagogical-universes/{universe['id']}/catalog-scopes")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["catalog_node_id"], str(node_id))

    def test_bind_pedagogical_universe_success(self):
        universe = self.create_universe(external_id="univ-bind", slug="univ-bind", name="Universo Bind")
        response = self.client.post(
            f"/api/v1/admin/pedagogical-universes/{universe['id']}/bindings",
            json={"subject_type": "EXTERNAL_IDENTITY", "subject_external_id": "teacher-x"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIn("id", response.json())

    def test_remove_pedagogical_universe_catalog_scope_success_and_not_found(self):
        universe = self.create_universe(external_id="univ-scope-rm", slug="univ-scope-rm", name="Universo Escopo Rm")
        node_id = self.seed_catalog_node()
        scope = self.client.post(
            f"/api/v1/admin/pedagogical-universes/{universe['id']}/catalog-scopes",
            json={"catalog_node_id": str(node_id), "scope_kind": "DISCIPLINE"},
        ).json()
        response = self.client.delete(f"/api/v1/admin/pedagogical-universes/catalog-scopes/{scope['id']}")
        self.assertEqual(response.status_code, 204, response.text)

        not_found = self.client.delete(f"/api/v1/admin/pedagogical-universes/catalog-scopes/{uuid4()}")
        self.assertEqual(not_found.status_code, 404, not_found.text)

    def test_remove_pedagogical_universe_binding_success_and_not_found(self):
        universe = self.create_universe(external_id="univ-bind-rm", slug="univ-bind-rm", name="Universo Bind Rm")
        binding = self.client.post(
            f"/api/v1/admin/pedagogical-universes/{universe['id']}/bindings",
            json={"subject_type": "EXTERNAL_IDENTITY", "subject_external_id": "teacher-y"},
        ).json()
        response = self.client.delete(f"/api/v1/admin/pedagogical-universes/bindings/{binding['id']}")
        self.assertEqual(response.status_code, 204, response.text)

        not_found = self.client.delete(f"/api/v1/admin/pedagogical-universes/bindings/{uuid4()}")
        self.assertEqual(not_found.status_code, 404, not_found.text)


if __name__ == "__main__":
    unittest.main()
