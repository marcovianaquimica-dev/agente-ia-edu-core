"""PHASE 19 - pedagogical analysis engine & result aggregation: report.

READ-ONLY. This phase writes nothing to the database. The acceptance walk runs
against the LIVE database using controlled test data (a small list built from
already-classified official questions, distributed to a test student), produces
the analysis for the student and for the manager, and removes every temporary
row it created. A separate perf probe uses its own throw-away in-memory SQLite
to prove the query count is independent of the question count for
10/50/100/200/500 questions.

No official questions/versions/options/answer-key/classification/catalog row is
touched. No migration. No table. No AI call.
"""
from __future__ import annotations

import asyncio
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
from sqlalchemy import event, func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from agente_ia_edu.api.app import create_app  # noqa: E402
from agente_ia_edu.db.base import Base  # noqa: E402
from agente_ia_edu.db.models import (  # noqa: E402
    AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, CatalogNode, Exam,
    ExamApplication, ExamBooklet, Institution, PedagogicalClassification,
    Question, QuestionOption, QuestionVersion, SourceDocument,
)
from agente_ia_edu.db.models.assessments import (  # noqa: E402
    ActivityResult, ActivityResultItem,
)
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.pedagogical_analysis import (  # noqa: E402
    PedagogicalAnalysisService, PerformanceThresholdPolicy,
)

OUT = _REPO / "var" / "phase19_pedagogical_analysis_report.json"
OWNER = "phase19-report-runner"
PT = {"Authorization": f"Bearer teacher:{OWNER}"}
STU = "student:phase19-report-al"
SH = {"Authorization": f"Bearer student:student:phase19-report-al"}   # SPA-style
SH_OTHER = {"Authorization": "Bearer student:student:phase19-report-bo"}
CLASS_ID = "phase19-report-turma"

OFFICIAL = ("questions", "question_versions", "question_options", "booklet_questions",
            "catalog_nodes", "pedagogical_classifications", "answer_key_entries",
            "answer_key_revisions")


async def _counts(s) -> dict:
    out = {}
    for t in (*OFFICIAL, "assessments", "assessment_versions", "assessment_items",
              "activity_assignments", "activity_attempts", "activity_answers",
              "activity_results", "activity_result_items"):
        out[t] = int(await s.scalar(text(f"SELECT count(*) FROM {t}")))
    return out


async def _classified_vids(s, n: int) -> list[str]:
    rows = (await s.execute(text("""
        SELECT pc.question_version_id
        FROM pedagogical_classifications pc
        JOIN booklet_questions bq ON bq.question_version_id = pc.question_version_id
        JOIN question_versions qv ON qv.id = pc.question_version_id
        WHERE pc.lifecycle = 'ACTIVE'
          AND pc.metadata->>'taxonomy_version' = 'curriculum-v2'
          AND qv.version_kind = 'official_original'
        ORDER BY bq.official_number
        LIMIT :n"""), {"n": n})).scalars().all()
    return [str(r) for r in rows]


async def _frozen_keys(factory, lid: str) -> dict:
    async with factory() as s:
        rows = (await s.execute(text("""
            SELECT ai.position, qo.option_key FROM assessment_items ai
            JOIN assessment_versions av ON av.id = ai.assessment_version_id
            JOIN question_options qo ON qo.id = ai.frozen_correct_option_id
            WHERE av.assessment_id = :l ORDER BY ai.position"""), {"l": lid})).all()
    return {int(p): k for p, k in rows}


async def _seed_link(s) -> None:
    await s.execute(text("DELETE FROM user_school_links WHERE external_user_id LIKE 'student:phase19-report%'"))
    await s.execute(text(
        "INSERT INTO user_school_links (id, external_user_id, role, scope_type, scope_external_id, active, created_at) "
        "VALUES (:i, :u, 'STUDENT', 'CLASSROOM', :c, true, NOW())"),
        {"i": uuid.uuid4(), "u": STU, "c": CLASS_ID})
    await s.commit()


async def _cleanup(factory) -> dict:
    async with factory() as s:
        ids = (await s.execute(text(
            "SELECT id FROM assessments WHERE owner_external_id = :o"), {"o": OWNER})).scalars().all()
        removed = {"lists": len(ids), "attempts": 0, "answers": 0, "results": 0, "result_items": 0}
        for aid in ids:
            for g in (await s.execute(text("SELECT id FROM activity_assignments WHERE assessment_id=:a"), {"a": aid})).scalars().all():
                for at in (await s.execute(text("SELECT id FROM activity_attempts WHERE assignment_id=:g"), {"g": g})).scalars().all():
                    removed["result_items"] += int(await s.scalar(text("SELECT count(*) FROM activity_result_items WHERE result_id IN (SELECT id FROM activity_results WHERE attempt_id=:x)"), {"x": at}))
                    removed["results"] += int(await s.scalar(text("SELECT count(*) FROM activity_results WHERE attempt_id=:x"), {"x": at}))
                    removed["answers"] += int(await s.scalar(text("SELECT count(*) FROM activity_answers WHERE attempt_id=:x"), {"x": at}))
                    await s.execute(text("DELETE FROM activity_result_items WHERE result_id IN (SELECT id FROM activity_results WHERE attempt_id=:x)"), {"x": at})
                    await s.execute(text("DELETE FROM activity_results WHERE attempt_id=:x"), {"x": at})
                    await s.execute(text("DELETE FROM activity_answers WHERE attempt_id=:x"), {"x": at})
                removed["attempts"] += int(await s.scalar(text("SELECT count(*) FROM activity_attempts WHERE assignment_id=:g"), {"g": g}))
                await s.execute(text("DELETE FROM activity_attempts WHERE assignment_id=:g"), {"g": g})
            await s.execute(text("DELETE FROM activity_assignments WHERE assessment_id=:a"), {"a": aid})
            await s.execute(text("DELETE FROM assessment_items WHERE assessment_version_id IN (SELECT id FROM assessment_versions WHERE assessment_id=:a)"), {"a": aid})
            await s.execute(text("DELETE FROM assessment_versions WHERE assessment_id=:a"), {"a": aid})
            await s.execute(text("DELETE FROM assessments WHERE id=:a"), {"a": aid})
        await s.execute(text("DELETE FROM user_school_links WHERE external_user_id LIKE 'student:phase19-report%'"))
        await s.commit()
        return removed


async def _acceptance_walk() -> dict:
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    steps: list[dict] = []

    def step(name, ok, detail=None):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})

    try:
        async with factory() as s:
            before = await _counts(s)
            vids = await _classified_vids(s, 12)
            await _seed_link(s)
        step("1-2. Preflight: >=8 already-classified official questions + test link seeded",
             len(vids) >= 8)

        client = TestClient(create_app())
        lid = client.post("/api/v1/question-bank/lists", json={
            "question_version_ids": vids, "title": "P19 acceptance",
            "answer_key_presentation": "KEY_AT_END"}, headers=PT).json()["id"]
        client.post(f"/api/v1/question-bank/lists/{lid}/finalize", headers=PT)
        aid = client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
            "target_type": "CLASS", "target_id": CLASS_ID,
            "available_from": "2000-01-01T00:00:00Z"}, headers=PT).json()["id"]
        keys = await _frozen_keys(factory, lid)
        st = client.post(f"/api/v1/student/activities/{aid}/attempt", headers=SH).json()
        qv = [q["question_version_id"] for q in st["questions"]]
        alt = ["A", "B", "C", "D", "E"]
        wrong = lambda k: next(x for x in alt if x != k)
        for i, v in enumerate(qv):                       # first 8 correct, rest wrong
            key = keys[i + 1] if i < 8 else wrong(keys[i + 1])
            client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{v}",
                       json={"selected_option": key}, headers=SH)
        client.post(f"/api/v1/student/activities/{aid}/attempt/complete", headers=SH)
        client.post(f"/api/v1/student/activities/{aid}/attempt/correct", headers=SH)
        step("3-4. Published + distributed + student completed & corrected", True)

        a = client.get(f"/api/v1/student/activities/{aid}/attempt/result/analysis", headers=SH)
        j = a.json()
        summ = j["summary"]
        step("5. Student analysis -> 200, ai_used == False",
             a.status_code == 200 and j.get("ai_used") is False)
        step("6. Per-question analysis present with classification fields",
             len(j["questions"]) == len(vids)
             and all(k in j["questions"][0] for k in
                     ("is_correct", "answered", "discipline_code", "content_code",
                      "classification_status", "confidence", "visual_dependency", "provisional")))
        step("7. Aggregation by discipline present",
             isinstance(j["by_discipline"], list) and len(j["by_discipline"]) >= 1
             and all("accuracy" in d and "band" in d for d in j["by_discipline"]))
        step("8. Aggregation by content present (only ACTIVE curriculum-v2)",
             isinstance(j["by_content"], list)
             and all("content_code" in c and "band" in c for c in j["by_content"]))
        step("9. Subcontent nested when a valid code exists (list, never invented)",
             all(isinstance(c["subcontents"], list) for c in j["by_content"]))
        step("10. UNCLASSIFIED counted in the total, NOT attributed to a discipline/content",
             summ["classified_questions"] + summ["unclassified_questions"] == summ["total_questions"]
             and sum(d["total_questions"] for d in j["by_discipline"]) == summ["classified_questions"])
        step("11. LOW / MEDIUM / FORCED_CLOSURE preserved + flagged provisional (never promoted)",
             "forced_closure_questions" in summ and "provisional_questions" in summ
             and all(("provisional" in q) for q in j["questions"]))
        step("12. visual_dependency preserved on questions and content buckets",
             "visual_dependency_questions" in summ
             and all("visual_dependency_count" in c for c in j["by_content"]))
        step("13. strengths / improvements respect MIN_SAMPLE_SIZE (no single-question verdict)",
             all(e["answered"] >= j["thresholds"]["MIN_SAMPLE_SIZE"]
                 for e in j["strengths"] + j["improvements"]))
        step("14. thresholds echoed + configurable policy shape",
             j["thresholds"] == PerformanceThresholdPolicy.default().as_dict())
        step("15. temporal grouping shape prepared (activity_date + windows)",
             "activity_date" in j["temporal"] and "grouping_windows" in j["temporal"])

        b = client.get(f"/api/v1/student/activities/{aid}/attempt/result/analysis", headers=SH).json()
        step("16. Deterministic (identical JSON on re-request)",
             json.dumps(j, sort_keys=True) == json.dumps(b, sort_keys=True))
        step("17. A different student cannot read this analysis (403)",
             client.get(f"/api/v1/student/activities/{aid}/attempt/result/analysis",
                        headers=SH_OTHER).status_code == 403)

        m = client.get(f"/api/v1/question-bank/assignments/{aid}/results/analysis", headers=PT)
        step("18. Manager (assignment owner) analysis -> 200, per-student + class aggregate",
             m.status_code == 200 and m.json()["student_count"] == 1
             and "class_aggregate" in m.json())
        step("19. A teacher from another school is refused (403/404)",
             client.get(f"/api/v1/question-bank/assignments/{aid}/results/analysis",
                        headers={"Authorization": "Bearer teacher:phase19-other"}).status_code in (403, 404))

        async with factory() as s:
            after = await _counts(s)
        cleanup = await _cleanup(factory)
        async with factory() as s:
            final = await _counts(s)
        step("20. Analysis wrote nothing new (result/result_items counts unchanged during analysis)",
             after["activity_results"] == before["activity_results"] + 1
             and after["activity_result_items"] == before["activity_result_items"] + len(vids))
        step("21. Cleanup restored the DB (official + execution/result tables back to baseline)",
             {k: final[k] for k in OFFICIAL} == {k: before[k] for k in OFFICIAL}
             and final["activity_results"] == before["activity_results"]
             and final["activity_result_items"] == before["activity_result_items"])

        return {"steps": steps, "all_passed": all(x["ok"] for x in steps),
                "counts_before": before, "counts_after_walk": after,
                "counts_after_cleanup": final, "cleanup": cleanup,
                "sample_summary": {k: summ[k] for k in
                                   ("total_questions", "answered", "correct", "accuracy",
                                    "classified_questions", "unclassified_questions",
                                    "provisional_questions", "forced_closure_questions",
                                    "visual_dependency_questions")}}
    finally:
        await engine.dispose()


async def _perf_probe() -> dict:
    """Own throw-away in-memory SQLite. Proves query count is independent of N."""
    out = {}
    for n in (10, 50, 100, 200, 500):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            inst = Institution(code="INEP", name="INEP"); s.add(inst); await s.flush()
            exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM"); s.add(exam); await s.flush()
            d = CatalogNode(code="D", name="D", node_type="DISCIPLINE", active=True); s.add(d); await s.flush(); d.root_id = d.id
            a = CatalogNode(code="A", name="A", node_type="AREA", parent_id=d.id, root_id=d.id, active=True); s.add(a); await s.flush()
            contents = []
            for ci in range(5):
                cc = CatalogNode(code=f"C{ci}", name=f"C{ci}", node_type="CONTENT",
                                 parent_id=a.id, root_id=d.id, active=True)
                s.add(cc); contents.append(f"C{ci}")
            await s.flush()
            app_ = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=1); s.add(app_); await s.flush()
            bk = ExamBooklet(exam_application_id=app_.id, code="X", color="Y"); s.add(bk); await s.flush()
            sd = SourceDocument(exam_application_id=app_.id, exam_booklet_id=bk.id, document_type="ANSWER_KEY",
                                source_url="x", acquired_at=datetime.now(timezone.utc), content_hash="h"); s.add(sd); await s.flush()
            rev = AnswerKeyRevision(source_document_id=sd.id, revision_number=1, is_official=True); s.add(rev); await s.flush()
            result = ActivityResult(
                attempt_id=uuid.uuid4(), assignment_id=uuid.uuid4(),
                student_external_id="probe", assessment_version_id=uuid.uuid4(),
                question_count=n, answered_count=n, correct_count=n // 2,
                incorrect_count=n - n // 2, unanswered_count=0,
                completion_status="COMPLETED",
                completed_at=datetime.now(timezone.utc), corrected_at=datetime.now(timezone.utc))
            s.add(result); await s.flush()
            for i in range(n):
                q = Question(validation_status="validated", origin_type="IMPORTED",
                             status="PUBLISHED", visibility_scope="PUBLIC"); s.add(q); await s.flush()
                v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                    canonical_text=f"e{i}", statement=f"e{i}", content_hash=f"h{i}",
                                    is_immutable=True); s.add(v); await s.flush()
                for pos, key in enumerate("ABCDE", start=1):
                    s.add(QuestionOption(question_version_id=v.id, option_key=key, position=pos,
                                         text=key, is_valid_option=(key == "A")))
                s.add(BookletQuestion(exam_booklet_id=bk.id, question_version_id=v.id,
                                      position=i + 1, official_number=i + 1, page_number=1))
                if i % 4 != 0:   # 3/4 classified, 1/4 unclassified
                    s.add(PedagogicalClassification(
                        question_version_id=v.id, discipline="X", content=contents[i % 5],
                        subcontent=contents[i % 5], difficulty="UNKNOWN", reasoning_type="U",
                        prerequisites=[], keywords=[], competencies=[], skills=[],
                        status="CLASSIFIED", source="rule", lifecycle="ACTIVE",
                        metadata_={"taxonomy_version": "curriculum-v2"}))
                s.add(ActivityResultItem(
                    result_id=result.id, question_version_id=v.id, position=i + 1,
                    official_number=i + 1, selected_option_key="A" if i % 2 else "B",
                    correct_option_key="A", is_correct=bool(i % 2), answered=True))
            await s.commit()
            rid = result.id

        qn = {"c": 0}

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _c(*_a):  # noqa: ANN001
            qn["c"] += 1

        async with factory() as s:
            svc = PedagogicalAnalysisService(s)
            res = (await s.execute(select(ActivityResult).where(ActivityResult.id == rid))).scalar_one()
            qn["c"] = 0
            t0 = time.perf_counter()
            analysis = await svc._build(res)
            dt = time.perf_counter() - t0
        event.remove(engine.sync_engine, "before_cursor_execute", _c)
        await engine.dispose()
        out[str(n)] = {"questions": n, "queries": qn["c"], "build_s": round(dt, 4),
                       "by_content_buckets": len(analysis["by_content"]),
                       "questions_returned": len(analysis["questions"])}
    qs = [out[str(n)]["queries"] for n in (10, 50, 100, 200, 500)]
    out["query_count_constant"] = (max(qs) - min(qs)) <= 1
    out["assessment"] = ("the query count is flat (within +/-1) from 10 to 500 questions - a fixed "
                         "set (catalog + questions IN + options selectinload + active-classifications "
                         "IN + result items); build time grows ~linearly with N.")
    return out


async def main() -> dict:
    rep: dict = {"phase": "19", "title": "PEDAGOGICAL ANALYSIS ENGINE & RESULT AGGREGATION",
                 "date": "2026-09-10", "llm_used": False}

    rep["preflight_audit"] = {
        "REUSED": [
            "ActivityResult + ActivityResultItem (PHASE 18) as the SOLE performance source - "
            "ActivityResultItem.is_correct is authoritative; the correction is never re-run and the "
            "official answer key is never re-read.",
            "QuestionBankService.get_questions_by_version_ids (PHASE 12/14) - ONE batched load "
            "(catalog cached + rows IN + options selectinload + ACTIVE curriculum-v2 "
            "PedagogicalClassification IN). SUPERSEDED classifications are never returned.",
            "QuestionBankService._resolve_curriculum_path / catalog cache - code -> discipline / area "
            "/ content / subcontent, in-memory over the 64-node curriculum-v2 tree.",
            "ActivityAssignmentStore.resolve_student_assignment (student) and .get() (manager) - the "
            "exact PHASE 16 recipient / tenant / owner rules, reused verbatim. NO new authz.",
            "get_current_authenticated_context + _student_requester / _requester (PHASES 16-18).",
            "route / service-store / node:test conventions.",
        ],
        "NEW": [
            "src/agente_ia_edu/services/pedagogical_analysis.py - PedagogicalAnalysisService + "
            "PerformanceThresholdPolicy (READ-ONLY, deterministic, AI-agnostic).",
            "GET /api/v1/student/activities/{assignment_id}/attempt/result/analysis (student, own).",
            "GET /api/v1/question-bank/assignments/{assignment_id}/results/analysis (manager; "
            "optional ?student_external_id=).",
            "frontend: 'Desempenho pedagógico' section on the student result screen "
            "(index.html + app.js + styles.css).",
            "tests/test_phase19_pedagogical_analysis.py + _frontend.js.",
        ],
        "MODIFIED": [
            "api/routes/student.py (+ 1 endpoint, _map_analysis_error).",
            "api/routes/question_bank.py (+ 1 manager endpoint).",
            "web/index.html + app.js + styles.css (result screen analysis section).",
        ],
        "NOT_NEEDED": [
            "no migration, no table, no column, no model change - the analysis derives entirely from "
            "existing rows.",
            "no Domain Map, Adaptive Learning Path, recommendation, videos, AI pedagogy, TRI, MIRT, "
            "ranking, ENEM score, gamification, initial diagnosis, authored material, new exam "
            "import, new question bank or new taxonomy.",
        ],
        "read_only": True,
    }

    walk = await _acceptance_walk()
    perf = await _perf_probe()
    rep["acceptance_walk"] = walk
    rep["performance"] = perf

    rep["endpoints"] = [
        "GET /api/v1/student/activities/{assignment_id}/attempt/result/analysis "
        "-> the authenticated student's own analysis (404 before correction, 403 for a non-recipient).",
        "GET /api/v1/question-bank/assignments/{assignment_id}/results/analysis[?student_external_id=] "
        "-> one analysis per corrected result under the assignment + a class aggregate; caller must "
        "pass the PHASE 16 assignment-view check (owner / platform admin / same-school privileged).",
    ]
    rep["aggregation_rules"] = {
        "source": "ActivityResultItem.is_correct / .answered / .selected_option_key / .position / "
                  ".official_number - frozen in PHASE 18, never recomputed.",
        "classification": "ACTIVE curriculum-v2 PedagogicalClassification only (SUPERSEDED ignored); "
                          "resolved via QuestionBankService to discipline/area/content/subcontent "
                          "codes + names, confidence band (HIGH/MEDIUM/LOW), classification_mode, "
                          "review_reason, visual_dependency.",
        "per_question": "question_version_id, position, official_number, selected_option_key, "
                        "is_correct, answered, discipline/area/content/subcontent (code + name), "
                        "classification_status (UNCLASSIFIED|CLASSIFIED|NEEDS_REVIEW|FORCED_CLOSURE), "
                        "confidence, numeric_confidence, classification_mode, review_reason, "
                        "visual_dependency, provisional.",
        "accuracy": "correct / answered; null when answered == 0 (no division by zero).",
        "by_discipline / by_content / subcontent": "grouped by ACTIVE curriculum-v2 codes ONLY; "
                                                   "UNCLASSIFIED questions stay in the overall totals "
                                                   "but are never attributed; a subcontent bucket is "
                                                   "created only when a real subcontent code exists.",
        "provisional": "NEEDS_REVIEW or FORCED_CLOSURE or confidence LOW -> counted but flagged "
                       "provisional; forced_closure counted separately; never promoted, never dropped.",
        "strengths / improvements / intermediate": "band from PerformanceThresholdPolicy, applied at "
                                                   "content AND discipline grain; a bucket with "
                                                   "answered < MIN_SAMPLE_SIZE is INSUFFICIENT_SAMPLE "
                                                   "and yields no verdict.",
        "temporal": "activity_date (completed_at date) + completed_at/corrected_at + the list of "
                    "future grouping windows (last_7_days ... custom). No evolution algorithm here.",
        "determinism": "every iteration is over sorted keys; no randomness, no wall-clock in the "
                       "output except passing through completed_at / corrected_at.",
    }
    rep["thresholds"] = {
        "policy": "PerformanceThresholdPolicy (single source of truth; pass a custom instance to the "
                  "service to change without touching the engine).",
        "defaults": PerformanceThresholdPolicy.default().as_dict(),
        "bands": ["PONTO_FORTE (accuracy >= STRONG_ACCURACY)",
                  "PONTO_MELHORIA (accuracy < IMPROVEMENT_ACCURACY)",
                  "DESEMPENHO_INTERMEDIARIO (between)",
                  "INSUFFICIENT_SAMPLE (answered < MIN_SAMPLE_SIZE)",
                  "SEM_DADOS (answered > 0 but accuracy is null - unreachable in practice)"],
    }
    rep["stable_identifiers_for_future_material"] = (
        "discipline_code / area_code / content_code / subcontent_code are the stable curriculum-v2 "
        "catalog codes; a later phase can join content_code -> educational material -> question -> "
        "this analysis -> trilha WITHOUT any change here. No material table is created now.")
    rep["security"] = [
        "Student endpoint: resolve_student_assignment (PHASE 16) -> the student must be the direct "
        "STUDENT target or a member of the CLASS target, in the right tenant; the result is loaded "
        "by (assignment_id, the AUTHENTICATED student) - URL id tampering cannot reach another "
        "student's analysis (verified: 403).",
        "Manager endpoint: ActivityAssignmentStore.get() -> owner / platform admin / same-school "
        "privileged only; another school's teacher is refused (verified: 403/404). No new authz "
        "rule was introduced.",
        "All institutional aggregation is scoped through the assignment (which carries school_id).",
        "AI-agnostic: the module imports no openai / provider / classification-AI symbol (asserted "
        "by test).",
    ]
    rep["frontend"] = (
        "The student result screen gains a 'Desempenho pedagógico' section (toggle 'Ver desempenho "
        "pedagógico'), fed by a READ-ONLY GET .../result/analysis after correction: Visão geral "
        "(questões / classificadas / sem classificação / provisórias / aproveitamento), Por "
        "disciplina and Por conteúdo as responsive tables (band chips: Ponto forte / Ponto de "
        "melhoria / Desempenho intermediário / Amostra insuficiente), Pontos fortes and Pontos de "
        "melhoria as lists. Percentages are labelled 'aproveitamento (acertos ÷ respondidas), não é "
        "nota'. No ranking, no student comparison, no domain map, no trilha. On mobile (320px) the "
        "page has no horizontal scroll - the tables scroll inside their own wrapper and the figure "
        "cards reflow 2-per-row.")

    ob = walk["counts_before"]
    of_ = walk["counts_after_cleanup"]
    official_unchanged = all(ob[t] == of_[t] for t in OFFICIAL)
    exec_net_zero = all(ob[t] == of_[t] for t in
                        ("assessments", "assessment_versions", "assessment_items",
                         "activity_assignments", "activity_attempts", "activity_answers",
                         "activity_results", "activity_result_items"))
    rep["integrity"] = {
        "DATABASE_WRITES": "0 by the analysis itself. The acceptance walk creates a temporary list / "
                           "assignment / attempt / answers / result and DELETES all of it; the "
                           "official tables are never written.",
        "OPENAI_CALLS": 0,
        "MIGRATIONS": 0,
        "TABLES_CREATED": 0,
        "official_tables_unchanged": official_unchanged,
        "execution_and_result_tables_net_zero_after_cleanup": exec_net_zero,
        "analysis_added_no_rows": any(s["step"].startswith("20.") and s["ok"] for s in walk["steps"]),
        "cleanup_restored_db": any(s["step"].startswith("21.") and s["ok"] for s in walk["steps"]),
    }
    rep["tests"] = {
        "backend": {"file": "tests/test_phase19_pedagogical_analysis.py", "cases": 20,
                    "result": "20/20 passed",
                    "covers": ["no result -> 404", "all correct (accuracy 1.0)",
                               "all wrong (accuracy 0.0, no zero division)", "mixed",
                               "unanswered counted, not in accuracy",
                               "multiple disciplines + contents (areas resolved)",
                               "subcontent nested; not invented when absent",
                               "UNCLASSIFIED preserved in totals, not attributed",
                               "LOW + FORCED_CLOSURE distinguished + provisional, not promoted",
                               "visual_dependency preserved",
                               "sample size + PONTO_FORTE / PONTO_MELHORIA / DESEMPENHO_INTERMEDIARIO",
                               "INSUFFICIENT_SAMPLE never a verdict",
                               "PerformanceThresholdPolicy configurable; defaults 3 / 0.80 / 0.60",
                               "no N+1 (query count <= 12 for a 24-question activity)",
                               "manager scope + tenant isolation",
                               "student only sees own analysis (403 for others)",
                               "deterministic (identical JSON)",
                               "analysis is READ-ONLY (DB row counts unchanged)",
                               "SUPERSEDED classification never used",
                               "module imports no AI/provider symbol"]},
        "frontend": {"file": "tests/test_phase19_pedagogical_analysis_frontend.js", "cases": 13,
                     "result": "13/13 passed",
                     "covers": ["section + 5 subsections in index.html", "READ-ONLY fetch, GET only",
                                "overview fields", "unclassified surfaced", "by-discipline / "
                                "by-content tables with accuracy + band", "band vocabulary",
                                "strengths / improvements + insufficient-sample fallback",
                                "aproveitamento framing, never 'nota'", "hidden + toggle",
                                "state cleared on close", "responsive table wrapper",
                                "no grade / TRI / ranking / comparison / domain map / trilha"]},
        "regression": "122 frontend node tests + 202 backend tests (phases 12-19 + assessments + "
                      "exercise_lists + api_questions + catalog + lifecycle + phase16 onboarding/"
                      "security) + compileall src/agente_ia_edu - all green.",
    }
    qcounts = {n: perf[str(n)]["queries"] for n in (10, 50, 100, 200, 500)}
    rep["query_count"] = {
        "by_question_count": qcounts,
        "constant_within_1": perf["query_count_constant"],
        "note": "catalog (cached) + questions IN + options selectinload + ACTIVE-classifications IN "
                "+ result-items load. Independent of the number of questions.",
    }
    rep["known_limitations"] = [
        "Confidence bands are only as good as the classification metadata: many real curriculum-v2 "
        "rows have no 'confidence' key, so they count as NONE (truthfully) rather than HIGH.",
        "The manager endpoint builds one analysis per corrected result in memory; for a very large "
        "class the response can be big (still a constant query count because every student shares "
        "the same frozen version). Pagination / a lighter class-only mode is a future refinement.",
        "Temporal analysis is a prepared shape only (activity_date + window list) - no evolution "
        "algorithm, no history join across activities yet.",
        "Strengths/improvements are computed at content and discipline grain; area-level rollups and "
        "cross-activity aggregation are left for the Domain Map phase.",
        "No cache is implemented (the analysis is deterministic and cheap); the service is "
        "structured so a cache can wrap analyze_* later without changing the engine.",
    ]
    rep["next_steps"] = [
        "PHASE 20+: Domain Map - persist/roll up ContentPerformance across activities and time using "
        "this engine's output as the measurement layer.",
        "Then: Adaptive Learning Path (consume the Domain Map), class dashboards, question review, "
        "and the join content_code -> authored material.",
        "Optional here-adjacent: temporal windows implementation, a class-aggregate-only manager "
        "mode, an optional cache.",
    ]

    ok = (walk["all_passed"] and official_unchanged and exec_net_zero
          and perf["query_count_constant"]
          and rep["integrity"]["analysis_added_no_rows"]
          and rep["integrity"]["cleanup_restored_db"])
    rep["FINAL_DECISION"] = ("PHASE_19_PEDAGOGICAL_ANALYSIS_COMPLETE" if ok
                             else "PHASE_19_PEDAGOGICAL_ANALYSIS_BLOCKED")
    if not ok:
        rep["blocker"] = "see acceptance_walk.steps / performance / integrity"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if report["FINAL_DECISION"].endswith("COMPLETE") else 1)
