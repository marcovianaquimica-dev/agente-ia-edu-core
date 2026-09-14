"""The gate applied to study search, through the institution_id it already takes."""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.pedagogical_universe import (
    PedagogicalUniverse,
    PedagogicalUniverseCatalogScope,
)
from agente_ia_edu.services.study_search import StudySearchService
from tests.test_question_bank_core import _Fixture

SCHOOL = str(uuid.uuid4())


class StudySearchDisciplineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_without_session_it_still_returns_the_parsed_payload(self):
        result = await StudySearchService.search("cinetica quimica", session=None)
        self.assertIn("resolved_context", result)

    async def test_school_without_universe_is_not_filtered(self):
        async with self.session_factory() as session:
            result = await StudySearchService.search(
                "cinetica quimica", session=session, institution_id=SCHOOL
            )
        self.assertIn("resolved_context", result)


class StudySearchRestrictedSchoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_restricted_school_gets_nothing_from_another_discipline(self):
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            biology = seeded["nodes"]["bio_content"]
            maths = seeded["nodes"]["math_content"]

            universe = PedagogicalUniverse(
                id=uuid.uuid4(), external_id="u", slug="u", name="u",
                owner_type="SCHOOL", owner_external_id=SCHOOL, status="ACTIVE",
            )
            session.add(universe)
            await session.flush()
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=biology.id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                )
            )
            await session.commit()

            # ``resolve_context`` always defaults difficulty to MEDIUM, and the
            # fixture stores UNKNOWN on every classification but one, so a
            # search with the default difficulty returns nothing for either
            # discipline - the restricted assertion would then pass on an empty
            # list and prove nothing. UNKNOWN is what the rows actually hold.
            unrestricted = await StudySearchService.search(
                maths.code, session=session, difficulty="UNKNOWN", institution_id=None
            )
            restricted = await StudySearchService.search(
                maths.code, session=session, difficulty="UNKNOWN", institution_id=SCHOOL
            )

        # `search` nests its hits under ``results``; the bare ``questions`` key
        # does not exist, and asserting on it would let both paths pass empty.
        self.assertGreater(
            len(unrestricted["results"]["questions"]), 0,
            "the fixture must return something for this content when unrestricted",
        )
        self.assertEqual(
            restricted["results"]["questions"], [],
            "a school scoped to biology must get nothing from mathematics",
        )

    async def test_restricted_school_still_sees_its_own_discipline(self):
        """The gate must restrict, not merely subtract - the other direction."""
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            biology = seeded["nodes"]["bio_content"]

            universe = PedagogicalUniverse(
                id=uuid.uuid4(), external_id="u2", slug="u2", name="u2",
                owner_type="SCHOOL", owner_external_id=SCHOOL, status="ACTIVE",
            )
            session.add(universe)
            await session.flush()
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=biology.id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                )
            )
            await session.commit()

            restricted = await StudySearchService.search(
                biology.code, session=session, difficulty="UNKNOWN", institution_id=SCHOOL
            )

        self.assertGreater(
            len(restricted["results"]["questions"]), 0,
            "biology content must survive a biology scope",
        )

    async def test_universe_without_declared_scope_restricts_nothing(self):
        """Absence, third shape: an active universe that declares no scope."""
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            maths = seeded["nodes"]["math_content"]

            session.add(
                PedagogicalUniverse(
                    id=uuid.uuid4(), external_id="u3", slug="u3", name="u3",
                    owner_type="SCHOOL", owner_external_id=SCHOOL, status="ACTIVE",
                )
            )
            await session.commit()

            result = await StudySearchService.search(
                maths.code, session=session, difficulty="UNKNOWN", institution_id=SCHOOL
            )

        self.assertGreater(
            len(result["results"]["questions"]), 0,
            "a universe with no catalog scope must not restrict anything",
        )


if __name__ == "__main__":
    unittest.main()
