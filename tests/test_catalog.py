"""
Tests for the Pedagogical Catalog domain (Phase 2 foundation).

Covers: taxonomy tree, resources, content<->resource (N:N), content<->question
linking without duplication, authored materials with versioning/publication
immutability, ownership/visibility, and a slice of the HTTP API.
"""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import app
from agente_ia_edu.api.dependencies import get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    Question,
    QuestionVersion,
)
from agente_ia_edu.repositories.catalog import (
    CatalogNodeRepository,
    ContentQuestionLinkRepository,
    ContentResourceLinkRepository,
    TheoryMaterialRepository,
)
from agente_ia_edu.services.catalog import (
    CatalogNodeService,
    ContentCatalogQueryService,
    ContentQuestionLinkService,
    ContentResourceLinkService,
    EducationalResourceService,
    TheoryMaterialService,
)


def _auth(user: str) -> dict:
    return {"Authorization": f"Bearer student:{user}"}


class CatalogNodeTests(unittest.IsolatedAsyncioTestCase):
    """Discipline / content tree creation and parent-child relationships."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_create_discipline_is_its_own_root(self):
        async with self.Session() as session:
            service = CatalogNodeService()
            node = await service.create_node(
                session, name="Química", node_type="DISCIPLINE"
            )
            await session.commit()
            self.assertIsNone(node.parent_id)
            self.assertEqual(node.root_id, node.id)

    async def test_create_tree_inherits_root_from_parent(self):
        async with self.Session() as session:
            service = CatalogNodeService()
            discipline = await service.create_node(session, name="Química", node_type="DISCIPLINE")
            area = await service.create_node(
                session, name="Físico-Química", node_type="LEARNING_AREA", parent_id=discipline.id
            )
            unit = await service.create_node(
                session, name="Termoquímica", node_type="LEARNING_UNIT", parent_id=area.id
            )
            content = await service.create_node(
                session, name="Entalpia", node_type="CONTENT", parent_id=unit.id
            )
            await session.commit()

            self.assertEqual(area.root_id, discipline.id)
            self.assertEqual(unit.root_id, discipline.id)
            self.assertEqual(content.root_id, discipline.id)

    async def test_parent_child_relationship_queryable(self):
        async with self.Session() as session:
            service = CatalogNodeService()
            discipline = await service.create_node(session, name="Matemática", node_type="DISCIPLINE")
            child_a = await service.create_node(
                session, name="Álgebra", node_type="LEARNING_AREA", parent_id=discipline.id
            )
            child_b = await service.create_node(
                session, name="Geometria", node_type="LEARNING_AREA", parent_id=discipline.id
            )
            await session.commit()

            repo = CatalogNodeRepository(session)
            children = await repo.list_children(discipline.id)
            self.assertEqual({c.id for c in children}, {child_a.id, child_b.id})

    async def test_create_node_without_existing_parent_fails(self):
        async with self.Session() as session:
            service = CatalogNodeService()
            with self.assertRaises(ValueError):
                await service.create_node(
                    session, name="Orphan", node_type="CONTENT", parent_id=uuid4()
                )

    async def test_different_disciplines_do_not_share_a_tree(self):
        """Different disciplines are independent - not tied to any subject-specific rule."""
        async with self.Session() as session:
            service = CatalogNodeService()
            chemistry = await service.create_node(session, name="Química", node_type="DISCIPLINE")
            history = await service.create_node(session, name="História", node_type="DISCIPLINE")
            await session.commit()

            repo = CatalogNodeRepository(session)
            chemistry_nodes = await repo.list_by_root(chemistry.id)
            history_nodes = await repo.list_by_root(history.id)
            self.assertEqual([n.id for n in chemistry_nodes], [chemistry.id])
            self.assertEqual([n.id for n in history_nodes], [history.id])


class EducationalResourceTests(unittest.IsolatedAsyncioTestCase):
    """Resource creation, content<->resource N:N, ownership/visibility."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _make_content_node(self, session) -> CatalogNode:
        service = CatalogNodeService()
        discipline = await service.create_node(session, name="Química", node_type="DISCIPLINE")
        return await service.create_node(
            session, name="Estequiometria", node_type="CONTENT", parent_id=discipline.id
        )

    async def test_create_resource_with_traceable_origin(self):
        async with self.Session() as session:
            service = EducationalResourceService()
            resource = await service.create_resource(
                session,
                title="Vídeo introdutório",
                resource_type="VIDEO",
                origin_type="EXTERNAL",
                owner_external_id="youtube-channel-x",
                visibility_scope="PUBLIC",
            )
            await session.commit()
            self.assertEqual(resource.origin_type, "EXTERNAL")
            self.assertEqual(resource.status, "draft")

    async def test_content_can_have_multiple_resources(self):
        async with self.Session() as session:
            content = await self._make_content_node(session)
            resource_service = EducationalResourceService()
            link_service = ContentResourceLinkService()

            r1 = await resource_service.create_resource(
                session, title="Teoria", resource_type="THEORY_MATERIAL", origin_type="AUTHOR"
            )
            r2 = await resource_service.create_resource(
                session, title="Vídeo", resource_type="VIDEO", origin_type="PLATFORM"
            )
            await link_service.link(
                session, content_node_id=content.id, resource_id=r1.id, pedagogical_role="THEORY"
            )
            await link_service.link(
                session, content_node_id=content.id, resource_id=r2.id, pedagogical_role="VIDEO"
            )
            await session.commit()

            query = ContentCatalogQueryService()
            links = await query.get_resources_for_content(session, content.id)
            self.assertEqual({l.resource_id for l in links}, {r1.id, r2.id})

    async def test_resource_can_be_associated_with_multiple_contents(self):
        async with self.Session() as session:
            service = CatalogNodeService()
            discipline = await service.create_node(session, name="Física", node_type="DISCIPLINE")
            content_a = await service.create_node(
                session, name="Cinemática", node_type="CONTENT", parent_id=discipline.id
            )
            content_b = await service.create_node(
                session, name="Dinâmica", node_type="CONTENT", parent_id=discipline.id
            )

            resource_service = EducationalResourceService()
            resource = await resource_service.create_resource(
                session, title="Vídeo geral de mecânica", resource_type="VIDEO", origin_type="PLATFORM"
            )

            link_service = ContentResourceLinkService()
            await link_service.link(
                session, content_node_id=content_a.id, resource_id=resource.id, pedagogical_role="VIDEO"
            )
            await link_service.link(
                session, content_node_id=content_b.id, resource_id=resource.id, pedagogical_role="VIDEO"
            )
            await session.commit()

            repo = ContentResourceLinkRepository(session)
            links = await repo.list_by_resource(resource.id)
            self.assertEqual({l.content_node_id for l in links}, {content_a.id, content_b.id})

    async def test_duplicate_content_resource_link_rejected(self):
        async with self.Session() as session:
            content = await self._make_content_node(session)
            resource_service = EducationalResourceService()
            resource = await resource_service.create_resource(
                session, title="Teoria", resource_type="THEORY_MATERIAL", origin_type="AUTHOR"
            )
            link_service = ContentResourceLinkService()
            await link_service.link(
                session, content_node_id=content.id, resource_id=resource.id, pedagogical_role="THEORY"
            )
            await session.commit()

            with self.assertRaises(ValueError):
                await link_service.link(
                    session,
                    content_node_id=content.id,
                    resource_id=resource.id,
                    pedagogical_role="THEORY",
                )

    async def test_ownership_and_visibility_scope_recorded(self):
        async with self.Session() as session:
            service = EducationalResourceService()
            school_owned = await service.create_resource(
                session,
                title="Apostila da Escola A",
                resource_type="PDF",
                origin_type="SCHOOL",
                owner_external_id="school:A",
                visibility_scope="SCHOOL",
            )
            public_resource = await service.create_resource(
                session,
                title="Recurso público",
                resource_type="EXTERNAL_RESOURCE",
                origin_type="EXTERNAL",
                visibility_scope="PUBLIC",
            )
            await session.commit()

            self.assertEqual(school_owned.visibility_scope, "SCHOOL")
            self.assertEqual(school_owned.owner_external_id, "school:A")
            self.assertEqual(public_resource.visibility_scope, "PUBLIC")

    async def test_isolation_between_institutions_by_owner(self):
        """Resources owned by different institutions are queryable independently."""
        from agente_ia_edu.repositories.catalog import EducationalResourceRepository

        async with self.Session() as session:
            service = EducationalResourceService()
            await service.create_resource(
                session, title="Recurso Escola A", resource_type="PDF",
                origin_type="SCHOOL", owner_external_id="school:A",
            )
            await service.create_resource(
                session, title="Recurso Escola B", resource_type="PDF",
                origin_type="SCHOOL", owner_external_id="school:B",
            )
            await session.commit()

            repo = EducationalResourceRepository(session)
            school_a_resources = await repo.list_by_owner("school:A")
            school_b_resources = await repo.list_by_owner("school:B")
            self.assertEqual(len(school_a_resources), 1)
            self.assertEqual(len(school_b_resources), 1)
            self.assertNotEqual(school_a_resources[0].id, school_b_resources[0].id)


class ContentQuestionLinkTests(unittest.IsolatedAsyncioTestCase):
    """Existing questions must be referenced, never duplicated."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _make_question_version(self, session) -> QuestionVersion:
        question = Question(validation_status="validated")
        session.add(question)
        await session.flush()
        version = QuestionVersion(
            question_id=question.id,
            version_kind="official_original",
            canonical_text="2 + 2?",
            content_hash=uuid4().hex,
        )
        session.add(version)
        await session.flush()
        return version

    async def test_link_existing_question_to_content(self):
        async with self.Session() as session:
            node_service = CatalogNodeService()
            discipline = await node_service.create_node(session, name="Matemática", node_type="DISCIPLINE")
            content = await node_service.create_node(
                session, name="Aritmética", node_type="CONTENT", parent_id=discipline.id
            )
            version = await self._make_question_version(session)

            link_service = ContentQuestionLinkService()
            link = await link_service.link(
                session, content_node_id=content.id, question_version_id=version.id
            )
            await session.commit()
            self.assertEqual(link.question_version_id, version.id)

    async def test_question_is_not_duplicated_when_linked_twice(self):
        async with self.Session() as session:
            node_service = CatalogNodeService()
            discipline = await node_service.create_node(session, name="Matemática", node_type="DISCIPLINE")
            content = await node_service.create_node(
                session, name="Aritmética", node_type="CONTENT", parent_id=discipline.id
            )
            version = await self._make_question_version(session)

            link_service = ContentQuestionLinkService()
            await link_service.link(
                session, content_node_id=content.id, question_version_id=version.id
            )
            await session.commit()

            with self.assertRaises(ValueError):
                await link_service.link(
                    session, content_node_id=content.id, question_version_id=version.id
                )

            # Only one QuestionVersion row exists - never duplicated.
            from sqlalchemy import select
            result = await session.execute(select(QuestionVersion))
            self.assertEqual(len(result.scalars().all()), 1)


class TheoryMaterialTests(unittest.IsolatedAsyncioTestCase):
    """Authored materials: versioning, sections, exercises, publication immutability."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_create_material_and_first_version(self):
        async with self.Session() as session:
            service = TheoryMaterialService()
            material = await service.create_material(
                session, title="Introdução à Estequiometria", created_by_external_identity="teacher:1"
            )
            version = await service.create_version(session, material_id=material.id)
            await session.commit()
            self.assertEqual(version.version_number, 1)
            self.assertEqual(version.status, "draft")

    async def test_versioning_increments_number(self):
        async with self.Session() as session:
            service = TheoryMaterialService()
            material = await service.create_material(session, title="Material X")
            v1 = await service.create_version(session, material_id=material.id)
            v2 = await service.create_version(session, material_id=material.id)
            await session.commit()
            self.assertEqual(v1.version_number, 1)
            self.assertEqual(v2.version_number, 2)

    async def test_add_sections_and_exercises(self):
        async with self.Session() as session:
            material_service = TheoryMaterialService()
            material = await material_service.create_material(session, title="Material Y")
            version = await material_service.create_version(session, material_id=material.id)

            section = await material_service.add_section(
                session,
                material_version_id=version.id,
                section_type="INTRODUCTION",
                position=1,
                title="Introdução",
                body="texto...",
            )
            await material_service.add_exercise(
                session,
                material_version_id=version.id,
                section_id=section.id,
                source_type="AUTHORED",
                position=1,
                authored_text="Quanto é 2 + 2?",
            )
            await session.commit()

            repo = TheoryMaterialRepository(session)
            sections = await repo.list_sections(version.id)
            exercises = await repo.list_exercises(version.id)
            self.assertEqual(len(sections), 1)
            self.assertEqual(len(exercises), 1)

    async def test_exercise_linked_to_existing_question_requires_id(self):
        async with self.Session() as session:
            material_service = TheoryMaterialService()
            material = await material_service.create_material(session, title="Material Z")
            version = await material_service.create_version(session, material_id=material.id)

            with self.assertRaises(ValueError):
                await material_service.add_exercise(
                    session,
                    material_version_id=version.id,
                    source_type="EXISTING_QUESTION",
                    position=1,
                    question_version_id=None,
                )

    async def test_exercise_referencing_existing_question_does_not_duplicate_it(self):
        async with self.Session() as session:
            question = Question(validation_status="validated")
            session.add(question)
            await session.flush()
            question_version = QuestionVersion(
                question_id=question.id,
                version_kind="official_original",
                canonical_text="Existing question?",
                content_hash=uuid4().hex,
            )
            session.add(question_version)
            await session.flush()

            material_service = TheoryMaterialService()
            material = await material_service.create_material(session, title="Material W")
            version = await material_service.create_version(session, material_id=material.id)
            exercise = await material_service.add_exercise(
                session,
                material_version_id=version.id,
                source_type="EXISTING_QUESTION",
                position=1,
                question_version_id=question_version.id,
            )
            await session.commit()
            self.assertEqual(exercise.question_version_id, question_version.id)

    async def test_publish_version_creates_resource_and_freezes_it(self):
        async with self.Session() as session:
            material_service = TheoryMaterialService()
            material = await material_service.create_material(
                session, title="Material Publicável", created_by_external_identity="teacher:2"
            )
            version = await material_service.create_version(session, material_id=material.id)
            await material_service.add_section(
                session, material_version_id=version.id, section_type="INTRO", position=1, body="..."
            )

            published = await material_service.publish_version(
                session, material_version_id=version.id, visibility_scope="SCHOOL"
            )
            await session.commit()

            self.assertEqual(published.status, "published")
            self.assertIsNotNone(published.resource_id)
            self.assertIsNotNone(published.published_at)

    async def test_published_version_cannot_be_modified(self):
        async with self.Session() as session:
            material_service = TheoryMaterialService()
            material = await material_service.create_material(session, title="Material Imutável")
            version = await material_service.create_version(session, material_id=material.id)
            await material_service.publish_version(session, material_version_id=version.id)
            await session.commit()

            with self.assertRaises(ValueError):
                await material_service.add_section(
                    session,
                    material_version_id=version.id,
                    section_type="NOTE",
                    position=1,
                    body="tentativa de alteração",
                )

            with self.assertRaises(ValueError):
                await material_service.add_exercise(
                    session,
                    material_version_id=version.id,
                    source_type="AUTHORED",
                    position=1,
                    authored_text="tentativa de alteração",
                )

    async def test_cannot_publish_same_version_twice(self):
        async with self.Session() as session:
            material_service = TheoryMaterialService()
            material = await material_service.create_material(session, title="Material Duplo")
            version = await material_service.create_version(session, material_id=material.id)
            await material_service.publish_version(session, material_version_id=version.id)
            await session.commit()

            with self.assertRaises(ValueError):
                await material_service.publish_version(session, material_version_id=version.id)


class CatalogApiTests(unittest.TestCase):
    """A slice of the HTTP API: identity usage, creation, and querying."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        cls.session_factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        import asyncio

        async def _init():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(_init())
        app.dependency_overrides[get_session_factory] = lambda: cls.session_factory
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        import asyncio
        asyncio.run(cls.engine.dispose())

    def test_create_and_list_disciplines(self):
        resp = self.client.post(
            "/api/v1/catalog/disciplines",
            json={"name": "Biologia", "node_type": "DISCIPLINE"},
            headers=_auth("teacher1"),
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        discipline_id = resp.json()["id"]

        list_resp = self.client.get("/api/v1/catalog/disciplines")
        self.assertEqual(list_resp.status_code, 200)
        self.assertIn(discipline_id, [d["id"] for d in list_resp.json()])

    def test_create_node_requires_parent(self):
        resp = self.client.post(
            "/api/v1/catalog/nodes",
            json={"name": "Sem pai", "node_type": "CONTENT"},
            headers=_auth("teacher1"),
        )
        self.assertEqual(resp.status_code, 400)

    def test_content_tree_and_resource_query_via_api(self):
        discipline_resp = self.client.post(
            "/api/v1/catalog/disciplines",
            json={"name": "Geografia", "node_type": "DISCIPLINE"},
            headers=_auth("teacher1"),
        )
        discipline_id = discipline_resp.json()["id"]

        content_resp = self.client.post(
            "/api/v1/catalog/nodes",
            json={"name": "Relevo", "node_type": "CONTENT", "parent_id": discipline_id},
            headers=_auth("teacher1"),
        )
        content_id = content_resp.json()["id"]

        tree_resp = self.client.get(f"/api/v1/catalog/nodes/{discipline_id}/tree")
        self.assertEqual(tree_resp.status_code, 200)
        node_ids = [n["id"] for n in tree_resp.json()["nodes"]]
        self.assertIn(content_id, node_ids)

        resource_resp = self.client.post(
            "/api/v1/catalog/resources",
            json={
                "title": "Vídeo sobre relevo",
                "resource_type": "VIDEO",
                "origin_type": "PLATFORM",
            },
            headers=_auth("teacher1"),
        )
        self.assertEqual(resource_resp.status_code, 201)
        resource_id = resource_resp.json()["id"]

        link_resp = self.client.post(
            "/api/v1/catalog/content-resource-links",
            json={
                "content_node_id": content_id,
                "resource_id": resource_id,
                "pedagogical_role": "VIDEO",
            },
            headers=_auth("teacher1"),
        )
        self.assertEqual(link_resp.status_code, 201, link_resp.text)

        resources_resp = self.client.get(f"/api/v1/catalog/nodes/{content_id}/resources")
        self.assertEqual(resources_resp.status_code, 200)
        self.assertEqual(len(resources_resp.json()["links"]), 1)

    def test_material_creation_and_versioning_via_api(self):
        material_resp = self.client.post(
            "/api/v1/catalog/materials",
            json={"title": "Material via API"},
            headers=_auth("teacher2"),
        )
        self.assertEqual(material_resp.status_code, 201)
        material_id = material_resp.json()["id"]
        self.assertEqual(material_resp.json()["created_by_external_identity"], "teacher2")

        version_resp = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/versions",
            json={"introduction": "Intro"},
            headers=_auth("teacher2"),
        )
        self.assertEqual(version_resp.status_code, 201)
        self.assertEqual(version_resp.json()["version_number"], 1)

        detail_resp = self.client.get(f"/api/v1/catalog/materials/{material_id}")
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(len(detail_resp.json()["versions"]), 1)

    def test_nonexistent_material_returns_404(self):
        resp = self.client.get(f"/api/v1/catalog/materials/{uuid4()}")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
