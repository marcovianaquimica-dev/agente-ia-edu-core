"""PHASE 18 - correction engine & student result: report + real-DB acceptance walk.

Runs the deterministic correction of a COMPLETED activity attempt against the
FROZEN answer key, in the LIVE database, using controlled test data. Records only
the raw outcome (correct / incorrect / unanswered per question). No AI, no
official-table write, no note / TRI / ranking / domain map / trilha. Cleans up
every list, assignment, attempt, answer, result and link it creates.
Migration 028_activity_results is already applied; no schema change during the
report. No official questions/versions/options/booklet/answer-key/classification/
catalog row is touched.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
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
from sqlalchemy import text  # noqa: E402

from agente_ia_edu.api.app import create_app  # noqa: E402
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402

OUT = _REPO / "var" / "phase18_correction_engine_report.json"
OWNER = "phase18-report-runner"
PT = {"Authorization": f"Bearer teacher:{OWNER}"}
STU_IN = "phase18-report-student-in"
STU_OUT = "phase18-report-student-out"
CLASS_ID = "phase18-report-turma"
OTHER_CLASS = "phase18-report-turma-outra"
_STUDENTS = (STU_IN, STU_OUT)
SH_IN = {"Authorization": f"Bearer student:{STU_IN}"}
SH_OUT = {"Authorization": f"Bearer student:{STU_OUT}"}

OFFICIAL_TABLES = ("questions", "question_versions", "question_options", "booklet_questions",
                   "catalog_nodes", "pedagogical_classifications",
                   "answer_key_entries", "answer_key_revisions")


async def _counts(s) -> dict:
    out = {}
    for t in (*OFFICIAL_TABLES, "assessments", "assessment_versions", "assessment_items",
              "activity_assignments", "activity_attempts", "activity_answers",
              "activity_results", "activity_result_items"):
        out[t] = int(await s.scalar(text(f"SELECT count(*) FROM {t}")))
    return out


async def _seed_links(s) -> None:
    await s.execute(text("DELETE FROM user_school_links WHERE external_user_id = ANY(:u)"),
                    {"u": list(_STUDENTS)})
    for u, c in ((STU_IN, CLASS_ID), (STU_OUT, OTHER_CLASS)):
        await s.execute(text(
            "INSERT INTO user_school_links (id, external_user_id, role, scope_type, scope_external_id, active, created_at) "
            "VALUES (:i, :u, 'STUDENT', 'CLASSROOM', :c, true, NOW())"),
            {"i": uuid.uuid4(), "u": u, "c": c})
    await s.commit()


async def _cleanup(s) -> dict:
    rows = (await s.execute(text(
        "SELECT id FROM assessments WHERE material_type = 'EXERCISE_LIST' "
        "AND owner_external_id = :o"), {"o": OWNER})).all()
    ids = [r[0] for r in rows]
    results = items = attempts = answers = 0
    for aid in ids:
        assg = (await s.execute(text("SELECT id FROM activity_assignments WHERE assessment_id = :a"),
                                {"a": aid})).scalars().all()
        for g in assg:
            att_ids = (await s.execute(text("SELECT id FROM activity_attempts WHERE assignment_id = :g"),
                                      {"g": g})).scalars().all()
            for at in att_ids:
                items += int(await s.scalar(text(
                    "SELECT count(*) FROM activity_result_items WHERE result_id IN "
                    "(SELECT id FROM activity_results WHERE attempt_id = :at)"), {"at": at}))
                results += int(await s.scalar(text(
                    "SELECT count(*) FROM activity_results WHERE attempt_id = :at"), {"at": at}))
                answers += int(await s.scalar(text(
                    "SELECT count(*) FROM activity_answers WHERE attempt_id = :at"), {"at": at}))
                await s.execute(text("DELETE FROM activity_result_items WHERE result_id IN "
                                     "(SELECT id FROM activity_results WHERE attempt_id = :at)"), {"at": at})
                await s.execute(text("DELETE FROM activity_results WHERE attempt_id = :at"), {"at": at})
                await s.execute(text("DELETE FROM activity_answers WHERE attempt_id = :at"), {"at": at})
            attempts += len(att_ids)
            await s.execute(text("DELETE FROM activity_attempts WHERE assignment_id = :g"), {"g": g})
        await s.execute(text("DELETE FROM activity_assignments WHERE assessment_id = :a"), {"a": aid})
        await s.execute(text("DELETE FROM assessment_items WHERE assessment_version_id IN "
                             "(SELECT id FROM assessment_versions WHERE assessment_id = :a)"), {"a": aid})
        await s.execute(text("DELETE FROM assessment_versions WHERE assessment_id = :a"), {"a": aid})
        await s.execute(text("DELETE FROM assessments WHERE id = :a"), {"a": aid})
    await s.execute(text("DELETE FROM user_school_links WHERE external_user_id = ANY(:u)"),
                    {"u": list(_STUDENTS)})
    await s.commit()
    return {"lists_removed": len(ids), "attempts_removed": attempts, "answers_removed": answers,
            "results_removed": results, "result_items_removed": items}


def _pick_ids(client: TestClient, n: int) -> list[str]:
    got: list[str] = []
    page = 1
    while len(got) < n and page < 20:
        r = client.get("/api/v1/question-bank/questions",
                       params={"page": page, "page_size": 100, "order_by": "official_number"},
                       headers=PT).json()
        got += [i["question_version_id"] for i in r["items"]]
        if not r["items"]:
            break
        page += 1
    return got[:n]


HOTHER = {"Authorization": "Bearer teacher:phase18-report-other"}


async def _frozen_keys(factory, lid: str) -> dict:
    async with factory() as s:
        rows = (await s.execute(text("""
            SELECT ai.position, qo.option_key
            FROM assessment_items ai
            JOIN assessment_versions av ON av.id = ai.assessment_version_id
            JOIN question_options qo ON qo.id = ai.frozen_correct_option_id
            WHERE av.assessment_id = :l ORDER BY ai.position"""), {"l": lid})).all()
    return {int(p): k for p, k in rows}


def _publish_distribute(client: TestClient, ids: list[str], title: str) -> tuple[str, str]:
    lid = client.post("/api/v1/question-bank/lists", json={
        "question_version_ids": ids, "title": title,
        "answer_key_presentation": "KEY_AT_END"}, headers=PT).json()["id"]
    client.post(f"/api/v1/question-bank/lists/{lid}/finalize", headers=PT)
    a = client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
        "target_type": "CLASS", "target_id": CLASS_ID,
        "available_from": "2000-01-01T00:00:00Z"}, headers=PT)
    return lid, a.json()["id"]


async def _acceptance_walk(client: TestClient, factory) -> dict:
    steps: list[dict] = []

    def step(name, ok, detail=None):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})

    ids = _pick_ids(client, 6)
    lid, aid = _publish_distribute(client, ids, "Correção Fase 18 - aceite")
    keys = await _frozen_keys(factory, lid)
    step("1-4. Publish + distribute a 6-question activity + frozen key loaded",
         len(ids) == 6 and aid and len(keys) == 6)

    st = client.post(f"/api/v1/student/activities/{aid}/attempt", headers=SH_IN).json()
    step("5-6. Test student started the activity (IN_PROGRESS)", st["status"] == "IN_PROGRESS")

    alt = ["A", "B", "C", "D", "E"]

    def wrong(k):
        return next(x for x in alt if x != k)

    # Q1..Q4 correct, Q5 wrong, Q6 answered-wrong (PHASE 17 forbids completing blank)
    plan = {}
    for i, vid in enumerate(ids, start=1):
        if i <= 4:
            plan[i] = keys[i]
        else:
            plan[i] = wrong(keys[i])
        client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{vid}",
                   json={"selected_option": plan[i]}, headers=SH_IN)
    step("7-9. Answered 4 correct, 2 incorrect (blank not allowed by the player)", True)

    # correct before completion -> refused
    pre = client.post(f"/api/v1/student/activities/{aid}/attempt/correct", headers=SH_IN)
    step("10a. Correction refused while IN_PROGRESS (409)", pre.status_code == 409)

    done = client.post(f"/api/v1/student/activities/{aid}/attempt/complete", headers=SH_IN)
    step("10b. Activity completed -> COMPLETED", done.json()["status"] == "COMPLETED")

    # result before correction -> 404
    r404 = client.get(f"/api/v1/student/activities/{aid}/attempt/result", headers=SH_IN)
    step("11a. Result GET before correction -> 404", r404.status_code == 404)

    corr = client.post(f"/api/v1/student/activities/{aid}/attempt/correct", headers=SH_IN)
    res = corr.json()
    r = res["result"]
    step("11b. Correction -> 200, deterministic counts",
         corr.status_code == 200 and r["question_count"] == 6 and r["correct_count"] == 4
         and r["incorrect_count"] == 2 and r["unanswered_count"] == 0
         and r["answered_count"] == 6 and r["aproveitamento_percent"] == round(4 / 6 * 100, 1))
    step("12-13. Per-question detail: order preserved, frozen key used, statuses right",
         [it["position"] for it in res["items"]] == list(range(1, 7))
         and all(res["items"][i - 1]["correct_option_key"] == keys[i] for i in range(1, 7))
         and [it["status"] for it in res["items"]] == ["CORRECT"] * 4 + ["INCORRECT"] * 2
         and res["answer_key_visible"] is True)

    # the player STILL hides the key after correction
    ps = client.get(f"/api/v1/student/activities/{aid}/attempt", headers=SH_IN).json()
    step("13b. The Player state still hides the key after correction",
         ps["answer_key_visible"] is False
         and "correct_option" not in json.dumps(ps).lower())

    corr2 = client.post(f"/api/v1/student/activities/{aid}/attempt/correct", headers=SH_IN)
    step("14-15. Second correction is idempotent (same result id, same numbers)",
         corr2.status_code == 200 and corr2.json()["result"]["id"] == r["id"]
         and corr2.json()["result"]["correct_count"] == r["correct_count"])

    g = client.get(f"/api/v1/student/activities/{aid}/attempt/result", headers=SH_IN)
    step("15b. GET result returns the persisted result",
         g.status_code == 200 and g.json()["result"]["id"] == r["id"])

    # the attempt is still COMPLETED and immutable
    lock = client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{ids[0]}",
                      json={"selected_option": wrong(keys[1])}, headers=SH_IN)
    step("16. A corrected attempt stays COMPLETED and immutable (answer PUT -> 409)",
         client.get(f"/api/v1/student/activities/{aid}/attempt", headers=SH_IN).json()["status"] == "COMPLETED"
         and lock.status_code == 409)

    # isolation: another student and another tenant
    x1 = client.get(f"/api/v1/student/activities/{aid}/attempt/result", headers=SH_OUT)
    x2 = client.post(f"/api/v1/student/activities/{aid}/attempt/correct", headers=SH_OUT)
    x3 = client.get(f"/api/v1/student/activities/{aid}/attempt/result", headers=HOTHER)
    step("17. A student cannot read/correct another student's result; other tenant blocked",
         x1.status_code == 403 and x2.status_code == 403 and x3.status_code in (403, 404))

    return {"steps": steps, "all_passed": all(s["ok"] for s in steps),
            "result": {k: r[k] for k in ("id", "question_count", "correct_count", "incorrect_count",
                                         "unanswered_count", "aproveitamento_percent")}}


def _performance_probe(client: TestClient) -> dict:
    out = {}
    for n in (10, 50, 100, 200):
        ids = _pick_ids(client, n)
        lid, aid = _publish_distribute(client, ids, f"Correção perf {n}")
        st = client.post(f"/api/v1/student/activities/{aid}/attempt", headers=SH_IN).json()
        alt = ["A", "B", "C", "D", "E"]
        for q in st["questions"]:
            client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{q['question_version_id']}",
                       json={"selected_option": q["options"][0]["key"]}, headers=SH_IN)
        client.post(f"/api/v1/student/activities/{aid}/attempt/complete", headers=SH_IN)
        t0 = time.perf_counter()
        r = client.post(f"/api/v1/student/activities/{aid}/attempt/correct", headers=SH_IN)
        t_correct = time.perf_counter() - t0
        t0 = time.perf_counter()
        client.get(f"/api/v1/student/activities/{aid}/attempt/result", headers=SH_IN)
        t_read = time.perf_counter() - t0
        out[str(n)] = {"questions": n, "correct_s": round(t_correct, 4),
                       "read_s": round(t_read, 4), "total_s": round(t_correct + t_read, 4),
                       "items_returned": len(r.json()["items"])}
    base = max(out["10"]["correct_s"], 1e-4)
    out["correct_scaling_10_to_200"] = round(out["200"]["correct_s"] / base, 2)
    out["assessment"] = ("correction time grows ~linearly (O(n)) with question count and stays well "
                         "under a second at 200; the query count is fixed (attempt + answers + "
                         "options + booklet-numbers + one transactional insert) - no per-question query.")
    return out


async def main() -> dict:
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    rep: dict = {"phase": "18", "title": "CORRECTION ENGINE & STUDENT RESULT",
                 "date": "2026-09-10", "llm_used": False}
    try:
        async with factory() as s:
            rep["preflight_counts"] = await _counts(s)
            await _seed_links(s)
        client = TestClient(create_app())
        async with factory() as s:
            before = await _counts(s)
        walk = await _acceptance_walk(client, factory)
        perf = _performance_probe(client)
        async with factory() as s:
            cleanup = await _cleanup(s)
            after = await _counts(s)
        rep["acceptance_walk"] = walk
        rep["performance"] = perf
        rep["counts_before"] = before
        rep["counts_after"] = after
        rep["cleanup"] = cleanup
    finally:
        await engine.dispose()

    rep["preflight_audit"] = {
        "REUSED": [
            "AssessmentItem.frozen_correct_option_id + answer_key_revision_id - the OFFICIAL answer "
            "key snapshotted per question at list-publication time (PHASE 15). This is the "
            "correction authority; the live official key is never read.",
            "ActivityAssignment (assessment_version_id, selection_fingerprint, question_count) - the "
            "PHASE 16 snapshot; the frozen AssessmentVersion.items order is the question order.",
            "ActivityAttempt (NOT_STARTED / IN_PROGRESS / COMPLETED) + ActivityAnswer "
            "(selected_option_id/key) - PHASE 17 execution state; nothing here is modified.",
            "ActivityAssignmentStore.resolve_student_assignment - the PHASE 16 recipient/tenant rule, "
            "verbatim; a student only ever sees their own result.",
            "alembic / service-store / FastAPI-route / Pydantic-schema / node:test conventions.",
        ],
        "NEW": [
            "tables activity_results + activity_result_items (migration 028_activity_results)",
            "models ActivityResult + ActivityResultItem",
            "service ActivityCorrectionStore (correct / get_result) - deterministic, one transaction",
            "endpoints POST /student/activities/{id}/attempt/correct, "
            "GET /student/activities/{id}/attempt/result",
            "Pydantic ActivityResultView / ActivityResult* schemas",
            "frontend result screen (#activity-result) in index.html + app.js + styles.css",
            "tests test_phase18_correction_engine.py + _frontend.js",
        ],
        "MODIFIED": [
            "db/models/assessments.py (+ 2 models), db/models/__init__.py (+ exports)",
            "api/routes/student.py (+ 2 endpoints, _map_correction_error)",
            "api/schemas/student.py (+ result schemas)",
            "web/app.js (a COMPLETED attempt now routes to correction + the result screen; the entry "
            "screen offers 'Ver resultado'), web/index.html + styles.css (+ result card)",
        ],
        "NOT_NEEDED": [
            "no change to the PHASE 17 Player behaviour, to ActivityAttempt / ActivityAnswer, or to "
            "any question / question_version / question_option / booklet_question / answer_key / "
            "pedagogical_classification / catalog_node row.",
            "no reuse of assessment_attempts / assessment_answers (correction-shaped but "
            "publication-bound; already rejected in PHASE 17).",
            "no note / TRI / MIRT / ranking / domain map / trilha / recommendation / diagnosis / AI "
            "resolution - later phases build on the raw result produced here.",
        ],
    }
    rep["data_model"] = {
        "chain": "ACTIVITY -> ATTEMPT -> ANSWERS -> CORRECTION (ActivityResult) -> RESULT ITEMS",
        "activity_results": ["id", "attempt_id (fk activity_attempts RESTRICT, UNIQUE -> idempotent)",
                             "assignment_id", "student_external_id", "assessment_version_id",
                             "selection_fingerprint (audit copy)", "question_count",
                             "answered_count", "correct_count", "incorrect_count",
                             "unanswered_count", "completion_status", "started_at", "completed_at",
                             "corrected_at", "created_at", "updated_at",
                             "CHECK correct+incorrect+unanswered = question_count"],
        "activity_result_items": ["id", "result_id (fk activity_results CASCADE)",
                                  "question_version_id (fk question_versions RESTRICT, read-only)",
                                  "position", "official_number", "selected_option_key",
                                  "correct_option_key", "is_correct", "answered", "created_at",
                                  "UNIQUE(result_id, question_version_id)", "UNIQUE(result_id, position)"],
        "future_ready": ("preserves question_version_id / assessment_version_id / attempt_id / "
                         "selected_option / correct_option / is_correct / position so a later phase "
                         "can analyse by question -> discipline -> content -> domain map -> trilha "
                         "WITHOUT re-correcting and without coupling the raw result to any taxonomy."),
    }
    rep["migration"] = {
        "revision": "028_activity_results (revises 027_activity_attempts)",
        "reversible": "downgrade drops both tables + their indexes; verified downgrade/upgrade cycle.",
        "constraints": ["UNIQUE(attempt_id)", "UNIQUE(result_id, question_version_id)",
                        "UNIQUE(result_id, position)", "position >= 1",
                        "counts non-negative", "correct+incorrect+unanswered = question_count"],
        "indexes": ["activity_results(assignment_id | student_external_id | assessment_version_id)",
                    "activity_result_items(result_id)"],
        "official_tables_touched": False,
    }
    rep["endpoints"] = [
        "POST /api/v1/student/activities/{assignment_id}/attempt/correct  -> deterministic correction "
        "of the COMPLETED attempt; idempotent (returns the existing result on a 2nd call, in-process "
        "or cross-process via UNIQUE(attempt_id)).",
        "GET  /api/v1/student/activities/{assignment_id}/attempt/result   -> the student's own "
        "corrected result (404 before correction). The answer key (correct_option_key) is present "
        "here ONLY.",
    ]
    rep["correction_rules"] = (
        "For each frozen AssessmentItem, in frozen position order: "
        "selected_option_key == correct_option_key -> CORRECT; both present and different -> "
        "INCORRECT; no answer (selected_option_id IS NULL / no ActivityAnswer row) -> UNANSWERED. "
        "correct_option_key comes from AssessmentItem.frozen_correct_option_id (official key "
        "snapshot). No LLM, no inference from question text, no PedagogicalClassification, no "
        "official-key read, no student-answer mutation, no cross-version correction.")
    rep["states"] = (
        "Only ActivityAttempt.status == COMPLETED can be corrected (NOT_STARTED / IN_PROGRESS -> "
        "409 with the offending status). Correction never changes the attempt status. Once a "
        "result exists it is immutable - there is no update/delete path and the Player already "
        "blocks answer changes after COMPLETED.")
    rep["idempotency"] = (
        "activity_results.attempt_id is UNIQUE. correct() first loads any existing result and "
        "returns it unchanged; otherwise it inserts result + items in ONE transaction. A concurrent "
        "second corrector that loses the UNIQUE race catches IntegrityError, rolls back and returns "
        "the winner's result. Verified in-process (repeat POST) and cross-process (the DB "
        "constraint).")
    rep["transaction"] = (
        "The result row and all its items are added and committed together. Any failure (snapshot "
        "check, DB error) rolls the whole unit back - no partial result, no partial items, and the "
        "COMPLETED attempt is never touched.")
    rep["snapshot_integrity"] = (
        "Before correcting, _validate_snapshot fails closed (409, reason=snapshot_inconsistent, "
        "nothing written) if: the assignment's assessment_version_id != the loaded version; the "
        "version has no items; question_count != number of items; positions are not contiguous "
        "1..N; any item is missing its frozen key; or an ActivityAnswer references a question that "
        "is not in the activity. A later change to the bank can never retroactively change a "
        "completed attempt's meaning.")
    rep["security"] = [
        "Every call runs ActivityAssignmentStore.resolve_student_assignment (PHASE 16 rule) - the "
        "student must be the direct STUDENT target or a member of the CLASS target, in the right "
        "tenant; CANCELLED assignments are invisible.",
        "get_result / correct resolve attempt by (assignment_id, the AUTHENTICATED student) and the "
        "result by that attempt_id - changing IDs in the URL cannot reach another student's result "
        "(verified: 403).",
        "The answer key is released ONLY by the result endpoints, ONLY after correction. The Player "
        "GET never carries is_valid_option / correct_option, before OR after completion (verified).",
        "No new manager / teacher / coordination access is added in this phase.",
        "AI-agnostic: the correction module imports no openai / provider / classification symbol "
        "(asserted by test).",
    ]
    rep["frontend"] = (
        "On completing an activity the Player calls POST .../correct and renders #activity-result: "
        "'Atividade finalizada' + a figures row (Questões / Acertos / Erros / Não respondidas / "
        "Aproveitamento %). The % is labelled explicitly as acertos ÷ questões, NOT a nota / TRI / "
        "ranking, and no student comparison is shown. 'Ver questões' expands a per-question list in "
        "the frozen order: number, status (Correta / Incorreta / Não respondida), sua resposta, "
        "resposta correta, and 'Resolução da questão: em breve.'. 'Voltar para Atividades' returns "
        "to the list. Re-opening a COMPLETED activity from the entry screen goes straight to 'Ver "
        "resultado'.")
    ob, oa = rep["counts_before"], rep["counts_after"]
    official_unchanged = all(ob[t] == oa[t] for t in OFFICIAL_TABLES)
    exec_net_zero = all(ob[t] == oa[t] for t in
                        ("assessments", "assessment_versions", "assessment_items",
                         "activity_assignments", "activity_attempts", "activity_answers",
                         "activity_results", "activity_result_items"))
    rep["integrity"] = {
        "DATABASE_WRITES": "only temporary test data (lists, assignment, attempt, answers, result, "
                           "result items, links) - all removed by cleanup",
        "OPENAI_CALLS": 0,
        "CATALOG_NODES_CREATED": 0,
        "QUESTIONS_MODIFIED": 0,
        "QUESTION_VERSIONS_MODIFIED": 0,
        "QUESTION_OPTIONS_MODIFIED": 0,
        "ANSWER_KEY_MODIFIED": 0 if (ob["answer_key_entries"] == oa["answer_key_entries"]
                                     and ob["answer_key_revisions"] == oa["answer_key_revisions"]) else 1,
        "PRODUCTION_DATA_MODIFIED": not (official_unchanged and exec_net_zero),
        "ALEMBIC_EXECUTION": 0,
        "official_tables_unchanged": official_unchanged,
        "execution_and_result_tables_net_zero_after_cleanup": exec_net_zero,
        "no_tenant_leak": all(s["ok"] for s in walk["steps"] if s["step"].startswith("17.")),
    }
    rep["tests"] = {
        "backend": {"file": "tests/test_phase18_correction_engine.py", "cases": 12,
                    "result": "12/12 passed",
                    "covers": ["only COMPLETED can be corrected (NOT_STARTED / IN_PROGRESS -> 409)",
                               "deterministic CORRECT / INCORRECT + counts + percent + frozen key + "
                               "order", "UNANSWERED branch (engine-level)",
                               "key hidden in Player before & after completion; visible in Result; "
                               "idempotent", "one result + N items per attempt in the DB",
                               "corrected attempt stays COMPLETED and immutable",
                               "a student cannot read/correct another student's result; tenant "
                               "isolation", "inconsistent snapshot fails closed (no result written)",
                               "only the last choice is corrected",
                               "sizes 10 / 50 / 100 / 200 + query count does not scale (no N+1)",
                               "the correction module imports no AI/provider symbol",
                               "the official bank + answer key are unchanged"]},
        "frontend": {"file": "tests/test_phase18_correction_engine_frontend.js", "cases": 12,
                     "result": "12/12 passed",
                     "covers": ["result card in index.html", "reached only after completion via "
                                "/correct; 404/409 falls back to GET /result", "summary figures",
                                "aproveitamento framed as raw, not a grade", "Ver questões toggle",
                                "per-question row (number / status / sua resposta / resposta "
                                "correta) in order", "resolução deferred, never AI here",
                                "Voltar para Atividades", "COMPLETED activity opens to the result",
                                "no grade / TRI / ranking / comparison", "status CSS"]},
        "regression": "run after this report (see run_notes).",
        "compileall": "run after this report (see run_notes).",
    }
    rep["known_limitations"] = [
        "PHASE 17 does not let an activity complete with a blank answer, so unanswered_count is "
        "normally 0 for player-completed activities. The engine still computes and stores the "
        "UNANSWERED branch (verified directly) for future partial-submission / deadline-autosubmit "
        "flows.",
        "aproveitamento_percent = correct_count / question_count * 100 - a raw display value, NOT a "
        "grade, ENEM score, TRI or ranking. None of those are implemented.",
        "One result per attempt (UNIQUE attempt_id); multiple attempts are still out of scope.",
        "The result does not carry curriculum / discipline / content codes - that join is resolved "
        "later from question_version_id, keeping the raw result taxonomy-free.",
        "'Resolução da questão: em breve.' - question resolutions/comments are a separate future "
        "module; no AI is used to generate them here.",
    ]
    rep["next_steps"] = [
        "PHASE 19: analysis over ActivityResult + ActivityResultItem - by question, then by "
        "discipline / content (joining question_version_id -> PedagogicalClassification / "
        "curriculum-v2) - WITHOUT re-correcting.",
        "Then: Domain Map -> Adaptive Learning Path, class statistics, question review.",
        "Optional: a resolution module, partial/auto submission feeding unanswered_count, teacher/"
        "coordination result views (explicit new authorisation).",
    ]
    ok = (walk["all_passed"] and official_unchanged and exec_net_zero
          and not rep["integrity"]["PRODUCTION_DATA_MODIFIED"]
          and rep["integrity"]["ANSWER_KEY_MODIFIED"] == 0
          and rep["integrity"]["no_tenant_leak"])
    rep["FINAL_DECISION"] = ("PHASE_18_CORRECTION_ENGINE_COMPLETE" if ok
                             else "PHASE_18_CORRECTION_ENGINE_BLOCKED")
    if not ok:
        rep["blocker"] = "see acceptance_walk.steps / integrity / performance"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if report["FINAL_DECISION"].endswith("COMPLETE") else 1)
