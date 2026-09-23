"""HTTP-layer tests for the ERROR branches of teacher_materials.py that the
existing tests/test_phase8a_teacher_list_builder_http.py happy-path suite
does not reach: role/ownership/scope authorization denials, the "no draft
version" conflict, pedagogical-universe PermissionError mapping, and payload
validation on reorder. Confirmed against coverage (concurrency=greenlet,thread
- see tests/test_phase8a_teacher_list_builder_http.py's sibling audit) that
these lines were genuinely unexecuted by any existing test.
"""

import asyncio
import unittest
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.routes.teacher_materials import router as teacher_materials_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    PedagogicalUniverse,
    PedagogicalUniverseBinding,
    PedagogicalUniverseCatalogScope,
    School,
    UserSchoolLink,
)
from agente_ia_edu.db.models.assessments import Assessment, AssessmentItem, AssessmentVersion
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext


class TeacherMaterialsErrorPathsHTTP(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                school_a = School(code="TMEP-A", name="Escola A")
                session.add(school_a)
                await session.flush()

                discipline = CatalogNode(node_type="DISCIPLINE", name="Quimica", position=1, active=True)
                session.add(discipline)
                await session.flush()
                discipline.root_id = discipline.id
                solutions = CatalogNode(parent_id=discipline.id, root_id=discipline.id, node_type="CONTENT", name="Solucoes", position=1, active=True)
                unbound = CatalogNode(parent_id=discipline.id, root_id=discipline.id, node_type="CONTENT", name="Fora do Universo", position=2, active=True)
                inactive_in_scope = CatalogNode(parent_id=discipline.id, root_id=discipline.id, node_type="CONTENT", name="Desativado", position=3, active=True)
                session.add_all([solutions, unbound, inactive_in_scope])
                await session.flush()

                universe = PedagogicalUniverse(
                    external_id="tmep-a", slug="tmep-a", name="Universo A", status="ACTIVE",
                    owner_type="SCHOOL", owner_external_id=str(school_a.id), configuration_version="v1",
                )
                session.add(universe)
                await session.flush()
                session.add_all([
                    PedagogicalUniverseBinding(universe_id=universe.id, subject_type="EXTERNAL_IDENTITY", subject_external_id="teacher-a"),
                    PedagogicalUniverseCatalogScope(universe_id=universe.id, catalog_node_id=solutions.id, scope_kind="CONTENT", include_descendants=True),
                    PedagogicalUniverseCatalogScope(universe_id=universe.id, catalog_node_id=inactive_in_scope.id, scope_kind="CONTENT", include_descendants=True),
                ])

                # A second universe scoped ONLY to "unbound" - so a caller
                # bound exclusively to it has a real, active universe (no
                # PermissionError) but "solutions" is genuinely outside it.
                other_universe = PedagogicalUniverse(
                    external_id="tmep-b", slug="tmep-b", name="Universo B", status="ACTIVE",
                    owner_type="SCHOOL", owner_external_id=str(school_a.id), configuration_version="v1",
                )
                session.add(other_universe)
                await session.flush()
                session.add_all([
                    PedagogicalUniverseBinding(universe_id=other_universe.id, subject_type="EXTERNAL_IDENTITY", subject_external_id="coord-outsideuniverse"),
                    PedagogicalUniverseCatalogScope(universe_id=other_universe.id, catalog_node_id=unbound.id, scope_kind="CONTENT", include_descendants=True),
                ])
                session.add(UserSchoolLink(
                    external_user_id="coord-outsideuniverse", school_id=school_a.id, role="COORDINATOR",
                    scope_type="SCHOOL", active=True,
                ))

                # teacher-a: bound to the universe, owns materials it creates.
                session.add(UserSchoolLink(
                    external_user_id="teacher-a", school_id=school_a.id, role="TEACHER",
                    scope_type="CLASSROOM", scope_external_id="CLASS-A", active=True,
                ))
                # teacher-b: same school/classroom scope, but a DIFFERENT teacher -
                # for the owner-mismatch (403) check.
                session.add(UserSchoolLink(
                    external_user_id="teacher-b", school_id=school_a.id, role="TEACHER",
                    scope_type="CLASSROOM", scope_external_id="CLASS-A", active=True,
                ))
                # coord-b: COORDINATOR (author-eligible role) but a DIFFERENT
                # CLASSROOM scope than the material - for the academic-scope
                # mismatch (403) check.
                session.add(UserSchoolLink(
                    external_user_id="coord-b", school_id=school_a.id, role="COORDINATOR",
                    scope_type="CLASSROOM", scope_external_id="CLASS-B", active=True,
                ))
                # student-a: not an author-eligible role - for the 403 role check.
                session.add(UserSchoolLink(
                    external_user_id="student-a", school_id=school_a.id, role="STUDENT",
                    scope_type="CLASSROOM", scope_external_id="CLASS-A", active=True,
                ))
                # teacher-nouniverse: author-eligible TEACHER role, but bound to
                # NO pedagogical universe at all - for the PermissionError->403
                # check on create (where there's no existing material to own).
                session.add(UserSchoolLink(
                    external_user_id="teacher-nouniverse", school_id=school_a.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                # coord-nouniverse: COORDINATOR (author-eligible, and exempt from
                # _load_material's TEACHER-only owner check) with SCHOOL scope
                # (exempt from the academic-scope check too), bound to NO
                # pedagogical universe - isolates the PermissionError->403 branch
                # on candidates/add-item from the owner/scope checks that would
                # otherwise fire first for a plain TEACHER.
                session.add(UserSchoolLink(
                    external_user_id="coord-nouniverse", school_id=school_a.id, role="COORDINATOR",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()
                return (
                    engine, factory, school_a.id, solutions.id, unbound.id, inactive_in_scope.id,
                )

        (
            self.engine, self.session_factory, self.school_a,
            self.solutions_id, self.unbound_id, self.inactive_in_scope_id,
        ) = asyncio.run(setup())

        self.context = {"value": self._teacher_a_context()}
        self.identity = {"value": self._teacher_a_identity()}
        app = FastAPI()
        app.include_router(teacher_materials_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_authenticated_context] = lambda: self.context["value"]
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def _teacher_a_context(self):
        return AuthenticatedUserContext(
            user_id="teacher-a", external_identity_id="teacher-a", role="TEACHER",
            school_id=self.school_a, scope_type="CLASSROOM", scope_external_id="CLASS-A",
        )

    def _teacher_a_identity(self):
        return ExternalIdentityContext(provider="test", external_user_id="teacher-a", roles=("teacher",))

    def create_material(self, content_id, quantity=1, easy=1, medium=0, hard=0):
        response = self.client.post("/api/v1/teacher/materials", json={
            "title": "Lista", "content_node_id": str(content_id),
            "quantity": quantity, "difficulty_distribution": {"EASY": easy, "MEDIUM": medium, "HARD": hard},
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    # ---- _require_author: role not in AUTHOR_ROLES (line 44) ----

    def test_student_role_cannot_list_materials(self):
        self.context["value"] = AuthenticatedUserContext(
            user_id="student-a", external_identity_id="student-a", role="STUDENT",
            school_id=self.school_a, scope_type="CLASSROOM", scope_external_id="CLASS-A",
        )
        response = self.client.get("/api/v1/teacher/materials")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("author role required", response.json()["detail"])

    def test_require_author_denies_when_school_id_is_none(self):
        self.context["value"] = AuthenticatedUserContext(
            user_id="indep-teacher", external_identity_id="indep-teacher", role="TEACHER",
            school_id=None, scope_type="PLATFORM",
        )
        response = self.client.get("/api/v1/teacher/materials")
        self.assertEqual(response.status_code, 403, response.text)

    # ---- _load_material: material not found / wrong type (line 52) ----

    def test_get_material_nonexistent_id_returns_404(self):
        response = self.client.get(f"/api/v1/teacher/materials/{uuid4()}")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Exercise list not found")

    # ---- _load_material: owner scope denied for TEACHER role (line 56) ----

    def test_teacher_cannot_access_another_teachers_material(self):
        material = self.create_material(self.solutions_id)
        self.context["value"] = AuthenticatedUserContext(
            user_id="teacher-b", external_identity_id="teacher-b", role="TEACHER",
            school_id=self.school_a, scope_type="CLASSROOM", scope_external_id="CLASS-A",
        )
        response = self.client.get(f"/api/v1/teacher/materials/{material['id']}")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("owner scope denied", response.json()["detail"])

    # ---- _load_material: academic scope denied (line 61) ----

    def test_coordinator_with_different_classroom_scope_denied(self):
        material = self.create_material(self.solutions_id)
        self.context["value"] = AuthenticatedUserContext(
            user_id="coord-b", external_identity_id="coord-b", role="COORDINATOR",
            school_id=self.school_a, scope_type="CLASSROOM", scope_external_id="CLASS-B",
        )
        response = self.client.get(f"/api/v1/teacher/materials/{material['id']}")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("academic scope denied", response.json()["detail"])

    # ---- _load_material: no draft version (line 69) ----

    def test_material_with_no_draft_version_returns_409(self):
        async def make_versionless_material():
            async with self.session_factory() as session:
                assessment = Assessment(
                    school_id=self.school_a, created_by_external_identity="teacher-a",
                    owner_external_id="teacher-a", visibility_scope="PRIVATE", origin_type="TEACHER",
                    material_type="EXERCISE_LIST", scope_type="CLASSROOM", scope_external_id="CLASS-A",
                    title="Sem versao", status="draft", metadata_={"content_node_id": str(self.solutions_id), "quantity": 1},
                )
                session.add(assessment)
                await session.commit()
                return assessment.id

        material_id = asyncio.run(make_versionless_material())
        response = self.client.get(f"/api/v1/teacher/materials/{material_id}")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "Exercise list has no draft version")

    # ---- create_teacher_material: no authorized universe (lines 173-174) ----

    def test_create_material_denied_when_teacher_has_no_pedagogical_universe(self):
        self.context["value"] = AuthenticatedUserContext(
            user_id="teacher-nouniverse", external_identity_id="teacher-nouniverse", role="TEACHER",
            school_id=self.school_a, scope_type="SCHOOL",
        )
        self.identity["value"] = ExternalIdentityContext(provider="test", external_user_id="teacher-nouniverse", roles=("teacher",))
        response = self.client.post("/api/v1/teacher/materials", json={
            "title": "Lista", "content_node_id": str(self.solutions_id),
            "quantity": 1, "difficulty_distribution": {"EASY": 1, "MEDIUM": 0, "HARD": 0},
        })
        self.assertEqual(response.status_code, 403, response.text)

    # ---- create_teacher_material: content outside universe (line 176) ----

    def test_create_material_denied_when_content_outside_universe(self):
        response = self.client.post("/api/v1/teacher/materials", json={
            "title": "Lista", "content_node_id": str(self.unbound_id),
            "quantity": 1, "difficulty_distribution": {"EASY": 1, "MEDIUM": 0, "HARD": 0},
        })
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("outside the authorized pedagogical universe", response.json()["detail"])

    # ---- create_teacher_material: content inactive (line 179) ----

    def test_create_material_denied_when_content_is_inactive(self):
        async def deactivate():
            async with self.session_factory() as session:
                node = await session.get(CatalogNode, self.inactive_in_scope_id)
                node.active = False
                await session.commit()

        asyncio.run(deactivate())
        response = self.client.post("/api/v1/teacher/materials", json={
            "title": "Lista", "content_node_id": str(self.inactive_in_scope_id),
            "quantity": 1, "difficulty_distribution": {"EASY": 1, "MEDIUM": 0, "HARD": 0},
        })
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Content not found")

    # ---- list_material_candidates: no universe / outside universe / bad filter (lines 262-263, 265, 277) ----

    def test_candidates_denied_when_teacher_has_no_pedagogical_universe(self):
        material = self.create_material(self.solutions_id)
        self.context["value"] = AuthenticatedUserContext(
            user_id="coord-nouniverse", external_identity_id="coord-nouniverse", role="COORDINATOR",
            school_id=self.school_a, scope_type="SCHOOL",
        )
        self.identity["value"] = ExternalIdentityContext(provider="test", external_user_id="coord-nouniverse", roles=("coordinator",))
        # coord-nouniverse doesn't own the material, but COORDINATOR/SCHOOL
        # scope bypasses _load_material's owner/academic-scope checks
        # entirely, so this isolates the universe PermissionError->403 branch.
        response = self.client.get(f"/api/v1/teacher/materials/{material['id']}/candidates")
        self.assertEqual(response.status_code, 403, response.text)

    def test_candidates_denied_when_content_outside_callers_universe(self):
        material = self.create_material(self.solutions_id)
        self.context["value"] = AuthenticatedUserContext(
            user_id="coord-outsideuniverse", external_identity_id="coord-outsideuniverse", role="COORDINATOR",
            school_id=self.school_a, scope_type="SCHOOL",
        )
        self.identity["value"] = ExternalIdentityContext(provider="test", external_user_id="coord-outsideuniverse", roles=("coordinator",))
        response = self.client.get(f"/api/v1/teacher/materials/{material['id']}/candidates")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("outside the authorized pedagogical universe", response.json()["detail"])

    def test_candidates_invalid_difficulty_filter_returns_422(self):
        material = self.create_material(self.solutions_id)
        response = self.client.get(f"/api/v1/teacher/materials/{material['id']}/candidates?difficulty=IMPOSSIBLE")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"], "Unsupported difficulty filter")

    # ---- add_teacher_material_item: no universe (lines 318-319) ----

    def test_add_item_denied_when_teacher_has_no_pedagogical_universe(self):
        material = self.create_material(self.solutions_id)
        self.context["value"] = AuthenticatedUserContext(
            user_id="coord-nouniverse", external_identity_id="coord-nouniverse", role="COORDINATOR",
            school_id=self.school_a, scope_type="SCHOOL",
        )
        self.identity["value"] = ExternalIdentityContext(provider="test", external_user_id="coord-nouniverse", roles=("coordinator",))
        response = self.client.post(
            f"/api/v1/teacher/materials/{material['id']}/items",
            json={"question_version_id": str(uuid4())},
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_add_item_denied_when_content_outside_callers_universe(self):
        material = self.create_material(self.solutions_id)
        self.context["value"] = AuthenticatedUserContext(
            user_id="coord-outsideuniverse", external_identity_id="coord-outsideuniverse", role="COORDINATOR",
            school_id=self.school_a, scope_type="SCHOOL",
        )
        self.identity["value"] = ExternalIdentityContext(provider="test", external_user_id="coord-outsideuniverse", roles=("coordinator",))
        response = self.client.post(
            f"/api/v1/teacher/materials/{material['id']}/items",
            json={"question_version_id": str(uuid4())},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("outside the authorized pedagogical universe", response.json()["detail"])

    # ---- remove_teacher_material_item: item not found (line 366) ----

    def test_remove_item_nonexistent_returns_404(self):
        material = self.create_material(self.solutions_id)
        response = self.client.delete(f"/api/v1/teacher/materials/{material['id']}/items/{uuid4()}")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Exercise list item not found")

    def test_remove_item_belonging_to_another_material_returns_404(self):
        material_1 = self.create_material(self.solutions_id, quantity=5, easy=5)
        material_2 = self.create_material(self.solutions_id, quantity=5, easy=5)
        # Build an item on material_1's version directly, then try deleting
        # it through material_2's path - the version mismatch must 404.
        async def add_item_to_material_1():
            async with self.session_factory() as session:
                version = await session.scalar(
                    select(AssessmentVersion)
                    .where(AssessmentVersion.assessment_id == UUID(material_1["id"]))
                    .order_by(AssessmentVersion.version_number.desc()).limit(1)
                )
                item = AssessmentItem(assessment_version_id=version.id, question_version_id=uuid4(), position=1)
                session.add(item)
                await session.commit()
                return item.id

        item_id = asyncio.run(add_item_to_material_1())
        response = self.client.delete(f"/api/v1/teacher/materials/{material_2['id']}/items/{item_id}")
        self.assertEqual(response.status_code, 404, response.text)

    # ---- reorder_teacher_material_items: payload mismatch (line 396) ----

    def test_reorder_with_missing_item_id_returns_422(self):
        material = self.create_material(self.solutions_id, quantity=5, easy=5)
        response = self.client.patch(
            f"/api/v1/teacher/materials/{material['id']}/items/reorder",
            json={"item_ids": [str(uuid4())]},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"], "Reorder must contain every list item exactly once")


if __name__ == "__main__":
    unittest.main()
