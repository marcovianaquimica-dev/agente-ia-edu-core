"""
HTTP-level route-handler coverage for src/agente_ia_edu/api/routes/learning_path.py.

Overnight bug-hunt campaign, "smaller route files" zone. This file targets
the 3 `await session.commit()` sites in learning_path.py:
  1. create_practice_session (POST /sessions)
  2. answer_practice_question (POST /sessions/{id}/questions/{id}/answer)
  3. complete_practice_session (POST /sessions/{id}/complete)

Static review of each site showed `await session.refresh(<obj>)` called
immediately after `commit()`, before any synchronous attribute is read on
the object that was loaded/added earlier in that session block - the
established safe pattern from the assessments.py fixes. This file is the
required empirical proof: it drives the real endpoints end-to-end through a
session factory built EXACTLY like production
(async_sessionmaker(engine, class_=AsyncSession) with no expire_on_commit
override, so the default expire_on_commit=True applies - unlike
tests/test_learning_path_flow.py's fixture, which sets
expire_on_commit=False and would paper over a MissingGreenlet bug here).

Fixture/seeding helpers are adapted from tests/test_learning_path_flow.py
(same seed shape: Institution/Exam/ExamApplication/ExamBooklet/
SourceDocument/Question/QuestionVersion/QuestionOption/BookletQuestion/
AnswerKeyRevision/AnswerKeyEntry/QuestionClassification), trimmed to what's
needed to drive the 3 commit sites through the real FastAPI app.
"""

import asyncio
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
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
    Question,
    QuestionClassification,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
    Taxonomy,
    TaxonomyNode,
)


def _auth(student: str) -> dict:
    return {"Authorization": f"Bearer student:{student}"}


class LearningPathRouteCoverageHTTP(unittest.TestCase):
    """Production-fidelity (expire_on_commit=True) HTTP coverage for the
    3 commit sites in learning_path.py."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        # Deliberately NOT relaxing expire_on_commit - see module docstring.
        cls.session_factory = async_sessionmaker(cls.engine, class_=AsyncSession)

        async def _init():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(_init())

        app.dependency_overrides[get_session_factory] = lambda: cls.session_factory
        cls.client = TestClient(app)

        cls.taxonomy_id, cls.content_a_id = asyncio.run(cls._seed_taxonomy())

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        asyncio.run(cls.engine.dispose())

    @classmethod
    async def _seed_taxonomy(cls):
        async with cls.session_factory() as session:
            taxonomy = Taxonomy(code=f"tax-{uuid4().hex[:8]}", name="Test Taxonomy", version="1.0")
            session.add(taxonomy)
            await session.flush()
            content_a = TaxonomyNode(
                taxonomy_id=taxonomy.id, code="A", name="Content A", node_type="skill"
            )
            session.add(content_a)
            await session.flush()
            taxonomy_id, content_a_id = taxonomy.id, content_a.id
            await session.commit()
            return taxonomy_id, content_a_id

    async def _seed_question(
        self, content_node_id, difficulty: str = "EASY", correct_key: str | None = "A"
    ):
        """Create one official question, classified under content_node_id.

        Returns (question_version_id, {option_key: option_id}).
        """
        async with self.session_factory() as session:
            institution = Institution(code=f"INST-{uuid4().hex[:8]}", name="Test Institution")
            session.add(institution)
            await session.flush()

            exam = Exam(institution_id=institution.id, code=f"EXAM-{uuid4().hex[:8]}", name="Test Exam")
            session.add(exam)
            await session.flush()

            application = ExamApplication(exam_id=exam.id, year=2024, application_type="regular")
            session.add(application)
            await session.flush()

            booklet = ExamBooklet(exam_application_id=application.id, code=f"BK-{uuid4().hex[:6]}")
            session.add(booklet)
            await session.flush()

            source_document = SourceDocument(
                exam_application_id=application.id,
                document_type="proof",
                source_url="https://example.com/doc.pdf",
                acquired_at=datetime.now(timezone.utc),
                content_hash=uuid4().hex,
            )
            session.add(source_document)
            await session.flush()

            question = Question(validation_status="validated")
            session.add(question)
            await session.flush()

            version = QuestionVersion(
                question_id=question.id,
                version_kind="official_original",
                canonical_text=f"Question {uuid4().hex[:6]}?",
                content_hash=uuid4().hex,
                recommended_difficulty=difficulty,
            )
            session.add(version)
            await session.flush()

            option_ids = {}
            for i, key in enumerate(["A", "B", "C", "D"], start=1):
                option = QuestionOption(
                    question_version_id=version.id, option_key=key, position=i, text=f"Option {key}"
                )
                session.add(option)
                await session.flush()
                option_ids[key] = option.id

            booklet_question = BookletQuestion(
                exam_booklet_id=booklet.id, question_version_id=version.id, position=1
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
                official_answer_label=correct_key,
                resolved_option_id=option_ids[correct_key],
            )
            session.add(entry)
            await session.flush()

            classification = QuestionClassification(
                question_version_id=version.id,
                taxonomy_id=self.taxonomy_id,
                competency_node_id=content_node_id,
                skill_node_id=content_node_id,
                is_primary=True,
                status="active",
                source="human",
            )
            session.add(classification)
            version_id = version.id
            await session.commit()

            return version_id, option_ids

    def _seed(self, coro):
        return asyncio.run(coro)

    # ------------------------------------------------------------------

    def test_full_flow_hits_all_three_commit_sites_cleanly(self):
        """Drives create -> answer -> complete end-to-end under
        expire_on_commit=True. Any MissingGreenlet / 500 here would confirm a
        real commit-then-read-expired-attribute bug at one of the 3 sites."""
        version_id, options = self._seed(
            self._seed_question(self.content_a_id, difficulty="EASY", correct_key="B")
        )
        student = f"alice-{uuid4().hex[:8]}"
        headers = _auth(student)

        # Commit site 1: create_practice_session
        create_resp = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(self.content_a_id), "requested_question_count": 1},
            headers=headers,
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        session_data = create_resp.json()
        self.assertIn("id", session_data)
        self.assertEqual(session_data["recommended_difficulty"], "EASY")
        session_id = session_data["id"]

        next_resp = self.client.get(
            f"/api/v1/practice/sessions/{session_id}/next-question", headers=headers
        )
        self.assertEqual(next_resp.status_code, 200, next_resp.text)
        question = next_resp.json()["question"]
        self.assertEqual(question["question_version_id"], str(version_id))
        selection_id = question["id"]

        # Commit site 2: answer_practice_question
        answer_resp = self.client.post(
            f"/api/v1/practice/sessions/{session_id}/questions/{selection_id}/answer",
            json={"selected_option_id": str(options["B"])},
            headers=headers,
        )
        self.assertEqual(answer_resp.status_code, 201, answer_resp.text)
        answer_body = answer_resp.json()
        self.assertEqual(answer_body["practice_question_selection_id"], selection_id)
        self.assertTrue(answer_body["is_received"])

        # Commit site 3: complete_practice_session
        complete_resp = self.client.post(
            f"/api/v1/practice/sessions/{session_id}/complete", json={}, headers=headers
        )
        self.assertEqual(complete_resp.status_code, 200, complete_resp.text)
        result = complete_resp.json()
        self.assertEqual(result["practice_session_id"], session_id)
        self.assertEqual(result["correct_count"], 1)
        self.assertEqual(result["percentage"], 100.0)

        # Sanity: the write really landed post-commit (not just avoided a crash).
        result_resp = self.client.get(
            f"/api/v1/practice/sessions/{session_id}/result", headers=headers
        )
        self.assertEqual(result_resp.status_code, 200, result_resp.text)
        self.assertEqual(result_resp.json()["correct_count"], 1)

    def test_unknown_answer_hits_commit_site_2_cleanly(self):
        """is_unknown=True path through answer_practice_question - a
        different branch of the same commit site."""
        version_id, options = self._seed(
            self._seed_question(self.content_a_id, difficulty="EASY", correct_key="A")
        )
        student = f"bob-{uuid4().hex[:8]}"
        headers = _auth(student)

        create_resp = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(self.content_a_id), "requested_question_count": 1},
            headers=headers,
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        session_id = create_resp.json()["id"]

        next_resp = self.client.get(
            f"/api/v1/practice/sessions/{session_id}/next-question", headers=headers
        )
        selection_id = next_resp.json()["question"]["id"]

        answer_resp = self.client.post(
            f"/api/v1/practice/sessions/{session_id}/questions/{selection_id}/answer",
            json={"is_unknown": True},
            headers=headers,
        )
        self.assertEqual(answer_resp.status_code, 201, answer_resp.text)
        self.assertTrue(answer_resp.json()["is_unknown"])


if __name__ == "__main__":
    unittest.main()
