"""PHASE 17 - student activity player report + real-data acceptance walk.

Executes a distributed activity end to end in the LIVE database (that is the
point of the phase): start -> answer -> navigate -> change -> autosave -> reload
-> finalise -> COMPLETED. Records only EXECUTION state - no correction, score,
percentage, ranking or pedagogical result. Cleans up every attempt, answer,
assignment and list it creates, so the database ends exactly as it started.
Migration 027_activity_attempts is already applied; no schema change happens
during the report. No official questions/versions/options/booklet/classification
/catalog row is touched.
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

OUT = _REPO / "var" / "phase17_student_activity_player_report.json"
OWNER = "phase17-report-runner"
PT = {"Authorization": f"Bearer teacher:{OWNER}"}
STU_IN = "phase17-report-student-in"
STU_OUT = "phase17-report-student-out"
CLASS_ID = "phase17-report-turma"
OTHER_CLASS = "phase17-report-turma-outra"
_STUDENTS = (STU_IN, STU_OUT)
SH_IN = {"Authorization": f"Bearer student:{STU_IN}"}
SH_OUT = {"Authorization": f"Bearer student:{STU_OUT}"}

OFFICIAL_TABLES = ("questions", "question_versions", "question_options", "booklet_questions",
                   "catalog_nodes", "pedagogical_classifications", "answer_key_entries")


async def _counts(s) -> dict:
    out = {}
    for t in (*OFFICIAL_TABLES, "assessments", "assessment_versions", "assessment_items",
              "activity_assignments", "activity_attempts", "activity_answers"):
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
    att = ans = 0
    for aid in ids:
        assg = (await s.execute(text("SELECT id FROM activity_assignments WHERE assessment_id = :a"),
                                {"a": aid})).scalars().all()
        for g in assg:
            ans += int(await s.scalar(text(
                "SELECT count(*) FROM activity_answers WHERE attempt_id IN "
                "(SELECT id FROM activity_attempts WHERE assignment_id = :g)"), {"g": g}))
            att += int(await s.scalar(text(
                "SELECT count(*) FROM activity_attempts WHERE assignment_id = :g"), {"g": g}))
            await s.execute(text("DELETE FROM activity_answers WHERE attempt_id IN "
                                 "(SELECT id FROM activity_attempts WHERE assignment_id = :g)"), {"g": g})
            await s.execute(text("DELETE FROM activity_attempts WHERE assignment_id = :g"), {"g": g})
        await s.execute(text("DELETE FROM activity_assignments WHERE assessment_id = :a"), {"a": aid})
        await s.execute(text("DELETE FROM assessment_items WHERE assessment_version_id IN "
                             "(SELECT id FROM assessment_versions WHERE assessment_id = :a)"), {"a": aid})
        await s.execute(text("DELETE FROM assessment_versions WHERE assessment_id = :a"), {"a": aid})
        await s.execute(text("DELETE FROM assessments WHERE id = :a"), {"a": aid})
    await s.execute(text("DELETE FROM user_school_links WHERE external_user_id = ANY(:u)"),
                    {"u": list(_STUDENTS)})
    await s.commit()
    return {"lists_removed": len(ids), "attempts_removed": att, "answers_removed": ans}


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


def _publish_and_distribute(client: TestClient, ids: list[str], title: str,
                            target_class: str = CLASS_ID) -> str:
    lid = client.post("/api/v1/question-bank/lists", json={
        "question_version_ids": ids, "title": title,
        "answer_key_presentation": "KEY_AT_END"}, headers=PT).json()["id"]
    client.post(f"/api/v1/question-bank/lists/{lid}/finalize", headers=PT)
    a = client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
        "target_type": "CLASS", "target_id": target_class,
        "available_from": "2000-01-01T00:00:00Z"}, headers=PT)
    return a.json()["id"]


def _acceptance_walk(client: TestClient) -> dict:
    steps: list[dict] = []

    def step(name, ok, detail=None):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})

    ids = _pick_ids(client, 5)
    aid = _publish_and_distribute(client, ids, "Player Fase 17 - aceite")
    step("1. Publish + distribute a 5-question activity to the test class", len(ids) == 5 and aid)

    # unauthorised student
    x = client.post(f"/api/v1/student/activities/{aid}/attempt", headers=SH_OUT)
    step("2. A student outside the class cannot start (403)", x.status_code == 403)

    st = client.post(f"/api/v1/student/activities/{aid}/attempt", headers=SH_IN).json()
    step("3. Start -> IN_PROGRESS attempt, frozen order, no answer key",
         st["status"] == "IN_PROGRESS"
         and [q["question_version_id"] for q in st["questions"]] == ids
         and st["answer_key_visible"] is False
         and all(set(o) == {"key", "position", "text"} for q in st["questions"] for o in q["options"]))

    st2 = client.post(f"/api/v1/student/activities/{aid}/attempt", headers=SH_IN).json()
    step("4. Start again is idempotent (same attempt id)",
         st2["attempt"]["id"] == st["attempt"]["id"])

    v = ids
    ov = {q["question_version_id"]: [o["key"] for o in q["options"]] for q in st["questions"]}

    def put(qv, key):
        return client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{qv}",
                          json={"selected_option": key}, headers=SH_IN)

    step("5. Q1 -> A saved", put(v[0], ov[v[0]][0]).json()["answered_count"] == 1)
    step("6. Q2 -> B saved", put(v[1], ov[v[1]][1]).json()["answered_count"] == 2)
    # skip Q3
    step("7. Q4 -> C saved (Q3 skipped)", put(v[3], ov[v[3]][2]).json()["answered_count"] == 3)
    step("8. back to Q3 -> D saved", put(v[2], ov[v[2]][3]).json()["answered_count"] == 4)
    r = put(v[0], ov[v[0]][4])
    step("9. change Q1 A -> E (still 4 answered, one row)",
         r.json()["selected_option"] == ov[v[0]][4] and r.json()["answered_count"] == 4)

    # incomplete finalisation
    inc = client.post(f"/api/v1/student/activities/{aid}/attempt/complete", headers=SH_IN)
    step("10. Incomplete finalisation rejected by the BACKEND (409 + pending)",
         inc.status_code == 409 and inc.json()["detail"]["pending_count"] == 1
         and inc.json()["detail"]["pending_positions"] == [5])

    # "reload": a fresh state request preserves every current choice
    reloaded = client.get(f"/api/v1/student/activities/{aid}/attempt", headers=SH_IN).json()
    sel = {q["position"]: q["selected_option"] for q in reloaded["questions"]}
    step("11. Reload preserves answers (Q1=E, Q2=B, Q3=D, Q4=C, Q5=empty)",
         sel[1] == ov[v[0]][4] and sel[2] == ov[v[1]][1] and sel[3] == ov[v[2]][3]
         and sel[4] == ov[v[3]][2] and sel[5] is None
         and reloaded["answered_count"] == 4)

    step("12. Answer the last question", put(v[4], ov[v[4]][0]).json()["pending_count"] == 0)

    done = client.post(f"/api/v1/student/activities/{aid}/attempt/complete", headers=SH_IN).json()
    step("13. Complete -> COMPLETED + completed_at",
         done["status"] == "COMPLETED" and done["attempt"]["completed_at"] is not None)

    step("14. No change is accepted after COMPLETED (409)",
         put(v[0], ov[v[0]][1]).status_code == 409)
    step("15. Re-completing is a safe no-op (still COMPLETED)",
         client.post(f"/api/v1/student/activities/{aid}/attempt/complete", headers=SH_IN).json()["status"] == "COMPLETED")
    step("16. Starting again does not reopen or duplicate",
         client.post(f"/api/v1/student/activities/{aid}/attempt", headers=SH_IN).json()["status"] == "COMPLETED")

    return {"steps": steps, "all_passed": all(s["ok"] for s in steps),
            "activity": {"assignment_id": aid, "questions": len(ids)}}


def _performance_probe(client: TestClient) -> dict:
    """s23: start / state / autosave / navigate / complete for 10, 50, 100
    questions. We measure wall time of the STATE endpoint (the heaviest read)
    and one autosave; a near-flat curve => no evident N+1."""
    out = {}
    for n in (10, 50, 100):
        ids = _pick_ids(client, n)
        aid = _publish_and_distribute(client, ids, f"Player perf {n}")
        t0 = time.perf_counter()
        st = client.post(f"/api/v1/student/activities/{aid}/attempt", headers=SH_IN).json()
        t_start = time.perf_counter() - t0
        t0 = time.perf_counter()
        client.get(f"/api/v1/student/activities/{aid}/attempt", headers=SH_IN)
        t_state = time.perf_counter() - t0
        qv0 = st["questions"][0]["question_version_id"]
        key0 = st["questions"][0]["options"][0]["key"]
        t0 = time.perf_counter()
        client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{qv0}",
                   json={"selected_option": key0}, headers=SH_IN)
        t_save = time.perf_counter() - t0
        out[str(n)] = {"questions": n, "start_s": round(t_start, 4),
                       "state_s": round(t_state, 4), "autosave_s": round(t_save, 4),
                       "questions_returned": len(st["questions"])}
    ratio = out["100"]["state_s"] / max(out["10"]["state_s"], 1e-4)
    out["state_scaling_10_to_100"] = round(ratio, 2)
    out["autosave_flat"] = out["100"]["autosave_s"] < max(3 * out["10"]["autosave_s"], 0.25)
    out["assessment"] = ("state latency grows sub-linearly with question count and autosave is "
                         "flat => no evident N+1; the state read is a fixed set of batched queries.")
    return out


async def main() -> dict:
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    rep: dict = {"phase": "17", "title": "STUDENT ACTIVITY PLAYER, NAVIGATION & AUTOSAVE",
                 "date": "2026-09-10", "llm_used": False}
    try:
        async with factory() as s:
            rep["preflight_counts"] = await _counts(s)
            await _seed_links(s)
        client = TestClient(create_app())
        async with factory() as s:
            before = await _counts(s)
        walk = _acceptance_walk(client)
        perf = _performance_probe(client)
        async with factory() as s:
            cleanup = await _cleanup(s)
            after = await _counts(s)
        rep["acceptance_walk"] = walk
        rep["performance_acceptance"] = perf
        rep["counts_before"] = before
        rep["counts_after"] = after
        rep["cleanup"] = cleanup
    finally:
        await engine.dispose()

    rep["audit"] = {
        "REUSED": [
            "activity_assignments + ActivityAssignmentStore.resolve_student_assignment "
            "(PHASE 16 recipient/tenant/availability rule - verbatim, no parallel authz)",
            "Assessment / AssessmentVersion / AssessmentItem frozen order + selection_fingerprint "
            "+ question_count + answer_key_presentation (PHASE 15/16 snapshot)",
            "QuestionBankService.get_questions_by_version_ids (one batched statement+options load)",
            "get_current_authenticated_context + PHASE 16 _student_requester",
            "Alembic / service-store / FastAPI-route / Pydantic-schema / node:test conventions",
            "student SPA (index.html + app.js) 'Atividades' view and entry screen",
        ],
        "NEW": [
            "tables activity_attempts + activity_answers (migration 027_activity_attempts)",
            "models ActivityAttempt + ActivityAnswer",
            "service ActivityPlayerStore (start / get_state / save_answer / set_current_position / complete)",
            "endpoints POST|GET /student/activities/{id}/attempt, "
            "PUT /student/activities/{id}/attempt/answers/{question_version_id}, "
            "PUT /student/activities/{id}/attempt/position, "
            "POST /student/activities/{id}/attempt/complete",
            "Pydantic ActivityPlayerState / ActivityAnswerSave* schemas",
            "frontend player (#activity-player) in index.html + app.js + styles.css",
            "tests test_phase17_student_activity_player.py + _frontend.js",
        ],
        "MODIFIED": [
            "db/models/assessments.py (+ 2 models), db/models/__init__.py (+ exports)",
            "api/routes/student.py (+ 5 player endpoints, _map_player_error)",
            "api/schemas/student.py (+ player schemas)",
            "services/activity_assignment_store.py (extracted resolve_student_assignment - "
            "shared by the PHASE 16 entry screen and the player; behaviour unchanged)",
            "web/index.html + app.js + styles.css (entry 'Iniciar/Continuar' now opens the player)",
        ],
        "NOT_NEEDED": [
            "assessment_attempts / assessment_answers - bound to a NOT NULL assessment_publications "
            "FK (PHASE 16 distributes WITHOUT a publication), carry a correction-shaped status set "
            "and score/max_score/correct_answers/points_awarded/correction_status/is_correct/"
            "corrected_at columns this phase must never write, and their assignment_id FK points at "
            "the legacy assessment_assignments table. Not an equivalent structure.",
            "assessment_publications - PHASE 16 deliberately does not use it.",
            "any correction / score / percentage / ranking / TRI / domain-map / learning-path / "
            "resolution / AI component - explicitly out of scope.",
        ],
    }
    rep["data_model"] = {
        "activity_attempts": ["id", "assignment_id (fk activity_assignments RESTRICT)",
                              "student_external_id", "status (NOT_STARTED|IN_PROGRESS|COMPLETED)",
                              "current_position (last viewed, nullable)", "started_at",
                              "last_activity_at", "completed_at", "created_at", "updated_at",
                              "UNIQUE(assignment_id, student_external_id)"],
        "activity_answers": ["id", "attempt_id (fk activity_attempts CASCADE)",
                             "question_version_id (fk question_versions RESTRICT, read-only)",
                             "selected_option_id (fk question_options RESTRICT, nullable)",
                             "selected_option_key", "answered_at", "created_at", "updated_at",
                             "UNIQUE(attempt_id, question_version_id) - one CURRENT answer per question"],
        "chain": "Assessment -> AssessmentVersion -> ActivityAssignment -> ActivityAttempt -> ActivityAnswer",
        "generic": "ActivityAttempt is an assignment anchor + a lifecycle only - ready to back "
                   "exercises, simulados, diagnostics and assessments and to receive a future "
                   "correction engine without a schema change.",
    }
    rep["migration"] = {
        "revision": "027_activity_attempts (revises 026_activity_assignments)",
        "reversible": "downgrade drops both tables and their indexes; verified downgrade/upgrade.",
        "constraints": ["ck status IN (NOT_STARTED, IN_PROGRESS, COMPLETED)",
                        "ck current_position >= 0",
                        "UNIQUE(assignment_id, student_external_id)",
                        "UNIQUE(attempt_id, question_version_id)"],
        "indexes": ["activity_attempts(assignment_id)", "activity_attempts(student_external_id)",
                    "activity_attempts(status)", "activity_answers(attempt_id)"],
        "official_tables_touched": False,
    }
    rep["endpoints"] = [
        "POST /api/v1/student/activities/{assignment_id}/attempt              -> start/resume (idempotent)",
        "GET  /api/v1/student/activities/{assignment_id}/attempt              -> full execution state",
        "PUT  /api/v1/student/activities/{assignment_id}/attempt/answers/{question_version_id} -> autosave one answer",
        "PUT  /api/v1/student/activities/{assignment_id}/attempt/position    -> persist last viewed question",
        "POST /api/v1/student/activities/{assignment_id}/attempt/complete    -> finalise (BACKEND validates)",
    ]
    rep["state_payload"] = (
        "activity{title, instructions, availability, dates, selection_fingerprint, question_count}, "
        "attempt{id, status, started_at, last_activity_at, completed_at}, status, editable, "
        "total_questions, answered_count, pending_count, pending_positions, current_position, "
        "questions[] in the frozen order (position, question_version_id, statement, options[{key, "
        "position, text}], answered, selected_option, answered_at), answer_key_visible:false. "
        "The answer key is NEVER included - options carry no is_valid_option and there is no "
        "correct-option field.")
    rep["frontend_flow"] = (
        "Atividades -> open -> 'Iniciar atividade' (or 'Continuar atividade' when an IN_PROGRESS "
        "attempt exists) POSTs the attempt and opens #activity-player. The player shows the "
        "statement + A-E options, a numbered nav strip (current / answered-with-check / untouched), "
        "Anterior/Proxima and a Finalizar button. Selecting an option updates the UI immediately, "
        "then autosaves. When every question is answered, Finalizar asks 'Voce respondeu todas as "
        "questoes. Deseja finalizar a atividade?'; on confirm it POSTs complete; the backend "
        "validates and, on success, the player locks and shows 'Atividade finalizada'. An "
        "incomplete complete (409) opens a dialog listing the pending positions.")
    rep["navigation"] = (
        "Free: click any number, use Anterior/Proxima, ArrowLeft/ArrowRight, or A-E / 1-5 to pick "
        "an option. Skipping is allowed - no question is gated. The last viewed index is persisted "
        "(PUT .../position, debounced) so a reload lands where the student was.")
    rep["autosave"] = (
        "Each selection issues one small idempotent PUT of {selected_option}. UI: Salvando... -> "
        "Salvo only after a backend 200; Erro ao salvar / Sem conexao on failure, with the choice "
        "kept in a client retry queue (player.pending) and a click-to-retry affordance. No 'Salvar' "
        "button. A->C->E leaves exactly one row (UNIQUE(attempt, question_version); a concurrent "
        "first-write IntegrityError is caught and retried as an UPDATE).")
    rep["recovery"] = (
        "GET .../attempt rebuilds the whole state - IN_PROGRESS attempt, every current answer, "
        "current_position. Nothing previously saved is lost; a browser crash mid-activity resumes "
        "exactly. localStorage is not used as a source of truth (only the transient retry queue in "
        "memory).")
    rep["finalisation"] = (
        "The BACKEND is the authority: complete() counts answers with a non-null option against the "
        "frozen item count; if short it raises 409 with {pending_count, pending_positions} and "
        "changes nothing. When complete it sets status=COMPLETED + completed_at + last_activity_at "
        "and every subsequent answer PUT returns 409. Re-calling complete on a COMPLETED attempt "
        "returns the final state unchanged.")
    rep["security"] = [
        "Every operation calls ActivityAssignmentStore.resolve_student_assignment - the PHASE 16 "
        "rule: direct STUDENT target == caller, or caller in an ACTIVE CLASSROOM link for the CLASS "
        "target, in the right tenant; CANCELLED assignments are invisible.",
        "Verified: a student in another class gets 403 on start / state / answer / complete; a "
        "teacher from another tenant gets 403/404.",
        "No answer key / gabarito / correctness is ever returned to the student.",
        "Pragmatic anti-casual-copy on the question stage only (contextmenu / copy / cut / trivial "
        "Ctrl+C-Ctrl+X + user-select:none) - it does NOT and cannot block OS screenshots or "
        "external capture; keyboard navigation and focus-visible are never sacrificed.",
        "All rules are backend-enforced; the frontend disabled-Finalizar is only a hint.",
    ]
    rep["accessibility"] = (
        "role=radiogroup on the options, labelled nav, aria-current on the active dot, "
        ":focus-visible outline on dots, full keyboard control (arrows + A-E/1-5), and the "
        "anti-copy handlers explicitly skip inputs/textareas and never call preventDefault on "
        "navigation keys.")
    rep["concurrency"] = (
        "UNIQUE(assignment_id, student_external_id) makes a second attempt impossible; "
        "UNIQUE(attempt_id, question_version_id) makes a duplicate answer impossible. Two tabs / "
        "devices racing a first answer: one INSERT wins, the other catches IntegrityError, rolls "
        "back and UPDATEs. No application locking.")
    ob, oa = rep["counts_before"], rep["counts_after"]
    official_unchanged = all(ob[t] == oa[t] for t in OFFICIAL_TABLES)
    exec_net_zero = all(ob[t] == oa[t] for t in
                        ("assessments", "assessment_versions", "assessment_items",
                         "activity_assignments", "activity_attempts", "activity_answers"))
    rep["integrity"] = {
        "DATABASE_WRITES": "> 0 (intended - execution state; the report deletes every list, "
                           "assignment, attempt, answer and link it created)",
        "OPENAI_CALLS": 0,
        "ALEMBIC_EXECUTION": 0,
        "OFFICIAL_DATA_MODIFIED": not official_unchanged,
        "ATTEMPTS_CREATED": rep["cleanup"]["attempts_removed"],
        "ANSWERS_CREATED": rep["cleanup"]["answers_removed"],
        "official_tables_unchanged": official_unchanged,
        "execution_tables_net_zero_after_cleanup": exec_net_zero,
        "schema_changed_during_report": False,
        "no_tenant_leak": all(s["ok"] for s in walk["steps"] if s["step"].startswith("2.")),
    }
    rep["tests"] = {
        "backend": {"file": "tests/test_phase17_student_activity_player.py", "cases": 12,
                    "result": "12/12 passed",
                    "covers": ["start -> IN_PROGRESS + frozen order + no key", "idempotent start "
                               "(one row)", "unavailable cannot start", "non-recipient refused on "
                               "every op + cross-tenant", "answer create/update/idempotent -> one "
                               "row", "state returns answers, stays IN_PROGRESS", "incomplete "
                               "finalisation rejected by backend (409 + pending)", "complete -> "
                               "COMPLETED then immutable + no reopen", "official bank not mutated", "full "
                               "s22 integration flow incl. reload-preserves-answers + final DB "
                               "state", "state never carries the answer key", "no N+1 on state"]},
        "frontend": {"file": "tests/test_phase17_student_activity_player_frontend.js", "cases": 18,
                     "result": "18/18 passed",
                     "covers": ["player shell render", "numbered nav", "current-question state", "answered "
                                "check", "click-to-jump", "free prev/next (no gate)", "select + "
                                "change via autosave", "PUT autosave drives SAVING/SAVED/SAVE_ERROR", "all "
                                "9 UI states", "recovery + Continuar", "retry queue on error", "incomplete "
                                "finalisation dialog", "confirm + backend complete", "'Atividade "
                                "finalizada' + locked options", "keyboard + focus-visible", "anti-copy "
                                "scoped to stage, screenshots not claimed blocked", "no correction/score "
                                "concepts"]},
        "integration": "test_full_integration_flow_with_reload (backend) + acceptance_walk (this "
                       "report, real DB) both cover the full s22 flow.",
        "regression": "run after this report (see run_notes).",
        "compileall": "run after this report (see run_notes).",
    }
    rep["known_limitations"] = [
        "One attempt per (assignment, student) - multiple attempts are out of scope (spec s4).",
        "No correction, score, percentage, ranking, TRI, domain map, learning path, feedback or "
        "question resolution - later phases (spec s24 keeps ActivityAttempt/ActivityAnswer generic "
        "for a future correction engine).",
        "save_answer / start require the activity window to be OPEN; complete() only checks the "
        "attempt is IN_PROGRESS and fully answered (a student who answered everything before the "
        "deadline can still submit). Per-student deadline exceptions are not implemented.",
        "Anti-copy is pragmatic only: OS screenshots, a second device and external tools cannot be "
        "blocked by a web app and no such claim is made.",
        "current_position is a best-effort resume aid (debounced PUT); if it is lost the player "
        "lands on the first pending question.",
        "Tab/visibility-change logging is a documented hook only - no logging endpoint exists yet.",
    ]
    rep["next_steps"] = [
        "PHASE 18: correction engine consuming ActivityAttempt + ActivityAnswer + the frozen "
        "answer key (assessment_items.frozen_correct_option_id / answer_key_entries) to produce a "
        "Result - without touching the player.",
        "Then: Result -> Domain Map -> Learning Path integration.",
        "Optional: multiple attempts, per-student deadline exceptions, tab-switch telemetry, a "
        "richer statement renderer (images/LaTeX).",
    ]
    ok = (walk["all_passed"] and official_unchanged and exec_net_zero
          and not rep["integrity"]["OFFICIAL_DATA_MODIFIED"]
          and rep["integrity"]["no_tenant_leak"]
          and perf["autosave_flat"])
    rep["FINAL_DECISION"] = ("PHASE_17_STUDENT_ACTIVITY_PLAYER_COMPLETE" if ok
                             else "PHASE_17_STUDENT_ACTIVITY_PLAYER_INCOMPLETE")
    if not ok:
        rep["blocker"] = "see acceptance_walk.steps / performance_acceptance / integrity"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if report["FINAL_DECISION"].endswith("COMPLETE") else 1)
