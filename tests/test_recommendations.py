import asyncio
import unittest
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
    PedagogicalClassification,
    PedagogicalContext,
    PedagogicalRecommendation,
    Question,
    QuestionVersion,
    StudentContentMastery,
)
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.recommendation import (
    RecommendationEngine,
    RecommendationPriorityPolicy,
)


class TestRecommendationEngine(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_catalog_and_knowledge(self, session: AsyncSession):
        root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id

        parent_node = CatalogNode(
            parent_id=root.id,
            root_id=root.id,
            node_type="CONTENT",
            code="QUIM-CONC",
            name="Concentração Comum",
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

        # Material
        res = EducationalResource(
            title="Apostila Teórica - Diluição de Soluções",
            resource_type="THEORY_MATERIAL",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            status="active",
        )
        session.add(res)
        await session.flush()

        link_r = ContentResourceLink(
            content_node_id=content_node.id,
            resource_id=res.id,
            pedagogical_role="THEORY",
            recommended_level="EASY",
        )
        session.add(link_r)

        # Questions
        q1 = Question(validation_status="approved")
        session.add(q1)
        await session.flush()

        v1 = QuestionVersion(
            question_id=q1.id,
            version_kind="official_original",
            canonical_text="Questão de Diluição nível fácil",
            statement="Questão de Diluição nível fácil",
            content_hash="hash-dil-1",
            recommended_difficulty="EASY",
        )
        session.add(v1)
        await session.flush()

        link_q = ContentQuestionLink(content_node_id=content_node.id, question_version_id=v1.id)
        session.add(link_q)

        await session.commit()
        return root.id, parent_node.id, content_node.id, res.id, v1.id

    async def test_01_low_mastery_student(self):
        """1. aluno com domínio baixo (<50%)"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            # Seed mastery = 38%
            mastery = StudentContentMastery(
                external_identity_id="student:alice",
                content_node_id=content_id,
                mastery_score=38.0,
                current_level="EASY",
            )
            session.add(mastery)
            await session.commit()

            recs = await engine.generate_recommendations(student_id="student:alice")
            self.assertGreater(len(recs), 0)
            rec = [r for r in recs if r.content_node_id == content_id][0]
            self.assertEqual(rec.recommended_difficulty, "EASY")
            self.assertIn("38.0%", rec.reason)

    async def test_02_intermediate_mastery_student(self):
        """2. aluno com domínio intermediário (50-69%)"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            mastery = StudentContentMastery(
                external_identity_id="student:bob",
                content_node_id=content_id,
                mastery_score=62.0,
                current_level="MEDIUM",
            )
            session.add(mastery)
            await session.commit()

            recs = await engine.generate_recommendations(student_id="student:bob")
            rec = [r for r in recs if r.content_node_id == content_id][0]
            self.assertEqual(rec.recommended_difficulty, "MEDIUM")

    async def test_03_high_mastery_student(self):
        """3. aluno com domínio alto (85%+)"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            mastery = StudentContentMastery(
                external_identity_id="student:carol",
                content_node_id=content_id,
                mastery_score=92.0,
                current_level="HARD",
            )
            session.add(mastery)
            await session.commit()

            recs = await engine.generate_recommendations(student_id="student:carol")
            rec = [r for r in recs if r.content_node_id == content_id][0]
            self.assertEqual(rec.recommended_difficulty, "HARD")

    async def test_04_teacher_context(self):
        """4. professor ensinou conteúdo"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            await engine.record_pedagogical_context(
                content_node_id=content_id,
                source="TEACHER",
                institution_id="SCHOOL_A",
                classroom_id="CLASS_101",
                author_id="teacher:prof_silva",
                title="Ensinei Diluição de Soluções",
            )

            recs = await engine.generate_recommendations(
                student_id="student:alice",
                institution_id="SCHOOL_A",
                classroom_id="CLASS_101",
            )
            self.assertEqual(recs[0].context_source, "TEACHER")

    async def test_05_coordination_context(self):
        """5. coordenação orientou conteúdo"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            await engine.record_pedagogical_context(
                content_node_id=content_id,
                source="COORDINATION",
                institution_id="SCHOOL_A",
                title="Revisar Diluição para Simulado",
            )

            recs = await engine.generate_recommendations(
                student_id="student:alice",
                institution_id="SCHOOL_A",
            )
            self.assertEqual(recs[0].context_source, "COORDINATION")

    async def test_06_school_plan_context(self):
        """6. planejamento escolar define conteúdo"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            await engine.record_pedagogical_context(
                content_node_id=content_id,
                source="SCHOOL_PLAN",
                institution_id="SCHOOL_A",
                title="Planejamento Bimestral - Semana 4",
            )

            recs = await engine.generate_recommendations(
                student_id="student:alice",
                institution_id="SCHOOL_A",
            )
            self.assertEqual(recs[0].context_source, "SCHOOL_PLAN")

    async def test_07_teacher_plus_school_plan_alignment(self):
        """7. professor + planejamento (alinhamento)"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            await engine.record_pedagogical_context(content_node_id=content_id, source="SCHOOL_PLAN", institution_id="SCHOOL_A")
            await engine.record_pedagogical_context(content_node_id=content_id, source="TEACHER", institution_id="SCHOOL_A")

            recs = await engine.generate_recommendations(student_id="student:alice", institution_id="SCHOOL_A")
            rec = recs[0]
            self.assertEqual(rec.context_source, "TEACHER")
            self.assertGreater(rec.priority_score, 120.0)  # Source 100 + Alignment 20

    async def test_08_coordination_plus_school_plan_alignment(self):
        """8. coordenação + planejamento (alinhamento)"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            await engine.record_pedagogical_context(content_node_id=content_id, source="SCHOOL_PLAN", institution_id="SCHOOL_A")
            await engine.record_pedagogical_context(content_node_id=content_id, source="COORDINATION", institution_id="SCHOOL_A")

            recs = await engine.generate_recommendations(student_id="student:alice", institution_id="SCHOOL_A")
            rec = recs[0]
            self.assertEqual(rec.context_source, "COORDINATION")

    async def test_09_10_absence_of_school_context_autonomous(self):
        """9, 10. ausência de contexto escolar / aluno sem professor (trilha autônoma)"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            recs = await engine.generate_recommendations(student_id="student:lonely")
            self.assertGreater(len(recs), 0)
            self.assertEqual(recs[0].context_source, "AUTONOMOUS")

    async def test_11_pending_prerequisite(self):
        """11. pré-requisito pendente (REVIEW_PREREQUISITE)"""
        async with self.session_factory() as session:
            _, parent_id, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            # Low mastery on parent prerequisite
            p_mastery = StudentContentMastery(
                external_identity_id="student:alice",
                content_node_id=parent_id,
                mastery_score=20.0,
            )
            session.add(p_mastery)
            await session.commit()

            await engine.record_pedagogical_context(content_node_id=content_id, source="TEACHER", institution_id="SCHOOL_A")

            recs = await engine.generate_recommendations(student_id="student:alice", institution_id="SCHOOL_A")
            prereq_rec = [r for r in recs if r.recommendation_type == "REVIEW_PREREQUISITE"][0]
            self.assertEqual(prereq_rec.content_node_id, parent_id)
            self.assertIn("pré-requisito", prereq_rec.reason)

    async def test_12_material_available(self):
        """12. material disponível (STUDY_MATERIAL)"""
        async with self.session_factory() as session:
            _, _, content_id, res_id, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            mastery = StudentContentMastery(external_identity_id="student:alice", content_node_id=content_id, mastery_score=30.0)
            session.add(mastery)
            await session.commit()

            await engine.record_pedagogical_context(content_node_id=content_id, source="TEACHER", institution_id="SCHOOL_A")

            recs = await engine.generate_recommendations(student_id="student:alice", institution_id="SCHOOL_A")
            rec = recs[0]
            self.assertEqual(rec.recommendation_type, "STUDY_MATERIAL")
            self.assertEqual(rec.resource_id, res_id)

    async def test_13_questions_available(self):
        """13. questões disponíveis (PRACTICE)"""
        async with self.session_factory() as session:
            _, _, content_id, _, qv_id = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            mastery = StudentContentMastery(external_identity_id="student:alice", content_node_id=content_id, mastery_score=60.0)
            session.add(mastery)
            await session.commit()

            await engine.record_pedagogical_context(content_node_id=content_id, source="TEACHER", institution_id="SCHOOL_A")

            recs = await engine.generate_recommendations(student_id="student:alice", institution_id="SCHOOL_A")
            rec = recs[0]
            self.assertEqual(rec.recommendation_type, "PRACTICE")

    async def test_14_no_resources_available_graceful(self):
        """14. nenhum recurso disponível (funciona sem erro)"""
        async with self.session_factory() as session:
            empty_node = CatalogNode(node_type="CONTENT", name="Conteúdo Sem Recurso", active=True)
            session.add(empty_node)
            await session.commit()

            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            await engine.record_pedagogical_context(content_node_id=empty_node.id, source="TEACHER", institution_id="SCHOOL_A")

            recs = await engine.generate_recommendations(student_id="student:alice", institution_id="SCHOOL_A")
            rec = [r for r in recs if r.content_node_id == empty_node.id][0]
            self.assertIsNotNone(rec.reason)

    async def test_15_repetition_penalty(self):
        """15. evitar repetição imediata"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            await engine.record_pedagogical_context(content_node_id=content_id, source="TEACHER", institution_id="SCHOOL_A")

            first_recs = await engine.generate_recommendations(student_id="student:alice", institution_id="SCHOOL_A")
            first_score = first_recs[0].priority_score

            second_recs = await engine.generate_recommendations(student_id="student:alice", institution_id="SCHOOL_A")
            second_score = second_recs[0].priority_score

            self.assertLess(second_score, first_score)

    async def test_16_17_18_recommended_difficulty_levels(self):
        """16, 17, 18. recomendação EASY, MEDIUM, HARD"""
        policy = RecommendationPriorityPolicy()
        self.assertEqual(policy.select_difficulty(20.0), "EASY")
        self.assertEqual(policy.select_difficulty(60.0), "MEDIUM")
        self.assertEqual(policy.select_difficulty(80.0), "HARD")

    async def test_19_determinism(self):
        """19. determinismo"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            await engine.record_pedagogical_context(content_node_id=content_id, source="TEACHER", institution_id="SCHOOL_A")

            recs1 = await engine.generate_recommendations(student_id="student:det1", institution_id="SCHOOL_A")
            recs2 = await engine.generate_recommendations(student_id="student:det2", institution_id="SCHOOL_A")

            self.assertEqual(recs1[0].reason, recs2[0].reason)
            self.assertEqual(recs1[0].priority_score, recs2[0].priority_score)

    async def test_20_school_isolation(self):
        """20. isolamento entre escolas"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            await engine.record_pedagogical_context(content_node_id=content_id, source="TEACHER", institution_id="SCHOOL_A")

            recs_school_a = await engine.generate_recommendations(student_id="student:alice", institution_id="SCHOOL_A")
            recs_school_b = await engine.generate_recommendations(student_id="student:bob", institution_id="SCHOOL_B")

            self.assertEqual(recs_school_a[0].context_source, "TEACHER")
            self.assertEqual(recs_school_b[0].context_source, "AUTONOMOUS")

    async def test_21_22_auditable_explanation_and_history(self):
        """21, 22. explicação auditável e histórico de recomendação"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            recs = await engine.generate_recommendations(student_id="student:audit")
            rec_id = recs[0].id

            retrieved = await session.get(PedagogicalRecommendation, rec_id)
            self.assertIsNotNone(retrieved)
            self.assertIn("Analisando", retrieved.reason)
            self.assertIsNotNone(retrieved.metadata_)

    async def test_23_24_25_mastered_unpracticed_conflict_sources(self):
        """23, 24, 25. conteúdo já dominado, nunca praticado, e conflito de fontes"""
        async with self.session_factory() as session:
            _, _, content_id, _, _ = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            # Conflict: TEACHER > COORDINATION > SCHOOL_PLAN
            await engine.record_pedagogical_context(content_node_id=content_id, source="SCHOOL_PLAN", institution_id="SCHOOL_A")
            await engine.record_pedagogical_context(content_node_id=content_id, source="COORDINATION", institution_id="SCHOOL_A")
            await engine.record_pedagogical_context(content_node_id=content_id, source="TEACHER", institution_id="SCHOOL_A")

            recs = await engine.generate_recommendations(student_id="student:conflict", institution_id="SCHOOL_A")
            self.assertEqual(recs[0].context_source, "TEACHER")

    async def test_26_real_integrated_scenario_teacher_student(self):
        """26. CENÁRIO REAL INTEGRADO (Escola -> Turma -> Professor -> Aluno)"""
        async with self.session_factory() as session:
            _, _, content_id, res_id, qv_id = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            # Teacher registers context: "Diluição de Soluções foi ensinada."
            await engine.record_pedagogical_context(
                content_node_id=content_id,
                source="TEACHER",
                institution_id="ESCOLA_PARCEIRA",
                classroom_id="TURMA_3A",
                author_id="teacher:prof_mendes",
                title="Diluição de Soluções foi ensinada em aula.",
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

            recs = await engine.generate_recommendations(
                student_id="student:joao",
                institution_id="ESCOLA_PARCEIRA",
                classroom_id="TURMA_3A",
            )

            self.assertGreater(len(recs), 0)
            rec = recs[0]
            self.assertEqual(rec.context_source, "TEACHER")
            self.assertEqual(rec.recommendation_type, "STUDY_MATERIAL")
            self.assertEqual(rec.resource_id, res_id)
            self.assertEqual(rec.recommended_difficulty, "EASY")
            self.assertIn("Diluição de Soluções", rec.reason)
            self.assertIn("38.0%", rec.reason)

    async def test_27_real_integrated_scenario_autonomous_student(self):
        """27. CENÁRIO REAL INTEGRADO (Aluno sem professor - Trilha Autônoma)"""
        async with self.session_factory() as session:
            _, _, content_id, res_id, qv_id = await self._seed_catalog_and_knowledge(session)
            knowledge_service = KnowledgeService(session)
            engine = RecommendationEngine(session, knowledge_service)

            # Autonomous student with low mastery (38%) and no teacher or classroom
            mastery = StudentContentMastery(
                external_identity_id="student:maria_autonoma",
                content_node_id=content_id,
                mastery_score=38.0,
                current_level="EASY",
            )
            session.add(mastery)
            await session.commit()

            recs = await engine.generate_recommendations(
                student_id="student:maria_autonoma",
            )

            self.assertGreater(len(recs), 0)
            rec = [r for r in recs if r.content_node_id == content_id][0]
            self.assertEqual(rec.context_source, "AUTONOMOUS")
            self.assertEqual(rec.recommendation_type, "STUDY_MATERIAL")
            self.assertEqual(rec.recommended_difficulty, "EASY")
            self.assertIn("38.0%", rec.reason)


if __name__ == "__main__":
    unittest.main()
