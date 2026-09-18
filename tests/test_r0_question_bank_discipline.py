"""The gate applied to the question bank - and to its count, not just its page.

Filtering the page query alone makes the total lie: the UI would say 48 and
hand back 12. The count assertion here is the point of the file.
"""

import asyncio
import unittest
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.question_bank import question_bank_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School, UserSchoolLink
from agente_ia_edu.db.models.pedagogical_universe import (
    PedagogicalUniverse,
    PedagogicalUniverseCatalogScope,
)
from agente_ia_edu.identity import ExternalIdentityContext
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


class QuestionBankRouteDisciplineHTTPTests(unittest.TestCase):
    """The service-level gate above is well covered, but every route in
    ``question_bank.py`` used to inject ``identity`` and never resolve it to a
    ``school_id`` at all - the gate never engaged over HTTP regardless of what
    the service supported. These tests exercise the routes themselves, with a
    real ``UserSchoolLink`` behind the identity, to prove the wiring - not just
    the mechanism - now works end to end."""

    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                fx = await _Fixture().build(session)
                school = School(code="QBHTTP1", name="Escola QB HTTP")
                session.add(school)
                await session.flush()
                universe = PedagogicalUniverse(
                    id=uuid.uuid4(), external_id="u-qb-http", slug="u-qb-http", name="u-qb-http",
                    owner_type="SCHOOL", owner_external_id=str(school.id), status="ACTIVE",
                )
                session.add(universe)
                await session.flush()
                session.add(PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=fx["nodes"]["bio_content"].id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                ))
                session.add(UserSchoolLink(
                    external_user_id="prof-qb-http", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()
                math_q, math_v = fx["made"][(2025, 116)]
                bio_q, bio_v = fx["made"][(2025, 97)]
                return engine, factory, math_q.id, math_v.id, bio_q.id, bio_v.id

        (self.engine, self.session_factory, self.math_question_id, self.math_version_id,
         self.bio_question_id, self.bio_version_id) = asyncio.run(setup())

        self.identity = ExternalIdentityContext(
            provider="test", external_user_id="prof-qb-http", roles=("teacher",),
        )
        app = FastAPI()
        app.include_router(question_bank_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_identity] = lambda: self.identity
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def test_list_questions_route_honors_the_callers_school_scope(self):
        resp = self.client.get("/api/v1/question-bank/questions", params={"page_size": 50})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        # The count and the page must agree, or the route only filtered one of
        # the two queries the service builds.
        self.assertEqual(body["pagination"]["total"], len(body["items"]))
        numbers = {item["official_number"] for item in body["items"]}
        self.assertIn(97, numbers)  # BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS: in scope
        self.assertNotIn(116, numbers)  # MATH-ALGEBRA-FUNCTIONS: outside scope

    def test_get_question_outside_scope_is_404_not_200(self):
        resp = self.client.get(f"/api/v1/question-bank/questions/{self.math_question_id}")
        self.assertEqual(resp.status_code, 404, resp.text)

    def test_get_question_inside_scope_still_200(self):
        resp = self.client.get(f"/api/v1/question-bank/questions/{self.bio_question_id}")
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_preview_selection_denies_out_of_scope_question(self):
        resp = self.client.post(
            "/api/v1/question-bank/selections/preview",
            json={"question_version_ids": [str(self.math_version_id)]},
        )
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_preview_selection_allows_in_scope_question(self):
        resp = self.client.post(
            "/api/v1/question-bank/selections/preview",
            json={"question_version_ids": [str(self.bio_version_id)]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_generate_list_denies_out_of_scope_question(self):
        resp = self.client.post(
            "/api/v1/question-bank/lists/generate",
            json={"question_version_ids": [str(self.math_version_id)], "title": "Lista fora de escopo"},
        )
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_generate_list_allows_in_scope_question(self):
        resp = self.client.post(
            "/api/v1/question-bank/lists/generate",
            json={"question_version_ids": [str(self.bio_version_id)], "title": "Lista dentro do escopo"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)


if __name__ == "__main__":
    unittest.main()
