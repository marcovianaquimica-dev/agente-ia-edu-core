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
    ContentResourceLink,
    EducationalResource,
    ExternalVideoCandidate,
    StudentContentMastery,
    VideoResourceDetail,
)
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.pedagogical_classifier import MockPedagogicalClassifierProvider
from agente_ia_edu.services.video_discovery import (
    CandidateStatus,
    MockVideoDiscoveryProvider,
    VideoDiscoveryService,
    YouTubeDiscoveryProvider,
)
from agente_ia_edu.services.video_engine import VideoRecommendationEngine


class TestVideoDiscoveryLayer(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_catalog(self, session: AsyncSession):
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
        await session.commit()
        return root.id, content_node.id

    async def test_01_02_03_provider_abstraction_mock_discovery(self):
        """1, 2, 3. abstração de provider, mock provider, descoberta básica"""
        async with self.session_factory() as session:
            _, content_id = await self._seed_catalog(session)
            service = VideoDiscoveryService(session)
            mock_p = MockVideoDiscoveryProvider(name="YOUTUBE")

            candidates = await service.discover_candidates(
                content_node_id=content_id,
                discipline="Química",
                providers=[mock_p],
            )

            self.assertGreater(len(candidates), 0)
            self.assertEqual(candidates[0].source, "YOUTUBE")
            self.assertTrue(candidates[0].url.startswith("https://youtube.com"))

    async def test_04_05_deterministic_query_and_context(self):
        """4, 5. query determinística e contexto pedagógico"""
        q1 = VideoDiscoveryService.generate_search_query("Diluição de Soluções", discipline="Química")
        q2 = VideoDiscoveryService.generate_search_query("Diluição de Soluções", discipline="Química")
        self.assertEqual(q1, "Diluição de Soluções Química")
        self.assertEqual(q1, q2)

    async def test_06_07_valid_candidate_and_optional_fields(self):
        """6, 7. candidato válido com campos opcionais"""
        async with self.session_factory() as session:
            service = VideoDiscoveryService(session)
            custom_cand = [{
                "source": "YOUTUBE",
                "external_id": "yt_opt_100",
                "title": "Aulão de Soluções",
                "description": None,
                "channel_or_author": None,
                "url": "https://youtube.com/watch?v=yt_opt_100",
                "thumbnail_url": None,
                "duration_seconds": None,
                "language": "pt-BR",
            }]
            provider = MockVideoDiscoveryProvider(candidates=custom_cand)

            cands = await service.discover_candidates(query="Diluição", providers=[provider])
            self.assertEqual(len(cands), 1)
            self.assertEqual(cands[0].external_id, "yt_opt_100")
            self.assertIsNone(cands[0].description)
            self.assertIsNone(cands[0].duration_seconds)

    async def test_08_09_deduplication_by_source_and_external_id(self):
        """8, 9. deduplicação determinística por (source, external_id)"""
        async with self.session_factory() as session:
            service = VideoDiscoveryService(session)
            duplicate_cands = [
                {"source": "YOUTUBE", "external_id": "abc123_dup", "title": "Vídeo Duplicado", "url": "https://yt/abc123_dup"},
                {"source": "YOUTUBE", "external_id": "abc123_dup", "title": "Vídeo Duplicado Mesma ID", "url": "https://yt/abc123_dup"},
            ]
            provider = MockVideoDiscoveryProvider(candidates=duplicate_cands)

            cands = await service.discover_candidates(query="Diluição", providers=[provider])
            self.assertEqual(len(cands), 1)
            self.assertEqual(cands[0].external_id, "abc123_dup")

    async def test_10_11_12_candidate_states_review_approval_rejection(self):
        """10, 11, 12. estados do candidato: PENDING_REVIEW -> APPROVED / REJECTED"""
        async with self.session_factory() as session:
            service = VideoDiscoveryService(session)
            cands = await service.discover_candidates(query="Diluição", providers=[MockVideoDiscoveryProvider()])
            cand = cands[0]

            self.assertIn(cand.status, (CandidateStatus.DISCOVERED, CandidateStatus.PENDING_REVIEW, CandidateStatus.CLASSIFIED))

            # Approve candidate
            approved = await service.review_candidate(cand.id, action="APPROVE", reasoning="Excelente conteúdo")
            self.assertEqual(approved.status, CandidateStatus.APPROVED)

            # Reject candidate
            cand2 = cands[1]
            rejected = await service.review_candidate(cand2.id, action="REJECT", reasoning="Fora do escopo")
            self.assertEqual(rejected.status, CandidateStatus.REJECTED)

    async def test_13_14_low_confidence_and_classification(self):
        """13, 14. classificação e baixa confiança mantendo PENDING_REVIEW"""
        async with self.session_factory() as session:
            service = VideoDiscoveryService(session, confidence_threshold=Decimal("0.80"))

            # Low confidence classifier (0.50) -> status PENDING_REVIEW
            low_conf_classifier = MockPedagogicalClassifierProvider(default_confidence=0.50)
            cands_low = await service.discover_candidates(
                query="Diluição",
                providers=[MockVideoDiscoveryProvider()],
                classifier=low_conf_classifier,
            )
            self.assertEqual(cands_low[0].status, CandidateStatus.PENDING_REVIEW)

            # High confidence classifier (0.90) -> status CLASSIFIED
            high_conf_classifier = MockPedagogicalClassifierProvider(default_confidence=0.90)
            cands_high = await service.discover_candidates(
                query="Concentração",
                providers=[MockVideoDiscoveryProvider(name="YOUTUBE_HIGH")],
                classifier=high_conf_classifier,
            )
            self.assertEqual(cands_high[0].status, CandidateStatus.CLASSIFIED)

    async def test_15_16_17_conversion_to_educational_resource(self):
        """15, 16, 17. conversão para EducationalResource, VideoResourceDetail e ContentResourceLink"""
        async with self.session_factory() as session:
            _, content_id = await self._seed_catalog(session)
            service = VideoDiscoveryService(session)

            cands = await service.discover_candidates(
                content_node_id=content_id,
                providers=[MockVideoDiscoveryProvider()],
            )
            cand = cands[0]

            res, link = await service.approve_and_convert_candidate(
                candidate_id=cand.id,
                content_node_id=content_id,
                origin_type="EXTERNAL",
                visibility_scope="PUBLIC",
                recommended_level="EASY",
            )

            self.assertIsNotNone(res.id)
            self.assertEqual(res.resource_type, "VIDEO")
            self.assertEqual(res.origin_type, "EXTERNAL")

            self.assertIsNotNone(res.video_detail)
            self.assertEqual(res.video_detail.external_video_id, cand.external_id)

            self.assertEqual(link.content_node_id, content_id)
            self.assertEqual(link.resource_id, res.id)

            # Candidate status becomes AVAILABLE
            refreshed_cand = await session.get(ExternalVideoCandidate, cand.id)
            self.assertEqual(refreshed_cand.status, CandidateStatus.AVAILABLE)
            self.assertEqual(refreshed_cand.converted_resource_id, res.id)

    async def test_18_19_20_origin_types_external_school_author(self):
        """18, 19, 20. origens EXTERNAL, SCHOOL, AUTHOR/PLATFORM"""
        async with self.session_factory() as session:
            _, content_id = await self._seed_catalog(session)
            service = VideoDiscoveryService(session)

            cands = await service.discover_candidates(content_node_id=content_id, providers=[MockVideoDiscoveryProvider()])

            res_ext, _ = await service.approve_and_convert_candidate(cands[0].id, content_id, origin_type="EXTERNAL")
            self.assertEqual(res_ext.origin_type, "EXTERNAL")

            res_sch, _ = await service.approve_and_convert_candidate(cands[1].id, content_id, origin_type="SCHOOL", owner_external_id="SCHOOL_A", visibility_scope="PRIVATE")
            self.assertEqual(res_sch.origin_type, "SCHOOL")
            self.assertEqual(res_sch.owner_external_id, "SCHOOL_A")
            self.assertEqual(res_sch.visibility_scope, "PRIVATE")

    async def test_21_22_multi_tenant_isolation_and_rejected_not_recommended(self):
        """21, 22. isolamento multi-tenant e candidato rejeitado não recomendado"""
        async with self.session_factory() as session:
            _, content_id = await self._seed_catalog(session)
            discovery_service = VideoDiscoveryService(session)
            knowledge_service = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, knowledge_service)

            cands = await discovery_service.discover_candidates(content_node_id=content_id, providers=[MockVideoDiscoveryProvider()])
            cand_rejected = cands[0]

            # Reject cand_rejected
            await discovery_service.review_candidate(cand_rejected.id, action="REJECT")

            # Trying to convert a REJECTED candidate raises error
            with self.assertRaises(ValueError):
                await discovery_service.approve_and_convert_candidate(cand_rejected.id, content_id)

            # Candidate is NOT in Knowledge Layer / Catalog
            videos = await knowledge_service.find_resources_by_content("Diluição de Soluções", resource_type="VIDEO")
            self.assertEqual(len(videos), 0)

    async def test_23_24_knowledge_layer_and_recommendation_engine_integration(self):
        """23, 24. integração completa com Knowledge Layer e VideoRecommendationEngine"""
        async with self.session_factory() as session:
            _, content_id = await self._seed_catalog(session)
            discovery_service = VideoDiscoveryService(session)
            knowledge_service = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, knowledge_service)

            cands = await discovery_service.discover_candidates(content_node_id=content_id, providers=[MockVideoDiscoveryProvider()])

            # Approve and convert candidate to catalog resource
            res, link = await discovery_service.approve_and_convert_candidate(cands[0].id, content_id, origin_type="PLATFORM", visibility_scope="PUBLIC", recommended_level="EASY")

            # Candidate is now in Knowledge Layer
            videos = await knowledge_service.find_resources_by_content("Diluição de Soluções", resource_type="VIDEO")
            self.assertEqual(len(videos), 1)

            # Recommended by VideoRecommendationEngine
            rec = await video_engine.recommend_video_for_student(student_id="s_disc", content_node_id=content_id)
            self.assertEqual(rec["status"], "OK")
            self.assertEqual(rec["video_resource_id"], str(res.id))

    async def test_25_26_no_candidate_and_no_provider_available(self):
        """25, 26. nenhum candidato e nenhum provider disponível (NO_PROVIDER_AVAILABLE / graceful)"""
        async with self.session_factory() as session:
            _, content_id = await self._seed_catalog(session)
            service = VideoDiscoveryService(session)

            # No providers configured -> returns empty list without error
            empty_res = await service.discover_candidates(content_node_id=content_id, providers=[])
            self.assertEqual(len(empty_res), 0)

            # Provider returning empty search -> empty list
            empty_provider = MockVideoDiscoveryProvider(candidates=[])
            empty_cands = await service.discover_candidates(content_node_id=content_id, providers=[empty_provider])
            self.assertEqual(len(empty_cands), 0)

            # Stubbed unconfigured YouTube Provider -> empty list
            yt_stub = YouTubeDiscoveryProvider(api_key=None)
            yt_cands = await service.discover_candidates(content_node_id=content_id, providers=[yt_stub])
            self.assertEqual(len(yt_cands), 0)

    async def test_27_28_provider_error_handling_and_multiple_providers(self):
        """27, 28. provider retornando erro (graceful) e múltiplos providers"""
        async with self.session_factory() as session:
            _, content_id = await self._seed_catalog(session)
            service = VideoDiscoveryService(session)

            failing_provider = MockVideoDiscoveryProvider(name="FAIL_PROVIDER", should_fail=True)
            good_provider = MockVideoDiscoveryProvider(name="GOOD_PROVIDER")

            # Failing provider alone -> graceful empty list
            cands_fail = await service.discover_candidates(content_node_id=content_id, providers=[failing_provider])
            self.assertEqual(len(cands_fail), 0)

            # Multiple providers: failing + good -> good provider's candidates preserved
            cands_multi = await service.discover_candidates(content_node_id=content_id, providers=[failing_provider, good_provider])
            self.assertGreater(len(cands_multi), 0)

    async def test_29_30_determinism_and_idempotency(self):
        """29, 30. determinismo e idempotência na descoberta"""
        async with self.session_factory() as session:
            _, content_id = await self._seed_catalog(session)
            service = VideoDiscoveryService(session)
            provider = MockVideoDiscoveryProvider(name="YOUTUBE_IDEMP")

            run1 = await service.discover_candidates(content_node_id=content_id, providers=[provider])
            run2 = await service.discover_candidates(content_node_id=content_id, providers=[provider])

            self.assertEqual(len(run1), len(run2))
            self.assertEqual([c.id for c in run1], [c.id for c in run2])

    async def test_31_real_integrated_scenario_discovery_approval_recommendation(self):
        """31. CENÁRIO REAL INTEGRADO (Descoberta -> Classificação -> Aprovação -> Catálogo -> Recomendação)"""
        async with self.session_factory() as session:
            _, content_id = await self._seed_catalog(session)
            discovery_service = VideoDiscoveryService(session)
            knowledge_service = KnowledgeService(session)
            video_engine = VideoRecommendationEngine(session, knowledge_service)

            # 1. Student has low mastery (38%) on "Diluição de Soluções"
            session.add(StudentContentMastery(external_identity_id="student:carlos_disc", content_node_id=content_id, mastery_score=38.0))
            await session.commit()

            # 2. No video in catalog -> Discovery finds candidates
            discovery_candidates = [
                {"source": "YOUTUBE", "external_id": "yt_dil_aula", "title": "Diluição de Soluções - Aula Completa", "duration_seconds": 1200},
                {"source": "YOUTUBE", "external_id": "yt_dil_exemplo", "title": "Diluição de Soluções - Exemplos Práticos", "duration_seconds": 480},
                {"source": "YOUTUBE", "external_id": "yt_dil_conc", "title": "Concentração e Diluição", "duration_seconds": 900},
            ]
            provider = MockVideoDiscoveryProvider(candidates=discovery_candidates)
            classifier = MockPedagogicalClassifierProvider(default_confidence=0.88, default_difficulty="EASY")

            discovered = await discovery_service.discover_candidates(
                content_node_id=content_id,
                providers=[provider],
                classifier=classifier,
            )
            self.assertEqual(len(discovered), 3)

            # 3. Unapproved candidate is NOT recommended yet
            rec_before = await video_engine.recommend_video_for_student(student_id="student:carlos_disc", content_node_id=content_id)
            self.assertEqual(rec_before["status"], "NO_VIDEO_AVAILABLE")

            # 4. Teacher/Admin approves candidate 'yt_dil_exemplo' (8 min EASY)
            target_cand = [c for c in discovered if c.external_id == "yt_dil_exemplo"][0]
            await discovery_service.review_candidate(target_cand.id, action="APPROVE")
            res, link = await discovery_service.approve_and_convert_candidate(
                candidate_id=target_cand.id,
                content_node_id=content_id,
                origin_type="PLATFORM",
                visibility_scope="PUBLIC",
                recommended_level="EASY",
            )

            # 5. Video is now available in catalog and recommended to student
            rec_after = await video_engine.recommend_video_for_student(student_id="student:carlos_disc", content_node_id=content_id)
            self.assertEqual(rec_after["status"], "OK")
            self.assertEqual(rec_after["video_resource_id"], str(res.id))
            self.assertEqual(rec_after["title"], "Diluição de Soluções - Exemplos Práticos")


if __name__ == "__main__":
    unittest.main()
