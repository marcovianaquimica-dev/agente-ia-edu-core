"""Service-layer coverage for src/agente_ia_edu/services/knowledge.py.

Wave 6 (background bug-hunt) - closes the gaps left after the earlier
route-layer sweeps: the visibility predicates (`_is_question_visible`,
`_is_resource_visible`, `_scope_values`) are pure/static so they are unit
tested directly without a DB round trip; the query methods
(`find_questions_by_content`, `find_questions_by_difficulty`,
`find_resources_by_content`, `link_question_to_content`,
`link_resource_to_content`) are tested against a real async SQLite session
with `expire_on_commit=True` (production's actual default), matching the
fixture pattern used in tests/test_assessments_route_coverage_http.py.
"""

import asyncio
import unittest

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
)
from agente_ia_edu.services.knowledge import KnowledgeService


# ---------------------------------------------------------------------------
# 1. _scope_values - pure staticmethod, no DB needed
# ---------------------------------------------------------------------------


class ScopeValuesTests(unittest.TestCase):
    def test_none_returns_empty_tuple(self):
        self.assertEqual(KnowledgeService._scope_values(None), ())

    def test_single_string_wrapped_in_tuple(self):
        self.assertEqual(KnowledgeService._scope_values("CLASS-A"), ("CLASS-A",))

    def test_tuple_input_filters_falsy_entries(self):
        # This is the one branch (line 55) not exercised by any caller today:
        # requester_scope_external_id passed as a tuple with an empty entry.
        result = KnowledgeService._scope_values(("CLASS-A", "", None, "CLASS-B"))
        self.assertEqual(result, ("CLASS-A", "CLASS-B"))


# ---------------------------------------------------------------------------
# 2. _is_question_visible - pure staticmethod, no DB needed.
#
# Existing tests only ever exercised the PUBLIC branch and the legacy
# no-owner/no-school PLATFORM shortcut. The PRIVATE / SCHOOL / CLASSROOM
# branches (lines 76-106) were entirely uncovered.
# ---------------------------------------------------------------------------


def _question(**overrides):
    defaults = dict(
        visibility_scope="PUBLIC",
        school_id=None,
        owner_external_id=None,
        origin_type="USER_CREATED",  # deliberately NOT in the legacy shortcut set
        metadata_=None,
    )
    defaults.update(overrides)
    return Question(**defaults)


class IsQuestionVisibleTests(unittest.TestCase):
    def test_public_is_always_visible(self):
        q = _question(visibility_scope="PUBLIC")
        self.assertTrue(KnowledgeService._is_question_visible(q))

    def test_legacy_platform_question_with_no_owner_is_visible(self):
        q = _question(visibility_scope="SCHOOL", origin_type="PLATFORM", school_id=None, owner_external_id=None)
        self.assertTrue(KnowledgeService._is_question_visible(q))

    def test_private_owner_match_is_visible(self):
        q = _question(visibility_scope="PRIVATE", owner_external_id="teacher-1")
        self.assertTrue(
            KnowledgeService._is_question_visible(q, requester_institution_id="teacher-1")
        )

    def test_private_school_match_is_visible(self):
        q = _question(visibility_scope="PRIVATE", school_id="11111111-1111-1111-1111-111111111111")
        self.assertTrue(
            KnowledgeService._is_question_visible(
                q, requester_institution_id="11111111-1111-1111-1111-111111111111"
            )
        )

    def test_private_no_owner_no_school_is_visible(self):
        q = _question(visibility_scope="PRIVATE", school_id=None, owner_external_id=None)
        self.assertTrue(KnowledgeService._is_question_visible(q))

    def test_private_owner_mismatch_is_not_visible(self):
        q = _question(visibility_scope="PRIVATE", owner_external_id="teacher-1")
        self.assertFalse(
            KnowledgeService._is_question_visible(q, requester_institution_id="teacher-2")
        )

    def test_school_scoped_with_no_school_id_is_not_public_shortcut(self):
        # visibility_scope="SCHOOL" (not PRIVATE), school_id is None, origin_type
        # is not one of the legacy shortcut values -> falls through to
        # `question.origin_type == "PLATFORM" and question.visibility_scope == "PUBLIC"`
        # which must be False here.
        q = _question(visibility_scope="SCHOOL", school_id=None, origin_type="USER_CREATED")
        self.assertFalse(KnowledgeService._is_question_visible(q))

    def test_school_scoped_no_requester_institution_is_not_visible(self):
        q = _question(visibility_scope="SCHOOL", school_id="22222222-2222-2222-2222-222222222222")
        self.assertFalse(KnowledgeService._is_question_visible(q, requester_institution_id=None))

    def test_school_scoped_mismatched_school_is_not_visible(self):
        q = _question(visibility_scope="SCHOOL", school_id="22222222-2222-2222-2222-222222222222")
        self.assertFalse(
            KnowledgeService._is_question_visible(q, requester_institution_id="other-school")
        )

    def test_school_scoped_matching_school_is_visible(self):
        q = _question(visibility_scope="SCHOOL", school_id="22222222-2222-2222-2222-222222222222")
        self.assertTrue(
            KnowledgeService._is_question_visible(
                q, requester_institution_id="22222222-2222-2222-2222-222222222222"
            )
        )

    def test_classroom_scoped_matching_classroom_is_visible(self):
        q = _question(
            visibility_scope="CLASSROOM",
            school_id="22222222-2222-2222-2222-222222222222",
            metadata_={"classroom_id": "CLASS-A"},
        )
        self.assertTrue(
            KnowledgeService._is_question_visible(
                q,
                requester_institution_id="22222222-2222-2222-2222-222222222222",
                requester_scope_type="classroom",
                requester_scope_external_id="CLASS-A",
            )
        )

    def test_classroom_scoped_non_matching_classroom_is_not_visible(self):
        q = _question(
            visibility_scope="CLASSROOM",
            school_id="22222222-2222-2222-2222-222222222222",
            metadata_={"classroom_id": "CLASS-A"},
        )
        self.assertFalse(
            KnowledgeService._is_question_visible(
                q,
                requester_institution_id="22222222-2222-2222-2222-222222222222",
                requester_scope_type="CLASSROOM",
                requester_scope_external_id="CLASS-B",
            )
        )

    def test_classroom_scoped_wrong_requester_scope_type_is_not_visible(self):
        q = _question(
            visibility_scope="CLASSROOM",
            school_id="22222222-2222-2222-2222-222222222222",
            metadata_={"classroom_id": "CLASS-A"},
        )
        self.assertFalse(
            KnowledgeService._is_question_visible(
                q,
                requester_institution_id="22222222-2222-2222-2222-222222222222",
                requester_scope_type="SCHOOL",
                requester_scope_external_id="CLASS-A",
            )
        )

    def test_unknown_visibility_scope_falls_through_to_false(self):
        # Same school, but a visibility_scope value that is none of
        # PUBLIC/PRIVATE/SCHOOL/CLASSROOM -> the final `return False`.
        q = _question(visibility_scope="WEIRD", school_id="22222222-2222-2222-2222-222222222222")
        self.assertFalse(
            KnowledgeService._is_question_visible(
                q, requester_institution_id="22222222-2222-2222-2222-222222222222"
            )
        )


# ---------------------------------------------------------------------------
# 3. _is_resource_visible - pure staticmethod, no DB needed.
# ---------------------------------------------------------------------------


def _resource(**overrides):
    defaults = dict(
        visibility_scope="PRIVATE",
        origin_type="AUTHOR",
        owner_external_id=None,
    )
    defaults.update(overrides)
    return EducationalResource(**defaults)


class _Grant:
    """Lightweight stand-in with the two attributes _is_resource_visible reads."""

    def __init__(self, grantee_type, grantee_external_id):
        self.grantee_type = grantee_type
        self.grantee_external_id = grantee_external_id


class IsResourceVisibleTests(unittest.TestCase):
    def test_public_or_shared_always_visible(self):
        self.assertTrue(KnowledgeService._is_resource_visible(_resource(visibility_scope="PUBLIC")))
        self.assertTrue(KnowledgeService._is_resource_visible(_resource(visibility_scope="SHARED")))

    def test_platform_origin_always_visible(self):
        self.assertTrue(KnowledgeService._is_resource_visible(_resource(origin_type="PLATFORM")))

    def test_owner_matches_requester_scope_tuple(self):
        # Line 533: owner_external_id present in requester_scope_external_id
        # (a tuple of scope ids), but requester_institution_id itself does
        # NOT match the owner - only the scope-tuple branch can return True.
        res = _resource(visibility_scope="SCHOOL", owner_external_id="school-9")
        self.assertTrue(
            KnowledgeService._is_resource_visible(
                res,
                requester_institution_id="different-school",
                requester_scope_external_id=("CLASS-A", "school-9"),
            )
        )

    def test_grants_as_non_list_iterable_is_converted(self):
        # Lines 537-540: __dict__["access_grants"] can be any iterable a
        # caller assembled from a query result (e.g. a tuple), not just the
        # ORM's InstrumentedList - the defensive `list(grants)` conversion
        # exists specifically for this.
        res = _resource(visibility_scope="SCHOOL", owner_external_id=None)
        res.__dict__["access_grants"] = (_Grant("SCHOOL", "school-9"),)
        self.assertTrue(
            KnowledgeService._is_resource_visible(res, requester_institution_id="school-9")
        )

    def test_grants_as_uniterable_object_falls_back_to_empty(self):
        # list(grants) raising -> grants = [] (still lines 537-540).
        res = _resource(visibility_scope="SCHOOL", owner_external_id=None)
        res.__dict__["access_grants"] = object()
        self.assertFalse(
            KnowledgeService._is_resource_visible(res, requester_institution_id="school-9")
        )

    def test_grant_institution_type_matches_requester_school(self):
        # Lines 548-549.
        res = _resource(visibility_scope="SCHOOL", owner_external_id=None)
        res.__dict__["access_grants"] = [_Grant("INSTITUTION", "school-9")]
        self.assertTrue(
            KnowledgeService._is_resource_visible(res, requester_institution_id="school-9")
        )

    def test_grant_type_matches_requester_scope_type(self):
        # Line 557: grant_type == requester_scope (upper-cased) AND the
        # grant_id is itself present in requester_scope_ids. Must use a
        # grantee_type that is NOT already covered by the broader
        # {CLASSROOM, GRADE_LEVEL, SEGMENT, UNIT, SCHOOL, INSTITUTION} set
        # checked just above (lines 551-553), otherwise that earlier branch
        # would return True first and line 557 would stay unreached -
        # EXTERNAL_IDENTITY is a valid grantee_type (see the DB
        # CheckConstraint) that falls outside that set.
        res = _resource(visibility_scope="SCHOOL", owner_external_id=None)
        res.__dict__["access_grants"] = [_Grant("EXTERNAL_IDENTITY", "student-42")]
        self.assertTrue(
            KnowledgeService._is_resource_visible(
                res,
                requester_scope_type="EXTERNAL_IDENTITY",
                requester_scope_external_id=("student-42",),
            )
        )

    def test_no_match_anywhere_is_not_visible(self):
        res = _resource(visibility_scope="SCHOOL", owner_external_id="school-9")
        self.assertFalse(
            KnowledgeService._is_resource_visible(res, requester_institution_id="school-other")
        )


# ---------------------------------------------------------------------------
# 4. Query methods against a real async session (expire_on_commit=True, as in
#    production's create_session_factory()).
# ---------------------------------------------------------------------------


class KnowledgeServiceQueryTests(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession)
            return engine, factory

        self.engine, self.session_factory = asyncio.run(setup())

    def tearDown(self):
        asyncio.run(self.engine.dispose())

    def test_classification_match_filters_out_invisible_question(self):
        # Line 179: a PedagogicalClassification whose question is PRIVATE and
        # owned by someone other than the requester must be skipped, not
        # returned.
        async def run():
            async with self.session_factory() as session:
                q = Question(
                    validation_status="approved",
                    visibility_scope="PRIVATE",
                    owner_external_id="teacher-owner",
                    origin_type="TEACHER",
                )
                session.add(q)
                await session.flush()
                qv = QuestionVersion(
                    question_id=q.id,
                    version_kind="official_original",
                    canonical_text="Sobre Cinetica Quimica",
                    statement="Sobre Cinetica Quimica",
                    content_hash="h-cinetica",
                )
                session.add(qv)
                await session.flush()
                cls = PedagogicalClassification(
                    question_version_id=qv.id,
                    discipline="Quimica",
                    content="Cinetica Quimica",
                    subcontent="Velocidade de Reacao",
                    difficulty="MEDIUM",
                    reasoning_type="conceptual",
                    status="CLASSIFIED",
                    lifecycle="ACTIVE",
                )
                session.add(cls)
                await session.commit()

                service = KnowledgeService(session)
                results = await service.find_questions_by_content(
                    "Cinetica", requester_institution_id="someone-else"
                )
                return results

        results = asyncio.run(run())
        self.assertEqual(results, [])

    def test_catalog_link_merges_multiple_matched_nodes_onto_one_row(self):
        # Lines 252-254: the same question_version linked to TWO catalog
        # nodes that both match the search term must appear ONCE with both
        # node ids collected, not as two separate rows (or with one node
        # silently dropped by row ordering).
        async def run():
            async with self.session_factory() as session:
                root = CatalogNode(node_type="DISCIPLINE", name="Quimica Root", position=1, active=True)
                session.add(root)
                await session.flush()
                root.root_id = root.id

                node_a = CatalogNode(
                    parent_id=root.id, root_id=root.id, node_type="CONTENT",
                    code="TERMO-A", name="Termoquimica A", position=1, active=True,
                )
                node_b = CatalogNode(
                    parent_id=root.id, root_id=root.id, node_type="CONTENT",
                    code="TERMO-B", name="Termoquimica B", position=2, active=True,
                )
                session.add_all([node_a, node_b])
                await session.flush()

                q = Question(validation_status="approved", visibility_scope="PUBLIC")
                session.add(q)
                await session.flush()
                qv = QuestionVersion(
                    question_id=q.id, version_kind="official_original",
                    canonical_text="Termoquimica pergunta", statement="Termoquimica pergunta",
                    content_hash="h-termo",
                )
                session.add(qv)
                await session.flush()

                link_a = ContentQuestionLink(content_node_id=node_a.id, question_version_id=qv.id)
                link_b = ContentQuestionLink(content_node_id=node_b.id, question_version_id=qv.id)
                session.add_all([link_a, link_b])
                await session.commit()

                service = KnowledgeService(session)
                return await service.find_questions_by_content("Termoquimica")

        results = asyncio.run(run())
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]["gate_content_node_ids"]), 2)

    def test_catalog_link_skips_invisible_question(self):
        # Line 264.
        async def run():
            async with self.session_factory() as session:
                root = CatalogNode(node_type="DISCIPLINE", name="Fisica Root", position=1, active=True)
                session.add(root)
                await session.flush()
                root.root_id = root.id
                node = CatalogNode(
                    parent_id=root.id, root_id=root.id, node_type="CONTENT",
                    code="OTICA", name="Otica Geometrica", position=1, active=True,
                )
                session.add(node)
                await session.flush()

                q = Question(
                    validation_status="approved",
                    visibility_scope="PRIVATE",
                    owner_external_id="teacher-owner",
                    origin_type="TEACHER",
                )
                session.add(q)
                await session.flush()
                qv = QuestionVersion(
                    question_id=q.id, version_kind="official_original",
                    canonical_text="Otica Geometrica pergunta", statement="Otica Geometrica pergunta",
                    content_hash="h-otica",
                )
                session.add(qv)
                await session.flush()
                link = ContentQuestionLink(content_node_id=node.id, question_version_id=qv.id)
                session.add(link)
                await session.commit()

                service = KnowledgeService(session)
                return await service.find_questions_by_content(
                    "Otica", requester_institution_id="someone-else"
                )

        results = asyncio.run(run())
        self.assertEqual(results, [])

    def test_find_questions_by_difficulty_without_content_filter(self):
        # Lines 306-321: no existing test called this branch at all.
        async def run():
            async with self.session_factory() as session:
                q_easy = Question(validation_status="approved", visibility_scope="PUBLIC")
                q_hard = Question(validation_status="approved", visibility_scope="PUBLIC")
                session.add_all([q_easy, q_hard])
                await session.flush()
                qv_easy = QuestionVersion(
                    question_id=q_easy.id, version_kind="official_original",
                    canonical_text="Facil", statement="Facil", content_hash="h-facil",
                    recommended_difficulty="EASY",
                )
                qv_hard = QuestionVersion(
                    question_id=q_hard.id, version_kind="official_original",
                    canonical_text="Dificil", statement="Dificil", content_hash="h-dificil",
                    recommended_difficulty="HARD",
                )
                session.add_all([qv_easy, qv_hard])
                await session.commit()

                service = KnowledgeService(session)
                return await service.find_questions_by_difficulty("hard")

        results = asyncio.run(run())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["difficulty_learning_level"], "HARD")

    def test_find_questions_by_difficulty_delegates_when_content_given(self):
        async def run():
            async with self.session_factory() as session:
                service = KnowledgeService(session)
                return await service.find_questions_by_difficulty(
                    "easy", content_name_or_code="anything"
                )

        # No matching content -> empty list, but exercises the delegation branch.
        results = asyncio.run(run())
        self.assertEqual(results, [])

    def test_find_resources_by_content_merges_multiple_matched_nodes_onto_one_row(self):
        # Lines 393-395: same resource linked to two matching catalog nodes
        # must appear once with both node ids collected (mirrors the
        # question-side dedup test above).
        async def run():
            async with self.session_factory() as session:
                root = CatalogNode(node_type="DISCIPLINE", name="Historia Root", position=1, active=True)
                session.add(root)
                await session.flush()
                root.root_id = root.id
                node_a = CatalogNode(
                    parent_id=root.id, root_id=root.id, node_type="CONTENT",
                    code="REV-A", name="Revolucao Francesa A", position=1, active=True,
                )
                node_b = CatalogNode(
                    parent_id=root.id, root_id=root.id, node_type="CONTENT",
                    code="REV-B", name="Revolucao Francesa B", position=2, active=True,
                )
                session.add_all([node_a, node_b])
                await session.flush()

                res = EducationalResource(
                    title="PDF Revolucao Francesa", resource_type="PDF", origin_type="PLATFORM",
                    visibility_scope="PUBLIC",
                )
                session.add(res)
                await session.flush()
                session.add_all([
                    ContentResourceLink(content_node_id=node_a.id, resource_id=res.id, pedagogical_role="THEORY"),
                    ContentResourceLink(content_node_id=node_b.id, resource_id=res.id, pedagogical_role="THEORY"),
                ])
                await session.commit()

                service = KnowledgeService(session)
                return await service.find_resources_by_content("Revolucao Francesa")

        results = asyncio.run(run())
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]["gate_content_node_ids"]), 2)

    def test_find_resources_by_content_no_matching_nodes_returns_empty(self):
        async def run():
            async with self.session_factory() as session:
                service = KnowledgeService(session)
                return await service.find_resources_by_content("Nao Existe Nada Assim")

        self.assertEqual(asyncio.run(run()), [])

    def test_find_resources_by_content_filters_by_resource_type(self):
        async def run():
            async with self.session_factory() as session:
                root = CatalogNode(node_type="DISCIPLINE", name="Biologia Root", position=1, active=True)
                session.add(root)
                await session.flush()
                root.root_id = root.id
                node = CatalogNode(
                    parent_id=root.id, root_id=root.id, node_type="CONTENT",
                    code="GENETICA", name="Genetica Mendeliana", position=1, active=True,
                )
                session.add(node)
                await session.flush()

                video = EducationalResource(
                    title="Video Genetica", resource_type="VIDEO", origin_type="PLATFORM",
                    visibility_scope="PUBLIC",
                )
                pdf = EducationalResource(
                    title="PDF Genetica", resource_type="PDF", origin_type="PLATFORM",
                    visibility_scope="PUBLIC",
                )
                session.add_all([video, pdf])
                await session.flush()
                session.add_all([
                    ContentResourceLink(content_node_id=node.id, resource_id=video.id, pedagogical_role="THEORY"),
                    ContentResourceLink(content_node_id=node.id, resource_id=pdf.id, pedagogical_role="THEORY"),
                ])
                await session.commit()

                service = KnowledgeService(session)
                return await service.find_resources_by_content("Genetica", resource_type="video")

        results = asyncio.run(run())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["resource_type"], "VIDEO")

    def test_link_question_to_content_is_idempotent(self):
        # Line 451: a second call with the same pair must return the
        # existing link, not create (or attempt to create) a duplicate.
        async def run():
            async with self.session_factory() as session:
                root = CatalogNode(node_type="DISCIPLINE", name="Root", position=1, active=True)
                session.add(root)
                await session.flush()
                root.root_id = root.id
                node = CatalogNode(
                    parent_id=root.id, root_id=root.id, node_type="CONTENT",
                    code="X", name="X", position=1, active=True,
                )
                session.add(node)
                await session.flush()
                q = Question(validation_status="approved", visibility_scope="PUBLIC")
                session.add(q)
                await session.flush()
                qv = QuestionVersion(
                    question_id=q.id, version_kind="official_original",
                    canonical_text="X", statement="X", content_hash="h-x",
                )
                session.add(qv)
                await session.flush()
                qv_id, node_id = qv.id, node.id
                await session.commit()

                service = KnowledgeService(session)
                first = await service.link_question_to_content(qv_id, node_id)
                second = await service.link_question_to_content(qv_id, node_id)
                return first.id, second.id

        first_id, second_id = asyncio.run(run())
        self.assertEqual(first_id, second_id)

    def test_link_resource_to_content_is_idempotent(self):
        # Line 479.
        async def run():
            async with self.session_factory() as session:
                root = CatalogNode(node_type="DISCIPLINE", name="Root2", position=1, active=True)
                session.add(root)
                await session.flush()
                root.root_id = root.id
                node = CatalogNode(
                    parent_id=root.id, root_id=root.id, node_type="CONTENT",
                    code="Y", name="Y", position=1, active=True,
                )
                session.add(node)
                await session.flush()
                res = EducationalResource(
                    title="Res Y", resource_type="PDF", origin_type="PLATFORM",
                    visibility_scope="PUBLIC",
                )
                session.add(res)
                await session.flush()
                res_id, node_id = res.id, node.id
                await session.commit()

                service = KnowledgeService(session)
                first = await service.link_resource_to_content(res_id, node_id)
                second = await service.link_resource_to_content(res_id, node_id)
                return first.id, second.id

        first_id, second_id = asyncio.run(run())
        self.assertEqual(first_id, second_id)


if __name__ == "__main__":
    unittest.main()
