"""PostgreSQL HTTP proof for the ``possible_prerequisite_gap`` contract gap.

``InitialDiagnosticService.get_diagnostic_result`` has built a
``possible_prerequisite_gap`` sub-dict into each ``probable_gaps`` item since
Phase 13 (see ``services/initial_diagnostic.py``), and ``reception.js``
already reads ``item.possible_prerequisite_gap`` when rendering a candidate's
diagnostic result. But the ``ProbableGap`` Pydantic response schema
(``api/schemas/diagnostic.py``) never declared that field, so FastAPI/Pydantic
silently dropped it from the HTTP response of
``GET /api/v1/student/diagnostic/{id}/result`` - the same class of bug fixed
for ``coverage_status`` on ``ContentMasteryEstimate``. This test proves the
field survives the real HTTP + Pydantic response_model layer against a real
PostgreSQL-backed run (not the in-memory SQLite unit test in
``test_initial_diagnostic.py``, which calls the service directly and never
exercises the response schema).
"""

import asyncio
import os
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.diagnostic import diagnostic_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    ContentQuestionLink,
    Question,
    QuestionOption,
    QuestionVersion,
)
from agente_ia_edu.identity import ExternalIdentityContext
from tests._postgres_test_db import create_database, drop_database


class DiagnosticPossiblePrerequisiteGapPostgreSQLE2E(unittest.TestCase):
    database_name = "agente_ia_edu_gap_contract_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "GAP_CONTRACT_TEST_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "GAP_CONTRACT_TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest("PostgreSQL de teste indisponivel") from exc
        cls._drop_database()
        create_database(cls.admin_url, cls.database_name)
        cls.engine = create_async_engine(cls.async_database_url)
        cls.session_factory = async_sessionmaker(
            cls.engine, class_=AsyncSession, expire_on_commit=False
        )

        async def create_schema():
            async with cls.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

        asyncio.run(create_schema())
        cls.identity = ExternalIdentityContext(
            provider="test",
            external_user_id="student:gap-contract-pg",
            roles=("student",),
        )
        app = FastAPI()
        app.include_router(diagnostic_router)
        app.dependency_overrides[get_session_factory] = lambda: cls.session_factory
        app.dependency_overrides[get_current_identity] = lambda: cls.identity
        cls.app = app
        cls.client = TestClient(app)
        asyncio.run(cls._seed())

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "client"):
            cls.client.close()
            cls.app.dependency_overrides.clear()
            asyncio.run(cls.engine.dispose())
        cls._drop_database()

    @classmethod
    def _admin_execute(cls, statement):
        engine = create_engine(
            cls.admin_url,
            connect_args={"autocommit": True},
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        try:
            with engine.connect() as connection:
                return connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    def _drop_database(cls):
        drop_database(cls.admin_url, cls.database_name)

    @classmethod
    async def _seed(cls):
        async with cls.session_factory() as session:
            root = CatalogNode(node_type="DISCIPLINE", name="Quimica", position=1, active=True)
            session.add(root)
            await session.flush()
            root.root_id = root.id

            parent_node = CatalogNode(
                parent_id=root.id, root_id=root.id, node_type="CONTENT",
                code="GAP-PG-PARENT", name="Concentracao de Solucoes",
                position=1, active=True,
            )
            session.add(parent_node)
            await session.flush()

            content_node = CatalogNode(
                parent_id=parent_node.id, root_id=root.id, node_type="SUBCONTENT",
                code="GAP-PG-CHILD", name="Diluicao de Solucoes",
                position=2, active=True,
            )
            session.add(content_node)
            await session.flush()

            question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
            session.add(question)
            await session.flush()
            version = QuestionVersion(
                question_id=question.id, version_kind="official_original",
                canonical_text="Questao de diluicao", statement="Questao de diluicao",
                content_hash="gap-pg-hash", recommended_difficulty="EASY",
            )
            session.add(version)
            await session.flush()
            session.add_all([
                QuestionOption(question_version_id=version.id, option_key="A", position=1, text="Correta", is_valid_option=True),
                QuestionOption(question_version_id=version.id, option_key="B", position=2, text="Incorreta", is_valid_option=False),
                ContentQuestionLink(content_node_id=content_node.id, question_version_id=version.id),
            ])
            await session.commit()

        cls.parent_content_node_id = str(parent_node.id)

    def test_probable_gap_result_includes_possible_prerequisite_gap_over_http(self):
        start = self.client.post(
            "/api/v1/student/diagnostic/start",
            json={"academic_year": "2026", "discipline": "Quimica"},
        )
        self.assertEqual(start.status_code, 201, start.text)
        diagnostic_id = start.json()["diagnostic_id"]
        question = start.json()["next_question"]
        self.assertIsNotNone(question)

        # Answer with response_text (not selected_option_id) so is_correct
        # stays falsy/None and the single EASY question keeps scoring low,
        # matching the existing SQLite unit test's approach
        # (test_24_prerequisite_gap_is_an_evidence_not_a_certainty).
        complete = False
        for _ in range(3):
            answer = self.client.post(
                f"/api/v1/student/diagnostic/{diagnostic_id}/questions/{question['selection_id']}/answer",
                json={"response_text": "resposta incorreta"},
            )
            self.assertEqual(answer.status_code, 200, answer.text)
            body = answer.json()
            complete = body["is_complete"]
            question = body.get("next_question")
            if complete or question is None:
                break

        result = self.client.get(f"/api/v1/student/diagnostic/{diagnostic_id}/result")
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()

        self.assertTrue(payload["probable_gaps"], "expected at least one probable gap")
        gap = payload["probable_gaps"][0]
        self.assertTrue(gap["prerequisite_check_required"])

        # This is the actual regression check: before the ProbableGap schema
        # declared `possible_prerequisite_gap`, FastAPI/Pydantic silently
        # dropped this key from the HTTP response even though the service
        # dict always included it (see InitialDiagnosticService.
        # get_diagnostic_result, services/initial_diagnostic.py).
        self.assertIn(
            "possible_prerequisite_gap", gap,
            "possible_prerequisite_gap missing from HTTP response - "
            "ProbableGap schema drops it silently (same bug class as "
            "coverage_status on ContentMasteryEstimate)",
        )
        prereq = gap["possible_prerequisite_gap"]
        self.assertIsNotNone(prereq)
        self.assertEqual(prereq["content_node_id"], self.parent_content_node_id)
        self.assertIn("content_name", prereq)
        self.assertIn("confidence", prereq)
        self.assertIn("evidence_origin", prereq)


if __name__ == "__main__":
    unittest.main()
