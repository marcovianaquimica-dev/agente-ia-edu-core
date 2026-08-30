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
    Question,
    QuestionVersion,
    ResourceAccessGrant,
    VideoResourceDetail,
)
from agente_ia_edu.services.knowledge import KnowledgeService


class TestKnowledgeLayer(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_data(self, session: AsyncSession):
        # 1. Create Catalog Node (Content)
        root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id

        content_node = CatalogNode(
            parent_id=root.id,
            root_id=root.id,
            node_type="CONTENT",
            code="QUIM-DIL",
            name="Diluição de Soluções",
            position=1,
            active=True,
        )
        session.add(content_node)
        await session.flush()

        # 2. Create Questions & Classifications
        q1 = Question(validation_status="approved")
        q2 = Question(validation_status="approved")
        session.add_all([q1, q2])
        await session.flush()

        v1 = QuestionVersion(
            question_id=q1.id,
            version_kind="official_original",
            canonical_text="Questão 1 sobre Diluição de Soluções...",
            statement="Questão 1 sobre Diluição...",
            content_hash="h1",
            recommended_difficulty="EASY",
        )
        v2 = QuestionVersion(
            question_id=q2.id,
            version_kind="official_original",
            canonical_text="Questão 2 avançada sobre Soluções...",
            statement="Questão 2 avançada...",
            content_hash="h2",
            recommended_difficulty="HARD",
        )
        session.add_all([v1, v2])
        await session.flush()

        # AI classification for v1
        c1 = PedagogicalClassification(
            question_version_id=v1.id,
            discipline="Química",
            content="Diluição de Soluções",
            subcontent="Cálculo de Molaridade Final",
            difficulty="EASY",
            classification_confidence=Decimal("0.95"),
            reasoning_type="quantitativo",
            prerequisites=["Molaridade"],
            keywords=["diluição", "soluções"],
            status="CLASSIFIED",
            source="ai",
        )
        session.add(c1)

        # Direct Catalog Link for v2
        link_q2 = ContentQuestionLink(content_node_id=content_node.id, question_version_id=v2.id)
        session.add(link_q2)

        # 3. Create Resources (Public & Private)
        res_public = EducationalResource(
            title="Videoaula: Introdução à Diluição de Soluções",
            description="Explicação simples de diluição",
            resource_type="VIDEO",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            source_url="https://youtube.com/watch?v=123",
            status="active",
        )
        session.add(res_public)
        await session.flush()

        video_detail = VideoResourceDetail(
            resource_id=res_public.id,
            platform="YOUTUBE",
            external_video_id="123",
            duration_seconds=600,
        )
        session.add(video_detail)

        res_private = EducationalResource(
            title="Apostila Exclusiva Escola A - Soluções",
            resource_type="THEORY_MATERIAL",
            origin_type="SCHOOL",
            owner_external_id="SCHOOL_A",
            visibility_scope="PRIVATE",
            status="active",
        )
        session.add(res_private)
        await session.flush()

        # Link resources to content node
        link_r1 = ContentResourceLink(
            content_node_id=content_node.id,
            resource_id=res_public.id,
            pedagogical_role="VIDEO",
            recommended_level="EASY",
        )
        link_r2 = ContentResourceLink(
            content_node_id=content_node.id,
            resource_id=res_private.id,
            pedagogical_role="THEORY",
            recommended_level="HARD",
        )
        session.add_all([link_r1, link_r2])

        await session.commit()
        return content_node.id, v1.id, v2.id, res_public.id, res_private.id

    async def test_01_find_questions_by_content(self):
        """1. buscar questões por conteúdo (via IA e via link)"""
        async with self.session_factory() as session:
            await self._seed_data(session)
            service = KnowledgeService(session)

            questions = await service.find_questions_by_content("Diluição")
            self.assertEqual(len(questions), 2)
            qv_ids = {q["question_version_id"] for q in questions}
            self.assertEqual(len(qv_ids), 2)

    async def test_02_find_resources_by_content(self):
        """2. buscar recursos por conteúdo"""
        async with self.session_factory() as session:
            await self._seed_data(session)
            service = KnowledgeService(session)

            resources = await service.find_resources_by_content("Diluição", requester_institution_id="SCHOOL_A")
            self.assertEqual(len(resources), 2)

    async def test_03_find_by_difficulty(self):
        """3. buscar por dificuldade"""
        async with self.session_factory() as session:
            await self._seed_data(session)
            service = KnowledgeService(session)

            easy_q = await service.find_questions_by_difficulty("EASY", content_name_or_code="Diluição")
            self.assertEqual(len(easy_q), 1)
            self.assertEqual(easy_q[0]["difficulty_ai"], "EASY")

    async def test_04_relate_material_and_content(self):
        """4. relacionar material e conteúdo dinamicamente"""
        async with self.session_factory() as session:
            content_id, _, _, _, _ = await self._seed_data(session)
            service = KnowledgeService(session)

            new_res = EducationalResource(
                title="Resumo PDF Diluição",
                resource_type="PDF",
                origin_type="PLATFORM",
                visibility_scope="PUBLIC",
                status="active",
            )
            session.add(new_res)
            await session.flush()

            link = await service.link_resource_to_content(new_res.id, content_id, "REVIEW")
            self.assertEqual(link.pedagogical_role, "REVIEW")

            res_list = await service.find_resources_by_content("Diluição")
            self.assertEqual(len(res_list), 2)  # Public video + New PDF

    async def test_05_relate_question_and_content(self):
        """5. relacionar questão e conteúdo"""
        async with self.session_factory() as session:
            content_id, _, _, _, _ = await self._seed_data(session)
            service = KnowledgeService(session)

            new_q = Question(validation_status="approved")
            session.add(new_q)
            await session.flush()

            new_qv = QuestionVersion(
                question_id=new_q.id,
                version_kind="official_original",
                canonical_text="Nova questão",
                content_hash="h3",
            )
            session.add(new_qv)
            await session.flush()

            await service.link_question_to_content(new_qv.id, content_id)

            q_list = await service.find_questions_by_content("Diluição")
            self.assertEqual(len(q_list), 3)

    async def test_06_maintain_origin_traceability(self):
        """6. manter origem e rastreabilidade"""
        async with self.session_factory() as session:
            _, _, _, res_pub_id, res_priv_id = await self._seed_data(session)
            res_pub = await session.get(EducationalResource, res_pub_id)
            res_priv = await session.get(EducationalResource, res_priv_id)

            self.assertEqual(res_pub.origin_type, "PLATFORM")
            self.assertEqual(res_priv.origin_type, "SCHOOL")
            self.assertEqual(res_priv.owner_external_id, "SCHOOL_A")

    async def test_07_08_09_institutional_isolation_and_visibility(self):
        """7, 8, 9. isolamento institucional e acesso privado/global/grant"""
        async with self.session_factory() as session:
            await self._seed_data(session)
            service = KnowledgeService(session)

            # School B sees only public resource
            res_school_b = await service.find_resources_by_content("Diluição", requester_institution_id="SCHOOL_B")
            self.assertEqual(len(res_school_b), 1)
            self.assertEqual(res_school_b[0]["title"], "Videoaula: Introdução à Diluição de Soluções")

            # School A sees public + private
            res_school_a = await service.find_resources_by_content("Diluição", requester_institution_id="SCHOOL_A")
            self.assertEqual(len(res_school_a), 2)

    async def test_10_11_versioned_and_reprocessed_classification(self):
        """10, 11. classificação versionada e reprocessada"""
        async with self.session_factory() as session:
            _, v1_id, _, _, _ = await self._seed_data(session)
            service = KnowledgeService(session)

            # Add newer classification with status NEEDS_REVIEW for v1
            c_new = PedagogicalClassification(
                question_version_id=v1_id,
                discipline="Química",
                content="Diluição de Soluções",
                subcontent="Novo Raciocínio",
                difficulty="MEDIUM",
                reasoning_type="conceitual",
                status="NEEDS_REVIEW",
                source="ai",
            )
            session.add(c_new)
            await session.commit()

            # Querying active_classification_only=True ignores NEEDS_REVIEW and returns active CLASSIFIED
            q_active = await service.find_questions_by_content("Diluição", active_classification_only=True)
            v1_match = [q for q in q_active if q["question_version_id"] == str(v1_id)][0]
            self.assertEqual(v1_match["classification"]["status"], "CLASSIFIED")

    async def test_12_non_duplication_of_results(self):
        """12. não duplicação de resultados"""
        async with self.session_factory() as session:
            content_id, v1_id, _, _, _ = await self._seed_data(session)
            service = KnowledgeService(session)

            # Also create direct link for v1 which already has AI classification
            await service.link_question_to_content(v1_id, content_id)

            q_list = await service.find_questions_by_content("Diluição")
            v1_occurrences = [q for q in q_list if q["question_version_id"] == str(v1_id)]
            self.assertEqual(len(v1_occurrences), 1)

    async def test_13_deterministic_queries(self):
        """13. consultas determinísticas"""
        async with self.session_factory() as session:
            await self._seed_data(session)
            service = KnowledgeService(session)

            res1 = await service.find_questions_by_content("Diluição")
            res2 = await service.find_questions_by_content("Diluição")
            self.assertEqual(res1, res2)


if __name__ == "__main__":
    unittest.main()
