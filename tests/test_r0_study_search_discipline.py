"""The gate applied to study search, through the institution_id it already takes."""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    ContentQuestionLink,
    ContentResourceLink,
    EducationalResource,
    Question,
    QuestionVersion,
)
from agente_ia_edu.db.models.pedagogical_universe import (
    PedagogicalUniverse,
    PedagogicalUniverseCatalogScope,
)
from agente_ia_edu.services.study_search import StudySearchService
from tests.test_question_bank_core import _Fixture

SCHOOL = str(uuid.uuid4())


async def _scope_school_to(session, node, external_id: str) -> None:
    """Give SCHOOL an active universe restricted to ``node`` and its descendants."""
    universe = PedagogicalUniverse(
        id=uuid.uuid4(), external_id=external_id, slug=external_id, name=external_id,
        owner_type="SCHOOL", owner_external_id=SCHOOL, status="ACTIVE",
    )
    session.add(universe)
    await session.flush()
    session.add(
        PedagogicalUniverseCatalogScope(
            id=uuid.uuid4(), universe_id=universe.id,
            catalog_node_id=node.id, scope_kind="DISCIPLINE",
            include_descendants=True,
        )
    )
    await session.commit()


async def _link_question_to(session, node, marker: str) -> str:
    """A question reachable ONLY through ``ContentQuestionLink``.

    No ``PedagogicalClassification`` row, so ``find_questions_by_content``
    cannot reach it through the classification path and returns it with
    ``source_type="catalog_link"`` - the projection that used to throw the
    node away. ``recommended_difficulty`` matches what the tests search with,
    because the catalog-link query filters on that column.
    """
    question = Question(
        validation_status="validated", origin_type="IMPORTED",
        status="PUBLISHED", visibility_scope="PUBLIC",
    )
    session.add(question)
    await session.flush()
    version = QuestionVersion(
        question_id=question.id, version_kind="official_original",
        canonical_text=f"Questao ligada por catalogo {marker}.",
        statement=f"Questao ligada por catalogo {marker}.",
        content_hash=f"h-link-{marker}", is_immutable=True,
        recommended_difficulty="UNKNOWN",
    )
    session.add(version)
    await session.flush()
    session.add(
        ContentQuestionLink(
            id=uuid.uuid4(), content_node_id=node.id, question_version_id=version.id
        )
    )
    await session.commit()
    return str(version.id)


async def _link_material_to(session, node, marker: str) -> str:
    """A PUBLIC material linked to ``node``; visibility never hides it by itself."""
    resource = EducationalResource(
        id=uuid.uuid4(), title=f"Material {marker}", resource_type="THEORY_MATERIAL",
        origin_type="PLATFORM", status="active", visibility_scope="PUBLIC",
    )
    session.add(resource)
    await session.flush()
    session.add(
        ContentResourceLink(
            id=uuid.uuid4(), content_node_id=node.id, resource_id=resource.id,
            pedagogical_role="THEORY",
        )
    )
    await session.commit()
    return str(resource.id)


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


class StudySearchCatalogLinkAndMaterialTests(unittest.IsolatedAsyncioTestCase):
    """The two paths that carried no classification and so leaked past the gate.

    A ``catalog_link`` question and a material are both *classified* content -
    the classification is the catalog node their link was selected by - but
    neither projection used to say so, and the gate read that silence as
    "unclassified", which it permits on purpose.
    """

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

    async def test_catalog_linked_question_from_another_discipline_is_filtered(self):
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            maths = seeded["nodes"]["math_content"]
            linked_id = await _link_question_to(session, maths, "math")
            await _scope_school_to(session, seeded["nodes"]["bio_content"], "u-link")

            # ``resolve_context`` defaults difficulty to MEDIUM and every
            # fixture row stores UNKNOWN, so without this the unrestricted half
            # comes back empty and the restricted assertion proves nothing.
            unrestricted = await StudySearchService.search(
                maths.code, session=session, difficulty="UNKNOWN", institution_id=None
            )
            restricted = await StudySearchService.search(
                maths.code, session=session, difficulty="UNKNOWN", institution_id=SCHOOL
            )

        unrestricted_ids = [q["id"] for q in unrestricted["results"]["questions"]]
        restricted_ids = [q["id"] for q in restricted["results"]["questions"]]
        self.assertIn(
            linked_id, unrestricted_ids,
            "the catalog-linked question must be reachable at all when unrestricted",
        )
        self.assertNotIn(
            linked_id, restricted_ids,
            "a catalog-linked mathematics question must not reach a biology-scoped school",
        )

    async def test_catalog_linked_question_of_its_own_discipline_survives(self):
        """The gate must restrict, not merely subtract."""
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            biology = seeded["nodes"]["bio_content"]
            linked_id = await _link_question_to(session, biology, "bio")
            await _scope_school_to(session, biology, "u-link-own")

            restricted = await StudySearchService.search(
                biology.code, session=session, difficulty="UNKNOWN", institution_id=SCHOOL
            )

        self.assertIn(
            linked_id, [q["id"] for q in restricted["results"]["questions"]],
            "a biology catalog link must survive a biology scope",
        )

    async def test_material_from_another_discipline_is_filtered(self):
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            maths = seeded["nodes"]["math_content"]
            material_id = await _link_material_to(session, maths, "math")
            await _scope_school_to(session, seeded["nodes"]["bio_content"], "u-mat")

            unrestricted = await StudySearchService.search(
                maths.code, session=session, difficulty="UNKNOWN", institution_id=None
            )
            restricted = await StudySearchService.search(
                maths.code, session=session, difficulty="UNKNOWN", institution_id=SCHOOL
            )

        unrestricted_ids = [m["id"] for m in unrestricted["results"]["materials"]]
        restricted_ids = [m["id"] for m in restricted["results"]["materials"]]
        self.assertIn(
            material_id, unrestricted_ids,
            "the material must be reachable at all when unrestricted",
        )
        self.assertNotIn(
            material_id, restricted_ids,
            "a mathematics material must not reach a biology-scoped school",
        )

    async def test_material_of_its_own_discipline_survives(self):
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            biology = seeded["nodes"]["bio_content"]
            material_id = await _link_material_to(session, biology, "bio")
            await _scope_school_to(session, biology, "u-mat-own")

            restricted = await StudySearchService.search(
                biology.code, session=session, difficulty="UNKNOWN", institution_id=SCHOOL
            )

        self.assertIn(
            material_id, [m["id"] for m in restricted["results"]["materials"]],
            "a biology material must survive a biology scope",
        )


if __name__ == "__main__":
    unittest.main()
