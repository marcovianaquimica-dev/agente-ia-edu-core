# tests/test_r0_fase2_gate.py
"""Proves the phase's central claim across all three consumers at once.

Each earlier task tested the service it changed. None tested that a school
with no universe still sees everything everywhere - which is the state of
every school in production today, and the failure mode that would lock out
the entire base on deploy day.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.pedagogical_universe import (
    PedagogicalUniverse,
    PedagogicalUniverseCatalogScope,
)
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.authorization import AuthorizationService
from agente_ia_edu.services.discipline_gate import DisciplineGate
from agente_ia_edu.services.question_bank import QuestionBankService
from agente_ia_edu.services.study_search import StudySearchService
from tests.test_question_bank_core import _Fixture

SCHOOL_WITHOUT_UNIVERSE = str(uuid.uuid4())
RESTRICTED_SCHOOL = str(uuid.uuid4())


def _context(school_id: str | None) -> AuthenticatedUserContext:
    return AuthenticatedUserContext(
        user_id="u-1", external_identity_id="ext-1",
        role="TEACHER", school_id=school_id,
    )


class Fase2GateTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_a_school_with_no_universe_is_unrestricted_in_all_three(self):
        async with self.session_factory() as session:
            scope = await DisciplineGate(session).scope_for_school(
                SCHOOL_WITHOUT_UNIVERSE
            )
            self.assertTrue(scope.unrestricted, "the gate itself")

            allowed = await AuthorizationService(session).require_discipline(
                AuthenticatedUserContext(
                    user_id="u-1", external_identity_id="ext-1",
                    role="TEACHER", school_id=SCHOOL_WITHOUT_UNIVERSE,
                ),
                uuid.uuid4(),
            )
            self.assertTrue(allowed.allowed, "authorization")

            # This database is empty, so ``0`` is the only total either a
            # filtered or an unfiltered query could return, and
            # ``resolved_context`` is filled before any row is read. Neither
            # assertion below can tell an open gate from a closed one; they
            # only prove these two calls do not raise on a school with no
            # rows to see. The claim that the school actually sees content is
            # proved on seeded data by
            # ``test_the_same_claim_against_content_that_actually_exists``.
            page = await QuestionBankService(session).list_questions(
                school_id=SCHOOL_WITHOUT_UNIVERSE
            )
            self.assertEqual(
                page.total, 0, "question bank does not raise on an empty database"
            )

            found = await StudySearchService.search(
                "cinetica quimica",
                session=session,
                institution_id=SCHOOL_WITHOUT_UNIVERSE,
            )
            self.assertIn(
                "resolved_context",
                found,
                "study search does not raise on an empty database",
            )

    async def test_no_consumer_refuses_on_absence(self):
        """The naive gate would raise instead of allowing. This is the test
        that would catch anyone rebuilding it on resolve_active_universe,
        which raises PermissionError when no universe exists."""
        async with self.session_factory() as session:
            for school in (None, SCHOOL_WITHOUT_UNIVERSE):
                with self.subTest(school=school):
                    scope = await DisciplineGate(session).scope_for_school(school)
                    self.assertTrue(scope.unrestricted)
                    self.assertTrue(scope.permits(uuid.uuid4()))
                    self.assertTrue(scope.permits(None))

    async def test_the_same_claim_against_content_that_actually_exists(self):
        """The teeth of the claim above, on a seeded bank.

        ``test_a_school_with_no_universe_is_unrestricted_in_all_three`` runs
        against an empty database, where ``total == 0`` is the only answer a
        filtered and an unfiltered query can give, and ``assertIn`` on a
        payload key holds with every hit filtered away. Both would keep
        passing with the gate wired to deny. This is the same claim with rows
        behind it: the school with no universe must see exactly what a caller
        that passes no school at all sees, in each of the three consumers.
        """
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            maths = seeded["nodes"]["math_content"]
            biology = seeded["nodes"]["bio_content"]

            for node in (maths, biology):
                with self.subTest(node=node.code):
                    allowed = await AuthorizationService(session).require_discipline(
                        _context(SCHOOL_WITHOUT_UNIVERSE), node.id
                    )
                    self.assertTrue(allowed.allowed, "authorization")

            page = await QuestionBankService(session).list_questions(
                school_id=SCHOOL_WITHOUT_UNIVERSE
            )
            everything = await QuestionBankService(session).list_questions()
            self.assertGreater(everything.total, 0, "the fixture must seed a bank")
            self.assertEqual(page.total, everything.total, "question bank")
            self.assertEqual(
                [item.official_number for item in page.items],
                [item.official_number for item in everything.items],
                "the page itself, not only its count",
            )

            # ``resolve_context`` defaults difficulty to MEDIUM while every
            # fixture classification stores UNKNOWN, so the default returns
            # nothing even unrestricted and would prove nothing either way.
            for_school = await StudySearchService.search(
                maths.code, session=session, difficulty="UNKNOWN",
                institution_id=SCHOOL_WITHOUT_UNIVERSE,
            )
            for_nobody = await StudySearchService.search(
                maths.code, session=session, difficulty="UNKNOWN",
                institution_id=None,
            )
            self.assertGreater(
                len(for_nobody["results"]["questions"]), 0,
                "the search must reach this content at all",
            )
            self.assertEqual(
                [q["id"] for q in for_school["results"]["questions"]],
                [q["id"] for q in for_nobody["results"]["questions"]],
                "study search",
            )


class Fase2RestrictedGateTests(unittest.IsolatedAsyncioTestCase):
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

    async def _scope_to_biology(self, session, seeded) -> None:
        universe = PedagogicalUniverse(
            id=uuid.uuid4(), external_id="u", slug="u", name="u",
            owner_type="SCHOOL", owner_external_id=RESTRICTED_SCHOOL,
            status="ACTIVE",
        )
        session.add(universe)
        await session.flush()
        session.add(
            PedagogicalUniverseCatalogScope(
                id=uuid.uuid4(), universe_id=universe.id,
                catalog_node_id=seeded["nodes"]["bio_content"].id,
                scope_kind="DISCIPLINE",
                include_descendants=True,
            )
        )
        await session.commit()

    async def test_a_restricted_school_gets_no_other_discipline_anywhere(self):
        """Spec section 9: a school restricted to one discipline receives no
        content from another - in search, in the bank, nor in authorization."""
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            biology = seeded["nodes"]["bio_content"]
            maths = seeded["nodes"]["math_content"]

            universe = PedagogicalUniverse(
                id=uuid.uuid4(), external_id="u", slug="u", name="u",
                owner_type="SCHOOL", owner_external_id=RESTRICTED_SCHOOL,
                status="ACTIVE",
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

            refused = await AuthorizationService(session).require_discipline(
                AuthenticatedUserContext(
                    user_id="u-1", external_identity_id="ext-1",
                    role="TEACHER", school_id=RESTRICTED_SCHOOL,
                ),
                maths.id,
            )
            self.assertFalse(refused.allowed, "authorization")

            page = await QuestionBankService(session).list_questions(
                school_id=RESTRICTED_SCHOOL
            )
            everything = await QuestionBankService(session).list_questions()
            self.assertLess(page.total, everything.total, "question bank")
            self.assertEqual(page.total, len(page.items), "count and page agree")

            # The brief originally read ``found.get("questions") or []``:
            # ``search`` nests its hits under ``results``, so that read a key
            # that never exists and was ``[] == []`` whatever the gate did -
            # provably inert. Fixed to read the key the service actually
            # fills, with the UNKNOWN difficulty the fixture rows really
            # carry (``resolve_context`` defaults to MEDIUM, which returns
            # nothing even unrestricted and would make this pass for the
            # wrong reason too).
            found = await StudySearchService.search(
                maths.code, session=session, difficulty="UNKNOWN",
                institution_id=RESTRICTED_SCHOOL,
            )
            self.assertEqual(
                found["results"]["questions"], [],
                "study search, on the key it fills",
            )

            # The unrestricted twin: proves the content above is reachable at
            # all, so the equality above is not vacuously true because the
            # query never returns anything for anyone.
            unrestricted_search = await StudySearchService.search(
                maths.code, session=session, difficulty="UNKNOWN",
                institution_id=None,
            )
            self.assertGreater(
                len(unrestricted_search["results"]["questions"]), 0,
                "mathematics must be reachable at all when unrestricted",
            )

    async def test_the_restriction_subtracts_only_the_other_discipline(self):
        """The mirror of the mirror: a gate that denied everything would pass
        every assertion above. Biology content must survive a biology scope,
        and the bank's page must be exactly the questions that are not
        another discipline's."""
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            await self._scope_to_biology(session, seeded)
            biology = seeded["nodes"]["bio_content"]

            allowed = await AuthorizationService(session).require_discipline(
                _context(RESTRICTED_SCHOOL), biology.id
            )
            self.assertTrue(allowed.allowed, "authorization, own discipline")

            page = await QuestionBankService(session).list_questions(
                school_id=RESTRICTED_SCHOOL
            )
            numbers = sorted(
                item.official_number for item in page.items
            )
            # 97 is the biology question; 96, 30 and 160 carry no
            # classification and absence is never evidence of another
            # discipline. 116, 129 and 150 are mathematics and 130 is
            # chemistry, none of which a biology scope may see.
            self.assertEqual(numbers, [30, 96, 97, 160], "question bank page")

            search = await StudySearchService.search(
                biology.code, session=session, difficulty="UNKNOWN",
                institution_id=RESTRICTED_SCHOOL,
            )
            self.assertGreater(
                len(search["results"]["questions"]), 0,
                "biology content must survive a biology scope",
            )


if __name__ == "__main__":
    unittest.main()
