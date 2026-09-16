"""PHASE 22 - Adaptive Practice Engine backend tests.

TestClient + in-memory SQLite. A practice is an ordinary EXERCISE_LIST Activity
distributed to the STUDENT themself with ``origin = PRACTICE`` on the assignment
metadata, so its evidence stays SEPARATE from OFFICIAL_ACTIVITY in the PHASE 20
Domain Map. Reuses PHASE 12-15 selection/list, PHASE 16 assignment, the PHASE 17
Player, PHASE 18 correction, PHASE 20 Domain Map, PHASE 21 path. ZERO AI.

Covers spec s32 items: create by content; 5/10 selection; max clamp; insufficient
bank; content without questions; classified-only (definitive) selection; exclude
UNCLASSIFIED; visual_dependency exclusion; protected exclusion; avoid recently
answered; determinism; tenant isolation; another student's practice is 403; play
+ autosave + reload + complete + correct + result; origin PRACTICE; Domain Map
updated with PRACTICE origin; OFFICIAL_ACTIVITY preserved; rebuild idempotency;
Learning Path updated; no N+1; AI-agnostic import guard; answer key / official
bank immutable.
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
    AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, CatalogNode, Exam,
    ExamApplication, ExamBooklet, Institution, PedagogicalClassification,
    Question, QuestionOption, QuestionVersion, SourceDocument,
)
from agente_ia_edu.db.models.admin import UserSchoolLink
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.adaptive_practice import (
    AdaptivePracticeService, MAX_QUESTIONS, PracticeSelectionPolicy,
)
from agente_ia_edu.services.question_list_store import Requester

KEYS = "ABCDE"

# catalog: MATH > MATH-A > {C_MAIN, C_VIS, C_PROT, C_NR, C_EMPTY}
#   C_MAIN  : 24 CLASSIFIED, non-visual, non-protected      (2024 booklet)
#   C_VIS   :  3 CLASSIFIED but visual_dependency=True       (2024 booklet)
#   C_NR    :  1 CLASSIFIED + 3 NEEDS_REVIEW                  (2024 booklet)
#   C_PROT  :  3 CLASSIFIED, official_number in the 2020 protected set (2020 booklet)
#   C_EMPTY :  node only, zero questions
#   +4 UNCLASSIFIED questions (no classification row)         (2024 booklet)
_SCHOOL: dict[str, str] = {}


def _school_uuid(name):
    if name is None:
        return None
    return _SCHOOL.setdefault(name, str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"phase22-{name}")))


def _ctx(user="prof_a", school="school-1", role="TEACHER"):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user, role=role,
                                    school_id=_school_uuid(school), scope_type="SCHOOL")


async def _seed(factory) -> dict:
    async with factory() as s:
        inst = Institution(code="INEP", name="INEP"); s.add(inst); await s.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM"); s.add(exam); await s.flush()

        def node(code, name, ntype, parent=None, root=None, pos=0):
            n = CatalogNode(code=code, name=name, node_type=ntype, position=pos,
                            parent_id=parent.id if parent else None,
                            root_id=(root.id if root else None), active=True)
            s.add(n)
            return n

        d1 = node("MATH", "Matemática", "DISCIPLINE"); await s.flush(); d1.root_id = d1.id
        a1 = node("MATH-A", "Área M", "AREA", d1, d1); await s.flush()
        cMain = node("C_MAIN", "Conteúdo principal", "CONTENT", a1, d1, pos=1); await s.flush()
        node("C_VIS", "Conteúdo visual", "CONTENT", a1, d1, pos=2); await s.flush()
        node("C_PROT", "Conteúdo protegido", "CONTENT", a1, d1, pos=3); await s.flush()
        node("C_NR", "Conteúdo em revisão", "CONTENT", a1, d1, pos=4); await s.flush()
        node("C_EMPTY", "Conteúdo sem questões", "CONTENT", a1, d1, pos=5); await s.flush()
        node("C_INACTIVE", "Conteúdo inativo", "CONTENT", a1, d1, pos=6)
        await s.flush()
        # deactivate C_INACTIVE
        (await s.execute(select(CatalogNode).where(CatalogNode.code == "C_INACTIVE"))).scalar_one().active = False
        await s.flush()

        def application(year):
            app = ExamApplication(exam_id=exam.id, year=year, application_type="regular", day=1)
            s.add(app)
            return app

        app24 = application(2024); app20 = application(2020); await s.flush()
        bk24 = ExamBooklet(exam_application_id=app24.id, code="C24", color="AZUL")
        bk20 = ExamBooklet(exam_application_id=app20.id, code="C20", color="AZUL")
        s.add_all([bk24, bk20]); await s.flush()

        def revision(app, bk):
            sd = SourceDocument(exam_application_id=app.id, exam_booklet_id=bk.id,
                                document_type="ANSWER_KEY", source_url="https://x/g.pdf",
                                acquired_at=datetime.now(timezone.utc), content_hash=f"g{app.year}")
            s.add(sd)
            return sd

        sd24 = revision(app24, bk24); sd20 = revision(app20, bk20); await s.flush()
        rev24 = AnswerKeyRevision(source_document_id=sd24.id, revision_number=1, is_official=True)
        rev20 = AnswerKeyRevision(source_document_id=sd20.id, revision_number=1, is_official=True)
        s.add_all([rev24, rev20]); await s.flush()

        by_content: dict[str, list[str]] = {}
        correct_key: dict[str, str] = {}

        async def make(bk, rev, official_number, *, content, status="CLASSIFIED",
                       visual=False, classify=True):
            correct = KEYS[official_number % 5]
            q = Question(validation_status="validated", origin_type="IMPORTED",
                         status="PUBLISHED", visibility_scope="PUBLIC")
            s.add(q); await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=f"e{official_number}", statement=f"e{official_number}",
                                content_hash=f"h{bk.code}{official_number}", is_immutable=True)
            s.add(v); await s.flush()
            opts = {}
            for pos, key in enumerate(KEYS, start=1):
                o = QuestionOption(question_version_id=v.id, option_key=key, position=pos,
                                   text=f"Alt {key}", is_valid_option=(key == correct))
                s.add(o); await s.flush(); opts[key] = o
            bq = BookletQuestion(exam_booklet_id=bk.id, question_version_id=v.id,
                                 position=official_number, official_number=official_number, page_number=1)
            s.add(bq); await s.flush()
            s.add(AnswerKeyEntry(answer_key_revision_id=rev.id, booklet_question_id=bq.id,
                                 official_answer_label=correct, resolved_option_id=opts[correct].id,
                                 page_number=1))
            if classify:
                md = {"taxonomy_version": "curriculum-v2", "primary_content_code": content,
                      "visual_dependency": visual}
                s.add(PedagogicalClassification(
                    question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
                    content=content, subcontent=content, difficulty="UNKNOWN", reasoning_type="U",
                    prerequisites=[], keywords=[], competencies=[], skills=[],
                    status=status, source="rule", lifecycle="ACTIVE",
                    model_version="fx", prompt_version="v1", metadata_=md))
            by_content.setdefault(content if classify else "_UNCLASSIFIED", []).append(str(v.id))
            correct_key[str(v.id)] = correct

        n = 1
        for _ in range(24):
            await make(bk24, rev24, n, content="C_MAIN"); n += 1
        for _ in range(3):
            await make(bk24, rev24, n, content="C_VIS", visual=True); n += 1
        await make(bk24, rev24, n, content="C_NR", status="CLASSIFIED"); n += 1
        for _ in range(3):
            await make(bk24, rev24, n, content="C_NR", status="NEEDS_REVIEW"); n += 1
        for _ in range(4):
            await make(bk24, rev24, n, content="_none", classify=False); n += 1
        for pn in (91, 93, 107):
            await make(bk20, rev20, pn, content="C_PROT")
        await s.commit()
        return {"by_content": by_content, "correct_key": correct_key}


class Phase22Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(cls.factory)

        seed = cls.loop.run_until_complete(_prep())
        cls.by_content = seed["by_content"]
        cls.correct_key = seed["correct_key"]
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls._ctx = _ctx()
        cls.app.dependency_overrides[get_current_authenticated_context] = lambda: cls._ctx
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    # -- helpers ---------------------------------------------------------

    def _as(self, ctx):
        self.__class__._ctx = ctx
        self.app.dependency_overrides[get_current_authenticated_context] = lambda: ctx

    def _link(self, uid, classroom="t-1", school="school-1", active=True):
        async def _do():
            async with self.factory() as s:
                s.add(UserSchoolLink(external_user_id=uid, school_id=_uuid.UUID(_school_uuid(school)),
                                     role="STUDENT", scope_type="CLASSROOM",
                                     scope_external_id=classroom, active=active))
                await s.commit()
        self.loop.run_until_complete(_do())

    def _student(self, uid, school="school-1"):
        self._as(_ctx(uid, school, role="STUDENT"))

    def _create(self, content_code, count, *, mode="PRACTICE_CONTENT"):
        return self.client.post("/api/v1/student/practice",
                                json={"content_code": content_code, "question_count": count,
                                      "mode": mode})

    def _play(self, practice_id, *, correct_positions=None):
        """Run the reused PHASE 17 player + PHASE 18 correction over a practice."""
        st = self.client.post(f"/api/v1/student/activities/{practice_id}/attempt").json()
        vids = [q["question_version_id"] for q in st["questions"]]
        cp = set(range(len(vids))) if correct_positions is None else set(correct_positions)
        for pos, vid in enumerate(vids):
            ck = self.correct_key[vid]
            key = ck if pos in cp else next(k for k in KEYS if k != ck)
            self.client.put(f"/api/v1/student/activities/{practice_id}/attempt/answers/{vid}",
                            json={"selected_option": key})
        self.client.post(f"/api/v1/student/activities/{practice_id}/attempt/complete")
        return self.client.post(f"/api/v1/student/activities/{practice_id}/attempt/correct"), vids

    def _domain_content(self, code):
        j = self.client.get("/api/v1/student/domain").json()
        return next((x for d in j["disciplines"] for x in d["contents"]
                     if x["content_code"] == code), None)

    def _rebuild_content(self, code):
        j = self.client.post("/api/v1/student/domain/rebuild").json()
        return next((x for d in j["disciplines"] for x in d["contents"]
                     if x["content_code"] == code), None)

    # -- 1  create by content -----------------------------------------
    def test_create_by_content(self):
        self._link("s1"); self._student("s1")
        r = self._create("C_MAIN", 10)
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        self.assertEqual(b["origin"], "PRACTICE")
        self.assertEqual(b["mode"], "PRACTICE_CONTENT")
        self.assertEqual(b["state"], "PRACTICE_CREATED")
        self.assertEqual(b["question_count"], 10)
        self.assertIs(b["ai_used"], False)
        self.assertEqual(b["selection"]["selected_questions"], 10)
        self.assertTrue(b["selection"]["sufficient"])
        self.assertEqual(b["practice_id"], b["assignment_id"])

    # -- 1b  a school-linked student's practice list must not claim a real
    #        ENEM institution it has no relationship to - school_id and
    #        institution_id are two distinct FKs on Assessment, and
    #        AdaptivePracticeService has no legitimate institution_id to give.
    #        (SQLite in this suite does not enforce FKs, so a real Postgres
    #        dev DB is what actually surfaces this as a 500 - assert on the
    #        stored value directly instead of relying on constraint failure.)
    def test_practice_list_does_not_borrow_school_id_as_institution_id(self):
        self._link("s1b"); self._student("s1b")
        r = self._create("C_MAIN", 10)
        self.assertEqual(r.status_code, 200, r.text)
        practice_id = r.json()["practice_id"]

        async def _fetch():
            from agente_ia_edu.db.models import ActivityAssignment, Assessment
            async with self.factory() as s:
                assignment = await s.get(ActivityAssignment, _uuid.UUID(practice_id))
                return await s.get(Assessment, assignment.assessment_id)
        assessment = self.loop.run_until_complete(_fetch())
        self.assertIsNotNone(assessment)
        self.assertIsNone(assessment.institution_id)
        self.assertIsNotNone(assessment.school_id)

    # -- 2  configurable count 5 / 10 -------------------------------
    def test_configurable_count(self):
        self._link("s2"); self._student("s2")
        for want in (5, 10, 15, 20):
            b = self._create("C_MAIN", want).json()
            self.assertEqual(b["question_count"], want)
            self.assertEqual(b["selection"]["selected_questions"], want)

    # -- 3  max clamp -------------------------------------------------
    def test_max_clamp(self):
        self._link("s3"); self._student("s3")
        # schema rejects > 20
        self.assertEqual(self._create("C_MAIN", 21).status_code, 422)
        self.assertEqual(self._create("C_MAIN", 0).status_code, 422)
        # service-level clamp: MAX_QUESTIONS is the safe ceiling
        self.assertEqual(MAX_QUESTIONS, 20)

        async def _svc():
            async with self.factory() as s:
                svc = AdaptivePracticeService(s)
                req = Requester(external_user_id="s3", school_id=_school_uuid("school-1"), role="STUDENT")
                return await svc.create_practice("s3", requester=req, content_code="C_MAIN",
                                                 question_count=999)
        b = self.loop.run_until_complete(_svc())
        self.assertEqual(b["question_count"], MAX_QUESTIONS)

    # -- 4  insufficient bank --------------------------------------
    def test_insufficient_bank(self):
        self._link("s4"); self._student("s4")
        r = self._create("C_NR", 5)          # only 1 CLASSIFIED question for C_NR
        self.assertEqual(r.status_code, 422)
        d = r.json()["detail"]
        self.assertEqual(d["message"], "Não há questões suficientes disponíveis para esta prática.")
        self.assertEqual(d["available_questions"], 1)
        self.assertEqual(d["requested_questions"], 5)
        self.assertEqual(d["selected_questions"], 1)
        self.assertIs(d["sufficient"], False)

    # -- 5  content without questions -----------------------------
    def test_content_without_questions(self):
        self._link("s5"); self._student("s5")
        r = self._create("C_EMPTY", 5)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"]["available_questions"], 0)

    # -- 5b  unknown / inactive content --------------------------
    def test_unknown_and_inactive_content(self):
        self._link("s5b"); self._student("s5b")
        self.assertEqual(self._create("NOPE-NOPE", 5).status_code, 422)
        self.assertEqual(self._create("C_INACTIVE", 5).status_code, 422)

    # -- 6  classified-only (definitive) selection ---------------
    def test_definitive_only_selection(self):
        self._link("s6"); self._student("s6")
        # C_NR has 1 CLASSIFIED + 3 NEEDS_REVIEW -> only the definitive one is eligible
        b = self._create("C_NR", 1).json()
        self.assertEqual(b["selection"]["available_questions"], 1)
        self.assertEqual(b["selection"]["selected_questions"], 1)

    # -- 7  exclude UNCLASSIFIED --------------------------------
    def test_excludes_unclassified(self):
        self._link("s7"); self._student("s7")
        b = self._create("C_MAIN", 20).json()
        # C_MAIN has exactly 24 classified; the 4 UNCLASSIFIED questions never leak in
        self.assertEqual(b["selection"]["available_questions"], 24)
        practice_id = b["practice_id"]
        st = self.client.post(f"/api/v1/student/activities/{practice_id}/attempt").json()
        chosen = {q["question_version_id"] for q in st["questions"]}
        self.assertTrue(chosen.issubset(set(self.by_content["C_MAIN"])))
        self.assertFalse(chosen & set(self.by_content["_UNCLASSIFIED"]))

    # -- 8  visual_dependency exclusion ------------------------
    def test_visual_dependency_excluded(self):
        self._link("s8"); self._student("s8")
        r = self._create("C_VIS", 3)     # all 3 are visual -> excluded -> nothing selectable
        self.assertEqual(r.status_code, 422)
        d = r.json()["detail"]
        self.assertEqual(d["available_questions"], 0)
        self.assertEqual(d["excluded_visual_dependency"], 3)

    # -- 9  protected exclusion --------------------------------
    def test_protected_excluded(self):
        self._link("s9"); self._student("s9")
        r = self._create("C_PROT", 3)    # all 3 are protected (2020 official set)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"]["excluded_protected"], 3)

    # -- 10  avoid recently answered + determinism ------------
    def test_avoid_recent_and_determinism(self):
        self._link("s10"); self._student("s10")
        first = self._create("C_MAIN", 10).json()["practice_id"]
        self._play(first, correct_positions=set(range(10)))
        seen_first = set(self.client.post(
            f"/api/v1/student/activities/{first}/attempt").json() and
            [q["question_version_id"] for q in
             self.client.get(f"/api/v1/student/activities/{first}/attempt").json()["questions"]])
        # a fresh practice prefers the not-recently-answered questions
        second = self._create("C_MAIN", 10).json()
        rep = second["selection"]
        self.assertEqual(rep["recent_pool_excluded"], 10)
        st2 = self.client.post(
            f"/api/v1/student/activities/{second['practice_id']}/attempt").json()
        seen_second = {q["question_version_id"] for q in st2["questions"]}
        # 24 total, 10 recent -> the other 14 are fresh; the 10 new picks avoid the recent set
        self.assertFalse(seen_second & seen_first)

    def test_selection_is_deterministic(self):
        async def pick():
            async with self.factory() as s:
                svc = AdaptivePracticeService(s, policy=PracticeSelectionPolicy.default())
                req = Requester(external_user_id="probe-det", school_id=None, role="STUDENT")
                b = await svc.create_practice("probe-det", requester=req,
                                              content_code="C_MAIN", question_count=8)
                return _uuid.UUID(b["practice_id"])

        async def items(aid):
            async with self.factory() as s:
                from agente_ia_edu.db.models.assessments import (
                    ActivityAssignment, AssessmentItem,
                )
                av = (await s.execute(select(ActivityAssignment.assessment_version_id)
                                      .where(ActivityAssignment.id == aid))).scalar_one()
                return sorted(str(x) for x in (await s.execute(
                    select(AssessmentItem.question_version_id)
                    .where(AssessmentItem.assessment_version_id == av))).scalars().all())

        a = self.loop.run_until_complete(pick())
        b = self.loop.run_until_complete(pick())
        # two independent runs, same policy + same (empty) recent history -> same picks
        self.assertEqual(self.loop.run_until_complete(items(a)),
                         self.loop.run_until_complete(items(b)))
        self.assertEqual(len(self.loop.run_until_complete(items(a))), 8)

    # -- 11  tenant isolation / always-self -------------------
    def test_other_student_cannot_read_my_practice(self):
        self._link("owner_x"); self._student("owner_x")
        pid = self._create("C_MAIN", 5).json()["practice_id"]
        self._link("intruder_y", school="school-2"); self._student("intruder_y", school="school-2")
        self.assertEqual(self.client.get(f"/api/v1/student/practice/{pid}").status_code, 403)
        self._student("intruder_same_school")
        self.assertEqual(self.client.get(f"/api/v1/student/practice/{pid}").status_code, 403)

    def test_practice_is_always_self(self):
        self._link("s_self"); self._student("s_self")
        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": "C_MAIN", "question_count": 5,
                                   "student_external_id": "somebody_else"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["student_external_id"], "s_self")
        lst = self.client.get("/api/v1/student/practice").json()
        self.assertTrue(all(i["practice_id"] for i in lst["items"]))

    # -- 12  full loop: play + autosave + reload + correct + result --
    def test_full_practice_loop(self):
        self._link("s12"); self._student("s12")
        pid = self._create("C_MAIN", 5).json()["practice_id"]
        st = self.client.post(f"/api/v1/student/activities/{pid}/attempt").json()
        self.assertEqual(st["status"], "IN_PROGRESS")
        vids = [q["question_version_id"] for q in st["questions"]]
        # answer 3, autosave, reload sees them
        for pos in range(3):
            self.client.put(f"/api/v1/student/activities/{pid}/attempt/answers/{vids[pos]}",
                            json={"selected_option": "A"})
        reload = self.client.get(f"/api/v1/student/activities/{pid}/attempt").json()
        self.assertEqual(sum(1 for q in reload["questions"] if q.get("selected_option")), 3)
        # finish the rest correctly, complete + correct
        for pos in range(3, 5):
            self.client.put(f"/api/v1/student/activities/{pid}/attempt/answers/{vids[pos]}",
                            json={"selected_option": self.correct_key[vids[pos]]})
        self.client.post(f"/api/v1/student/activities/{pid}/attempt/complete")
        res = self.client.post(f"/api/v1/student/activities/{pid}/attempt/correct").json()
        self.assertEqual(res["result"]["question_count"], 5)
        self.assertEqual(res["result"]["assignment_id"], pid)
        # practice detail now reports CORRECTED + a result block
        pd = self.client.get(f"/api/v1/student/practice/{pid}").json()
        self.assertEqual(pd["state"], "PRACTICE_CORRECTED")
        self.assertEqual(pd["result"]["question_count"], 5)
        self.assertIn(pd["state"], ("PRACTICE_CORRECTED",))

    # -- 13/14/15  Domain Map origin PRACTICE, OFFICIAL preserved, rebuild idempotent --
    def test_domain_map_keeps_practice_separate_from_official(self):
        student = "s_sep"
        self._link(student); self._student(student)

        # (a) an OFFICIAL activity on C_MAIN (teacher builds + assigns to the class)
        off_ids = self.by_content["C_MAIN"][:6]
        self._as(_ctx("prof_a", "school-1"))
        lid = self.client.post("/api/v1/question-bank/lists", json={
            "question_version_ids": off_ids, "title": "P22 official",
            "answer_key_presentation": "KEY_AT_END"}).json()["id"]
        self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        aid = self.client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
            "target_type": "CLASS", "target_id": "t-1",
            "available_from": "2000-01-01T00:00:00Z"}).json()["id"]
        self._student(student)
        self.client.post(f"/api/v1/student/activities/{aid}/attempt")
        for vid in off_ids:
            self.client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{vid}",
                            json={"selected_option": self.correct_key[vid]})
        self.client.post(f"/api/v1/student/activities/{aid}/attempt/complete")
        self.client.post(f"/api/v1/student/activities/{aid}/attempt/correct")

        c_off = self._rebuild_content("C_MAIN")
        self.assertEqual(c_off["origin_breakdown"].get("OFFICIAL_ACTIVITY"), 6)
        self.assertNotIn("PRACTICE", c_off["origin_breakdown"])

        # (b) now a PRACTICE on the same content
        pid = self._create("C_MAIN", 5).json()["practice_id"]
        self._play(pid, correct_positions={0, 1})

        c_both = self._rebuild_content("C_MAIN")
        self.assertEqual(c_both["origin_breakdown"].get("OFFICIAL_ACTIVITY"), 6)   # unchanged
        self.assertEqual(c_both["origin_breakdown"].get("PRACTICE"), 5)            # separate key
        self.assertEqual(c_both["questions_answered"], 11)

        # (c) rebuild again -> identical (idempotent, derived)
        c_again = self._rebuild_content("C_MAIN")
        self.assertEqual(c_again["origin_breakdown"], c_both["origin_breakdown"])
        self.assertEqual(c_again["questions_answered"], c_both["questions_answered"])

    # -- 16  Learning Path reflects the new practice evidence -----
    def test_learning_path_updates_after_practice(self):
        student = "s_path"
        self._link(student); self._student(student)
        before = self.client.get("/api/v1/student/study-path").json()
        self.assertNotIn("C_MAIN", [s["content_code"] for s in before["steps"]]
                         + [s["content_code"] for s in before["mastered"]])
        pid = self._create("C_MAIN", 10).json()["practice_id"]
        self._play(pid, correct_positions={0, 1, 2})   # 3/10 -> weak -> recommended
        after = self.client.get("/api/v1/student/study-path").json()
        tgt = next((s for s in after["steps"] if s["content_code"] == "C_MAIN"), None)
        self.assertIsNotNone(tgt)
        self.assertIn(tgt["content_state"], ("RECOMMENDED", "READY", "NEEDS_REVIEW"))
        self.assertTrue(tgt["practice_available"])
        self.assertTrue(tgt["action_available"])

    # -- 17  no N+1 on create + list ------------------------------
    def test_no_n_plus_1(self):
        self._link("s_n1"); self._student("s_n1")

        def count_for(count):
            n = {"c": 0}

            @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
            def _c(*_a):  # noqa: ANN001
                n["c"] += 1
            try:
                async def _do():
                    async with self.factory() as s:
                        svc = AdaptivePracticeService(s)
                        req = Requester(external_user_id="s_n1",
                                        school_id=_school_uuid("school-1"), role="STUDENT")
                        n["c"] = 0
                        await svc.create_practice("s_n1", requester=req,
                                                  content_code="C_MAIN", question_count=count)
                        return n["c"]
                return self.loop.run_until_complete(_do())
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", _c)

        # query cost must not scale with the number of selected questions
        small = count_for(5)
        big = count_for(20)
        self.assertLessEqual(big - small, 8,
                             f"create query count scales with N: 5->{small}, 20->{big}")

        # listing many practices is a bounded number of batched queries
        for _ in range(6):
            self._create("C_MAIN", 5)
        n = {"c": 0}

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def _c2(*_a):  # noqa: ANN001
            n["c"] += 1
        try:
            async def _list():
                async with self.factory() as s:
                    svc = AdaptivePracticeService(s)
                    req = Requester(external_user_id="s_n1",
                                    school_id=_school_uuid("school-1"), role="STUDENT")
                    n["c"] = 0
                    await svc.list_practices("s_n1", requester=req)
                    return n["c"]
            q = self.loop.run_until_complete(_list())
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", _c2)
        self.assertLessEqual(q, 6, f"list_practices is not batched: {q}")

    # -- 18  AI-agnostic import guard ---------------------------
    def test_module_is_ai_agnostic(self):
        import agente_ia_edu.services.adaptive_practice as mod
        src = importlib.util.find_spec(mod.__name__).origin
        with open(src, encoding="utf-8") as fh:
            body = fh.read()
        for banned in ("openai", "AsyncOpenAI", "OpenAIProvider", "OpenAIProvider",
                       "build_text_provider", "providers.router", "classification_consensus",
                       "classification_prompts", "AsyncOpenAI"):
            self.assertNotIn(banned, body)

    # -- 19  official bank + answer key immutable --------------
    def test_official_tables_unchanged(self):
        async def counts():
            async with self.factory() as s:
                return {k: await s.scalar(select(func.count()).select_from(m)) for k, m in {
                    "q": Question, "v": QuestionVersion, "o": QuestionOption,
                    "bq": BookletQuestion, "cn": CatalogNode,
                    "pc": PedagogicalClassification, "ake": AnswerKeyEntry,
                    "akr": AnswerKeyRevision}.items()}

        self._link("s_ro"); self._student("s_ro")
        before = self.loop.run_until_complete(counts())
        pid = self._create("C_MAIN", 10).json()["practice_id"]
        self._play(pid)
        self.client.post("/api/v1/student/domain/rebuild")
        self.client.get("/api/v1/student/study-path")
        after = self.loop.run_until_complete(counts())
        self.assertEqual(before, after)

    # -- 20  contract modes rejected (not yet executed) --------
    def test_contract_modes_not_executed(self):
        self._link("s_modes"); self._student("s_modes")
        for mode in ("PRACTICE_REVIEW", "PRACTICE_PREREQUISITE", "PRACTICE_MIXED"):
            r = self._create("C_MAIN", 5, mode=mode)
            self.assertEqual(r.status_code, 422, mode)
            self.assertIn("prática", r.json()["detail"]["message"].lower())


if __name__ == "__main__":
    unittest.main()
