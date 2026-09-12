import unittest

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, ContentQuestionLink, Question, QuestionOption, QuestionVersion
from agente_ia_edu.services.initial_diagnostic import InitialDiagnosticService
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.proficiency import DiagnosticConcentrationPolicy


class TestDiagnosticMultidisciplinaryOrchestration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_discipline(self, session, name, position):
        root = CatalogNode(node_type="DISCIPLINE", name=name, position=position, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id
        content = CatalogNode(parent_id=root.id, root_id=root.id, node_type="CONTENT", name=f"{name} base", position=1, active=True)
        session.add(content)
        await session.flush()
        question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
        session.add(question)
        await session.flush()
        version = QuestionVersion(question_id=question.id, version_kind="official_original", canonical_text=name, content_hash=name, recommended_difficulty="EASY")
        session.add(version)
        await session.flush()
        option = QuestionOption(question_version_id=version.id, option_key="A", position=1, text="Correta", is_valid_option=True)
        session.add_all([option, ContentQuestionLink(content_node_id=content.id, question_version_id=version.id)])
        return root, content, option

    async def test_global_mode_balances_the_next_question_across_disciplines(self):
        async with self.session_factory() as session:
            math_root, _, math_option = await self._seed_discipline(session, "Matemática", 1)
            chemistry_root, _, _ = await self._seed_discipline(session, "Química", 2)
            await session.commit()
            service = InitialDiagnosticService(session, KnowledgeService(session))
            diagnostic, first = await service.start_diagnostic(
                student_id="student:global",
                discipline="Matemática",
                metadata={"entry_profile": {"diagnostic_mode": "GLOBAL"}},
            )
            first_node = await session.get(CatalogNode, first.content_node_id)
            self.assertEqual(first_node.root_id, math_root.id)
            _, _, complete, second = await service.answer_question(
                diagnostic_id=diagnostic.id, selection_id=first.id, selected_option_id=math_option.id
            )
            self.assertFalse(complete)
            self.assertEqual(diagnostic.metadata_["latest_decision"]["reason"], "GLOBAL_COVERAGE_INCOMPLETE")
            self.assertIn("Química", diagnostic.metadata_["global_coverage"]["residual_uncertainty"])
            self.assertIsNotNone(second)
            second_node = await session.get(CatalogNode, second.content_node_id)
            self.assertEqual(second_node.root_id, chemistry_root.id)

    async def test_discipline_mode_does_not_expand_to_other_disciplines(self):
        async with self.session_factory() as session:
            math_root, _, math_option = await self._seed_discipline(session, "Matemática", 1)
            await self._seed_discipline(session, "Química", 2)
            await session.commit()
            service = InitialDiagnosticService(session, KnowledgeService(session))
            diagnostic, first = await service.start_diagnostic(student_id="student:subject", discipline="Matemática")
            _, _, _, second = await service.answer_question(
                diagnostic_id=diagnostic.id, selection_id=first.id, selected_option_id=math_option.id
            )
            self.assertEqual((await session.get(CatalogNode, first.content_node_id)).root_id, math_root.id)
            self.assertIsNone(second)

    async def test_objective_mode_uses_only_prioritized_disciplines(self):
        async with self.session_factory() as session:
            await self._seed_discipline(session, "Matemática", 1)
            chemistry_root, _, _ = await self._seed_discipline(session, "Química", 2)
            await session.commit()
            service = InitialDiagnosticService(session, KnowledgeService(session))
            _, selection = await service.start_diagnostic(
                student_id="student:objective",
                discipline="Matemática",
                metadata={"entry_profile": {
                    "diagnostic_mode": "OBJECTIVE",
                    "priority_disciplines": ["Química"],
                }},
            )
            node = await session.get(CatalogNode, selection.content_node_id)
            self.assertEqual(node.root_id, chemistry_root.id)

    async def test_selector_switches_content_when_real_alternative_exists(self):
        async with self.session_factory() as session:
            root = CatalogNode(node_type="DISCIPLINE", name="Biologia", position=1, active=True)
            session.add(root)
            await session.flush()
            root.root_id = root.id
            contents = []
            options = []
            for position, name in enumerate(("Citologia", "Genética"), start=1):
                content = CatalogNode(parent_id=root.id, root_id=root.id, node_type="CONTENT", name=name, position=position, active=True)
                session.add(content)
                await session.flush()
                question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
                session.add(question)
                await session.flush()
                version = QuestionVersion(question_id=question.id, version_kind="official_original", canonical_text=name, content_hash=name, recommended_difficulty="EASY")
                session.add(version)
                await session.flush()
                option = QuestionOption(question_version_id=version.id, option_key="A", position=1, text="Correta", is_valid_option=True)
                session.add_all([option, ContentQuestionLink(content_node_id=content.id, question_version_id=version.id)])
                if name == "Citologia":
                    extra_question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
                    session.add(extra_question)
                    await session.flush()
                    extra_version = QuestionVersion(question_id=extra_question.id, version_kind="official_original", canonical_text=f"{name} extra", content_hash=f"{name}-extra", recommended_difficulty="EASY")
                    session.add(extra_version)
                    await session.flush()
                    session.add_all([
                        QuestionOption(question_version_id=extra_version.id, option_key="A", position=1, text="Correta", is_valid_option=True),
                        ContentQuestionLink(content_node_id=content.id, question_version_id=extra_version.id),
                    ])
                contents.append(content)
                options.append(option)
            await session.commit()
            service = InitialDiagnosticService(
                session, KnowledgeService(session),
                concentration_policy=DiagnosticConcentrationPolicy(max_consecutive_same_content=1, max_consecutive_same_discipline=3),
            )
            diagnostic, first = await service.start_diagnostic(student_id="student:concentration", discipline="Biologia")
            self.assertEqual(first.content_node_id, contents[0].id)
            _, _, complete, second = await service.answer_question(
                diagnostic_id=diagnostic.id, selection_id=first.id, selected_option_id=options[0].id
            )
            self.assertFalse(complete)
            self.assertEqual(second.content_node_id, contents[1].id)
            await session.refresh(diagnostic)
            self.assertEqual(diagnostic.metadata_["selection_trace"][-1]["concentration_decision"], "SWITCH_CONTENT")