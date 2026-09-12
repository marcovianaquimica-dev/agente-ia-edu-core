import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    LearningHistory,
    PedagogicalContext,
    PedagogicalUniverse,
    PedagogicalUniverseBinding,
    PedagogicalUniverseCatalogScope,
    StudentContentMastery,
    Taxonomy,
    TaxonomyNode,
)
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.api.dependencies import get_session_factory
from agente_ia_edu.api.routes.diagnostic import get_current_diagnostic_identity
from agente_ia_edu.api.routes.domain_map import domain_map_router
from agente_ia_edu.services.domain_map import DomainMapService
from agente_ia_edu.services.learning_path import ContentMasteryService, LearningHistoryService
from agente_ia_edu.services.learning_path_policies import (
    ActivityType,
    DifficultyLevel,
    NextBestActionCandidate,
    NextBestActionPolicy,
)


class TestNextBestActionPolicy(unittest.TestCase):
    def setUp(self):
        self.policy = NextBestActionPolicy()

    @staticmethod
    def candidate(**overrides):
        values = {
            "content_node_id": "content-a",
            "content_name": "Equilibrio quimico",
            "mastery_score": 60.0,
            "confidence": 0.7,
            "evidence_count": 8,
        }
        values.update(overrides)
        return NextBestActionCandidate(**values)

    def test_01_high_mastery_advances(self):
        decision = self.policy.decide([
            self.candidate(mastery_score=92.0, confidence=0.8)
        ])
        self.assertEqual(decision.action, "ADVANCE_CONTENT")

    def test_02_low_mastery_practices(self):
        decision = self.policy.decide([
            self.candidate(mastery_score=25.0, confidence=0.7)
        ])
        self.assertEqual(decision.action, "PRACTICE_CONTENT")
        self.assertIn("LOW_MASTERY", decision.factors)

    def test_03_low_confidence_completes_evidence(self):
        decision = self.policy.decide([
            self.candidate(mastery_score=90.0, confidence=0.2, evidence_count=2)
        ])
        self.assertEqual(decision.action, "COMPLETE_MISSING_EVIDENCE")
        self.assertIn("LOW_CONFIDENCE", decision.factors)

    def test_04_content_without_evidence_is_not_zero_mastery(self):
        decision = self.policy.decide([
            self.candidate(mastery_score=None, confidence=0.0, evidence_count=0)
        ])
        self.assertEqual(decision.action, "COMPLETE_MISSING_EVIDENCE")
        self.assertIn("NOT_EVALUATED", decision.factors)

    def test_05_insufficient_prerequisite_evidence_is_completed_first(self):
        decision = self.policy.decide([
            self.candidate(
                prerequisite_node_id="prerequisite-a",
                prerequisite_name="Concentracao",
                prerequisite_evidence_count=1,
                prerequisite_confidence=0.1,
                prerequisite_hypothesis_status="SUPPORTED",
            )
        ])
        self.assertEqual(decision.action, "COMPLETE_MISSING_EVIDENCE")
        self.assertEqual(decision.target_content_node_id, "prerequisite-a")
        self.assertEqual(decision.related_content_node_id, "content-a")

    def test_06_mastered_prerequisite_does_not_block_target(self):
        decision = self.policy.decide([
            self.candidate(
                mastery_score=25.0,
                prerequisite_node_id="prerequisite-a",
                prerequisite_name="Concentracao",
                prerequisite_mastery_score=90.0,
                prerequisite_evidence_count=10,
                prerequisite_confidence=0.8,
                prerequisite_hypothesis_status="SUPPORTED",
            )
        ])
        self.assertEqual(decision.action, "PRACTICE_CONTENT")
        self.assertEqual(decision.target_content_node_id, "content-a")

    def test_12_decision_is_deterministic_for_same_input(self):
        candidates = [
            self.candidate(content_node_id="b", content_name="B"),
            self.candidate(content_node_id="a", content_name="A"),
        ]
        first = self.policy.decide(candidates)
        second = self.policy.decide(list(reversed(candidates)))
        self.assertEqual(first, second)
        self.assertEqual(first.target_content_node_id, "a")

    def test_13_recent_practice_reduces_priority(self):
        practiced = self.candidate(
            content_node_id="a", content_name="A", recent_practice_count=5
        )
        fresh = self.candidate(content_node_id="b", content_name="B")
        decision = self.policy.decide([practiced, fresh])
        self.assertEqual(decision.target_content_node_id, "b")

    def test_14_student_objective_breaks_equal_need_tie(self):
        neutral = self.candidate(content_node_id="a", content_name="A")
        objective = self.candidate(
            content_node_id="b", content_name="B", objective_aligned=True
        )
        decision = self.policy.decide([neutral, objective])
        self.assertEqual(decision.target_content_node_id, "b")
        self.assertIn("STUDENT_OBJECTIVE", decision.factors)

    def test_15_unknown_only_history_has_insufficient_trend(self):
        entries = [
            SimpleNamespace(response_text="UNKNOWN", is_correct=False)
            for _ in range(5)
        ]
        self.assertEqual(
            DomainMapService._trend(entries), ("INSUFFICIENT_EVIDENCE", None)
        )


class TestDomainMapIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def seed_catalog(self, session):
        root = CatalogNode(
            node_type="DISCIPLINE", name="Quimica", position=1, active=True
        )
        session.add(root)
        await session.flush()
        root.root_id = root.id
        area = CatalogNode(
            parent_id=root.id,
            root_id=root.id,
            node_type="AREA",
            name="Ciencias da Natureza",
            position=1,
            active=True,
        )
        session.add(area)
        await session.flush()
        need = CatalogNode(
            parent_id=area.id,
            root_id=root.id,
            node_type="CONTENT",
            code="NEED",
            name="Equilibrio quimico",
            position=1,
            active=True,
        )
        planned = CatalogNode(
            parent_id=area.id,
            root_id=root.id,
            node_type="CONTENT",
            code="PLAN",
            name="Estequiometria",
            position=2,
            active=True,
        )
        session.add_all([need, planned])
        await session.flush()
        taxonomy = Taxonomy(code=f"phase6-{uuid4()}", name="Phase 6", version="1")
        session.add(taxonomy)
        await session.flush()
        session.add_all([
            TaxonomyNode(
                id=need.id,
                taxonomy_id=taxonomy.id,
                code="NEED",
                name=need.name,
                node_type="skill",
            ),
            TaxonomyNode(
                id=planned.id,
                taxonomy_id=taxonomy.id,
                code="PLAN",
                name=planned.name,
                node_type="skill",
            ),
        ])
        await session.flush()
        return root, area, need, planned

    async def add_state(
        self,
        session,
        *,
        student_id,
        node,
        score,
        confidence,
        outcomes,
        activity_type="OFFICIAL_ASSESSMENT",
    ):
        session.add(StudentContentMastery(
            external_identity_id=student_id,
            content_node_id=node.id,
            mastery_score=score,
            confidence=confidence,
            questions_answered=len(outcomes),
            questions_correct=sum(outcome is True for outcome in outcomes),
            current_level="EASY",
            last_activity_at=self.now,
        ))
        for index, outcome in enumerate(outcomes):
            session.add(LearningHistory(
                external_identity_id=student_id,
                activity_type=activity_type,
                question_version_id=uuid4(),
                difficulty_level="EASY",
                is_correct=outcome,
                content_node_id=node.id,
                created_at=self.now - timedelta(days=len(outcomes) - index),
            ))
        await session.flush()

    def service(self, session):
        return DomainMapService(session, now_provider=lambda: self.now)

    async def test_07_practice_updates_mastery_and_keeps_evidence_origin(self):
        async with self.session_factory() as session:
            _, _, need, _ = await self.seed_catalog(session)
            mastery = await ContentMasteryService().get_or_create_mastery(
                session, "student-practice", need.id
            )
            await ContentMasteryService().update_mastery_after_response(
                session, mastery, True
            )
            await LearningHistoryService().record_history(
                session,
                "student-practice",
                ActivityType.INDIVIDUAL_PRACTICE,
                uuid4(),
                DifficultyLevel.EASY,
                is_correct=True,
                content_node_id=need.id,
            )
            session.add(LearningHistory(
                external_identity_id="student-practice",
                activity_type=ActivityType.INITIAL_DIAGNOSTIC.value,
                question_version_id=uuid4(),
                difficulty_level="EASY",
                is_correct=False,
                response_text="UNKNOWN",
                content_node_id=need.id,
                created_at=self.now,
            ))
            session.add(LearningHistory(
                external_identity_id="student-practice",
                activity_type=ActivityType.OFFICIAL_ASSESSMENT.value,
                question_version_id=uuid4(),
                difficulty_level="EASY",
                is_correct=True,
                content_node_id=need.id,
                created_at=self.now,
            ))
            await session.commit()
            payload = await self.service(session).build(
                student_id="student-practice",
                identity=ExternalIdentityContext(
                    provider="test", external_user_id="student-practice"
                ),
            )
            state = next(item for item in payload["contents"] if item["content_node_id"] == need.id)
            self.assertEqual(state["mastery_score"], 100.0)
            self.assertEqual(state["unknown_count"], 1)
            self.assertEqual(state["error_count"], 0)
            self.assertEqual(state["evidence_origins"]["INDIVIDUAL_PRACTICE"], 1)
            self.assertEqual(state["evidence_origins"]["INITIAL_DIAGNOSTIC"], 1)
            self.assertEqual(state["evidence_origins"]["OFFICIAL_ASSESSMENT"], 1)

    async def test_08_independent_student_ignores_school_context(self):
        async with self.session_factory() as session:
            _, _, need, _ = await self.seed_catalog(session)
            session.add(PedagogicalContext(
                content_node_id=need.id,
                source="TEACHER",
                institution_id="school-a",
                classroom_id="class-a",
                recorded_at=self.now,
                active=True,
            ))
            await session.commit()
            payload = await self.service(session).build(
                student_id="independent",
                identity=ExternalIdentityContext(
                    provider="test", external_user_id="independent"
                ),
            )
            self.assertTrue(payload["is_independent"])
            self.assertTrue(all(not item["pedagogical_contexts"] for item in payload["contents"]))

    async def test_09_school_student_is_filtered_by_authorized_universe(self):
        async with self.session_factory() as session:
            _, _, need, planned = await self.seed_catalog(session)
            universe = PedagogicalUniverse(
                external_id="phase6-universe",
                slug="phase6-universe",
                name="Universo Phase 6",
                status="ACTIVE",
                owner_type="SCHOOL",
                owner_external_id="school-a",
                configuration_version="v1",
            )
            session.add(universe)
            await session.flush()
            session.add_all([
                PedagogicalUniverseBinding(
                    universe_id=universe.id,
                    subject_type="SCHOOL",
                    subject_external_id="school-a",
                    active=True,
                ),
                PedagogicalUniverseCatalogScope(
                    universe_id=universe.id,
                    catalog_node_id=need.id,
                    scope_kind="CONTENT",
                    include_descendants=False,
                ),
            ])
            await session.commit()
            payload = await self.service(session).build(
                student_id="school-student",
                identity=ExternalIdentityContext(
                    provider="test",
                    external_user_id="school-student",
                    institution_id="school-a",
                    classroom_id="class-a",
                ),
            )
            self.assertFalse(payload["is_independent"])
            self.assertEqual(payload["universe"]["id"], universe.id)
            self.assertEqual(
                [item["content_node_id"] for item in payload["contents"]], [need.id]
            )
            self.assertNotIn(planned.id, [item["content_node_id"] for item in payload["contents"]])

    async def test_10_recent_teacher_content_receives_context_priority(self):
        async with self.session_factory() as session:
            _, _, need, planned = await self.seed_catalog(session)
            await self.add_state(
                session,
                student_id="school-student",
                node=need,
                score=55,
                confidence=0.6,
                outcomes=[True, False, True, False],
            )
            await self.add_state(
                session,
                student_id="school-student",
                node=planned,
                score=55,
                confidence=0.6,
                outcomes=[True, False, True, False],
            )
            session.add(PedagogicalContext(
                content_node_id=planned.id,
                source="TEACHER",
                institution_id="school-a",
                classroom_id="class-a",
                recorded_at=self.now,
                active=True,
            ))
            await session.commit()
            payload = await self.service(session).build(
                student_id="school-student",
                identity=ExternalIdentityContext(
                    provider="test",
                    external_user_id="school-student",
                    institution_id="school-a",
                    classroom_id="class-a",
                ),
            )
            self.assertEqual(payload["next_best_action"].target_content_node_id, str(planned.id))
            self.assertIn("PEDAGOGICAL_CONTEXT", payload["next_best_action"].factors)

    async def test_11_student_need_beats_school_plan_for_mastered_content(self):
        async with self.session_factory() as session:
            _, _, need, planned = await self.seed_catalog(session)
            await self.add_state(
                session,
                student_id="conflict-student",
                node=need,
                score=20,
                confidence=0.7,
                outcomes=[False, False, False, True],
            )
            await self.add_state(
                session,
                student_id="conflict-student",
                node=planned,
                score=95,
                confidence=0.8,
                outcomes=[True, True, True, True],
            )
            session.add(PedagogicalContext(
                content_node_id=planned.id,
                source="SCHOOL_PLAN",
                institution_id="school-a",
                classroom_id=None,
                recorded_at=self.now,
                active=True,
            ))
            await session.commit()
            payload = await self.service(session).build(
                student_id="conflict-student",
                identity=ExternalIdentityContext(
                    provider="test",
                    external_user_id="conflict-student",
                    institution_id="school-a",
                    classroom_id="class-a",
                ),
            )
            self.assertEqual(payload["next_best_action"].target_content_node_id, str(need.id))
            self.assertEqual(payload["next_best_action"].action, "PRACTICE_CONTENT")

    async def test_16_independent_student_respects_identity_universe(self):
        async with self.session_factory() as session:
            _, _, need, planned = await self.seed_catalog(session)
            universe = PedagogicalUniverse(
                external_id="independent-universe",
                slug="independent-universe",
                name="Universo Independente",
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
                    subject_external_id="independent-scoped",
                    active=True,
                ),
                PedagogicalUniverseCatalogScope(
                    universe_id=universe.id,
                    catalog_node_id=planned.id,
                    scope_kind="CONTENT",
                    include_descendants=False,
                ),
            ])
            await session.commit()
            payload = await self.service(session).build(
                student_id="independent-scoped",
                identity=ExternalIdentityContext(
                    provider="test", external_user_id="independent-scoped"
                ),
            )
            self.assertTrue(payload["is_independent"])
            self.assertEqual(
                [item["content_node_id"] for item in payload["contents"]],
                [planned.id],
            )
            self.assertNotIn(need.id, [item["content_node_id"] for item in payload["contents"]])


class TestDomainMapHTTP(unittest.TestCase):
    def setUp(self):
        async def setup_database():
            engine = create_async_engine(
                "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            async with session_factory() as session:
                root = CatalogNode(
                    node_type="DISCIPLINE", name="Matematica", position=1, active=True
                )
                session.add(root)
                await session.flush()
                root.root_id = root.id
                content = CatalogNode(
                    parent_id=root.id,
                    root_id=root.id,
                    node_type="CONTENT",
                    code="EQ",
                    name="Equacoes",
                    position=1,
                    active=True,
                )
                session.add(content)
                await session.commit()
            return engine, session_factory, content.id

        self.engine, self.session_factory, self.content_id = asyncio.run(setup_database())
        app = FastAPI()
        app.include_router(domain_map_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_diagnostic_identity] = lambda: ExternalIdentityContext(
            provider="test", external_user_id="http-student", roles=("student",)
        )
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def test_http_domain_map_preserves_not_evaluated_semantics(self):
        response = self.client.get("/api/v1/student/domain-map")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["is_independent"])
        self.assertEqual(payload["contents"][0]["content_node_id"], str(self.content_id))
        self.assertIsNone(payload["contents"][0]["mastery_score"])
        self.assertEqual(payload["contents"][0]["state"], "NOT_EVALUATED")
        self.assertEqual(
            payload["next_best_action"]["action"], "COMPLETE_MISSING_EVIDENCE"
        )


if __name__ == "__main__":
    unittest.main()
