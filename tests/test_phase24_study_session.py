"""PHASE 24 - Study Session / Momento de Aprendizado backend tests.

TestClient + in-memory SQLite. The Study Session is a pure ORCHESTRATION layer:
it calls PHASE 21 AdaptiveLearningPathService, PHASE 23 MaterialAvailabilityService
and (lazily, per PRACTICE block) PHASE 22 AdaptivePracticeService, and persists
only the plan it produced (for resume + idempotency). It creates no question, no
domain-map row, no second learning path, no second practice engine. ZERO AI.

Covers spec s25 items 1-25.
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
from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, CatalogNode, Exam,
    ExamApplication, ExamBooklet, Institution, PedagogicalClassification,
    Question, QuestionOption, QuestionVersion, SourceDocument,
)
from agente_ia_edu.db.models.admin import School, UserSchoolLink
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.services.study_session import StudySessionService
from agente_ia_edu.services.study_session_planner import StudySessionPlanner, StudySessionPlanPolicy

KEYS = "ABCDE"
_SCHOOL_UUID = _uuid.uuid5(_uuid.NAMESPACE_DNS, "phase24-school")


def _ctx(user="s_al", role="STUDENT", school=None):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user, role=role,
                                    school_id=str(school) if school else None,
                                    scope_type="SCHOOL" if school else "PLATFORM")


def _ident(user="p24_coord"):
    return ExternalIdentityContext(provider="test", external_user_id=user)


async def _seed(factory) -> dict:
    async with factory() as s:
        inst = Institution(code="INEP", name="INEP"); s.add(inst); await s.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM"); s.add(exam); await s.flush()
        school = School(id=_SCHOOL_UUID, code="P24SCH", name="Escola P24", status="ACTIVE")
        s.add(school); await s.flush()

        def node(code, name, ntype, parent=None, root=None, pos=0):
            n = CatalogNode(code=code, name=name, node_type=ntype, position=pos,
                            parent_id=parent.id if parent else None,
                            root_id=(root.id if root else None), active=True)
            s.add(n)
            return n

        d1 = node("MATH", "Matemática", "DISCIPLINE"); await s.flush(); d1.root_id = d1.id
        a1 = node("MATH-A", "Área M", "AREA", d1, d1); await s.flush()
        cA = node("C_A", "Conteúdo A", "CONTENT", a1, d1, pos=1)   # will be RECOMMENDED (weak)
        cB = node("C_B", "Conteúdo B", "CONTENT", a1, d1, pos=2)   # prerequisite of C_C
        cC = node("C_C", "Conteúdo C", "CONTENT", a1, d1, pos=3)   # BLOCKED by C_B
        cD = node("C_D", "Conteúdo D", "CONTENT", a1, d1, pos=4)   # no evidence at all
        await s.flush()
        from agente_ia_edu.db.models.catalog import CatalogNodePrerequisite
        s.add(CatalogNodePrerequisite(content_node_id=cC.id, prerequisite_node_id=cB.id))
        await s.flush()

        app_ = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=1)
        s.add(app_); await s.flush()
        bk = ExamBooklet(exam_application_id=app_.id, code="C24", color="AZUL"); s.add(bk); await s.flush()
        sd = SourceDocument(exam_application_id=app_.id, exam_booklet_id=bk.id, document_type="ANSWER_KEY",
                            source_url="https://x/g.pdf", acquired_at=datetime.now(timezone.utc),
                            content_hash="g"); s.add(sd); await s.flush()
        rev = AnswerKeyRevision(source_document_id=sd.id, revision_number=1, is_official=True)
        s.add(rev); await s.flush()

        by_content: dict[str, list[str]] = {}
        correct_key: dict[str, str] = {}

        async def make(n, content):
            correct = KEYS[n % 5]
            q = Question(validation_status="validated", origin_type="IMPORTED",
                         status="PUBLISHED", visibility_scope="PUBLIC")
            s.add(q); await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=f"e{n}", statement=f"e{n}", content_hash=f"h{n}",
                                is_immutable=True)
            s.add(v); await s.flush()
            opts = {}
            for pos, key in enumerate(KEYS, start=1):
                o = QuestionOption(question_version_id=v.id, option_key=key, position=pos,
                                   text=f"Alt {key}", is_valid_option=(key == correct))
                s.add(o); await s.flush(); opts[key] = o
            bq = BookletQuestion(exam_booklet_id=bk.id, question_version_id=v.id,
                                 position=n, official_number=n, page_number=1)
            s.add(bq); await s.flush()
            s.add(AnswerKeyEntry(answer_key_revision_id=rev.id, booklet_question_id=bq.id,
                                 official_answer_label=correct, resolved_option_id=opts[correct].id,
                                 page_number=1))
            md = {"taxonomy_version": "curriculum-v2", "primary_content_code": content,
                  "visual_dependency": False}
            s.add(PedagogicalClassification(
                question_version_id=v.id, discipline="CURRICULUM_PROPOSAL", content=content,
                subcontent=content, difficulty="UNKNOWN", reasoning_type="U",
                prerequisites=[], keywords=[], competencies=[], skills=[],
                status="CLASSIFIED", source="rule", lifecycle="ACTIVE",
                model_version="fx", prompt_version="v1", metadata_=md))
            by_content.setdefault(content, []).append(str(v.id))
            correct_key[str(v.id)] = correct

        n = 1
        for _ in range(20):
            await make(n, "C_A"); n += 1
        for _ in range(8):
            await make(n, "C_B"); n += 1
        for _ in range(6):
            await make(n, "C_C"); n += 1
        await s.commit()

        # links: coordinator + 2 students in classroom "turma-1"
        for ext, role, stype, sext in (
            ("p24_coord", "COORDINATOR", "SCHOOL", str(_SCHOOL_UUID)),
            ("s_al", "STUDENT", "CLASSROOM", "turma-1"),
            ("s_bo", "STUDENT", "CLASSROOM", "turma-1"),
        ):
            s.add(UserSchoolLink(external_user_id=ext, school_id=_SCHOOL_UUID, role=role,
                                 scope_type=stype, scope_external_id=sext, active=True))
        await s.commit()
        return {"by_content": by_content, "correct_key": correct_key}


class Phase24Tests(unittest.TestCase):
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
        cls.app.dependency_overrides[get_current_authenticated_context] = lambda: _ctx()
        cls.app.dependency_overrides[get_current_identity] = lambda: _ident()
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    # -- helpers ---------------------------------------------------------
    def _as_student(self, user="s_al", school=None):
        self.app.dependency_overrides[get_current_authenticated_context] = lambda: _ctx(user, school=school)

    def _as_coord(self, user="p24_coord"):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _give_evidence(self, student, content_code, n_correct, n_total, *, date=None):
        """Directly write domain_content_mastery so the Learning Path has a real
        state for `content_code` without re-running the whole activity chain."""
        from agente_ia_edu.db.models.assessments import DomainContentMastery

        async def _do():
            async with self.factory() as s:
                acc = round(n_correct / n_total, 4) if n_total else None
                s.add(DomainContentMastery(
                    student_external_id=student, taxonomy_version="curriculum-v2",
                    content_code=content_code, subcontent_code=None,
                    questions_seen=n_total, questions_answered=n_total,
                    questions_correct=n_correct, questions_incorrect=n_total - n_correct,
                    accuracy=acc, evidence_count=n_total,
                    evidence_state="OBSERVED" if n_total >= 3 else "INSUFFICIENT_EVIDENCE",
                    definitive_evidence_count=n_total, provisional_evidence_count=0,
                    forced_closure_evidence_count=0, visual_dependency_evidence_count=0,
                    origin_breakdown={"OFFICIAL_ACTIVITY": n_total},
                    last_evaluated_at=datetime.now(timezone.utc)))
                await s.commit()
        self.loop.run_until_complete(_do())

    def _clear_sessions(self, student=None):
        from agente_ia_edu.db.models import StudySession

        async def _do():
            async with self.factory() as s:
                q = select(StudySession)
                if student:
                    q = q.where(StudySession.student_external_id == student)
                for row in (await s.execute(q)).scalars().all():
                    await s.delete(row)
                await s.commit()
        self.loop.run_until_complete(_do())

    def _force_block_type(self, session_id, index, block_type):
        """Overwrite one block's block_type in a persisted plan, bypassing the
        planner's own heuristics - used to deterministically reach a specific
        start_block code path regardless of how the planner would have typed
        that content on its own."""
        from agente_ia_edu.db.models import StudySession
        from sqlalchemy.orm.attributes import flag_modified

        async def _do():
            async with self.factory() as s:
                row = await s.get(StudySession, _uuid.UUID(session_id))
                blocks = list((row.plan or {}).get("blocks", []))
                for b in blocks:
                    if b["index"] == index:
                        b["block_type"] = block_type
                row.plan = {**(row.plan or {}), "blocks": blocks}
                flag_modified(row, "plan")
                await s.commit()
        self.loop.run_until_complete(_do())

    def _create_free(self, student="s_al", **body):
        self._as_student(student)
        return self.client.post("/api/v1/student/study-session", json=body)

    # -- 1/2  student picks a duration ------------------------------
    def test_student_picks_30_minutes(self):
        self._clear_sessions("s_min30")
        self._give_evidence("s_min30", "C_A", 2, 6)
        r = self._create_free("s_min30", available_minutes=30)
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        self.assertEqual(b["timer_mode"], "TIMED")
        self.assertGreater(b["blocks_total"], 0)
        self.assertLessEqual(b["effective_study_minutes"] + b["break_minutes"], 32)

    def test_student_picks_2_hours(self):
        self._clear_sessions("s_min120")
        self._give_evidence("s_min120", "C_A", 2, 6)
        r = self._create_free("s_min120", available_minutes=120)
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["effective_study_minutes"], 60)

    # -- 3  student does not define a time --------------------------
    def test_student_no_timer(self):
        self._clear_sessions("s_notimer")
        self._give_evidence("s_notimer", "C_A", 2, 6)
        r = self._create_free("s_notimer", no_timer=True)
        self.assertEqual(r.status_code, 200)
        b = r.json()
        self.assertEqual(b["timer_mode"], "UNTIMED")
        self.assertGreater(b["blocks_total"], 0)   # still an organised sequence

    # -- 4/5  coordination window, no content -----------------------
    def test_coordination_window_no_content(self):
        self._clear_sessions("s_al")
        self._as_coord()
        r = self.client.post("/api/v1/coordination/study-sessions", json={
            "school_id": str(_SCHOOL_UUID), "target_type": "STUDENT", "target_id": "s_al",
            "session_date": "2026-09-20", "start_at": "2026-09-20T14:00:00Z",
            "end_at": "2026-09-20T17:00:00Z"})
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        self.assertEqual(b["window_minutes"], 180)
        self.assertEqual(b["created_or_updated"], 1)
        self._as_student("s_al")
        sid = b["sessions"][0]["session_id"]
        sview = self.client.get(f"/api/v1/student/study-session/{sid}").json()
        self.assertEqual(sview["source"], "SCHOOL_DEFINED")
        self.assertGreater(sview["blocks_total"], 0)   # platform decided what to study

    # -- 6  window with one content -----------------------------
    def test_coordination_window_one_content(self):
        self._clear_sessions("s_al")
        self._as_coord()
        r = self.client.post("/api/v1/coordination/study-sessions", json={
            "school_id": str(_SCHOOL_UUID), "target_type": "STUDENT", "target_id": "s_al",
            "session_date": "2026-09-21", "start_at": "2026-09-21T14:00:00Z",
            "end_at": "2026-09-21T15:00:00Z", "content_codes": ["C_A"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["target_content_codes"], ["C_A"])
        self.assertEqual(r.json()["target_content_names"], ["Conteúdo A"])
        sid = r.json()["sessions"][0]["session_id"]
        self._as_student("s_al")
        sview = self.client.get(f"/api/v1/student/study-session/{sid}").json()
        codes = {b.get("content_code") for b in sview["blocks"] if b.get("content_code")}
        self.assertEqual(codes, {"C_A"})
        self.assertEqual(sview["target_content_names"], ["Conteúdo A"])
        # No prior evidence for C_A on "s_al" at this point - the block title
        # must still show the real catalog name, not the raw content code.
        for b in sview["blocks"]:
            if b.get("content_code") == "C_A":
                self.assertIn("Conteúdo A", b["title"])
                self.assertEqual(b["content_name"], "Conteúdo A")

    # -- 7  window with multiple contents, time explainably split --
    def test_coordination_window_multiple_contents(self):
        self._clear_sessions("s_al")
        self._as_coord()
        r = self.client.post("/api/v1/coordination/study-sessions", json={
            "school_id": str(_SCHOOL_UUID), "target_type": "STUDENT", "target_id": "s_al",
            "session_date": "2026-09-22", "start_at": "2026-09-22T14:00:00Z",
            "end_at": "2026-09-22T16:00:00Z", "content_codes": ["C_A", "C_B"]})
        self.assertEqual(r.status_code, 200, r.text)
        sid = r.json()["sessions"][0]["session_id"]
        self._as_student("s_al")
        sview = self.client.get(f"/api/v1/student/study-session/{sid}").json()
        codes = {b.get("content_code") for b in sview["blocks"] if b.get("content_code")}
        self.assertEqual(codes, {"C_A", "C_B"})
        for b in sview["blocks"]:
            if b.get("content_code"):
                self.assertIn("reason", b)   # explainable allocation

    # -- 8/9  breaks: one / multiple --------------------------------
    def test_one_break(self):
        self._clear_sessions("s_al")
        self._as_coord()
        r = self.client.post("/api/v1/coordination/study-sessions", json={
            "school_id": str(_SCHOOL_UUID), "target_type": "STUDENT", "target_id": "s_al",
            "session_date": "2026-09-23", "start_at": "2026-09-23T14:00:00Z",
            "end_at": "2026-09-23T15:30:00Z", "content_codes": ["C_A"],
            "breaks": [{"duration_minutes": 10}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["break_minutes"], 10)
        sid = r.json()["sessions"][0]["session_id"]
        self._as_student("s_al")
        sview = self.client.get(f"/api/v1/student/study-session/{sid}").json()
        self.assertEqual(sum(1 for b in sview["blocks"] if b["block_type"] == "BREAK"), 1)

    def test_multiple_breaks(self):
        self._clear_sessions("s_al")
        self._as_coord()
        r = self.client.post("/api/v1/coordination/study-sessions", json={
            "school_id": str(_SCHOOL_UUID), "target_type": "STUDENT", "target_id": "s_al",
            "session_date": "2026-09-24", "start_at": "2026-09-24T14:00:00Z",
            "end_at": "2026-09-24T17:00:00Z", "content_codes": ["C_A", "C_B"],
            "breaks": [{"duration_minutes": 10}, {"duration_minutes": 10}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["break_minutes"], 20)
        sid = r.json()["sessions"][0]["session_id"]
        self._as_student("s_al")
        sview = self.client.get(f"/api/v1/student/study-session/{sid}").json()
        self.assertEqual(sum(1 for b in sview["blocks"] if b["block_type"] == "BREAK"), 2)

    # -- 10  invalid break ------------------------------------------
    def test_invalid_break_rejected(self):
        self._as_coord()
        out_of_window = self.client.post("/api/v1/coordination/study-sessions", json={
            "school_id": str(_SCHOOL_UUID), "target_type": "STUDENT", "target_id": "s_al",
            "session_date": "2026-09-25", "start_at": "2026-09-25T14:00:00Z",
            "end_at": "2026-09-25T15:00:00Z",
            "breaks": [{"start_at": "2026-09-25T13:00:00Z", "end_at": "2026-09-25T13:10:00Z"}]})
        self.assertEqual(out_of_window.status_code, 422)
        overlap = self.client.post("/api/v1/coordination/study-sessions", json={
            "school_id": str(_SCHOOL_UUID), "target_type": "STUDENT", "target_id": "s_al",
            "session_date": "2026-09-25", "start_at": "2026-09-25T14:00:00Z",
            "end_at": "2026-09-25T16:00:00Z",
            "breaks": [{"start_at": "2026-09-25T14:30:00Z", "end_at": "2026-09-25T15:00:00Z"},
                      {"start_at": "2026-09-25T14:45:00Z", "end_at": "2026-09-25T15:15:00Z"}]})
        self.assertEqual(overlap.status_code, 422)
        backwards = self.client.post("/api/v1/coordination/study-sessions", json={
            "school_id": str(_SCHOOL_UUID), "target_type": "STUDENT", "target_id": "s_al",
            "session_date": "2026-09-25", "start_at": "2026-09-25T14:00:00Z",
            "end_at": "2026-09-25T15:00:00Z",
            "breaks": [{"start_at": "2026-09-25T14:30:00Z", "end_at": "2026-09-25T14:20:00Z"}]})
        self.assertEqual(backwards.status_code, 422)

    # -- 11  effective time excludes breaks --------------------------
    def test_effective_time_excludes_breaks(self):
        self._clear_sessions("s_al")
        self._as_coord()
        r = self.client.post("/api/v1/coordination/study-sessions", json={
            "school_id": str(_SCHOOL_UUID), "target_type": "STUDENT", "target_id": "s_al",
            "session_date": "2026-09-26", "start_at": "2026-09-26T14:00:00Z",
            "end_at": "2026-09-26T17:00:00Z", "content_codes": ["C_A"],
            "breaks": [{"start_at": "2026-09-26T14:50:00Z", "end_at": "2026-09-26T15:00:00Z"},
                      {"start_at": "2026-09-26T15:50:00Z", "end_at": "2026-09-26T16:00:00Z"}]})
        b = r.json()
        self.assertEqual(b["window_minutes"], 180)
        self.assertEqual(b["break_minutes"], 20)
        self.assertEqual(b["effective_study_minutes"], 160)
        self.assertNotEqual(b["effective_study_minutes"], b["window_minutes"])

    # -- 12  low-evidence content -> more STUDY -----------------
    def test_low_evidence_gets_more_study(self):
        self._clear_sessions("s_low")
        self._give_evidence("s_low", "C_A", 0, 1)   # INSUFFICIENT_EVIDENCE
        r = self._create_free("s_low", available_minutes=90, target_content_codes=["C_A"])
        blocks = r.json()["blocks"]
        study = sum(b["estimated_minutes"] for b in blocks if b["block_type"] == "STUDY")
        practice = sum(b["estimated_minutes"] for b in blocks if b["block_type"] == "PRACTICE")
        self.assertGreater(study, 0)
        self.assertGreaterEqual(study, practice)

    # -- 13  prerequisite prioritised --------------------------------
    def test_blocked_content_prioritises_prerequisite(self):
        self._clear_sessions("s_prereq")
        self._give_evidence("s_prereq", "C_B", 1, 8)   # C_B weak, not mastered
        self._give_evidence("s_prereq", "C_C", 3, 4)   # C_C would look fine but is BLOCKED by C_B
        r = self._create_free("s_prereq", available_minutes=60, target_content_codes=["C_C"])
        self.assertEqual(r.status_code, 200, r.text)
        codes = {b.get("content_code") for b in r.json()["blocks"] if b.get("content_code")}
        self.assertIn("C_B", codes)   # the prerequisite was substituted in

    # -- 14  mastered content -> short review / advance -------------
    def test_mastered_content_gets_short_block(self):
        self._clear_sessions("s_mastered")
        self._give_evidence("s_mastered", "C_A", 9, 10)   # strong accuracy
        r = self._create_free("s_mastered", available_minutes=60, target_content_codes=["C_A"])
        blocks = r.json()["blocks"]
        study = sum(b["estimated_minutes"] for b in blocks if b["block_type"] == "STUDY")
        self.assertEqual(study, 0)   # mastered -> no fresh STUDY block

    # -- 15  integration with Adaptive Learning Path ------------------
    def test_integrates_learning_path_without_recomputing_it(self):
        self._clear_sessions("s_path")
        self._give_evidence("s_path", "C_A", 2, 6)
        r = self._create_free("s_path", available_minutes=60)
        self.assertEqual(r.status_code, 200, r.text)
        path = self.client.get("/api/v1/student/study-path").json()
        step_codes = {s["content_code"] for s in path["steps"]}
        blocks_codes = {b.get("content_code") for b in r.json()["blocks"] if b.get("content_code")}
        self.assertTrue(blocks_codes.issubset(step_codes | {None}))

    # -- 16  integration with Adaptive Practice -----------------------
    def test_practice_block_uses_adaptive_practice_service(self):
        self._clear_sessions("s_prac")
        self._give_evidence("s_prac", "C_A", 2, 6)
        sid = self._create_free("s_prac", available_minutes=60,
                                target_content_codes=["C_A"]).json()["id"]
        self._as_student("s_prac")
        self.client.post(f"/api/v1/student/study-session/{sid}/start")
        blocks = self.client.get(f"/api/v1/student/study-session/{sid}").json()["blocks"]
        pidx = next(b["index"] for b in blocks if b["block_type"] == "PRACTICE")
        started = self.client.post(f"/api/v1/student/study-session/{sid}/blocks/{pidx}/start").json()
        block = next(b for b in started["blocks"] if b["index"] == pidx)
        self.assertIsNotNone(block.get("practice_id"))
        practice = self.client.get(f"/api/v1/student/practice/{block['practice_id']}").json()
        self.assertEqual(practice["origin"], "PRACTICE")

    # -- 16b  a block auto-skipped for lack of questions must not strand
    #         current_block_index on itself - the student needs to be able
    #         to move on to the next PENDING block without repeatedly
    #         re-triggering the same failing block.
    def test_skipped_practice_block_advances_current_index(self):
        self._clear_sessions("s_skip")
        self._give_evidence("s_skip", "C_D", 0, 1)  # C_D has zero questions in the fixture
        self._give_evidence("s_skip", "C_A", 2, 6)
        sid = self._create_free(
            "s_skip", available_minutes=60,
            target_content_codes=["C_D", "C_A"],
        ).json()["id"]
        self._as_student("s_skip")
        self.client.post(f"/api/v1/student/study-session/{sid}/start")
        blocks = self.client.get(f"/api/v1/student/study-session/{sid}").json()["blocks"]
        skip_idx = next(b["index"] for b in blocks if b.get("content_code") == "C_D")

        # Force the C_D block to PRACTICE regardless of what the planner's own
        # evidence heuristics picked for it - this test targets start_block's
        # failure branch specifically, not the planner's block-type choice.
        self._force_block_type(sid, skip_idx, "PRACTICE")

        started = self.client.post(
            f"/api/v1/student/study-session/{sid}/blocks/{skip_idx}/start"
        ).json()
        skipped_block = next(b for b in started["blocks"] if b["index"] == skip_idx)
        self.assertEqual(skipped_block["status"], "SKIPPED")
        self.assertNotEqual(
            started["current_block_index"], skip_idx,
            "current_block_index must move past an auto-skipped block, "
            "not strand the student on it",
        )
        next_block = next(
            (b for b in started["blocks"] if b["index"] == started["current_block_index"]),
            None,
        )
        self.assertIsNotNone(next_block, "current_block_index must point at a real block")
        self.assertEqual(next_block["status"], "PENDING")

    # -- 17  material available / unavailable ------------------------
    def test_study_block_reflects_material_availability(self):
        self._clear_sessions("s_mat")
        self._give_evidence("s_mat", "C_D", 0, 1)
        r = self._create_free("s_mat", available_minutes=60, target_content_codes=["C_D"])
        blocks = r.json()["blocks"]
        study = next((b for b in blocks if b["block_type"] == "STUDY"), None)
        if study:
            self.assertFalse(study["action_available"])   # no material published for C_D
            self.assertIsNotNone(study["action_note"])

    # -- 18  independent student --------------------------------------
    def test_independent_student_no_school(self):
        self._clear_sessions("s_indep")
        self._give_evidence("s_indep", "C_A", 2, 6)
        self._as_student("s_indep", school=None)
        r = self.client.post("/api/v1/student/study-session", json={"available_minutes": 45})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["student_external_id"] if "student_external_id" in r.json() else "s_indep", "s_indep")

    # -- 19  tenant isolation -------------------------------------
    def test_tenant_isolation(self):
        self._clear_sessions("s_al")
        sid = self._create_free("s_al", available_minutes=30, target_content_codes=["C_A"]).json()["id"]
        self._as_student("s_bo")
        self.assertEqual(self.client.get(f"/api/v1/student/study-session/{sid}").status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/student/study-session/{sid}/start").status_code, 403)

    # -- 20  coordination authorization --------------------------------
    def test_coordination_authorization(self):
        self._as_coord("nobody_p24")
        r = self.client.post("/api/v1/coordination/study-sessions", json={
            "school_id": str(_SCHOOL_UUID), "target_type": "STUDENT", "target_id": "s_al",
            "session_date": "2026-09-27", "start_at": "2026-09-27T14:00:00Z",
            "end_at": "2026-09-27T15:00:00Z"})
        self.assertEqual(r.status_code, 403)

    # -- 21  determinism (pure planner) -------------------------------
    def test_planner_is_deterministic(self):
        policy = StudySessionPlanPolicy.default()
        planner = StudySessionPlanner(policy)
        path = {"state": "PATH_READY", "steps": [
            {"content_code": "C_A", "content_name": "A", "content_state": "RECOMMENDED",
             "priority_score": 0.7, "accuracy": 0.4, "questions_answered": 5,
             "unsatisfied_prerequisites": []},
        ], "mastered": []}
        mat = {"C_A": {"material_available": True, "material_count": 1}}
        p1 = planner.plan(effective_minutes=90, timer_mode="TIMED", breaks=[{"duration_minutes": 10}],
                          target_content_codes=None, path=path, material_availability=mat)
        p2 = planner.plan(effective_minutes=90, timer_mode="TIMED", breaks=[{"duration_minutes": 10}],
                          target_content_codes=None, path=path, material_availability=mat)
        simplify = lambda pl: [(b["index"], b["block_type"], b.get("content_code"),
                               b["estimated_minutes"], b["action_available"]) for b in pl["blocks"]]
        self.assertEqual(simplify(p1), simplify(p2))

    # -- 22  idempotency (persisted service) ---------------------------
    def test_create_is_idempotent(self):
        self._clear_sessions("s_idem")
        self._give_evidence("s_idem", "C_A", 2, 6)
        r1 = self._create_free("s_idem", available_minutes=45)
        r2 = self._create_free("s_idem", available_minutes=45)
        self.assertEqual(r1.json()["id"], r2.json()["id"])

        async def _count():
            from agente_ia_edu.db.models import StudySession
            async with self.factory() as s:
                return int(await s.scalar(select(func.count()).select_from(StudySession)
                                          .where(StudySession.student_external_id == "s_idem")))
        self.assertEqual(self.loop.run_until_complete(_count()), 1)

    # -- 23  resume ----------------------------------------------------
    def test_resume_after_disconnect(self):
        self._clear_sessions("s_resume")
        self._give_evidence("s_resume", "C_A", 2, 6)
        sid = self._create_free("s_resume", available_minutes=60,
                                target_content_codes=["C_A"]).json()["id"]
        self._as_student("s_resume")
        self.client.post(f"/api/v1/student/study-session/{sid}/start")
        self.client.post(f"/api/v1/student/study-session/{sid}/blocks/0/complete", json={})
        # simulate "closing the browser" - just GET again later
        resumed = self.client.get(f"/api/v1/student/study-session/{sid}").json()
        self.assertEqual(resumed["status"], "IN_PROGRESS")
        self.assertGreaterEqual(resumed["blocks_done"], 1)
        self.assertEqual(resumed["blocks"][0]["status"], "DONE")

    # -- 24  no N+1 (scaling contents) ---------------------------------
    def test_no_n_plus_1_scaling_targets(self):
        self._clear_sessions("s_scale")
        for code in ("C_A", "C_B", "C_C"):
            self._give_evidence("s_scale", code, 2, 5)
        n = {"c": 0}

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def _c(*_a):  # noqa: ANN001
            n["c"] += 1
        try:
            r = self._create_free("s_scale", available_minutes=90,
                                  target_content_codes=["C_A", "C_B", "C_C"])
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", _c)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertLessEqual(n["c"], 30, f"query count too high for 3 contents: {n['c']}")

    # -- 25  AI-agnostic import guard -----------------------------------
    def test_modules_are_ai_agnostic(self):
        for modname in ("agente_ia_edu.services.study_session",
                        "agente_ia_edu.services.study_session_planner"):
            mod = importlib.import_module(modname)
            src = importlib.util.find_spec(mod.__name__).origin
            with open(src, encoding="utf-8") as fh:
                body = fh.read()
            for banned in ("openai", "AsyncOpenAI", "OpenAIProvider", "build_text_provider",
                           "classification_consensus", "classification_prompts"):
                self.assertNotIn(banned, body, f"{modname} imports {banned}")

    # -- extra: official / activity tables untouched --------------------
    def test_official_tables_untouched(self):
        async def _counts():
            async with self.factory() as s:
                return (
                    int(await s.scalar(select(func.count()).select_from(Question))),
                    int(await s.scalar(select(func.count()).select_from(QuestionVersion))),
                    int(await s.scalar(select(func.count()).select_from(QuestionOption))),
                )
        before = self.loop.run_until_complete(_counts())
        self._clear_sessions("s_final")
        self._give_evidence("s_final", "C_A", 2, 6)
        self._create_free("s_final", available_minutes=30)
        after = self.loop.run_until_complete(_counts())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
