"""Service-layer coverage for src/agente_ia_edu/services/activity_correction_store.py.

Wave 6 (background bug-hunt). Covers:
  - `_resolve` raising CorrectionNotFound for an unknown assignment (only
    reachable via `correct`/`get_result`, never exercised directly before).
  - `_validate_snapshot`'s five fail-closed branches (pure logic, tested
    directly with lightweight stand-in objects - no DB needed).
  - the concurrent-correction UNIQUE(attempt_id) race (IntegrityError caught,
    rolled back, existing row returned) - simulated by making `_load_result`
    miss once (the real interleaving a second corrector racing to commit
    first would produce) then see the row a genuine competitor committed.
  - `get_result` end to end (success, no attempt yet, attempt not corrected
    yet) - entirely untested before this file.

DB-backed tests use a real async SQLite session with the production default
`expire_on_commit=True` (see tests/test_assessments_route_coverage_http.py),
so ids are always captured right after `flush()`, never read back off an ORM
instance after `commit()`.
"""

import asyncio
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.assessments import (
    ActivityAnswer,
    ActivityAssignment,
    ActivityAttempt,
    ActivityResult,
    Assessment,
    AssessmentItem,
    AssessmentVersion,
)
from agente_ia_edu.db.models.official import Question, QuestionOption, QuestionVersion
from agente_ia_edu.services.activity_correction_store import (
    ActivityCorrectionStore,
    CorrectionNotFound,
    CorrectionSnapshotError,
    CorrectionStateError,
)
from agente_ia_edu.services.question_list_store import Requester

STUDENT_ID = "student-1"


def _requester(**overrides):
    defaults = dict(external_user_id=STUDENT_ID, school_id=None, role="STUDENT")
    defaults.update(overrides)
    return Requester(**defaults)


class ValidateSnapshotTests(unittest.TestCase):
    """`_validate_snapshot` is pure attribute-reading logic - no `self.*` use
    beyond the method signature - so it is exercised directly with plain
    objects, no DB round trip needed."""

    def setUp(self):
        self.store = ActivityCorrectionStore(session=None)  # never touches self._session

    @staticmethod
    def _item(position, qv_id, frozen_correct_option_id="opt-1"):
        class Item:
            pass

        it = Item()
        it.position = position
        it.question_version_id = qv_id
        it.frozen_correct_option_id = frozen_correct_option_id
        return it

    @staticmethod
    def _answer(qv_id):
        class Answer:
            pass

        a = Answer()
        a.question_version_id = qv_id
        return a

    @staticmethod
    def _assignment(assessment_version_id, question_count=None):
        class A:
            pass

        a = A()
        a.assessment_version_id = assessment_version_id
        a.question_count = question_count
        return a

    @staticmethod
    def _version(version_id, items):
        class V:
            pass

        v = V()
        v.id = version_id
        v.items = items
        return v

    def test_version_mismatch_fails_closed(self):
        assignment = self._assignment("version-A")
        version = self._version("version-B", items=[])
        with self.assertRaises(CorrectionSnapshotError) as ctx:
            self.store._validate_snapshot(assignment, version, answers=[])
        self.assertIn("assessment_version_id", str(ctx.exception))

    def test_no_items_fails_closed(self):
        assignment = self._assignment("v1")
        version = self._version("v1", items=[])
        with self.assertRaises(CorrectionSnapshotError) as ctx:
            self.store._validate_snapshot(assignment, version, answers=[])
        self.assertIn("não possui questões", str(ctx.exception))

    def test_question_count_mismatch_fails_closed(self):
        assignment = self._assignment("v1", question_count=3)
        items = [self._item(1, "qv1"), self._item(2, "qv2")]
        version = self._version("v1", items=items)
        with self.assertRaises(CorrectionSnapshotError) as ctx:
            self.store._validate_snapshot(assignment, version, answers=[])
        self.assertIn("question_count", str(ctx.exception))

    def test_non_contiguous_positions_fails_closed(self):
        assignment = self._assignment("v1", question_count=2)
        items = [self._item(1, "qv1"), self._item(3, "qv2")]
        version = self._version("v1", items=items)
        with self.assertRaises(CorrectionSnapshotError) as ctx:
            self.store._validate_snapshot(assignment, version, answers=[])
        self.assertIn("contíguas", str(ctx.exception))

    def test_missing_frozen_key_fails_closed(self):
        assignment = self._assignment("v1", question_count=1)
        items = [self._item(1, "qv1", frozen_correct_option_id=None)]
        version = self._version("v1", items=items)
        with self.assertRaises(CorrectionSnapshotError) as ctx:
            self.store._validate_snapshot(assignment, version, answers=[])
        self.assertIn("gabarito", str(ctx.exception))

    def test_stray_answer_fails_closed(self):
        assignment = self._assignment("v1", question_count=1)
        items = [self._item(1, "qv1")]
        version = self._version("v1", items=items)
        answers = [self._answer("qv-not-in-this-activity")]
        with self.assertRaises(CorrectionSnapshotError) as ctx:
            self.store._validate_snapshot(assignment, version, answers=answers)
        self.assertIn("não pertencem", str(ctx.exception))

    def test_valid_snapshot_does_not_raise(self):
        assignment = self._assignment("v1", question_count=1)
        items = [self._item(1, "qv1")]
        version = self._version("v1", items=items)
        answers = [self._answer("qv1")]
        self.store._validate_snapshot(assignment, version, answers=answers)  # no raise


class ActivityCorrectionStoreDBTests(unittest.TestCase):
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

    async def _seed(self, session, *, n_items=2, target_id=STUDENT_ID):
        """Minimal EXERCISE_LIST assignment with N frozen items, correct
        answer key on option 'A' for every item, target=STUDENT."""
        item_ids = []
        for i in range(1, n_items + 1):
            q = Question(validation_status="approved", origin_type="IMPORTED")
            session.add(q)
            await session.flush()
            qv = QuestionVersion(
                question_id=q.id, version_kind="official_original",
                canonical_text=f"Questao {i}", statement=f"Questao {i}",
                content_hash=f"hash-{i}-{uuid4().hex[:6]}",
            )
            session.add(qv)
            await session.flush()
            opt_a = QuestionOption(
                question_version_id=qv.id, option_key="A", position=1,
                text="Alternativa A", is_valid_option=True,
            )
            opt_b = QuestionOption(
                question_version_id=qv.id, option_key="B", position=2,
                text="Alternativa B", is_valid_option=False,
            )
            session.add_all([opt_a, opt_b])
            await session.flush()
            item_ids.append((qv.id, opt_a.id, opt_b.id))

        assessment = Assessment(material_type="EXERCISE_LIST", title="Lista de Teste")
        session.add(assessment)
        await session.flush()
        version = AssessmentVersion(
            assessment_id=assessment.id, version_number=1, title="v1", status="published",
        )
        session.add(version)
        await session.flush()
        for pos, (qv_id, opt_a_id, _opt_b_id) in enumerate(item_ids, start=1):
            session.add(AssessmentItem(
                assessment_version_id=version.id, question_version_id=qv_id,
                position=pos, frozen_correct_option_id=opt_a_id, points=1,
            ))
        await session.flush()

        assignment = ActivityAssignment(
            assessment_id=assessment.id, assessment_version_id=version.id,
            target_type="STUDENT", target_id=target_id, question_count=n_items,
        )
        session.add(assignment)
        await session.flush()

        ids = {
            "assessment_id": assessment.id,
            "assignment_id": assignment.id,
            "items": item_ids,
        }
        return ids

    async def _complete_attempt(self, session, assignment_id, *, student_id=STUDENT_ID, answers=None):
        attempt = ActivityAttempt(
            assignment_id=assignment_id, student_external_id=student_id, status="COMPLETED",
            started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
        )
        session.add(attempt)
        await session.flush()
        for qv_id, option_id, option_key in (answers or []):
            session.add(ActivityAnswer(
                attempt_id=attempt.id, question_version_id=qv_id,
                selected_option_id=option_id, selected_option_key=option_key,
            ))
        await session.flush()
        return attempt.id

    # -- _resolve / correct() -----------------------------------------

    def test_correct_unknown_assignment_raises_not_found(self):
        async def run():
            async with self.session_factory() as session:
                store = ActivityCorrectionStore(session)
                with self.assertRaises(CorrectionNotFound):
                    await store.correct(uuid4(), requester=_requester())

        asyncio.run(run())

    def test_get_result_unknown_assignment_raises_not_found(self):
        async def run():
            async with self.session_factory() as session:
                store = ActivityCorrectionStore(session)
                with self.assertRaises(CorrectionNotFound):
                    await store.get_result(uuid4(), requester=_requester())

        asyncio.run(run())

    # -- get_result --------------------------------------------------

    def test_get_result_no_attempt_raises_not_found(self):
        async def run():
            async with self.session_factory() as session:
                ids = await self._seed(session)
                await session.commit()
                store = ActivityCorrectionStore(session)
                with self.assertRaises(CorrectionNotFound):
                    await store.get_result(ids["assignment_id"], requester=_requester())

        asyncio.run(run())

    def test_get_result_attempt_not_yet_corrected_raises_not_found(self):
        async def run():
            async with self.session_factory() as session:
                ids = await self._seed(session)
                await self._complete_attempt(session, ids["assignment_id"])
                await session.commit()
                store = ActivityCorrectionStore(session)
                with self.assertRaises(CorrectionNotFound):
                    await store.get_result(ids["assignment_id"], requester=_requester())

        asyncio.run(run())

    def test_get_result_after_correction_returns_result_dict(self):
        async def run():
            async with self.session_factory() as session:
                ids = await self._seed(session, n_items=2)
                qv1, opt_a1, opt_b1 = ids["items"][0]
                qv2, opt_a2, _opt_b2 = ids["items"][1]
                await self._complete_attempt(
                    session, ids["assignment_id"],
                    answers=[(qv1, opt_b1, "B"), (qv2, opt_a2, "A")],
                )
                await session.commit()

                store = ActivityCorrectionStore(session)
                corrected = await store.correct(ids["assignment_id"], requester=_requester())
                fetched = await store.get_result(ids["assignment_id"], requester=_requester())
                return corrected, fetched

        corrected, fetched = asyncio.run(run())
        self.assertEqual(fetched["result"]["id"], corrected["result"]["id"])
        self.assertEqual(fetched["result"]["correct_count"], 1)
        self.assertEqual(fetched["result"]["incorrect_count"], 1)
        self.assertEqual(fetched["result"]["unanswered_count"], 0)
        self.assertTrue(fetched["answer_key_visible"])

    # -- correct(): state errors ---------------------------------------

    def test_correct_without_attempt_raises_not_started(self):
        async def run():
            async with self.session_factory() as session:
                ids = await self._seed(session)
                await session.commit()
                store = ActivityCorrectionStore(session)
                with self.assertRaises(CorrectionStateError):
                    await store.correct(ids["assignment_id"], requester=_requester())

        asyncio.run(run())

    # -- concurrent-correction race (IntegrityError -> rollback -> existing) --

    def test_concurrent_correction_race_returns_the_winners_result(self):
        """Simulates two correctors racing on the same attempt: this session's
        pre-commit "already corrected?" check misses (returns None, exactly
        as it would if a competitor's commit lands a heartbeat later), so it
        proceeds to build and commit its own ActivityResult - but the
        UNIQUE(attempt_id) constraint means only one INSERT can win. Here the
        competitor's row is inserted directly (its commit already landed)
        before our commit runs, so our commit collides for real and the
        IntegrityError/rollback/re-fetch path executes exactly as it would
        under genuine concurrency."""

        async def run():
            async with self.session_factory() as session:
                ids = await self._seed(session, n_items=1)
                qv1, opt_a1, _opt_b1 = ids["items"][0]
                attempt_id = await self._complete_attempt(
                    session, ids["assignment_id"], answers=[(qv1, opt_a1, "A")],
                )
                await session.commit()

            # A concurrent corrector already committed a result for this
            # attempt, via a fully independent session/transaction.
            async with self.session_factory() as winner_session:
                winner_result = ActivityResult(
                    attempt_id=attempt_id,
                    assignment_id=ids["assignment_id"],
                    student_external_id=STUDENT_ID,
                    assessment_version_id=(await winner_session.get(
                        ActivityAssignment, ids["assignment_id"])).assessment_version_id,
                    question_count=1, answered_count=1, correct_count=1,
                    incorrect_count=0, unanswered_count=0,
                    completion_status="COMPLETED",
                    corrected_at=datetime.now(timezone.utc),
                )
                winner_session.add(winner_result)
                await winner_session.flush()
                winner_result_id = winner_result.id
                await winner_session.commit()

            async with self.session_factory() as session:
                store = ActivityCorrectionStore(session)
                real_load_result = store._load_result
                call_count = {"n": 0}

                async def flaky_load_result(attempt_id_arg):
                    call_count["n"] += 1
                    if call_count["n"] == 1:
                        # this session's own pre-commit visibility of the
                        # not-yet-committed-when-it-started competitor row
                        return None
                    return await real_load_result(attempt_id_arg)

                store._load_result = flaky_load_result
                result = await store.correct(ids["assignment_id"], requester=_requester())
                return result, winner_result_id, call_count["n"]

        result, winner_result_id, call_count = asyncio.run(run())
        self.assertEqual(result["result"]["id"], str(winner_result_id))
        # confirms the race path (>1 _load_result call: the initial miss,
        # then the post-IntegrityError re-fetch) actually executed.
        self.assertGreaterEqual(call_count, 2)


if __name__ == "__main__":
    unittest.main()
