"""The gate applied to study search, through the institution_id it already takes."""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
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
from agente_ia_edu.services.discipline_gate import DisciplineScope
from agente_ia_edu.services.study_search import (
    StudySearchService,
    _as_node_id,
    _as_node_ids,
)
from tests.test_question_bank_core import _Fixture

SCHOOL = str(uuid.uuid4())


async def _scope_school_to(session, node, external_id: str, school_id: str = SCHOOL) -> None:
    """Give a school an active universe restricted to ``node`` and its descendants."""
    universe = PedagogicalUniverse(
        id=uuid.uuid4(), external_id=external_id, slug=external_id, name=external_id,
        owner_type="SCHOOL", owner_external_id=school_id, status="ACTIVE",
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
        """Absence, first shape - asserted on real content, not on payload shape.

        ``assertIn("resolved_context", ...)`` alone would still pass with every
        result filtered away, which is the failure this whole phase is meant to
        avoid. The assertion is that a specific seeded question and a specific
        seeded material actually come back.
        """
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            maths = seeded["nodes"]["math_content"]
            question_id = await _link_question_to(session, maths, "no-universe")
            material_id = await _link_material_to(session, maths, "no-universe")

            result = await StudySearchService.search(
                maths.code, session=session, difficulty="UNKNOWN", institution_id=SCHOOL
            )

        self.assertIn("resolved_context", result)
        self.assertIn(
            question_id, [q["id"] for q in result["results"]["questions"]],
            "a school with no universe must still see this question",
        )
        self.assertIn(
            material_id, [m["id"] for m in result["results"]["materials"]],
            "a school with no universe must still see this material",
        )


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


class StudySearchMultiLinkedRowTests(unittest.IsolatedAsyncioTestCase):
    """One row, two disciplines: it belongs to both, so either school gets it.

    ``find_questions_by_content`` and ``find_resources_by_content`` project one
    row per question/resource, and the link query they build it from is
    unordered. Carrying a single node would let row order decide which
    discipline the row appears to belong to, and deny it to the other - a
    non-deterministic denial, the expensive direction of failure.
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

    async def _two_nodes(self, session, seeded):
        """A node in each discipline, sharing a token no fixture node carries."""
        made = []
        for parent, code in (
            (seeded["nodes"]["math_content"], "MATH-ALGEBRA-MULTILINK"),
            (seeded["nodes"]["bio_content"], "BIOLOGY-ANIMAL-MULTILINK"),
        ):
            node = CatalogNode(
                code=code, name=code, node_type="CONTENT",
                parent_id=parent.id, root_id=parent.root_id, active=True,
            )
            session.add(node)
            made.append(node)
        await session.flush()
        await session.commit()
        return made

    async def test_resource_linked_to_two_disciplines_reaches_either_school(self):
        math_school = str(uuid.uuid4())
        bio_school = str(uuid.uuid4())

        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            math_node, bio_node = await self._two_nodes(session, seeded)

            resource = EducationalResource(
                id=uuid.uuid4(), title="Material de duas disciplinas",
                resource_type="THEORY_MATERIAL", origin_type="PLATFORM",
                status="active", visibility_scope="PUBLIC",
            )
            session.add(resource)
            await session.flush()
            for node in (math_node, bio_node):
                session.add(
                    ContentResourceLink(
                        id=uuid.uuid4(), content_node_id=node.id,
                        resource_id=resource.id, pedagogical_role="THEORY",
                    )
                )
            await session.commit()
            material_id = str(resource.id)

            await _scope_school_to(session, math_node, "u-multi-math", math_school)
            await _scope_school_to(session, bio_node, "u-multi-bio", bio_school)

            for_math = await StudySearchService.search(
                "MULTILINK", session=session, difficulty="UNKNOWN",
                institution_id=math_school,
            )
            for_bio = await StudySearchService.search(
                "MULTILINK", session=session, difficulty="UNKNOWN",
                institution_id=bio_school,
            )

        self.assertIn(
            material_id, [m["id"] for m in for_math["results"]["materials"]],
            "a resource also linked to biology must still reach a mathematics school",
        )
        self.assertIn(
            material_id, [m["id"] for m in for_bio["results"]["materials"]],
            "a resource also linked to mathematics must still reach a biology school",
        )

    async def test_question_linked_to_two_disciplines_reaches_either_school(self):
        """The same rule on the ``catalog_link`` half, which dedupes the same way."""
        math_school = str(uuid.uuid4())
        bio_school = str(uuid.uuid4())

        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            math_node, bio_node = await self._two_nodes(session, seeded)

            question = Question(
                validation_status="validated", origin_type="IMPORTED",
                status="PUBLISHED", visibility_scope="PUBLIC",
            )
            session.add(question)
            await session.flush()
            version = QuestionVersion(
                question_id=question.id, version_kind="official_original",
                canonical_text="Questao de duas disciplinas.",
                statement="Questao de duas disciplinas.",
                content_hash="h-multilink", is_immutable=True,
                recommended_difficulty="UNKNOWN",
            )
            session.add(version)
            await session.flush()
            for node in (math_node, bio_node):
                session.add(
                    ContentQuestionLink(
                        id=uuid.uuid4(), content_node_id=node.id,
                        question_version_id=version.id,
                    )
                )
            await session.commit()
            question_id = str(version.id)

            await _scope_school_to(session, math_node, "u-multiq-math", math_school)
            await _scope_school_to(session, bio_node, "u-multiq-bio", bio_school)

            for_math = await StudySearchService.search(
                "MULTILINK", session=session, difficulty="UNKNOWN",
                institution_id=math_school,
            )
            for_bio = await StudySearchService.search(
                "MULTILINK", session=session, difficulty="UNKNOWN",
                institution_id=bio_school,
            )

        self.assertIn(
            question_id, [q["id"] for q in for_math["results"]["questions"]],
            "a question also linked to biology must still reach a mathematics school",
        )
        self.assertIn(
            question_id, [q["id"] for q in for_bio["results"]["questions"]],
            "a question also linked to mathematics must still reach a biology school",
        )

    async def test_a_row_of_a_third_discipline_is_still_denied(self):
        """The permissive ``any`` must not turn the gate into a no-op."""
        math_school = str(uuid.uuid4())

        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            math_node, bio_node = await self._two_nodes(session, seeded)
            bio_only = str(uuid.uuid4())
            resource = EducationalResource(
                id=uuid.uuid4(), title="Material so de biologia",
                resource_type="THEORY_MATERIAL", origin_type="PLATFORM",
                status="active", visibility_scope="PUBLIC",
            )
            session.add(resource)
            await session.flush()
            session.add(
                ContentResourceLink(
                    id=uuid.uuid4(), content_node_id=bio_node.id,
                    resource_id=resource.id, pedagogical_role="THEORY",
                )
            )
            await session.commit()
            bio_only = str(resource.id)

            await _scope_school_to(session, math_node, "u-third", math_school)

            for_math = await StudySearchService.search(
                "MULTILINK", session=session, difficulty="UNKNOWN",
                institution_id=math_school,
            )

        self.assertNotIn(
            bio_only, [m["id"] for m in for_math["results"]["materials"]],
            "a resource linked only to biology must not reach a mathematics school",
        )


class NodeIdCoercionTests(unittest.TestCase):
    """A malformed id must read as absent, never as a denial.

    ``_as_node_id`` returning ``None`` is what makes the caller fall back to the
    classification code and, failing that, to the absence rule. Nothing asserted
    that before, so the permissive direction of the fallback was unprotected.
    """

    def test_unusable_values_are_absent_not_denied(self):
        for raw in (None, "", "   ", "not-a-uuid", 17, object()):
            with self.subTest(raw=raw):
                self.assertIsNone(_as_node_id(raw))

    def test_usable_values_are_coerced(self):
        node_id = uuid.uuid4()
        self.assertEqual(_as_node_id(node_id), node_id)
        self.assertEqual(_as_node_id(str(node_id)), node_id)

    def test_the_list_form_drops_unusable_entries_and_deduplicates(self):
        node_id = uuid.uuid4()
        other = uuid.uuid4()
        self.assertEqual(_as_node_ids([]), [])
        self.assertEqual(_as_node_ids(None), [])
        self.assertEqual(_as_node_ids(["", "   ", "nope"]), [])
        self.assertEqual(
            _as_node_ids([str(node_id), "nope", str(node_id), other]),
            [node_id, other],
        )

    def test_a_row_with_only_malformed_ids_is_permitted_by_a_restricted_scope(self):
        """End of the chain: absence is permission, so the row survives."""
        scope = DisciplineScope(
            unrestricted=False,
            allowed_node_ids=frozenset({uuid.uuid4()}),
            allowed_codes=frozenset({"SOMETHING-ELSE"}),
        )
        node_ids = _as_node_ids(["", "   ", "not-a-uuid", None])
        self.assertEqual(node_ids, [])
        self.assertTrue(scope.permits_code(None))


if __name__ == "__main__":
    unittest.main()
