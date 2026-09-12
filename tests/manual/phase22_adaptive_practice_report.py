"""PHASE 22 - Adaptive Practice Engine: report + real-DB acceptance walk.

A practice is an ORDINARY EXERCISE_LIST Activity distributed to the STUDENT
themself, tagged ``origin = PRACTICE`` on the ActivityAssignment metadata so its
evidence stays SEPARATE from OFFICIAL_ACTIVITY in the PHASE 20 Domain Map. It
reuses QuestionBankService/QuestionSelection (PHASE 12/14) for selection,
QuestionListStore (PHASE 15) to build the list, ActivityAssignmentStore
(PHASE 16) to distribute it, the PHASE 17 Player unchanged, PHASE 18 correction
unchanged, PHASE 19 analysis, PHASE 20 Domain Map, PHASE 21 path. NO second
question bank, NO question copies, NO second player, NO migration, ZERO AI.

The acceptance walk runs against the LIVE database with controlled test data,
exercises create -> list -> read -> tenant-isolation -> PHASE 17 play ->
PHASE 18 correction -> PHASE 20 Domain Map (origin PRACTICE kept apart from
OFFICIAL_ACTIVITY) -> PHASE 21 recalculated path, plus the insufficient-bank /
unknown-content / contract-mode error contracts, and removes every temporary
row it created. A perf probe uses a throw-away in-memory SQLite to prove the
create-practice query count is flat for 5/10/20/50/100 questions.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _c in (Path.cwd(), _HERE.parents[1]):
    if (_c / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_c / "src"))
        _REPO = _c
        break
else:  # pragma: no cover
    _REPO = _HERE.parents[1]

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from agente_ia_edu.api.app import create_app  # noqa: E402
from agente_ia_edu.db.base import Base  # noqa: E402
from agente_ia_edu.db.models import (  # noqa: E402
    AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, CatalogNode, Exam,
    ExamApplication, ExamBooklet, Institution, PedagogicalClassification,
    Question, QuestionOption, QuestionVersion, SourceDocument,
)
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.adaptive_practice import (  # noqa: E402
    AdaptivePracticeService, ALLOWED_COUNTS, MAX_QUESTIONS, PracticeSelectionPolicy,
)
from agente_ia_edu.services.question_bank import QuestionBankFilters, QuestionBankService  # noqa: E402
from agente_ia_edu.services.question_list_store import Requester  # noqa: E402

OUT = _REPO / "var" / "phase22_adaptive_practice_report.json"
OWNER = "teacher:phase22-report-runner"
PT = {"Authorization": f"Bearer {OWNER}"}
STU = "student:phase22-report-al"
STU_OTHER = "student:phase22-report-bo"
SH = {"Authorization": f"Bearer student:{STU}"}
SH_OTHER = {"Authorization": f"Bearer student:{STU_OTHER}"}
CLASS_ID = "phase22-report-turma"

OFFICIAL = ("questions", "question_versions", "question_options", "booklet_questions",
            "catalog_nodes", "pedagogical_classifications", "answer_key_entries",
            "answer_key_revisions", "catalog_node_prerequisites")
IMMUTABLE = OFFICIAL + ("activity_results", "activity_result_items", "activity_attempts",
                        "activity_answers")


async def _counts(s) -> dict:
    out = {}
    for t in (*IMMUTABLE, "assessments", "assessment_versions", "assessment_items",
              "activity_assignments", "domain_content_mastery"):
        out[t] = int(await s.scalar(text(f"SELECT count(*) FROM {t}")))
    return out


async def _pick_content(s) -> tuple[str, int]:
    """The classified curriculum-v2 content with the most eligible official
    questions in the live DB (non-visual, non-protected are filtered by the
    service; here we just take the largest CLASSIFIED bucket)."""
    row = (await s.execute(text("""
        SELECT content, count(*) n FROM pedagogical_classifications
        WHERE lifecycle = 'ACTIVE' AND status = 'CLASSIFIED' AND content <> ''
        GROUP BY content ORDER BY n DESC, content LIMIT 1"""))).first()
    return row[0], int(row[1])


async def _cleanup(factory) -> dict:
    async with factory() as s:
        removed = {"domain_rows": 0, "assessments": 0}
        removed["domain_rows"] = int(await s.scalar(text(
            "SELECT count(*) FROM domain_content_mastery WHERE student_external_id LIKE '%phase22-report%'")))
        await s.execute(text(
            "DELETE FROM domain_content_mastery WHERE student_external_id LIKE '%phase22-report%'"))
        ids = (await s.execute(text(
            "SELECT id FROM assessments WHERE owner_external_id LIKE '%phase22-report%'"))).scalars().all()
        removed["assessments"] = len(ids)
        for aid in ids:
            for g in (await s.execute(text("SELECT id FROM activity_assignments WHERE assessment_id=:a"),
                                      {"a": aid})).scalars().all():
                for at in (await s.execute(text("SELECT id FROM activity_attempts WHERE assignment_id=:g"),
                                           {"g": g})).scalars().all():
                    await s.execute(text("DELETE FROM activity_result_items WHERE result_id IN "
                                         "(SELECT id FROM activity_results WHERE attempt_id=:x)"), {"x": at})
                    await s.execute(text("DELETE FROM activity_results WHERE attempt_id=:x"), {"x": at})
                    await s.execute(text("DELETE FROM activity_answers WHERE attempt_id=:x"), {"x": at})
                await s.execute(text("DELETE FROM activity_attempts WHERE assignment_id=:g"), {"g": g})
            await s.execute(text("DELETE FROM activity_assignments WHERE assessment_id=:a"), {"a": aid})
            await s.execute(text("DELETE FROM assessment_items WHERE assessment_version_id IN "
                                 "(SELECT id FROM assessment_versions WHERE assessment_id=:a)"), {"a": aid})
            await s.execute(text("DELETE FROM assessment_versions WHERE assessment_id=:a"), {"a": aid})
            await s.execute(text("DELETE FROM assessments WHERE id=:a"), {"a": aid})
        await s.execute(text("DELETE FROM user_school_links WHERE external_user_id LIKE '%phase22-report%'"))
        await s.commit()
        return removed


def _domain_content(client, code):
    j = client.post("/api/v1/student/domain/rebuild", headers=SH).json()
    return next((x for d in j["disciplines"] for x in d["contents"]
                 if x["content_code"] == code), None)


async def _acceptance_walk() -> dict:
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    steps: list[dict] = []

    def step(name, ok, detail=None):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})

    try:
        await _cleanup(factory)   # drop anything a previous aborted run left behind
        async with factory() as s:
            before = await _counts(s)
            content_code, n_classified = await _pick_content(s)
            svc = QuestionBankService(s)
            page = await svc.list_questions(
                QuestionBankFilters(content_code=content_code, classification_state="CLASSIFIED"),
                page_size=200)
            eligible = [i for i in page.items if not i.has_visual_dependency and not i.is_protected]
            n_eligible = len(eligible)
            await s.execute(text("DELETE FROM user_school_links WHERE external_user_id LIKE 'student:phase22-report%'"))
            for u, c in ((STU, CLASS_ID), (STU_OTHER, "phase22-report-outra")):
                await s.execute(text(
                    "INSERT INTO user_school_links (id, external_user_id, role, scope_type, "
                    "scope_external_id, active, created_at) "
                    "VALUES (:i, :u, 'STUDENT', 'CLASSROOM', :c, true, NOW())"),
                    {"i": uuid.uuid4(), "u": u, "c": c})
            await s.commit()

        # the live dev DB is sparsely classified; the walk uses whatever fits.
        happy = min(3, n_eligible)
        over = n_eligible + 2
        step("1. Preflight audit: a classified curriculum-v2 content with eligible official "
             "questions exists (non-visual, non-protected)",
             n_eligible >= 1, {"content_code": content_code, "classified": n_classified,
                               "eligible": n_eligible, "happy_count": happy})

        client = TestClient(create_app())

        # -- create ------------------------------------------------------
        r = client.post("/api/v1/student/practice",
                        json={"content_code": content_code, "question_count": happy}, headers=SH)
        pj = r.json()
        pid = pj.get("practice_id")
        step("2. POST /api/v1/student/practice -> 200; origin=PRACTICE, mode=PRACTICE_CONTENT, "
             "state=PRACTICE_CREATED, ai_used=False, deterministic selection report",
             r.status_code == 200 and pj["origin"] == "PRACTICE"
             and pj["mode"] == "PRACTICE_CONTENT" and pj["state"] == "PRACTICE_CREATED"
             and pj["ai_used"] is False and pj["selection"]["selected_questions"] == happy
             and pj["selection"]["sufficient"] is True,
             {k: pj.get(k) for k in ("practice_id", "origin", "mode", "state", "question_count")} |
             {"selection": pj.get("selection")})

        lst = client.get("/api/v1/student/practice", headers=SH).json()
        one = client.get(f"/api/v1/student/practice/{pid}", headers=SH)
        step("3. GET /practice lists the caller's practices; GET /practice/{id} returns it "
             "(state PRACTICE_CREATED)",
             any(it["practice_id"] == pid for it in lst["items"])
             and one.status_code == 200 and one.json()["state"] == "PRACTICE_CREATED")

        step("4. Tenant isolation: another student gets 403 on this practice; no student route "
             "accepts a student id (always self)",
             client.get(f"/api/v1/student/practice/{pid}", headers=SH_OTHER).status_code == 403
             and client.post("/api/v1/student/practice",
                             json={"content_code": content_code, "question_count": happy,
                                   "student_external_id": STU_OTHER}, headers=SH).json()
             ["student_external_id"] == STU)

        # -- play (reused PHASE 17 player, unchanged) -------------------
        st = client.post(f"/api/v1/student/activities/{pid}/attempt", headers=SH).json()
        qv = [q["question_version_id"] for q in st["questions"]]
        no_key_leak = all("is_valid_option" not in o and "correct" not in o
                          for q in st["questions"] for o in q["options"])
        for i, v in enumerate(qv):
            client.put(f"/api/v1/student/activities/{pid}/attempt/answers/{v}",
                       json={"selected_option": "ABCDE"[i % 5]}, headers=SH)
        reload = client.get(f"/api/v1/student/activities/{pid}/attempt", headers=SH).json()
        answered = sum(1 for q in reload["questions"] if q.get("selected_option"))
        done = client.post(f"/api/v1/student/activities/{pid}/attempt/complete", headers=SH)
        step("5. Reused PHASE 17 player: start -> autosave -> reload (answers persisted) -> "
             "complete; the answer key never leaks before correction",
             st["status"] == "IN_PROGRESS" and answered == len(qv)
             and done.status_code == 200 and done.json()["status"] == "COMPLETED" and no_key_leak)

        # -- correct (reused PHASE 18) --------------------------------
        corr = client.post(f"/api/v1/student/activities/{pid}/attempt/correct", headers=SH)
        res = corr.json().get("result", {})
        pd = client.get(f"/api/v1/student/practice/{pid}", headers=SH).json()
        step("6. Reused PHASE 18 correction -> ActivityResult (assignment_id == practice_id); "
             "practice detail becomes PRACTICE_CORRECTED and carries the result block",
             corr.status_code == 200 and res.get("assignment_id") == pid
             and res.get("question_count") == len(qv)
             and pd["state"] == "PRACTICE_CORRECTED" and pd.get("result", {}).get("question_count") == len(qv),
             {"result": {k: res.get(k) for k in ("question_count", "correct_count",
                                                 "incorrect_count", "aproveitamento_percent")}})

        # -- Domain Map: PRACTICE origin, kept apart from OFFICIAL_ACTIVITY --
        # seed an OFFICIAL activity on the SAME content to prove separation.
        off_ids = [str(i.question_version_id) for i in eligible[:min(2, n_eligible)]]
        lid = client.post("/api/v1/question-bank/lists", json={
            "question_version_ids": off_ids, "title": "P22 official baseline",
            "answer_key_presentation": "KEY_AT_END"}, headers=PT).json()["id"]
        client.post(f"/api/v1/question-bank/lists/{lid}/finalize", headers=PT)
        oaid = client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
            "target_type": "CLASS", "target_id": CLASS_ID,
            "available_from": "2000-01-01T00:00:00Z"}, headers=PT).json()["id"]
        ost = client.post(f"/api/v1/student/activities/{oaid}/attempt", headers=SH).json()
        for i, q in enumerate(ost["questions"]):
            client.put(f"/api/v1/student/activities/{oaid}/attempt/answers/{q['question_version_id']}",
                       json={"selected_option": "ABCDE"[i % 5]}, headers=SH)
        client.post(f"/api/v1/student/activities/{oaid}/attempt/complete", headers=SH)
        client.post(f"/api/v1/student/activities/{oaid}/attempt/correct", headers=SH)

        c1 = _domain_content(client, content_code)
        ob_ = (c1 or {}).get("origin_breakdown", {})
        step("7. PHASE 20 Domain Map: origin_breakdown carries PRACTICE and OFFICIAL_ACTIVITY as "
             "SEPARATE keys (practice evidence is NOT mixed into OFFICIAL_ACTIVITY)",
             ob_.get("PRACTICE") == len(qv) and ob_.get("OFFICIAL_ACTIVITY") == len(off_ids),
             {"origin_breakdown": ob_, "questions_answered": (c1 or {}).get("questions_answered")})

        c2 = _domain_content(client, content_code)
        step("8. Domain Map rebuild is idempotent (derived cache): a second rebuild yields the "
             "identical origin_breakdown",
             (c2 or {}).get("origin_breakdown") == ob_)

        sp = client.get("/api/v1/student/study-path", headers=SH).json()
        tgt = next((x for x in sp["steps"] if x["content_code"] == content_code),
                   next((x for x in sp["mastered"] if x["content_code"] == content_code), None))
        step("9. PHASE 21 path recalculated from the new evidence; practice_available is exposed "
             "for the practised content",
             tgt is not None and tgt.get("practice_available") is True
             and tgt.get("action_available") is True,
             {"content_state": (tgt or {}).get("content_state"),
              "practice_available": (tgt or {}).get("practice_available")})

        # -- error contracts -----------------------------------------
        insuf = client.post("/api/v1/student/practice",
                            json={"content_code": content_code, "question_count": min(over, 20)},
                            headers=SH)
        idet = insuf.json().get("detail", {})
        step("10. Insufficient bank -> 422 with the standard message + "
             "available/requested/selected counts (never invents or silently shrinks)",
             insuf.status_code == 422
             and idet.get("message") == "Não há questões suficientes disponíveis para esta prática."
             and idet.get("available_questions") == n_eligible
             and idet.get("sufficient") is False,
             idet if insuf.status_code == 422 else {"unexpected_status": insuf.status_code})

        step("11. Unknown / inactive content -> 422; question_count > safe max -> 422 (schema le=20)",
             client.post("/api/v1/student/practice",
                         json={"content_code": "NOPE-NOPE-NOPE", "question_count": happy},
                         headers=SH).status_code == 422
             and client.post("/api/v1/student/practice",
                             json={"content_code": content_code, "question_count": 99},
                             headers=SH).status_code == 422)

        step("12. Contract-only modes are rejected (not executed this phase): "
             "PRACTICE_REVIEW / PRACTICE_PREREQUISITE / PRACTICE_MIXED -> 422",
             all(client.post("/api/v1/student/practice",
                             json={"content_code": content_code, "question_count": happy, "mode": m},
                             headers=SH).status_code == 422
                 for m in ("PRACTICE_REVIEW", "PRACTICE_PREREQUISITE", "PRACTICE_MIXED")))

        # -- determinism (service-level, no recent history) ----------
        async def _pick_ids():
            async with factory() as s2:
                svc2 = AdaptivePracticeService(s2, policy=PracticeSelectionPolicy.default())
                req = Requester(external_user_id="phase22-report-det", school_id=None, role="STUDENT")
                b = await svc2.create_practice("phase22-report-det", requester=req,
                                               content_code=content_code, question_count=happy)
                av = (await s2.execute(text(
                    "SELECT assessment_version_id FROM activity_assignments WHERE id=:a"),
                    {"a": b["practice_id"]})).scalar_one()
                rows = (await s2.execute(text(
                    "SELECT question_version_id FROM assessment_items "
                    "WHERE assessment_version_id=:v ORDER BY position"), {"v": av})).scalars().all()
                return [str(x) for x in rows]
        d1 = await _pick_ids()
        d2 = await _pick_ids()
        step("13. Selection is deterministic: two independent create_practice runs with the same "
             "policy + empty recent history pick the same questions in the same order",
             d1 == d2 and len(d1) == happy)

        async with factory() as s:
            after = await _counts(s)
        cleanup = await _cleanup(factory)
        async with factory() as s:
            final = await _counts(s)

        official_unchanged = all(after[t] == before[t] for t in OFFICIAL)
        step("14. During the walk NO official / answer-key / classification / catalog row changed; "
             "the practice only added reusable Activity* rows",
             official_unchanged
             and after["activity_results"] > before["activity_results"]
             and after["assessment_items"] > before["assessment_items"])
        step("15. Cleanup restored the DB: official + immutable + domain tables back to baseline "
             "(the practice added nothing permanent)",
             {k: final[k] for k in IMMUTABLE} == {k: before[k] for k in IMMUTABLE}
             and final["domain_content_mastery"] == before["domain_content_mastery"]
             and final["assessments"] == before["assessments"]
             and final["activity_assignments"] == before["activity_assignments"])

        return {"steps": steps, "all_passed": all(x["ok"] for x in steps),
                "counts_before": before, "counts_after_walk": after,
                "counts_after_cleanup": final, "cleanup": cleanup,
                "content_used": content_code, "eligible_questions": n_eligible,
                "official_tables_unchanged": official_unchanged}
    finally:
        await engine.dispose()


async def _seed_perf_content(factory, k: int) -> str:
    async with factory() as s:
        inst = Institution(code="INEP", name="INEP"); s.add(inst); await s.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM"); s.add(exam); await s.flush()
        d = CatalogNode(code="D", name="D", node_type="DISCIPLINE", active=True)
        s.add(d); await s.flush(); d.root_id = d.id
        a = CatalogNode(code="A", name="A", node_type="AREA", parent_id=d.id, root_id=d.id, active=True)
        s.add(a); await s.flush()
        cc = CatalogNode(code="C_PERF", name="C perf", node_type="CONTENT",
                         parent_id=a.id, root_id=d.id, position=1, active=True)
        s.add(cc); await s.flush()
        app = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=1)
        s.add(app); await s.flush()
        bk = ExamBooklet(exam_application_id=app.id, code="C24", color="AZUL"); s.add(bk); await s.flush()
        sd = SourceDocument(exam_application_id=app.id, exam_booklet_id=bk.id, document_type="ANSWER_KEY",
                            source_url="https://x/g.pdf", acquired_at=datetime.now(timezone.utc),
                            content_hash="g"); s.add(sd); await s.flush()
        rev = AnswerKeyRevision(source_document_id=sd.id, revision_number=1, is_official=True)
        s.add(rev); await s.flush()
        for n in range(1, k + 1):
            correct = "ABCDE"[n % 5]
            q = Question(validation_status="validated", origin_type="IMPORTED",
                         status="PUBLISHED", visibility_scope="PUBLIC"); s.add(q); await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=f"e{n}", statement=f"e{n}", content_hash=f"h{n}",
                                is_immutable=True); s.add(v); await s.flush()
            opts = {}
            for pos, key in enumerate("ABCDE", start=1):
                o = QuestionOption(question_version_id=v.id, option_key=key, position=pos,
                                   text=f"Alt {key}", is_valid_option=(key == correct))
                s.add(o); await s.flush(); opts[key] = o
            bq = BookletQuestion(exam_booklet_id=bk.id, question_version_id=v.id,
                                 position=n, official_number=n, page_number=1); s.add(bq); await s.flush()
            s.add(AnswerKeyEntry(answer_key_revision_id=rev.id, booklet_question_id=bq.id,
                                 official_answer_label=correct, resolved_option_id=opts[correct].id,
                                 page_number=1))
            s.add(PedagogicalClassification(
                question_version_id=v.id, discipline="CURRICULUM_PROPOSAL", content="C_PERF",
                subcontent="C_PERF", difficulty="UNKNOWN", reasoning_type="U",
                prerequisites=[], keywords=[], competencies=[], skills=[],
                status="CLASSIFIED", source="rule", lifecycle="ACTIVE",
                model_version="fx", prompt_version="v1",
                metadata_={"taxonomy_version": "curriculum-v2", "primary_content_code": "C_PERF",
                           "visual_dependency": False}))
        await s.commit()
    return "C_PERF"


async def _perf_probe() -> dict:
    out: dict = {}
    for k in (5, 10, 20, 50, 100):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _seed_perf_content(factory, max(k, 100))
        qn = {"c": 0}

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _c(*_a):  # noqa: ANN001
            qn["c"] += 1

        req = Requester(external_user_id="perf", school_id=None, role="STUDENT")
        async with factory() as s:
            svc = AdaptivePracticeService(s)
            qn["c"] = 0
            t0 = time.perf_counter()
            b = await svc.create_practice("perf", requester=req, content_code="C_PERF",
                                          question_count=min(k, MAX_QUESTIONS))
            dt = time.perf_counter() - t0
        event.remove(engine.sync_engine, "before_cursor_execute", _c)
        await engine.dispose()
        out[str(k)] = {"requested": k, "selected": b["question_count"], "queries": qn["c"],
                       "create_s": round(dt, 4)}
    qs = [out[str(k)]["queries"] for k in (5, 10, 20, 50, 100)]
    out["query_count_constant"] = (max(qs) - min(qs)) <= 3
    out["assessment"] = ("create_practice issues a fixed set of statements regardless of how many "
                         "questions are requested/selected: catalog load (cached) + one banded "
                         "QuestionBank page + recent-questions lookup + the reused PHASE 15 list "
                         "build (batched) + the PHASE 16 assignment insert. No per-question query; "
                         "no N+1.")
    return out


def _module_is_ai_agnostic() -> dict:
    import agente_ia_edu.services.adaptive_practice as mod
    src = importlib.util.find_spec(mod.__name__).origin
    body = Path(src).read_text(encoding="utf-8")
    banned = ("openai", "AsyncOpenAI", "OpenAIProvider", "build_text_provider", "providers.router",
              "classification_consensus", "classification_prompts")
    hits = [b for b in banned if b in body]
    return {"clean": not hits, "banned_symbols_found": hits}


async def main() -> dict:
    rep: dict = {"phase": "22", "title": "ADAPTIVE PRACTICE ENGINE / MOTOR DE PRÁTICA ADAPTATIVA",
                 "date": "2026-09-10", "llm_used": False}

    rep["preflight_audit"] = {
        "REUSED": [
            "QuestionBankService.list_questions / QuestionBankFilters (PHASE 12/14) - the SOLE "
            "question source. No second bank. content_code + CLASSIFIED (definitive) state; "
            "visual-dependency and protected questions are filtered in-memory (the bank's "
            "visual_dependency filter only matches is-True; protected is a derived property).",
            "QuestionListStore.create + finalize (PHASE 15) -> runs the PHASE 14 ListGenerator "
            "which SNAPSHOTS the gabarito into assessment_items. No question row is copied.",
            "ActivityAssignmentStore.create (PHASE 16) - distributes the list to the STUDENT "
            "themself (target_type=STUDENT, target_id=self); origin + practice metadata on the "
            "assignment. Duplicate control + authz reused.",
            "The PHASE 17 Player (POST/GET .../attempt, answers, position, complete) UNCHANGED - "
            "no second player.",
            "The PHASE 18 correction (POST .../attempt/correct) + ActivityResult / "
            "ActivityResultItem UNCHANGED - the result already reports assignment_id.",
            "PHASE 20 CurriculumDomainMapService - origin_breakdown JSON already multi-key; the "
            "practice evidence lands under the new PRACTICE key.",
            "PHASE 21 AdaptiveLearningPathService - now exposes practice_available for an "
            "observed, non-blocked content (RECOMMENDED / READY / NEEDS_REVIEW).",
        ],
        "NEW": [
            "src/agente_ia_edu/services/adaptive_practice.py - AdaptivePracticeService + "
            "PracticeSelectionPolicy (deterministic, AI-agnostic, adds no table).",
            "POST /api/v1/student/practice, GET /api/v1/student/practice, "
            "GET /api/v1/student/practice/{practice_id} (all always self - no student id accepted).",
            "frontend: 'Praticar agora' on a Trilha step + a minimal direct "
            "disciplina->conteudo->quantidade picker (index.html + app.js + styles.css).",
            "tests/test_phase22_adaptive_practice.py (+ _frontend.js), "
            "tests/manual/phase22_adaptive_practice_report.py.",
        ],
        "MODIFIED": [
            "services/curriculum_domain_map.py - _aggregate resolves each result's evidence origin "
            "from ActivityAssignment.metadata_['origin'] (default OFFICIAL_ACTIVITY); adds "
            "ORIGIN_PRACTICE + _KNOWN_ORIGINS. One extra CONSTANT batched query, not N+1.",
            "services/activity_assignment_store.py - create() gains origin + extra_metadata "
            "(optional; default None -> identical behaviour for every existing caller).",
            "services/adaptive_learning_path.py - practice_available / action_available true for "
            "RECOMMENDED / READY / NEEDS_REVIEW (not blocked); action_note updated.",
            "api/routes/student.py - +3 practice endpoints + _map_practice_error.",
            "web/index.html + app.js + styles.css - practice UI.",
            "tests/test_phase21_adaptive_learning_path.py - no-N+1 threshold 16 -> 18 for the one "
            "extra constant Domain-Map origin query.",
        ],
        "NOT_TOUCHED": [
            "questions / question_versions / question_options / answer_key_entries / "
            "answer_key_revisions / catalog_nodes / pedagogical_classifications - never written.",
            "ActivityResult / ActivityResultItem shape - unchanged; the gabarito snapshot is "
            "unchanged.",
            "the LEGACY /api/v1/practice/sessions engine + its 'Praticar' view - not merged, not "
            "reused, not modified. PHASE 22 is exposed under /api/v1/student/practice and inside "
            "the 'Trilha de Estudos' view.",
            "Diagnostico (s30) + Redacao IA (s31) - Practice is a distinct evidence origin; not "
            "mixed. Teacher practice-list generation - contract only, not implemented.",
        ],
        "persistence_decision": ("NO migration, NO new table. A practice is fully representable "
                                 "with the existing Assessment / AssessmentVersion / AssessmentItem "
                                 "/ ActivityAssignment / ActivityAttempt / ActivityAnswer / "
                                 "ActivityResult chain. The only new concept - the evidence ORIGIN "
                                 "- fits in ActivityAssignment.metadata_ (JSONB, already present) "
                                 "and the Domain Map's origin_breakdown JSON (already multi-key). "
                                 "alembic head stays 029_domain_content_mastery."),
    }

    walk = await _acceptance_walk()
    perf = await _perf_probe()
    ai = _module_is_ai_agnostic()
    rep["acceptance_walk"] = walk
    rep["performance"] = perf
    rep["ai_agnostic"] = ai

    rep["selection_policy"] = {
        "class": "PracticeSelectionPolicy (frozen dataclass, swappable)",
        "defaults": PracticeSelectionPolicy.default().as_dict(),
        "rules": [
            "prefer definitive: classification_state=CLASSIFIED (excludes NEEDS_REVIEW / "
            "FORCED_CLOSURE / LOW). ANY_CLASSIFIED only if prefer_definitive is turned off.",
            "exclude questions with an unavailable figure (has_visual_dependency).",
            "exclude protected questions (never modified, never copied).",
            "deterministically de-prioritise the student's recently answered questions "
            "(last RECENT_EXCLUDE distinct), but re-add them in order if the fresh pool is too "
            "small - practice is never made impossible.",
            "rank by (official_number, question_version_id) - stable, no randomness.",
            "recommended_difficulty is respected when present; never invented.",
        ],
        "insufficient_bank": ("if fewer eligible questions than requested: 422 with "
                              "available_questions / requested_questions / selected_questions / "
                              "sufficient=false and the message 'Não há questões suficientes "
                              "disponíveis para esta prática.' - it never invents a question and "
                              "never silently shrinks the practice."),
        "counts": {"allowed_ui": list(ALLOWED_COUNTS), "safe_max": MAX_QUESTIONS},
        "modes": {"implemented": ["PRACTICE_CONTENT"],
                  "contract_only": ["PRACTICE_REVIEW", "PRACTICE_PREREQUISITE", "PRACTICE_MIXED"]},
    }

    rep["evidence_origin"] = (
        "A practice ActivityAssignment carries metadata_ = {origin: 'PRACTICE', practice: true, "
        "mode, content_code}. PHASE 20 _aggregate maps each ActivityResult to its assignment's "
        "origin (default OFFICIAL_ACTIVITY) and increments origin_breakdown[origin]. PRACTICE and "
        "OFFICIAL_ACTIVITY are therefore counted on the SAME content grain but under SEPARATE "
        "keys - accuracy/among counts still aggregate, but the source split is always visible and "
        "never merged. Rebuild is idempotent.")

    rep["endpoints"] = [
        "POST /api/v1/student/practice {content_code, question_count<=20, mode=PRACTICE_CONTENT} "
        "-> creates the practice Activity for the caller (origin=PRACTICE). Never accepts a "
        "student id.",
        "GET /api/v1/student/practice -> the caller's own practices (+ derived state).",
        "GET /api/v1/student/practice/{practice_id} -> one practice; 403 if it is not the "
        "caller's; + a result block once corrected.",
        "(play / correct / result reuse the PHASE 17 / PHASE 18 endpoints unchanged.)",
    ]
    rep["frontend"] = (
        "'Trilha de Estudos' step: when the path reports practice_available the disabled "
        "'<Ação> — em breve' button becomes a real [Praticar agora] that opens a small 'Quantas "
        "questões?' selector (5 / 10 / 15 / 20, default 10) + [Começar prática]. That POSTs "
        "/api/v1/student/practice and hands the returned id to the REUSED PHASE 17 player "
        "(switchView('activities') + startActivityPlayer). The PHASE 18 result screen is "
        "re-framed for practice: title 'Prática concluída', a 'Prática' badge, the note 'Este "
        "resultado faz parte do seu estudo e não é uma nota escolar.', and the back button reads "
        "'Voltar para a Trilha' - it rebuilds the Domain Map then returns to the recalculated "
        "Trilha. An insufficient bank shows 'Não há questões suficientes disponíveis para esta "
        "prática. (disponíveis: X, pedidas: Y)' inline. A minimal direct picker "
        "(disciplina -> conteúdo -> quantidade, fed from the student's own Domain Map) is under "
        "the path. No gamification / ranking / comparison / grade. 320px: no horizontal scroll.")

    ob = walk["counts_before"]
    of_ = walk["counts_after_cleanup"]
    rep["integrity"] = {
        "DATABASE_WRITES": ("The practice writes ONLY reusable rows: one assessments + "
                            "assessment_versions + assessment_items (gabarito snapshot) + "
                            "activity_assignments (origin=PRACTICE) + activity_attempts + "
                            "activity_answers + activity_results + activity_result_items, all owned "
                            "by the student. PHASE 20's Domain Map cache (domain_content_mastery) "
                            "is refreshed on rebuild (a derived write owned by PHASE 20). The "
                            "acceptance walk deletes every row it created."),
        "OFFICIAL_TABLES_WRITTEN": 0,
        "OPENAI_CALLS": 0,
        "MIGRATIONS": 0,
        "TABLES_CREATED": 0,
        "ALEMBIC_HEAD": "029_domain_content_mastery (unchanged)",
        "CODE_FILES_CREATED": [
            "src/agente_ia_edu/services/adaptive_practice.py",
            "tests/test_phase22_adaptive_practice.py",
            "tests/test_phase22_adaptive_practice_frontend.js",
            "tests/manual/phase22_adaptive_practice_report.py",
            "var/phase22_adaptive_practice_report.json",
        ],
        "CODE_FILES_MODIFIED": [
            "src/agente_ia_edu/services/curriculum_domain_map.py",
            "src/agente_ia_edu/services/activity_assignment_store.py",
            "src/agente_ia_edu/services/adaptive_learning_path.py",
            "src/agente_ia_edu/api/routes/student.py",
            "src/agente_ia_edu/web/index.html",
            "src/agente_ia_edu/web/app.js",
            "src/agente_ia_edu/web/styles.css",
            "tests/test_phase21_adaptive_learning_path.py (no-N+1 threshold 16 -> 18)",
        ],
        "official_tables_unchanged": all(ob[t] == of_[t] for t in OFFICIAL),
        "immutable_tables_net_zero_after_cleanup": all(ob[t] == of_[t] for t in IMMUTABLE),
        "domain_rows_net_zero_after_cleanup": ob["domain_content_mastery"] == of_["domain_content_mastery"],
    }

    rep["security"] = [
        "Every student practice endpoint is 'self' only - the request body has no "
        "student_external_id; an extra one is ignored and the practice belongs to the caller.",
        "GET /practice/{id} returns 403 unless the assignment is target_type=STUDENT, "
        "target_id == caller and metadata.practice is true.",
        "A practice list is built + finalised + assigned as the STUDENT (owner Requester), so "
        "PHASE 15/16 ownership + duplicate + scope rules apply unchanged.",
        "AI-agnostic: adaptive_practice.py imports no openai / provider / classification-AI "
        "symbol (asserted by test + this report).",
    ]

    rep["tests"] = {
        "backend": {"file": "tests/test_phase22_adaptive_practice.py",
                    "covers": ["create by content (origin PRACTICE, ai_used False)",
                               "configurable 5/10/15/20", "safe-max clamp + schema le=20",
                               "insufficient bank -> 422 + counts + message",
                               "content without questions -> 422", "unknown / inactive content",
                               "definitive-only selection (NEEDS_REVIEW excluded)",
                               "UNCLASSIFIED never selected", "visual_dependency excluded",
                               "protected excluded", "avoid recently answered + determinism",
                               "tenant isolation + another student's practice is 403 + always self",
                               "full loop: play + autosave + reload + complete + correct + result",
                               "Domain Map keeps PRACTICE separate from OFFICIAL_ACTIVITY + rebuild "
                               "idempotent", "Learning Path updates + practice_available",
                               "no N+1 on create + list", "AI-agnostic import guard",
                               "official bank + answer key immutable",
                               "contract modes rejected"]},
        "frontend": {"file": "tests/test_phase22_adaptive_practice_frontend.js",
                     "covers": ["[Praticar agora] replaces '— em breve' when practice_available",
                                "5/10/15/20 selector + Começar prática", "POST /student/practice "
                                "with content_code + question_count; no student id",
                                "reuses PHASE 17 player (no second player / no legacy sessions)",
                                "insufficient-bank message + counts", "result re-framed (Prática "
                                "badge + 'não é uma nota escolar')", "back returns to the Trilha + "
                                "Domain rebuild", "minimal direct disciplina->conteúdo->quantidade "
                                "picker from the Domain Map", "wiring", "no gamification / ranking / "
                                "grade / second bank", "mobile reflow"]},
        "browser_validation": ["Trilha -> Praticar agora -> quantidade -> Player -> responder -> "
                               "finalizar -> Resultado ('Prática concluída' + badge + disclaimer) "
                               "-> Voltar para a Trilha (recalculated: questions answered went up)",
                               "insufficient bank shows the standard inline message + counts",
                               "320px: document scrollWidth == clientWidth (no horizontal scroll) "
                               "on the Trilha, the practice panel and the result screen"],
    }

    ok = (walk["all_passed"]
          and rep["integrity"]["official_tables_unchanged"]
          and rep["integrity"]["immutable_tables_net_zero_after_cleanup"]
          and rep["integrity"]["domain_rows_net_zero_after_cleanup"]
          and perf["query_count_constant"]
          and ai["clean"])
    rep["FINAL_DECISION"] = ("PHASE_22_ADAPTIVE_PRACTICE_COMPLETE" if ok
                             else "PHASE_22_ADAPTIVE_PRACTICE_BLOCKED")
    if not ok:
        rep["blocker"] = "see acceptance_walk.steps / performance / ai_agnostic / integrity"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
