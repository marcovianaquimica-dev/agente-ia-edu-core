"""HTTP-layer gap-fill tests for diagnostic.py (src/agente_ia_edu/api/routes/diagnostic.py).

tests/test_diagnostic_http_security.py already covers tenant isolation, entry
profile resumption and pedagogical-universe scoping - but every one of its
requested_universe_id scenarios goes through POST /diagnostic/entry/start,
never POST /diagnostic/start (which has its own, separate try/except around
resolve_active_universe with the identical 403-mapping and metadata-snapshot
code, entirely untouched). This file also targets what neither that file nor
test_initial_diagnostic.py (which calls the service directly, skipping the
HTTP layer) ever reached:

  * ValueError -> 400/404 mappings on save_diagnostic_entry, get_initial_
    diagnostic, answer_diagnostic_question and get_diagnostic_result for a
    diagnostic_id that simply does not exist.
  * get_diagnostic_result's cross-tenant 403 (a diagnostic that exists but
    belongs to a different student/school).
  * GET /student/mastery-map, which had ZERO HTTP coverage: neither the
    NOT_STARTED fallback nor the "delegate to get_diagnostic_result" branch.
"""

from __future__ import annotations

import asyncio
import unittest
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.diagnostic import diagnostic_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    ContentQuestionLink,
    InitialDiagnostic,
    Question,
    QuestionOption,
    QuestionVersion,
)
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.admin import PlatformAdminService
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService


class DiagnosticHttpGapsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        asyncio.run(self._create_schema())
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        self.school_a, self.school_b = asyncio.run(self._create_schools())
        self.identity = ExternalIdentityContext(
            provider="test", external_user_id="student-a", institution_id=str(self.school_a), classroom_id="CLASS_A"
        )
        self.app = FastAPI()
        self.app.include_router(diagnostic_router)

        async def current_identity():
            return self.identity

        self.app.dependency_overrides[get_current_identity] = current_identity
        self.app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    async def _create_schema(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def _create_schools(self):
        async with self.session_factory() as session:
            admin = PlatformAdminService(session)
            school_a = await admin.create_school(performed_by_external_id="admin", code="GAP_A", name="Gap A")
            school_b = await admin.create_school(performed_by_external_id="admin", code="GAP_B", name="Gap B")
            await session.commit()
            return school_a.id, school_b.id

    async def _create_diagnostic(self, student_id, school_id, *, status="IN_PROGRESS", academic_year="2026"):
        async with self.session_factory() as session:
            diagnostic = InitialDiagnostic(
                student_id=student_id, school_id=school_id, status=status, academic_year=academic_year,
            )
            session.add(diagnostic)
            await session.commit()
            return diagnostic.id

    # -- POST /diagnostic/start's next_question.options must show every real --
    # option, not only the correct one (found live 2026-09-28: is_valid_option
    # marks the CORRECT answer - set by QuestionPublicationService.publish_run
    # as is_valid_option=opt.is_correct - not "safe to show the student"; a
    # naive `if opt.is_valid_option` filter on the options list therefore
    # strips every wrong answer and leaves the diagnostic showing the correct
    # one as if it were the question's only alternative) -------------------

    def test_start_diagnostic_next_question_includes_every_option_not_only_the_correct_one(self):
        async def seed():
            async with self.session_factory() as session:
                root = CatalogNode(parent_id=None, root_id=None, node_type="DISCIPLINE", name="Química", active=True)
                session.add(root)
                await session.flush()
                root.root_id = root.id
                content = CatalogNode(parent_id=root.id, root_id=root.id, node_type="CONTENT", name="Soluções", active=True)
                session.add(content)
                await session.flush()

                question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
                session.add(question)
                await session.flush()
                version = QuestionVersion(
                    question_id=question.id, version_kind="official_original",
                    canonical_text="Questão de teste", content_hash="gap-multi-option",
                    recommended_difficulty="EASY",
                )
                session.add(version)
                await session.flush()
                session.add_all([
                    QuestionOption(question_version_id=version.id, option_key="A", position=1, text="Errada 1", is_valid_option=False),
                    QuestionOption(question_version_id=version.id, option_key="B", position=2, text="Errada 2", is_valid_option=False),
                    QuestionOption(question_version_id=version.id, option_key="C", position=3, text="Correta", is_valid_option=True),
                    ContentQuestionLink(content_node_id=content.id, question_version_id=version.id),
                ])
                await session.commit()

        asyncio.run(seed())
        response = self.client.post(
            "/api/v1/student/diagnostic/start",
            json={"classroom_id": "CLASS_A", "discipline": "Química"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        options = response.json()["next_question"]["options"]
        option_keys = {opt["option_key"] for opt in options}
        self.assertEqual(
            option_keys, {"A", "B", "C"},
            "next_question.options must include every real option (right and "
            "wrong), not only the one marked is_valid_option=True",
        )

    # -- PUT /diagnostic/{id}/entry with complete=true, right after a real ---
    # POST /diagnostic/start, must not 500 (found live 2026-09-28: /start
    # always creates the position=1 DiagnosticQuestionSelection immediately
    # (start_diagnostic's defer_questions defaults to False), but app.js
    # always shows the entry-profile form first and never uses that
    # next_question for a brand-new diagnostic (initDiagnosticView only skips
    # the profile form when entry_status is already "COMPLETED" - never true
    # right after /start). Submitting that profile calls
    # save_entry_profile(complete=True), which unconditionally tried to
    # INSERT another position=1 row, colliding with the one /start already
    # made: psycopg.errors.UniqueViolation on
    # uq_diagnostic_selections_diagnostic_position) --------------------------

    def test_completing_entry_profile_right_after_start_does_not_duplicate_position_one(self):
        async def seed():
            async with self.session_factory() as session:
                root = CatalogNode(parent_id=None, root_id=None, node_type="DISCIPLINE", name="Química", active=True)
                session.add(root)
                await session.flush()
                root.root_id = root.id
                content = CatalogNode(parent_id=root.id, root_id=root.id, node_type="CONTENT", name="Soluções", active=True)
                session.add(content)
                await session.flush()

                question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
                session.add(question)
                await session.flush()
                version = QuestionVersion(
                    question_id=question.id, version_kind="official_original",
                    canonical_text="Questão de teste", content_hash="gap-entry-dup",
                    recommended_difficulty="EASY",
                )
                session.add(version)
                await session.flush()
                session.add_all([
                    QuestionOption(question_version_id=version.id, option_key="A", position=1, text="Errada", is_valid_option=False),
                    QuestionOption(question_version_id=version.id, option_key="B", position=2, text="Correta", is_valid_option=True),
                    ContentQuestionLink(content_node_id=content.id, question_version_id=version.id),
                ])
                await session.commit()

        asyncio.run(seed())
        start = self.client.post(
            "/api/v1/student/diagnostic/start",
            json={"classroom_id": "CLASS_A", "discipline": "Química"},
        )
        self.assertEqual(start.status_code, 201, start.text)
        diagnostic_id = start.json()["diagnostic_id"]
        # app.js's real flow: for a brand-new diagnostic it ALWAYS shows the
        # entry-profile form next, regardless of next_question already being
        # populated by /start - reproduce that exact call.
        entry = self.client.put(f"/api/v1/student/diagnostic/{diagnostic_id}/entry", json={
            "preferred_name": "Aluno Teste", "study_objectives": ["ENEM"],
            "perceived_difficulties": [], "free_text": None,
            "needs_guidance": False, "step": "PREPARATION", "complete": True,
        })
        self.assertEqual(entry.status_code, 200, entry.text)
        self.assertIsNotNone(entry.json().get("next_question"))

    # -- POST /diagnostic/start with requested_universe_id (never hit via ----
    # this endpoint before - only /entry/start's identical code was tested) --

    def test_start_diagnostic_with_authorized_universe_snapshots_it(self):
        async def seed():
            async with self.session_factory() as session:
                service = PedagogicalUniverseService(session)
                universe = await service.create_universe(
                    external_id="GAP_SCHOOL_A", slug="gap-school-a", name="Gap School A universe",
                    owner_type="SCHOOL", owner_external_id=str(self.school_a),
                    performed_by_external_id="admin", status="ACTIVE",
                )
                await service.bind(universe_id=universe.id, subject_type="SCHOOL", subject_external_id=str(self.school_a))
                return universe.id

        universe_id = asyncio.run(seed())
        response = self.client.post("/api/v1/student/diagnostic/start", json={
            "classroom_id": "CLASS_A", "discipline": "Química",
            "requested_universe_id": str(universe_id),
        })
        self.assertEqual(response.status_code, 201, response.text)
        diagnostic_id = response.json()["diagnostic_id"]

        async def snapshot():
            async with self.session_factory() as session:
                diagnostic = await session.get(InitialDiagnostic, UUID(diagnostic_id))
                return diagnostic.metadata_["context_snapshot"]["pedagogical_universe"]

        self.assertEqual(asyncio.run(snapshot())["id"], str(universe_id))

    def test_start_diagnostic_with_unauthorized_requested_universe_is_denied(self):
        async def seed():
            async with self.session_factory() as session:
                service = PedagogicalUniverseService(session)
                universe = await service.create_universe(
                    external_id="GAP_SCHOOL_B", slug="gap-school-b", name="Gap School B universe",
                    owner_type="SCHOOL", owner_external_id=str(self.school_b),
                    performed_by_external_id="admin", status="ACTIVE",
                )
                return universe.id

        universe_id = asyncio.run(seed())
        response = self.client.post("/api/v1/student/diagnostic/start", json={
            "classroom_id": "CLASS_A", "discipline": "Química",
            "requested_universe_id": str(universe_id),
        })
        self.assertEqual(response.status_code, 403, response.text)

        async def count_diagnostics():
            async with self.session_factory() as session:
                from sqlalchemy import select
                rows = (await session.execute(
                    select(InitialDiagnostic).where(InitialDiagnostic.student_id == "student-a")
                )).scalars().all()
                return len(rows)

        # denied before any diagnostic is created (never a half-created row)
        self.assertEqual(asyncio.run(count_diagnostics()), 0)

    # -- ValueError -> 400/404 for a diagnostic_id that does not exist ------

    def test_save_entry_for_unknown_diagnostic_returns_400(self):
        response = self.client.put(f"/api/v1/student/diagnostic/{uuid4()}/entry", json={
            "preferred_name": "Ninguem"
        })
        self.assertEqual(response.status_code, 400, response.text)

    def test_get_unknown_diagnostic_returns_404(self):
        response = self.client.get(f"/api/v1/student/diagnostic/{uuid4()}")
        self.assertEqual(response.status_code, 404, response.text)

    def test_answer_unknown_diagnostic_returns_400(self):
        response = self.client.post(
            f"/api/v1/student/diagnostic/{uuid4()}/questions/{uuid4()}/answer",
            json={"is_unknown": True},
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_result_of_unknown_diagnostic_returns_404(self):
        response = self.client.get(f"/api/v1/student/diagnostic/{uuid4()}/result")
        self.assertEqual(response.status_code, 404, response.text)

    # -- get_diagnostic_result cross-tenant 403 ------------------------------

    def test_result_of_other_students_diagnostic_is_denied(self):
        diagnostic_id = asyncio.run(
            self._create_diagnostic("student-b", self.school_b, status="COMPLETED")
        )
        response = self.client.get(f"/api/v1/student/diagnostic/{diagnostic_id}/result")
        self.assertEqual(response.status_code, 403, response.text)

    # -- GET /student/mastery-map: zero HTTP coverage before this file -------

    def test_mastery_map_with_no_completed_diagnostic_returns_not_started(self):
        response = self.client.get("/api/v1/student/mastery-map")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "NOT_STARTED")
        self.assertEqual(body["mastery_map"], [])
        self.assertEqual(body["student_id"], "student-a")

    def test_mastery_map_with_completed_diagnostic_delegates_to_result(self):
        diagnostic_id = asyncio.run(
            self._create_diagnostic("student-a", self.school_a, status="COMPLETED")
        )
        response = self.client.get("/api/v1/student/mastery-map")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["diagnostic_id"], str(diagnostic_id))
        self.assertEqual(body["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
