"""
Direct repository-layer coverage for src/agente_ia_edu/repositories/questions.py.

Wave 8 of the overnight bug-hunting campaign - the REPOSITORY layer, previously
untouched by this campaign.

Targets:
- QuestionRepository.get_official()'s defensive "no official_original version"
  branch (the query eagerly loads all versions via selectinload without
  filtering version_kind in SQL, then checks in Python that at least one
  version is official_original before returning the Question - this is the
  untested False branch).
- QuestionRepository._apply_filters()'s exam_code and year filter branches
  (institution_code's branch is already covered by existing tests; exam_code
  and year were not).

Uses a real async SQLAlchemy session against SQLite with expire_on_commit=True
(production default, see db/session.py's create_session_factory()) per this
campaign's established pattern (tests/test_assessments_route_coverage_http.py).

NOTE ON FIXTURE STYLE: with expire_on_commit=True every attribute of an ORM
object - including its primary key - is expired by session.commit(). Reading
`obj.id` on an object created before a commit but not re-read until after it
reproduces the exact MissingGreenlet-after-commit bug shape this campaign
targets. Every id used after a commit below is therefore captured into a
plain UUID local immediately after flush(), before the commit that follows.
"""

import unittest
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    BookletQuestion,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    Question,
    QuestionVersion,
)
from agente_ia_edu.repositories.questions import QuestionRepository


class RepositoryTestCase(unittest.IsolatedAsyncioTestCase):
    """Base fixture: production-like session (expire_on_commit=True, default)."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, class_=AsyncSession)

    async def asyncTearDown(self):
        await self.engine.dispose()


class QuestionRepositoryGetOfficialTests(RepositoryTestCase):
    async def test_get_official_returns_none_when_only_non_official_versions_exist(self):
        """
        Hypothesis: a Question whose only version is NOT 'official_original'
        (e.g. still a draft/edited version, no official_original published
        yet) must not be returned by get_official() - it exists in the DB and
        matches the id lookup, but is not yet a "published official question"
        from this repository's point of view. This exercises the defensive
        `if not any(...): return None` branch that the SQL query itself does
        not filter for.
        """
        async with self.Session() as session:
            q = Question(validation_status="approved", status="DRAFT", visibility_scope="PRIVATE")
            session.add(q)
            await session.flush()
            q_id = q.id
            draft_version = QuestionVersion(
                question_id=q_id,
                version_kind="draft",
                canonical_text="rascunho ainda nao oficial",
                content_hash="hdraft",
            )
            session.add(draft_version)
            await session.commit()

            repo = QuestionRepository(session)
            result = await repo.get_official(q_id)
            self.assertIsNone(result)

    async def test_get_official_returns_question_when_official_version_exists(self):
        async with self.Session() as session:
            q = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
            session.add(q)
            await session.flush()
            q_id = q.id
            version = QuestionVersion(
                question_id=q_id,
                version_kind="official_original",
                canonical_text="questao oficial",
                content_hash="hoff",
            )
            session.add(version)
            await session.commit()

            repo = QuestionRepository(session)
            result = await repo.get_official(q_id)
            self.assertIsNotNone(result)
            self.assertEqual(result.id, q_id)

    async def test_get_official_returns_none_for_missing_question(self):
        async with self.Session() as session:
            repo = QuestionRepository(session)
            result = await repo.get_official(uuid4())
            self.assertIsNone(result)


class QuestionRepositoryApplyFiltersTests(RepositoryTestCase):
    async def _seed_exam_question(self, session, *, institution_code, exam_code, year) -> UUID:
        inst = Institution(code=institution_code, name=institution_code)
        session.add(inst)
        await session.flush()
        exam = Exam(institution_id=inst.id, code=exam_code, name=exam_code)
        session.add(exam)
        await session.flush()
        app = ExamApplication(exam_id=exam.id, year=year, application_type="regular", day=1)
        session.add(app)
        await session.flush()
        booklet = ExamBooklet(exam_application_id=app.id, code=f"CAD-{exam_code}-{year}", color="AZUL")
        session.add(booklet)
        await session.flush()

        q = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
        session.add(q)
        await session.flush()
        q_id = q.id
        version = QuestionVersion(
            question_id=q_id,
            version_kind="official_original",
            canonical_text=f"questao {exam_code} {year}",
            content_hash=f"h-{exam_code}-{year}",
        )
        session.add(version)
        await session.flush()
        session.add(
            BookletQuestion(
                exam_booklet_id=booklet.id, question_version_id=version.id, position=1, official_number=1
            )
        )
        await session.commit()
        return q_id

    async def test_exam_code_filter_narrows_count_and_list(self):
        async with self.Session() as session:
            # Distinct institution codes: Institution.code is unique, and this
            # test cares only about the exam_code filter, not real-world
            # institution/exam pairings.
            await self._seed_exam_question(session, institution_code="INST-ENEM", exam_code="ENEM", year=2023)
            await self._seed_exam_question(session, institution_code="INST-FUVEST", exam_code="FUVEST", year=2023)

            repo = QuestionRepository(session)
            enem_count = await repo.count(exam_code="ENEM")
            self.assertEqual(enem_count, 1)

            all_count = await repo.count()
            self.assertEqual(all_count, 2)

            enem_questions = await repo.list_official(offset=0, limit=10, exam_code="ENEM")
            self.assertEqual(len(enem_questions), 1)
            texts = [v.canonical_text for q in enem_questions for v in q.versions]
            self.assertTrue(any("ENEM" in t for t in texts))
            self.assertFalse(any("FUVEST" in t for t in texts))

    async def test_year_filter_narrows_count_and_list(self):
        async with self.Session() as session:
            await self._seed_exam_question(session, institution_code="INST-2022", exam_code="ENEM", year=2022)
            await self._seed_exam_question(session, institution_code="INST-2023", exam_code="ENEM", year=2023)

            repo = QuestionRepository(session)
            count_2023 = await repo.count(year=2023)
            self.assertEqual(count_2023, 1)

            questions_2023 = await repo.list_official(offset=0, limit=10, year=2023)
            self.assertEqual(len(questions_2023), 1)
            texts = [v.canonical_text for q in questions_2023 for v in q.versions]
            self.assertTrue(any("2023" in t for t in texts))
            self.assertFalse(any(" 2022" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
