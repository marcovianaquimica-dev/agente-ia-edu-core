"""PostgreSQL HTTP proof for Domain Map -> practice -> evidence -> new action."""

import asyncio
import os
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.domain_map import domain_map_router
from agente_ia_edu.api.routes.learning_path import practice_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    CatalogNode,
    ContentQuestionLink,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    LearningHistory,
    PedagogicalUniverse,
    PedagogicalUniverseBinding,
    PedagogicalUniverseCatalogScope,
    Question,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
    StudentContentMastery,
    Taxonomy,
    TaxonomyNode,
)
from agente_ia_edu.identity import ExternalIdentityContext
from tests._postgres_test_db import create_database, drop_database


class Phase6CPostgreSQLE2E(unittest.TestCase):
    database_name = "agente_ia_edu_phase6c_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "PHASE6C_TEST_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "PHASE6C_TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest("PostgreSQL de teste indisponivel para Phase 6C") from exc
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
            external_user_id="phase6c-postgres-student",
            roles=("student",),
        )
        app = FastAPI()
        app.include_router(domain_map_router)
        app.include_router(practice_router)
        app.dependency_overrides[get_session_factory] = lambda: cls.session_factory
        app.dependency_overrides[get_current_identity] = lambda: cls.identity
        cls.app = app
        cls.client = TestClient(app)

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
                code="PHASE6C-PG",
                name="Equilibrio",
                position=1,
                active=True,
            )
            session.add(content)
            await session.flush()
            taxonomy = Taxonomy(code="phase6c-pg", name="Phase 6C PG", version="1")
            session.add(taxonomy)
            await session.flush()
            session.add(TaxonomyNode(
                id=content.id,
                taxonomy_id=taxonomy.id,
                code=content.code,
                name=content.name,
                node_type="skill",
            ))
            universe = PedagogicalUniverse(
                external_id="phase6c-pg",
                slug="phase6c-pg",
                name="Phase 6C PostgreSQL",
                status="ACTIVE",
                owner_type="PLATFORM",
                configuration_version="v1",
            )
            session.add(universe)
            await session.flush()
            session.add_all([
                PedagogicalUniverseBinding(
                    universe_id=universe.id,
                    subject_type="EXTERNAL_IDENTITY",
                    subject_external_id=cls.identity.external_user_id,
                    active=True,
                ),
                PedagogicalUniverseCatalogScope(
                    universe_id=universe.id,
                    catalog_node_id=content.id,
                    scope_kind="CONTENT",
                    include_descendants=False,
                ),
            ])

            institution = Institution(code="PHASE6C-PG", name="Phase 6C")
            session.add(institution)
            await session.flush()
            exam = Exam(institution_id=institution.id, code="PHASE6C", name="Phase 6C")
            session.add(exam)
            await session.flush()
            application = ExamApplication(exam_id=exam.id, year=2026, application_type="regular")
            session.add(application)
            await session.flush()
            booklet = ExamBooklet(exam_application_id=application.id, code="PHASE6C")
            source = SourceDocument(
                exam_application_id=application.id,
                document_type="proof",
                source_url="https://example.test/phase6c.pdf",
                acquired_at=datetime.now(timezone.utc),
                content_hash=uuid4().hex,
            )
            session.add_all([booklet, source])
            await session.flush()
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
                canonical_text="Questao Phase 6C PostgreSQL",
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
                position=1,
            )
            revision = AnswerKeyRevision(
                source_document_id=source.id,
                revision_number=1,
                is_official=True,
            )
            session.add_all([occurrence, revision])
            await session.flush()
            session.add_all([
                AnswerKeyEntry(
                    answer_key_revision_id=revision.id,
                    booklet_question_id=occurrence.id,
                    official_answer_label="A",
                    resolved_option_id=correct.id,
                ),
                ContentQuestionLink(
                    content_node_id=content.id,
                    question_version_id=version.id,
                ),
                StudentContentMastery(
                    external_identity_id=cls.identity.external_user_id,
                    content_node_id=content.id,
                    mastery_score=25,
                    confidence=0.5,
                    questions_answered=4,
                    questions_correct=1,
                    current_level="EASY",
                ),
            ])
            for index in range(4):
                session.add(LearningHistory(
                    external_identity_id=cls.identity.external_user_id,
                    activity_type="INITIAL_DIAGNOSTIC",
                    question_version_id=version.id,
                    difficulty_level="EASY",
                    is_correct=index == 0,
                    content_node_id=content.id,
                ))
            await session.commit()
            return content.id, correct.id

    def test_http_cycle_recalculates_next_best_action(self):
        content_id, correct_id = asyncio.run(self._seed())
        before_response = self.client.get("/api/v1/student/domain-map")
        self.assertEqual(before_response.status_code, 200, before_response.text)
        before = before_response.json()
        before_action = before["next_best_action"]

        created = self.client.post(
            "/api/v1/practice/sessions", json={"requested_question_count": 1}
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["content_node_id"], str(content_id))
        session_id = created.json()["id"]
        question = self.client.get(
            f"/api/v1/practice/sessions/{session_id}/next-question"
        ).json()["question"]
        answered = self.client.post(
            f"/api/v1/practice/sessions/{session_id}/questions/{question['id']}/answer",
            json={"selected_option_id": str(correct_id)},
        )
        self.assertEqual(answered.status_code, 201, answered.text)
        completed = self.client.post(
            f"/api/v1/practice/sessions/{session_id}/complete", json={}
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(
            self.client.get(f"/api/v1/practice/sessions/{session_id}/result").status_code,
            200,
        )

        after = self.client.get("/api/v1/student/domain-map").json()
        content = next(
            item for item in after["contents"]
            if item["content_node_id"] == str(content_id)
        )
        self.assertEqual(content["evidence_origins"]["INDIVIDUAL_PRACTICE"], 1)
        self.assertEqual(content["evidence_count"], 5)
        self.assertNotEqual(content["mastery_score"], 25.0)
        self.assertNotEqual(after["next_best_action"], before_action)

        async def inspect_lineage():
            async with self.session_factory() as session:
                histories = list((await session.scalars(
                    select(LearningHistory).where(
                        LearningHistory.external_identity_id == self.identity.external_user_id
                    )
                )).all())
                mastery = await session.scalar(select(StudentContentMastery).where(
                    StudentContentMastery.external_identity_id == self.identity.external_user_id,
                    StudentContentMastery.content_node_id == content_id,
                ))
                return histories, mastery

        histories, mastery = asyncio.run(inspect_lineage())
        self.assertEqual(len(histories), 5)
        self.assertEqual(mastery.questions_answered, 5)
        self.assertEqual(mastery.questions_correct, 2)


if __name__ == "__main__":
    unittest.main()
