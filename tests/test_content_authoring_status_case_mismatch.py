"""Regression test for a real, live-confirmed bug in
`agente_ia_edu.services.content_authoring.QuestionAuthoringService`.

Found while auditing content_authoring.py (2026-09-17 session) and its only
live route, `POST /api/v1/questions/{question_id}/review`
(src/agente_ia_edu/api/routes/questions.py), against the real dev server.

`POST /api/v1/questions` (questions.py:349) writes a freshly created
question's `validation_status` as the lowercase string "draft" (the
convention used by the rest of the codebase for this column - see
ingestion_classifier.py's "extracted", question_bank_importer.py's
"validated", question_governance.py's lower()-then-compare against
{"valid", "acceptable", "approved"}).

QuestionAuthoringService, however, compares `validation_status` against the
UPPERCASE `QuestionWorkflowStatus` enum values ("DRAFT", "PENDING_REVIEW",
...) with a plain `==`/`in` check and no normalization. Confirmed live
against Postgres: create a question via POST /api/v1/questions, then POST
{id}/review with action=submit -> always 400 "Only draft or rejected
questions can be submitted for review." even on a question that is, in
fact, freshly drafted. This is a pure string-comparison bug, not the
MissingGreenlet class (it reproduces identically on SQLite, which is why a
plain unit test is the correct evidence for it, unlike the MissingGreenlet
bugs which need live Postgres verification).
"""

from __future__ import annotations

import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import Question, QuestionVersion
from agente_ia_edu.services.content_authoring import QuestionAuthoringService


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


async def _seed_freshly_created_question(session):
    """Mirrors exactly what POST /api/v1/questions writes today
    (questions.py:340-351): status="DRAFT" (uppercase, a different column)
    but validation_status="draft" (lowercase)."""
    question = Question(
        origin_type="TEACHER",
        status="DRAFT",
        visibility_scope="PRIVATE",
        validation_status="draft",
    )
    session.add(question)
    await session.flush()
    version = QuestionVersion(
        question_id=question.id,
        version_kind="official_original",
        canonical_text="Qual e a capital da Franca?",
        content_hash="h-case-mismatch-1",
    )
    session.add(version)
    await session.flush()
    return question, version


class SubmitForReviewCaseMismatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_submit_for_review_accepts_a_freshly_created_question(self):
        async with self.factory() as session:
            question, _ = await _seed_freshly_created_question(session)
            service = QuestionAuthoringService(session)
            # Must not raise: a freshly created (DRAFT-equivalent) question
            # must always be submittable for review.
            updated = await service.submit_for_review(question.id)
            self.assertEqual(updated.validation_status, "PENDING_REVIEW")

    async def test_approve_then_reject_then_resubmit_roundtrip_with_lowercase_origin(self):
        async with self.factory() as session:
            question, _ = await _seed_freshly_created_question(session)
            service = QuestionAuthoringService(session)
            await service.submit_for_review(question.id)
            rejected = await service.reject(question.id, reason="needs work")
            self.assertEqual(rejected.validation_status, "REJECTED")
            # A rejected question must be resubmittable too.
            resubmitted = await service.submit_for_review(question.id)
            self.assertEqual(resubmitted.validation_status, "PENDING_REVIEW")


if __name__ == "__main__":
    unittest.main()
