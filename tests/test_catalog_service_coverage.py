"""Service-layer coverage for src/agente_ia_edu/services/catalog.py.

Wave 6 (background bug-hunt). Targets branches left uncovered after the
route-layer (api/routes/catalog.py) commit-site audit from an earlier wave:
`grant_access` (entirely untested except the fresh-grant happy path),
`_get_version`'s not-found guard, `reject_version`'s metadata-clearing
branch, `add_section`/`add_block`/`add_exercise`'s guard clauses,
`update_material_fields` (almost entirely untested), `version_overview`
(entirely untested), several `publish_version` branches (re-publish with a
stale resource_id, publishing an unapproved version, an orphaned
material_id, and the idempotent-merge-by-title/owner/origin fallback),
`archive_version`'s invalid-state guard, and
`ContentCatalogQueryService.get_questions_for_content`.

Uses a real async SQLite session with `expire_on_commit=True` (the
production default - see tests/test_assessments_route_coverage_http.py),
capturing ids right after `flush()` rather than reading attributes off an
ORM instance after `commit()`.
"""

import asyncio
import unittest
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    EducationalResource,
    Question,
    QuestionVersion,
    ResourceAccessGrant,
    TheoryMaterial,
    TheoryMaterialVersion,
)
from agente_ia_edu.services.catalog import (
    ContentCatalogQueryService,
    ContentQuestionLinkService,
    EducationalResourceService,
    TheoryMaterialService,
)


class CatalogServiceTests(unittest.TestCase):
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

    async def _make_node(self, session, *, name="Node", code="ND", active=True):
        root = CatalogNode(node_type="DISCIPLINE", name=f"{name} Root", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id
        node = CatalogNode(
            parent_id=root.id, root_id=root.id, node_type="CONTENT",
            code=code, name=name, position=1, active=active,
        )
        session.add(node)
        await session.flush()
        return node

    # -- EducationalResourceService.grant_access -------------------------

    def test_grant_access_resource_not_found(self):
        async def run():
            async with self.session_factory() as session:
                service = EducationalResourceService()
                with self.assertRaises(ValueError):
                    await service.grant_access(
                        session, resource_id=uuid4(), grantee_type="SCHOOL",
                        grantee_external_id="school-1",
                    )

        asyncio.run(run())

    def test_grant_access_invalid_grantee_type(self):
        async def run():
            async with self.session_factory() as session:
                res = EducationalResource(
                    title="R", resource_type="PDF", origin_type="PLATFORM", visibility_scope="PUBLIC",
                )
                session.add(res)
                await session.flush()
                res_id = res.id
                service = EducationalResourceService()
                with self.assertRaises(ValueError):
                    await service.grant_access(
                        session, resource_id=res_id, grantee_type="NOT_A_TYPE",
                        grantee_external_id="school-1",
                    )

        asyncio.run(run())

    def test_grant_access_missing_grantee_external_id(self):
        async def run():
            async with self.session_factory() as session:
                res = EducationalResource(
                    title="R", resource_type="PDF", origin_type="PLATFORM", visibility_scope="PUBLIC",
                )
                session.add(res)
                await session.flush()
                res_id = res.id
                service = EducationalResourceService()
                with self.assertRaises(ValueError):
                    await service.grant_access(
                        session, resource_id=res_id, grantee_type="SCHOOL", grantee_external_id="   ",
                    )

        asyncio.run(run())

    def test_grant_access_existing_grant_is_returned_idempotently(self):
        # Also the regression test for a suspected bug: the original code
        # called `existing.scalar_one_or_none()` TWICE on the same Result
        # object (once in the `if`, once again in the `return`) - a Result
        # can only be consumed once, so the second call would raise
        # ResourceClosedError instead of returning the existing grant.
        async def run():
            async with self.session_factory() as session:
                res = EducationalResource(
                    title="R", resource_type="PDF", origin_type="PLATFORM", visibility_scope="PUBLIC",
                )
                session.add(res)
                await session.flush()
                res_id = res.id
                service = EducationalResourceService()
                first = await service.grant_access(
                    session, resource_id=res_id, grantee_type="school",
                    grantee_external_id=" school-9 ",
                )
                first_id = first.id
                await session.flush()
                second = await service.grant_access(
                    session, resource_id=res_id, grantee_type="school",
                    grantee_external_id="school-9",
                )
                return first_id, second.id

        first_id, second_id = asyncio.run(run())
        self.assertEqual(first_id, second_id)

    # -- TheoryMaterialService._get_version -------------------------------

    def test_submit_for_review_unknown_version_raises(self):
        async def run():
            async with self.session_factory() as session:
                service = TheoryMaterialService()
                with self.assertRaises(ValueError):
                    await service.submit_for_review(session, material_version_id=uuid4())

        asyncio.run(run())

    # -- reject_version clears a stale rejection_reason on re-review ------

    def test_reject_version_without_reason_clears_previous_rejection_metadata(self):
        async def run():
            async with self.session_factory() as session:
                service = TheoryMaterialService()
                material = await service.create_material(session, title="Material Rejeitado")
                version = await service._repository(session).get_latest_version(material.id)
                await service.submit_for_review(session, material_version_id=version.id)
                await service.reject_version(
                    session, material_version_id=version.id, reason="faltam referencias",
                )
                # resubmit, then reject again with NO reason - the stale
                # rejection_reason/rejected_at from the first rejection must
                # be cleared, not left stale on the new rejection.
                await service.submit_for_review(session, material_version_id=version.id)
                rejected_again = await service.reject_version(
                    session, material_version_id=version.id, reason=None,
                )
                return rejected_again.metadata_

        metadata = asyncio.run(run())
        self.assertNotIn("rejection_reason", metadata or {})
        self.assertNotIn("rejected_at", metadata or {})

    # -- add_section with a real content_node_id ---------------------------

    def test_add_section_with_active_content_node(self):
        async def run():
            async with self.session_factory() as session:
                node = await self._make_node(session, name="Secao Node", code="SEC")
                node_id = node.id
                service = TheoryMaterialService()
                material = await service.create_material(session, title="Material Secao")
                version = await service._repository(session).get_latest_version(material.id)
                section = await service.add_section(
                    session, material_version_id=version.id, section_type="INTRODUCTION",
                    position=1, content_node_id=node_id,
                )
                return section.content_node_id, node_id

        section_node_id, node_id = asyncio.run(run())
        self.assertEqual(section_node_id, node_id)

    def test_add_section_with_inactive_content_node_fails(self):
        async def run():
            async with self.session_factory() as session:
                node = await self._make_node(session, name="Inactive Node", code="INA", active=False)
                node_id = node.id
                service = TheoryMaterialService()
                material = await service.create_material(session, title="Material Secao Inativa")
                version = await service._repository(session).get_latest_version(material.id)
                with self.assertRaises(ValueError):
                    await service.add_section(
                        session, material_version_id=version.id, section_type="INTRODUCTION",
                        position=1, content_node_id=node_id,
                    )

        asyncio.run(run())

    # -- add_block with a bogus section_id --------------------------------

    def test_add_block_unknown_section_raises(self):
        async def run():
            async with self.session_factory() as session:
                service = TheoryMaterialService()
                with self.assertRaises(ValueError):
                    await service.add_block(
                        session, section_id=uuid4(), block_type="TEXT", position=1,
                    )

        asyncio.run(run())

    # -- add_exercise duplicate question link ------------------------------

    def test_add_exercise_duplicate_question_version_rejected(self):
        async def run():
            async with self.session_factory() as session:
                q = Question(validation_status="approved")
                session.add(q)
                await session.flush()
                qv = QuestionVersion(
                    question_id=q.id, version_kind="official_original",
                    canonical_text="Q", statement="Q", content_hash=uuid4().hex,
                )
                session.add(qv)
                await session.flush()
                qv_id = qv.id

                service = TheoryMaterialService()
                material = await service.create_material(session, title="Material Dup")
                version = await service._repository(session).get_latest_version(material.id)
                await service.add_exercise(
                    session, material_version_id=version.id, source_type="EXISTING_QUESTION",
                    position=1, question_version_id=qv_id,
                )
                with self.assertRaises(ValueError):
                    await service.add_exercise(
                        session, material_version_id=version.id, source_type="EXISTING_QUESTION",
                        position=2, question_version_id=qv_id,
                    )

        asyncio.run(run())

    # -- update_material_fields --------------------------------------------

    def test_update_material_fields_unknown_material_raises(self):
        async def run():
            async with self.session_factory() as session:
                service = TheoryMaterialService()
                with self.assertRaises(ValueError):
                    await service.update_material_fields(session, material_id=uuid4(), title="X")

        asyncio.run(run())

    def test_update_material_fields_sets_every_field(self):
        async def run():
            async with self.session_factory() as session:
                node = await self._make_node(session, name="Campo Node", code="CMP")
                node_id = node.id
                service = TheoryMaterialService()
                material = await service.create_material(session, title="Original")
                material_id = material.id
                updated = await service.update_material_fields(
                    session,
                    material_id=material_id,
                    title="Novo Titulo",
                    description="Nova Descricao",
                    material_kind="APOSTILA",
                    authoring_source="TEACHER",
                    visibility_scope="school",
                    primary_content_node_id=node_id,
                )
                return (
                    updated.title, updated.description, updated.material_kind,
                    updated.authoring_source, updated.visibility_scope,
                    updated.primary_content_node_id, node_id,
                )

        title, description, kind, source, scope, primary_node, node_id = asyncio.run(run())
        self.assertEqual(title, "Novo Titulo")
        self.assertEqual(description, "Nova Descricao")
        self.assertEqual(kind, "APOSTILA")
        self.assertEqual(source, "TEACHER")
        self.assertEqual(scope, "SCHOOL")
        self.assertEqual(primary_node, node_id)

    def test_update_material_fields_unsets_primary_content_node(self):
        async def run():
            async with self.session_factory() as session:
                node = await self._make_node(session, name="Unset Node", code="UNS")
                service = TheoryMaterialService()
                material = await service.create_material(session, title="Com Node")
                await service.update_material_fields(
                    session, material_id=material.id, primary_content_node_id=node.id,
                )
                updated = await service.update_material_fields(
                    session, material_id=material.id, unset_primary_content_node=True,
                )
                return updated.primary_content_node_id

        result = asyncio.run(run())
        self.assertIsNone(result)

    # -- version_overview ----------------------------------------------------

    def test_version_overview_counts_sections_blocks_and_questions(self):
        async def run():
            async with self.session_factory() as session:
                q = Question(validation_status="approved")
                session.add(q)
                await session.flush()
                qv = QuestionVersion(
                    question_id=q.id, version_kind="official_original",
                    canonical_text="Q", statement="Q", content_hash=uuid4().hex,
                )
                session.add(qv)
                await session.flush()
                qv_id = qv.id

                service = TheoryMaterialService()
                material = await service.create_material(session, title="Overview Material")
                version = await service._repository(session).get_latest_version(material.id)
                version_id = version.id
                section = await service.add_section(
                    session, material_version_id=version_id, section_type="INTRODUCTION", position=1,
                )
                await service.add_block(
                    session, section_id=section.id, block_type="TEXT", position=1,
                )
                await service.add_exercise(
                    session, material_version_id=version_id, source_type="EXISTING_QUESTION",
                    position=1, question_version_id=qv_id,
                )
                await service.add_exercise(
                    session, material_version_id=version_id, source_type="AUTHORED",
                    position=2, authored_text="Texto autoral, sem questao vinculada",
                )
                return await service.version_overview(session, material_version_id=version_id)

        overview = asyncio.run(run())
        self.assertEqual(overview, {"sections": 1, "blocks": 1, "questions": 1})

    # -- publish_version -------------------------------------------------

    async def _approved_version(self, session, *, title="Publicavel"):
        service = TheoryMaterialService()
        material = await service.create_material(session, title=title)
        version = await service._repository(session).get_latest_version(material.id)
        await service.submit_for_review(session, material_version_id=version.id)
        await service.approve_version(session, material_version_id=version.id)
        return service, material, version

    def test_publish_version_rejects_unapproved_status(self):
        async def run():
            async with self.session_factory() as session:
                service = TheoryMaterialService()
                material = await service.create_material(session, title="Nao Aprovado")
                version = await service._repository(session).get_latest_version(material.id)
                with self.assertRaises(ValueError):
                    await service.publish_version(session, material_version_id=version.id)

        asyncio.run(run())

    def test_publish_version_orphaned_material_raises(self):
        # Direct-DB-state test: an AssessmentVersion-style orphan (a version
        # row whose material_id points nowhere) is only reachable by
        # constructing it outside the service, but it is exactly the
        # defensive branch `if material is None: raise` guards against.
        async def run():
            async with self.session_factory() as session:
                orphan_version = TheoryMaterialVersion(
                    material_id=uuid4(), version_number=1, status="APPROVED",
                )
                session.add(orphan_version)
                await session.flush()
                version_id = orphan_version.id
                service = TheoryMaterialService()
                with self.assertRaises(ValueError):
                    await service.publish_version(session, material_version_id=version_id)

        asyncio.run(run())

    def test_publish_version_already_published_without_resource_id_raises(self):
        # Defensive branch: status == PUBLISHED but resource_id somehow
        # unset (a contradiction that should never happen through the
        # service's own API, but the code fails closed on it explicitly).
        async def run():
            async with self.session_factory() as session:
                service, material, version = await self._approved_version(session)
                await service.publish_version(session, material_version_id=version.id)
                # force the contradictory state directly
                fresh = await session.get(TheoryMaterialVersion, version.id)
                fresh.resource_id = None
                await session.flush()
                with self.assertRaises(ValueError):
                    await service.publish_version(session, material_version_id=version.id)

        asyncio.run(run())

    def test_republish_with_existing_resource_id_updates_it(self):
        # Lines ~738-739: version.resource_id already set but status is not
        # PUBLISHED (e.g. sent back through review after a content fix,
        # keeping the same resource binding) - publish_version must reuse
        # (and refresh) that same resource rather than creating a new one.
        async def run():
            async with self.session_factory() as session:
                service, material, version = await self._approved_version(session, title="Republicar")
                published = await service.publish_version(session, material_version_id=version.id)
                resource_id = published.resource_id

                fresh = await session.get(TheoryMaterialVersion, version.id)
                fresh.status = "APPROVED"
                await session.flush()

                republished = await service.publish_version(session, material_version_id=version.id)
                return republished.resource_id, resource_id

        new_resource_id, original_resource_id = asyncio.run(run())
        self.assertEqual(new_resource_id, original_resource_id)

    def test_publish_version_school_scoped_material_defaults_to_school_visibility(self):
        # Line ~732: a school-owned material published with the default
        # visibility_scope ("PRIVATE") must be coerced to "SCHOOL" - a
        # school's material should never publish as a dangling PRIVATE
        # resource with no visible owner semantics.
        async def run():
            async with self.session_factory() as session:
                school_id = uuid4()
                service = TheoryMaterialService()
                material = await service.create_material(
                    session, title="Material da Escola", school_id=school_id,
                )
                version = await service._repository(session).get_latest_version(material.id)
                await service.submit_for_review(session, material_version_id=version.id)
                await service.approve_version(session, material_version_id=version.id)
                published = await service.publish_version(session, material_version_id=version.id)
                resource = await session.get(EducationalResource, published.resource_id)
                return resource.visibility_scope, str(school_id)

        visibility_scope, school_id = asyncio.run(run())
        self.assertEqual(visibility_scope, "SCHOOL")

    def test_publish_version_merges_into_matching_existing_resource(self):
        # Lines 756-785: no direct resource_id link yet, but a resource with
        # the same (resource_type, title, owner, origin_type) already
        # exists - publish_version must merge into it, not create a
        # duplicate EducationalResource.
        async def run():
            async with self.session_factory() as session:
                pre_existing = EducationalResource(
                    title="Material Casa Com Recurso",
                    resource_type="THEORY_MATERIAL",
                    origin_type="PLATFORM",
                    owner_external_id=None,
                    visibility_scope="PRIVATE",
                    status="draft",
                )
                session.add(pre_existing)
                await session.flush()
                pre_existing_id = pre_existing.id

                service = TheoryMaterialService()
                material = await service.create_material(session, title="Material Casa Com Recurso")
                version = await service._repository(session).get_latest_version(material.id)
                await service.submit_for_review(session, material_version_id=version.id)
                await service.approve_version(session, material_version_id=version.id)

                published = await service.publish_version(
                    session, material_version_id=version.id, origin_type="PLATFORM",
                )
                return published.resource_id, pre_existing_id

        published_resource_id, pre_existing_id = asyncio.run(run())
        self.assertEqual(published_resource_id, pre_existing_id)

    # NOTE on archive_version's "unknown status" branch (the `status not in
    # {...}` guard, source line ~838, distinct from the explicit "already
    # ARCHIVED" check right above it): NOT tested. The DB-level
    # CheckConstraint `ck_theory_material_versions_status` restricts
    # TheoryMaterialVersion.status to exactly
    # {DRAFT, PENDING_REVIEW, APPROVED, REJECTED, PUBLISHED, ARCHIVED} - the
    # same 6 values archive_version's own logic already partitions
    # completely (5 in the allowed-to-archive set + ARCHIVED handled
    # separately just above). No value could ever reach that branch without
    # writing a status string past the DB constraint directly (a
    # SQLite-only bypass that doesn't reflect real Postgres behavior
    # either) - confirmed by attempting exactly that: it raises
    # sqlite3.IntegrityError before the service method is even called.

    # -- ContentCatalogQueryService -------------------------------------

    def test_get_questions_for_content(self):
        async def run():
            async with self.session_factory() as session:
                node = await self._make_node(session, name="Query Node", code="QRY")
                node_id = node.id
                q = Question(validation_status="approved")
                session.add(q)
                await session.flush()
                qv = QuestionVersion(
                    question_id=q.id, version_kind="official_original",
                    canonical_text="Q", statement="Q", content_hash=uuid4().hex,
                )
                session.add(qv)
                await session.flush()
                qv_id = qv.id

                link_service = ContentQuestionLinkService()
                await link_service.link(session, content_node_id=node_id, question_version_id=qv_id)
                await session.commit()

                query_service = ContentCatalogQueryService()
                return await query_service.get_questions_for_content(session, node_id)

        links = asyncio.run(run())
        self.assertEqual(len(links), 1)


if __name__ == "__main__":
    unittest.main()
