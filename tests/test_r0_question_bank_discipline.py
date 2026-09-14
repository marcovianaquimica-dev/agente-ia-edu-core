"""The gate applied to the question bank - and to its count, not just its page.

Filtering the page query alone makes the total lie: the UI would say 48 and
hand back 12. The count assertion here is the point of the file.
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
from agente_ia_edu.services.question_bank import QuestionBankService
from tests.test_question_bank_core import _Fixture

SCHOOL = str(uuid.uuid4())


class QuestionBankDisciplineTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_no_school_id_lists_everything(self):
        """Every existing caller passes no school_id, and must stay unrestricted."""
        async with self.session_factory() as session:
            page = await QuestionBankService(session).list_questions()
        self.assertEqual(page.total, 0)

    async def test_school_without_universe_lists_everything(self):
        async with self.session_factory() as session:
            page = await QuestionBankService(session).list_questions(school_id=SCHOOL)
        self.assertEqual(page.total, 0)


class QuestionBankRestrictedSchoolTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_restricted_school_sees_only_its_own_discipline(self):
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            biology = seeded["nodes"]["bio_content"]

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

            service = QuestionBankService(session)
            everything = await service.list_questions()
            restricted = await service.list_questions(school_id=SCHOOL)

        self.assertGreater(everything.total, restricted.total,
                           "the fixture must contain more than one discipline")
        self.assertGreater(restricted.total, 0,
                           "the allowed discipline must survive the filter")
        # The count and the page must agree - this is the assertion that catches
        # a filter applied to only one of the two queries.
        self.assertEqual(restricted.total, len(restricted.items))
        for item in restricted.items:
            # `content_code` hangs off the classification view, and is absent on
            # unclassified questions - which the gate deliberately lets through.
            code = item.classification.content_code if item.classification else None
            self.assertNotEqual(code, "MATH-ALGEBRA-FUNCTIONS")


if __name__ == "__main__":
    unittest.main()
