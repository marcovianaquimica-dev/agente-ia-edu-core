"""Direct service-layer coverage for services/questions.py's formatting
helpers (_classification, _difficulty, _source, get_question's None/answer-
key branches).

QuestionService.get_question is reachable via GET /api/v1/questions/{id}
(covered at the HTTP layer by test_route_coverage_questions.py), but a fully
populated BNCC classification + difficulty estimate + answer key needs
Taxonomy/TaxonomyNode/DifficultyEstimate/ExamBooklet fixtures that are pure
formatting concerns, not routing/authorization ones - exercised directly
against the service here instead of re-deriving that fixture through HTTP.
QuestionService.list_questions itself is NOT covered here: reading
api/routes/questions.py's list_questions shows its `if/else` both return
before ever reaching `return await service.list_questions(...)` - that call
is dead code, unreachable through the route (see session summary).
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    DifficultyEstimate,
    Question,
    QuestionClassification,
    QuestionOption,
    QuestionVersion,
    Taxonomy,
    TaxonomyNode,
)
from agente_ia_edu.repositories.questions import QuestionRepository
from agente_ia_edu.services.questions import QuestionService


class QuestionServiceGetQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_get_question_returns_none_for_unknown_id(self):
        async with self.factory() as session:
            service = QuestionService(QuestionRepository(session))
            result = await service.get_question(uuid.uuid4())
        self.assertIsNone(result)

    async def test_get_question_with_no_answer_key_requested_omits_it(self):
        async with self.factory() as session:
            question = Question(question_type="MULTIPLE_CHOICE", status="PUBLISHED",
                                 visibility_scope="PUBLIC", validation_status="approved", origin_type="PLATFORM")
            session.add(question)
            await session.flush()
            version = QuestionVersion(question_id=question.id, version_kind="official_original",
                                       canonical_text="Enunciado simples.", content_hash=f"h-{uuid.uuid4().hex}")
            session.add(version)
            await session.flush()
            session.add(QuestionOption(question_version_id=version.id, option_key="A", position=1, text="Alternativa A"))
            await session.commit()
            service = QuestionService(QuestionRepository(session))
            result = await service.get_question(question.id)
        self.assertIsNotNone(result)
        self.assertIsNone(result.answer_key)
        self.assertIsNone(result.classification)
        self.assertIsNone(result.difficulty)

    async def test_get_question_include_answer_key_with_no_booklet_occurrences(self):
        async with self.factory() as session:
            question = Question(question_type="MULTIPLE_CHOICE", status="PUBLISHED",
                                 visibility_scope="PUBLIC", validation_status="approved", origin_type="PLATFORM")
            session.add(question)
            await session.flush()
            version = QuestionVersion(question_id=question.id, version_kind="official_original",
                                       canonical_text="Enunciado sem prova associada.", content_hash=f"h-{uuid.uuid4().hex}")
            session.add(version)
            await session.commit()
            service = QuestionService(QuestionRepository(session))
            result = await service.get_question(question.id, include_answer_key=True)
        # A question with no ExamBooklet occurrences (e.g. an
        # authorial/teacher question) has answer_key=[] rather than None -
        # the include_answer_key branch runs, it just has nothing to report.
        self.assertIsNotNone(result)
        self.assertEqual(result.answer_key, [])

    async def test_get_question_full_bncc_classification_and_difficulty(self):
        async with self.factory() as session:
            taxonomy = Taxonomy(code="bncc", name="BNCC", version="2018")
            session.add(taxonomy)
            await session.flush()
            competency = TaxonomyNode(taxonomy_id=taxonomy.id, code="EM13CNT101",
                                       name="Competencia 1", node_type="competency")
            session.add(competency)
            await session.flush()
            skill = TaxonomyNode(taxonomy_id=taxonomy.id, parent_id=competency.id, code="EM13CNT101-H1",
                                  name="Habilidade 1", node_type="skill")
            session.add(skill)
            await session.flush()

            question = Question(question_type="MULTIPLE_CHOICE", status="PUBLISHED",
                                 visibility_scope="PUBLIC", validation_status="approved", origin_type="PLATFORM")
            session.add(question)
            await session.flush()
            version = QuestionVersion(question_id=question.id, version_kind="official_original",
                                       canonical_text="Questao classificada com BNCC.", content_hash=f"h-{uuid.uuid4().hex}")
            session.add(version)
            await session.flush()
            session.add_all([
                QuestionClassification(
                    question_version_id=version.id, taxonomy_id=taxonomy.id,
                    competency_node_id=competency.id, skill_node_id=skill.id,
                    is_primary=True, status="active", confidence="0.9",
                    source="ai", classifier_version="v1",
                ),
                DifficultyEstimate(
                    question_version_id=version.id, score="62.5", band="MEDIUM",
                    confidence="0.8", source="empirical", method="irt", method_version="v2",
                    status="active",
                ),
            ])
            await session.commit()
            service = QuestionService(QuestionRepository(session))
            result = await service.get_question(version.question_id)

        self.assertIsNotNone(result.classification)
        self.assertEqual(result.classification.taxonomy.code, "bncc")
        self.assertEqual(result.classification.competency.code, "EM13CNT101")
        self.assertEqual(result.classification.skill.code, "EM13CNT101-H1")
        self.assertIsNotNone(result.difficulty)
        self.assertEqual(result.difficulty.band, "MEDIUM")
        self.assertEqual(result.difficulty.method, "irt")

    async def test_classification_from_a_non_bncc_taxonomy_is_omitted(self):
        # _classification only ever surfaces "bncc"-coded taxonomies (spec
        # decision baked into QuestionService._classification) - any other
        # active primary classification is silently dropped from the API
        # response rather than raising, which this test documents.
        async with self.factory() as session:
            taxonomy = Taxonomy(code="enem-matrix", name="ENEM Matrix", version="2020")
            session.add(taxonomy)
            await session.flush()
            node = TaxonomyNode(taxonomy_id=taxonomy.id, code="C1", name="Competencia", node_type="competency")
            session.add(node)
            await session.flush()
            skill = TaxonomyNode(taxonomy_id=taxonomy.id, parent_id=node.id, code="C1-H1", name="Habilidade", node_type="skill")
            session.add(skill)
            await session.flush()

            question = Question(question_type="MULTIPLE_CHOICE", status="PUBLISHED",
                                 visibility_scope="PUBLIC", validation_status="approved", origin_type="PLATFORM")
            session.add(question)
            await session.flush()
            version = QuestionVersion(question_id=question.id, version_kind="official_original",
                                       canonical_text="Questao com taxonomia nao-BNCC.", content_hash=f"h-{uuid.uuid4().hex}")
            session.add(version)
            await session.flush()
            session.add(QuestionClassification(
                question_version_id=version.id, taxonomy_id=taxonomy.id,
                competency_node_id=node.id, skill_node_id=skill.id,
                is_primary=True, status="active", source="ai",
            ))
            await session.commit()
            service = QuestionService(QuestionRepository(session))
            result = await service.get_question(question.id)

        self.assertIsNone(result.classification)


if __name__ == "__main__":
    unittest.main()
