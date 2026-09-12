"""PHASE 20 - curriculum-v2 Domain Map (persistent, DERIVED) backend tests.

TestClient + in-memory SQLite. The Domain Map is a deterministic, recomputable
projection of the immutable ActivityResult / ActivityResultItem history onto the
ACTIVE curriculum-v2 catalog. Correction is never re-run, the answer key is never
re-read, and no official / result row is written. Covers spec s21 items 1-28.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import unittest
import uuid as _uuid
from datetime import datetime, timedelta, timezone

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
from agente_ia_edu.db.models.assessments import (
    ActivityAttempt, ActivityResult, ActivityResultItem, DomainContentMastery,
)
from agente_ia_edu.db.models.catalog import CatalogNodePrerequisite
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.curriculum_domain_map import CurriculumDomainMapService

KEYS = "ABCDE"
# 40 questions. C1 (MATH-ALG-LINEAR), SC1 (MATH-ALG-LINEAR-SYS) under C1,
# C2 (MATH-ALG-QUAD), C3 (SCI-PHY-MECH). Prereq: C1 <- C0 (MATH-ARITH).
PLAN: list[dict] = []
for i in range(40):
    if i < 10:
        PLAN.append({"content": "MATH-ALG-LINEAR"})
    elif i < 20:
        PLAN.append({"content": "MATH-ALG-QUAD"})
    elif i < 30:
        PLAN.append({"content": "SCI-PHY-MECH"})
    elif i in (30, 31):
        PLAN.append({"content": "MATH-ALG-LINEAR-SYS"})       # subcontent grain
    elif i == 32:
        PLAN.append({"content": "MATH-ALG-QUAD", "mode": "FORCED_CLOSURE",
                     "status": "NEEDS_REVIEW", "confidence": "LOW"})
    elif i == 33:
        PLAN.append({"content": "SCI-PHY-MECH", "confidence": "LOW"})
    elif i == 34:
        PLAN.append({"content": "SCI-PHY-MECH", "visual": True})
    else:
        PLAN.append({"content": None})                        # UNCLASSIFIED

_SCHOOL: dict[str, str] = {}


def _school_uuid(name):
    if name is None:
        return None
    return _SCHOOL.setdefault(name, str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"phase20-{name}")))


def _ctx(user="prof_a", school="school-1", role="TEACHER"):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user, role=role,
                                    school_id=_school_uuid(school), scope_type="SCHOOL")


async def _seed(factory) -> list[str]:
    async with factory() as s:
        inst = Institution(code="INEP", name="INEP"); s.add(inst); await s.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM"); s.add(exam); await s.flush()

        def node(code, name, ntype, parent=None, root=None):
            n = CatalogNode(code=code, name=name, node_type=ntype,
                            parent_id=parent.id if parent else None,
                            root_id=(root.id if root else None), active=True)
            s.add(n)
            return n
        d1 = node("MATH", "Matemática", "DISCIPLINE"); await s.flush(); d1.root_id = d1.id
        a1 = node("MATH-ALG", "Álgebra", "AREA", d1, d1); await s.flush()
        c0 = node("MATH-ARITH", "Aritmética", "CONTENT", a1, d1); await s.flush()
        c1 = node("MATH-ALG-LINEAR", "Funções lineares", "CONTENT", a1, d1); await s.flush()
        sc1 = node("MATH-ALG-LINEAR-SYS", "Sistemas lineares", "SUBCONTENT", c1, d1); await s.flush()
        c2 = node("MATH-ALG-QUAD", "Funções quadráticas", "CONTENT", a1, d1); await s.flush()
        d2 = node("SCI", "Ciências da Natureza", "DISCIPLINE"); await s.flush(); d2.root_id = d2.id
        a2 = node("SCI-PHY", "Física", "AREA", d2, d2); await s.flush()
        c3 = node("SCI-PHY-MECH", "Mecânica", "CONTENT", a2, d2); await s.flush()
        await s.flush()
        # C1 requires C0
        s.add(CatalogNodePrerequisite(content_node_id=c1.id, prerequisite_node_id=c0.id))

        app = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=1)
        s.add(app); await s.flush()
        bk = ExamBooklet(exam_application_id=app.id, code="CAD", color="AZUL"); s.add(bk); await s.flush()
        sd = SourceDocument(exam_application_id=app.id, exam_booklet_id=bk.id, document_type="ANSWER_KEY",
                            source_url="https://x/g.pdf", acquired_at=datetime.now(timezone.utc), content_hash="g")
        s.add(sd); await s.flush()
        rev = AnswerKeyRevision(source_document_id=sd.id, revision_number=1, is_official=True)
        s.add(rev); await s.flush()

        vids: list[str] = []
        for idx, plan in enumerate(PLAN):
            num = idx + 1
            correct = KEYS[num % 5]
            q = Question(validation_status="validated", origin_type="IMPORTED",
                         status="PUBLISHED", visibility_scope="PUBLIC")
            s.add(q); await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=f"e{num}", statement=f"e{num}", content_hash=f"h{num}",
                                is_immutable=True)
            s.add(v); await s.flush()
            opts = {}
            for pos, key in enumerate(KEYS, start=1):
                o = QuestionOption(question_version_id=v.id, option_key=key, position=pos,
                                   text=f"Alt {key}", is_valid_option=(key == correct))
                s.add(o); await s.flush(); opts[key] = o
            bq = BookletQuestion(exam_booklet_id=bk.id, question_version_id=v.id,
                                 position=num, official_number=num, page_number=1)
            s.add(bq); await s.flush()
            s.add(AnswerKeyEntry(answer_key_revision_id=rev.id, booklet_question_id=bq.id,
                                 official_answer_label=correct, resolved_option_id=opts[correct].id,
                                 page_number=1))
            if plan["content"]:
                md = {"taxonomy_version": "curriculum-v2", "primary_content_code": plan["content"]}
                if plan.get("mode"):
                    md["classification_mode"] = plan["mode"]
                if plan.get("confidence"):
                    md["confidence"] = plan["confidence"]
                if plan.get("visual"):
                    md["visual_dependency"] = True
                s.add(PedagogicalClassification(
                    question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
                    content=plan["content"], subcontent=plan["content"],
                    difficulty="UNKNOWN", reasoning_type="U",
                    prerequisites=[], keywords=[], competencies=[], skills=[],
                    status=plan.get("status", "CLASSIFIED"), source="rule",
                    lifecycle="ACTIVE", model_version="fx", prompt_version="v1", metadata_=md))
            vids.append(str(v.id))
        await s.commit()
        return vids


class Phase20Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(cls.factory)

        cls.vids = cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls._ctx = _ctx()
        cls.app.dependency_overrides[get_current_authenticated_context] = lambda: cls._ctx
        cls.client = TestClient(cls.app)
        cls.correct_key = {vid: KEYS[(i + 1) % 5] for i, vid in enumerate(cls.vids)}

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    # -- helpers ---------------------------------------------------

    def _as(self, ctx):
        self.__class__._ctx = ctx
        self.app.dependency_overrides[get_current_authenticated_context] = lambda: ctx

    def _link(self, uid, classroom, school="school-1", active=True):
        async def _do():
            async with self.factory() as s:
                s.add(UserSchoolLink(external_user_id=uid, school_id=_uuid.UUID(_school_uuid(school)),
                                     role="STUDENT", scope_type="CLASSROOM",
                                     scope_external_id=classroom, active=active))
                await s.commit()
        self.loop.run_until_complete(_do())

    def _run_activity(self, indices, *, student, classroom, owner="prof_a", school="school-1",
                      correct_positions=None, unanswered_positions=None, backdate_days=None):
        ids = [self.vids[i] for i in indices]
        self._link(student, classroom, school=school)
        self._as(_ctx(owner, school))
        lid = self.client.post("/api/v1/question-bank/lists", json={
            "question_version_ids": ids, "title": f"P20 {indices[:3]}",
            "answer_key_presentation": "KEY_AT_END"}).json()["id"]
        self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        aid = self.client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
            "target_type": "CLASS", "target_id": classroom,
            "available_from": "2000-01-01T00:00:00Z"}).json()["id"]
        self._as(_ctx(student, school, role="STUDENT"))
        self.client.post(f"/api/v1/student/activities/{aid}/attempt")
        cp = set(range(len(ids))) if correct_positions is None else set(correct_positions)
        up = set(unanswered_positions or ())
        for pos, vid in enumerate(ids):
            if pos in up:
                continue
            ck = self.correct_key[vid]
            key = ck if pos in cp else next(k for k in KEYS if k != ck)
            self.client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{vid}",
                            json={"selected_option": key})
        if up:
            self._force_complete(aid)
        else:
            self.client.post(f"/api/v1/student/activities/{aid}/attempt/complete")
        self.client.post(f"/api/v1/student/activities/{aid}/attempt/correct")
        if backdate_days is not None:
            self._backdate(aid, backdate_days)
        return aid

    def _force_complete(self, aid):
        async def _do():
            async with self.factory() as s:
                att = (await s.execute(select(ActivityAttempt).where(
                    ActivityAttempt.assignment_id == _uuid.UUID(aid)))).scalar_one()
                att.status = "COMPLETED"; att.completed_at = datetime.now(timezone.utc)
                await s.commit()
        self.loop.run_until_complete(_do())

    def _backdate(self, aid, days):
        async def _do():
            async with self.factory() as s:
                r = (await s.execute(select(ActivityResult).where(
                    ActivityResult.assignment_id == _uuid.UUID(aid)))).scalar_one()
                r.completed_at = datetime.now(timezone.utc) - timedelta(days=days)
                await s.commit()
        self.loop.run_until_complete(_do())

    def _student(self, uid, school="school-1"):
        self._as(_ctx(uid, school, role="STUDENT"))

    def _map(self, **params):
        return self.client.get("/api/v1/student/domain", params=params or None)

    def _content(self, j, code):
        for d in j["disciplines"]:
            for c in d["contents"]:
                if c["content_code"] == code:
                    return c
        return None

    # -- 1: no results -------------------------------------------
    def test_student_without_results(self):
        self._link("s_none", "t-none"); self._student("s_none")
        j = self._map().json()
        self.assertEqual(j["summary"]["content_count"], 0)
        self.assertEqual(j["disciplines"], [])
        self.assertFalse(j["ai_used"])
        # persisted (empty) rebuild wrote nothing
        async def _cnt():
            async with self.factory() as s:
                return int(await s.scalar(select(func.count()).select_from(DomainContentMastery)
                                          .where(DomainContentMastery.student_external_id == "s_none")))
        self.assertEqual(self.loop.run_until_complete(_cnt()), 0)

    # -- 2 / 3: 1 question then 3 (evidence threshold) ---------
    def test_evidence_threshold(self):
        aid = self._run_activity([0], student="s_thr", classroom="t-thr")
        self._student("s_thr")
        c = self._content(self._map().json(), "MATH-ALG-LINEAR")
        self.assertEqual(c["questions_answered"], 1)
        self.assertEqual(c["evidence_state"], "INSUFFICIENT_EVIDENCE")
        # add two more (a second activity) -> 3 answered -> OBSERVED
        self._run_activity([1, 2], student="s_thr", classroom="t-thr")
        self._student("s_thr")
        self.client.post("/api/v1/student/domain/rebuild")
        c = self._content(self._map().json(), "MATH-ALG-LINEAR")
        self.assertEqual(c["questions_answered"], 3)
        self.assertEqual(c["evidence_state"], "OBSERVED")

    # -- 4 / 5 / 6: all correct / all wrong / mixed -----------
    def test_all_correct_all_wrong_mixed(self):
        self._run_activity(list(range(0, 4)), student="s_ac", classroom="t-ac",
                           correct_positions=set(range(4)))
        self._student("s_ac")
        c = self._content(self._map().json(), "MATH-ALG-LINEAR")
        self.assertEqual((c["questions_correct"], c["questions_incorrect"], c["accuracy"]), (4, 0, 1.0))

        self._run_activity(list(range(10, 14)), student="s_aw", classroom="t-aw",
                           correct_positions=set())
        self._student("s_aw")
        c = self._content(self._map().json(), "MATH-ALG-QUAD")
        self.assertEqual((c["questions_correct"], c["questions_incorrect"], c["accuracy"]), (0, 4, 0.0))

        self._run_activity(list(range(20, 24)), student="s_mx", classroom="t-mx",
                           correct_positions={0, 1})
        self._student("s_mx")
        c = self._content(self._map().json(), "SCI-PHY-MECH")
        self.assertEqual((c["questions_correct"], c["questions_incorrect"], c["accuracy"]), (2, 2, 0.5))

    # -- 7 / 8: multiple contents + disciplines ---------------
    def test_multiple_contents_and_disciplines(self):
        self._run_activity([0, 1, 2, 10, 11, 12, 20, 21, 22], student="s_multi", classroom="t-multi",
                           correct_positions=set(range(9)))
        self._student("s_multi")
        j = self._map().json()
        self.assertEqual(sorted(j["summary"]["disciplines_covered"]), ["MATH", "SCI"])
        self.assertEqual(sorted(c["content_code"] for d in j["disciplines"] for c in d["contents"]),
                         ["MATH-ALG-LINEAR", "MATH-ALG-QUAD", "SCI-PHY-MECH"])
        math = next(d for d in j["disciplines"] if d["discipline_code"] == "MATH")
        self.assertEqual(math["questions_answered"], 6)

    # -- 9: subcontent ----------------------------------------
    def test_subcontent_grain(self):
        self._run_activity([0, 1, 2, 30, 31], student="s_sub", classroom="t-sub",
                           correct_positions=set(range(5)))
        self._student("s_sub")
        c = self._content(self._map().json(), "MATH-ALG-LINEAR")
        # 5 answered land on the CONTENT grain (subcontent qs also count for content)
        self.assertEqual(c["questions_answered"], 5)
        self.assertEqual(len(c["subcontents"]), 1)
        self.assertEqual(c["subcontents"][0]["subcontent_code"], "MATH-ALG-LINEAR-SYS")
        self.assertEqual(c["subcontents"][0]["questions_answered"], 2)
        # a content with no subcontent has none
        self._run_activity([10, 11], student="s_sub2", classroom="t-sub2")
        self._student("s_sub2")
        self.assertEqual(self._content(self._map().json(), "MATH-ALG-QUAD")["subcontents"], [])

    # -- 10: UNCLASSIFIED not attributed ---------------------
    def test_unclassified_not_attributed(self):
        self._run_activity([0, 1, 2, 35, 36, 37], student="s_uncl", classroom="t-uncl",
                           correct_positions=set(range(6)))
        self._student("s_uncl")
        j = self._map().json()
        total = sum(c["questions_answered"] for d in j["disciplines"] for c in d["contents"])
        self.assertEqual(total, 3)   # only the 3 classified questions
        self.assertEqual(j["summary"]["content_count"], 1)

    # -- 11 / 12: NEEDS_REVIEW / FORCED_CLOSURE preserved ----
    def test_provisional_and_forced_closure_preserved(self):
        # index 32 = MATH-ALG-QUAD FORCED_CLOSURE/NEEDS_REVIEW/LOW
        self._run_activity([10, 11, 32], student="s_prov", classroom="t-prov",
                           correct_positions={0, 1, 2})
        self._student("s_prov")
        c = self._content(self._map().json(), "MATH-ALG-QUAD")
        self.assertEqual(c["questions_answered"], 3)
        self.assertEqual(c["provisional_evidence_count"], 1)
        self.assertEqual(c["forced_closure_evidence_count"], 1)
        self.assertEqual(c["definitive_evidence_count"], 2)
        self.assertEqual(self._map().json()["summary"]["forced_closure_evidence_count"], 1)

    # -- 13: visual dependency ------------------------------
    def test_visual_dependency_preserved(self):
        self._run_activity([20, 21, 34], student="s_vis", classroom="t-vis",
                           correct_positions={0, 1, 2})
        self._student("s_vis")
        c = self._content(self._map().json(), "SCI-PHY-MECH")
        self.assertEqual(c["visual_dependency_evidence_count"], 1)

    # -- 11 (prereq): prerequisites exposed -----------------
    def test_prerequisites_exposed(self):
        self._run_activity([0, 1, 2], student="s_pre", classroom="t-pre")
        self._student("s_pre")
        c = self._content(self._map().json(), "MATH-ALG-LINEAR")
        self.assertEqual([p["code"] for p in c["prerequisites"]], ["MATH-ARITH"])
        self.assertEqual(c["prerequisites"][0]["name"], "Aritmética")

    # -- 14: official activity origin ----------------------
    def test_official_activity_origin(self):
        self._run_activity([0, 1, 2], student="s_orig", classroom="t-orig")
        self._student("s_orig")
        c = self._content(self._map().json(), "MATH-ALG-LINEAR")
        self.assertEqual(c["origin_breakdown"], {"OFFICIAL_ACTIVITY": 3})

    # -- 16 / 17: multiple activities + period --------------
    def test_multiple_activities_and_period_filter(self):
        self._run_activity([0, 1, 2], student="s_time", classroom="t-time", backdate_days=40)
        self._run_activity([3, 4, 5], student="s_time", classroom="t-time")   # recent
        self._student("s_time")
        self.client.post("/api/v1/student/domain/rebuild")
        allmap = self._map().json()
        self.assertEqual(self._content(allmap, "MATH-ALG-LINEAR")["questions_answered"], 6)
        # last 7 days -> only the recent 3
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        recent = self._map(since=since).json()
        self.assertEqual(self._content(recent, "MATH-ALG-LINEAR")["questions_answered"], 3)
        self.assertFalse(recent["persisted"])
        # bad range
        self.assertEqual(self.client.get("/api/v1/student/domain", params={
            "since": "2099-01-01T00:00:00Z", "until": "2000-01-01T00:00:00Z"}).status_code, 422)

    # -- 18 / 19: rebuild + idempotency --------------------
    def test_rebuild_and_idempotency(self):
        self._run_activity([0, 1, 2, 10, 11, 12], student="s_re", classroom="t-re",
                           correct_positions={0, 2, 4})
        self._student("s_re")
        r1 = self.client.post("/api/v1/student/domain/rebuild").json()
        r2 = self.client.post("/api/v1/student/domain/rebuild").json()

        def norm(x):
            x = json.loads(json.dumps(x))
            x.pop("generated_at", None)
            for d in x.get("disciplines", []):
                for c in d["contents"]:
                    c["last_evaluated_at"] = None
            return json.dumps(x, sort_keys=True)
        self.assertEqual(norm(r1), norm(r2))

        async def _rowcount():
            async with self.factory() as s:
                return int(await s.scalar(select(func.count()).select_from(DomainContentMastery)
                                          .where(DomainContentMastery.student_external_id == "s_re")))
        self.assertEqual(self.loop.run_until_complete(_rowcount()), 2)   # not doubled

    # -- 20 / 22: tenant isolation + manager scope ---------
    def test_tenant_isolation_and_manager_scope(self):
        aid = self._run_activity([0, 1, 2], student="stu_A", classroom="t-A",
                                 owner="prof_A", school="school-A")
        self._as(_ctx("prof_A", "school-A"))
        m = self.client.get(f"/api/v1/question-bank/assignments/{aid}/students/domain-map")
        self.assertEqual(m.status_code, 200)
        self.assertEqual(m.json()["student_count"], 1)
        self.assertEqual(m.json()["students"][0]["student_external_id"], "stu_A")
        # teacher from another school
        self._as(_ctx("prof_Z", "school-Z"))
        self.assertIn(self.client.get(
            f"/api/v1/question-bank/assignments/{aid}/students/domain-map").status_code, (403, 404))

    # -- 21: a student cannot read another student's map ---
    def test_student_only_reads_own(self):
        self._run_activity([0, 1, 2], student="owner_dm", classroom="t-dm")
        self._link("intruder_dm", "t-dm")
        # the student endpoints are always "self" - an intruder just gets their own (empty) map
        self._student("intruder_dm")
        j = self._map().json()
        self.assertEqual(j["summary"]["content_count"], 0)
        # and there is no student route that takes another student's id
        self.assertEqual(self.client.get("/api/v1/student/domain/content/MATH-ALG-LINEAR").status_code, 404)

    # -- 23 / 24: no N+1 + performance -------------------
    def test_no_n_plus_1(self):
        # one big activity: 24 classified + 3 provisional/visual + 3 unclassified
        self._run_activity(list(range(0, 30)) + [32, 33, 34, 35, 36, 37],
                           student="s_n1", classroom="t-n1",
                           correct_positions=set(range(20)))
        self._student("s_n1")

        async def run():
            from agente_ia_edu.services.question_list_store import Requester
            n = {"c": 0}

            @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
            def _c(*_a):  # noqa: ANN001
                n["c"] += 1
            try:
                async with self.factory() as s:
                    svc = CurriculumDomainMapService(s)
                    req = Requester(external_user_id="s_n1", school_id=_school_uuid("school-1"), role="STUDENT")
                    n["c"] = 0
                    await svc.rebuild_student("s_n1", requester=req)
                    rebuild_q = n["c"]
                    n["c"] = 0
                    await svc.get_map("s_n1", requester=req)
                    read_q = n["c"]
                    return rebuild_q, read_q
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", _c)
        rebuild_q, read_q = self.loop.run_until_complete(run())
        self.assertLessEqual(rebuild_q, 12, f"rebuild query count too high: {rebuild_q}")
        self.assertLessEqual(read_q, 8, f"read query count too high: {read_q}")

    # -- 25: determinism -------------------------------
    def test_deterministic(self):
        self._run_activity([0, 1, 2, 10, 11, 12, 20, 21, 22], student="s_det", classroom="t-det",
                           correct_positions={0, 2, 4, 6, 8})
        self._student("s_det")
        a = self._map().json(); b = self._map().json()
        for x in (a, b):
            x.pop("generated_at", None)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    # -- 26: AI-agnostic import guard ------------------
    def test_module_is_ai_agnostic(self):
        import agente_ia_edu.services.curriculum_domain_map as mod
        src = importlib.util.find_spec(mod.__name__).origin
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        for banned in ("openai", "AsyncOpenAI", "OpenAIProvider", "build_text_provider",
                       "classification_consensus", "classification_prompts", "ai_classification_service"):
            self.assertNotIn(banned, text)

    # -- 27 / 28: ActivityResult + answer key immutable -----
    def test_source_data_unchanged_by_domain_map(self):
        async def counts():
            async with self.factory() as s:
                return {k: await s.scalar(select(func.count()).select_from(m)) for k, m in {
                    "q": Question, "v": QuestionVersion, "o": QuestionOption,
                    "bq": BookletQuestion, "cn": CatalogNode, "pc": PedagogicalClassification,
                    "ake": AnswerKeyEntry, "ar": ActivityResult, "ari": ActivityResultItem}.items()}
        self._run_activity([0, 1, 2, 10, 11, 12], student="s_ro", classroom="t-ro")
        self._student("s_ro")
        before = self.loop.run_until_complete(counts())
        # rebuild + every read path multiple times
        for _ in range(3):
            self.client.post("/api/v1/student/domain/rebuild")
            self._map()
            self.client.get("/api/v1/student/domain/content/MATH-ALG-LINEAR")
            self.client.get("/api/v1/student/domain/discipline/MATH")
            self.client.get("/api/v1/student/domain/evidence/MATH-ALG-LINEAR")
        self.assertEqual(before, self.loop.run_until_complete(counts()))
        # and the result item is byte-for-byte the same
        async def _item():
            async with self.factory() as s:
                rows = (await s.execute(select(ActivityResultItem.is_correct, ActivityResultItem.selected_option_key,
                                               ActivityResultItem.correct_option_key)
                                        .order_by(ActivityResultItem.position))).all()
                return rows
        snap1 = self.loop.run_until_complete(_item())
        self.client.post("/api/v1/student/domain/rebuild")
        self.assertEqual(snap1, self.loop.run_until_complete(_item()))


if __name__ == "__main__":
    unittest.main()
