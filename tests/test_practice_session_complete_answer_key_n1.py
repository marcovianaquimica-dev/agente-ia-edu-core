"""N+1 regression test for POST /api/v1/practice/sessions/{id}/complete.

Assessment/answer-key performance audit (performance audit session, 2026-09):
the correction loop inside ``complete_practice_session``
(``api/routes/learning_path.py``) used to call
``resolve_official_correct_option_id`` once PER answered selection - a
genuine N+1 confirmed LIVE against real Postgres (port 5433, disposable
schema, before/after `before_cursor_execute` counter): 5 -> 40 answered
selections grew the query count 1:1 (5 -> 40 queries) before the fix, and
stayed flat (1 constant query via the existing batched
``resolve_official_answer_key_snapshots`` - already used by
``PracticeSessionService.complete_session`` and by
``POST /assessments/publications/{id}/activate``) after it.

This test pins the fixed-input-shape down with a fast, in-process, disposable
sqlite counter, same technique as ``tests/test_phase24_study_session_n1.py``.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import app
from agente_ia_edu.api.dependencies import get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    PracticeQuestionSelection,
    PracticeSession,
    Question,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
)


class QueryCounter:
    """Same technique as tests/test_portal_n1_queries.py: count SQL
    statements executed against `engine` for the duration of a `with` block
    via SQLAlchemy's `before_cursor_execute`."""

    def __init__(self, engine):
        self.engine = engine
        self.count = 0
        self._listener = None

    def __enter__(self):
        def _count(conn, cursor, statement, parameters, context, executemany):
            self.count += 1
        self._listener = _count
        event.listen(self.engine.sync_engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine.sync_engine, "before_cursor_execute", self._listener)


class PracticeCompletionAnswerKeyN1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        cls.session_factory = async_sessionmaker(
            cls.engine, class_=AsyncSession, expire_on_commit=False
        )

        async def _init():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(_init())

        app.dependency_overrides[get_session_factory] = lambda: cls.session_factory
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        asyncio.run(cls.engine.dispose())

    async def _seed_session_with_answered_questions(self, n: int, student: str) -> uuid.UUID:
        """One practice session, answered (and wrong, so correction runs) on
        `n` distinct questions each with its own official answer key."""
        async with self.session_factory() as session:
            institution = Institution(code=f"N1-{uuid.uuid4().hex[:8]}", name="N1 Institution")
            session.add(institution)
            await session.flush()
            exam = Exam(institution_id=institution.id, code=f"N1EX-{uuid.uuid4().hex[:8]}", name="N1 Exam")
            session.add(exam)
            await session.flush()
            application = ExamApplication(exam_id=exam.id, year=2024, application_type="regular")
            session.add(application)
            await session.flush()
            booklet = ExamBooklet(exam_application_id=application.id, code=f"N1BK-{uuid.uuid4().hex[:6]}")
            session.add(booklet)
            await session.flush()
            source_document = SourceDocument(
                exam_application_id=application.id,
                document_type="proof",
                source_url="https://example.com/n1.pdf",
                acquired_at=datetime.now(timezone.utc),
                content_hash=uuid.uuid4().hex,
            )
            session.add(source_document)
            await session.flush()

            practice_session = PracticeSession(
                external_identity_id=student,
                requested_question_count=n,
                status="active",
            )
            session.add(practice_session)
            await session.flush()

            for i in range(n):
                question = Question(validation_status="validated")
                session.add(question)
                await session.flush()
                version = QuestionVersion(
                    question_id=question.id,
                    version_kind="official_original",
                    canonical_text=f"N1 Question {uuid.uuid4().hex[:6]}?",
                    content_hash=uuid.uuid4().hex,
                    recommended_difficulty="EASY",
                )
                session.add(version)
                await session.flush()

                option_ids = {}
                for j, key in enumerate(["A", "B", "C", "D"], start=1):
                    option = QuestionOption(
                        question_version_id=version.id, option_key=key, position=j, text=f"Option {key}"
                    )
                    session.add(option)
                    await session.flush()
                    option_ids[key] = option.id

                booklet_question = BookletQuestion(
                    exam_booklet_id=booklet.id, question_version_id=version.id, position=i + 1
                )
                session.add(booklet_question)
                await session.flush()

                revision = AnswerKeyRevision(
                    source_document_id=source_document.id, revision_number=1, is_official=True
                )
                session.add(revision)
                await session.flush()
                entry = AnswerKeyEntry(
                    answer_key_revision_id=revision.id,
                    booklet_question_id=booklet_question.id,
                    official_answer_label="A",
                    resolved_option_id=option_ids["A"],
                )
                session.add(entry)
                await session.flush()

                selection = PracticeQuestionSelection(
                    practice_session_id=practice_session.id,
                    question_version_id=version.id,
                    difficulty_level="EASY",
                    position=i + 1,
                    selected_option_id=option_ids["B"],  # wrong on purpose: forces correction
                    answered_at=datetime.now(timezone.utc),
                )
                session.add(selection)

            await session.commit()
            return practice_session.id

    def _complete_and_count(self, n: int, student: str) -> int:
        session_id = asyncio.run(self._seed_session_with_answered_questions(n, student))
        with QueryCounter(self.engine) as counter:
            resp = self.client.post(
                f"/api/v1/practice/sessions/{session_id}/complete",
                json={},
                headers={"Authorization": f"Bearer student:{student}"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["total_questions"], n)
        self.assertEqual(body["incorrect_count"], n)  # every selection picked the wrong option
        return counter.count

    def test_complete_endpoint_query_count_does_not_scale_with_answered_question_count(self):
        q_small = self._complete_and_count(5, "n1_student_small")
        q_large = self._complete_and_count(40, "n1_student_large")

        # N+1 signature: before the fix this grew 1:1 with the number of
        # answered questions (confirmed live against real Postgres: 5 -> 40
        # items grew 5 -> 40 queries). After the fix the answer-key
        # resolution is one batched query regardless of N, so the two
        # scenarios' query counts must be equal.
        self.assertEqual(
            q_small,
            q_large,
            f"query count scaled with answered-question count ({q_small} for 5 items, "
            f"{q_large} for 40 items) - the per-selection answer-key lookup regressed "
            f"back to N+1",
        )


if __name__ == "__main__":
    unittest.main()
