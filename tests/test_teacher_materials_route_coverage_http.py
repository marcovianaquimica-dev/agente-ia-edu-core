"""
HTTP-level route-handler coverage for src/agente_ia_edu/api/routes/teacher_materials.py.

Overnight bug-hunt campaign, "smaller route files" zone. Targets the 4
`await session.commit()` sites in teacher_materials.py:
  1. create_teacher_material (POST /materials)
  2. add_teacher_material_item (POST /materials/{id}/items)
  3. remove_teacher_material_item (DELETE /materials/{id}/items/{item_id})
  4. reorder_teacher_material_items (PATCH /materials/{id}/items/reorder)

Static review showed all 4 sites already either (a) refresh every object
they later read a synchronous attribute on, or (b) return a plain Response
with no ORM attribute access after commit, or (c) re-query fresh state via
_material_response()/_question_rows() instead of reusing stale pre-commit
objects. This file is the required empirical proof: it drives the real
"complete draft builder flow" (create list -> list candidates -> add 20
items -> hit duplicate/full/forbidden guards -> remove one -> re-add ->
reorder) end-to-end through a session factory built EXACTLY like production
(async_sessionmaker(engine, class_=AsyncSession), default expire_on_commit=
True) - unlike tests/test_phase8a_teacher_list_builder_http.py's
Phase8ATeacherListBuilderHTTP fixture, which this is adapted from but with
expire_on_commit=False removed, since that relaxation is exactly what would
paper over a MissingGreenlet bug here.

TeacherMaterialsProdFidelityHTTP (the base class below) is also reused by
tests/test_question_modification_proposals_route_coverage_http.py, the same
way Phase8ATeacherListBuilderHTTP is reused across several existing test
files in this repo.
"""

import asyncio
import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
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
    ContentQuestionLink,
    PedagogicalUniverse,
    PedagogicalUniverseBinding,
    PedagogicalUniverseCatalogScope,
    Question,
    QuestionOption,
    QuestionVersion,
    School,
    UserSchoolLink,
)
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext


class TeacherMaterialsProdFidelityHTTP(unittest.TestCase):
    """Base fixture: same shape as Phase8ATeacherListBuilderHTTP
    (tests/test_phase8a_teacher_list_builder_http.py) but with the session
    factory built to match production fidelity (no expire_on_commit
    override, so the async default expire_on_commit=True applies)."""

    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            # Deliberately NOT relaxing expire_on_commit - see module docstring.
            factory = async_sessionmaker(engine, class_=AsyncSession)
            async with factory() as session:
                school_a = School(code=f"TMPF-A-{uuid4().hex[:6]}", name="Escola A")
                school_b = School(code=f"TMPF-B-{uuid4().hex[:6]}", name="Escola B")
                session.add_all([school_a, school_b])
                await session.flush()
                school_a_id, school_b_id = school_a.id, school_b.id
                session.add(UserSchoolLink(
                    external_user_id="teacher-a", school_id=school_a_id, role="TEACHER",
                    scope_type="CLASSROOM", scope_external_id="CLASS-A", active=True,
                    metadata_={"academic_year": "2026", "unit_id": "UNIT-A", "segment": "EM", "grade_level": "3"},
                ))
                await session.commit()
                return engine, factory, school_a_id, school_b_id

        self.engine, self.session_factory, self.school_a, self.school_b = asyncio.run(setup())
        self.context = {"value": AuthenticatedUserContext(
            user_id="teacher-a", external_identity_id="teacher-a", role="TEACHER",
            school_id=self.school_a, scope_type="CLASSROOM", scope_external_id="CLASS-A",
        )}
        self.identity = {"value": ExternalIdentityContext(
            provider="test", external_user_id="teacher-a", roles=("teacher",),
            institution_id=str(self.school_a),
        )}
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

    def seed_candidates(self):
        async def seed():
            async with self.session_factory() as session:
                discipline = CatalogNode(node_type="DISCIPLINE", name="Quimica", position=1, active=True)
                session.add(discipline)
                await session.flush()
                discipline.root_id = discipline.id
                solutions = CatalogNode(parent_id=discipline.id, root_id=discipline.id, node_type="CONTENT", name="Solucoes", position=1, active=True)
                outside = CatalogNode(parent_id=discipline.id, root_id=discipline.id, node_type="CONTENT", name="Estequiometria", position=2, active=True)
                session.add_all([solutions, outside])
                universe = PedagogicalUniverse(
                    external_id=f"tmpf-{uuid4().hex[:8]}", slug=f"tmpf-{uuid4().hex[:8]}", name="Universo A", status="ACTIVE",
                    owner_type="SCHOOL", owner_external_id=str(self.school_a), configuration_version="v1",
                )
                session.add(universe)
                await session.flush()
                session.add_all([
                    PedagogicalUniverseBinding(universe_id=universe.id, subject_type="EXTERNAL_IDENTITY", subject_external_id="teacher-a"),
                    PedagogicalUniverseCatalogScope(universe_id=universe.id, catalog_node_id=solutions.id, scope_kind="CONTENT", include_descendants=True),
                ])
                candidates = []
                for difficulty, count in (("EASY", 8), ("MEDIUM", 8), ("HARD", 5)):
                    for number in range(count):
                        question = Question(
                            question_type="MULTIPLE_CHOICE", school_id=self.school_a,
                            author_external_id="teacher-a", owner_external_id="teacher-a", origin_type="TEACHER",
                            status="PUBLISHED", visibility_scope="SCHOOL", validation_status="approved",
                        )
                        session.add(question)
                        await session.flush()
                        version = QuestionVersion(
                            question_id=question.id, version_kind="official_original",
                            canonical_text=f"Questao {difficulty} {number}", statement=f"Enunciado completo {difficulty} {number}",
                            content_hash=uuid4().hex, recommended_difficulty=difficulty,
                        )
                        session.add(version)
                        await session.flush()
                        version_id = version.id
                        session.add_all([
                            QuestionOption(question_version_id=version_id, option_key="A", position=1, text="Alternativa A", is_valid_option=True),
                            QuestionOption(question_version_id=version_id, option_key="B", position=2, text="Alternativa B", is_valid_option=True),
                            ContentQuestionLink(content_node_id=solutions.id, question_version_id=version_id),
                        ])
                        candidates.append(version_id)
                blocked_question = Question(status="PUBLISHED", visibility_scope="SCHOOL", school_id=self.school_a, origin_type="TEACHER", validation_status="approved")
                session.add(blocked_question)
                await session.flush()
                blocked_version = QuestionVersion(question_id=blocked_question.id, version_kind="official_original", canonical_text="Fora", content_hash=uuid4().hex, recommended_difficulty="HARD")
                session.add(blocked_version)
                await session.flush()
                blocked_version_id = blocked_version.id
                session.add(ContentQuestionLink(content_node_id=outside.id, question_version_id=blocked_version_id))
                solutions_id = solutions.id
                await session.commit()
                return solutions_id, candidates, blocked_version_id
        return asyncio.run(seed())

    def create_list(self, content_id, quantity=20, easy=8, medium=8, hard=4):
        return self.client.post("/api/v1/teacher/materials", json={
            "title": "Lista de Exercicios - Solucoes", "content_node_id": str(content_id),
            "quantity": quantity, "difficulty_distribution": {"EASY": easy, "MEDIUM": medium, "HARD": hard},
        })


class TeacherMaterialsRouteCoverageHTTP(TeacherMaterialsProdFidelityHTTP):
    def test_complete_draft_builder_flow_hits_all_four_commit_sites_cleanly(self):
        """Adapted from Phase8ATeacherListBuilderHTTP.test_complete_draft_builder_flow,
        run here under production-fidelity expire_on_commit=True. Exercises,
        in order: commit site 1 (create), commit site 2 (add item, x21 incl.
        the 409/403 guard branches which do NOT commit), commit site 3
        (remove), commit site 2 again (re-add), commit site 4 (reorder)."""
        content_id, candidates, blocked_version_id = self.seed_candidates()
        created = self.create_list(content_id)
        self.assertEqual(created.status_code, 201, created.text)
        material = created.json()
        self.assertEqual(material["material_type"], "EXERCISE_LIST")
        self.assertEqual(material["status"], "draft")
        self.assertEqual(material["configuration"]["quantity"], 20)

        available = self.client.get(f"/api/v1/teacher/materials/{material['id']}/candidates")
        self.assertEqual(available.status_code, 200, available.text)
        self.assertEqual(available.json()["availability"], {"EASY": 8, "MEDIUM": 8, "HARD": 5})
        self.assertEqual(len(available.json()["items"]), 21)

        for position, version_id in enumerate(candidates[:20], start=1):
            response = self.client.post(f"/api/v1/teacher/materials/{material['id']}/items", json={"question_version_id": str(version_id)})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["question_number"], position)
        duplicate = self.client.post(f"/api/v1/teacher/materials/{material['id']}/items", json={"question_version_id": str(candidates[0])})
        self.assertEqual(duplicate.status_code, 409)
        full_list = self.client.post(f"/api/v1/teacher/materials/{material['id']}/items", json={"question_version_id": str(candidates[20])})
        self.assertEqual(full_list.status_code, 409)
        forbidden = self.client.post(f"/api/v1/teacher/materials/{material['id']}/items", json={"question_version_id": str(blocked_version_id)})
        self.assertEqual(forbidden.status_code, 403)

        detail = self.client.get(f"/api/v1/teacher/materials/{material['id']}")
        self.assertEqual(len(detail.json()["items"]), 20)
        removed = self.client.delete(f"/api/v1/teacher/materials/{material['id']}/items/{detail.json()['items'][0]['id']}")
        self.assertEqual(removed.status_code, 204, removed.text)
        replacement = self.client.post(f"/api/v1/teacher/materials/{material['id']}/items", json={"question_version_id": str(candidates[20])})
        self.assertEqual(replacement.status_code, 201, replacement.text)
        reordered = self.client.patch(
            f"/api/v1/teacher/materials/{material['id']}/items/reorder",
            json={"item_ids": [item["id"] for item in reversed(self.client.get(f"/api/v1/teacher/materials/{material['id']}").json()["items"])]},
        )
        self.assertEqual(reordered.status_code, 200, reordered.text)
        reopened = self.client.get(f"/api/v1/teacher/materials/{material['id']}").json()
        self.assertEqual([item["question_number"] for item in reopened["items"]], list(range(1, 21)))
        self.assertEqual(reopened["items"][0]["id"], reordered.json()["items"][0]["id"])


if __name__ == "__main__":
    unittest.main()
