"""HTTP-layer coverage for api/routes/questions.py.

Test coverage audit (2026-09): this route file scored 26% on a naive
`coverage report` run, but a large share of that was a measurement artifact
- pytest-cov under-counts anything executed inside SQLAlchemy's async
greenlet bridge or Starlette TestClient's worker thread unless coverage's
`concurrency = ["thread", "greenlet"]` is configured (now set in
pyproject.toml's [tool.coverage.run] - see this audit's summary). With that
fix, the TRUE baseline (existing tests only) was 52%. This file closes most
of the real remaining gap: the "real" list_questions query branch (which
every existing test bypassed via a FakeQuestionService with no `.repository`
attribute), the authoring/version/status-transition/approval routes, and the
403/404 authorization guards shared by `_load_question_for_view` /
`_load_question_for_manage`.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.questions import get_question_service, router as questions_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    PedagogicalClassification,
    Question,
    QuestionOption,
    QuestionVersion,
    School,
    UserSchoolLink,
)
from agente_ia_edu.db.models.official import QuestionApproval
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.repositories.questions import QuestionRepository
from agente_ia_edu.services.questions import QuestionService


class QuestionsRouteCoverageTests(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                school_a = School(code="QRC-A", name="Escola Route Coverage A")
                school_b = School(code="QRC-B", name="Escola Route Coverage B")
                session.add_all([school_a, school_b])
                await session.flush()
                session.add_all([
                    UserSchoolLink(external_user_id="teacher-a", school_id=school_a.id, role="TEACHER",
                                   scope_type="SCHOOL", active=True),
                    UserSchoolLink(external_user_id="teacher-a2", school_id=school_a.id, role="TEACHER",
                                   scope_type="SCHOOL", active=True),
                    UserSchoolLink(external_user_id="director-a", school_id=school_a.id, role="DIRECTOR",
                                   scope_type="SCHOOL", active=True),
                    UserSchoolLink(external_user_id="teacher-b", school_id=school_b.id, role="TEACHER",
                                   scope_type="SCHOOL", active=True),
                    UserSchoolLink(external_user_id="student-a", school_id=school_a.id, role="STUDENT",
                                   scope_type="SCHOOL", active=True),
                    UserSchoolLink(external_user_id="admin-1", school_id=school_a.id, role="PLATFORM_ADMIN",
                                   scope_type="SCHOOL", active=True),
                ])
                await session.commit()
                return engine, factory, school_a.id, school_b.id

        self.engine, self.session_factory, self.school_a, self.school_b = asyncio.run(setup())
        self.identity = {"value": ExternalIdentityContext(
            provider="test", external_user_id="teacher-a", roles=("teacher",),
        )}
        app = FastAPI()
        app.include_router(questions_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]

        async def _real_question_service():
            async with self.session_factory() as session:
                yield QuestionService(QuestionRepository(session))

        app.dependency_overrides[get_question_service] = _real_question_service
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def _as(self, external_user_id: str):
        self.identity["value"] = ExternalIdentityContext(
            provider="test", external_user_id=external_user_id, roles=("teacher",),
        )

    # -- fixtures ----------------------------------------------------------
    def _make_question(
        self, *, school_id=None, author="teacher-a", owner=None, status="DRAFT",
        visibility_scope="SCHOOL", validation_status="draft", origin_type="TEACHER",
        statement="Enunciado de teste.", difficulty="MEDIUM", classified=False,
        created_at=None, updated_at=None,
    ) -> tuple:
        async def create():
            async with self.session_factory() as session:
                question = Question(
                    question_type="MULTIPLE_CHOICE", school_id=school_id,
                    author_external_id=author, owner_external_id=owner or author,
                    origin_type=origin_type, status=status, visibility_scope=visibility_scope,
                    validation_status=validation_status,
                )
                if created_at is not None:
                    question.created_at = created_at
                if updated_at is not None:
                    question.updated_at = updated_at
                session.add(question)
                await session.flush()
                version = QuestionVersion(
                    question_id=question.id, version_kind="official_original",
                    canonical_text=statement, statement=statement,
                    content_hash=f"hash-{uuid4().hex}", recommended_difficulty=difficulty,
                )
                session.add(version)
                await session.flush()
                session.add_all([
                    QuestionOption(question_version_id=version.id, option_key="A", position=1, text="Alternativa A", is_valid_option=True),
                    QuestionOption(question_version_id=version.id, option_key="B", position=2, text="Alternativa B", is_valid_option=False),
                ])
                if classified:
                    session.add(PedagogicalClassification(
                        question_version_id=version.id, discipline="CHEMISTRY", content="CHEMISTRY-SOLUTIONS",
                        subcontent="", difficulty=difficulty, reasoning_type="MANUAL",
                        status="CLASSIFIED", source="human", lifecycle="ACTIVE",
                        model_version="v1", metadata_={},
                    ))
                await session.commit()
                return question.id, version.id
        return asyncio.run(create())

    def _create_via_api(self, **overrides):
        payload = {
            "statement": "Qual e a capital da Franca?",
            "options": ["Paris", "Londres"],
            "correct_option": "Paris",
        }
        payload.update(overrides)
        return self.client.post("/api/v1/questions", json=payload)

    # ======================================================================
    # _load_question_for_view / _load_question_for_manage (shared 404/403)
    # ======================================================================
    def test_eligibility_404_for_unknown_question(self):
        resp = self.client.get(f"/api/v1/questions/{uuid4()}/eligibility")
        self.assertEqual(resp.status_code, 404, resp.text)

    def test_eligibility_403_when_question_not_visible(self):
        question_id, _ = self._make_question(school_id=self.school_b, visibility_scope="PRIVATE", author="teacher-b")
        resp = self.client.get(f"/api/v1/questions/{question_id}/eligibility")
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_version_creation_404_for_unknown_question(self):
        resp = self.client.post(
            f"/api/v1/questions/{uuid4()}/versions",
            json={"statement": "novo", "options": ["A", "B"], "correct_option": "A"},
        )
        self.assertEqual(resp.status_code, 404, resp.text)

    def test_version_creation_403_when_not_manageable(self):
        question_id, _ = self._make_question(school_id=self.school_b, author="teacher-b")
        resp = self.client.post(
            f"/api/v1/questions/{question_id}/versions",
            json={"statement": "novo", "options": ["A", "B"], "correct_option": "A"},
        )
        self.assertEqual(resp.status_code, 403, resp.text)

    # ======================================================================
    # GET /api/v1/questions (real branch)
    # ======================================================================
    def test_list_falls_back_to_default_order_on_invalid_query_params(self):
        self._make_question(school_id=self.school_a, status="PUBLISHED", validation_status="approved")
        resp = self.client.get("/api/v1/questions?order_by=not_a_real_column&order_direction=sideways")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["pagination"]["total"], 1)

    def test_list_orders_by_difficulty_both_directions(self):
        self._make_question(school_id=self.school_a, difficulty="HARD", statement="Q hard")
        self._make_question(school_id=self.school_a, difficulty="EASY", statement="Q easy")
        asc = self.client.get("/api/v1/questions?order_by=difficulty&order_direction=asc")
        desc = self.client.get("/api/v1/questions?order_by=difficulty&order_direction=desc")
        self.assertEqual(asc.status_code, 200, asc.text)
        self.assertEqual(desc.status_code, 200, desc.text)
        self.assertEqual(asc.json()["pagination"]["total"], 2)

    def test_list_student_sees_only_public(self):
        self._make_question(school_id=self.school_a, visibility_scope="PUBLIC", status="PUBLISHED", validation_status="approved")
        self._make_question(school_id=self.school_a, visibility_scope="SCHOOL", status="PUBLISHED", validation_status="approved")
        self._as("student-a")
        resp = self.client.get("/api/v1/questions")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["pagination"]["total"], 1)

    def test_list_teacher_with_school_sees_own_school_and_public(self):
        self._make_question(school_id=self.school_a, visibility_scope="SCHOOL", status="PUBLISHED", validation_status="approved")
        self._make_question(school_id=self.school_b, visibility_scope="SCHOOL", status="PUBLISHED", validation_status="approved")
        self._make_question(school_id=None, visibility_scope="PUBLIC", status="PUBLISHED", validation_status="approved", origin_type="PLATFORM")
        resp = self.client.get("/api/v1/questions")
        self.assertEqual(resp.status_code, 200, resp.text)
        # school_a's own SCHOOL-scope question + the PUBLIC one - not
        # school_b's.
        self.assertEqual(resp.json()["pagination"]["total"], 2)

    def test_list_governance_filters(self):
        self._make_question(school_id=self.school_a, status="PUBLISHED", validation_status="approved", visibility_scope="SCHOOL", origin_type="TEACHER")
        self._make_question(school_id=self.school_a, status="DRAFT", validation_status="draft", visibility_scope="SCHOOL", origin_type="TEACHER")
        resp = self.client.get("/api/v1/questions?status=PUBLISHED&visibility_scope=SCHOOL&origin_type=TEACHER")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["pagination"]["total"], 1)

    def test_list_question_type_filter(self):
        self._make_question(school_id=self.school_a, status="PUBLISHED", validation_status="approved")
        resp = self.client.get("/api/v1/questions?question_type=MULTIPLE_CHOICE")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["pagination"]["total"], 1)
        resp_none = self.client.get("/api/v1/questions?question_type=ESSAY")
        self.assertEqual(resp_none.json()["pagination"]["total"], 0)

    def test_list_content_and_difficulty_filters_join_question_version(self):
        self._make_question(school_id=self.school_a, status="PUBLISHED", validation_status="approved",
                             statement="Diluicao de solucoes aquosas", difficulty="HARD")
        self._make_question(school_id=self.school_a, status="PUBLISHED", validation_status="approved",
                             statement="Estequiometria basica", difficulty="EASY")
        by_content = self.client.get("/api/v1/questions?content=Diluicao")
        self.assertEqual(by_content.json()["pagination"]["total"], 1)
        by_difficulty = self.client.get("/api/v1/questions?difficulty=HARD")
        self.assertEqual(by_difficulty.json()["pagination"]["total"], 1)

    def test_list_date_range_filters(self):
        old = datetime.now(timezone.utc) - timedelta(days=30)
        recent = datetime.now(timezone.utc)
        self._make_question(school_id=self.school_a, status="PUBLISHED", validation_status="approved", created_at=old)
        self._make_question(school_id=self.school_a, status="PUBLISHED", validation_status="approved", created_at=recent)
        cutoff = (recent - timedelta(days=1)).date().isoformat()
        resp = self.client.get(f"/api/v1/questions?created_from={cutoff}")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["pagination"]["total"], 1)
        resp_to = self.client.get(f"/api/v1/questions?created_to={cutoff}")
        self.assertEqual(resp_to.json()["pagination"]["total"], 1)
        resp_updated = self.client.get(f"/api/v1/questions?updated_from={cutoff}&updated_to={cutoff}")
        self.assertEqual(resp_updated.status_code, 200, resp_updated.text)

    def test_list_empty_result_has_zero_total_pages(self):
        resp = self.client.get("/api/v1/questions?status=ARCHIVED")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), {"items": [], "pagination": {"page": 1, "limit": 20, "total": 0, "total_pages": 0}})

    def test_list_eligible_only_filters_out_ineligible_questions(self):
        self._make_question(school_id=self.school_a, status="PUBLISHED", validation_status="approved", classified=True)
        self._make_question(school_id=self.school_a, status="DRAFT", validation_status="draft")
        resp = self.client.get("/api/v1/questions?eligible_only=true")
        self.assertEqual(resp.status_code, 200, resp.text)
        # eligible_only is applied in Python AFTER the page is fetched, so
        # only `items` reflects it - `pagination.total` is the tenant-scoped
        # SQL count from BEFORE the eligibility filter (both questions), a
        # pre-existing metadata quirk out of scope for this coverage pass
        # (flagged separately, see session summary).
        self.assertEqual(resp.json()["pagination"]["total"], 2)
        self.assertEqual(len(resp.json()["items"]), 1)

    # ======================================================================
    # POST /api/v1/questions (create_question_authoring)
    # ======================================================================
    def test_create_rejects_mismatched_school_id(self):
        resp = self._create_via_api(school_id=str(self.school_b))
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(resp.json()["detail"], "School context does not match the authenticated tenant")

    def test_create_rejects_author_override(self):
        resp = self._create_via_api(author_external_id="someone-else")
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(resp.json()["detail"], "Author identity cannot be overridden by the client")

    def test_create_rejects_created_by_override(self):
        resp = self._create_via_api(created_by_external_identity="someone-else")
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(resp.json()["detail"], "Created-by identity cannot be overridden by the client")

    def test_create_rejects_unsupported_visibility_scope(self):
        resp = self._create_via_api(visibility_scope="GALAXY")
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"], "Unsupported visibility_scope")

    def test_create_rejects_too_few_options(self):
        resp = self._create_via_api(options=["Paris"], correct_option="Paris")
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"], "At least two answer options are required")

    def test_create_rejects_correct_option_not_in_list(self):
        resp = self._create_via_api(options=["Paris", "Londres"], correct_option="Berlim")
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"], "The correct option must be present in the answer options")

    def test_create_succeeds_and_defaults_are_applied(self):
        resp = self._create_via_api()
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "DRAFT")
        self.assertIn("question_id", body)
        self.assertIn("version_id", body)

    # ======================================================================
    # POST /api/v1/questions/{id}/versions (create_question_version)
    # ======================================================================
    def test_create_version_success(self):
        question_id, _ = self._make_question(school_id=self.school_a, author="teacher-a")
        resp = self.client.post(
            f"/api/v1/questions/{question_id}/versions",
            json={"statement": "Nova redacao", "options": ["A", "B", "C"], "correct_option": "B", "reason": "melhoria"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("version_id", resp.json())

    def test_create_version_rejects_too_few_options(self):
        question_id, _ = self._make_question(school_id=self.school_a, author="teacher-a")
        resp = self.client.post(
            f"/api/v1/questions/{question_id}/versions",
            json={"statement": "x", "options": ["so-uma"], "correct_option": "so-uma"},
        )
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_create_version_rejects_correct_option_not_in_list(self):
        question_id, _ = self._make_question(school_id=self.school_a, author="teacher-a")
        resp = self.client.post(
            f"/api/v1/questions/{question_id}/versions",
            json={"statement": "x", "options": ["A", "B"], "correct_option": "C"},
        )
        self.assertEqual(resp.status_code, 422, resp.text)

    # ======================================================================
    # POST /api/v1/questions/{id}/status (transition_question_status)
    # ======================================================================
    def test_status_transition_unauthorized(self):
        question_id, _ = self._make_question(school_id=self.school_a, author="teacher-a", status="DRAFT")
        self._as("teacher-a2")  # same school, does not own the question
        resp = self.client.post(f"/api/v1/questions/{question_id}/status", json={"status": "REVIEW"})
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_status_transition_wrong_state_target_is_403(self):
        # can_transition_status's own precondition for PUBLISHED
        # (question.status == "APPROVED") mirrors QuestionStatusWorkflow.
        # ALLOWED_TRANSITIONS exactly, so an out-of-order jump is rejected
        # by the authorization gate before QuestionStatusWorkflow.transition
        # ever runs - the InvalidQuestionStatusTransitionError -> 409 branch
        # has no reachable case through this route today (both layers agree
        # on every legal transition); this documents the actually-reachable
        # behavior instead of a false claim of a 409.
        question_id, _ = self._make_question(school_id=self.school_a, author="teacher-a", status="DRAFT")
        resp = self.client.post(f"/api/v1/questions/{question_id}/status", json={"status": "PUBLISHED"})
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(resp.json()["detail"], "Status transition is not authorized for this role and scope")

    def test_status_transition_success_and_approval_record(self):
        question_id, _ = self._make_question(school_id=self.school_a, author="teacher-a", status="DRAFT")
        submitted = self.client.post(f"/api/v1/questions/{question_id}/status", json={"status": "REVIEW", "reason": "pronta"})
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertEqual(submitted.json()["to_status"], "REVIEW")

        self._as("director-a")
        approved = self.client.post(f"/api/v1/questions/{question_id}/status", json={"status": "APPROVED"})
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["to_status"], "APPROVED")

        async def read_approval():
            async with self.session_factory() as session:
                from sqlalchemy import select
                rows = (await session.scalars(
                    select(QuestionApproval).where(QuestionApproval.question_id == question_id)
                )).all()
                return rows
        rows = asyncio.run(read_approval())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].decision, "APPROVED")

    # ======================================================================
    # GET /api/v1/questions/{id}/eligibility (success)
    # ======================================================================
    def test_eligibility_success_for_ineligible_draft(self):
        question_id, _ = self._make_question(school_id=self.school_a, status="DRAFT", validation_status="draft")
        resp = self.client.get(f"/api/v1/questions/{question_id}/eligibility")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertFalse(body["is_eligible"])
        self.assertTrue(body["reasons"])

    # ======================================================================
    # POST /api/v1/questions/{id}/approval (question_approval)
    # ======================================================================
    def test_approval_rejects_unsupported_decision(self):
        question_id, _ = self._make_question(school_id=self.school_a, author="director-a", owner="director-a")
        self._as("director-a")
        resp = self.client.post(f"/api/v1/questions/{question_id}/approval", json={"decision": "MAYBE"})
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_approval_rejects_non_admin_role(self):
        question_id, _ = self._make_question(school_id=self.school_a, author="teacher-a")
        resp = self.client.post(f"/api/v1/questions/{question_id}/approval", json={"decision": "APPROVED"})
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_approval_success_for_director(self):
        question_id, _ = self._make_question(school_id=self.school_a, author="teacher-a")
        self._as("director-a")
        resp = self.client.post(
            f"/api/v1/questions/{question_id}/approval",
            json={"decision": "APPROVED", "feedback_text": "ok"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["decision"], "APPROVED")

    # ======================================================================
    # GET /api/v1/questions/{id} (real branch)
    # ======================================================================
    def test_get_question_success(self):
        question_id, _ = self._make_question(school_id=self.school_a, status="PUBLISHED", validation_status="approved")
        resp = self.client.get(f"/api/v1/questions/{question_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["id"], str(question_id))

    def test_get_question_403_when_not_visible(self):
        question_id, _ = self._make_question(school_id=self.school_b, visibility_scope="PRIVATE", author="teacher-b")
        resp = self.client.get(f"/api/v1/questions/{question_id}")
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_get_question_404_when_unknown(self):
        resp = self.client.get(f"/api/v1/questions/{uuid4()}")
        self.assertEqual(resp.status_code, 404, resp.text)

    # ======================================================================
    # POST /api/v1/questions/{id}/review (review_question_authoring)
    # ======================================================================
    def _submit_via_review(self, statement="Enunciado revisao"):
        created = self._create_via_api(statement=statement)
        question_id = created.json()["question_id"]
        submitted = self.client.post(f"/api/v1/questions/{question_id}/review", json={"action": "submit"})
        self.assertEqual(submitted.status_code, 200, submitted.text)
        return question_id

    def test_review_reject_success(self):
        question_id = self._submit_via_review()
        self._as("director-a")
        resp = self.client.post(f"/api/v1/questions/{question_id}/review", json={"action": "reject", "reason": "incompleta"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "REJECTED")

    def test_review_archive_success(self):
        question_id = self._submit_via_review(statement="Outro enunciado")
        self._as("director-a")
        resp = self.client.post(f"/api/v1/questions/{question_id}/review", json={"action": "archive"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "ARCHIVED")

    def test_review_unsupported_action_is_400(self):
        question_id = self._submit_via_review(statement="Mais um enunciado")
        self._as("director-a")
        resp = self.client.post(f"/api/v1/questions/{question_id}/review", json={"action": "teleport"})
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_review_invalid_state_transition_is_400(self):
        # approve() requires PENDING_REVIEW - a DRAFT question was never
        # submitted, so QuestionAuthoringService.approve raises ValueError.
        created = self._create_via_api(statement="Nunca submetida")
        question_id = created.json()["question_id"]
        self._as("director-a")
        resp = self.client.post(f"/api/v1/questions/{question_id}/review", json={"action": "approve"})
        self.assertEqual(resp.status_code, 400, resp.text)


if __name__ == "__main__":
    unittest.main()
