"""HTTP-level, production-fidelity coverage for api/routes/attempts.py.

Overnight bug-hunt campaign (2026-09-22): Wave 2 confirmed a recurring bug
class in assessments.py - a route loads an ORM object, later does
`await session.commit()`, then reads a synchronous attribute on that SAME
object. In production `create_session_factory()` uses the sessionmaker
default `expire_on_commit=True`, so commit() expires every object touched in
that session and the next sync attribute read triggers a background refresh
outside the async greenlet bridge -> MissingGreenlet (500, with the write
already persisted).

This file targets attempts.py's 6 `await session.commit()` sites with a
session fixture that mirrors production exactly: `async_sessionmaker(engine,
class_=AsyncSession)` with NO `expire_on_commit` override (defaults to
True). This is deliberately different from tests/test_phase7_assessment_core_http.py's
fixture, which sets `expire_on_commit=False` and therefore cannot catch this
bug class - it happens to still cover the same endpoints, just under a
session configuration that papers over the exact failure mode we're hunting.

Investigation result: attempts.py's 6 commit sites (start_attempt,
get_attempt's expiry branch, save_answer's normal + expiry branches,
submit_attempt's normal + expiry branches) already capture-into-locals or
`await session.refresh(...)` every object read after commit - this was fixed
in a prior session (commit fa01bcc, "fix: mais MissingGreenlet e erros mal
tratados em 4 areas paralelas"). These tests exist as a production-fidelity
regression guard for that fix, run BEFORE any further changes: they passed
cleanly on first run, confirming the sites are safe, not proving a new bug.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
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
from agente_ia_edu.api.routes.assessments import router as assessments_router
from agente_ia_edu.api.routes.attempts import router as attempts_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    AssessmentAnswer,
    AssessmentAssignment,
    AssessmentAttempt,
    BookletQuestion,
    CatalogNode,
    ContentQuestionLink,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    LearningHistory,
    Question,
    QuestionOption,
    QuestionVersion,
    School,
    SourceDocument,
    StudentContentMastery,
    Taxonomy,
    TaxonomyNode,
    UserSchoolLink,
)
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext


class AttemptsRouteCoverageHTTP(unittest.TestCase):
    def setUp(self):
        async def setup_database():
            engine = create_async_engine(
                "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            # NO expire_on_commit override - this mirrors
            # create_session_factory()'s production default (expire_on_commit=True)
            # exactly. Do not "fix" this to False; that would hide the bug class
            # this file exists to catch.
            factory = async_sessionmaker(engine, class_=AsyncSession)
            async with factory() as session:
                school_a = School(code=f"ARC-A-{uuid4().hex[:6]}", name="Escola A")
                school_b = School(code=f"ARC-B-{uuid4().hex[:6]}", name="Escola B")
                session.add_all([school_a, school_b])
                await session.flush()
                school_a_id, school_b_id = school_a.id, school_b.id
                session.add_all([
                    UserSchoolLink(
                        external_user_id="student-a",
                        school_id=school_a_id,
                        role="STUDENT",
                        scope_type="CLASSROOM",
                        scope_external_id="CLASS-A",
                        active=True,
                        metadata_={
                            "academic_year": "2026",
                            "unit_id": "UNIT-A",
                            "segment": "ENSINO_MEDIO",
                            "grade_level": "3_SERIE",
                            "classroom_id": "CLASS-A",
                        },
                    ),
                    UserSchoolLink(
                        external_user_id="student-b",
                        school_id=school_b_id,
                        role="STUDENT",
                        scope_type="CLASSROOM",
                        scope_external_id="CLASS-B",
                        active=True,
                    ),
                ])
                await session.commit()
                return engine, factory, school_a_id, school_b_id

        self.engine, self.session_factory, self.school_a, self.school_b = asyncio.run(
            setup_database()
        )
        self.context = {
            "value": AuthenticatedUserContext(
                user_id="teacher-a",
                external_identity_id="teacher-a",
                role="TEACHER",
                school_id=self.school_a,
                scope_type="CLASSROOM",
                scope_external_id="CLASS-A",
            )
        }
        self.identity = {
            "value": ExternalIdentityContext(
                provider="test", external_user_id="teacher-a", roles=("teacher",)
            )
        }
        app = FastAPI()
        app.include_router(assessments_router)
        app.include_router(attempts_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_authenticated_context] = (
            lambda: self.context["value"]
        )
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    # ------------------------------------------------------------------
    # seeding helpers (adapted from tests/test_phase7_assessment_core_http.py,
    # trimmed to what attempts.py's routes actually touch)
    # ------------------------------------------------------------------

    async def seed_question_bank(self, count=3):
        async with self.session_factory() as session:
            root = CatalogNode(
                node_type="DISCIPLINE", name="Quimica", position=1, active=True
            )
            session.add(root)
            await session.flush()
            root.root_id = root.id
            content = CatalogNode(
                parent_id=root.id,
                root_id=root.id,
                node_type="CONTENT",
                code=f"ARC-{uuid4().hex[:8]}",
                name="Equilibrio",
                position=1,
                active=True,
            )
            session.add(content)
            taxonomy = Taxonomy(code=f"arc-{uuid4().hex}", name="Attempts Coverage", version="1")
            session.add(taxonomy)
            await session.flush()
            session.add(TaxonomyNode(
                id=content.id,
                taxonomy_id=taxonomy.id,
                code=content.code,
                name=content.name,
                node_type="skill",
            ))
            institution = Institution(code=f"I-{uuid4().hex}", name="Institution")
            session.add(institution)
            await session.flush()
            exam = Exam(institution_id=institution.id, code=f"E-{uuid4().hex}", name="Exam")
            session.add(exam)
            await session.flush()
            application = ExamApplication(exam_id=exam.id, year=2026, application_type="regular")
            session.add(application)
            await session.flush()
            booklet = ExamBooklet(exam_application_id=application.id, code=f"B-{uuid4().hex}")
            source = SourceDocument(
                exam_application_id=application.id,
                document_type="proof",
                source_url="https://example.test/attempts.pdf",
                acquired_at=datetime.now(timezone.utc),
                content_hash=uuid4().hex,
            )
            session.add_all([booklet, source])
            await session.flush()
            revision = AnswerKeyRevision(
                source_document_id=source.id,
                revision_number=1,
                is_official=True,
            )
            session.add(revision)
            await session.flush()
            seeded = []
            for position in range(1, count + 1):
                question = Question(
                    validation_status="approved",
                    status="PUBLISHED",
                    visibility_scope="PUBLIC",
                )
                session.add(question)
                await session.flush()
                version = QuestionVersion(
                    question_id=question.id,
                    version_kind="official_original",
                    canonical_text=f"Enunciado congelado {position}",
                    content_hash=uuid4().hex,
                    recommended_difficulty="EASY",
                )
                session.add(version)
                await session.flush()
                correct = QuestionOption(
                    question_version_id=version.id,
                    option_key="A",
                    position=1,
                    text="Correta",
                    is_valid_option=True,
                )
                wrong = QuestionOption(
                    question_version_id=version.id,
                    option_key="B",
                    position=2,
                    text="Incorreta",
                    is_valid_option=True,
                )
                session.add_all([correct, wrong])
                await session.flush()
                occurrence = BookletQuestion(
                    exam_booklet_id=booklet.id,
                    question_version_id=version.id,
                    position=position,
                )
                session.add(occurrence)
                await session.flush()
                key = AnswerKeyEntry(
                    answer_key_revision_id=revision.id,
                    booklet_question_id=occurrence.id,
                    official_answer_label="A",
                    resolved_option_id=correct.id,
                )
                session.add_all([
                    key,
                    ContentQuestionLink(
                        content_node_id=content.id,
                        question_version_id=version.id,
                    ),
                ])
                seeded.append((version.id, correct.id, wrong.id, key))
            content_id = content.id
            revision_id = revision.id
            await session.commit()
            return content_id, revision_id, seeded

    def create_published_assessment(self, item_count=3):
        content_id, revision_id, questions = asyncio.run(
            self.seed_question_bank(item_count)
        )
        created = self.client.post(
            "/api/v1/assessments",
            json={
                "title": "Simulado individual",
                "school_id": str(self.school_a),
                "academic_year": "2026",
                "scope_type": "CLASSROOM",
                "scope_external_id": "CLASS-A",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        assessment_id = created.json()["id"]
        version = self.client.post(
            f"/api/v1/assessments/{assessment_id}/versions",
            json={"title": "Versao aplicada"},
        )
        self.assertEqual(version.status_code, 201, version.text)
        version_id = version.json()["id"]
        for position, (question_version_id, _, _, _) in enumerate(questions, start=1):
            item = self.client.post(
                f"/api/v1/assessments/{assessment_id}/versions/{version_id}/items",
                json={
                    "question_version_id": str(question_version_id),
                    "position": position,
                    "points": 1,
                },
            )
            self.assertEqual(item.status_code, 201, item.text)
        for action, expected in (
            ("review", "review"),
            ("approve", "approved"),
            ("publish", "published"),
        ):
            response = self.client.post(
                f"/api/v1/assessments/{assessment_id}/versions/{version_id}/{action}"
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], expected)
        publication = self.client.post(
            f"/api/v1/assessments/{assessment_id}/publications",
            json={
                "assessment_version_id": version_id,
                "publication_type": "immediate",
                "time_limit_seconds": 1800,
                "attempts_allowed": 1,
            },
        )
        self.assertEqual(publication.status_code, 201, publication.text)
        publication_id = publication.json()["id"]
        activated = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/activate"
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        return assessment_id, version_id, publication_id, content_id, revision_id, questions

    def assign(self, publication_id, student_id="student-a"):
        return self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/assignments",
            json={"student_external_id": student_id},
        )

    def become_student(self, student_id):
        self.context["value"] = AuthenticatedUserContext(
            user_id=student_id,
            external_identity_id=student_id,
            role="STUDENT",
            school_id=self.school_a if student_id != "student-b" else self.school_b,
            scope_type="CLASSROOM",
            scope_external_id="CLASS-A" if student_id == "student-a" else "CLASS-B",
        )
        self.identity["value"] = ExternalIdentityContext(
            provider="test",
            external_user_id=student_id,
            student_id=student_id,
            institution_id=str(self.context["value"].school_id),
            classroom_id=self.context["value"].scope_external_id,
            roles=("student",),
            metadata={
                "academic_year": "2026",
                "unit_id": "UNIT-A" if student_id != "student-b" else "UNIT-B",
                "segment": "ENSINO_MEDIO",
                "grade_level": "3_SERIE",
            },
        )

    def _start_attempt(self, item_count=2, student="student-a"):
        _, _, publication_id, content_id, _, questions = self.create_published_assessment(item_count)
        self.assertEqual(self.assign(publication_id, student).status_code, 201)
        self.become_student(student)
        started = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/attempts"
        )
        self.assertEqual(started.status_code, 201, started.text)
        return started.json()["id"], questions, content_id

    async def _expire_attempt(self, attempt_id):
        async with self.session_factory() as session:
            attempt = await session.get(AssessmentAttempt, UUID(attempt_id))
            attempt.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await session.commit()

    # ------------------------------------------------------------------
    # commit-site 1: start_attempt (attempts.py ~215) - commits attempt +
    # assignment (status="IN_PROGRESS") together, then response reads
    # attempt.id/attempt_number/status/started_at/expires_at/score/max_score
    # and assignment.id and publication.assessment_version_id.
    # ------------------------------------------------------------------
    def test_start_attempt_commits_and_reads_attempt_and_assignment_cleanly(self):
        _, _, publication_id, _, _, _questions = self.create_published_assessment(2)
        self.assertEqual(self.assign(publication_id, "student-a").status_code, 201)
        self.become_student("student-a")

        resp = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/attempts"
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        payload = resp.json()
        self.assertIn("id", payload)
        self.assertEqual(payload["attempt_number"], 1)
        self.assertEqual(payload["status"], "in_progress")
        self.assertIsNotNone(payload["assignment_id"])
        self.assertIsNotNone(payload["expires_at"])

        # Confirm the write actually persisted (not just that the response
        # didn't crash): a phantom-commit bug would still leave the row
        # committed even while the endpoint 500s.
        async def inspect():
            async with self.session_factory() as session:
                attempt = await session.get(AssessmentAttempt, UUID(payload["id"]))
                return attempt.status, attempt.attempt_number

            return None

        status, attempt_number = asyncio.run(inspect())
        self.assertEqual(status, "in_progress")
        self.assertEqual(attempt_number, 1)

    # ------------------------------------------------------------------
    # commit-site 2: get_attempt's expiry branch (attempts.py ~266) - sets
    # attempt.status="expired", commits, then immediately raises. No
    # attribute is read on `attempt` after this commit, so this should
    # already be safe by inspection; this test proves it end-to-end anyway.
    # ------------------------------------------------------------------
    def test_get_attempt_expiry_commit_then_raise_is_clean(self):
        attempt_id, _, _ = self._start_attempt(1)
        asyncio.run(self._expire_attempt(attempt_id))

        resp = self.client.get(f"/api/v1/assessments/attempts/{attempt_id}")
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(resp.json()["detail"], "Attempt has expired")

        async def inspect():
            async with self.session_factory() as session:
                return (await session.get(AssessmentAttempt, UUID(attempt_id))).status

        self.assertEqual(asyncio.run(inspect()), "expired")

    # ------------------------------------------------------------------
    # commit-site 3: save_answer's normal path (attempts.py ~392) - commits
    # a newly-created (or updated) AssessmentAnswer, then
    # `await session.refresh(answer)` before reading answer.assessment_item_id/
    # selected_option_id/response_text/correction_status/first_answered_at.
    # Exercised twice (create then update) to hit both branches.
    # ------------------------------------------------------------------
    def test_save_answer_create_then_update_commits_cleanly(self):
        attempt_id, questions, _ = self._start_attempt(1)
        item_id = self.client.get(
            f"/api/v1/assessments/attempts/{attempt_id}"
        ).json()["items"][0]["id"]

        created = self.client.put(
            f"/api/v1/assessments/attempts/{attempt_id}/answers/{item_id}",
            json={"selected_option_id": str(questions[0][1])},
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["correction_status"], "pending")
        self.assertIsNotNone(created.json()["first_answered_at"])

        updated = self.client.put(
            f"/api/v1/assessments/attempts/{attempt_id}/answers/{item_id}",
            json={"selected_option_id": str(questions[0][2])},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["selected_option_id"], str(questions[0][2]))

    # ------------------------------------------------------------------
    # commit-site 4: save_answer's expiry branch (attempts.py ~347) - same
    # shape as commit-site 2 (commit then immediate raise, nothing read
    # after).
    # ------------------------------------------------------------------
    def test_save_answer_expiry_commit_then_raise_is_clean(self):
        attempt_id, _, _ = self._start_attempt(1)
        item_id = self.client.get(
            f"/api/v1/assessments/attempts/{attempt_id}"
        ).json()["items"][0]["id"]
        asyncio.run(self._expire_attempt(attempt_id))

        resp = self.client.put(
            f"/api/v1/assessments/attempts/{attempt_id}/answers/{item_id}",
            json={},
        )
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(resp.json()["detail"], "Attempt has expired")

    # ------------------------------------------------------------------
    # commit-site 5: submit_attempt's normal path (attempts.py ~528) - the
    # heaviest site: mutates attempt + every AssessmentAnswer + the linked
    # AssessmentAssignment, records LearningHistory and StudentContentMastery
    # via two services inside the same session, commits, then
    # `await session.refresh(attempt)` before reading attempt.id/status/
    # submitted_at/score/max_score/correct_answers/answered_count.
    # ------------------------------------------------------------------
    def test_submit_attempt_commits_and_reads_attempt_cleanly(self):
        attempt_id, questions, content_id = self._start_attempt(2)
        detail = self.client.get(f"/api/v1/assessments/attempts/{attempt_id}").json()
        correct_item, unknown_item = detail["items"]
        self.client.put(
            f"/api/v1/assessments/attempts/{attempt_id}/answers/{correct_item['id']}",
            json={"selected_option_id": str(questions[0][1])},
        )
        self.client.put(
            f"/api/v1/assessments/attempts/{attempt_id}/answers/{unknown_item['id']}",
            json={"is_unknown": True},
        )

        resp = self.client.post(f"/api/v1/assessments/attempts/{attempt_id}/submit")
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertEqual(payload["status"], "submitted")
        self.assertEqual(payload["correct_answers"], 1)
        self.assertEqual(payload["unknown_answers"], 1)
        self.assertIsNotNone(payload["submitted_at"])

        # Downstream side effects (LearningHistory, StudentContentMastery,
        # AssessmentAssignment.status) really landed - confirms the commit
        # actually persisted rather than the response merely not crashing.
        async def inspect():
            async with self.session_factory() as session:
                histories = list((await session.scalars(
                    select(LearningHistory).where(
                        LearningHistory.assessment_attempt_id == UUID(attempt_id)
                    )
                )).all())
                mastery = await session.scalar(select(StudentContentMastery).where(
                    StudentContentMastery.external_identity_id == "student-a",
                    StudentContentMastery.content_node_id == content_id,
                ))
                assignment = await session.scalar(select(AssessmentAssignment).where(
                    AssessmentAssignment.recipient_id == "student-a",
                ))
                return histories, mastery, assignment

        histories, mastery, assignment = asyncio.run(inspect())
        self.assertEqual(len(histories), 2)
        self.assertIsNotNone(mastery)
        self.assertEqual(assignment.status, "COMPLETED")

    # ------------------------------------------------------------------
    # commit-site 6: submit_attempt's expiry branch (attempts.py ~441) -
    # same shape as commit-sites 2/4.
    # ------------------------------------------------------------------
    def test_submit_attempt_expiry_commit_then_raise_is_clean(self):
        attempt_id, _, _ = self._start_attempt(1)
        asyncio.run(self._expire_attempt(attempt_id))

        resp = self.client.post(f"/api/v1/assessments/attempts/{attempt_id}/submit")
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(resp.json()["detail"], "Attempt has expired")

    # ------------------------------------------------------------------
    # get_result has no commit site of its own, but only makes sense to
    # exercise (under the production-fidelity session) after submit_attempt's
    # commit above - confirms the full happy-path chain survives
    # expire_on_commit=True end to end.
    # ------------------------------------------------------------------
    def test_get_result_after_submit_reads_cleanly(self):
        attempt_id, questions, _ = self._start_attempt(1)
        item_id = self.client.get(
            f"/api/v1/assessments/attempts/{attempt_id}"
        ).json()["items"][0]["id"]
        self.client.put(
            f"/api/v1/assessments/attempts/{attempt_id}/answers/{item_id}",
            json={"selected_option_id": str(questions[0][1])},
        )
        submitted = self.client.post(f"/api/v1/assessments/attempts/{attempt_id}/submit")
        self.assertEqual(submitted.status_code, 200, submitted.text)

        resp = self.client.get(f"/api/v1/assessments/attempts/{attempt_id}/result")
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertEqual(payload["correct_answers"], 1)
        self.assertEqual(len(payload["answers"]), 1)


if __name__ == "__main__":
    unittest.main()
