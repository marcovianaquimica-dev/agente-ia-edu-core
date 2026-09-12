import asyncio
import unittest
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

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
    InitialDiagnostic,
    Institution,
    LearningHistory,
    PedagogicalContext,
    PedagogicalUniverse,
    PedagogicalUniverseBinding,
    PedagogicalUniverseCatalogScope,
    PracticeSession,
    Question,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
    StudentContentMastery,
    Taxonomy,
    TaxonomyNode,
)
from agente_ia_edu.identity import ExternalIdentityContext


class Phase6CAdaptivePracticeHTTP(unittest.TestCase):
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
            return engine, factory

        self.engine, self.session_factory = asyncio.run(setup_database())
        self.identity = {
            "value": ExternalIdentityContext(
                provider="test", external_user_id="phase6c-student", roles=("student",)
            )
        }
        app = FastAPI()
        app.include_router(domain_map_router)
        app.include_router(practice_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    async def seed_catalog(self):
        async with self.session_factory() as session:
            root = CatalogNode(
                node_type="DISCIPLINE", name="Quimica", position=1, active=True
            )
            session.add(root)
            await session.flush()
            root.root_id = root.id
            target = CatalogNode(
                parent_id=root.id,
                root_id=root.id,
                node_type="CONTENT",
                code=f"TARGET-{uuid4().hex[:6]}",
                name="Equilibrio",
                position=1,
                active=True,
            )
            outside = CatalogNode(
                parent_id=root.id,
                root_id=root.id,
                node_type="CONTENT",
                code=f"OUT-{uuid4().hex[:6]}",
                name="Fora do universo",
                position=2,
                active=True,
            )
            session.add_all([target, outside])
            await session.flush()
            taxonomy = Taxonomy(code=f"phase6c-{uuid4().hex}", name="Phase 6C", version="1")
            session.add(taxonomy)
            await session.flush()
            for node in (target, outside):
                session.add(TaxonomyNode(
                    id=node.id,
                    taxonomy_id=taxonomy.id,
                    code=node.code,
                    name=node.name,
                    node_type="skill",
                ))
            await session.flush()
            universe = PedagogicalUniverse(
                external_id=f"phase6c-{uuid4().hex}",
                slug=f"phase6c-{uuid4().hex}",
                name="Universo Phase 6C",
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
                    subject_external_id=self.identity["value"].external_user_id,
                    active=True,
                ),
                PedagogicalUniverseCatalogScope(
                    universe_id=universe.id,
                    catalog_node_id=target.id,
                    scope_kind="CONTENT",
                    include_descendants=False,
                ),
            ])
            await session.commit()
            return target.id, outside.id, universe.id, taxonomy.id

    async def seed_question(
        self,
        content_id,
        *,
        difficulty="EASY",
        published=True,
        validated=True,
        canonical_link=True,
        visibility_scope="PUBLIC",
    ):
        async with self.session_factory() as session:
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
            session.add(booklet)
            source = SourceDocument(
                exam_application_id=application.id,
                document_type="proof",
                source_url="https://example.test/proof.pdf",
                acquired_at=datetime.now(timezone.utc),
                content_hash=uuid4().hex,
            )
            session.add_all([booklet, source])
            await session.flush()
            question = Question(
                validation_status="approved" if validated else "draft",
                status="PUBLISHED" if published else "DRAFT",
                visibility_scope=visibility_scope,
            )
            session.add(question)
            await session.flush()
            version = QuestionVersion(
                question_id=question.id,
                version_kind="official_original",
                canonical_text=f"Questao {uuid4().hex}",
                content_hash=uuid4().hex,
                recommended_difficulty=difficulty,
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
            session.add(AnswerKeyEntry(
                answer_key_revision_id=revision.id,
                booklet_question_id=occurrence.id,
                official_answer_label="A",
                resolved_option_id=correct.id,
            ))
            if canonical_link:
                session.add(ContentQuestionLink(
                    content_node_id=content_id,
                    question_version_id=version.id,
                ))
            await session.commit()
            return version.id, correct.id, wrong.id

    async def seed_mastery(self, content_id, score=20, confidence=0.5, answered=4, correct=1):
        async with self.session_factory() as session:
            session.add(StudentContentMastery(
                external_identity_id=self.identity["value"].external_user_id,
                content_node_id=content_id,
                mastery_score=score,
                confidence=confidence,
                questions_answered=answered,
                questions_correct=correct,
                current_level="EASY",
            ))
            question_id = (await session.scalars(select(QuestionVersion.id))).first()
            for index in range(answered):
                session.add(LearningHistory(
                    external_identity_id=self.identity["value"].external_user_id,
                    activity_type="INITIAL_DIAGNOSTIC",
                    question_version_id=question_id,
                    difficulty_level="EASY",
                    is_correct=index < correct,
                    content_node_id=content_id,
                ))
            await session.commit()

    def test_a_b_next_best_action_is_source_of_practice_and_snapshot(self):
        target, _, universe, _ = asyncio.run(self.seed_catalog())
        asyncio.run(self.seed_question(target))
        asyncio.run(self.seed_mastery(target))
        before = self.client.get("/api/v1/student/domain-map")
        self.assertEqual(before.status_code, 200, before.text)
        action = before.json()["next_best_action"]
        created = self.client.post("/api/v1/practice/sessions", json={"requested_question_count": 1})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["content_node_id"], action["target_content_node_id"])

        async def inspect_snapshot():
            async with self.session_factory() as session:
                practice = await session.get(PracticeSession, UUID(created.json()["id"]))
                return practice.metadata_

        snapshot = asyncio.run(inspect_snapshot())
        self.assertEqual(snapshot["universe"]["id"], str(universe))
        self.assertEqual(snapshot["selected_content_node_id"], str(target))
        self.assertEqual(snapshot["recommendation"]["action"], action["action"])

    def test_c_d_explicit_content_is_validated_against_universe(self):
        target, outside, _, _ = asyncio.run(self.seed_catalog())
        asyncio.run(self.seed_question(target))
        allowed = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(target), "requested_question_count": 1},
        )
        self.assertEqual(allowed.status_code, 201, allowed.text)
        blocked = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(outside), "requested_question_count": 1},
        )
        self.assertEqual(blocked.status_code, 403, blocked.text)
        missing = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(uuid4()), "requested_question_count": 1},
        )
        self.assertEqual(missing.status_code, 404, missing.text)

    def test_c_explicit_content_without_eligible_questions_creates_no_session(self):
        target, _, _, _ = asyncio.run(self.seed_catalog())
        response = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(target), "requested_question_count": 1},
        )
        self.assertEqual(response.status_code, 409, response.text)

        async def count_sessions():
            async with self.session_factory() as session:
                return len((await session.scalars(select(PracticeSession))).all())

        self.assertEqual(asyncio.run(count_sessions()), 0)

    def test_e_f_only_eligible_published_validated_universe_questions_appear(self):
        target, outside, _, _ = asyncio.run(self.seed_catalog())
        eligible, _, _ = asyncio.run(self.seed_question(target))
        outside_version, _, _ = asyncio.run(self.seed_question(outside))
        draft_version, _, _ = asyncio.run(self.seed_question(target, published=False))
        invalid_version, _, _ = asyncio.run(self.seed_question(target, validated=False))
        private_version, _, _ = asyncio.run(
            self.seed_question(target, visibility_scope="PRIVATE")
        )
        created = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(target), "requested_question_count": 10},
        )
        self.assertEqual(created.status_code, 201, created.text)
        questions = self.client.get(
            f"/api/v1/practice/sessions/{created.json()['id']}/questions"
        ).json()
        selected = {item["question_version_id"] for item in questions}
        self.assertEqual(selected, {str(eligible)})
        self.assertNotIn(str(outside_version), selected)
        self.assertNotIn(str(draft_version), selected)
        self.assertNotIn(str(invalid_version), selected)
        self.assertNotIn(str(private_version), selected)

    def test_h_unknown_is_persisted_separately_and_does_not_update_mastery(self):
        target, _, _, _ = asyncio.run(self.seed_catalog())
        asyncio.run(self.seed_question(target))
        created = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(target), "requested_question_count": 1},
        ).json()
        question = self.client.get(
            f"/api/v1/practice/sessions/{created['id']}/next-question"
        ).json()["question"]
        answer = self.client.post(
            f"/api/v1/practice/sessions/{created['id']}/questions/{question['id']}/answer",
            json={"is_unknown": True},
        )
        self.assertEqual(answer.status_code, 201, answer.text)
        self.assertTrue(answer.json()["is_unknown"])
        complete = self.client.post(
            f"/api/v1/practice/sessions/{created['id']}/complete", json={}
        )
        self.assertEqual(complete.status_code, 200, complete.text)

        async def inspect():
            async with self.session_factory() as session:
                history = (await session.scalars(select(LearningHistory))).all()
                mastery = (await session.scalars(select(StudentContentMastery))).all()
                return history, mastery

        history, mastery = asyncio.run(inspect())
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].activity_type, "INDIVIDUAL_PRACTICE")
        self.assertEqual(history[0].response_text, "UNKNOWN")
        self.assertIsNone(history[0].is_correct)
        self.assertEqual(mastery[0].questions_answered, 0)
        domain_map = self.client.get("/api/v1/student/domain-map").json()
        content = next(item for item in domain_map["contents"] if item["content_node_id"] == str(target))
        self.assertEqual(content["unknown_count"], 1)
        self.assertEqual(content["error_count"], 0)

    def test_g_recent_question_is_avoided_when_alternative_exists(self):
        target, _, _, _ = asyncio.run(self.seed_catalog())
        old_version, _, _ = asyncio.run(self.seed_question(target))
        fresh_version, _, _ = asyncio.run(self.seed_question(target))

        async def record_recent():
            async with self.session_factory() as session:
                session.add(LearningHistory(
                    external_identity_id=self.identity["value"].external_user_id,
                    activity_type="INDIVIDUAL_PRACTICE",
                    question_version_id=old_version,
                    difficulty_level="EASY",
                    is_correct=True,
                    content_node_id=target,
                ))
                await session.commit()

        asyncio.run(record_recent())
        created = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(target), "requested_question_count": 1},
        ).json()
        selected = self.client.get(
            f"/api/v1/practice/sessions/{created['id']}/questions"
        ).json()
        self.assertEqual(selected[0]["question_version_id"], str(fresh_version))

    def test_g_recent_question_is_reused_when_no_alternative_exists(self):
        target, _, _, _ = asyncio.run(self.seed_catalog())
        only_version, _, _ = asyncio.run(self.seed_question(target))

        async def record_recent():
            async with self.session_factory() as session:
                session.add(LearningHistory(
                    external_identity_id=self.identity["value"].external_user_id,
                    activity_type="INDIVIDUAL_PRACTICE",
                    question_version_id=only_version,
                    difficulty_level="EASY",
                    is_correct=True,
                    content_node_id=target,
                ))
                await session.commit()

        asyncio.run(record_recent())
        created = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(target), "requested_question_count": 1},
        )
        self.assertEqual(created.status_code, 201, created.text)
        selected = self.client.get(
            f"/api/v1/practice/sessions/{created.json()['id']}/questions"
        ).json()
        self.assertEqual(selected[0]["question_version_id"], str(only_version))

    def test_g_fresh_alternate_difficulty_precedes_recent_repeat(self):
        target, _, _, _ = asyncio.run(self.seed_catalog())
        recent_easy, _, _ = asyncio.run(self.seed_question(target, difficulty="EASY"))
        fresh_medium, _, _ = asyncio.run(self.seed_question(target, difficulty="MEDIUM"))

        async def record_recent():
            async with self.session_factory() as session:
                session.add(LearningHistory(
                    external_identity_id=self.identity["value"].external_user_id,
                    activity_type="INDIVIDUAL_PRACTICE",
                    question_version_id=recent_easy,
                    difficulty_level="EASY",
                    is_correct=True,
                    content_node_id=target,
                ))
                await session.commit()

        asyncio.run(record_recent())
        created = self.client.post(
            "/api/v1/practice/sessions",
            json={"content_node_id": str(target), "requested_question_count": 1},
        )
        self.assertEqual(created.status_code, 201, created.text)
        selected = self.client.get(
            f"/api/v1/practice/sessions/{created.json()['id']}/questions"
        ).json()
        self.assertEqual(selected[0]["question_version_id"], str(fresh_medium))
        self.assertEqual(selected[0]["difficulty_level"], "MEDIUM")

    def test_m_teacher_context_changes_priority_not_mastery(self):
        target, outside, universe, _ = asyncio.run(self.seed_catalog())
        asyncio.run(self.seed_question(target))
        asyncio.run(self.seed_question(outside))
        asyncio.run(self.seed_mastery(target, score=60, confidence=0.6, answered=4, correct=2))
        asyncio.run(self.seed_mastery(outside, score=60, confidence=0.6, answered=4, correct=2))

        async def seed_context():
            async with self.session_factory() as session:
                session.add_all([
                    PedagogicalUniverseCatalogScope(
                        universe_id=universe,
                        catalog_node_id=outside,
                        scope_kind="CONTENT",
                        include_descendants=False,
                    ),
                    PedagogicalContext(
                        content_node_id=target,
                        source="TEACHER",
                        institution_id="school-a",
                        classroom_id="class-a",
                        recorded_at=datetime.now(timezone.utc),
                        active=True,
                    ),
                ])
                await session.commit()

        asyncio.run(seed_context())
        self.identity["value"] = ExternalIdentityContext(
            provider="test",
            external_user_id="phase6c-student",
            institution_id="school-a",
            classroom_id="class-a",
            roles=("student",),
        )
        payload = self.client.get("/api/v1/student/domain-map").json()
        self.assertEqual(payload["next_best_action"]["target_content_node_id"], str(target))
        target_state = next(item for item in payload["contents"] if item["content_node_id"] == str(target))
        self.assertEqual(target_state["mastery_score"], 60.0)
        self.assertEqual(target_state["pedagogical_contexts"][0]["source"], "TEACHER")
        created = self.client.post(
            "/api/v1/practice/sessions", json={"requested_question_count": 1}
        )
        self.assertEqual(created.status_code, 201, created.text)

        async def inspect_context():
            async with self.session_factory() as session:
                practice = await session.get(PracticeSession, UUID(created.json()["id"]))
                return practice.metadata_["academic_context"]

        context = asyncio.run(inspect_context())
        self.assertEqual(context["school_id"], "school-a")
        self.assertEqual(context["classroom_id"], "class-a")

    def test_n_independent_student_uses_autonomous_path(self):
        target, _, _, _ = asyncio.run(self.seed_catalog())
        asyncio.run(self.seed_question(target))
        asyncio.run(self.seed_mastery(target))
        self.identity["value"] = ExternalIdentityContext(
            provider="test", external_user_id="phase6c-student", roles=("student",)
        )
        before = self.client.get("/api/v1/student/domain-map").json()
        self.assertTrue(before["is_independent"])
        created = self.client.post(
            "/api/v1/practice/sessions", json={"requested_question_count": 1}
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(
            created.json()["content_node_id"],
            before["next_best_action"]["target_content_node_id"],
        )

    def test_i_j_k_l_o_full_cycle_recalculates_domain_map(self):
        target, _, _, _ = asyncio.run(self.seed_catalog())
        _, correct, _ = asyncio.run(self.seed_question(target))
        asyncio.run(self.seed_mastery(target, score=25, confidence=0.5, answered=4, correct=1))
        before = self.client.get("/api/v1/student/domain-map").json()
        before_action = before["next_best_action"]
        created = self.client.post(
            "/api/v1/practice/sessions", json={"requested_question_count": 1}
        ).json()
        question = self.client.get(
            f"/api/v1/practice/sessions/{created['id']}/next-question"
        ).json()["question"]
        self.client.post(
            f"/api/v1/practice/sessions/{created['id']}/questions/{question['id']}/answer",
            json={"selected_option_id": str(correct)},
        )
        complete = self.client.post(
            f"/api/v1/practice/sessions/{created['id']}/complete", json={}
        )
        self.assertEqual(complete.status_code, 200, complete.text)
        result = self.client.get(
            f"/api/v1/practice/sessions/{created['id']}/result"
        )
        self.assertEqual(result.status_code, 200, result.text)
        after = self.client.get("/api/v1/student/domain-map").json()
        target_after = next(item for item in after["contents"] if item["content_node_id"] == str(target))
        self.assertEqual(target_after["evidence_origins"]["INDIVIDUAL_PRACTICE"], 1)
        self.assertGreater(target_after["evidence_count"], 4)
        self.assertNotEqual(target_after["mastery_score"], 25.0)
        self.assertNotEqual(after["next_best_action"], before_action)
        self.assertEqual(target_after["evidence_origins"]["INITIAL_DIAGNOSTIC"], 4)
        self.assertEqual(target_after["evidence_origins"]["OFFICIAL_ASSESSMENT"], 0)


if __name__ == "__main__":
    unittest.main()
