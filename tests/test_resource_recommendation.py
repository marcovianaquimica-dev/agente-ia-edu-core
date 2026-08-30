import asyncio
import unittest
import uuid
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    ContentQuestionLink,
    ContentResourceLink,
    EducationalResource,
    LearningHistory,
    PedagogicalContext,
    PedagogicalRecommendation,
    Question,
    QuestionVersion,
    StudentContentMastery,
    VideoResourceDetail,
)
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.recommendation import (
    RecommendationEngine,
    RecommendationPriorityPolicy,
    ResourceRecommendationPolicy,
    ResourceTrackingService,
)


class TestResourceRecommendationLayer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=False,
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_hierarchy_and_resources(self, session: AsyncSession):
        root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id

        parent_node = CatalogNode(
            parent_id=root.id,
            root_id=root.id,
            node_type="CONTENT",
            code="QUIM-SOL",
            name="Soluções Químicas",
            position=1,
            active=True,
        )
        session.add(parent_node)
        await session.flush()

        content_node = CatalogNode(
            parent_id=parent_node.id,
            root_id=root.id,
            node_type="SUBCONTENT",
            code="QUIM-DIL",
            name="Diluição de Soluções",
            position=2,
            active=True,
        )
        session.add(content_node)
        await session.flush()

        # 1. Direct Material for Subcontent (Public Platform)
        res_direct = EducationalResource(
            title="Apostila Teórica - Diluição de Soluções",
            description="Material focado em diluição",
            resource_type="THEORY_MATERIAL",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            status="active",
        )
        session.add(res_direct)
        await session.flush()

        link_direct = ContentResourceLink(
            content_node_id=content_node.id,
            resource_id=res_direct.id,
            pedagogical_role="THEORY",
            recommended_level="EASY",
        )
        session.add(link_direct)

        # 2. Material for Parent Content (School A Private)
        res_school = EducationalResource(
            title="Apostila Exclusiva Escola A - Soluções",
            description="Material do professor da Escola A",
            resource_type="THEORY_MATERIAL",
            origin_type="SCHOOL",
            owner_external_id="SCHOOL_A",
            visibility_scope="PRIVATE",
            status="active",
        )
        session.add(res_school)
        await session.flush()

        link_school = ContentResourceLink(
            content_node_id=parent_node.id,
            resource_id=res_school.id,
            pedagogical_role="THEORY",
            recommended_level="MEDIUM",
        )
        session.add(link_school)

        # 3. Video Resource
        res_video = EducationalResource(
            title="Videoaula: Como Funciona a Diluição",
            resource_type="VIDEO",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            source_url="https://youtube.com/watch?v=dil123",
            status="active",
        )
        session.add(res_video)
        await session.flush()

        video_detail = VideoResourceDetail(
            resource_id=res_video.id,
            platform="YOUTUBE",
            external_video_id="dil123",
            duration_seconds=420,
        )
        session.add(video_detail)

        link_video = ContentResourceLink(
            content_node_id=content_node.id,
            resource_id=res_video.id,
            pedagogical_role="VIDEO",
            recommended_level="EASY",
        )
        session.add(link_video)

        # 4. Questions (EASY & HARD)
        q1 = Question(validation_status="approved")
        q2 = Question(validation_status="approved")
        session.add_all([q1, q2])
        await session.flush()

        v1 = QuestionVersion(
            question_id=q1.id,
            version_kind="official_original",
            canonical_text="Questão fácil sobre Diluição",
            statement="Questão fácil sobre Diluição",
            content_hash="h-dil-easy",
            recommended_difficulty="EASY",
        )
        v2 = QuestionVersion(
            question_id=q2.id,
            version_kind="official_original",
            canonical_text="Questão difícil sobre Diluição",
            statement="Questão difícil sobre Diluição",
            content_hash="h-dil-hard",
            recommended_difficulty="HARD",
        )
        session.add_all([v1, v2])
        await session.flush()

        link_q1 = ContentQuestionLink(content_node_id=content_node.id, question_version_id=v1.id)
        link_q2 = ContentQuestionLink(content_node_id=content_node.id, question_version_id=v2.id)
        session.add_all([link_q1, link_q2])

        await session.commit()
        return root.id, parent_node.id, content_node.id, res_direct.id, res_school.id, v1.id, v2.id

    async def test_01_recommendation_with_material_available(self):
        """1. recomendação com material disponível"""
        async with self.session_factory() as session:
            _, _, content_id, _, _, _, _ = await self._seed_hierarchy_and_resources(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            recs = await engine.generate_and_resolve_recommendations(student_id="student:alice")
            self.assertGreater(len(recs), 0)
            rec = [r for r in recs if r["content_node_id"] == str(content_id)][0]
            self.assertTrue(rec["has_material"])
            self.assertIsNotNone(rec["primary_resource"])
            self.assertEqual(rec["status"], "OK")

    async def test_02_material_directly_related_to_content(self):
        """2. material diretamente relacionado ao conteúdo"""
        async with self.session_factory() as session:
            _, _, content_id, res_direct_id, _, _, _ = await self._seed_hierarchy_and_resources(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            recs = await engine.generate_and_resolve_recommendations(student_id="student:alice")
            direct_rec = [r for r in recs if r["content_node_id"] == str(content_id)][0]
            self.assertEqual(direct_rec["primary_resource"]["resource_id"], str(res_direct_id))

    async def test_03_material_related_to_subcontent_or_parent(self):
        """3. material relacionado ao subconteúdo/parent"""
        async with self.session_factory() as session:
            _, parent_id, _, _, res_school_id, _, _ = await self._seed_hierarchy_and_resources(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            recs = await engine.generate_and_resolve_recommendations(
                student_id="student:alice",
                institution_id="SCHOOL_A",
            )
            parent_rec = [r for r in recs if r["content_node_id"] == str(parent_id)][0]
            self.assertEqual(parent_rec["primary_resource"]["resource_id"], str(res_school_id))

    async def test_04_05_06_institutional_global_and_private_material_isolation(self):
        """4, 5, 6. material institucional priorizado, global autorizado e isolamento privado"""
        async with self.session_factory() as session:
            _, parent_id, _, _, res_school_id, _, _ = await self._seed_hierarchy_and_resources(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            # School A student sees private material
            recs_a = await engine.generate_and_resolve_recommendations(student_id="student:a", institution_id="SCHOOL_A")
            parent_rec_a = [r for r in recs_a if r["content_node_id"] == str(parent_id)][0]
            self.assertEqual(parent_rec_a["primary_resource"]["resource_id"], str(res_school_id))

            # School B student does not see School A private material
            recs_b = await engine.generate_and_resolve_recommendations(student_id="student:b", institution_id="SCHOOL_B")
            parent_rec_b = [r for r in recs_b if r["content_node_id"] == str(parent_id)][0]
            self.assertFalse(parent_rec_b["has_material"])
            self.assertIsNone(parent_rec_b["primary_resource"])

    async def test_07_08_practice_with_questions_and_reuse_selection(self):
        """7, 8. prática com questões e reutilização de histórico recente"""
        async with self.session_factory() as session:
            _, _, content_id, _, _, v1_id, _ = await self._seed_hierarchy_and_resources(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            # Seed history showing v1 was answered
            history = LearningHistory(
                external_identity_id="student:alice",
                activity_type="INDIVIDUAL_PRACTICE",
                question_version_id=v1_id,
                difficulty_level="EASY",
                content_node_id=content_id,
            )
            session.add(history)
            await session.commit()

            recs = await engine.generate_and_resolve_recommendations(student_id="student:alice")
            rec = [r for r in recs if r["content_node_id"] == str(content_id)][0]
            self.assertTrue(rec["has_questions"])
            self.assertGreater(len(rec["practice_questions"]), 0)

    async def test_09_10_11_difficulty_levels(self):
        """9, 10, 11. dificuldade EASY, MEDIUM, HARD"""
        async with self.session_factory() as session:
            _, _, content_id, _, _, v1_id, v2_id = await self._seed_hierarchy_and_resources(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            # Low mastery -> EASY difficulty
            mastery_low = StudentContentMastery(external_identity_id="student:low", content_node_id=content_id, mastery_score=30.0)
            # High mastery -> HARD difficulty
            mastery_high = StudentContentMastery(external_identity_id="student:high", content_node_id=content_id, mastery_score=88.0)
            session.add_all([mastery_low, mastery_high])
            await session.commit()

            recs_low = await engine.generate_and_resolve_recommendations(student_id="student:low")
            rec_low = [r for r in recs_low if r["content_node_id"] == str(content_id)][0]
            self.assertEqual(rec_low["recommended_difficulty"], "EASY")

            recs_high = await engine.generate_and_resolve_recommendations(student_id="student:high")
            rec_high = [r for r in recs_high if r["content_node_id"] == str(content_id)][0]
            self.assertEqual(rec_high["recommended_difficulty"], "HARD")

    async def test_12_material_without_questions(self):
        """12. material sem questões (graceful, status OK)"""
        async with self.session_factory() as session:
            no_q_node = CatalogNode(node_type="CONTENT", name="Sistemas Coloidais", active=True)
            session.add(no_q_node)
            await session.flush()

            res = EducationalResource(title="Apostila Coloides", resource_type="THEORY_MATERIAL", origin_type="PLATFORM", visibility_scope="PUBLIC", status="active")
            session.add(res)
            await session.flush()

            link = ContentResourceLink(content_node_id=no_q_node.id, resource_id=res.id, pedagogical_role="THEORY")
            session.add(link)
            await session.commit()

            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            await engine.record_pedagogical_context(content_node_id=no_q_node.id, source="TEACHER")

            recs = await engine.generate_and_resolve_recommendations(student_id="student:no_q")
            rec = [r for r in recs if r["content_node_id"] == str(no_q_node.id)][0]
            self.assertTrue(rec["has_material"])
            self.assertFalse(rec["has_questions"])
            self.assertEqual(rec["status"], "OK")

    async def test_13_questions_without_material(self):
        """13. questões sem material (graceful, status OK)"""
        async with self.session_factory() as session:
            no_mat_node = CatalogNode(node_type="CONTENT", name="Leis Ponderais", active=True)
            session.add(no_mat_node)
            await session.flush()

            q = Question(validation_status="approved")
            session.add(q)
            await session.flush()

            v = QuestionVersion(question_id=q.id, version_kind="official_original", canonical_text="Questão Leis Ponderais", statement="Questão Leis Ponderais", content_hash="h-leis", recommended_difficulty="EASY")
            session.add(v)
            await session.flush()

            link_q = ContentQuestionLink(content_node_id=no_mat_node.id, question_version_id=v.id)
            session.add(link_q)
            await session.commit()

            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            await engine.record_pedagogical_context(content_node_id=no_mat_node.id, source="TEACHER")

            recs = await engine.generate_and_resolve_recommendations(student_id="student:no_mat")
            rec = [r for r in recs if r["content_node_id"] == str(no_mat_node.id)][0]
            self.assertFalse(rec["has_material"])
            self.assertTrue(rec["has_questions"])
            self.assertEqual(rec["status"], "OK")

    async def test_14_no_resources_available(self):
        """14. nenhum recurso disponível (NO_RESOURCE_AVAILABLE)"""
        async with self.session_factory() as session:
            empty_node = CatalogNode(node_type="CONTENT", name="Termoquímica Avançada", active=True)
            session.add(empty_node)
            await session.commit()

            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            await engine.record_pedagogical_context(content_node_id=empty_node.id, source="TEACHER")

            recs = await engine.generate_and_resolve_recommendations(student_id="student:empty")
            rec = [r for r in recs if r["content_node_id"] == str(empty_node.id)][0]
            self.assertFalse(rec["has_material"])
            self.assertFalse(rec["has_questions"])
            self.assertEqual(rec["status"], "NO_RESOURCE_AVAILABLE")
            self.assertIn("Nenhum recurso pedagógico", rec["reason"])

    async def test_15_avoid_immediate_repetition(self):
        """15. evitar repetição imediata de recursos"""
        policy = ResourceRecommendationPolicy()
        res_list = [
            {"resource_id": str(uuid4()), "content_node_id": "1", "origin_type": "PLATFORM"},
            {"resource_id": str(uuid4()), "content_node_id": "1", "origin_type": "PLATFORM"},
        ]
        recent = {uuid.UUID(res_list[0]["resource_id"])}

        ranked = policy.rank_resources(res_list, recent_resource_ids=recent)
        self.assertEqual(ranked[0]["resource_id"], res_list[1]["resource_id"])

    async def test_16_determinism(self):
        """16. determinismo na resolução de recursos"""
        async with self.session_factory() as session:
            _, _, content_id, _, _, _, _ = await self._seed_hierarchy_and_resources(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            recs1 = await engine.generate_and_resolve_recommendations(student_id="student:det1")
            recs2 = await engine.generate_and_resolve_recommendations(student_id="student:det2")

            self.assertEqual(recs1[0]["primary_resource"], recs2[0]["primary_resource"])
            self.assertEqual(recs1[0]["priority_score"], recs2[0]["priority_score"])

    async def test_17_school_isolation(self):
        """17. isolamento entre escolas"""
        async with self.session_factory() as session:
            _, parent_id, _, _, res_school_id, _, _ = await self._seed_hierarchy_and_resources(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            recs_a = await engine.generate_and_resolve_recommendations(student_id="student:a", institution_id="SCHOOL_A")
            recs_b = await engine.generate_and_resolve_recommendations(student_id="student:b", institution_id="SCHOOL_B")

            parent_a = [r for r in recs_a if r["content_node_id"] == str(parent_id)][0]
            parent_b = [r for r in recs_b if r["content_node_id"] == str(parent_id)][0]

            self.assertTrue(parent_a["has_material"])
            self.assertFalse(parent_b["has_material"])

    async def test_18_author_material_scoring(self):
        """18. material autoral priorizado pela política"""
        policy = ResourceRecommendationPolicy()
        res_list = [
            {"resource_id": str(uuid4()), "origin_type": "PLATFORM"},
            {"resource_id": str(uuid4()), "origin_type": "AUTHOR"},
        ]
        ranked = policy.rank_resources(res_list)
        self.assertEqual(ranked[0]["origin_type"], "AUTHOR")

    async def test_19_multiple_resources_bundled(self):
        """19. múltiplos recursos (material principal + questões)"""
        async with self.session_factory() as session:
            _, _, content_id, _, _, _, _ = await self._seed_hierarchy_and_resources(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            recs = await engine.generate_and_resolve_recommendations(student_id="student:bundle")
            rec = [r for r in recs if r["content_node_id"] == str(content_id)][0]

            self.assertTrue(rec["has_material"])
            self.assertTrue(rec["has_questions"])
            self.assertIsNotNone(rec["primary_resource"])
            self.assertGreater(len(rec["practice_questions"]), 0)

    async def test_20_preservation_of_original_recommendation(self):
        """20. preservação da recomendação original"""
        async with self.session_factory() as session:
            _, _, content_id, _, _, _, _ = await self._seed_hierarchy_and_resources(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            await engine.record_pedagogical_context(content_node_id=content_id, source="TEACHER")

            recs = await engine.generate_and_resolve_recommendations(student_id="student:pres")
            rec = [r for r in recs if r["content_node_id"] == str(content_id)][0]

            self.assertEqual(rec["context_source"], "TEACHER")
            self.assertIn("Você estudou Diluição de Soluções", rec["reason"])
            self.assertIsNotNone(rec["recommendation_id"])

    async def test_21_integrated_real_scenario(self):
        """21. CENÁRIO REAL INTEGRADO (Escola -> Turma -> Professor -> Aluno -> Recursos)"""
        async with self.session_factory() as session:
            _, _, content_id, res_direct_id, _, v1_id, _ = await self._seed_hierarchy_and_resources(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            # Teacher registers context: "Diluição de Soluções foi ensinada."
            await engine.record_pedagogical_context(
                content_node_id=content_id,
                source="TEACHER",
                institution_id="ESCOLA_PILOTO",
                classroom_id="TURMA_3A",
                author_id="teacher:prof_mendes",
                title="Diluição de Soluções ensinada.",
            )

            # Student in TURMA_3A with low mastery (38%)
            mastery = StudentContentMastery(
                external_identity_id="student:joao",
                content_node_id=content_id,
                mastery_score=38.0,
                current_level="EASY",
            )
            session.add(mastery)
            await session.commit()

            recs = await engine.generate_and_resolve_recommendations(
                student_id="student:joao",
                institution_id="ESCOLA_PILOTO",
                classroom_id="TURMA_3A",
            )

            rec = [r for r in recs if r["content_node_id"] == str(content_id)][0]
            self.assertEqual(rec["context_source"], "TEACHER")
            self.assertEqual(rec["recommendation_type"], "STUDY_MATERIAL")
            self.assertEqual(rec["recommended_difficulty"], "EASY")
            self.assertEqual(rec["primary_resource"]["resource_id"], str(res_direct_id))
            self.assertTrue(rec["has_questions"])
            self.assertIn("38.0%", rec["reason"])

    async def test_22_autonomous_real_scenario(self):
        """22. CENÁRIO REAL INTEGRADO (Aluno Autônomo sem Professor -> Recursos)"""
        async with self.session_factory() as session:
            _, _, content_id, res_direct_id, _, v1_id, _ = await self._seed_hierarchy_and_resources(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            # Autonomous student with low mastery (38%)
            mastery = StudentContentMastery(
                external_identity_id="student:maria_autonoma",
                content_node_id=content_id,
                mastery_score=38.0,
                current_level="EASY",
            )
            session.add(mastery)
            await session.commit()

            recs = await engine.generate_and_resolve_recommendations(
                student_id="student:maria_autonoma",
            )

            rec = [r for r in recs if r["content_node_id"] == str(content_id)][0]
            self.assertEqual(rec["context_source"], "AUTONOMOUS")
            self.assertEqual(rec["recommendation_type"], "STUDY_MATERIAL")
            self.assertEqual(rec["recommended_difficulty"], "EASY")
            self.assertEqual(rec["primary_resource"]["resource_id"], str(res_direct_id))
            self.assertTrue(rec["has_questions"])

    async def test_23_telemetry_tracking_contract(self):
        """23. contrato de rastreamento de interação com recursos (telemetria futura)"""
        async with self.session_factory() as session:
            _, _, content_id, res_direct_id, _, _, _ = await self._seed_hierarchy_and_resources(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            recs = await engine.generate_recommendations(student_id="student:tel")
            rec = recs[0]

            tracking_service = ResourceTrackingService(session)
            event = await tracking_service.record_interaction(
                student_id="student:tel",
                resource_id=res_direct_id,
                action_type="STARTED",
                recommendation_id=rec.id,
                progress_percentage=10.0,
            )

            self.assertTrue(event["recorded"])
            self.assertEqual(event["action_type"], "STARTED")

            rec_refreshed = await session.get(PedagogicalRecommendation, rec.id)
            self.assertIsNotNone(rec_refreshed.metadata_)
            self.assertIn("interactions", rec_refreshed.metadata_)


if __name__ == "__main__":
    unittest.main()
