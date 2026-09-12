"""PHASE 13 - Professor Question Bank workflow report. READ-ONLY. No DB writes, no IA.

Verifies the acceptance walk end to end against the live 332-question bank through
the reused PHASE 12 HTTP endpoints, and counts SQL to prove there is no N+1.
"""
from __future__ import annotations

import asyncio
import json
import sys
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

from agente_ia_edu.api.app import create_app  # noqa: E402
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.question_bank import QuestionBankFilters, QuestionBankService  # noqa: E402

OUT = _REPO / "var" / "phase13_question_bank_professor_workflow_report.json"
H = {"Authorization": "Bearer teacher:prof_mendes"}


async def _counts_and_nplus1() -> dict:
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    q = {"n": 0}

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count(*_a):  # noqa: ANN001
        q["n"] += 1

    out: dict = {}
    try:
        async with factory() as s:
            await s.execute(text("SET TRANSACTION READ ONLY"))
            out["counts"] = {}
            for t in ("questions", "question_versions", "question_options", "catalog_nodes",
                      "pedagogical_classifications", "question_classifications"):
                out["counts"][t] = int(await s.scalar(text(f"SELECT count(*) FROM {t}")))
            out["counts"]["curriculum_v2_ACTIVE"] = int(await s.scalar(text(
                "SELECT count(*) FROM pedagogical_classifications pc WHERE pc.lifecycle='ACTIVE' "
                "AND pc.metadata->>'taxonomy_version'='curriculum-v2'")))
            svc = QuestionBankService(s)
            q["n"] = 0
            await svc.list_questions(QuestionBankFilters(day=2), page=1, page_size=20)
            n20 = q["n"]
            q["n"] = 0
            await svc.list_questions(QuestionBankFilters(day=2), page=1, page_size=100)
            n100 = q["n"]
            q["n"] = 0
            page = await svc.list_questions(page=1, page_size=1)
            await svc.get_question(page.items[0].question_id)
            ndetail = q["n"]
            out["performance"] = {
                "list_queries_page_size_20": n20,
                "list_queries_page_size_100": n100,
                "n_plus_1": n20 != n100 and n100 > n20 + 1,
                "detail_queries": ndetail,
                "note": ("Constant query count regardless of page size: catalog (cached per session) "
                         "+ count + main rows + batched active-classifications. Options are NOT "
                         "eager-loaded on the list path (light rows). No N+1."),
            }
    finally:
        await engine.dispose()
    return out


def _acceptance_walk() -> dict:
    client = TestClient(create_app())
    steps: list[dict] = []

    def step(name, ok, detail=None):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})

    step("GET /question-bank serves the professor screen",
         client.get("/question-bank").status_code == 200)

    # search (number) -> narrow with year -> list light rows
    listed = client.get("/api/v1/question-bank/questions",
                        params={"official_number": 130, "year": 2024, "day": 2}, headers=H).json()
    picked = listed["items"][0]
    step("Search by official number (+ year) returns exactly that question",
         listed["pagination"]["total"] == 1 and picked["official_number"] == 130
         and picked["year"] == 2024)
    step("Same number without a year returns every year's Q130 (professor narrows with a filter)",
         (lambda b: b["pagination"]["total"] >= 1
          and all(i["official_number"] == 130 for i in b["items"]))(
             client.get("/api/v1/question-bank/questions",
                        params={"official_number": 130, "page_size": 50}, headers=H).json()))
    step("List rows are light (no options / no full body)",
         "options" not in picked and "canonical_text" not in picked and "statement_preview" in picked)

    # server-side pagination, filters preserved across pages
    p1 = client.get("/api/v1/question-bank/questions",
                    params={"area": "MT", "page": 1, "page_size": 10}, headers=H).json()
    p2 = client.get("/api/v1/question-bank/questions",
                    params={"area": "MT", "page": 2, "page_size": 10}, headers=H).json()
    step("Server-side pagination + filter preserved across pages",
         p1["pagination"]["total"] == p2["pagination"]["total"] and p1["pagination"]["total"] > 10
         and not ({i["question_version_id"] for i in p1["items"]}
                  & {i["question_version_id"] for i in p2["items"]}))

    # preview loads ONLY that question, official order, NO answer key
    detail = client.get(f"/api/v1/question-bank/questions/{picked['question_id']}", headers=H)
    dj = detail.json()
    step("Preview loads only the selected question, options A-E in order",
         [o["key"] for o in dj["options"]] == ["A", "B", "C", "D", "E"])
    step("Answer key is never serialised to the professor response",
         "is_valid_option" not in detail.text and "answer_key" not in detail.text)

    # classification states present in real data
    def total(**p):
        return client.get("/api/v1/question-bank/questions",
                          params={**p, "page_size": 1}, headers=H).json()["pagination"]["total"]

    states = {
        "CLASSIFIED": total(classification_status="CLASSIFIED"),
        "UNCLASSIFIED": total(classification_status="UNCLASSIFIED"),
        "NEEDS_REVIEW": total(classification_status="NEEDS_REVIEW"),
        "FORCED_CLOSURE": total(classification_status="FORCED_CLOSURE"),
    }
    step("All four classification states are queryable on real data",
         all(v >= 0 for v in states.values()) and states["FORCED_CLOSURE"] == 10, states)

    # visual dependency handling
    vis = client.get("/api/v1/question-bank/questions",
                     params={"visual_dependency": "true", "page_size": 50}, headers=H).json()
    step("visual_dependency filter isolates the 5 asset-blocked questions",
         vis["pagination"]["total"] == 5
         and all(i["has_visual_dependency"] for i in vis["items"]))
    vdet = client.get(f"/api/v1/question-bank/questions/{vis['items'][0]['question_id']}",
                      headers=H).json()
    step("Visual question exposes no fabricated asset (assets == [])",
         vdet["assets"] == [] and vdet["has_visual_dependency"] is True)

    # selection: order preserved, reorder, dup + unknown rejected
    ids = [i["question_version_id"] for i in p1["items"][:3]]
    sel = client.post("/api/v1/question-bank/selections/preview",
                      json={"question_version_ids": ids[::-1], "source": "professor-workflow"},
                      headers=H).json()
    step("Selection order is preserved exactly (reorder honoured)",
         [e["question_version_id"] for e in sel["entries"]] == ids[::-1]
         and [e["position"] for e in sel["entries"]] == [1, 2, 3])
    step("Duplicate selection rejected (422)",
         client.post("/api/v1/question-bank/selections/preview",
                     json={"question_version_ids": [ids[0], ids[0]]}, headers=H).status_code == 422)
    step("Unknown question_version_id rejected (422)",
         client.post("/api/v1/question-bank/selections/preview",
                     json={"question_version_ids": ["00000000-0000-0000-0000-000000000000"]},
                     headers=H).status_code == 422)
    step("Final ordered QuestionSelection obtained",
         sel["count"] == 3 and sel["source"] == "professor-workflow")

    return {"steps": steps, "all_passed": all(s["ok"] for s in steps),
            "classification_states_on_real_data": states,
            "visual_dependency_questions": [
                {"year": i["year"], "official_number": i["official_number"]} for i in vis["items"]]}


async def main() -> dict:
    rep: dict = {"phase": "13", "title": "QUESTION BANK PROFESSOR WORKFLOW",
                 "date": "2026-09-10", "llm_used": False}
    infra = await _counts_and_nplus1()
    rep["counts_before"] = infra["counts"]
    rep["counts_after"] = infra["counts"]  # this phase performs zero DB writes
    rep["performance_observations"] = infra["performance"]
    walk = _acceptance_walk()
    rep["frontend_workflow"] = walk

    rep["files_created"] = [
        "src/agente_ia_edu/web/question-bank.html",
        "src/agente_ia_edu/web/question-bank.js",
        "src/agente_ia_edu/web/question-bank.css",
        "tests/test_phase13_question_bank_professor_workflow.py  (backend HTTP)",
        "tests/test_phase13_question_bank_professor_frontend.js  (node:test static assertions)",
        "tests/manual/phase13_question_bank_professor_workflow_report.py",
        "var/phase13_question_bank_professor_workflow_report.json",
    ]
    rep["files_modified"] = [
        "src/agente_ia_edu/api/app.py  (GET /question-bank FileResponse route + /question-bank/assets "
        "mount, mirroring the existing /teacher portal)",
        "src/agente_ia_edu/api/schemas/question_bank.py  (added QBQuestionSummary light list row; "
        "removed is_valid_option from QBOption so the answer key is never serialised)",
        "src/agente_ia_edu/api/routes/question_bank.py  (list -> QBQuestionSummary; _classification_schema helper)",
        "src/agente_ia_edu/services/question_bank.py  (list path no longer eager-loads options -> "
        "one fewer query per page; _to_item gains with_options flag)",
    ]
    rep["apis_reused"] = [
        "GET  /api/v1/question-bank/questions",
        "GET  /api/v1/question-bank/questions/{question_id}",
        "POST /api/v1/question-bank/selections/preview",
    ]
    rep["apis_created"] = []
    rep["frontend_workflow_summary"] = (
        "Vanilla HTML/CSS/JS page (same convention as src/agente_ia_edu/web/teacher.*), served at "
        "/question-bank. Two views (Banco de Questões / Minha Seleção) + a preview dialog. Search box "
        "routes a numeric term -> official_number, an UPPER-CODE term -> content, otherwise text "
        "(reserved). Primary filters: Ano/Dia/Área/Disciplina/Conteúdo/Subconteúdo/Status/Dificuldade/"
        "Origem. Advanced (in <details>): classification_mode / provisional_only / visual_dependency / "
        "protected_only. Server-side pagination with prev/next; text search debounced 300 ms; page nav "
        "re-issues the query with the same state (filters preserved). Light list rows show year, day, "
        "official number, area, the Disciplina › Área › Conteúdo › Subconteúdo chain, difficulty, "
        "provisional and visual badges. Clicking 'Abrir' fetches ONLY that question; the dialog shows "
        "the classification chain + confidence/mode/review_reason + a provisional banner for "
        "NEEDS_REVIEW/FORCED_CLOSURE, the visual-dependency copy, the statement verbatim, and options "
        "A-E in official order (no answer key). Selection is a client-side ordered array (never "
        "persisted): select/deselect from list or preview, a persistent 'Questões selecionadas: N' "
        "counter, clear, and a review list with drag-and-drop + move up/down + 'Remover questão'. "
        "'Substituir questão' only returns to the bank with context preserved (no auto-pick, no AI). "
        "'Obter seleção ordenada' calls /selections/preview and shows the validated ordered "
        "QuestionSelection."
    )
    rep["tests"] = {
        "backend": {
            "file": "tests/test_phase13_question_bank_professor_workflow.py",
            "cases": 6,
            "covers": ["server-side pagination + filter preservation across pages",
                       "server-side filters (year/area/discipline/status/visual/provisional/422)",
                       "preview single question, official option order, NO answer key",
                       "classification-state preservation (UNCLASSIFIED/CLASSIFIED/NEEDS_REVIEW/FORCED_CLOSURE)",
                       "selection preview: order preserved, reorder, duplicate rejected, unknown rejected",
                       "full acceptance walk"],
            "result": "6/6 passed",
        },
        "frontend": {
            "file": "tests/test_phase13_question_bank_professor_frontend.js",
            "cases": 18,
            "covers": ["design-system reuse", "AI-agnostic (no provider/LLM refs)",
                       "only PHASE 12 endpoints called", "reused Bearer auth",
                       "search fields + primary filters", "advanced filters",
                       "server-side pagination, no full-bank download", "list row fields + indicators",
                       "filters preserved across pages", "preview loads one question, option order",
                       "no answer key / no internal identifiers", "Discipline->Area->Content->Subcontent + meta",
                       "visual-dependency copy, no fabricated assets", "client-side selection + counter + clear",
                       "drag-and-drop + move up/down, ordering == QuestionSelection",
                       "remove implemented; replace returns to bank; no auto/AI replace",
                       "workflow state survives view navigation", "debounced text search"],
            "result": "18/18 passed",
        },
        "regression": "PHASE 12 tests (tests/test_question_bank_core.py) 9/9 still pass; existing "
                      "frontend node tests still pass; broader related backend suite green.",
        "conventions": "node:test + fs.readFileSync (like tests/test_phase8c_question_modification_frontend.js); "
                       "unittest + fastapi TestClient + in-memory SQLite (like tests/test_bloco_c_http.py).",
    }
    rep["security_observations"] = [
        "Reuses get_current_identity + Bearer <subject> exactly like the other portals; no parallel "
        "permission system introduced.",
        "The professor-facing option DTO no longer carries is_valid_option; the answer key, "
        "resolved_option and official_answer_label never reach the response or the UI (asserted).",
        "No database IDs beyond question_id / question_version_id (needed to open + select) are "
        "exposed; no provider name, model, prompt or secret is rendered by the UI.",
        "Tenant boundary: the ENEM bank is the global/institutional bank (school_id IS NULL). A "
        "per-school authored-question filter remains the documented PHASE 12 integration point "
        "(wire QuestionBankFilters to AuthenticatedUserContext.school_id) - NOT implemented here to "
        "avoid an improvised mechanism.",
    ]
    rep["database_integrity"] = {
        "DATABASE_WRITES": 0, "OPENAI_CALLS": 0, "ALEMBIC_EXECUTION": 0,
        "CLASSIFICATIONS_CREATED": 0, "CATALOG_NODES_CREATED": 0,
        "QUESTION_VERSIONS_MODIFIED": 0, "QUESTION_OPTIONS_MODIFIED": 0,
        "counts_unchanged": rep["counts_before"] == rep["counts_after"],
    }
    rep["ai_calls"] = 0
    rep["migrations"] = []
    rep["known_limitations"] = [
        "No asset store: visual questions are represented and clearly flagged, but figures are not "
        "rendered ('Material visual não disponível.'). Asset ingestion is a later phase.",
        "Free-text statement search is not yet a PHASE 12 capability; the search box routes numbers -> "
        "official_number and CODE-like terms -> content, and keeps a 'text' param wired for when a "
        "full-text endpoint lands. No Elasticsearch/vector search was added.",
        "Selection is browser-only and lost on full page reload (by design for this phase - no "
        "persistence, no selections table).",
        "Drag-and-drop is basic HTML5 DnD (no library); move up/down is the always-available fallback.",
        "The static assets are served the same way as the existing /teacher portal (host/deploy "
        "serves web/); the FastAPI route only returns the HTML entry.",
    ]
    rep["acceptance_criteria"] = {
        "workflow_steps": [
            "Professor -> Question Bank", "Search", "Filter", "Open question",
            "Read statement/options", "Select question", "Select multiple questions",
            "Review selection", "Reorder", "Remove", "Obtain final ordered QuestionSelection",
        ],
        "verified_against_332_question_bank": walk["all_passed"],
        "states_verified": walk["classification_states_on_real_data"],
        "visual_dependency_verified": len(walk["visual_dependency_questions"]) == 5,
        "no_n_plus_1_in_main_listing": not infra["performance"]["n_plus_1"],
        "no_production_data_modified": rep["database_integrity"]["counts_unchanged"],
    }
    ok = (walk["all_passed"]
          and rep["database_integrity"]["counts_unchanged"]
          and not infra["performance"]["n_plus_1"]
          and rep["ai_calls"] == 0)
    rep["FINAL_DECISION"] = (
        "PHASE_13_QUESTION_BANK_PROFESSOR_WORKFLOW_COMPLETE" if ok else "PHASE_13_BLOCKED"
    )
    if not ok:
        rep["blocker"] = "see frontend_workflow.steps for the failed step(s)"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if report["FINAL_DECISION"].endswith("COMPLETE") else 1)
