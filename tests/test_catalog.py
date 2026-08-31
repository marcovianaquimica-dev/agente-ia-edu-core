"""
Tests for the Pedagogical Catalog domain (Phase 2 foundation).

Covers: taxonomy tree, resources, content<->resource (N:N), content<->question
linking without duplication, authored materials with versioning/publication
immutability, ownership/visibility, and a slice of the HTTP API.
"""

import unittest
import uuid
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import app
from agente_ia_edu.api.dependencies import get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AdminAuditLog,
    CatalogNode,
    EducationalResource,
    Question,
    QuestionVersion,
    School,
    UserSchoolLink,
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
            versions = await service._repository(session).list_versions(material.id)
            await session.commit()
            self.assertEqual(len(versions), 1)
            self.assertEqual(versions[0].version_number, 1)
            self.assertEqual(versions[0].status, "DRAFT")

    async def test_versioning_increments_number(self):
        async with self.Session() as session:
            service = TheoryMaterialService()
            material = await service.create_material(session, title="Material X")
            v1 = await service._repository(session).get_latest_version(material.id)
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
            version = await material_service._repository(session).get_latest_version(material.id)
            await material_service.add_section(
                session, material_version_id=version.id, section_type="INTRO", position=1, body="..."
            )
            version = await material_service.submit_for_review(session, material_version_id=version.id)
            version = await material_service.approve_version(session, material_version_id=version.id)

            published = await material_service.publish_version(
                session, material_version_id=version.id, visibility_scope="SCHOOL"
            )
            await session.commit()

            self.assertEqual(published.status, "PUBLISHED")
            self.assertIsNotNone(published.resource_id)
            self.assertIsNotNone(published.published_at)

    async def test_publish_version_scopes_school_resources_and_is_idempotent(self):
        async with self.Session() as session:
            school_id = uuid.uuid4()
            material_service = TheoryMaterialService()
            material = await material_service.create_material(
                session,
                title="Material Escolar",
                created_by_external_identity="teacher:school-1",
                school_id=school_id,
            )
            version = await material_service._repository(session).get_latest_version(material.id)
            version = await material_service.submit_for_review(session, material_version_id=version.id)
            version = await material_service.approve_version(session, material_version_id=version.id)

            published = await material_service.publish_version(
                session,
                material_version_id=version.id,
                visibility_scope="SCHOOL",
            )
            await session.commit()

            resource = await session.get(EducationalResource, published.resource_id)
            self.assertIsNotNone(resource)
            self.assertEqual(resource.owner_external_id, str(school_id))
            self.assertEqual(resource.origin_type, "SCHOOL")
            self.assertEqual(resource.visibility_scope, "SCHOOL")

            duplicate = await material_service.publish_version(
                session,
                material_version_id=version.id,
                visibility_scope="SCHOOL",
            )
            await session.commit()

            self.assertEqual(duplicate.resource_id, published.resource_id)
            self.assertEqual(resource.id, duplicate.resource_id)

    async def test_published_version_cannot_be_modified(self):
        async with self.Session() as session:
            material_service = TheoryMaterialService()
            material = await material_service.create_material(session, title="Material Imutável")
            version = await material_service._repository(session).get_latest_version(material.id)
            version = await material_service.submit_for_review(session, material_version_id=version.id)
            version = await material_service.approve_version(session, material_version_id=version.id)
            version = await material_service.publish_version(session, material_version_id=version.id)
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

    async def test_publish_same_version_twice_is_idempotent(self):
        async with self.Session() as session:
            material_service = TheoryMaterialService()
            material = await material_service.create_material(session, title="Material Duplo")
            version = await material_service._repository(session).get_latest_version(material.id)
            version = await material_service.submit_for_review(session, material_version_id=version.id)
            version = await material_service.approve_version(session, material_version_id=version.id)
            first = await material_service.publish_version(session, material_version_id=version.id)
            await session.commit()

            second = await material_service.publish_version(session, material_version_id=version.id)
            await session.commit()

            self.assertEqual(first.resource_id, second.resource_id)
            self.assertIsNotNone(first.resource_id)
            self.assertEqual(second.status, "PUBLISHED")

    async def test_material_review_workflow_and_invalid_transitions(self):
        async with self.Session() as session:
            service = TheoryMaterialService()
            material = await service.create_material(session, title="Material de Revisão")
            version = await service._repository(session).get_latest_version(material.id)

            self.assertEqual(version.status, "DRAFT")

            with self.assertRaises(ValueError):
                await service.publish_version(session, material_version_id=version.id)

            version = await service.submit_for_review(session, material_version_id=version.id)
            self.assertEqual(version.status, "PENDING_REVIEW")

            version = await service.reject_version(session, material_version_id=version.id)
            self.assertEqual(version.status, "REJECTED")

            version = await service.submit_for_review(session, material_version_id=version.id)
            self.assertEqual(version.status, "PENDING_REVIEW")

            version = await service.approve_version(session, material_version_id=version.id)
            self.assertEqual(version.status, "APPROVED")

            version = await service.publish_version(session, material_version_id=version.id)
            self.assertEqual(version.status, "PUBLISHED")

            with self.assertRaises(ValueError):
                await service.submit_for_review(session, material_version_id=version.id)

            version = await service.archive_version(session, material_version_id=version.id)
            self.assertEqual(version.status, "ARCHIVED")

    async def test_reject_version_keeps_pedagogical_summary_intact(self):
        async with self.Session() as session:
            service = TheoryMaterialService()
            material = await service.create_material(session, title="Material com Resumo")
            version = await service._repository(session).get_latest_version(material.id)
            version.summary = "Resumo didático do conteúdo."
            version = await service.submit_for_review(session, material_version_id=version.id)
            version = await service.reject_version(
                session,
                material_version_id=version.id,
                reason="Conteúdo sem alinhamento ao plano.",
            )
            await session.commit()

            self.assertEqual(version.status, "REJECTED")
            self.assertEqual(version.summary, "Resumo didático do conteúdo.")
            self.assertEqual(version.metadata_["rejection_reason"], "Conteúdo sem alinhamento ao plano.")

    async def test_rejecting_latest_version_does_not_mutate_historical_versions(self):
        async with self.Session() as session:
            service = TheoryMaterialService()
            material = await service.create_material(session, title="Material Histórico")
            v1 = await service._repository(session).get_latest_version(material.id)
            v1.summary = "Versão 1 do conteúdo."
            v1 = await service.submit_for_review(session, material_version_id=v1.id)
            v1 = await service.approve_version(session, material_version_id=v1.id)
            v1 = await service.publish_version(session, material_version_id=v1.id)

            v2 = await service.create_version(session, material_id=material.id, summary="Versão 2 do conteúdo.")
            v2 = await service.submit_for_review(session, material_version_id=v2.id)
            v2 = await service.reject_version(
                session,
                material_version_id=v2.id,
                reason="Versão 2 rejeitada por revisão.",
            )
            await session.commit()

            self.assertEqual(v1.summary, "Versão 1 do conteúdo.")
            self.assertEqual(v2.summary, "Versão 2 do conteúdo.")
            self.assertNotIn("rejection_reason", (v1.metadata_ or {}))
            self.assertEqual(v2.metadata_["rejection_reason"], "Versão 2 rejeitada por revisão.")

    async def test_material_workflow_is_role_only_not_school_scoped(self):
        async with self.Session() as session:
            material_service = TheoryMaterialService()
            material = await material_service.create_material(session, title="Material sem tenant")
            version = await material_service._repository(session).get_latest_version(material.id)
            version.summary = "Conteúdo pedagógico."
            version = await material_service.submit_for_review(session, material_version_id=version.id)

            approved = await material_service.approve_version(session, material_version_id=version.id)
            self.assertEqual(approved.status, "APPROVED")

            published = await material_service.publish_version(session, material_version_id=approved.id)
            self.assertEqual(published.status, "PUBLISHED")

    async def test_material_ai_metadata_cannot_bypass_review_workflow(self):
        async with self.Session() as session:
            service = TheoryMaterialService()
            material = await service.create_material(session, title="Material com IA")
            version = await service._repository(session).get_latest_version(material.id)
            version.summary = "Resumo pedagógico válido."
            version.metadata_ = {"ai_suggested_status": "APPROVED", "ai_summary": "IA sugeriu aprovação."}
            version = await service.submit_for_review(session, material_version_id=version.id)
            self.assertEqual(version.status, "PENDING_REVIEW")

            with self.assertRaises(ValueError):
                await service.publish_version(session, material_version_id=version.id)

            version = await service.approve_version(session, material_version_id=version.id)
            self.assertEqual(version.status, "APPROVED")
            self.assertEqual(version.summary, "Resumo pedagógico válido.")

    async def test_materials_are_scoped_by_school_and_creator(self):
        async with self.Session() as session:
            school_a = School(code="A", name="Escola A")
            school_b = School(code="B", name="Escola B")
            session.add_all([school_a, school_b])
            await session.flush()

            session.add_all([
                UserSchoolLink(
                    external_user_id="teacher:a",
                    school_id=school_a.id,
                    role="TEACHER",
                    scope_type="SCHOOL",
                    active=True,
                ),
                UserSchoolLink(
                    external_user_id="teacher:b",
                    school_id=school_b.id,
                    role="TEACHER",
                    scope_type="SCHOOL",
                    active=True,
                ),
            ])
            await session.flush()

            service = TheoryMaterialService()
            material_a = await service.create_material(
                session,
                title="Material escola A",
                created_by_external_identity="teacher:a",
                school_id=school_a.id,
            )
            material_b = await service.create_material(
                session,
                title="Material escola B",
                created_by_external_identity="teacher:b",
                school_id=school_b.id,
            )
            await session.commit()

            self.assertEqual(material_a.school_id, school_a.id)
            self.assertEqual(material_b.school_id, school_b.id)
            self.assertNotEqual(material_a.school_id, material_b.school_id)

    async def test_editing_material_creates_new_version(self):
        async with self.Session() as session:
            service = TheoryMaterialService()
            material = await service.create_material(session, title="Material com Edição")
            v1 = await service._repository(session).get_latest_version(material.id)
            v1 = await service.submit_for_review(session, material_version_id=v1.id)
            v1 = await service.approve_version(session, material_version_id=v1.id)
            v1 = await service.publish_version(session, material_version_id=v1.id)

            v2 = await service.create_version(session, material_id=material.id, introduction="Segunda versão")
            self.assertEqual(v2.version_number, 2)
            self.assertEqual(v2.status, "DRAFT")


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
            headers={"Authorization": "Bearer teacher:teacher2"},
        )
        self.assertEqual(material_resp.status_code, 201)
        material_id = material_resp.json()["id"]
        self.assertEqual(material_resp.json()["created_by_external_identity"], "teacher2")

        version_resp = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/versions",
            json={"introduction": "Intro"},
            headers={"Authorization": "Bearer teacher:teacher2"},
        )
        self.assertEqual(version_resp.status_code, 201)
        self.assertEqual(version_resp.json()["version_number"], 2)

        detail_resp = self.client.get(
            f"/api/v1/catalog/materials/{material_id}",
            headers={"Authorization": "Bearer teacher:teacher2"},
        )
        self.assertEqual(detail_resp.status_code, 200)
        self.assertGreaterEqual(len(detail_resp.json()["versions"]), 2)

    def test_material_actions_require_teacher_or_coordinator_role(self):
        create_resp = self.client.post(
            "/api/v1/catalog/materials",
            json={"title": "Material Protegido"},
            headers={"Authorization": "Bearer student:alice"},
        )
        self.assertEqual(create_resp.status_code, 403)

        teacher_resp = self.client.post(
            "/api/v1/catalog/materials",
            json={"title": "Material Professor"},
            headers={"Authorization": "Bearer teacher:prof_1"},
        )
        self.assertEqual(teacher_resp.status_code, 201)
        material_id = teacher_resp.json()["id"]

        approve_resp = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/approve",
            headers={"Authorization": "Bearer student:alice"},
        )
        self.assertEqual(approve_resp.status_code, 403)

    def test_student_catalog_lists_only_visible_resources(self):
        async def _seed():
            async with self.session_factory() as session:
                school_a = School(code="A", name="Escola A")
                school_b = School(code="B", name="Escola B")
                session.add_all([school_a, school_b])
                await session.flush()

                session.add_all([
                    UserSchoolLink(
                        external_user_id="alice",
                        school_id=school_a.id,
                        role="STUDENT",
                        scope_type="SCHOOL",
                        active=True,
                    ),
                    UserSchoolLink(
                        external_user_id="bob",
                        school_id=school_b.id,
                        role="STUDENT",
                        scope_type="SCHOOL",
                        active=True,
                    ),
                ])

                public_resource = EducationalResource(
                    title="Recurso público",
                    resource_type="THEORY_MATERIAL",
                    origin_type="PLATFORM",
                    visibility_scope="PUBLIC",
                    status="active",
                )
                school_a_resource = EducationalResource(
                    title="Recurso escola A",
                    resource_type="THEORY_MATERIAL",
                    origin_type="SCHOOL",
                    owner_external_id=str(school_a.id),
                    visibility_scope="SCHOOL",
                    status="active",
                )
                school_b_private = EducationalResource(
                    title="SEGREDO_ESCOLA_B",
                    resource_type="THEORY_MATERIAL",
                    origin_type="SCHOOL",
                    owner_external_id=str(school_b.id),
                    visibility_scope="PRIVATE",
                    status="active",
                )
                archived = EducationalResource(
                    title="Material Arquivado",
                    resource_type="THEORY_MATERIAL",
                    origin_type="AUTHOR",
                    visibility_scope="PRIVATE",
                    status="archived",
                    owner_external_id="alice",
                )
                session.add_all([public_resource, school_a_resource, school_b_private, archived])
                await session.commit()

        import asyncio
        asyncio.run(_seed())

        resp = self.client.get(
            "/api/v1/catalog/resources",
            headers={"Authorization": "Bearer student:alice"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        titles = {item["title"] for item in resp.json()}
        self.assertIn("Recurso público", titles)
        self.assertIn("Recurso escola A", titles)
        self.assertNotIn("SEGREDO_ESCOLA_B", titles)
        self.assertNotIn("Material Arquivado", titles)

    def test_student_catalog_search_is_isolated_by_school(self):
        async def _seed():
            async with self.session_factory() as session:
                school_a = School(code="A2", name="Escola A2")
                school_b = School(code="B2", name="Escola B2")
                session.add_all([school_a, school_b])
                await session.flush()

                session.add_all([
                    UserSchoolLink(
                        external_user_id="alice2",
                        school_id=school_a.id,
                        role="STUDENT",
                        scope_type="SCHOOL",
                        active=True,
                    )
                ])

                session.add_all([
                    EducationalResource(
                        title="Resumo Público de Química",
                        resource_type="THEORY_MATERIAL",
                        origin_type="PLATFORM",
                        visibility_scope="PUBLIC",
                        status="active",
                    ),
                    EducationalResource(
                        title="SEGREDO_ESCOLA_B",
                        resource_type="THEORY_MATERIAL",
                        origin_type="SCHOOL",
                        owner_external_id=str(school_b.id),
                        visibility_scope="PRIVATE",
                        status="active",
                    ),
                ])
                await session.commit()

        import asyncio
        asyncio.run(_seed())

        resp = self.client.get(
            "/api/v1/catalog/resources",
            params={"q": "SEGREDO_ESCOLA_B"},
            headers={"Authorization": "Bearer student:alice2"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), [])

    def test_resource_detail_respects_visibility(self):
        async def _seed():
            async with self.session_factory() as session:
                school_a = School(code="A3", name="Escola A3")
                school_b = School(code="B3", name="Escola B3")
                session.add_all([school_a, school_b])
                await session.flush()

                session.add_all([
                    UserSchoolLink(
                        external_user_id="alice3",
                        school_id=school_a.id,
                        role="STUDENT",
                        scope_type="SCHOOL",
                        active=True,
                    )
                ])

                private_resource = EducationalResource(
                    title="Material privado da escola B",
                    resource_type="THEORY_MATERIAL",
                    origin_type="SCHOOL",
                    owner_external_id=str(school_b.id),
                    visibility_scope="PRIVATE",
                    status="active",
                )
                public_resource = EducationalResource(
                    title="Material público",
                    resource_type="THEORY_MATERIAL",
                    origin_type="PLATFORM",
                    visibility_scope="PUBLIC",
                    status="active",
                )
                session.add_all([private_resource, public_resource])
                await session.commit()
                return public_resource.id, private_resource.id

        import asyncio
        public_id, private_id = asyncio.run(_seed())

        ok = self.client.get(
            f"/api/v1/catalog/resources/{public_id}",
            headers={"Authorization": "Bearer student:alice3"},
        )
        self.assertEqual(ok.status_code, 200)

        blocked = self.client.get(
            f"/api/v1/catalog/resources/{private_id}",
            headers={"Authorization": "Bearer student:alice3"},
        )
        self.assertEqual(blocked.status_code, 403)

    def test_material_history_records_valid_editorial_events(self):
        material_resp = self.client.post(
            "/api/v1/catalog/materials",
            json={"title": "Material com auditoria"},
            headers={"Authorization": "Bearer teacher:auditor"},
        )
        self.assertEqual(material_resp.status_code, 201, material_resp.text)
        material_id = material_resp.json()["id"]

        version_resp = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/versions",
            json={"summary": "Versão inicial"},
            headers={"Authorization": "Bearer teacher:auditor"},
        )
        self.assertEqual(version_resp.status_code, 201, version_resp.text)

        submit_resp = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/review",
            json={"action": "submit"},
            headers={"Authorization": "Bearer teacher:auditor"},
        )
        self.assertEqual(submit_resp.status_code, 200, submit_resp.text)

        approve_resp = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/approve",
            headers={"Authorization": "Bearer teacher:auditor"},
        )
        self.assertEqual(approve_resp.status_code, 200, approve_resp.text)

        publish_resp = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/publish",
            headers={"Authorization": "Bearer teacher:auditor"},
        )
        self.assertEqual(publish_resp.status_code, 200, publish_resp.text)

        archive_resp = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/archive",
            headers={"Authorization": "Bearer teacher:auditor"},
        )
        self.assertEqual(archive_resp.status_code, 200, archive_resp.text)

        import asyncio

        async def _load_history():
            async with self.session_factory() as session:
                stmt = await session.execute(
                    __import__("sqlalchemy").sql.select(AdminAuditLog).where(
                        AdminAuditLog.entity_id == str(material_id)
                    )
                )
                return list(stmt.scalars().all())

        events = asyncio.run(_load_history())
        actions = {log.action for log in events}
        self.assertIn("MATERIAL_CREATED", actions)
        self.assertIn("MATERIAL_VERSION_CREATED", actions)
        self.assertIn("MATERIAL_SUBMITTED_FOR_REVIEW", actions)
        self.assertIn("MATERIAL_APPROVED", actions)
        self.assertIn("MATERIAL_PUBLISHED", actions)
        self.assertIn("MATERIAL_ARCHIVED", actions)

    def test_invalid_transition_does_not_create_event(self):
        material_resp = self.client.post(
            "/api/v1/catalog/materials",
            json={"title": "Material inválido"},
            headers={"Authorization": "Bearer teacher:invalid"},
        )
        material_id = material_resp.json()["id"]

        bad = self.client.post(
            f"/api/v1/catalog/materials/{material_id}/review",
            json={"action": "publish"},
            headers={"Authorization": "Bearer teacher:invalid"},
        )
        self.assertEqual(bad.status_code, 400)

        import asyncio

        async def _load_history():
            async with self.session_factory() as session:
                stmt = await session.execute(
                    __import__("sqlalchemy").sql.select(AdminAuditLog).where(
                        AdminAuditLog.entity_id == str(material_id)
                    )
                )
                return list(stmt.scalars().all())

        events = asyncio.run(_load_history())
        self.assertNotIn("MATERIAL_PUBLISHED", {log.action for log in events})

    def test_material_history_respects_school_scope(self):
        async def _seed():
            async with self.session_factory() as session:
                school_a = School(code="H1", name="Escola H1")
                school_b = School(code="H2", name="Escola H2")
                session.add_all([school_a, school_b])
                await session.flush()

                session.add_all([
                    UserSchoolLink(
                        external_user_id="teacher_a_history",
                        school_id=school_a.id,
                        role="TEACHER",
                        scope_type="SCHOOL",
                        active=True,
                    ),
                    UserSchoolLink(
                        external_user_id="teacher_b_history",
                        school_id=school_b.id,
                        role="TEACHER",
                        scope_type="SCHOOL",
                        active=True,
                    ),
                ])
                await session.flush()

                material = __import__("agente_ia_edu.services.catalog", fromlist=['TheoryMaterialService']).TheoryMaterialService()
                created = await material.create_material(
                    session,
                    title="Material de escola A",
                    created_by_external_identity="teacher_a_history",
                    school_id=school_a.id,
                )
                await session.commit()
                return created.id

        import asyncio
        material_id = asyncio.run(_seed())

        forbidden = self.client.get(
            f"/api/v1/catalog/materials/{material_id}/history",
            headers={"Authorization": "Bearer teacher:teacher_b_history"},
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.text)

    def test_nonexistent_material_returns_404(self):
        resp = self.client.get(f"/api/v1/catalog/materials/{uuid4()}")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
