"""Direct service-layer coverage for services/questions.py's formatting
helpers (_classification, _difficulty, _source, get_question's None/answer-
key branches).

QuestionService.get_question is reachable via GET /api/v1/questions/{id}
(covered at the HTTP layer by test_route_coverage_questions.py), but a fully
populated BNCC classification + difficulty estimate + answer key needs
Taxonomy/TaxonomyNode/DifficultyEstimate/ExamBooklet fixtures that are pure
formatting concerns, not routing/authorization ones - exercised directly
against the service here instead of re-deriving that fixture through HTTP.
QuestionService.list_questions is unreachable through the HTTP route (its
`if/else` both return before ever reaching `return await
service.list_questions(...)`) but is still real, directly-callable service
code - see QuestionServiceListQuestionsTests below, which exercises it
directly instead of leaving it uncovered.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    DifficultyEstimate,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    Question,
    QuestionClassification,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
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

    async def test_classification_with_bncc_taxonomy_but_malformed_node_types_is_omitted(self):
        # _classification's SECOND guard (distinct from the "not bncc" guard
        # above): the repository's active_classifications query already
        # filters on Taxonomy.code == "bncc", so a malformed-but-bncc
        # classification (wrong node_type, or skill not a child of the
        # competency) reaches _classification and must be dropped there
        # instead of raising or returning a nonsensical schema.
        async with self.factory() as session:
            taxonomy = Taxonomy(code="bncc", name="BNCC", version="2018")
            session.add(taxonomy)
            await session.flush()
            # Two competency-type nodes, no parent/child skill relationship
            # between them - competency_node.node_type is "competency" (ok)
            # but skill_node.node_type is ALSO "competency" (not "skill"),
            # which should trip the guard.
            competency = TaxonomyNode(taxonomy_id=taxonomy.id, code="EM13CNT101",
                                       name="Competencia 1", node_type="competency")
            session.add(competency)
            await session.flush()
            bogus_skill = TaxonomyNode(taxonomy_id=taxonomy.id, parent_id=competency.id, code="EM13CNT101-BOGUS",
                                        name="Not really a skill", node_type="competency")
            session.add(bogus_skill)
            await session.flush()

            question = Question(question_type="MULTIPLE_CHOICE", status="PUBLISHED",
                                 visibility_scope="PUBLIC", validation_status="approved", origin_type="PLATFORM")
            session.add(question)
            await session.flush()
            version = QuestionVersion(question_id=question.id, version_kind="official_original",
                                       canonical_text="Questao com no de habilidade malformado.", content_hash=f"h-{uuid.uuid4().hex}")
            session.add(version)
            await session.flush()
            session.add(QuestionClassification(
                question_version_id=version.id, taxonomy_id=taxonomy.id,
                competency_node_id=competency.id, skill_node_id=bogus_skill.id,
                is_primary=True, status="active", confidence="0.9",
                source="ai", classifier_version="v1",
            ))
            await session.commit()
            service = QuestionService(QuestionRepository(session))
            result = await service.get_question(question.id)

        self.assertIsNone(result.classification)

    async def test_get_question_include_answer_key_deduplicates_and_resolves_full_source_chain(self):
        # Builds the full Institution -> Exam -> ExamApplication ->
        # ExamBooklet -> SourceDocument -> AnswerKeyRevision -> BookletQuestion
        # -> AnswerKeyEntry chain to exercise both the include_answer_key
        # loop's dedup branch (two official revisions answering the SAME
        # occurrence - repository orders newest revision first, so the older
        # duplicate must be skipped via `continue`) and _source()'s full
        # institution/exam/application/booklet resolution, neither of which
        # the "no booklet occurrences" test above can reach.
        async with self.factory() as session:
            institution = Institution(code="INEP", name="INEP")
            session.add(institution)
            await session.flush()
            exam = Exam(institution_id=institution.id, code="ENEM", name="ENEM")
            session.add(exam)
            await session.flush()
            application = ExamApplication(exam_id=exam.id, year=2022, application_type="REGULAR", day=2)
            session.add(application)
            await session.flush()
            booklet = ExamBooklet(exam_application_id=application.id, code="D2_AZUL", color="AZUL", language="PT")
            session.add(booklet)
            await session.flush()
            source_document = SourceDocument(
                exam_application_id=application.id,
                exam_booklet_id=booklet.id,
                document_type="ANSWER_KEY",
                source_url="https://example.test/gabarito.pdf",
                acquired_at=datetime.now(timezone.utc),
                content_hash=f"src-hash-{uuid.uuid4().hex}",
            )
            session.add(source_document)
            await session.flush()
            revision_old = AnswerKeyRevision(source_document_id=source_document.id, revision_number=1)
            revision_new = AnswerKeyRevision(source_document_id=source_document.id, revision_number=2)
            session.add_all([revision_old, revision_new])
            await session.flush()

            question = Question(question_type="MULTIPLE_CHOICE", status="PUBLISHED",
                                 visibility_scope="PUBLIC", validation_status="approved", origin_type="PLATFORM")
            session.add(question)
            await session.flush()
            version = QuestionVersion(question_id=question.id, version_kind="official_original",
                                       canonical_text="Questao oficial com gabarito revisado.", content_hash=f"h-{uuid.uuid4().hex}")
            session.add(version)
            await session.flush()
            booklet_question = BookletQuestion(exam_booklet_id=booklet.id, question_version_id=version.id,
                                                position=91, official_number=91)
            session.add(booklet_question)
            await session.flush()
            option_c = QuestionOption(question_version_id=version.id, option_key="C", position=3, text="Alternativa C")
            option_d = QuestionOption(question_version_id=version.id, option_key="D", position=4, text="Alternativa D")
            session.add_all([option_c, option_d])
            await session.flush()

            # revision_old ("C") is later superseded by revision_new ("D")
            # for the SAME occurrence - only the newest should survive.
            session.add_all([
                AnswerKeyEntry(answer_key_revision_id=revision_old.id, booklet_question_id=booklet_question.id,
                                official_answer_label="C", resolved_option_id=option_c.id),
                AnswerKeyEntry(answer_key_revision_id=revision_new.id, booklet_question_id=booklet_question.id,
                                official_answer_label="D", resolved_option_id=option_d.id),
            ])
            await session.commit()

            service = QuestionService(QuestionRepository(session))
            result = await service.get_question(question.id, include_answer_key=True)

        self.assertIsNotNone(result.answer_key)
        self.assertEqual(len(result.answer_key), 1)
        entry = result.answer_key[0]
        self.assertEqual(entry.label, "D")
        self.assertEqual(entry.revision, 2)
        self.assertTrue(entry.official)
        self.assertEqual(entry.booklet_code, "D2_AZUL")

        self.assertEqual(len(result.sources), 1)
        source = result.sources[0]
        self.assertEqual(source.institution.code, "INEP")
        self.assertEqual(source.exam.code, "ENEM")
        self.assertEqual(source.application.year, 2022)
        self.assertEqual(source.application.day, 2)
        self.assertEqual(len(source.booklets), 1)
        self.assertEqual(source.booklets[0].code, "D2_AZUL")
        self.assertEqual(source.booklets[0].official_number, 91)


class QuestionServiceListQuestionsTests(unittest.IsolatedAsyncioTestCase):
    """QuestionService.list_questions has no live HTTP caller: reading
    api/routes/questions.py's `list_questions` route shows its `if/else`
    both return before ever reaching `return await service.list_questions(...)`
    - that call is dead code from the route's perspective. It remains real,
    directly-callable service code, so it is exercised directly here rather
    than left uncovered."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_list_questions_applies_filters_and_builds_pagination(self):
        async with self.factory() as session:
            institution = Institution(code="INEP", name="INEP")
            session.add(institution)
            await session.flush()
            exam = Exam(institution_id=institution.id, code="ENEM", name="ENEM")
            session.add(exam)
            await session.flush()
            application = ExamApplication(exam_id=exam.id, year=2023, application_type="REGULAR", day=1)
            session.add(application)
            await session.flush()
            booklet = ExamBooklet(exam_application_id=application.id, code="AZUL")
            session.add(booklet)
            await session.flush()

            matching = Question(question_type="MULTIPLE_CHOICE", status="PUBLISHED",
                                 visibility_scope="PUBLIC", validation_status="approved", origin_type="PLATFORM")
            other = Question(question_type="MULTIPLE_CHOICE", status="PUBLISHED",
                              visibility_scope="PUBLIC", validation_status="approved", origin_type="PLATFORM")
            session.add_all([matching, other])
            await session.flush()
            matching_version = QuestionVersion(question_id=matching.id, version_kind="official_original",
                                                canonical_text="Questao oficial sobre estequiometria.",
                                                content_hash=f"h-{uuid.uuid4().hex}")
            other_version = QuestionVersion(question_id=other.id, version_kind="official_original",
                                             canonical_text="Questao sem relacao nenhuma com o filtro.",
                                             content_hash=f"h-{uuid.uuid4().hex}")
            session.add_all([matching_version, other_version])
            await session.flush()
            session.add(BookletQuestion(exam_booklet_id=booklet.id, question_version_id=matching_version.id,
                                         position=1, official_number=1))
            await session.commit()

            service = QuestionService(QuestionRepository(session))
            result = await service.list_questions(
                page=1, limit=10, institution_code="INEP", content="estequiometria",
            )

        self.assertEqual(result.pagination.page, 1)
        self.assertEqual(result.pagination.limit, 10)
        self.assertEqual(result.pagination.total, 1)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].id, matching.id)

    async def test_list_questions_empty_result_when_no_match(self):
        async with self.factory() as session:
            service = QuestionService(QuestionRepository(session))
            result = await service.list_questions(page=1, limit=5)

        self.assertEqual(result.items, [])
        self.assertEqual(result.pagination.total, 0)


if __name__ == "__main__":
    unittest.main()
