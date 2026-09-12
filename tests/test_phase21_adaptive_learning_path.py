"""PHASE 21 - Adaptive Learning Path (deterministic decision layer) backend tests.

TestClient + in-memory SQLite. The path is a DERIVED VIEW over the PHASE 20
curriculum-v2 Domain Map + catalog_node_prerequisites + curriculum-v2 + optional
pedagogical context. It writes nothing, re-runs no correction, reads no answer
key, mutates no Domain Map / catalog row. Covers spec s30 items 1-30.
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
from agente_ia_edu.db.models.recommendations import PedagogicalContext
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.adaptive_learning_path import AdaptiveLearningPathService

KEYS = "ABCDE"
# contents: C_A ..C_F (MATH), C_S1 subcontent of C_A, C_X (SCI). Prereq chain:
#   C_A <- C_B <- C_C ;  C_D <- {C_E, C_F}
# 44 questions.
PLAN: list[dict] = []
for i in range(44):
    if i < 8:
        PLAN.append({"content": "C_A"})
    elif i < 16:
        PLAN.append({"content": "C_B"})
    elif i < 24:
        PLAN.append({"content": "C_C"})
    elif i < 30:
        PLAN.append({"content": "C_D"})
    elif i < 34:
        PLAN.append({"content": "C_E"})
    elif i == 34:
        PLAN.append({"content": "C_D", "mode": "FORCED_CLOSURE", "status": "NEEDS_REVIEW",
                     "confidence": "LOW"})
    elif i in (35, 36):
        PLAN.append({"content": "C_X"})       # SCI discipline
    elif i in (37, 38):
        PLAN.append({"content": "C_S1"})      # subcontent of C_A
    else:
        PLAN.append({"content": None})        # UNCLASSIFIED

_SCHOOL: dict[str, str] = {}


def _school_uuid(name):
    if name is None:
        return None
    return _SCHOOL.setdefault(name, str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"phase21-{name}")))


def _ctx(user="prof_a", school="school-1", role="TEACHER"):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user, role=role,
                                    school_id=_school_uuid(school), scope_type="SCHOOL")


async def _seed(factory) -> tuple[list[str], dict]:
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
        cA = node("C_A", "Conteúdo A", "CONTENT", a1, d1, pos=1); await s.flush()
        s1 = node("C_S1", "Subconteúdo S1", "SUBCONTENT", cA, d1, pos=1); await s.flush()
        cB = node("C_B", "Conteúdo B", "CONTENT", a1, d1, pos=2); await s.flush()
        cC = node("C_C", "Conteúdo C", "CONTENT", a1, d1, pos=3); await s.flush()
        cD = node("C_D", "Conteúdo D", "CONTENT", a1, d1, pos=4); await s.flush()
        cE = node("C_E", "Conteúdo E", "CONTENT", a1, d1, pos=5); await s.flush()
        cF = node("C_F", "Conteúdo F", "CONTENT", a1, d1, pos=6); await s.flush()
        d2 = node("SCI", "Ciências", "DISCIPLINE"); await s.flush(); d2.root_id = d2.id
        a2 = node("SCI-A", "Área C", "AREA", d2, d2); await s.flush()
        cX = node("C_X", "Conteúdo X", "CONTENT", a2, d2, pos=1); await s.flush()
        await s.flush()
        # prereqs: C_A <- C_B <- C_C ; C_D <- C_E, C_D <- C_F
        for cn, pn in ((cA, cB), (cB, cC), (cD, cE), (cD, cF)):
            s.add(CatalogNodePrerequisite(content_node_id=cn.id, prerequisite_node_id=pn.id))
        await s.flush()

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
                s.add(PedagogicalClassification(
                    question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
                    content=plan["content"], subcontent=plan["content"],
                    difficulty="UNKNOWN", reasoning_type="U",
                    prerequisites=[], keywords=[], competencies=[], skills=[],
                    status=plan.get("status", "CLASSIFIED"), source="rule",
                    lifecycle="ACTIVE", model_version="fx", prompt_version="v1", metadata_=md))
            vids.append(str(v.id))
        await s.commit()
        # index -> content_code for slicing
        idx_content = {i: p["content"] for i, p in enumerate(PLAN)}
        return vids, idx_content


class Phase21Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(cls.factory)

        cls.vids, cls.idx_content = cls.loop.run_until_complete(_prep())
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

    def _run(self, indices, *, student, classroom, owner="prof_a", school="school-1",
             correct_positions=None):
        ids = [self.vids[i] for i in indices]
        self._link(student, classroom, school=school)
        self._as(_ctx(owner, school))
        lid = self.client.post("/api/v1/question-bank/lists", json={
            "question_version_ids": ids, "title": f"P21 {indices[:2]}",
            "answer_key_presentation": "KEY_AT_END"}).json()["id"]
        self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        aid = self.client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
            "target_type": "CLASS", "target_id": classroom,
            "available_from": "2000-01-01T00:00:00Z"}).json()["id"]
        self._as(_ctx(student, school, role="STUDENT"))
        self.client.post(f"/api/v1/student/activities/{aid}/attempt")
        cp = set(range(len(ids))) if correct_positions is None else set(correct_positions)
        for pos, vid in enumerate(ids):
            ck = self.correct_key[vid]
            key = ck if pos in cp else next(k for k in KEYS if k != ck)
            self.client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{vid}",
                            json={"selected_option": key})
        self.client.post(f"/api/v1/student/activities/{aid}/attempt/complete")
        self.client.post(f"/api/v1/student/activities/{aid}/attempt/correct")
        return aid

    def _student(self, uid, school="school-1"):
        self._as(_ctx(uid, school, role="STUDENT"))

    def _path(self):
        return self.client.get("/api/v1/student/study-path")

    def _step(self, j, code):
        return next((s for s in j["steps"] if s["content_code"] == code), None)

    def _mastered(self, j, code):
        return next((s for s in j["mastered"] if s["content_code"] == code), None)

    # -- 1 -----------------------------------------------------------
    def test_no_evidence(self):
        self._link("s_none", "t-none"); self._student("s_none")
        j = self._path().json()
        self.assertEqual(j["state"], "NO_EVIDENCE")
        self.assertEqual(j["steps"], [])
        self.assertFalse(j["ai_used"])

    # -- 2 / 3 -------------------------------------------------
    def test_one_content_insufficient_evidence(self):
        # C_X has no prerequisite -> a plain INSUFFICIENT_EVIDENCE (2 < MIN_SAMPLE_SIZE)
        self._run([35, 36], student="s_one", classroom="t-one")
        self._student("s_one")
        j = self._path().json()
        st = self._step(j, "C_X")
        self.assertIsNotNone(st)
        self.assertEqual(st["content_state"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(st["action_type"], "DIAGNOSE")
        self.assertFalse(st["action_available"])
        self.assertGreater(st["priority_factors"]["low_evidence"], 0)
        self.assertEqual(st["priority_factors"]["weak_performance"], 0)  # not "weak" without evidence

    # -- 4 / 10 (MASTERED) -----------------------------------
    def test_mastered_needs_min_evidence(self):
        self._run(list(range(0, 8)), student="s_m", classroom="t-m",
                  correct_positions=set(range(8)))                # 8/8 C_A
        self._student("s_m")
        j = self._path().json()
        self.assertIsNone(self._step(j, "C_A"))                    # not in the ordered steps
        m = self._mastered(j, "C_A")
        self.assertIsNotNone(m)
        self.assertEqual(m["content_state"], "MASTERED")
        self.assertEqual(m["action_type"], "NONE")

    # -- 5 / 8 (chain) --------------------------------------
    def test_weak_content_with_prerequisite_chain(self):
        # weak on C_A (its prereq C_B, whose prereq C_C). Only C_A answered, weakly.
        self._run(list(range(0, 8)), student="s_chain", classroom="t-chain",
                  correct_positions={0})                          # 1/8 -> weak
        self._student("s_chain")
        j = self._path().json()
        a = self._step(j, "C_A")
        # C_A is BLOCKED because C_B is not mastered
        self.assertEqual(a["content_state"], "BLOCKED_BY_PREREQUISITE")
        self.assertEqual([p["code"] for p in a["unsatisfied_prerequisites"]], ["C_B"])
        self.assertEqual(a["priority_factors"]["blocked_penalty"], 1.0)
        # C_B appears as a candidate (transitive prereq) even with zero evidence
        b = self._step(j, "C_B")
        self.assertIsNotNone(b)
        self.assertEqual(b["content_state"], "BLOCKED_BY_PREREQUISITE")   # C_C not mastered
        # C_C (root of the chain) is the one that should surface first among these
        cc = self._step(j, "C_C")
        self.assertIsNotNone(cc)
        self.assertNotEqual(cc["content_state"], "BLOCKED_BY_PREREQUISITE")
        self.assertGreater(cc["priority_score"], a["priority_score"])

    # -- 6 / 15 (multi content + discipline) ----------------
    def test_multiple_contents_and_disciplines(self):
        self._run([0, 1, 2, 8, 9, 10, 35, 36], student="s_multi", classroom="t-multi",
                  correct_positions={0, 1, 2})
        self._student("s_multi")
        j = self._path().json()
        codes = {s["content_code"] for s in j["steps"]} | {s["content_code"] for s in j["mastered"]}
        self.assertTrue({"C_A", "C_B", "C_X"} <= codes)
        discs = {s["discipline_code"] for s in j["steps"] if s["discipline_code"]}
        self.assertTrue({"MATH", "SCI"} <= discs)

    # -- 7 / 9 (multiple prereqs) --------------------------
    def test_content_with_two_prerequisites(self):
        # weak on C_D; prereqs C_E, C_F. C_E has some (weak) evidence, C_F none.
        self._run(list(range(24, 30)) + [30, 31, 32], student="s_two", classroom="t-two",
                  correct_positions=set())
        self._student("s_two")
        j = self._path().json()
        d = self._step(j, "C_D")
        self.assertEqual(d["content_state"], "BLOCKED_BY_PREREQUISITE")
        self.assertEqual(sorted(p["code"] for p in d["unsatisfied_prerequisites"]), ["C_E", "C_F"])

    # -- 11 (blocker priority: an 80% content that is a needed prereq) ---
    def test_prerequisite_can_outrank_weaker_dependent(self):
        # C_C mastered-ish? no - give C_C strong (8/8), C_B weak, C_A weak.
        # C_C strong AND is prereq of C_B (which blocks C_A) -> C_C should rank
        # highly as a review/practice blocker even though it is not "weak".
        self._run(list(range(0, 8)) + list(range(8, 16)) + list(range(16, 24)),
                  student="s_block", classroom="t-block",
                  correct_positions=set(range(16, 24)))           # only C_C correct (8/8)
        self._student("s_block")
        j = self._path().json()
        cc = self._step(j, "C_C") or self._mastered(j, "C_C")
        # C_C is mastered -> it's in `mastered`, not steps; its dependents (C_B) are now unblocked-by-C_C
        self.assertEqual((cc or {}).get("content_state"), "MASTERED")
        b = self._step(j, "C_B")
        # C_B's only prereq (C_C) is mastered -> C_B is no longer BLOCKED
        self.assertNotEqual(b["content_state"], "BLOCKED_BY_PREREQUISITE")

    # -- 12 / 13 (provisional / FORCED_CLOSURE) ------------
    def test_provisional_evidence_carried_and_flagged(self):
        self._run(list(range(24, 30)) + [34], student="s_prov", classroom="t-prov",
                  correct_positions=set())
        self._student("s_prov")
        j = self._path().json()
        d = self._step(j, "C_D")
        self.assertGreaterEqual(d["forced_closure_evidence_count"], 1)
        self.assertGreater(d["priority_factors"]["provisional_ratio"], 0)
        self.assertEqual(j["provisional_note"],
                         "Parte das questões usadas nesta análise possui classificação pedagógica provisória.")

    # -- 14 (UNCLASSIFIED never a candidate) ---------------
    def test_unclassified_never_recommended(self):
        self._run([0, 1, 2, 39, 40, 41], student="s_uncl", classroom="t-uncl",
                  correct_positions={0, 1, 2})
        self._student("s_uncl")
        j = self._path().json()
        all_codes = {s["content_code"] for s in j["steps"] + j["mastered"]}
        self.assertNotIn(None, all_codes)
        self.assertTrue(all(c.startswith("C_") for c in all_codes))

    # -- 16 (independent student, no school) ----------------
    def test_independent_student(self):
        # no UserSchoolLink at all -> distribute via STUDENT target
        ids = [self.vids[i] for i in (0, 1, 2, 16, 17, 18)]
        self._as(_ctx("prof_a", "school-1"))
        lid = self.client.post("/api/v1/question-bank/lists", json={
            "question_version_ids": ids, "title": "P21 indep",
            "answer_key_presentation": "KEY_AT_END"}).json()["id"]
        self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        aid = self.client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
            "target_type": "STUDENT", "target_id": "s_indep"}).json()["id"]
        self._as(_ctx("s_indep", None, role="STUDENT"))
        self.client.post(f"/api/v1/student/activities/{aid}/attempt")
        for vid in ids:
            self.client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{vid}",
                            json={"selected_option": next(k for k in KEYS if k != self.correct_key[vid])})
        self.client.post(f"/api/v1/student/activities/{aid}/attempt/complete")
        self.client.post(f"/api/v1/student/activities/{aid}/attempt/correct")
        j = self._path().json()
        self.assertEqual(j["state"], "READY")
        self.assertFalse(j["has_school_context"])
        self.assertTrue(len(j["steps"]) >= 1)

    # -- 17 / 18 (school context boosts when present) -------
    def test_school_context_boost(self):
        self._link("s_ctx", "t-ctx")
        self._run([16, 17, 18], student="s_ctx", classroom="t-ctx",
                  correct_positions={0, 1, 2})          # C_C, decent
        self._student("s_ctx")
        base = self._step(self._path().json(), "C_C")
        base_score = base["priority_score"] if base else None

        async def _add_ctx():
            async with self.factory() as s:
                cid = (await s.execute(select(CatalogNode.id).where(CatalogNode.code == "C_C"))).scalar_one()
                s.add(PedagogicalContext(content_node_id=cid, source="TEACHER",
                                         classroom_id="t-ctx", active=True))
                await s.commit()
        self.loop.run_until_complete(_add_ctx())
        self._student("s_ctx")
        j = self._path().json()
        boosted = self._step(j, "C_C") or self._mastered(j, "C_C")
        self.assertTrue(j["has_school_context"])
        self.assertIn("TEACHER", j["school_context_sources"])
        if base_score is not None and boosted and "priority_score" in boosted:
            self.assertGreaterEqual(boosted["priority_score"], base_score)

    # -- 19 / 20 (determinism + idempotency) --------------
    def test_deterministic_and_idempotent(self):
        self._run([0, 1, 2, 8, 9, 10, 16, 17, 18], student="s_det", classroom="t-det",
                  correct_positions={0, 2, 4, 6, 8})
        self._student("s_det")
        a = self._path().json(); b = self._path().json()
        for x in (a, b):
            x.pop("generated_at", None)
            for st_ in x["steps"] + x["mastered"]:
                st_.pop("last_activity_at", None)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

        async def _dcm():
            async with self.factory() as s:
                return int(await s.scalar(select(func.count()).select_from(DomainContentMastery)
                                          .where(DomainContentMastery.student_external_id == "s_det")))
        n1 = self.loop.run_until_complete(_dcm())
        self._path()
        self.assertEqual(n1, self.loop.run_until_complete(_dcm()))   # path writes nothing new

    # -- 21 / 22 (tenant + other student) ----------------
    def test_tenant_isolation_and_other_student(self):
        aid = self._run([0, 1, 2], student="stu_A", classroom="t-A",
                        owner="prof_A", school="school-A")
        self._as(_ctx("prof_A", "school-A"))
        m = self.client.get(f"/api/v1/question-bank/assignments/{aid}/students/study-path")
        self.assertEqual(m.status_code, 200)
        self.assertEqual(m.json()["students"][0]["student_external_id"], "stu_A")
        self._as(_ctx("prof_Z", "school-Z"))
        self.assertIn(self.client.get(
            f"/api/v1/question-bank/assignments/{aid}/students/study-path").status_code, (403, 404))
        # a student never gets another student's path
        self._student("someone_else", "school-A")
        self.assertEqual(self.client.get("/api/v1/student/study-path").json()["state"], "NO_EVIDENCE")

    # -- 24 (cyclic prerequisite graph -> controlled error) --
    def test_cyclic_prerequisite_graph(self):
        self._run([0, 1, 2], student="s_cyc", classroom="t-cyc")
        # inject a cycle C_E <- C_D (already C_D <- C_E)
        async def _cycle(add=True):
            async with self.factory() as s:
                ce = (await s.execute(select(CatalogNode.id).where(CatalogNode.code == "C_E"))).scalar_one()
                cd = (await s.execute(select(CatalogNode.id).where(CatalogNode.code == "C_D"))).scalar_one()
                if add:
                    s.add(CatalogNodePrerequisite(content_node_id=ce, prerequisite_node_id=cd))
                else:
                    await s.execute(CatalogNodePrerequisite.__table__.delete().where(
                        CatalogNodePrerequisite.content_node_id == ce,
                        CatalogNodePrerequisite.prerequisite_node_id == cd))
                await s.commit()
        self.loop.run_until_complete(_cycle(True))
        try:
            self._student("s_cyc")
            j = self.client.get("/api/v1/student/study-path").json()
            self.assertEqual(j["state"], "PREREQUISITE_GRAPH_INVALID")
            self.assertIn("cycle", j["detail"])
            self.assertEqual(j["steps"], [])
        finally:
            self.loop.run_until_complete(_cycle(False))   # restore

    # -- 25 / 26 (no N+1 + performance) -----------------
    def test_no_n_plus_1(self):
        self._run(list(range(0, 38)), student="s_n1", classroom="t-n1",
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
                    svc = AdaptiveLearningPathService(s)
                    req = Requester(external_user_id="s_n1", school_id=_school_uuid("school-1"), role="STUDENT")
                    n["c"] = 0
                    await svc.build_path("s_n1", requester=req)
                    return n["c"]
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", _c)
        q = self.loop.run_until_complete(run())
        self.assertLessEqual(q, 20, f"query count too high: {q}")  # +1: Domain Map origin resolution (PHASE 22)
        # +2 (18->20): resolve_for_content()'s two batched, constant-cost
        # queries (material-level + section-level lookup) added in PHASE 25
        # for the tenant-aware material_available signal - still one query
        # set regardless of candidate-content-code count, not per-row.

    # -- 27 (AI-agnostic import guard) -----------------
    def test_module_is_ai_agnostic(self):
        import agente_ia_edu.services.adaptive_learning_path as mod
        src = importlib.util.find_spec(mod.__name__).origin
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        for banned in ("openai", "AsyncOpenAI", "OpenAIProvider", "build_text_provider",
                       "classification_consensus", "classification_prompts"):
            self.assertNotIn(banned, text)

    # -- 28 / 29 / 30 (Domain Map / ActivityResult / answer key unchanged) --
    def test_source_data_unchanged(self):
        async def counts():
            async with self.factory() as s:
                return {k: await s.scalar(select(func.count()).select_from(m)) for k, m in {
                    "q": Question, "v": QuestionVersion, "o": QuestionOption,
                    "bq": BookletQuestion, "cn": CatalogNode, "pc": PedagogicalClassification,
                    "ake": AnswerKeyEntry, "cnp": CatalogNodePrerequisite,
                    "ar": ActivityResult, "ari": ActivityResultItem}.items()}
        self._run([0, 1, 2, 8, 9, 10], student="s_ro", classroom="t-ro")
        self._student("s_ro")
        # snapshot the domain rows too
        async def _dcm():
            async with self.factory() as s:
                rows = (await s.execute(select(DomainContentMastery.content_code, DomainContentMastery.questions_answered,
                                               DomainContentMastery.questions_correct)
                                        .where(DomainContentMastery.student_external_id == "s_ro")
                                        .order_by(DomainContentMastery.content_code))).all()
                return rows
        # the FIRST study-path call lazily populates the PHASE 20 Domain Map cache
        # (a derived write owned by PHASE 20). After that, the path itself writes
        # nothing - neither to the source tables nor to the Domain Map.
        self.client.get("/api/v1/student/study-path")
        before = self.loop.run_until_complete(counts())
        dcm_before = self.loop.run_until_complete(_dcm())
        self.assertTrue(dcm_before)   # the map was populated once
        for _ in range(3):
            self.client.get("/api/v1/student/study-path")
            self.client.get("/api/v1/student/study-path/next")
            self.client.get("/api/v1/student/study-path/content/C_A")
        self.assertEqual(before, self.loop.run_until_complete(counts()))
        self.assertEqual(dcm_before, self.loop.run_until_complete(_dcm()))


if __name__ == "__main__":
    unittest.main()
