"""
End-to-end tests for the Learning Path practice flow, exercised through the
real FastAPI endpoints (session -> selection -> answer -> correction ->
domain update -> result).

Uses a shared in-memory SQLite database (StaticPool) so the same "state" is
visible across the multiple HTTP requests each test makes.
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
    StudentContentMastery,
    Taxonomy,
    TaxonomyNode,
    LearningHistory,
)


def _auth(student: str) -> dict:
    return {"Authorization": f"Bearer student:{student}"}


class PracticeFlowE2ETests(unittest.TestCase):
    """Full practice flow exercised via the real HTTP endpoints."""

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

        cls.taxonomy_id, cls.content_a_id, cls.content_b_id = asyncio.run(cls._seed_taxonomy())

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
            content_b = TaxonomyNode(
                taxonomy_id=taxonomy.id, code="B", name="Content B", node_type="skill"
            )
            session.add_all([content_a, content_b])
            await session.commit()
            return taxonomy.id, content_a.id, content_b.id

    async def _seed_question(
        self,
        content_node_id,
        difficulty: str = "EASY",
        correct_key: str | None = "A",
        with_answer_key: bool = True,
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
                    question_version_id=version.id,
                    option_key=key,
                    position=i,
                    text=f"Option {key}",
                )
                session.add(option)
                await session.flush()
                option_ids[key] = option.id

            booklet_question = BookletQuestion(
                exam_booklet_id=booklet.id,
                question_version_id=version.id,
                position=1,
            )
            session.add(booklet_question)
            await session.flush()

            if with_answer_key:
                revision = AnswerKeyRevision(
                    source_document_id=source_document.id,
                    revision_number=1,
                    is_official=True,
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
            await session.commit()

            return version.id, option_ids

    def _seed(self, coro):
        return asyncio.run(coro)

    async def _new_content_node(self):
        """Create a fresh, isolated content node (avoids cross-test candidate
        pool contamination for tests that assert on exact selection)."""
        async with self.session_factory() as session:
            node = TaxonomyNode(
                taxonomy_id=self.taxonomy_id,
                code=f"NODE-{uuid4().hex[:10]}",
                name="Isolated Content",
                node_type="skill",
            )
            session.add(node)
            await session.commit()
            return node.id

    async def _set_mastery(
        self,
        student: str,
        content_node_id,
        *,
        score: float,
        level: str,
        answered: int,
        correct: int,
        confidence: float,
    ):
        async with self.session_factory() as session:
            mastery = StudentContentMastery(
                external_identity_id=student,
                content_node_id=content_node_id,
                mastery_score=score,
                current_level=level,
                questions_answered=answered,
                questions_correct=correct,
                confidence=confidence,
            )
            session.add(mastery)
            await session.commit()

    # ------------------------------------------------------------------
    # Full happy path
    # ------------------------------------------------------------------

    def test_full_flow_new_student_no_history(self):
        """New student: session starts EASY, selects real questions, answers,
        gets corrected, mastery updates, result is queryable."""
        version_id, options = self._seed(
            self._seed_question(self.content_a_id, difficulty="EASY", correct_key="B")
        )
        student = f"alice-{uuid4().hex[:8]}"
        headers = _auth(student)

        create_resp = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(self.content_a_id), "requested_question_count": 1},
            headers=headers,
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        session_data = create_resp.json()
        self.assertEqual(session_data["recommended_difficulty"], "EASY")
        session_id = session_data["id"]

        # No gabarito exposed pre-answer
        next_resp = self.client.get(
            f"/api/v1/practice/sessions/{session_id}/next-question", headers=headers
        )
        self.assertEqual(next_resp.status_code, 200)
        next_data = next_resp.json()
        self.assertFalse(next_data["is_complete"])
        question = next_data["question"]
        self.assertEqual(question["question_version_id"], str(version_id))
        for option in question["options"]:
            self.assertNotIn("is_correct", option)

        selection_id = question["id"]
        answer_resp = self.client.post(
            f"/api/v1/practice/sessions/{session_id}/questions/{selection_id}/answer",
            json={"selected_option_id": str(options["B"])},
            headers=headers,
        )
        self.assertEqual(answer_resp.status_code, 201, answer_resp.text)

        # Now complete
        complete_resp = self.client.post(
            f"/api/v1/practice/sessions/{session_id}/complete", json={}, headers=headers
        )
        self.assertEqual(complete_resp.status_code, 200, complete_resp.text)
        result = complete_resp.json()
        self.assertEqual(result["correct_count"], 1)
        self.assertEqual(result["incorrect_count"], 0)
        self.assertEqual(result["percentage"], 100.0)

        # Result queryable afterwards
        result_resp = self.client.get(
            f"/api/v1/practice/sessions/{session_id}/result", headers=headers
        )
        self.assertEqual(result_resp.status_code, 200)
        self.assertEqual(result_resp.json()["correct_count"], 1)

        # Mastery updated
        mastery_resp = self.client.get("/api/v1/practice/mastery", headers=headers)
        self.assertEqual(mastery_resp.status_code, 200)
        masteries = mastery_resp.json()["masteries"]
        self.assertEqual(len(masteries), 1)
        self.assertEqual(masteries[0]["questions_answered"], 1)
        self.assertEqual(masteries[0]["questions_correct"], 1)

        # Learning history recorded
        history_resp = self.client.get("/api/v1/practice/history", headers=headers)
        self.assertEqual(history_resp.status_code, 200)
        self.assertEqual(len(history_resp.json()["entries"]), 1)

    # ------------------------------------------------------------------
    # Difficulty / mastery scenarios
    # ------------------------------------------------------------------

    def test_low_mastery_student_gets_easy(self):
        student = f"low-{uuid4().hex[:8]}"
        self._seed(
            self._set_mastery(
                student, self.content_a_id, score=20.0, level="EASY",
                answered=5, correct=1, confidence=0.4,
            )
        )
        resp = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(self.content_a_id), "requested_question_count": 1},
            headers=_auth(student),
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["recommended_difficulty"], "EASY")

    def test_high_mastery_student_gets_hard(self):
        student = f"high-{uuid4().hex[:8]}"
        self._seed(
            self._set_mastery(
                student, self.content_a_id, score=90.0, level="HARD",
                answered=25, correct=23, confidence=0.9,
            )
        )
        resp = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(self.content_a_id), "requested_question_count": 1},
            headers=_auth(student),
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["recommended_difficulty"], "HARD")

    def test_progression_easy_to_medium_via_full_flow(self):
        """Answering enough EASY questions correctly should progress the
        student's mastery level beyond EASY."""
        student = f"prog-{uuid4().hex[:8]}"
        headers = _auth(student)

        questions = [
            self._seed(self._seed_question(self.content_a_id, difficulty="EASY", correct_key="A"))
            for _ in range(10)
        ]

        create_resp = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(self.content_a_id), "requested_question_count": 10},
            headers=headers,
        )
        self.assertEqual(create_resp.status_code, 201)
        session_id = create_resp.json()["id"]

        questions_resp = self.client.get(
            f"/api/v1/practice/sessions/{session_id}/questions", headers=headers
        )
        self.assertEqual(questions_resp.status_code, 200)
        for q in questions_resp.json():
            correct_option = next(o for o in q["options"] if o["option_key"] == "A")
            answer_resp = self.client.post(
                f"/api/v1/practice/sessions/{session_id}/questions/{q['id']}/answer",
                json={"selected_option_id": correct_option["id"]},
                headers=headers,
            )
            self.assertEqual(answer_resp.status_code, 201)

        complete_resp = self.client.post(
            f"/api/v1/practice/sessions/{session_id}/complete", json={}, headers=headers
        )
        self.assertEqual(complete_resp.status_code, 200)
        self.assertNotEqual(complete_resp.json()["updated_mastery_level"], "EASY")

    def test_progression_medium_to_hard_via_full_flow(self):
        """Student already at MEDIUM with strong evidence should reach HARD
        after another strong session."""
        student = f"prog2-{uuid4().hex[:8]}"
        headers = _auth(student)
        self._seed(
            self._set_mastery(
                student, self.content_a_id, score=75.0, level="MEDIUM",
                answered=15, correct=11, confidence=0.75,
            )
        )

        questions = [
            self._seed(self._seed_question(self.content_a_id, difficulty="MEDIUM", correct_key="C"))
            for _ in range(5)
        ]

        create_resp = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(self.content_a_id), "requested_question_count": 5},
            headers=headers,
        )
        self.assertEqual(create_resp.status_code, 201)
        self.assertEqual(create_resp.json()["recommended_difficulty"], "MEDIUM")
        session_id = create_resp.json()["id"]

        questions_resp = self.client.get(
            f"/api/v1/practice/sessions/{session_id}/questions", headers=headers
        )
        for q in questions_resp.json():
            correct_option = next(o for o in q["options"] if o["option_key"] == "C")
            self.client.post(
                f"/api/v1/practice/sessions/{session_id}/questions/{q['id']}/answer",
                json={"selected_option_id": correct_option["id"]},
                headers=headers,
            )

        complete_resp = self.client.post(
            f"/api/v1/practice/sessions/{session_id}/complete", json={}, headers=headers
        )
        self.assertEqual(complete_resp.status_code, 200)
        self.assertEqual(complete_resp.json()["updated_mastery_level"], "HARD")

    def test_mastery_transfer_between_contents(self):
        """Strong, well-evidenced mastery in content A should transfer to a
        brand-new content B session."""
        student = f"transfer-{uuid4().hex[:8]}"
        headers = _auth(student)
        self._seed(
            self._set_mastery(
                student, self.content_a_id, score=88.0, level="HARD",
                answered=30, correct=27, confidence=0.95,
            )
        )

        resp = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(self.content_b_id), "requested_question_count": 1},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["recommended_difficulty"], "HARD")

    # ------------------------------------------------------------------
    # Correction edge cases
    # ------------------------------------------------------------------

    def test_question_without_answer_key_does_not_crash(self):
        content_node_id = self._seed(self._new_content_node())
        version_id, options = self._seed(
            self._seed_question(
                content_node_id, difficulty="EASY", with_answer_key=False
            )
        )
        student = f"nogabarito-{uuid4().hex[:8]}"
        headers = _auth(student)

        create_resp = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(content_node_id), "requested_question_count": 1},
            headers=headers,
        )
        session_id = create_resp.json()["id"]
        q = self.client.get(
            f"/api/v1/practice/sessions/{session_id}/questions", headers=headers
        ).json()[0]

        self.client.post(
            f"/api/v1/practice/sessions/{session_id}/questions/{q['id']}/answer",
            json={"selected_option_id": q["options"][0]["id"]},
            headers=headers,
        )

        complete_resp = self.client.post(
            f"/api/v1/practice/sessions/{session_id}/complete", json={}, headers=headers
        )
        self.assertEqual(complete_resp.status_code, 200)
        result = complete_resp.json()
        self.assertEqual(result["correct_count"], 0)
        self.assertEqual(result["incorrect_count"], 0)
        self.assertEqual(result["answered_count"], 1)

    def test_invalid_option_is_rejected(self):
        content_node_id = self._seed(self._new_content_node())
        version_a, options_a = self._seed(self._seed_question(content_node_id))
        version_b, options_b = self._seed(self._seed_question(content_node_id))
        student = f"invalidopt-{uuid4().hex[:8]}"
        headers = _auth(student)

        create_resp = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(content_node_id), "requested_question_count": 2},
            headers=headers,
        )
        session_id = create_resp.json()["id"]
        questions = self.client.get(
            f"/api/v1/practice/sessions/{session_id}/questions", headers=headers
        ).json()

        first_question = questions[0]
        other_question_version = version_b if first_question["question_version_id"] == str(version_a) else version_a
        other_options = options_b if other_question_version == version_b else options_a

        answer_resp = self.client.post(
            f"/api/v1/practice/sessions/{session_id}/questions/{first_question['id']}/answer",
            json={"selected_option_id": str(other_options["A"])},
            headers=headers,
        )
        self.assertEqual(answer_resp.status_code, 400)

    # ------------------------------------------------------------------
    # Security / isolation
    # ------------------------------------------------------------------

    def test_student_cannot_access_another_students_session(self):
        content_node_id = self._seed(self._new_content_node())
        self._seed(self._seed_question(content_node_id))
        alice = f"alice2-{uuid4().hex[:8]}"
        bob = f"bob2-{uuid4().hex[:8]}"

        create_resp = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(content_node_id), "requested_question_count": 1},
            headers=_auth(alice),
        )
        session_id = create_resp.json()["id"]

        for method, url in [
            ("get", f"/api/v1/practice/sessions/{session_id}"),
            ("get", f"/api/v1/practice/sessions/{session_id}/questions"),
            ("get", f"/api/v1/practice/sessions/{session_id}/next-question"),
        ]:
            resp = getattr(self.client, method)(url, headers=_auth(bob))
            self.assertEqual(resp.status_code, 403, f"{method} {url}")

        complete_resp = self.client.post(
            f"/api/v1/practice/sessions/{session_id}/complete", json={}, headers=_auth(bob)
        )
        self.assertEqual(complete_resp.status_code, 403)

    def test_nonexistent_session_returns_404(self):
        resp = self.client.get(
            f"/api/v1/practice/sessions/{uuid4()}", headers=_auth("someone")
        )
        self.assertEqual(resp.status_code, 404)

    # ------------------------------------------------------------------
    # Selection edge cases
    # ------------------------------------------------------------------

    def test_question_repetition_when_not_enough_fresh_questions(self):
        content_node_id = self._seed(self._new_content_node())
        version_id, options = self._seed(self._seed_question(content_node_id, difficulty="EASY"))
        student = f"repeat-{uuid4().hex[:8]}"
        headers = _auth(student)

        # Simulate that this student already answered this question recently.
        async def _record_prior_history():
            async with self.session_factory() as session:
                history = LearningHistory(
                    external_identity_id=student,
                    activity_type="INDIVIDUAL_PRACTICE",
                    question_version_id=version_id,
                    difficulty_level="EASY",
                    content_node_id=content_node_id,
                )
                session.add(history)
                await session.commit()

        self._seed(_record_prior_history())

        create_resp = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(content_node_id), "requested_question_count": 2},
            headers=headers,
        )
        self.assertEqual(create_resp.status_code, 201)
        questions = self.client.get(
            f"/api/v1/practice/sessions/{create_resp.json()['id']}/questions", headers=headers
        ).json()
        # Only one question exists in the bank; it must still be selected
        # (repeated) to try to fill the request, even though it was recently seen.
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["question_version_id"], str(version_id))

    def test_requested_count_exceeds_available_questions(self):
        content_node_id = self._seed(self._new_content_node())
        self._seed(self._seed_question(content_node_id, difficulty="MEDIUM"))
        self._seed(self._seed_question(content_node_id, difficulty="MEDIUM"))
        student = f"toomany-{uuid4().hex[:8]}"
        expected_available = 2

        create_resp = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(content_node_id), "requested_question_count": 50},
            headers=_auth(student),
        )
        self.assertEqual(create_resp.status_code, 201)
        questions = self.client.get(
            f"/api/v1/practice/sessions/{create_resp.json()['id']}/questions",
            headers=_auth(student),
        ).json()
        # Session creation must not fail even though fewer questions exist,
        # and must not exceed what's actually available.
        self.assertEqual(len(questions), expected_available)

    def test_selection_is_deterministic_for_same_state(self):
        content_node_id = self._seed(self._new_content_node())
        self._seed(self._seed_question(content_node_id, difficulty="EASY"))
        self._seed(self._seed_question(content_node_id, difficulty="EASY"))

        results = []
        for _ in range(2):
            student = f"deterministic-{uuid4().hex[:8]}"
            resp = self.client.post(
                "/api/v1/practice/sessions",
                json={"content_node_id": str(content_node_id), "requested_question_count": 2},
                headers=_auth(student),
            )
            questions = self.client.get(
                f"/api/v1/practice/sessions/{resp.json()['id']}/questions",
                headers=_auth(student),
            ).json()
            results.append([q["question_version_id"] for q in questions])

        # Same DB state (same two candidates, fresh students) => same order.
        self.assertEqual(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
