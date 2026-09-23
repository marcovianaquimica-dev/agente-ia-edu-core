"""HTTP-level, production-fidelity coverage for api/routes/questions.py.

Overnight bug-hunt campaign (2026-09-22): Wave 2 confirmed a recurring bug
class in assessments.py - a route loads an ORM object, later does
`await session.commit()`, then reads a synchronous attribute on that SAME
object. In production `create_session_factory()` uses the sessionmaker
default `expire_on_commit=True`, so commit() expires every object touched in
that session and the next sync attribute read triggers a background refresh
outside the async greenlet bridge -> MissingGreenlet (500, with the write
already persisted).

This file targets questions.py's 5 `await session.commit()` sites
(create_question_authoring, create_question_version,
transition_question_status, question_approval, review_question_authoring)
with a session fixture that mirrors production exactly:
`async_sessionmaker(engine, class_=AsyncSession)` with NO `expire_on_commit`
override (defaults to True). This is deliberately different from
tests/test_route_coverage_questions.py's fixture, which sets
`expire_on_commit=False` and therefore cannot catch this bug class.

Investigation result: all 5 commit sites in questions.py already
`await session.refresh(...)` every object read after commit (question,
version, record, approval) - this was fixed in a prior session for
review_question_authoring specifically (commit fa01bcc, "fix: mais
MissingGreenlet e erros mal tratados em 4 areas paralelas") and the other 4
sites already followed the same refresh pattern. These tests are a
production-fidelity regression guard for that: they passed cleanly on first
run, confirming the sites are safe, not proving a new bug.

Note: this file's own `_make_question` seeding helper hit the exact same
bug in its OWN code (returning `question.id, version.id` after a commit on
the same non-expired-off session) before being fixed to capture the ids into
locals before commit - a useful confirmation that this fixture reproduces
the failure mode faithfully.
"""

from __future__ import annotations

import asyncio
import unittest
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.questions import get_question_service, router as questions_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import Question, QuestionOption, QuestionVersion, School, UserSchoolLink
from agente_ia_edu.db.models.official import QuestionApproval
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.repositories.questions import QuestionRepository
from agente_ia_edu.services.questions import QuestionService


class QuestionsRouteCoverageHTTP(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            # NO expire_on_commit override - mirrors create_session_factory()'s
            # production default (expire_on_commit=True) exactly. Do not
            # "fix" this to False; that hides the bug class this file exists
            # to catch.
            factory = async_sessionmaker(engine, class_=AsyncSession)
            async with factory() as session:
                school_a = School(code=f"QRCH-A-{uuid4().hex[:6]}", name="Escola Route Coverage HTTP A")
                school_b = School(code=f"QRCH-B-{uuid4().hex[:6]}", name="Escola Route Coverage HTTP B")
                session.add_all([school_a, school_b])
                await session.flush()
                school_a_id, school_b_id = school_a.id, school_b.id
                session.add_all([
                    UserSchoolLink(external_user_id="teacher-a", school_id=school_a_id, role="TEACHER",
                                   scope_type="SCHOOL", active=True),
                    UserSchoolLink(external_user_id="teacher-a2", school_id=school_a_id, role="TEACHER",
                                   scope_type="SCHOOL", active=True),
                    UserSchoolLink(external_user_id="director-a", school_id=school_a_id, role="DIRECTOR",
                                   scope_type="SCHOOL", active=True),
                    UserSchoolLink(external_user_id="teacher-b", school_id=school_b_id, role="TEACHER",
                                   scope_type="SCHOOL", active=True),
                ])
                await session.commit()
                return engine, factory, school_a_id, school_b_id

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
        visibility_scope="SCHOOL", validation_status="draft", statement="Enunciado de teste.",
        difficulty="MEDIUM",
    ) -> tuple:
        async def create():
            async with self.session_factory() as session:
                question = Question(
                    question_type="MULTIPLE_CHOICE", school_id=school_id,
                    author_external_id=author, owner_external_id=owner or author,
                    origin_type="TEACHER", status=status, visibility_scope=visibility_scope,
                    validation_status=validation_status,
                )
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
                # Capture before commit - see module docstring. Under the
                # production-fidelity session (expire_on_commit=True, no
                # override), reading question.id/version.id on these
                # already-committed objects would itself raise
                # MissingGreenlet.
                question_id, version_id = question.id, version.id
                await session.commit()
                return question_id, version_id
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
    # commit-site 1: create_question_authoring (questions.py ~421) - commits
    # a new Question + QuestionVersion + QuestionOptions, then
    # `await session.refresh(question)` / `refresh(version)` before reading
    # question.id/version.id/question.status.
    # ======================================================================
    def test_create_question_authoring_commits_and_reads_cleanly(self):
        resp = self._create_via_api()
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "DRAFT")
        self.assertIn("question_id", body)
        self.assertIn("version_id", body)

        async def inspect():
            async with self.session_factory() as session:
                question = await session.get(Question, UUID(body["question_id"]))
                return question.status

        self.assertEqual(asyncio.run(inspect()), "DRAFT")

    # ======================================================================
    # commit-site 2: create_question_version (questions.py ~483) - loads the
    # existing Question via _load_question_for_manage, adds a new
    # QuestionVersion + options, mutates question.updated_at, commits, then
    # refreshes both question and version before reading their ids/status.
    # ======================================================================
    def test_create_question_version_commits_and_reads_cleanly(self):
        question_id, _ = self._make_question(school_id=self.school_a, author="teacher-a")
        resp = self.client.post(
            f"/api/v1/questions/{question_id}/versions",
            json={"statement": "Nova redacao", "options": ["A", "B", "C"], "correct_option": "B", "reason": "melhoria"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("version_id", resp.json())

    # ======================================================================
    # commit-site 3: transition_question_status (questions.py ~529) - adds a
    # QuestionStatusTransition record (+ a QuestionApproval when the target
    # is APPROVED/REJECTED), commits, then refreshes `record` and `question`
    # before reading record.id/from_status/to_status/... and question.id.
    # Exercised through submit (REVIEW) then approve (APPROVED, which also
    # hits the QuestionApproval insert branch).
    # ======================================================================
    def test_status_transition_commits_and_reads_cleanly(self):
        question_id, _ = self._make_question(school_id=self.school_a, author="teacher-a", status="DRAFT")
        submitted = self.client.post(
            f"/api/v1/questions/{question_id}/status", json={"status": "REVIEW", "reason": "pronta"}
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertEqual(submitted.json()["to_status"], "REVIEW")

        self._as("director-a")
        approved = self.client.post(f"/api/v1/questions/{question_id}/status", json={"status": "APPROVED"})
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["to_status"], "APPROVED")
        self.assertEqual(approved.json()["question_id"], str(question_id))

        async def read_approval():
            async with self.session_factory() as session:
                rows = (await session.scalars(
                    select(QuestionApproval).where(QuestionApproval.question_id == question_id)
                )).all()
                return rows

        rows = asyncio.run(read_approval())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].decision, "APPROVED")

    # ======================================================================
    # commit-site 4: question_approval (questions.py ~581) - adds a
    # QuestionApproval, commits, then refreshes `approval` and `question`
    # before reading approval.id/reviewer_external_id/decision/feedback_text
    # and question.id.
    # ======================================================================
    def test_question_approval_commits_and_reads_cleanly(self):
        question_id, _ = self._make_question(school_id=self.school_a, author="teacher-a")
        self._as("director-a")
        resp = self.client.post(
            f"/api/v1/questions/{question_id}/approval",
            json={"decision": "APPROVED", "feedback_text": "ok"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["decision"], "APPROVED")
        self.assertEqual(body["question_id"], str(question_id))
        self.assertEqual(body["feedback_text"], "ok")

    # ======================================================================
    # commit-site 5: review_question_authoring (questions.py ~651) - calls
    # into QuestionAuthoringService (submit_for_review/approve/reject/
    # archive_question, none of which commit internally), commits once in
    # the route, refreshes `question`, then does a FRESH query via
    # service.get_current_version(question_id) rather than touching a stale
    # relationship - exercised through all four actions.
    # ======================================================================
    def _submit_via_review(self, statement="Enunciado revisao"):
        created = self._create_via_api(statement=statement)
        self.assertEqual(created.status_code, 201, created.text)
        question_id = created.json()["question_id"]
        submitted = self.client.post(f"/api/v1/questions/{question_id}/review", json={"action": "submit"})
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertEqual(submitted.json()["status"], "PENDING_REVIEW")
        return question_id

    def test_review_submit_commits_and_reads_cleanly(self):
        # submit is exercised as part of _submit_via_review's own assertion
        # of a clean 200 - kept as a dedicated test for direct traceability
        # to commit-site 5's "submit" branch.
        self._submit_via_review()

    def test_review_approve_commits_and_reads_cleanly(self):
        question_id = self._submit_via_review(statement="Enunciado para aprovar")
        self._as("director-a")
        resp = self.client.post(f"/api/v1/questions/{question_id}/review", json={"action": "approve"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "APPROVED")
        self.assertEqual(resp.json()["question_id"], question_id)

    def test_review_reject_commits_and_reads_cleanly(self):
        question_id = self._submit_via_review(statement="Enunciado para rejeitar")
        self._as("director-a")
        resp = self.client.post(
            f"/api/v1/questions/{question_id}/review", json={"action": "reject", "reason": "incompleta"}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "REJECTED")

    def test_review_archive_commits_and_reads_cleanly(self):
        question_id = self._submit_via_review(statement="Enunciado para arquivar")
        self._as("director-a")
        resp = self.client.post(f"/api/v1/questions/{question_id}/review", json={"action": "archive"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "ARCHIVED")


if __name__ == "__main__":
    unittest.main()
