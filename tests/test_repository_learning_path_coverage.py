"""
Direct repository-layer coverage for src/agente_ia_edu/repositories/learning_path.py.

Wave 8 of the overnight bug-hunting campaign - the REPOSITORY layer, previously
untouched by this campaign (waves 1-7 covered HTTP routes and services).

Targets: LearningHistoryRepository, StudentContentMasteryRepository,
PracticeSessionRepository, PracticeQuestionSelectionRepository (all read-only,
entirely uncovered by any existing test file), plus three specific branches of
QuestionSelectionRepository.list_eligible_candidate_versions
(exclude_version_ids / include_version_ids / limit) that ARE exercised by
services/learning_path.py in production but happened not to be hit by the
existing service-level test fixtures.

Uses a real async SQLAlchemy session against SQLite with expire_on_commit=True
(the production default - create_session_factory() in db/session.py is called
with no override) per this campaign's established pattern (see
tests/test_assessments_route_coverage_http.py).

NOTE ON FIXTURE STYLE: with expire_on_commit=True, EVERY attribute of an ORM
object - including its primary key - is expired by session.commit(), not just
"business" columns. Reading `obj.id` (or any attribute) on an object that was
created/flushed *before* a commit, but not re-read until *after* that commit,
triggers exactly the MissingGreenlet-after-commit bug shape this campaign
targets (a synchronous attribute access forcing an implicit lazy-reload
outside any active greenlet). That bug shape first surfaced here in early
drafts of these very fixtures (accessing `.id` right after `await
session.commit()`), which is why every helper below captures ids into plain
UUID locals immediately after `flush()` and never touches the ORM object
again afterwards - the fixture-writing equivalent of the exact bug this
campaign hunts for in application code.
"""

import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    ContentQuestionLink,
    LearningHistory,
    PracticeQuestionSelection,
    PracticeSession,
    Question,
    QuestionVersion,
    StudentContentMastery,
)
from agente_ia_edu.repositories.learning_path import (
    LearningHistoryRepository,
    PracticeQuestionSelectionRepository,
    PracticeSessionRepository,
    QuestionSelectionRepository,
    StudentContentMasteryRepository,
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

    @staticmethod
    async def _make_catalog_node(session, name="Conteudo", node_type="CONTENT") -> UUID:
        node = CatalogNode(node_type=node_type, name=name, position=1, active=True)
        session.add(node)
        await session.flush()
        node_id = node.id
        node.root_id = node_id
        return node_id

    @staticmethod
    async def _make_question_version(session, difficulty="EASY", suffix="") -> UUID:
        q = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
        session.add(q)
        await session.flush()
        v = QuestionVersion(
            question_id=q.id,
            version_kind="official_original",
            canonical_text=f"Questao {suffix}",
            statement=f"Questao {suffix}",
            content_hash=f"hash-{uuid4().hex}",
            recommended_difficulty=difficulty,
        )
        session.add(v)
        await session.flush()
        return v.id


class LearningHistoryRepositoryTests(RepositoryTestCase):
    async def test_get_by_id_returns_record_then_none_for_missing(self):
        async with self.Session() as session:
            node_id = await self._make_catalog_node(session)
            version_id = await self._make_question_version(session)
            history = LearningHistory(
                external_identity_id="student:ana",
                activity_type="INDIVIDUAL_PRACTICE",
                question_version_id=version_id,
                difficulty_level="EASY",
                is_correct=True,
                content_node_id=node_id,
            )
            session.add(history)
            await session.flush()
            history_id = history.id
            await session.commit()

            repo = LearningHistoryRepository(session)
            found = await repo.get_by_id(history_id)
            self.assertIsNotNone(found)
            self.assertEqual(found.external_identity_id, "student:ana")

            missing = await repo.get_by_id(uuid4())
            self.assertIsNone(missing)

    async def test_list_by_student_orders_desc_and_paginates(self):
        async with self.Session() as session:
            node_id = await self._make_catalog_node(session)
            version_id = await self._make_question_version(session)
            base = datetime(2026, 1, 1, tzinfo=timezone.utc)
            for i in range(3):
                session.add(
                    LearningHistory(
                        external_identity_id="student:bea",
                        activity_type="INDIVIDUAL_PRACTICE",
                        question_version_id=version_id,
                        difficulty_level="EASY",
                        content_node_id=node_id,
                        created_at=base + timedelta(minutes=i),
                    )
                )
            # A different student's row must never leak in.
            session.add(
                LearningHistory(
                    external_identity_id="student:other",
                    activity_type="INDIVIDUAL_PRACTICE",
                    question_version_id=version_id,
                    difficulty_level="EASY",
                    content_node_id=node_id,
                    created_at=base,
                )
            )
            await session.commit()

            repo = LearningHistoryRepository(session)
            page1 = await repo.list_by_student("student:bea", limit=2, offset=0)
            self.assertEqual(len(page1), 2)
            # SQLite has no native tz-aware datetime type; it round-trips as
            # naive, so compare naive-to-naive here.
            self.assertEqual(page1[0].created_at.replace(tzinfo=None), (base + timedelta(minutes=2)).replace(tzinfo=None))
            self.assertEqual(page1[1].created_at.replace(tzinfo=None), (base + timedelta(minutes=1)).replace(tzinfo=None))

            page2 = await repo.list_by_student("student:bea", limit=2, offset=2)
            self.assertEqual(len(page2), 1)
            self.assertEqual(page2[0].created_at.replace(tzinfo=None), base.replace(tzinfo=None))

    async def test_list_by_content_filters_by_student_and_content(self):
        async with self.Session() as session:
            node_a_id = await self._make_catalog_node(session, name="A")
            node_b_id = await self._make_catalog_node(session, name="B")
            version_id = await self._make_question_version(session)
            session.add_all(
                [
                    LearningHistory(
                        external_identity_id="student:cid",
                        activity_type="INDIVIDUAL_PRACTICE",
                        question_version_id=version_id,
                        difficulty_level="EASY",
                        content_node_id=node_a_id,
                    ),
                    LearningHistory(
                        external_identity_id="student:cid",
                        activity_type="INDIVIDUAL_PRACTICE",
                        question_version_id=version_id,
                        difficulty_level="EASY",
                        content_node_id=node_b_id,
                    ),
                    LearningHistory(
                        external_identity_id="student:other",
                        activity_type="INDIVIDUAL_PRACTICE",
                        question_version_id=version_id,
                        difficulty_level="EASY",
                        content_node_id=node_a_id,
                    ),
                ]
            )
            await session.commit()

            repo = LearningHistoryRepository(session)
            results = await repo.list_by_content("student:cid", node_a_id)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].content_node_id, node_a_id)

    async def test_count_by_student_content(self):
        async with self.Session() as session:
            node_id = await self._make_catalog_node(session)
            other_node_id = await self._make_catalog_node(session, name="other")
            version_id = await self._make_question_version(session)
            for _ in range(3):
                session.add(
                    LearningHistory(
                        external_identity_id="student:dan",
                        activity_type="INDIVIDUAL_PRACTICE",
                        question_version_id=version_id,
                        difficulty_level="EASY",
                        content_node_id=node_id,
                    )
                )
            session.add(
                LearningHistory(
                    external_identity_id="student:dan",
                    activity_type="INDIVIDUAL_PRACTICE",
                    question_version_id=version_id,
                    difficulty_level="EASY",
                    content_node_id=other_node_id,
                )
            )
            await session.commit()

            repo = LearningHistoryRepository(session)
            self.assertEqual(await repo.count_by_student_content("student:dan", node_id), 3)
            self.assertEqual(await repo.count_by_student_content("student:absent", node_id), 0)


class StudentContentMasteryRepositoryTests(RepositoryTestCase):
    async def test_get_by_student_content_found_and_none(self):
        async with self.Session() as session:
            node_id = await self._make_catalog_node(session)
            mastery = StudentContentMastery(
                external_identity_id="student:eli",
                content_node_id=node_id,
                mastery_score=42,
                current_level="MEDIUM",
            )
            session.add(mastery)
            await session.commit()

            repo = StudentContentMasteryRepository(session)
            found = await repo.get_by_student_content("student:eli", node_id)
            self.assertIsNotNone(found)
            self.assertEqual(float(found.mastery_score), 42)

            other_node_id = await self._make_catalog_node(session, name="other")
            await session.commit()
            missing = await repo.get_by_student_content("student:eli", other_node_id)
            self.assertIsNone(missing)

    async def test_list_by_student_orders_by_mastery_score_desc(self):
        async with self.Session() as session:
            n1_id = await self._make_catalog_node(session, name="n1")
            n2_id = await self._make_catalog_node(session, name="n2")
            n3_id = await self._make_catalog_node(session, name="n3")
            session.add_all(
                [
                    StudentContentMastery(
                        external_identity_id="student:fab", content_node_id=n1_id,
                        mastery_score=30, current_level="EASY",
                    ),
                    StudentContentMastery(
                        external_identity_id="student:fab", content_node_id=n2_id,
                        mastery_score=90, current_level="HARD",
                    ),
                    StudentContentMastery(
                        external_identity_id="student:fab", content_node_id=n3_id,
                        mastery_score=60, current_level="MEDIUM",
                    ),
                    StudentContentMastery(
                        external_identity_id="student:other", content_node_id=n1_id,
                        mastery_score=99, current_level="HARD",
                    ),
                ]
            )
            await session.commit()

            repo = StudentContentMasteryRepository(session)
            results = await repo.list_by_student("student:fab")
            self.assertEqual([float(r.mastery_score) for r in results], [90, 60, 30])

    async def test_list_by_level_filters_to_matching_difficulty(self):
        async with self.Session() as session:
            n1_id = await self._make_catalog_node(session, name="n1")
            n2_id = await self._make_catalog_node(session, name="n2")
            session.add_all(
                [
                    StudentContentMastery(
                        external_identity_id="student:gui", content_node_id=n1_id,
                        mastery_score=10, current_level="EASY",
                    ),
                    StudentContentMastery(
                        external_identity_id="student:gui", content_node_id=n2_id,
                        mastery_score=80, current_level="HARD",
                    ),
                ]
            )
            await session.commit()

            repo = StudentContentMasteryRepository(session)
            results = await repo.list_by_level("student:gui", "HARD")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].content_node_id, n2_id)

    async def test_get_lowest_mastery_and_empty_case(self):
        async with self.Session() as session:
            n1_id = await self._make_catalog_node(session, name="n1")
            n2_id = await self._make_catalog_node(session, name="n2")
            session.add_all(
                [
                    StudentContentMastery(
                        external_identity_id="student:hel", content_node_id=n1_id,
                        mastery_score=70, current_level="MEDIUM",
                    ),
                    StudentContentMastery(
                        external_identity_id="student:hel", content_node_id=n2_id,
                        mastery_score=15, current_level="EASY",
                    ),
                ]
            )
            await session.commit()

            repo = StudentContentMasteryRepository(session)
            lowest = await repo.get_lowest_mastery("student:hel")
            self.assertEqual(lowest.content_node_id, n2_id)

            empty = await repo.get_lowest_mastery("student:nobody")
            self.assertIsNone(empty)


class PracticeSessionRepositoryTests(RepositoryTestCase):
    async def test_get_by_id_found_and_missing(self):
        async with self.Session() as session:
            ps = PracticeSession(external_identity_id="student:ivo", status="active")
            session.add(ps)
            await session.flush()
            ps_id = ps.id
            await session.commit()

            repo = PracticeSessionRepository(session)
            found = await repo.get_by_id(ps_id)
            self.assertIsNotNone(found)
            missing = await repo.get_by_id(uuid4())
            self.assertIsNone(missing)

    async def test_list_by_student_with_and_without_status_filter(self):
        async with self.Session() as session:
            session.add_all(
                [
                    PracticeSession(external_identity_id="student:joa", status="active"),
                    PracticeSession(external_identity_id="student:joa", status="completed"),
                    PracticeSession(external_identity_id="student:other", status="active"),
                ]
            )
            await session.commit()

            repo = PracticeSessionRepository(session)
            all_sessions = await repo.list_by_student("student:joa")
            self.assertEqual(len(all_sessions), 2)

            active_only = await repo.list_by_student("student:joa", status="active")
            self.assertEqual(len(active_only), 1)
            self.assertEqual(active_only[0].status, "active")

    async def test_get_active_session_found_and_none(self):
        async with self.Session() as session:
            session.add(PracticeSession(external_identity_id="student:ken", status="completed"))
            await session.commit()

            repo = PracticeSessionRepository(session)
            none_found = await repo.get_active_session("student:ken")
            self.assertIsNone(none_found)

            session.add(PracticeSession(external_identity_id="student:ken", status="active"))
            await session.commit()

            found = await repo.get_active_session("student:ken")
            self.assertIsNotNone(found)
            self.assertEqual(found.status, "active")


class PracticeQuestionSelectionRepositoryTests(RepositoryTestCase):
    async def test_get_by_id_and_list_by_session_ordered_by_position(self):
        async with self.Session() as session:
            version_id = await self._make_question_version(session)
            ps = PracticeSession(external_identity_id="student:lea", status="active")
            session.add(ps)
            await session.flush()
            ps_id = ps.id
            sel1 = PracticeQuestionSelection(
                practice_session_id=ps_id, question_version_id=version_id,
                difficulty_level="EASY", position=1,
            )
            session.add_all(
                [
                    PracticeQuestionSelection(
                        practice_session_id=ps_id, question_version_id=version_id,
                        difficulty_level="EASY", position=3,
                    ),
                    sel1,
                    PracticeQuestionSelection(
                        practice_session_id=ps_id, question_version_id=version_id,
                        difficulty_level="EASY", position=2,
                    ),
                ]
            )
            await session.flush()
            sel1_id = sel1.id
            await session.commit()

            repo = PracticeQuestionSelectionRepository(session)
            found = await repo.get_by_id(sel1_id)
            self.assertIsNotNone(found)
            missing = await repo.get_by_id(uuid4())
            self.assertIsNone(missing)

            ordered = await repo.list_by_session(ps_id)
            self.assertEqual([s.position for s in ordered], [1, 2, 3])

    async def test_count_by_session_and_count_answered(self):
        async with self.Session() as session:
            version_id = await self._make_question_version(session)
            ps = PracticeSession(external_identity_id="student:mar", status="active")
            session.add(ps)
            await session.flush()
            ps_id = ps.id
            session.add_all(
                [
                    PracticeQuestionSelection(
                        practice_session_id=ps_id, question_version_id=version_id,
                        difficulty_level="EASY", position=1,
                        answered_at=datetime.now(timezone.utc),
                    ),
                    PracticeQuestionSelection(
                        practice_session_id=ps_id, question_version_id=version_id,
                        difficulty_level="EASY", position=2, answered_at=None,
                    ),
                    PracticeQuestionSelection(
                        practice_session_id=ps_id, question_version_id=version_id,
                        difficulty_level="EASY", position=3,
                        answered_at=datetime.now(timezone.utc),
                    ),
                ]
            )
            await session.commit()

            repo = PracticeQuestionSelectionRepository(session)
            self.assertEqual(await repo.count_by_session(ps_id), 3)
            self.assertEqual(await repo.count_answered(ps_id), 2)


class QuestionSelectionRepositoryEligibleCandidatesTests(RepositoryTestCase):
    """
    Covers the three branches of list_eligible_candidate_versions that ARE
    exercised in production (services/learning_path.py's practice-question
    selection calls it with exclude_version_ids, include_version_ids and
    limit) but weren't triggered by any existing service-level test fixture.
    """

    async def _seed_eligible(self, session, node_id, n) -> list[UUID]:
        version_ids = []
        for i in range(n):
            v_id = await self._make_question_version(session, difficulty="EASY", suffix=str(i))
            session.add(ContentQuestionLink(content_node_id=node_id, question_version_id=v_id))
            version_ids.append(v_id)
        await session.commit()
        return version_ids

    async def test_exclude_version_ids_removes_matching_candidates(self):
        async with self.Session() as session:
            node_id = await self._make_catalog_node(session)
            version_ids = await self._seed_eligible(session, node_id, 3)

            repo = QuestionSelectionRepository(session)
            results = await repo.list_eligible_candidate_versions(
                node_id, exclude_version_ids={version_ids[0]}
            )
            result_ids = {v.id for v in results}
            self.assertNotIn(version_ids[0], result_ids)
            self.assertEqual(len(result_ids), 2)

    async def test_include_version_ids_empty_set_short_circuits_to_empty_list(self):
        async with self.Session() as session:
            node_id = await self._make_catalog_node(session)
            await self._seed_eligible(session, node_id, 2)

            repo = QuestionSelectionRepository(session)
            results = await repo.list_eligible_candidate_versions(
                node_id, include_version_ids=set()
            )
            self.assertEqual(results, [])

    async def test_include_version_ids_restricts_to_given_set(self):
        async with self.Session() as session:
            node_id = await self._make_catalog_node(session)
            version_ids = await self._seed_eligible(session, node_id, 3)

            repo = QuestionSelectionRepository(session)
            results = await repo.list_eligible_candidate_versions(
                node_id, include_version_ids={version_ids[1]}
            )
            self.assertEqual([v.id for v in results], [version_ids[1]])

    async def test_limit_truncates_candidate_list(self):
        async with self.Session() as session:
            node_id = await self._make_catalog_node(session)
            await self._seed_eligible(session, node_id, 5)

            repo = QuestionSelectionRepository(session)
            results = await repo.list_eligible_candidate_versions(node_id, limit=2)
            self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
