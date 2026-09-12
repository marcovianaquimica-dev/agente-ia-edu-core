import asyncio
import unittest
from datetime import datetime, timezone
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
from agente_ia_edu.api.routes.catalog import catalog_router
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


class Phase8ATeacherListBuilderHTTP(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                school_a = School(code="P8A-A", name="Escola A")
                school_b = School(code="P8A-B", name="Escola B")
                session.add_all([school_a, school_b])
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="teacher-a", school_id=school_a.id, role="TEACHER",
                    scope_type="CLASSROOM", scope_external_id="CLASS-A", active=True,
                    metadata_={"academic_year": "2026", "unit_id": "UNIT-A", "segment": "EM", "grade_level": "3"},
                ))
                await session.commit()
                return engine, factory, school_a.id, school_b.id

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
        app.include_router(catalog_router)
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
                    external_id="p8a-a", slug="p8a-a", name="Universo A", status="ACTIVE",
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
                        session.add_all([
                            QuestionOption(question_version_id=version.id, option_key="A", position=1, text="Alternativa A", is_valid_option=True),
                            QuestionOption(question_version_id=version.id, option_key="B", position=2, text="Alternativa B", is_valid_option=True),
                            ContentQuestionLink(content_node_id=solutions.id, question_version_id=version.id),
                        ])
                        candidates.append(version.id)
                blocked_question = Question(status="PUBLISHED", visibility_scope="SCHOOL", school_id=self.school_a, origin_type="TEACHER", validation_status="approved")
                session.add(blocked_question)
                await session.flush()
                blocked_version = QuestionVersion(question_id=blocked_question.id, version_kind="official_original", canonical_text="Fora", content_hash=uuid4().hex, recommended_difficulty="HARD")
                session.add(blocked_version)
                await session.flush()
                session.add(ContentQuestionLink(content_node_id=outside.id, question_version_id=blocked_version.id))
                await session.commit()
                return solutions.id, candidates, blocked_version.id
        return asyncio.run(seed())

    def create_list(self, content_id, quantity=20, easy=8, medium=8, hard=4):
        return self.client.post("/api/v1/teacher/materials", json={
            "title": "Lista de Exercicios - Solucoes", "content_node_id": str(content_id),
            "quantity": quantity, "difficulty_distribution": {"EASY": easy, "MEDIUM": medium, "HARD": hard},
        })

    def test_complete_draft_builder_flow(self):
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
        self.assertIn("stem", available.json()["items"][0])
        self.assertIn("alternatives", available.json()["items"][0])

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
        self.assertEqual(removed.status_code, 204)
        replacement = self.client.post(f"/api/v1/teacher/materials/{material['id']}/items", json={"question_version_id": str(candidates[20])})
        self.assertEqual(replacement.status_code, 201)
        reordered = self.client.patch(f"/api/v1/teacher/materials/{material['id']}/items/reorder", json={"item_ids": [item["id"] for item in reversed(self.client.get(f"/api/v1/teacher/materials/{material['id']}").json()["items"])]})
        self.assertEqual(reordered.status_code, 200, reordered.text)
        reopened = self.client.get(f"/api/v1/teacher/materials/{material['id']}").json()
        self.assertEqual([item["question_number"] for item in reopened["items"]], list(range(1, 21)))
        self.assertEqual(reopened["items"][0]["id"], reordered.json()["items"][0]["id"])
        self.assertEqual(sum(1 for item in reopened["items"] if item["difficulty"] == "EASY"), 7)
        self.assertEqual(sum(1 for item in reopened["items"] if item["difficulty"] == "HARD"), 5)

    def test_quantity_and_scope_protections(self):
        content_id, _, _ = self.seed_candidates()
        for payload in (
            {"quantity": 0, "difficulty_distribution": {"EASY": 0, "MEDIUM": 0, "HARD": 0}},
            {"quantity": 20, "difficulty_distribution": {"EASY": 8, "MEDIUM": 8, "HARD": 3}},
            {"quantity": 1000, "difficulty_distribution": {"EASY": 400, "MEDIUM": 400, "HARD": 200}},
        ):
            response = self.client.post("/api/v1/teacher/materials", json={"title": "Invalida", "content_node_id": str(content_id), **payload})
            self.assertEqual(response.status_code, 422, response.text)
        created = self.create_list(content_id)
        self.context["value"] = AuthenticatedUserContext(user_id="teacher-b", external_identity_id="teacher-b", role="TEACHER", school_id=self.school_b, scope_type="SCHOOL")
        self.assertEqual(self.client.get(f"/api/v1/teacher/materials/{created.json()['id']}").status_code, 403)

    def test_candidates_are_paginated_filtered_and_persist_individual_selection(self):
        content_id, candidates, _ = self.seed_candidates()
        material_id = self.create_list(content_id).json()["id"]
        page = self.client.get(
            f"/api/v1/teacher/materials/{material_id}/candidates?difficulty=EASY&page=2&limit=3"
        )
        self.assertEqual(page.status_code, 200, page.text)
        payload = page.json()
        self.assertEqual(payload["pagination"], {"page": 2, "limit": 3, "total": 8, "total_pages": 3})
        self.assertEqual(len(payload["items"]), 3)
        self.assertTrue(all(item["difficulty"] == "EASY" for item in payload["items"]))
        self.assertEqual(payload["items"][0]["source"], "Banco de Questoes")
        selected = self.client.post(
            f"/api/v1/teacher/materials/{material_id}/items",
            json={"question_version_id": str(candidates[1])},
        )
        self.assertEqual(selected.status_code, 201)
        reopened = self.client.get(f"/api/v1/teacher/materials/{material_id}").json()
        self.assertEqual(len(reopened["items"]), 1)
        self.assertEqual(reopened["items"][0]["difficulty"], "EASY")

    def test_catalog_children_do_not_leak_another_universe_branch(self):
        content_id, _, blocked_version_id = self.seed_candidates()
        async def parent_ids():
            async with self.session_factory() as session:
                allowed = await session.get(CatalogNode, content_id)
                foreign_root = CatalogNode(node_type="DISCIPLINE", name="Fisica", active=True)
                session.add(foreign_root)
                await session.flush()
                foreign_root.root_id = foreign_root.id
                session.add(CatalogNode(parent_id=foreign_root.id, root_id=foreign_root.id, node_type="CONTENT", name="Mecanica", active=True))
                await session.commit()
                return allowed.parent_id, foreign_root.id
        allowed_parent, blocked_parent = asyncio.run(parent_ids())
        allowed = self.client.get(f"/api/v1/catalog/nodes?parent_id={allowed_parent}")
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual([node["id"] for node in allowed.json()], [str(content_id)])
        bypass = self.client.get(f"/api/v1/catalog/nodes?parent_id={blocked_parent}")
        self.assertEqual(bypass.status_code, 200, bypass.text)
        self.assertEqual(bypass.json(), [])


if __name__ == "__main__":
    unittest.main()