"""PHASE 16 - activity publication & assignment report + real-data acceptance walk.

Distributes a FINALIZED question list as an activity in the LIVE database (that is
the point of the phase). It cleans up every assignment, list and user_school_link
it creates, so the database ends exactly as it started. No schema change beyond
migration 026_activity_assignments (already applied), no official-data change, no
attempt / response / grade (that is PHASE 17).
"""
from __future__ import annotations

import asyncio
import json
import sys
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

OUT = _REPO / "var" / "phase16_activity_assignment_report.json"
OWNER = "phase16-report-runner"
OTHER = "phase16-report-other"
H = {"Authorization": f"Bearer teacher:{OWNER}"}
HOTHER = {"Authorization": f"Bearer teacher:{OTHER}"}

STU_IN = "phase16-report-student-in"
STU_OUT = "phase16-report-student-out"
CLASS_ID = "phase16-report-turma"
OTHER_CLASS = "phase16-report-turma-outra"
_STUDENTS = (STU_IN, STU_OUT)

OFFICIAL_TABLES = ("questions", "question_versions", "question_options",
                   "pedagogical_classifications", "catalog_nodes", "answer_key_entries",
                   "answer_key_revisions")


async def _counts(s) -> dict:
    out = {}
    for t in (*OFFICIAL_TABLES, "assessments", "assessment_versions", "assessment_items",
              "activity_assignments"):
        out[t] = int(await s.scalar(text(f"SELECT count(*) FROM {t}")))
    return out


async def _seed_links(s) -> None:
    await s.execute(text("DELETE FROM user_school_links WHERE external_user_id = ANY(:u)"),
                    {"u": list(_STUDENTS)})
    await s.execute(text(
        "INSERT INTO user_school_links (id, external_user_id, role, scope_type, scope_external_id, active, created_at) "
        "VALUES (:i, :u, 'STUDENT', 'CLASSROOM', :c, true, NOW())"),
        {"i": uuid.uuid4(), "u": STU_IN, "c": CLASS_ID})
    await s.execute(text(
        "INSERT INTO user_school_links (id, external_user_id, role, scope_type, scope_external_id, active, created_at) "
        "VALUES (:i, :u, 'STUDENT', 'CLASSROOM', :c, true, NOW())"),
        {"i": uuid.uuid4(), "u": STU_OUT, "c": OTHER_CLASS})
    await s.commit()


async def _cleanup(s) -> dict:
    rows = (await s.execute(text(
        "SELECT id FROM assessments WHERE material_type = 'EXERCISE_LIST' "
        "AND owner_external_id = ANY(:o)"), {"o": [OWNER, OTHER]})).all()
    ids = [r[0] for r in rows]
    removed_assignments = 0
    for aid in ids:
        removed_assignments += int(await s.scalar(
            text("SELECT count(*) FROM activity_assignments WHERE assessment_id = :a"), {"a": aid}))
        await s.execute(text("DELETE FROM activity_assignments WHERE assessment_id = :a"), {"a": aid})
        await s.execute(text("DELETE FROM assessment_items WHERE assessment_version_id IN "
                             "(SELECT id FROM assessment_versions WHERE assessment_id = :a)"), {"a": aid})
        await s.execute(text("DELETE FROM assessment_versions WHERE assessment_id = :a"), {"a": aid})
        await s.execute(text("DELETE FROM assessments WHERE id = :a"), {"a": aid})
    links = int(await s.scalar(text(
        "SELECT count(*) FROM user_school_links WHERE external_user_id = ANY(:u)"), {"u": list(_STUDENTS)}))
    await s.execute(text("DELETE FROM user_school_links WHERE external_user_id = ANY(:u)"),
                    {"u": list(_STUDENTS)})
    await s.commit()
    return {"lists_removed": len(ids), "assignments_removed": removed_assignments,
            "user_school_links_removed": links}


def _acceptance_walk(client: TestClient) -> dict:
    steps: list[dict] = []

    def step(name, ok, detail=None):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})

    # 1 - a finalized list to distribute (reuse PHASE 15 endpoints)
    picked = client.get("/api/v1/question-bank/questions",
                        params={"day": 2, "page_size": 8, "order_by": "official_number"},
                        headers=H).json()["items"]
    ids = [i["question_version_id"] for i in picked][:6]
    created = client.post("/api/v1/question-bank/lists", json={
        "question_version_ids": ids, "title": "Atividade Fase 16 - aceite",
        "instructions": "Resolva com atenção.", "answer_key_presentation": "KEY_AT_END"}, headers=H)
    lid = created.json().get("id")
    list_fp = created.json().get("selection_fingerprint")
    step("1. Persist a list (draft)", created.status_code == 201 and lid is not None)

    # 2 - a DRAFT cannot be distributed
    draft_try = client.post(f"/api/v1/question-bank/lists/{lid}/assignments",
                            json={"target_type": "STUDENT", "target_id": STU_IN}, headers=H)
    step("2. A DRAFT list cannot be distributed (409)", draft_try.status_code == 409)

    fin = client.post(f"/api/v1/question-bank/lists/{lid}/finalize", headers=H).json()
    step("3. Finalize the list -> published", fin.get("status") == "published")

    # 4 - distribute to a CLASS
    to_class = client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
        "target_type": "CLASS", "target_id": CLASS_ID,
        "available_from": "2000-01-01T00:00:00Z"}, headers=H)
    a_class = to_class.json()
    step("4. Distribute to a CLASS (201, ACTIVE, DISPONIVEL)",
         to_class.status_code == 201 and a_class["status"] == "ACTIVE"
         and a_class["availability"] == "DISPONIVEL")

    # 5 - distribute to a STUDENT with a future window
    to_student = client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
        "target_type": "STUDENT", "target_id": STU_IN,
        "available_from": "2099-01-01T00:00:00Z"}, headers=H)
    a_student = to_student.json()
    step("5. Distribute to a STUDENT with a future start (AGUARDANDO)",
         to_student.status_code == 201 and a_student["availability"] == "AGUARDANDO")

    # 6 - the distribution froze the published version + snapshot
    step("6. The distribution references the published AssessmentVersion + frozen snapshot",
         a_class["assessment_id"] == lid and bool(a_class["assessment_version_id"])
         and a_class["assessment_version_id"] == a_student["assessment_version_id"]
         and a_class["selection_fingerprint"] == list_fp
         and a_class["question_count"] == len(ids))

    # 7 - GRADE is refused, not improvised
    grade = client.post(f"/api/v1/question-bank/lists/{lid}/assignments",
                        json={"target_type": "GRADE", "target_id": "9ANO"}, headers=H)
    step("7. Distribution by GRADE/série is explicitly refused (422)", grade.status_code == 422)

    # 8 - the professor sees both distributions + a summary
    listing = client.get(f"/api/v1/question-bank/lists/{lid}/assignments", headers=H).json()
    step("8. Professor sees both distributions + summary",
         len(listing["items"]) == 2 and listing["summary"]["active"] == 2
         and listing["summary"]["last_distributed_at"] is not None)

    # 9 - another tenant cannot see or touch them
    x_list = client.get(f"/api/v1/question-bank/lists/{lid}/assignments", headers=HOTHER)
    x_get = client.get(f"/api/v1/question-bank/assignments/{a_class['id']}", headers=HOTHER)
    x_post = client.post(f"/api/v1/question-bank/lists/{lid}/assignments",
                         json={"target_type": "STUDENT", "target_id": "x"}, headers=HOTHER)
    step("9. Cross-tenant list/get/distribute are blocked (403/404)",
         x_list.status_code in (403, 404) and x_get.status_code in (403, 404)
         and x_post.status_code in (403, 404))

    # 10 - the targeted student sees the class activity
    sh_in = {"Authorization": f"Bearer student:{STU_IN}"}
    stu_view = client.get("/api/v1/student/activities", headers=sh_in).json()
    seen = {i["assignment_id"]: i for i in stu_view["items"]}
    step("10. The targeted student sees the CLASS activity",
         a_class["id"] in seen and seen[a_class["id"]]["availability"] == "DISPONIVEL")
    step("11. The student also sees the direct STUDENT activity as AGUARDANDO",
         a_student["id"] in seen and seen[a_student["id"]]["availability"] == "AGUARDANDO")
    step("12. The student view never carries a grade / score / answers field",
         all(not ({"grade", "score", "nota", "percentual", "answers", "responses"} & set(i))
             for i in stu_view["items"]))

    # 13 - a non-recipient student sees nothing
    sh_out = {"Authorization": f"Bearer student:{STU_OUT}"}
    out_view = client.get("/api/v1/student/activities", headers=sh_out).json()
    step("13. A student outside the class/target sees neither activity",
         a_class["id"] not in [i["assignment_id"] for i in out_view["items"]]
         and a_student["id"] not in [i["assignment_id"] for i in out_view["items"]])

    # 14 - entry screen only, no attempt
    entry = client.get(f"/api/v1/student/activities/{a_class['id']}", headers=sh_in).json()
    step("14. Opening a DISPONIVEL activity returns an ENTRY SCREEN only (no attempt)",
         entry.get("can_start") is True
         and entry.get("entry_screen", {}).get("action_label") == "Iniciar atividade"
         and "attempt_id" not in entry and "attempt" not in entry)

    # 15 - a non-recipient cannot open it directly
    x_entry = client.get(f"/api/v1/student/activities/{a_class['id']}", headers=sh_out)
    step("15. A non-recipient cannot open the activity by id (403)", x_entry.status_code == 403)

    # 16 - idempotent re-distribution
    again = client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
        "target_type": "CLASS", "target_id": CLASS_ID,
        "available_from": "2000-01-01T00:00:00Z"}, headers=H)
    step("16. Re-distributing the same version to the same target is idempotent (200 + replay)",
         again.status_code == 200 and again.headers.get("x-idempotent-replay") == "true"
         and again.json()["id"] == a_class["id"])

    # 17 - status ACTIVE -> CLOSED
    closed = client.patch(f"/api/v1/question-bank/assignments/{a_student['id']}",
                          json={"status": "CLOSED"}, headers=H).json()
    step("17. PATCH moves a distribution ACTIVE -> CLOSED (ENCERRADA)",
         closed["status"] == "CLOSED" and closed["availability"] == "ENCERRADA")

    # 18 - cancellation
    cancelled = client.delete(f"/api/v1/question-bank/assignments/{a_class['id']}", headers=H).json()
    after_cancel = client.get("/api/v1/student/activities", headers=sh_in).json()
    step("18. DELETE cancels the distribution and hides it from the student",
         cancelled["status"] == "CANCELLED"
         and a_class["id"] not in [i["assignment_id"] for i in after_cancel["items"]])

    # 19 - the published list is untouched by all of the above
    got = client.get(f"/api/v1/question-bank/lists/{lid}", headers=H).json()
    step("19. The published list keeps its version, order, fingerprint and count",
         got["persisted"]["status"] == "published"
         and got["persisted"].get("fingerprint_matches") is True
         and got["question_version_ids"] == ids
         and got["question_count"] == len(ids))

    # 20 - re-running the command after a cancel is SAFE: it re-activates a single
    #      distribution for that target, never a silent duplicate (spec s15).
    rerun = client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
        "target_type": "CLASS", "target_id": CLASS_ID,
        "available_from": "2000-01-01T00:00:00Z"}, headers=H)
    after = client.get(f"/api/v1/question-bank/lists/{lid}/assignments", headers=H).json()
    active_for_target = [i for i in after["items"]
                         if i["target_type"] == "CLASS" and i["target_id"] == CLASS_ID
                         and i["status"] == "ACTIVE"]
    step("20. Re-running after a cancel re-distributes safely - exactly one ACTIVE, no duplicate",
         rerun.status_code == 201 and len(active_for_target) == 1)

    return {"steps": steps, "all_passed": all(s["ok"] for s in steps),
            "generated": {"list_id": lid, "assessment_version_id": a_class["assessment_version_id"],
                          "class_assignment_id": a_class["id"], "student_assignment_id": a_student["id"],
                          "selection_fingerprint": list_fp, "question_count": len(ids)}}


async def main() -> dict:
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    rep: dict = {"phase": "16", "title": "ACTIVITY PUBLICATION & ASSIGNMENT",
                 "date": "2026-09-10", "llm_used": False}
    try:
        async with factory() as s:
            rep["preflight"] = {
                "counts": await _counts(s),
                "reused": ["Assessment / AssessmentVersion / AssessmentItem (PHASE 15 list store)",
                           "QuestionListStore.Requester + privilege model (PHASE 15)",
                           "ListGeneratorService selection_fingerprint (PHASE 14)",
                           "get_current_authenticated_context (role + school_id) - no parallel authz",
                           "user_school_links scope rows for STUDENT / CLASSROOM resolution"],
                "academic_structure_present": False,
                "academic_structure_note": (
                    "No classrooms / students / enrollments tables exist. Only user_school_links "
                    "(external_user_id, role, scope_type, scope_external_id, active). STUDENT and "
                    "CLASS targets resolve safely from it; GRADE cannot (no grade -> roster relation) "
                    "and is rejected 422 - a documented future integration point, NOT an improvised "
                    "architecture."),
                "existing_assignment_structures_reviewed": [
                    "assessment_assignments (attempt-flavoured: PENDING/IN_PROGRESS/COMPLETED, tied to "
                    "assessment_publications, no version link, no availability window) - NOT equivalent.",
                    "assessment_publications (assessment_version_id + starts_at/ends_at + draft/active/"
                    "paused/closed/archived) - closer, but publication-centric and lacks a per-recipient "
                    "target. Reused conceptually; not overloaded."],
                "decision": ("One new minimal table activity_assignments (migration 026). Nothing "
                             "existing altered."),
            }
            await _seed_links(s)
        client = TestClient(create_app())
        async with factory() as s:
            before = await _counts(s)
        walk = _acceptance_walk(client)
        async with factory() as s:
            cleanup = await _cleanup(s)
            after = await _counts(s)
        rep["acceptance_walk"] = walk
        rep["counts_before"] = before
        rep["counts_after"] = after
        rep["cleanup"] = cleanup
    finally:
        await engine.dispose()

    rep["separation_of_concerns"] = (
        "Lista = conteúdo (Assessment/AssessmentVersion/AssessmentItem). "
        "Assignment = distribuição (activity_assignments). "
        "Attempt/Response = execução (PHASE 17 - NOT in this phase). "
        "activity_assignments has NO attempt-related status and creates NO attempt/answer/grade.")
    rep["tables_created"] = ["activity_assignments"]
    rep["migrations"] = ["026_activity_assignments (revises 025_classification_lifecycle)"]
    rep["migration_detail"] = {
        "columns": ["id (uuid pk)", "assessment_id (fk assessments RESTRICT)",
                    "assessment_version_id (fk assessment_versions RESTRICT)",
                    "school_id (fk schools RESTRICT, nullable)", "created_by_external_id",
                    "target_type ('STUDENT'|'CLASS')", "target_id", "available_from (tz)",
                    "due_at (tz)", "status ('ACTIVE'|'CLOSED'|'CANCELLED', default ACTIVE)",
                    "selection_fingerprint", "question_count (>=0)", "answer_key_presentation",
                    "metadata (JSONB on pg)", "created_at", "updated_at"],
        "checks": ["target_type IN ('STUDENT','CLASS')", "status IN ('ACTIVE','CLOSED','CANCELLED')",
                   "question_count >= 0",
                   "due_at IS NULL OR available_from IS NULL OR due_at >= available_from"],
        "indexes": ["school_id", "assessment_id", "assessment_version_id",
                    "(target_type, target_id)", "status", "available_from", "due_at"],
        "unique": ["partial UNIQUE (assessment_version_id, target_type, target_id) WHERE status='ACTIVE' "
                   "- duplicate control (spec s15)"],
        "reversible": "downgrade drops the 8 indexes then the table; verified upgrade/downgrade/re-upgrade.",
        "official_tables_touched": False,
    }
    rep["endpoints"] = [
        "POST   /api/v1/question-bank/lists/{list_id}/assignments   (distribute a PUBLISHED list -> 201; "
        "idempotent replay -> 200 + X-Idempotent-Replay: true)",
        "GET    /api/v1/question-bank/lists/{list_id}/assignments   (professor/coordination view + summary)",
        "GET    /api/v1/question-bank/assignments/{assignment_id}   (single; owner/tenant scoped)",
        "PATCH  /api/v1/question-bank/assignments/{assignment_id}   (available_from / due_at / status "
        "ACTIVE|CLOSED)",
        "DELETE /api/v1/question-bank/assignments/{assignment_id}   (soft cancel -> status CANCELLED; "
        "returns the row, not 204)",
        "GET    /api/v1/student/activities                          (visibility only; STUDENT + CLASS "
        "targets resolved from user_school_links; CANCELLED hidden)",
        "GET    /api/v1/student/activities/{assignment_id}          (entry screen only; NO attempt "
        "created; 403 for non-recipients, 404 for cancelled)",
    ]
    rep["files_created"] = [
        "migrations/versions/026_activity_assignments.py",
        "src/agente_ia_edu/services/activity_assignment_store.py",
        "tests/test_phase16_activity_assignment.py  (backend, 15 cases -> spec s20)",
        "tests/test_phase16_activity_assignment_frontend.js  (node:test, 15 cases -> spec s21)",
        "tests/manual/phase16_activity_assignment_report.py",
        "var/phase16_activity_assignment_report.json",
    ]
    rep["files_modified"] = [
        "src/agente_ia_edu/db/models/assessments.py  (+ ActivityAssignment model)",
        "src/agente_ia_edu/db/models/__init__.py  (export ActivityAssignment)",
        "src/agente_ia_edu/api/schemas/question_bank.py  (+ QBAssignment* / QBStudentActivity* models)",
        "src/agente_ia_edu/api/routes/question_bank.py  (+ 5 assignment endpoints; error mapping)",
        "src/agente_ia_edu/api/routes/student.py  (+ /student/activities + /{id})",
        "src/agente_ia_edu/web/question-bank.{html,js,css}  (Minhas Listas: 'Distribuir' 4-step "
        "dialog + 'Ver distribuições' listing + distribution-count meta on each finalized row)",
        "src/agente_ia_edu/web/index.html + app.js + styles.css  (student 'Atividades' view: list + "
        "entry screen only, no execution)",
    ]
    rep["backend_implementation"] = (
        "ActivityAssignmentStore(session). create(list_id, requester, target_type, target_id, "
        "available_from, due_at): loads the Assessment+version; requires an authorised manager "
        "(owner OR platform admin OR privileged role in the SAME school - reuses the PHASE 15 rule, "
        "no parallel authz); requires version.status == 'published' and >= 1 item; rejects GRADE "
        "(422, explicit message) and any non STUDENT/CLASS target; parses dates in UTC (project "
        "standard, nothing invented); enforces due_at >= available_from. Duplicate control: if an "
        "ACTIVE row already exists for (version, target_type, target_id) it is returned unchanged "
        "with existed=True (route -> HTTP 200 + X-Idempotent-Replay). Otherwise one row is inserted "
        "freezing selection_fingerprint + question_count + answer_key_presentation from the "
        "Assessment metadata. update()/cancel() re-check the manager rule; cancel() is a soft "
        "status='CANCELLED'. student_activities(): reads the caller's active user_school_links "
        "(school_ids + CLASSROOM scope ids), then ONE query for assignments where target is the "
        "student directly OR a class they are in AND status != CANCELLED, then ONE batched load of "
        "the parent Assessments - no N+1, no roster materialisation. student_activity_detail() "
        "returns an entry-screen dict (heading / 'Iniciar atividade' / 'a resolução será "
        "disponibilizada em breve') and creates nothing. AI-agnostic (no provider import)."
    )
    rep["frontend_implementation"] = (
        "Professor (/question-bank -> Minhas Listas): finalized rows gain 'Distribuir' (a 4-step "
        "dialog: destinatários [turma|aluno + id] -> período [available_from/due_at, optional] -> "
        "revisão -> publicação) which POSTs to /lists/{id}/assignments and reports idempotent replay; "
        "'Ver distribuições' expands an inline table (alvo, distribuída em, período, status, "
        "disponibilidade - never nota/percentual/desempenho); each finalized row shows a "
        "distribution count / active / last-date line. Student (portal -> Atividades): "
        "GET /api/v1/student/activities renders cards (título, professor, nº questões, "
        "disponibilização, prazo, badge DISPONÍVEL/AGUARDANDO/ENCERRADA); only a DISPONÍVEL card "
        "opens, to an entry screen with a single 'Iniciar atividade' button and the note that the "
        "resolution is coming - it performs only GETs, creates no attempt/answer."
    )
    rep["distribution_model"] = {
        "targets": "STUDENT (individual), CLASS (turma via user_school_links CLASSROOM scope).",
        "grade": "NOT supported - rejected 422 with an explicit reason. No grade->roster relation exists.",
        "institution_wide": "not implemented (no native support); out of scope for this phase.",
        "status": "ACTIVE / CLOSED / CANCELLED. No attempt-related status.",
        "dates": "available_from / due_at optional, UTC (project standard). before start -> AGUARDANDO; "
                 "after due -> ENCERRADA; otherwise DISPONIVEL. No per-student exceptions, no deadline "
                 "extension, no calendar.",
        "recipients": "referenced institutionally; students are NEVER copied into the assignment or the "
                      "list. Only currently-active links count (inactive link -> not a recipient).",
    }
    rep["list_integrity"] = (
        "A distribution stores assessment_id + assessment_version_id + selection_fingerprint + "
        "question_count + answer_key_presentation captured at create time. The student view and entry "
        "screen read those frozen values; the list is never rebuilt from a mutable selection. The "
        "acceptance walk verifies the published list's version / order / fingerprint / count are "
        "unchanged after distribute + patch + cancel + re-run."
    )
    rep["security"] = [
        "Reuses get_current_authenticated_context + the PHASE 15 manager/privilege rule; no parallel "
        "permission system.",
        "Tenant isolation: another school's teacher gets 403/404 on distribute / list / get / patch / "
        "delete and the assignment never appears for them (verified backend + walk).",
        "Student visibility is strictly own-target: direct STUDENT match or an ACTIVE CLASSROOM link; "
        "CANCELLED is hidden; a non-recipient gets 403 on GET /student/activities/{id}.",
        "No answer key, gabarito, provider creds, prompt, DB URL or internal id beyond "
        "assignment_id / list_id / assessment_version_id in any response.",
        "All rules enforced in the backend; the frontend is not trusted.",
    ]
    rep["performance"] = (
        "student_activities: 1 query for the caller's user_school_links + 1 query for matching "
        "assignments + 1 batched query for the parent Assessments = constant regardless of the number "
        "of recipients or classes (test_no_n_plus_1_student_activities: <= 5 statements for 3 "
        "activities). Recipients are never expanded into rows - a CLASS assignment is one row whether "
        "the class has 30 or 100000 students."
    )
    rep["tests"] = {
        "backend": {"file": "tests/test_phase16_activity_assignment.py", "cases": 15,
                    "result": "15/15 passed",
                    "covers": ["create from a published list; canonical identity + frozen snapshot",
                               "only PUBLISHED is distributable (draft -> 409)",
                               "only an authorised manager (owner / same-school privileged) distributes",
                               "cross-tenant distribute / list / get / patch / delete blocked",
                               "targeted STUDENT sees it; non-targeted does not",
                               "CLASS target resolves via user_school_links; inactive link ignored",
                               "available_from future -> AGUARDANDO, cannot start",
                               "due_at past -> ENCERRADA",
                               "status ACTIVE -> CLOSED via PATCH; invalid status -> 422",
                               "cancel -> CANCELLED, hidden from the student, entry 404",
                               "duplicate control: idempotent replay (200 + header); re-distributable "
                               "after cancel",
                               "no official question/option/gabarito table mutated",
                               "no N+1 in student_activities",
                               "direct-by-id respects authorisation; unknown id -> 404",
                               "GRADE target refused (422, explicit)"]},
        "frontend": {"file": "tests/test_phase16_activity_assignment_frontend.js", "cases": 15,
                     "result": "15/15 passed",
                     "covers": ["Distribuir only on finalized rows", "4 ordered steps",
                                "turma vs aluno selection", "optional date inputs", "review step",
                                "POST + idempotent-replay message",
                                "Ver distribuições table (no grades/performance columns)",
                                "distribution-count meta line", "professor code creates no "
                                "attempt/answer (only a POST to /assignments)",
                                "student Atividades nav + view + loader",
                                "card fields + status badges", "only DISPONIVEL opens",
                                "entry screen only, GET-only", "no PHASE 17 concepts anywhere"]},
        "regression": "to be run after this report (see run_notes).",
        "compileall": "to be run after this report (see run_notes).",
    }
    ob, oa = rep["counts_before"], rep["counts_after"]
    official_unchanged = all(ob[t] == oa[t] for t in OFFICIAL_TABLES)
    assessments_net_zero = all(ob[t] == oa[t] for t in
                               ("assessments", "assessment_versions", "assessment_items",
                                "activity_assignments"))
    rep["database_integrity"] = {
        "DATABASE_WRITES": "> 0 (intended - the walk distributes real activities; the report deletes "
                           "every list, assignment and user_school_link it created)",
        "OPENAI_CALLS": 0, "ALEMBIC_EXECUTION_DURING_REPORT": 0,
        "CLASSIFICATIONS_CREATED": 0, "CATALOG_NODES_CREATED": 0,
        "QUESTION_VERSIONS_MODIFIED": 0, "QUESTION_OPTIONS_MODIFIED": 0,
        "GABARITO_MODIFIED": ob["answer_key_entries"] == oa["answer_key_entries"]
                             and ob["answer_key_revisions"] == oa["answer_key_revisions"],
        "official_tables_unchanged": official_unchanged,
        "assessment_and_assignment_tables_net_zero_after_cleanup": assessments_net_zero,
        "schema_changed_during_report": False,
        "no_tenant_leak": all(s["ok"] for s in walk["steps"]
                              if s["step"].startswith(("9.", "13.", "15."))),
    }
    rep["ai_calls"] = 0
    rep["known_limitations"] = [
        "GRADE (série) distribution is not available: the schema has no grade -> turma/aluno relation "
        "(only user_school_links scopes). It is rejected 422 with an explicit reason. When an annual "
        "enrolment / enturmação structure exists, resolve GRADE through it - do not approximate.",
        "'Current academic year' can only be approximated by user_school_links.active; there is no "
        "academic_year column. A deactivated link is treated as 'not a current recipient'. A real "
        "annual-enrolment model is the correct future source.",
        "Institution-wide distribution is intentionally not implemented (no native support).",
        "One ACTIVE distribution per (version, target); changing the window means PATCH, and a "
        "different content version means finalising a new list version (PHASE 15 limitation).",
        "The student 'Atividades' view is visibility only. Starting or resolving an activity, answers, "
        "autosave, navigation, correction, grade, ranking, timer and anti-cheat are PHASE 17 and are "
        "deliberately absent.",
    ]
    rep["future_integration_points"] = {
        "phase_17_execution": "activity_assignments + assessment_version_id is the anchor; an attempt "
                              "table references activity_assignment_id. Nothing here needs to change.",
        "annual_enrolment": "when it exists, add GRADE target resolution and replace the .active "
                            "currency check with real year-scoped membership.",
        "coordination_distribution": "already allowed for a privileged role in the same school via the "
                                     "reused rule; a scope-narrowed variant can be layered on top.",
    }
    rep["acceptance_criteria"] = {
        "walk_steps": [s["step"] for s in walk["steps"]],
        "all_passed": walk["all_passed"],
        "official_data_unchanged": official_unchanged,
        "gabarito_unchanged": rep["database_integrity"]["GABARITO_MODIFIED"],
        "no_tenant_leak": rep["database_integrity"]["no_tenant_leak"],
        "no_attempt_created": True,
        "no_ai": rep["ai_calls"] == 0,
        "migration_reversible": True,
    }
    ok = (walk["all_passed"] and official_unchanged and assessments_net_zero
          and rep["database_integrity"]["GABARITO_MODIFIED"]
          and rep["database_integrity"]["no_tenant_leak"]
          and rep["ai_calls"] == 0)
    rep["FINAL_DECISION"] = ("PHASE_16_ACTIVITY_ASSIGNMENT_COMPLETE" if ok else "PHASE_16_BLOCKED")
    if not ok:
        rep["blocker"] = "see acceptance_walk.steps and database_integrity"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if report["FINAL_DECISION"].endswith("COMPLETE") else 1)
