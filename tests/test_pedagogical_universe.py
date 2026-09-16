import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AdminAuditLog, CatalogNode, ContentQuestionLink, Question, QuestionVersion
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.admin import PlatformAdminService
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService
from agente_ia_edu.repositories.learning_path import QuestionSelectionRepository


class TestPedagogicalUniverse(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _catalog(self, session):
        area = CatalogNode(node_type="AREA", name="Ciências da Natureza", active=True)
        session.add(area)
        await session.flush()
        area.root_id = area.id
        chemistry = CatalogNode(parent_id=area.id, root_id=area.id, node_type="DISCIPLINE", name="Química", active=True)
        math = CatalogNode(node_type="DISCIPLINE", name="Matemática", active=True)
        session.add_all([chemistry, math])
        await session.flush()
        math.root_id = math.id
        content = CatalogNode(parent_id=chemistry.id, root_id=area.id, node_type="CONTENT", name="Soluções", active=True)
        session.add(content)
        await session.commit()
        return area, chemistry, math, content

    async def test_school_universes_are_isolated_and_support_multiple_bindings(self):
        async with self.session_factory() as session:
            admin = PlatformAdminService(session)
            school_a = await admin.create_school(performed_by_external_id="admin", code="UA", name="School A")
            school_b = await admin.create_school(performed_by_external_id="admin", code="UB", name="School B")
            service = PedagogicalUniverseService(session)
            universe_a = await service.create_universe(external_id="A_MEDIO", slug="a-medio", name="A Medio", owner_type="SCHOOL", owner_external_id=str(school_a.id), performed_by_external_id="admin", status="ACTIVE")
            universe_enem = await service.create_universe(external_id="A_ENEM", slug="a-enem", name="A ENEM", owner_type="SCHOOL", owner_external_id=str(school_a.id), performed_by_external_id="admin", status="ACTIVE")
            universe_b = await service.create_universe(external_id="B_MEDIO", slug="b-medio", name="B Medio", owner_type="SCHOOL", owner_external_id=str(school_b.id), performed_by_external_id="admin", status="ACTIVE")
            await service.bind(universe_id=universe_a.id, subject_type="SCHOOL", subject_external_id=str(school_a.id), priority=2)
            await service.bind(universe_id=universe_enem.id, subject_type="EXTERNAL_IDENTITY", subject_external_id="student:a", priority=1)
            await service.bind(universe_id=universe_b.id, subject_type="SCHOOL", subject_external_id=str(school_b.id))

            identity = ExternalIdentityContext(provider="test", external_user_id="student:a", institution_id=str(school_a.id))
            authorized = await service.authorized_universes(identity)
            self.assertEqual({item.id for item in authorized}, {universe_a.id, universe_enem.id})
            self.assertEqual((await service.resolve_active_universe(identity)).id, universe_a.id)
            with self.assertRaises(PermissionError):
                await service.resolve_active_universe(identity, universe_b.id)

    async def test_partner_and_independent_bindings_never_require_school(self):
        async with self.session_factory() as session:
            service = PedagogicalUniverseService(session)
            partner = await service.create_universe(external_id="QUIMICA_ENEM", slug="quimica-enem", name="Química ENEM", owner_type="PARTNER", owner_external_id="quimica-do-enem", performed_by_external_id="admin", status="ACTIVE")
            independent = await service.create_universe(external_id="ENEM", slug="enem", name="ENEM", owner_type="PLATFORM", owner_external_id=None, performed_by_external_id="admin", status="ACTIVE")
            await service.bind(universe_id=partner.id, subject_type="PRODUCT_CONTEXT", subject_external_id="quimica-do-enem")
            await service.bind(universe_id=independent.id, subject_type="EXTERNAL_IDENTITY", subject_external_id="independent:a")
            self.assertEqual((await service.resolve_active_universe(ExternalIdentityContext(provider="test", external_user_id="independent:a"))).id, independent.id)
            partner_identity = ExternalIdentityContext(provider="test", external_user_id="pupil", metadata={"product_context": "quimica-do-enem"})
            self.assertEqual((await service.resolve_active_universe(partner_identity)).id, partner.id)

    async def test_catalog_scope_reuses_area_hierarchy_and_audits_configuration(self):
        async with self.session_factory() as session:
            area, chemistry, math, content = await self._catalog(session)
            service = PedagogicalUniverseService(session)
            universe = await service.create_universe(external_id="NATUREZA", slug="natureza", name="Natureza", owner_type="PLATFORM", owner_external_id=None, performed_by_external_id="admin", status="ACTIVE")
            await service.add_catalog_scope(universe_id=universe.id, catalog_node_id=area.id, scope_kind="AREA")
            self.assertTrue(await service.contains_catalog_node(universe.id, content.id))
            self.assertFalse(await service.contains_catalog_node(universe.id, math.id))
            chemistry_question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
            math_question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
            session.add_all([chemistry_question, math_question])
            await session.flush()
            chemistry_version = QuestionVersion(question_id=chemistry_question.id, version_kind="official_original", canonical_text="Química", content_hash="chemistry", recommended_difficulty="EASY")
            math_version = QuestionVersion(question_id=math_question.id, version_kind="official_original", canonical_text="Matemática", content_hash="math", recommended_difficulty="EASY")
            session.add_all([chemistry_version, math_version])
            await session.flush()
            session.add_all([
                ContentQuestionLink(content_node_id=content.id, question_version_id=chemistry_version.id),
                ContentQuestionLink(content_node_id=math.id, question_version_id=math_version.id),
            ])
            await session.commit()
            self.assertTrue(await service.contains_question_version(universe.id, chemistry_version.id))
            self.assertFalse(await service.contains_question_version(universe.id, math_version.id))
            await service.update_configuration(universe_id=universe.id, configuration={"matrix": "ENEM_2026"}, configuration_version="v2", performed_by_external_id="admin")
            actions = (await session.execute(select(AdminAuditLog.action).where(AdminAuditLog.entity_id == str(universe.id)))).scalars().all()
            self.assertIn("PEDAGOGICAL_UNIVERSE_CREATED", actions)
            self.assertIn("PEDAGOGICAL_UNIVERSE_CONFIGURATION_UPDATED", actions)

    async def test_list_catalog_scopes_returns_and_omits_removed_scopes(self):
        """The admin UI needs to read back which disciplines a universe is
        currently scoped to (to pre-check the right boxes) - there was no
        way to list scopes, only add/remove them blind."""
        async with self.session_factory() as session:
            area, chemistry, math, content = await self._catalog(session)
            service = PedagogicalUniverseService(session)
            universe = await service.create_universe(external_id="NATUREZA2", slug="natureza2", name="Natureza 2", owner_type="PLATFORM", owner_external_id=None, performed_by_external_id="admin", status="ACTIVE")

            self.assertEqual(await service.list_catalog_scopes(universe.id), [])

            scope_chem = await service.add_catalog_scope(universe_id=universe.id, catalog_node_id=chemistry.id, scope_kind="DISCIPLINE")
            scope_math = await service.add_catalog_scope(universe_id=universe.id, catalog_node_id=math.id, scope_kind="DISCIPLINE")

            scopes = await service.list_catalog_scopes(universe.id)
            self.assertEqual({s.catalog_node_id for s in scopes}, {chemistry.id, math.id})

            await service.remove_catalog_scope(scope_id=scope_math.id, performed_by_external_id="admin")
            scopes_after = await service.list_catalog_scopes(universe.id)
            self.assertEqual({s.catalog_node_id for s in scopes_after}, {chemistry.id})

    async def test_candidate_query_filters_competing_eligible_questions_by_universe(self):
        async with self.session_factory() as session:
            area, _, math, chemistry_content = await self._catalog(session)
            chemistry_question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
            math_question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
            session.add_all([chemistry_question, math_question])
            await session.flush()
            chemistry_version = QuestionVersion(question_id=chemistry_question.id, version_kind="official_original", canonical_text="Química elegível", content_hash="chem-query", recommended_difficulty="EASY")
            math_version = QuestionVersion(question_id=math_question.id, version_kind="official_original", canonical_text="Matemática elegível", content_hash="math-query", recommended_difficulty="EASY")
            session.add_all([chemistry_version, math_version])
            await session.flush()
            session.add_all([
                ContentQuestionLink(content_node_id=chemistry_content.id, question_version_id=chemistry_version.id),
                ContentQuestionLink(content_node_id=math.id, question_version_id=math_version.id),
            ])
            universe = await PedagogicalUniverseService(session).create_universe(
                external_id="CHEM_QUERY", slug="chem-query", name="Chemistry", owner_type="PLATFORM",
                owner_external_id=None, performed_by_external_id="admin", status="ACTIVE",
            )
            await PedagogicalUniverseService(session).add_catalog_scope(
                universe_id=universe.id, catalog_node_id=area.id, scope_kind="AREA"
            )
            repository = QuestionSelectionRepository(session)
            chemistry_candidates = await repository.list_diagnostic_candidate_versions(
                chemistry_content.id, difficulty_level="EASY", universe_id=universe.id
            )
            math_candidates = await repository.list_diagnostic_candidate_versions(
                math.id, difficulty_level="EASY", universe_id=universe.id
            )
            self.assertEqual([candidate.id for candidate in chemistry_candidates], [chemistry_version.id])
            self.assertEqual(math_candidates, [])