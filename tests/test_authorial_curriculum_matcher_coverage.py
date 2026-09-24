"""
Service-layer coverage for src/agente_ia_edu/services/authorial_curriculum_matcher.py.

Wave 7 of the overnight coverage campaign, filling the gaps left after
tests/test_phase26_authorial_material_ingestion.py already exercises the
happy-path MAPPED/TAXONOMY_GAP cases indirectly through the ingestion
pipeline. This file targets AuthorialCurriculumMatcher.match() and its
helpers directly: the empty-candidate short circuit, _score's early-return
on an empty token set, as_dict()'s serialization, the SUBCONTENT-walks-up-
to-its-CONTENT-parent branch, and the sibling-subcontent collection loop.
"""

import asyncio
import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode
from agente_ia_edu.services.authorial_curriculum_matcher import (
    AuthorialCurriculumMatcher,
    CurriculumMatch,
    _score,
)


def _run(coro):
    return asyncio.run(coro)


class AuthorialCurriculumMatcherTests(unittest.TestCase):
    def setUp(self):
        async def setup_database():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            return engine, async_sessionmaker(engine, class_=AsyncSession)

        self.engine, self.session_factory = _run(setup_database())
        self.addCleanup(lambda: _run(self.engine.dispose()))

    async def _seed_taxonomy(self):
        async with self.session_factory() as s:
            disc = CatalogNode(code=f"D-{uuid.uuid4().hex[:6]}", name="Quimica",
                                node_type="DISCIPLINE", active=True)
            s.add(disc)
            await s.flush()
            disc.root_id = disc.id
            area = CatalogNode(code=f"A-{uuid.uuid4().hex[:6]}", name="Fisico Quimica",
                                node_type="AREA", parent_id=disc.id, root_id=disc.id, active=True)
            s.add(area)
            await s.flush()
            content = CatalogNode(code=f"C-{uuid.uuid4().hex[:6]}", name="Cinetica Quimica",
                                   node_type="CONTENT", parent_id=area.id, root_id=disc.id, active=True)
            s.add(content)
            await s.flush()
            sub1 = CatalogNode(code=f"S1-{uuid.uuid4().hex[:6]}", name="Velocidade de Reacao",
                                node_type="SUBCONTENT", parent_id=content.id, root_id=disc.id, active=True)
            sub2 = CatalogNode(code=f"S2-{uuid.uuid4().hex[:6]}", name="Catalisador Enzimatico",
                                node_type="SUBCONTENT", parent_id=content.id, root_id=disc.id, active=True)
            s.add_all([sub1, sub2])
            await s.flush()
            # Captured before commit() - the async default session
            # (expire_on_commit=True, matching production) expires every
            # object tracked here, and reading `.code` afterward would need
            # an implicit reload outside any await -> MissingGreenlet.
            codes = (disc.code, area.code, content.code, sub1.code, sub2.code)
            await s.commit()
            return codes

    # -- _score() early return on empty token sets --------------------------
    def test_score_returns_zero_when_query_tokens_empty(self):
        self.assertEqual(_score(set(), {"reacao", "quimica"}), 0.0)

    def test_score_returns_zero_when_node_tokens_empty(self):
        self.assertEqual(_score({"reacao"}, set()), 0.0)

    def test_score_returns_zero_when_no_overlap(self):
        self.assertEqual(_score({"biologia"}, {"quimica", "fisica"}), 0.0)

    # -- CurriculumMatch.as_dict() -------------------------------------------
    def test_as_dict_serializes_every_field(self):
        match = CurriculumMatch(
            "MAPPED", discipline_code="D1", area_code="A1", content_code="C1",
            subcontent_codes=["S2", "S1"], confidence=0.5678, matched_text="Cinetica",
        )
        self.assertEqual(match.as_dict(), {
            "classification_state": "MAPPED",
            "discipline_code": "D1",
            "area_code": "A1",
            "content_code": "C1",
            "subcontent_codes": ["S2", "S1"],
            "confidence": 0.568,
            "matched_text": "Cinetica",
        })

    # -- match(): empty candidate list short-circuits to TAXONOMY_GAP -------
    def test_match_with_no_candidate_texts_is_taxonomy_gap(self):
        async def run():
            async with self.session_factory() as s:
                matcher = AuthorialCurriculumMatcher(s)
                return await matcher.match([])

        result = _run(run())
        self.assertEqual(result.classification_state, "TAXONOMY_GAP")
        self.assertEqual(result.confidence, 0.0)

    def test_match_with_only_blank_candidate_texts_is_taxonomy_gap(self):
        async def run():
            async with self.session_factory() as s:
                matcher = AuthorialCurriculumMatcher(s)
                return await matcher.match(["   ", "", None])

        result = _run(run())
        self.assertEqual(result.classification_state, "TAXONOMY_GAP")

    # -- match(): best match is a SUBCONTENT -> walks up to its CONTENT parent
    def test_match_on_subcontent_name_walks_up_to_content_and_collects_siblings(self):
        async def seed_and_match():
            disc_code, area_code, content_code, sub1_code, sub2_code = await self._seed_taxonomy()
            async with self.session_factory() as s:
                matcher = AuthorialCurriculumMatcher(s)
                result = await matcher.match(["Velocidade de Reacao Quimica"])
            return result, disc_code, area_code, content_code, sub1_code

        result, disc_code, area_code, content_code, sub1_code = _run(seed_and_match())
        self.assertEqual(result.classification_state, "MAPPED")
        self.assertEqual(result.content_code, content_code)
        self.assertEqual(result.area_code, area_code)
        self.assertEqual(result.discipline_code, disc_code)
        # the matched subcontent itself is always in the returned collection,
        # since it scores >= MIN_CONFIDENCE against its own name
        self.assertIn(sub1_code, result.subcontent_codes)
        self.assertEqual(result.matched_text, "Velocidade de Reacao")

    # -- match(): sibling subcontent collection is genuinely selective -------
    def test_match_only_collects_siblings_scoring_above_confidence_floor(self):
        async def seed_and_match():
            disc_code, area_code, content_code, sub1_code, sub2_code = await self._seed_taxonomy()
            async with self.session_factory() as s:
                matcher = AuthorialCurriculumMatcher(s)
                # Overlaps heavily with sub1's name, shares nothing with sub2's.
                result = await matcher.match(["Velocidade de Reacao"])
            return result, sub1_code, sub2_code

        result, sub1_code, sub2_code = _run(seed_and_match())
        self.assertEqual(result.classification_state, "MAPPED")
        self.assertIn(sub1_code, result.subcontent_codes)
        self.assertNotIn(sub2_code, result.subcontent_codes)

    # -- match(): below MIN_CONFIDENCE on a CONTENT-level match, no SUBCONTENT parent walk
    def test_match_below_confidence_floor_is_taxonomy_gap_with_best_score_reported(self):
        async def seed_and_match():
            await self._seed_taxonomy()
            async with self.session_factory() as s:
                matcher = AuthorialCurriculumMatcher(s)
                # Shares no meaningful tokens with any seeded node name.
                return await matcher.match(["Historia Medieval Europeia"])

        result = _run(seed_and_match())
        self.assertEqual(result.classification_state, "TAXONOMY_GAP")
        self.assertEqual(result.confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
