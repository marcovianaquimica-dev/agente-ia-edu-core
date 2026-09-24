"""
Direct repository-layer coverage for src/agente_ia_edu/repositories/catalog.py.

Wave 8 of the overnight bug-hunting campaign - the REPOSITORY layer, previously
untouched by this campaign.

Targets EducationalResourceRepository.get_by_id/list_by_type/list_access_grants,
ResourceQuestionLinkRepository (never instantiated anywhere in production code
or existing tests - entirely dead-but-present repository API surface), and
TheoryMaterialRepository.list_blocks_for_version (also unused by any current
caller). All are exercised here directly against a real async SQLAlchemy
session over SQLite with expire_on_commit=True (production default, see
db/session.py's create_session_factory()) per this campaign's established
pattern (tests/test_assessments_route_coverage_http.py).

NOTE ON FIXTURE STYLE: with expire_on_commit=True every attribute of an ORM
object - including its primary key - is expired by session.commit(). Reading
`obj.id` on an object created before a commit but not re-read until after it
reproduces the exact MissingGreenlet-after-commit bug shape this campaign
targets. Every helper below therefore captures ids into plain UUID locals
immediately after flush() and the tests only ever pass those locals around
after a commit, never the ORM object itself.
"""

import unittest
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    EducationalResource,
    MaterialBlock,
    MaterialSection,
    Question,
    QuestionVersion,
    ResourceAccessGrant,
    ResourceQuestionLink,
    TheoryMaterial,
    TheoryMaterialVersion,
)
from agente_ia_edu.repositories.catalog import (
    EducationalResourceRepository,
    ResourceQuestionLinkRepository,
    TheoryMaterialRepository,
)


class RepositoryTestCase(unittest.IsolatedAsyncioTestCase):
    """Base fixture: production-like session (expire_on_commit=True, default)."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, class_=AsyncSession)

    async def asyncTearDown(self):
        await self.engine.dispose()


class EducationalResourceRepositoryTests(RepositoryTestCase):
    async def test_get_by_id_found_and_missing(self):
        async with self.Session() as session:
            resource = EducationalResource(
                title="Video de Cinetica",
                resource_type="VIDEO",
                origin_type="PLATFORM",
                status="active",
                visibility_scope="PUBLIC",
            )
            session.add(resource)
            await session.flush()
            resource_id = resource.id
            await session.commit()

            repo = EducationalResourceRepository(session)
            found = await repo.get_by_id(resource_id)
            self.assertIsNotNone(found)
            self.assertEqual(found.title, "Video de Cinetica")

            missing = await repo.get_by_id(uuid4())
            self.assertIsNone(missing)

    async def test_list_by_type_filters_and_only_returns_matching_type(self):
        async with self.Session() as session:
            session.add_all(
                [
                    EducationalResource(
                        title="Video 1", resource_type="VIDEO", origin_type="PLATFORM",
                        status="active", visibility_scope="PUBLIC",
                    ),
                    EducationalResource(
                        title="PDF 1", resource_type="PDF", origin_type="PLATFORM",
                        status="active", visibility_scope="PUBLIC",
                    ),
                    EducationalResource(
                        title="Video 2", resource_type="VIDEO", origin_type="PLATFORM",
                        status="active", visibility_scope="PUBLIC",
                    ),
                ]
            )
            await session.commit()

            repo = EducationalResourceRepository(session)
            videos = await repo.list_by_type("VIDEO")
            self.assertEqual(len(videos), 2)
            self.assertTrue(all(v.resource_type == "VIDEO" for v in videos))

    async def test_list_access_grants_scoped_to_single_resource(self):
        async with self.Session() as session:
            resource = EducationalResource(
                title="Licenciado", resource_type="BOOK", origin_type="LICENSED",
                status="active", visibility_scope="SCHOOL",
            )
            other = EducationalResource(
                title="Outro", resource_type="BOOK", origin_type="LICENSED",
                status="active", visibility_scope="SCHOOL",
            )
            session.add_all([resource, other])
            await session.flush()
            resource_id = resource.id
            other_id = other.id
            session.add_all(
                [
                    ResourceAccessGrant(
                        resource_id=resource_id, grantee_type="SCHOOL", grantee_external_id="school:A"
                    ),
                    ResourceAccessGrant(
                        resource_id=resource_id, grantee_type="SCHOOL", grantee_external_id="school:B"
                    ),
                    ResourceAccessGrant(
                        resource_id=other_id, grantee_type="SCHOOL", grantee_external_id="school:C"
                    ),
                ]
            )
            await session.commit()

            repo = EducationalResourceRepository(session)
            grants = await repo.list_access_grants(resource_id)
            self.assertEqual(len(grants), 2)
            self.assertTrue(all(g.resource_id == resource_id for g in grants))


class ResourceQuestionLinkRepositoryTests(RepositoryTestCase):
    """
    ResourceQuestionLinkRepository is never instantiated by any service or API
    route today (confirmed via grep across src/agente_ia_edu/) - it is
    forward-looking repository surface for the QUESTION_SET resource type.
    Still worth a direct test: it is public API and the ordering behaviour
    (order_by position) is exactly the kind of thing that silently breaks.
    """

    async def test_list_by_resource_orders_by_position(self):
        async with self.Session() as session:
            resource = EducationalResource(
                title="Lista de Exercicios", resource_type="QUESTION_SET",
                origin_type="AUTHOR", status="active", visibility_scope="PRIVATE",
            )
            session.add(resource)
            await session.flush()
            resource_id = resource.id

            q1 = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
            q2 = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
            session.add_all([q1, q2])
            await session.flush()
            v1 = QuestionVersion(
                question_id=q1.id, version_kind="official_original", canonical_text="q1", content_hash="h1"
            )
            v2 = QuestionVersion(
                question_id=q2.id, version_kind="official_original", canonical_text="q2", content_hash="h2"
            )
            session.add_all([v1, v2])
            await session.flush()
            v1_id, v2_id = v1.id, v2.id

            session.add_all(
                [
                    ResourceQuestionLink(resource_id=resource_id, question_version_id=v2_id, position=2),
                    ResourceQuestionLink(resource_id=resource_id, question_version_id=v1_id, position=1),
                ]
            )
            await session.commit()

            repo = ResourceQuestionLinkRepository(session)
            links = await repo.list_by_resource(resource_id)
            self.assertEqual([link.position for link in links], [1, 2])
            self.assertEqual(links[0].question_version_id, v1_id)

    async def test_list_by_resource_returns_empty_for_resource_with_no_links(self):
        async with self.Session() as session:
            resource = EducationalResource(
                title="Lista Vazia", resource_type="QUESTION_SET",
                origin_type="AUTHOR", status="draft", visibility_scope="PRIVATE",
            )
            session.add(resource)
            await session.flush()
            resource_id = resource.id
            await session.commit()

            repo = ResourceQuestionLinkRepository(session)
            links = await repo.list_by_resource(resource_id)
            self.assertEqual(links, [])


class TheoryMaterialRepositoryListBlocksForVersionTests(RepositoryTestCase):
    """
    list_blocks_for_version is the denormalized "all blocks of a version"
    query (material_version_id is a direct column on MaterialBlock precisely
    to serve this without joining through MaterialSection). Not currently
    called by any service, but is public repository API.
    """

    async def test_list_blocks_for_version_orders_by_position_across_sections(self):
        async with self.Session() as session:
            material = TheoryMaterial(title="Cinetica Quimica", visibility_scope="PRIVATE")
            session.add(material)
            await session.flush()
            version = TheoryMaterialVersion(material_id=material.id, version_number=1, status="DRAFT")
            session.add(version)
            await session.flush()
            version_id = version.id
            section = MaterialSection(
                material_version_id=version_id, section_type="chapter", position=1, title="Cap 1"
            )
            session.add(section)
            await session.flush()
            section_id = section.id

            session.add_all(
                [
                    MaterialBlock(
                        section_id=section_id, material_version_id=version_id,
                        block_type="TEXT", position=2, body="segundo",
                    ),
                    MaterialBlock(
                        section_id=section_id, material_version_id=version_id,
                        block_type="TEXT", position=1, body="primeiro",
                    ),
                ]
            )
            await session.commit()

            repo = TheoryMaterialRepository(session)
            blocks = await repo.list_blocks_for_version(version_id)
            self.assertEqual([b.body for b in blocks], ["primeiro", "segundo"])

    async def test_list_blocks_for_version_scoped_to_its_own_version(self):
        async with self.Session() as session:
            material = TheoryMaterial(title="Termoquimica", visibility_scope="PRIVATE")
            session.add(material)
            await session.flush()
            v1 = TheoryMaterialVersion(material_id=material.id, version_number=1, status="DRAFT")
            v2 = TheoryMaterialVersion(material_id=material.id, version_number=2, status="DRAFT")
            session.add_all([v1, v2])
            await session.flush()
            v1_id, v2_id = v1.id, v2.id
            s1 = MaterialSection(material_version_id=v1_id, section_type="chapter", position=1)
            s2 = MaterialSection(material_version_id=v2_id, section_type="chapter", position=1)
            session.add_all([s1, s2])
            await session.flush()
            s1_id, s2_id = s1.id, s2.id
            session.add_all(
                [
                    MaterialBlock(section_id=s1_id, material_version_id=v1_id, block_type="TEXT", position=1, body="v1 block"),
                    MaterialBlock(section_id=s2_id, material_version_id=v2_id, block_type="TEXT", position=1, body="v2 block"),
                ]
            )
            await session.commit()

            repo = TheoryMaterialRepository(session)
            v1_blocks = await repo.list_blocks_for_version(v1_id)
            self.assertEqual([b.body for b in v1_blocks], ["v1 block"])


if __name__ == "__main__":
    unittest.main()
