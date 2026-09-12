import asyncio
import unittest
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.diagnostic import diagnostic_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, ContentQuestionLink, InitialDiagnostic, Question, QuestionOption, QuestionVersion
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService


class TestDiagnosticSegmentE2E(unittest.TestCase):
    def setUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        asyncio.run(self._create_schema())
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        self.identity = ExternalIdentityContext(provider="test", external_user_id="learner")
        self.app = FastAPI()
        self.app.include_router(diagnostic_router)

        async def current_identity():
            return self.identity

        self.app.dependency_overrides[get_current_identity] = current_identity
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    async def _create_schema(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def _seed_question(self, session, root, name, difficulty="EASY", segment=None, grade=None):
        content = CatalogNode(parent_id=root.id, root_id=root.root_id or root.id, node_type="CONTENT", name=name, position=1, active=True)
        session.add(content)
        await session.flush()
        question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC", metadata_={key: value for key, value in {"segment": segment, "grade_level": grade}.items() if value})
        session.add(question)
        await session.flush()
        version = QuestionVersion(question_id=question.id, version_kind="official_original", canonical_text=name, content_hash=f"{root.name}-{name}-{difficulty}-{segment}-{grade}", recommended_difficulty=difficulty)
        session.add(version)
        await session.flush()
        session.add_all([
            QuestionOption(question_version_id=version.id, option_key="A", position=1, text="Correta", is_valid_option=True),
            ContentQuestionLink(content_node_id=content.id, question_version_id=version.id),
        ])
        return content, version

    async def _root(self, session, name, position, parent=None):
        node = CatalogNode(parent_id=parent.id if parent else None, root_id=parent.root_id if parent else None, node_type="DISCIPLINE", name=name, position=position, active=True)
        session.add(node)
        await session.flush()
        if parent is None:
            node.root_id = node.id
        return node

    def _start_and_complete_entry(self, mode="GLOBAL", free_text=None, requested_universe_id=None):
        start = self.client.post("/api/v1/student/diagnostic/entry/start", json={"requested_universe_id": requested_universe_id})
        self.assertEqual(start.status_code, 201)
        diagnostic_id = start.json()["diagnostic_id"]
        entry = self.client.put(f"/api/v1/student/diagnostic/{diagnostic_id}/entry", json={"diagnostic_mode": mode, "free_text": free_text, "complete": True})
        self.assertEqual(entry.status_code, 200)
        return diagnostic_id, entry.json().get("next_question")

    def _answer(self, diagnostic_id, question, unknown=False):
        payload = {"is_unknown": True} if unknown else {"selected_option_id": question["options"][0]["id"]}
        response = self.client.post(f"/api/v1/student/diagnostic/{diagnostic_id}/questions/{question['selection_id']}/answer", json=payload)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_ensino_fundamental_http_e2e_filters_other_grades_and_keeps_global_coverage(self):
        async def seed():
            async with self.factory() as session:
                math = await self._root(session, "Matemática", 1)
                science = await self._root(session, "Ciências", 2)
                allowed_math, _ = await self._seed_question(session, math, "Álgebra 9", segment="ENSINO_FUNDAMENTAL", grade="9_ANO")
                allowed_science, _ = await self._seed_question(session, science, "Ciências 9", segment="ENSINO_FUNDAMENTAL", grade="9_ANO")
                await self._seed_question(session, math, "Álgebra 8", segment="ENSINO_FUNDAMENTAL", grade="8_ANO")
                await self._seed_question(session, science, "Ciências médio", segment="ENSINO_MEDIO", grade="3_SERIE")
                await session.commit()
                return allowed_math.id, allowed_science.id

        math_id, science_id = asyncio.run(seed())
        self.identity = ExternalIdentityContext(provider="test", external_user_id="fundamental", grade_level="9_ANO", metadata={"segment": "ENSINO_FUNDAMENTAL"})
        diagnostic_id, first = self._start_and_complete_entry()
        self.assertIn(first["content_node_id"], {str(math_id), str(science_id)})
        second = self._answer(diagnostic_id, first)["next_question"]
        self.assertNotEqual(second["content_node_id"], first["content_node_id"])
        self.assertIn(second["content_node_id"], {str(math_id), str(science_id)})
        final = self._answer(diagnostic_id, second)
        self.assertTrue(final["is_complete"])
        result = self.client.get(f"/api/v1/student/diagnostic/{diagnostic_id}/result")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["raw_result"]["correct"], 2)

    def test_ensino_medio_http_e2e_filters_fundamental_and_preserves_unknown(self):
        async def seed():
            async with self.factory() as session:
                math = await self._root(session, "Matemática", 1)
                chemistry = await self._root(session, "Química", 2)
                allowed_math, _ = await self._seed_question(session, math, "Funções 3", segment="ENSINO_MEDIO", grade="3_SERIE")
                allowed_chemistry, _ = await self._seed_question(session, chemistry, "Soluções 3", segment="ENSINO_MEDIO", grade="3_SERIE")
                await self._seed_question(session, math, "Funções 9", segment="ENSINO_FUNDAMENTAL", grade="9_ANO")
                await self._seed_question(session, chemistry, "Soluções 2", segment="ENSINO_MEDIO", grade="2_SERIE")
                await session.commit()
                return allowed_math.id, allowed_chemistry.id

        math_id, chemistry_id = asyncio.run(seed())
        self.identity = ExternalIdentityContext(provider="test", external_user_id="medio", grade_level="3_SERIE", metadata={"segment": "ENSINO_MEDIO"})
        diagnostic_id, first = self._start_and_complete_entry()
        self.assertEqual(first["content_node_id"], str(math_id))
        second = self._answer(diagnostic_id, first, unknown=True)["next_question"]
        self.assertEqual(second["content_node_id"], str(chemistry_id))
        self._answer(diagnostic_id, second)
        result = self.client.get(f"/api/v1/student/diagnostic/{diagnostic_id}/result").json()
        self.assertEqual(result["raw_result"]["unknown"], 1)
        self.assertEqual(result["raw_result"]["incorrect"], 0)

    def test_enem_partner_http_e2e_freezes_universe_and_excludes_math(self):
        async def seed():
            async with self.factory() as session:
                area = CatalogNode(node_type="AREA", name="Ciências da Natureza", active=True)
                session.add(area)
                await session.flush()
                area.root_id = area.id
                chemistry = await self._root(session, "Química", 1, area)
                math = await self._root(session, "Matemática", 2)
                chemistry_content, chemistry_version = await self._seed_question(session, chemistry, "Química ENEM")
                await self._seed_question(session, math, "Matemática ENEM")
                service = PedagogicalUniverseService(session)
                universe = await service.create_universe(external_id="QUIMICA_ENEM_E2E", slug="quimica-enem-e2e", name="Química ENEM", owner_type="PARTNER", owner_external_id="quimica-do-enem", performed_by_external_id="admin", configuration={"matrix": "ENEM_2026"}, configuration_version="ENEM_2026", status="ACTIVE")
                outside = await service.create_universe(external_id="OUTSIDE_E2E", slug="outside-e2e", name="Outside", owner_type="PLATFORM", owner_external_id=None, performed_by_external_id="admin", status="ACTIVE")
                await service.add_catalog_scope(universe_id=universe.id, catalog_node_id=chemistry.id, scope_kind="DISCIPLINE")
                await service.bind(universe_id=universe.id, subject_type="PRODUCT_CONTEXT", subject_external_id="quimica-do-enem")
                return universe.id, outside.id, chemistry_content.id, chemistry_version.id

        universe_id, outside_id, chemistry_content_id, chemistry_version_id = asyncio.run(seed())
        self.identity = ExternalIdentityContext(provider="test", external_user_id="enem", metadata={"product_context": "quimica-do-enem"})
        diagnostic_id, first = self._start_and_complete_entry(mode="ENEM", free_text="quero estudar matemática também", requested_universe_id=str(universe_id))
        self.assertEqual(first["content_node_id"], str(chemistry_content_id))
        self.assertEqual(first["question_version_id"], str(chemistry_version_id))
        self._answer(diagnostic_id, first)

        async def snapshot():
            async with self.factory() as session:
                diagnostic = await session.get(InitialDiagnostic, UUID(diagnostic_id))
                return diagnostic.metadata_["context_snapshot"]["pedagogical_universe"], diagnostic.metadata_["entry_profile"]

        universe_snapshot, profile = asyncio.run(snapshot())
        self.assertEqual(universe_snapshot["id"], str(universe_id))
        self.assertEqual(universe_snapshot["owner_type"], "PARTNER")
        self.assertEqual(universe_snapshot["configuration_version"], "ENEM_2026")
        self.assertEqual(profile["free_text"], "quero estudar matemática também")
        blocked = self.client.post("/api/v1/student/diagnostic/entry/start", json={"requested_universe_id": str(outside_id)})
        self.assertEqual(blocked.status_code, 403)
