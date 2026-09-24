"""Service-level coverage for src/agente_ia_edu/services/video_discovery.py.

Wave-7 (concurrent-zone campaign). tests/test_video_discovery.py already
exercises the bulk of VideoDiscoveryService (provider abstraction, dedup,
classification-threshold routing, conversion, tenant/rejection rules); this
file targets the branches it never reaches: the real (unconfigured)
YouTubeDiscoveryProvider's api_key-present stub path, a raw candidate with no
external_id, an out-of-range classifier difficulty, a classifier that raises,
review_candidate's invalid-action guard, and approve_and_convert_candidate's
missing-content-node / unknown-catalog-node / already-converted branches.
Baseline just before this file (existing test files only): 179 statements, 15
missing - 136, 207, 251, 257-259, 324, 355, 359, 363-371.

Uses a real async SQLAlchemy session against SQLite with the session_factory
default (``expire_on_commit=True``, matching production's
``create_session_factory()``) rather than the ``expire_on_commit=False``
override tests/test_video_discovery.py uses.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, EducationalResource, ExternalVideoCandidate
from agente_ia_edu.services.video_discovery import (
    CandidateStatus,
    MockVideoDiscoveryProvider,
    VideoDiscoveryService,
    YouTubeDiscoveryProvider,
)


class _RaisingClassifier:
    """A classifier that always fails - the real classify() call is wrapped
    in a try/except in discover_candidates() specifically because a
    classification backend can be unavailable/flaky; this is a plausible
    real failure, not a hypothetical one."""

    async def classify(self, *, question_text: str, model_name: str | None = None, **_kw):
        raise RuntimeError("classifier backend unavailable")


class VideoDiscoveryServiceCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        # deliberately no expire_on_commit override - matches production's
        # create_session_factory() default (True).
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def seed_catalog(self, session):
        root = CatalogNode(node_type="DISCIPLINE", name="Quimica", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id
        content = CatalogNode(
            parent_id=root.id, root_id=root.id, node_type="CONTENT",
            code="VDC-CONTENT", name="Estequiometria", position=1, active=True,
        )
        session.add(content)
        await session.flush()
        return content.id

    # -- line 136: a configured YouTubeDiscoveryProvider (api_key set) is
    #    still a structural stub - no HTTP client wired up yet - and must
    #    return an empty result rather than raise or fabricate data.
    async def test_youtube_provider_with_api_key_returns_empty_stub(self):
        provider = YouTubeDiscoveryProvider(api_key="a-real-looking-key")
        result = await provider.search("Estequiometria", limit=5)
        self.assertEqual(result, [])

    # -- line 207: a raw candidate with no external_id is silently dropped
    #    from the dedup map, never persisted (never a NULL-external_id row).
    async def test_discover_candidates_skips_entries_without_external_id(self):
        async with self.session_factory() as session:
            content_id = await self.seed_catalog(session)
            service = VideoDiscoveryService(session)
            provider = MockVideoDiscoveryProvider(candidates=[
                {"source": "YOUTUBE", "external_id": "", "title": "Sem ID nenhum"},
                {"source": "YOUTUBE", "external_id": "has_id_1", "title": "Com ID", "url": "https://yt/has_id_1"},
            ])
            candidates = await service.discover_candidates(content_node_id=content_id, providers=[provider])
            self.assertEqual([c.external_id for c in candidates], ["has_id_1"])

    # -- line 251: an out-of-range recommended_difficulty from the classifier
    #    is never trusted verbatim - falls back to EASY.
    async def test_discover_candidates_normalizes_invalid_classifier_difficulty(self):
        class _WeirdDifficultyClassifier:
            async def classify(self, *, question_text: str, model_name: str | None = None, **_kw):
                return {"classification_confidence": 0.95, "difficulty": "IMPOSSIVEL"}

        async with self.session_factory() as session:
            content_id = await self.seed_catalog(session)
            service = VideoDiscoveryService(session)
            provider = MockVideoDiscoveryProvider(candidates=[
                {"source": "YOUTUBE", "external_id": "weird_diff_1", "title": "T", "url": "https://yt/weird_diff_1"},
            ])
            candidates = await service.discover_candidates(
                content_node_id=content_id, providers=[provider], classifier=_WeirdDifficultyClassifier(),
            )
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].recommended_difficulty, "EASY")
            # confidence was high enough to classify despite the bogus
            # difficulty label - the two fields are independent.
            self.assertEqual(candidates[0].status, CandidateStatus.CLASSIFIED)

    # -- lines 257-259: the classifier itself raising is caught, logged, and
    #    degrades to PENDING_REVIEW rather than failing the whole discovery.
    async def test_discover_candidates_classifier_exception_degrades_to_pending_review(self):
        async with self.session_factory() as session:
            content_id = await self.seed_catalog(session)
            service = VideoDiscoveryService(session)
            provider = MockVideoDiscoveryProvider(candidates=[
                {"source": "YOUTUBE", "external_id": "classifier_boom_1", "title": "T", "url": "https://yt/classifier_boom_1"},
            ])
            candidates = await service.discover_candidates(
                content_node_id=content_id, providers=[provider], classifier=_RaisingClassifier(),
            )
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].status, CandidateStatus.PENDING_REVIEW)
            # never a fabricated confidence for a classification that failed
            self.assertEqual(candidates[0].classification_confidence, 0)

    # -- line 324: review_candidate rejects an action that is neither
    #    APPROVE nor REJECT.
    async def test_review_candidate_invalid_action_raises(self):
        async with self.session_factory() as session:
            content_id = await self.seed_catalog(session)
            service = VideoDiscoveryService(session)
            provider = MockVideoDiscoveryProvider()
            candidates = await service.discover_candidates(content_node_id=content_id, providers=[provider])
            with self.assertRaises(ValueError) as ctx:
                await service.review_candidate(candidates[0].id, action="MAYBE")
            self.assertIn("Invalid review action", str(ctx.exception))

    # -- line 355: neither an explicit content_node_id nor the candidate's
    #    own content_node_id is available - never guesses a target.
    async def test_approve_and_convert_without_any_content_node_id_raises(self):
        async with self.session_factory() as session:
            service = VideoDiscoveryService(session)
            provider = MockVideoDiscoveryProvider(candidates=[
                {"source": "YOUTUBE", "external_id": "no_node_1", "title": "T", "url": "https://yt/no_node_1"},
            ])
            # query-based discovery, no content_node_id at all -> the
            # persisted candidate's own content_node_id is also None.
            candidates = await service.discover_candidates(query="Estequiometria", providers=[provider])
            self.assertIsNone(candidates[0].content_node_id)
            with self.assertRaises(ValueError) as ctx:
                await service.approve_and_convert_candidate(candidates[0].id)
            self.assertIn("Must specify a content_node_id", str(ctx.exception))

    # -- line 359: an explicit content_node_id that matches no CatalogNode.
    async def test_approve_and_convert_with_unknown_catalog_node_raises(self):
        async with self.session_factory() as session:
            content_id = await self.seed_catalog(session)
            service = VideoDiscoveryService(session)
            provider = MockVideoDiscoveryProvider()
            candidates = await service.discover_candidates(content_node_id=content_id, providers=[provider])
            bogus_node_id = uuid.uuid4()
            with self.assertRaises(ValueError) as ctx:
                await service.approve_and_convert_candidate(candidates[0].id, bogus_node_id)
            self.assertIn("CatalogNode not found", str(ctx.exception))

    # -- lines 363-371: converting an already-converted candidate a second
    #    time returns the EXISTING resource/link, never a duplicate.
    async def test_approve_and_convert_twice_returns_the_same_resource(self):
        async with self.session_factory() as session:
            content_id = await self.seed_catalog(session)
            service = VideoDiscoveryService(session)
            provider = MockVideoDiscoveryProvider()
            candidates = await service.discover_candidates(content_node_id=content_id, providers=[provider])
            # captured before the first approve_and_convert_candidate() call:
            # that call commits internally (expire_on_commit=True here, as in
            # production), which would expire this ORM object too - a second,
            # later `candidates[0].id` attribute read would then need a lazy
            # reload outside the async greenlet bridge and MissingGreenlet.
            candidate_id = candidates[0].id

            res1, link1 = await service.approve_and_convert_candidate(candidate_id, content_id)
            res2, link2 = await service.approve_and_convert_candidate(candidate_id, content_id)

            self.assertEqual(res1.id, res2.id)
            self.assertEqual(link1.id, link2.id)

            all_resources = (await session.execute(
                select(EducationalResource).where(EducationalResource.id == res1.id)
            )).scalars().all()
            self.assertEqual(len(all_resources), 1)


if __name__ == "__main__":
    unittest.main()
