"""PHASE 18 - deterministic correction engine & student result backend tests.

TestClient + in-memory SQLite. Correction compares each student ActivityAnswer
to the FROZEN answer key (AssessmentItem.frozen_correct_option_id, snapshotted at
list-publication time) - deterministic, no AI, no official-table write. The raw
outcome is persisted in activity_results (+ activity_result_items), UNIQUE per
attempt (idempotent).

Covered (spec s20, 28 items):
  1  NOT_STARTED cannot be corrected
  2  IN_PROGRESS cannot be corrected
  3  COMPLETED can be corrected
  4  a correct answer -> CORRECT
  5  a wrong answer -> INCORRECT
  6  no answer -> UNANSWERED
  7  total counts add up (correct+incorrect+unanswered == question_count)
  8  aproveitamento_percent == correct/question_count*100
  9  the FROZEN key is used (not the live official key)
  10 the Player GET never exposes the key (before or after correction)
  11 the Result GET exposes correct_option_key (only there)
  12 the result is not duplicated (UNIQUE attempt_id)
  13 a second correction is idempotent (same result id, same numbers)
  14 a corrected attempt stays COMPLETED
  15 the student cannot mutate the result (no such endpoint; answers still 409)
  16 a student cannot read another student's result
  17 tenant isolation (other-tenant teacher blocked)
  18 an inconsistent snapshot fails closed (no result written)
  19 question order is preserved in the result items
  20 changing an answer before completion -> only the last choice is corrected
  21-24 activities with 10 / 50 / 100 / 200 questions
  25 no N+1 (bounded query count on correct())
  26 zero AI/provider dependency (import check)
  27 the official bank is unchanged
  28 the official answer key is unchanged
"""

from __future__ import annotations

import asyncio
import importlib
import unittest
import uuid as _uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_authenticated_context, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    CatalogNode,
    ContentQuestionLink,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    LearningHistory,
    PedagogicalClassification,
    Question,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
)
from agente_ia_edu.db.models.admin import UserSchoolLink
from agente_ia_edu.db.models.assessments import (
    ActivityResult,
    ActivityResultItem,
    AssessmentItem,
)
from agente_ia_edu.identity import AuthenticatedUserContext

N_SEED = 210
KEYS = "ABCDE"
CORRECT_BY_NUM = {n: KEYS[n % 5] for n in range(1, N_SEED + 1)}  # deterministic frozen key

_SCHOOL_UUIDS: dict[str, str] = {}


def _school_uuid(name):
    if name is None:
        return None
    return _SCHOOL_UUIDS.setdefault(
        name, str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"phase18-school-{name}")))


def _ctx(user="prof_a", school="school-1", role="TEACHER"):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user, role=role,
                                    school_id=_school_uuid(school), scope_type="SCHOOL")


async def _seed(factory) -> list[str]:
    async with factory() as s:
        inst = Institution(code="INEP", name="INEP")
        s.add(inst)
        await s.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM")
        s.add(exam)
        await s.flush()
        cat = CatalogNode(code="MATH", name="M", node_type="DISCIPLINE", active=True)
        s.add(cat)
        await s.flush()
        cat.root_id = cat.id
        app = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=1)
        s.add(app)
        await s.flush()
        bk = ExamBooklet(exam_application_id=app.id, code="D1_CD5", color="AMARELO")
        s.add(bk)
        await s.flush()
        sd = SourceDocument(exam_application_id=app.id, exam_booklet_id=bk.id, document_type="ANSWER_KEY",
                            source_url="https://x/g.pdf", acquired_at=datetime.now(timezone.utc), content_hash="g")
        s.add(sd)
        await s.flush()
        rev = AnswerKeyRevision(source_document_id=sd.id, revision_number=1, is_official=True)
        s.add(rev)
        await s.flush()
        vids: list[str] = []
        for num in range(1, N_SEED + 1):
            correct = CORRECT_BY_NUM[num]
            q = Question(validation_status="validated", origin_type="IMPORTED",
                         status="PUBLISHED", visibility_scope="PUBLIC")
            s.add(q)
            await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=f"Enunciado {num}", statement=f"Enunciado {num}",
                                content_hash=f"h{num}", is_immutable=True)
            s.add(v)
            await s.flush()
            opts = {}
            for pos, key in enumerate(KEYS, start=1):
                o = QuestionOption(question_version_id=v.id, option_key=key, position=pos,
                                   text=f"Alt {key}", is_valid_option=(key == correct))
                s.add(o)
                await s.flush()
                opts[key] = o
            bq = BookletQuestion(exam_booklet_id=bk.id, question_version_id=v.id,
                                 position=num, official_number=num, page_number=1)
            s.add(bq)
            await s.flush()
            s.add(AnswerKeyEntry(answer_key_revision_id=rev.id, booklet_question_id=bq.id,
                                 official_answer_label=correct, resolved_option_id=opts[correct].id,
                                 page_number=1))
        await s.commit()
        vids = [str(v) for v in (await s.execute(
            select(QuestionVersion.id).join(BookletQuestion,
                BookletQuestion.question_version_id == QuestionVersion.id)
            .order_by(BookletQuestion.official_number))).scalars().all()]
        return vids


class Phase18Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(cls.factory)

        cls.vids_all = cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls._ctx = _ctx()
        cls.app.dependency_overrides[get_current_authenticated_context] = lambda: cls._ctx
        cls.client = TestClient(cls.app)
        # official_number == index+1 == the seed order
        cls.correct_key = {vid: CORRECT_BY_NUM[i + 1] for i, vid in enumerate(cls.vids_all)}

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    # -- helpers ------------------------------------------------------

    def _as(self, ctx):
        self.__class__._ctx = ctx
        self.app.dependency_overrides[get_current_authenticated_context] = lambda: ctx

    def _link_content_and_difficulty(self, vids, *, content_code="TESTCONTENT", difficulty="EASY"):
        """Seed a CatalogNode + ContentQuestionLink + recommended_difficulty for
        the given question_version_ids, so correct() has what it needs to
        record LearningHistory (content_node_id + difficulty_level are both
        NOT NULL there)."""
        async def _do():
            async with self.factory() as s:
                node = (await s.execute(
                    select(CatalogNode).where(CatalogNode.code == content_code)
                )).scalar_one_or_none()
                if node is None:
                    node = CatalogNode(code=content_code, name=content_code, node_type="CONTENT", active=True)
                    s.add(node)
                    await s.flush()
                for vid in vids:
                    s.add(ContentQuestionLink(content_node_id=node.id, question_version_id=_uuid.UUID(vid)))
                    qv = await s.get(QuestionVersion, _uuid.UUID(vid))
                    qv.recommended_difficulty = difficulty
                await s.commit()
                return node.id
        return self.loop.run_until_complete(_do())

    def _seed_link(self, uid, classroom, school="school-1", active=True):
        async def _do():
            async with self.factory() as s:
                s.add(UserSchoolLink(
                    external_user_id=uid, school_id=_uuid.UUID(_school_uuid(school)),
                    role="STUDENT", scope_type="CLASSROOM", scope_external_id=classroom, active=active))
                await s.commit()
        self.loop.run_until_complete(_do())

    def _distribute(self, n, *, school="school-1", owner="prof_a", classroom="turma"):
        ids = self.vids_all[:n]
        self._as(_ctx(owner, school))
        r = self.client.post("/api/v1/question-bank/lists", json={
            "question_version_ids": ids, "title": f"P18 {n}", "answer_key_presentation": "KEY_AT_END"})
        lid = r.json()["id"]
        self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        a = self.client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
            "target_type": "CLASS", "target_id": classroom, "available_from": "2000-01-01T00:00:00Z"})
        return lid, a.json()["id"], ids

    def _student(self, uid, school="school-1"):
        self._as(_ctx(uid, school, role="STUDENT"))

    def _play(self, aid, ids, *, answer):
        """answer(i, correct_key) -> option key or None."""
        st = self.client.post(f"/api/v1/student/activities/{aid}/attempt").json()
        for i, vid in enumerate(ids):
            key = answer(i, self.correct_key[vid])
            if key is not None:
                self.client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{vid}",
                                json={"selected_option": key})
        return st

    def _complete(self, aid):
        return self.client.post(f"/api/v1/student/activities/{aid}/attempt/complete")

    def _correct(self, aid):
        return self.client.post(f"/api/v1/student/activities/{aid}/attempt/correct")

    def _result(self, aid):
        return self.client.get(f"/api/v1/student/activities/{aid}/attempt/result")

    @staticmethod
    def _wrong(k):
        return next(x for x in KEYS if x != k)

    # -- 1 / 2 / 3 -----------------------------------------------
    def test_only_completed_can_be_corrected(self):
        lid, aid, ids = self._distribute(3, classroom="t-states")
        self._seed_link("s_states", "t-states")
        self._student("s_states")
        # NOT_STARTED
        r = self._correct(aid)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["status"], "NOT_STARTED")
        # IN_PROGRESS
        self._play(aid, ids, answer=lambda i, k: k if i == 0 else None)
        r = self._correct(aid)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["status"], "IN_PROGRESS")
        # COMPLETED
        for vid in ids[1:]:
            self.client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{vid}",
                            json={"selected_option": self.correct_key[vid]})
        self._complete(aid)
        self.assertEqual(self._correct(aid).status_code, 200)

    # -- 4 / 5 / 7 / 8 / 9 / 19 ---------------------------
    def test_deterministic_correct_incorrect_and_counts(self):
        # PHASE 17 only lets a fully-answered attempt complete, so this path
        # exercises CORRECT vs INCORRECT; the UNANSWERED branch is covered
        # directly in test_unanswered_question_scored_as_unanswered.
        lid, aid, ids = self._distribute(10, classroom="t-outcome")
        self._seed_link("s_outcome", "t-outcome")
        self._student("s_outcome")
        def ans(i, k):
            return k if i < 6 else self._wrong(k)   # 6 correct, 4 wrong
        self._play(aid, ids, answer=ans)
        self.assertEqual(self._complete(aid).status_code, 200)
        res = self._correct(aid).json()
        r = res["result"]
        self.assertEqual(r["question_count"], 10)
        self.assertEqual(r["correct_count"], 6)
        self.assertEqual(r["incorrect_count"], 4)
        self.assertEqual(r["unanswered_count"], 0)
        self.assertEqual(r["answered_count"], 10)
        self.assertEqual(r["correct_count"] + r["incorrect_count"] + r["unanswered_count"], 10)
        self.assertEqual(r["aproveitamento_percent"], 60.0)
        # order preserved + per-question status + FROZEN key used
        self.assertEqual([it["position"] for it in res["items"]], list(range(1, 11)))
        for i, it in enumerate(res["items"]):
            self.assertEqual(it["question_version_id"], ids[i])
            self.assertEqual(it["correct_option_key"], self.correct_key[ids[i]])
            self.assertEqual(it["status"], "CORRECT" if i < 6 else "INCORRECT")
        self.assertTrue(res["answer_key_visible"])

    # -- correction bridges into LearningHistory (domain-map evidence)
    def test_correction_records_learning_history_for_classified_questions(self):
        lid, aid, ids = self._distribute(5, classroom="t-history")
        node_id = self._link_content_and_difficulty(ids[:3], content_code="TC-HIST", difficulty="EASY")
        self._seed_link("s_history", "t-history")
        self._student("s_history")

        def ans(i, k):
            return k if i % 2 == 0 else self._wrong(k)  # positions 0,2,4 correct
        self._play(aid, ids, answer=ans)
        self.assertEqual(self._complete(aid).status_code, 200)
        self.assertEqual(self._correct(aid).status_code, 200)

        async def _rows():
            async with self.factory() as s:
                return (await s.execute(
                    select(LearningHistory).where(LearningHistory.external_identity_id == "s_history")
                )).scalars().all()
        rows = self.loop.run_until_complete(_rows())

        # only the 3 linked+difficulty-tagged questions get a row; the other
        # 2 (no ContentQuestionLink, no recommended_difficulty) are skipped,
        # not written with invented data.
        self.assertEqual(len(rows), 3)
        by_qv = {str(r.question_version_id): r for r in rows}
        self.assertEqual(set(by_qv), set(ids[:3]))
        for i, vid in enumerate(ids[:3]):
            row = by_qv[vid]
            self.assertEqual(row.activity_type, "OFFICIAL_ASSESSMENT")
            self.assertEqual(str(row.content_node_id), str(node_id))
            self.assertEqual(row.difficulty_level, "EASY")
            self.assertEqual(row.is_correct, i % 2 == 0)

        # idempotent: a second correct() call must not duplicate history rows
        self.assertEqual(self._correct(aid).status_code, 200)
        rows_again = self.loop.run_until_complete(_rows())
        self.assertEqual(len(rows_again), 3)

    # -- 6: an unanswered question -> UNANSWERED (engine-level; PHASE 17
    #       forbids completing with a blank, so we build the COMPLETED
    #       attempt + partial answers directly).
    def test_unanswered_question_scored_as_unanswered(self):
        lid, aid, ids = self._distribute(4, classroom="t-unans")
        self._seed_link("s_unans", "t-unans")
        self._student("s_unans")
        st = self.client.post(f"/api/v1/student/activities/{aid}/attempt").json()
        # answer only the first three (all correct)
        for vid in ids[:3]:
            self.client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{vid}",
                            json={"selected_option": self.correct_key[vid]})

        async def _force_complete():
            from agente_ia_edu.db.models.assessments import ActivityAttempt
            async with self.factory() as s:
                att = (await s.execute(select(ActivityAttempt).where(
                    ActivityAttempt.assignment_id == _uuid.UUID(aid)))).scalar_one()
                att.status = "COMPLETED"
                att.completed_at = datetime.now(timezone.utc)
                await s.commit()
        self.loop.run_until_complete(_force_complete())

        res = self._correct(aid).json()
        r = res["result"]
        self.assertEqual(r["correct_count"], 3)
        self.assertEqual(r["incorrect_count"], 0)
        self.assertEqual(r["unanswered_count"], 1)
        self.assertEqual(r["answered_count"], 3)
        self.assertEqual(res["items"][3]["status"], "UNANSWERED")
        self.assertFalse(res["items"][3]["answered"])
        self.assertIsNone(res["items"][3]["selected_option_key"])
        self.assertEqual(res["items"][3]["correct_option_key"], self.correct_key[ids[3]])
        self.assertEqual(r["aproveitamento_percent"], 75.0)

    # -- 10 / 11 / 13 ---------------------------------------
    def test_key_hidden_in_player_visible_in_result_and_idempotent(self):
        lid, aid, ids = self._distribute(5, classroom="t-key")
        self._seed_link("s_key", "t-key")
        self._student("s_key")
        st = self.client.post(f"/api/v1/student/activities/{aid}/attempt").json()
        for vid in ids:
            self.client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{vid}",
                            json={"selected_option": self.correct_key[vid]})
        # player state BEFORE completion: no key
        blob = repr(st).lower().replace("'answer_key_visible': false", "")
        for needle in ("is_valid_option", "correct_option", "correct_answer", "answer_key"):
            self.assertNotIn(needle, blob)
        self._complete(aid)
        # player state AFTER completion: still no key
        after = self.client.get(f"/api/v1/student/activities/{aid}/attempt").json()
        self.assertFalse(after["answer_key_visible"])
        self.assertNotIn("correct_option", repr(after).lower().replace("'answer_key_visible': false", ""))
        # result: key present
        res1 = self._correct(aid).json()
        self.assertTrue(all(it["correct_option_key"] in KEYS for it in res1["items"]))
        # idempotent
        res2 = self._correct(aid).json()
        self.assertEqual(res1["result"]["id"], res2["result"]["id"])
        self.assertEqual(res1["result"]["correct_count"], res2["result"]["correct_count"])

    # -- 12: DB-level single result per attempt
    def test_result_not_duplicated_in_db(self):
        lid, aid, ids = self._distribute(4, classroom="t-dup")
        self._seed_link("s_dup", "t-dup")
        self._student("s_dup")
        self._play(aid, ids, answer=lambda i, k: k)
        self._complete(aid)
        r1 = self._correct(aid).json()
        r2 = self._correct(aid).json()
        r3 = self._correct(aid).json()
        self.assertEqual(r1["result"]["id"], r2["result"]["id"])
        self.assertEqual(r2["result"]["id"], r3["result"]["id"])

        async def _counts():
            async with self.factory() as s:
                nr = int(await s.scalar(select(func.count()).select_from(ActivityResult)
                                        .where(ActivityResult.assignment_id == _uuid.UUID(aid))))
                ni = int(await s.scalar(
                    select(func.count()).select_from(ActivityResultItem)
                    .join(ActivityResult, ActivityResult.id == ActivityResultItem.result_id)
                    .where(ActivityResult.assignment_id == _uuid.UUID(aid))))
                return nr, ni
        nr, ni = self.loop.run_until_complete(_counts())
        self.assertEqual(nr, 1)
        self.assertEqual(ni, 4)

    # -- 14 / 15 --------------------------------------------
    def test_corrected_attempt_stays_completed_and_immutable(self):
        lid, aid, ids = self._distribute(3, classroom="t-immut")
        self._seed_link("s_immut", "t-immut")
        self._student("s_immut")
        self._play(aid, ids, answer=lambda i, k: k)
        self._complete(aid)
        self._correct(aid)
        state = self.client.get(f"/api/v1/student/activities/{aid}/attempt").json()
        self.assertEqual(state["status"], "COMPLETED")
        # the student has no way to change an answer or the result
        r = self.client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{ids[0]}",
                            json={"selected_option": self._wrong(self.correct_key[ids[0]])})
        self.assertEqual(r.status_code, 409)
        # re-correcting still yields the same numbers
        self.assertEqual(self._correct(aid).json()["result"]["correct_count"], 3)

    # -- 16 / 17 -------------------------------------------
    def test_student_cannot_read_others_result_and_tenant_isolation(self):
        lid, aid, ids = self._distribute(3, school="school-A", owner="prof_A", classroom="t-A")
        self._seed_link("owner_stu", "t-A", school="school-A")
        self._seed_link("intruder_stu", "t-B", school="school-A")
        self._student("owner_stu", "school-A")
        self._play(aid, ids, answer=lambda i, k: k)
        self._complete(aid)
        self._correct(aid)
        # another student
        self._student("intruder_stu", "school-A")
        self.assertEqual(self._result(aid).status_code, 403)
        self.assertEqual(self._correct(aid).status_code, 403)
        # another tenant's teacher
        self._as(_ctx("prof_Z", "school-Z"))
        self.assertIn(self._result(aid).status_code, (403, 404))

    # -- 18: snapshot inconsistency fails closed
    def test_inconsistent_snapshot_fails_closed(self):
        lid, aid, ids = self._distribute(4, classroom="t-snap")
        self._seed_link("s_snap", "t-snap")
        self._student("s_snap")
        self._play(aid, ids, answer=lambda i, k: k)
        self._complete(aid)

        # corrupt the frozen key of THIS activity's 2nd item only
        async def _corrupt():
            from agente_ia_edu.db.models.assessments import AssessmentVersion
            async with self.factory() as s:
                it = (await s.execute(
                    select(AssessmentItem)
                    .join(AssessmentVersion, AssessmentVersion.id == AssessmentItem.assessment_version_id)
                    .where(AssessmentVersion.assessment_id == _uuid.UUID(lid),
                           AssessmentItem.position == 2))).scalar_one()
                it.frozen_correct_option_id = None
                await s.commit()
        self.loop.run_until_complete(_corrupt())

        r = self._correct(aid)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["reason"], "snapshot_inconsistent")

        async def _no_result():
            async with self.factory() as s:
                return int(await s.scalar(select(func.count()).select_from(ActivityResult).where(
                    ActivityResult.assignment_id == _uuid.UUID(aid))))
        self.assertEqual(self.loop.run_until_complete(_no_result()), 0)

    # -- 20: only the last choice is corrected
    def test_only_last_choice_is_corrected(self):
        lid, aid, ids = self._distribute(3, classroom="t-last")
        self._seed_link("s_last", "t-last")
        self._student("s_last")
        self.client.post(f"/api/v1/student/activities/{aid}/attempt")
        vid = ids[0]
        ck = self.correct_key[vid]
        # A -> wrong -> correct  (final = correct)
        self.client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{vid}",
                        json={"selected_option": self._wrong(ck)})
        self.client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{vid}",
                        json={"selected_option": ck})
        for other in ids[1:]:
            self.client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{other}",
                            json={"selected_option": self._wrong(self.correct_key[other])})
        self._complete(aid)
        res = self._correct(aid).json()
        self.assertEqual(res["items"][0]["selected_option_key"], ck)
        self.assertEqual(res["items"][0]["status"], "CORRECT")
        self.assertEqual(res["result"]["correct_count"], 1)

    # -- 21-24 + 25: sizes 10/50/100/200 + no N+1
    def test_sizes_and_no_n_plus_1(self):
        query_counts = {}
        for n in (10, 50, 100, 200):
            lid, aid, ids = self._distribute(n, classroom=f"t-{n}")
            self._seed_link(f"s_{n}", f"t-{n}")
            self._student(f"s_{n}")
            self._play(aid, ids, answer=lambda i, k: k if i % 2 == 0 else self._wrong(k))
            self._complete(aid)

            n_q = {"c": 0}

            @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
            def _c(*_a, _b=n_q):  # noqa: ANN001
                _b["c"] += 1
            try:
                res = self._correct(aid).json()
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", _c)
            query_counts[n] = n_q["c"]
            exp_correct = (n + 1) // 2
            self.assertEqual(res["result"]["correct_count"], exp_correct)
            self.assertEqual(res["result"]["question_count"], n)
            self.assertEqual(len(res["items"]), n)
        # query count must NOT scale with n (bounded, no per-question query)
        self.assertLess(query_counts[200], query_counts[10] + 8,
                        f"query count scaled with size: {query_counts}")

    # -- 26: AI-agnostic
    def test_correction_module_is_ai_agnostic(self):
        import agente_ia_edu.services.activity_correction_store as mod
        src = importlib.util.find_spec(mod.__name__).origin
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        for banned in ("openai", "AsyncOpenAI", "OpenAIProvider", "build_text_provider",
                       "classification_consensus", "classification_prompts", "ai_classification_service"):
            self.assertNotIn(banned, text)

    # -- 27 / 28: official bank + answer key unchanged
    def test_official_data_unchanged(self):
        async def counts():
            async with self.factory() as s:
                return {k: await s.scalar(select(func.count()).select_from(m)) for k, m in {
                    "q": Question, "v": QuestionVersion, "o": QuestionOption,
                    "bq": BookletQuestion, "cn": CatalogNode,
                    "pc": PedagogicalClassification,
                    "ake": AnswerKeyEntry, "akr": AnswerKeyRevision}.items()}
        before = self.loop.run_until_complete(counts())
        lid, aid, ids = self._distribute(6, classroom="t-official")
        self._seed_link("s_official", "t-official")
        self._student("s_official")
        self._play(aid, ids, answer=lambda i, k: k)
        self._complete(aid)
        self._correct(aid)
        self._correct(aid)
        self.assertEqual(before, self.loop.run_until_complete(counts()))


if __name__ == "__main__":
    unittest.main()
