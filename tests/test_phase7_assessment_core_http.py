import asyncio
import unittest
from datetime import datetime, timezone
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
from agente_ia_edu.api.routes.domain_map import domain_map_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    AssessmentAnswer,
    AssessmentAssignment,
    AssessmentAttempt,
    AssessmentItem,
    AssessmentPublication,
    AssessmentVersion,
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


class Phase7AssessmentCoreHTTP(unittest.TestCase):
    def setUp(self):
        async def setup_database():
            engine = create_async_engine(
                "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            async with factory() as session:
                school_a = School(code="PH7-A", name="Escola Phase 7 A")
                school_b = School(code="PH7-B", name="Escola Phase 7 B")
                session.add_all([school_a, school_b])
                await session.flush()
                session.add_all([
                    UserSchoolLink(
                        external_user_id="student-a",
                        school_id=school_a.id,
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
                        external_user_id="student-other-class",
                        school_id=school_a.id,
                        role="STUDENT",
                        scope_type="CLASSROOM",
                        scope_external_id="CLASS-B",
                        active=True,
                    ),
                    UserSchoolLink(
                        external_user_id="student-b",
                        school_id=school_b.id,
                        role="STUDENT",
                        scope_type="CLASSROOM",
                        scope_external_id="CLASS-B",
                        active=True,
                    ),
                ])
                await session.commit()
                return engine, factory, school_a.id, school_b.id

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
        app.include_router(domain_map_router)
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
                code=f"PH7-{uuid4().hex[:8]}",
                name="Equilibrio",
                position=1,
                active=True,
            )
            session.add(content)
            taxonomy = Taxonomy(code=f"ph7-{uuid4().hex}", name="Phase 7", version="1")
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
                source_url="https://example.test/phase7.pdf",
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
            await session.commit()
            return content.id, revision.id, seeded

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
        self.assertEqual(publication.json()["assessment_version_id"], version_id)
        self.assertEqual(publication.json()["status"], "draft")
        publication_id = publication.json()["id"]
        activated = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/activate"
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()["status"], "active")
        return assessment_id, version_id, publication_id, content_id, revision_id, questions

    def assign(self, publication_id, student_id="student-a"):
        response = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/assignments",
            json={"student_external_id": student_id},
        )
        return response

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

    def test_teacher_scope_creation_and_version_workflow(self):
        outside = self.client.post(
            "/api/v1/assessments",
            json={"title": "Fora", "school_id": str(self.school_b)},
        )
        self.assertEqual(outside.status_code, 403, outside.text)
        assessment_id, version_id, _, _, _, questions = self.create_published_assessment(1)

        async def inspect():
            async with self.session_factory() as session:
                version = await session.get(AssessmentVersion, UUID(version_id))
                items = list((await session.scalars(
                    select(AssessmentItem).where(
                        AssessmentItem.assessment_version_id == version.id
                    ).order_by(AssessmentItem.position)
                )).all())
                return version, items

        version, items = asyncio.run(inspect())
        self.assertEqual(version.status, "published")
        self.assertEqual(items[0].question_version_id, questions[0][0])

    def test_assignment_and_attempt_authorization(self):
        _, _, publication_id, _, _, _ = self.create_published_assessment(1)
        self.assertEqual(self.assign(publication_id, "student-a").status_code, 201)
        self.assertEqual(self.assign(publication_id, "student-other-class").status_code, 403)
        self.assertEqual(self.assign(publication_id, "student-b").status_code, 403)

        self.become_student("student-other-class")
        no_assignment = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/attempts"
        )
        self.assertEqual(no_assignment.status_code, 403, no_assignment.text)
        self.become_student("student-b")
        wrong_school = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/attempts"
        )
        self.assertEqual(wrong_school.status_code, 403, wrong_school.text)
        self.become_student("student-a")
        started = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/attempts"
        )
        self.assertEqual(started.status_code, 201, started.text)

    def test_attempt_snapshot_and_frozen_answer_key(self):
        assessment_id, version_id, publication_id, _, revision_id, questions = (
            self.create_published_assessment(3)
        )
        assignment = self.assign(publication_id)
        self.assertEqual(assignment.status_code, 201, assignment.text)
        self.become_student("student-a")
        started = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/attempts"
        )
        self.assertEqual(started.status_code, 201, started.text)
        attempt_id = started.json()["id"]
        detail = self.client.get(f"/api/v1/assessments/attempts/{attempt_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["total_questions"], 3)
        self.assertEqual(detail.json()["assessment_version_id"], version_id)
        self.assertEqual(detail.json()["items"][0]["question_number"], 1)
        self.assertEqual(detail.json()["items"][0]["canonical_text"], "Enunciado congelado 1")
        self.assertEqual(detail.json()["items"][0]["question_version_id"], str(questions[0][0]))

        async def mutate_sources():
            async with self.session_factory() as session:
                version = await session.get(QuestionVersion, questions[0][0])
                version.canonical_text = "Enunciado alterado depois do inicio"
                key = await session.get(AnswerKeyEntry, questions[0][3].id)
                key.resolved_option_id = questions[0][2]
                await session.commit()

        asyncio.run(mutate_sources())
        detail_again = self.client.get(f"/api/v1/assessments/attempts/{attempt_id}")
        self.assertEqual(detail_again.json()["items"][0]["canonical_text"], "Enunciado congelado 1")
        answer = self.client.put(
            f"/api/v1/assessments/attempts/{attempt_id}/answers/{detail.json()['items'][0]['id']}",
            json={"selected_option_id": str(questions[0][1])},
        )
        self.assertEqual(answer.status_code, 200, answer.text)
        submitted = self.client.post(f"/api/v1/assessments/attempts/{attempt_id}/submit")
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertEqual(submitted.json()["correct_answers"], 1)

    def test_result_evidence_mastery_and_domain_map(self):
        _, _, publication_id, content_id, _, questions = self.create_published_assessment(3)
        self.assertEqual(self.assign(publication_id).status_code, 201)
        self.become_student("student-a")
        started = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/attempts"
        ).json()
        attempt_id = started["id"]
        detail = self.client.get(f"/api/v1/assessments/attempts/{attempt_id}").json()
        correct_item, unknown_item, _ = detail["items"]
        self.client.put(
            f"/api/v1/assessments/attempts/{attempt_id}/answers/{correct_item['id']}",
            json={"selected_option_id": str(questions[0][1])},
        )
        unknown = self.client.put(
            f"/api/v1/assessments/attempts/{attempt_id}/answers/{unknown_item['id']}",
            json={"is_unknown": True},
        )
        self.assertEqual(unknown.status_code, 200, unknown.text)
        submitted = self.client.post(f"/api/v1/assessments/attempts/{attempt_id}/submit")
        self.assertEqual(submitted.status_code, 200, submitted.text)
        result = self.client.get(f"/api/v1/assessments/attempts/{attempt_id}/result")
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()
        self.assertEqual(payload["correct_answers"], 1)
        self.assertEqual(payload["incorrect_answers"], 0)
        self.assertEqual(payload["unknown_answers"], 1)
        self.assertEqual(payload["unanswered"], 1)

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
                    AssessmentAssignment.publication_id == UUID(publication_id),
                    AssessmentAssignment.recipient_id == "student-a",
                ))
                attempt = await session.get(AssessmentAttempt, UUID(attempt_id))
                answers = list((await session.scalars(select(AssessmentAnswer).where(
                    AssessmentAnswer.attempt_id == UUID(attempt_id)
                ))).all())
                return histories, mastery, assignment, attempt, answers

        histories, mastery, assignment, attempt, answers = asyncio.run(inspect())
        self.assertEqual(len(histories), 2)
        self.assertEqual(
            {entry.activity_type for entry in histories}, {"OFFICIAL_ASSESSMENT"}
        )
        self.assertEqual(sum(entry.response_text == "UNKNOWN" for entry in histories), 1)
        self.assertEqual(mastery.questions_answered, 1)
        self.assertEqual(mastery.questions_correct, 1)
        self.assertEqual(assignment.status, "COMPLETED")
        self.assertEqual(attempt.assignment_id, assignment.id)
        self.assertEqual(attempt.metadata_["school_id"], str(self.school_a))
        self.assertEqual(attempt.metadata_["classroom_id"], "CLASS-A")
        domain_map = self.client.get("/api/v1/student/domain-map")
        self.assertEqual(domain_map.status_code, 200, domain_map.text)
        state = next(
            item for item in domain_map.json()["contents"]
            if item["content_node_id"] == str(content_id)
        )
        self.assertEqual(state["evidence_origins"]["OFFICIAL_ASSESSMENT"], 2)
        self.assertEqual(state["unknown_count"], 1)
        self.assertEqual(state["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
