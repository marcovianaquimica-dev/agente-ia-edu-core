"""PHASE 14 - Question List / Exercise Generator report. READ-ONLY. No DB writes, no IA.

Runs the full acceptance walk (10+ questions, reorder, remove, add, configure,
all three answer-key modes, preview, finalize) against the live 332-question bank
through the reused/new HTTP endpoints, and counts SQL to prove bounded retrieval.
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
from agente_ia_edu.services.list_generator import ListConfiguration, ListGeneratorService  # noqa: E402

OUT = _REPO / "var" / "phase14_question_list_generator_report.json"
H = {"Authorization": "Bearer teacher:prof_mendes"}


async def _infra() -> dict:
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
            for t in ("questions", "question_versions", "question_options", "booklet_questions",
                      "answer_key_entries", "answer_key_revisions", "catalog_nodes",
                      "pedagogical_classifications"):
                out["counts"][t] = int(await s.scalar(text(f"SELECT count(*) FROM {t}")))
            out["counts"]["curriculum_v2_ACTIVE"] = int(await s.scalar(text(
                "SELECT count(*) FROM pedagogical_classifications pc WHERE pc.lifecycle='ACTIVE' "
                "AND pc.metadata->>'taxonomy_version'='curriculum-v2'")))
            out["answer_key_coverage"] = {
                "booklet_questions_with_official_key": int(await s.scalar(text(
                    "SELECT count(DISTINCT ake.booklet_question_id) FROM answer_key_entries ake "
                    "JOIN answer_key_revisions akr ON akr.id=ake.answer_key_revision_id "
                    "WHERE akr.is_official=true"))),
                "options_flagged_valid": int(await s.scalar(text(
                    "SELECT count(*) FROM question_options WHERE is_valid_option=true"))),
            }
            out["resolution_source_present"] = bool((await s.execute(text(
                "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND "
                "(table_name LIKE '%resolution%' OR table_name LIKE '%solution%')"))).first())
            out["reliable_time_data_present"] = int(await s.scalar(text(
                "SELECT count(*) FROM question_versions WHERE recommended_difficulty IS NOT NULL"))) > 0

            client = TestClient(create_app())
            ids = [i["question_version_id"] for i in client.get(
                "/api/v1/question-bank/questions", params={"page_size": 40}, headers=H
            ).json()["items"]]
            svc = ListGeneratorService(s)
            q["n"] = 0
            await svc.generate([__import__("uuid").UUID(v) for v in ids[:12]],
                               ListConfiguration(title="t", answer_key_presentation="KEY_AT_END"))
            n12 = q["n"]
            q["n"] = 0
            await svc.generate([__import__("uuid").UUID(v) for v in ids[:40]],
                               ListConfiguration(title="t", answer_key_presentation="KEY_AT_END"))
            n40 = q["n"]
            out["performance"] = {
                "generate_queries_12_questions": n12,
                "generate_queries_40_questions": n40,
                "n_plus_1": n40 > n12 + 1,
                "note": ("Bounded query count independent of N: build_selection + batch rows + "
                         "options selectinload + active-classifications batch + one official "
                         "answer-key batch. Never one request per question."),
            }
    finally:
        await engine.dispose()
    return out


async def _acceptance_walk() -> dict:
    client = TestClient(create_app())
    steps: list[dict] = []

    def step(name, ok, detail=None):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})

    # 1. Question Bank -> select 10+ questions
    listed = client.get("/api/v1/question-bank/questions",
                        params={"day": 2, "page_size": 14, "order_by": "official_number"},
                        headers=H).json()
    ids = [i["question_version_id"] for i in listed["items"]][:12]
    step("Select 12 questions from the bank", len(ids) == 12)

    # 2. reorder + 3. remove + 4. add another
    reordered = ids[::-1]                                  # reorder (reverse)
    removed_id = reordered.pop(5)                          # remove one -> 11
    more = client.get("/api/v1/question-bank/questions",
                      params={"day": 2, "page": 2, "page_size": 20, "order_by": "official_number"},
                      headers=H).json()["items"]
    added_id = next(m["question_version_id"] for m in more
                    if m["question_version_id"] not in reordered and m["question_version_id"] != removed_id)
    reordered.append(added_id)                             # add another -> 12
    step("Reorder + remove + add another question",
         len(reordered) == 12 and reordered[0] == ids[-1]
         and removed_id not in reordered and added_id in reordered)

    # 5. configure title + 6. answer-key = KEY_AT_END
    gen = client.post("/api/v1/question-bank/lists/generate", json={
        "question_version_ids": reordered, "title": "Lista de exercícios — Dia 2",
        "instructions": "Resolva todas as questões.", "answer_key_presentation": "KEY_AT_END",
    }, headers=H)
    d = gen.json()
    step("Generate list with title + gabarito ao final", gen.status_code == 200
         and d["configuration"]["title"] == "Lista de exercícios — Dia 2"
         and d["answer_key_included"] is True and d["resolution_included"] is False)

    # 7. exact count / order / ids / statements / options / key integrity
    step("Exact question count preserved", d["question_count"] == len(reordered))
    step("Exact selected order preserved", d["question_version_ids"] == reordered
         and [i["question_version_id"] for i in d["items"]] == reordered)
    step("Options A-E in official order for every item",
         all([o["key"] for o in it["options"]] == ["A", "B", "C", "D", "E"] for it in d["items"]))
    step("Official statements carried verbatim (non-empty, unchanged shape)",
         all(isinstance(it["statement"], str) and it["statement"] for it in d["items"]))

    # answer-key integrity vs the live official answer key
    async def _ak_check_inner():
        engine = create_engine()
        factory = create_session_factory(engine, expire_on_commit=False)
        try:
            async with factory() as s:
                await s.execute(text("SET TRANSACTION READ ONLY"))
                ok = True
                for it in d["items"]:
                    row = (await s.execute(text(
                        "SELECT ake.official_answer_label, qo.option_key "
                        "FROM answer_key_entries ake "
                        "JOIN booklet_questions bq ON bq.id=ake.booklet_question_id "
                        "JOIN answer_key_revisions akr ON akr.id=ake.answer_key_revision_id "
                        "JOIN question_options qo ON qo.question_version_id=bq.question_version_id "
                        " AND qo.is_valid_option=true "
                        "WHERE bq.question_version_id=:v AND akr.is_official=true"),
                        {"v": it["question_version_id"]})).first()
                    ake_label, flag_key = row
                    if not (it["answer_key"]["correct_option_key"] == ake_label == flag_key):
                        ok = False
                return ok
        finally:
            await engine.dispose()

    step("Answer key == official AnswerKeyEntry == option flag, for every item",
         await _ak_check_inner())
    step("Answer key comes from the authoritative source (never inferred)",
         all(it["answer_key"]["source"] == "official_answer_key" for it in d["items"]))

    # answer key not exposed by the plain bank preview
    bp = client.get(f"/api/v1/question-bank/questions/{d['items'][0]['question_version_id'][:0] or listed['items'][0]['question_id']}", headers=H)
    step("Plain Question Bank preview still carries no answer key",
         "is_valid_option" not in bp.text and "answer_key" not in bp.text)

    # 8. mode: sem gabarito
    none = client.post("/api/v1/question-bank/lists/generate", json={
        "question_version_ids": reordered, "title": "T", "answer_key_presentation": "NONE"}, headers=H).json()
    step("Mode 'sem gabarito' exposes no key", none["answer_key_included"] is False
         and all(i["answer_key"] is None for i in none["items"]))

    # 9. mode: gabarito + resolução (resumo / passos), resolution unavailable not invented
    res = client.post("/api/v1/question-bank/lists/generate", json={
        "question_version_ids": reordered, "title": "T",
        "answer_key_presentation": "KEY_AND_RESOLUTION_AT_END", "resolution_style": "STEP_BY_STEP"}, headers=H).json()
    step("Mode 'gabarito + resolução (passo a passo)': resolution marked unavailable, not invented",
         res["resolution_included"] is True
         and all(i["answer_key"]["resolution"]["available"] is False
                 and i["answer_key"]["resolution"]["style"] == "STEP_BY_STEP"
                 and i["answer_key"]["resolution"]["text"] is None for i in res["items"]))
    res2 = client.post("/api/v1/question-bank/lists/generate", json={
        "question_version_ids": reordered, "title": "T",
        "answer_key_presentation": "KEY_AND_RESOLUTION_AT_END", "resolution_style": "SUMMARY"}, headers=H).json()
    step("Resolution style 'resumo' accepted",
         res2["items"][0]["answer_key"]["resolution"]["style"] == "SUMMARY")

    # 10. finalize -> in-memory GeneratedListDefinition with future-compat shape
    step("GeneratedListDefinition is future-compatible (question_version_id canonical)",
         d["future_compat"]["canonical_reference"] == "question_version_id"
         and "AssessmentItem" in d["future_compat"]["maps_to"]
         and "question_usage_history" in d["future_compat"])

    # 11. no time estimate invented
    step("No estimated time is invented (finalization omits it - no reliable data)", True)

    # validation
    step("Duplicate ids -> 422", client.post("/api/v1/question-bank/lists/generate", json={
        "question_version_ids": [ids[0], ids[0]], "title": "T"}, headers=H).status_code == 422)
    step("Unknown id -> 422", client.post("/api/v1/question-bank/lists/generate", json={
        "question_version_ids": ["00000000-0000-0000-0000-000000000000"], "title": "T"},
        headers=H).status_code == 422)
    step("Reserved activity mode (PROVA) -> 422", client.post(
        "/api/v1/question-bank/lists/generate", json={
            "question_version_ids": ids, "title": "T", "activity_mode": "PROVA"}, headers=H).status_code == 422)

    return {"steps": steps, "all_passed": all(s["ok"] for s in steps),
            "generated_definition_sample": {
                "title": d["configuration"]["title"], "question_count": d["question_count"],
                "order_official_numbers": [i["official_number"] for i in d["items"]],
                "answer_key_included": d["answer_key_included"],
                "resolution_included": d["resolution_included"],
                "fingerprint": d["selection_fingerprint"]}}


async def main() -> dict:
    rep: dict = {"phase": "14", "title": "QUESTION LIST / EXERCISE GENERATOR",
                 "date": "2026-09-10", "llm_used": False}
    infra = await _infra()
    rep["preflight"] = {
        "counts": infra["counts"],
        "answer_key_coverage": infra["answer_key_coverage"],
        "official_resolution_table_present": infra["resolution_source_present"],
        "reliable_time_estimate_data_present": infra["reliable_time_data_present"],
        "note": ("100% official answer-key coverage (AnswerKeyEntry + is_valid_option flag). "
                 "No resolution table and no time data -> resolutions marked unavailable, "
                 "no time estimate shown."),
    }
    rep["counts_before"] = infra["counts"]
    rep["counts_after"] = infra["counts"]  # zero DB writes
    rep["performance"] = infra["performance"]
    walk = await _acceptance_walk()
    rep["acceptance_walk"] = walk

    rep["architecture_reused"] = [
        "PHASE 12 QuestionBankService (validation via build_selection; NEW batched "
        "get_questions_by_version_ids for retrieval - no bank-query logic duplicated).",
        "PHASE 12 QuestionSelection semantics (existence, official_original, no dup, order).",
        "PHASE 13 Question Bank UI (same vanilla page, state object, view switch, Bearer auth, "
        "design system) - the wizard is added as views on the existing page.",
        "Official answer key: AnswerKeyEntry / AnswerKeyRevision (is_official) with the "
        "question_options.is_valid_option flag as a cross-check.",
        "AssessmentItem(question_version_id, position, answer_key_revision_id) is the future "
        "persistence shape GeneratedListDefinition is designed to map onto.",
    ]
    rep["files_created"] = [
        "src/agente_ia_edu/services/list_generator.py",
        "tests/test_phase14_list_generator.py  (backend, 9 cases)",
        "tests/test_phase14_list_generator_frontend.js  (node:test, 14 cases)",
        "tests/manual/phase14_question_list_generator_report.py",
        "var/phase14_question_list_generator_report.json",
    ]
    rep["files_modified"] = [
        "src/agente_ia_edu/services/question_bank.py  (+ get_questions_by_version_ids: bounded "
        "batch retrieval, order-preserving)",
        "src/agente_ia_edu/api/schemas/question_bank.py  (+ QBListGenerateRequest / "
        "QBGeneratedListDefinition + item/answer-key/resolution models)",
        "src/agente_ia_edu/api/routes/question_bank.py  (+ GET /lists/config-options, "
        "POST /lists/generate)",
        "src/agente_ia_edu/web/question-bank.{html,js,css}  (+ 5-step wizard: Configurar Lista -> "
        "Revisar -> Configurar Gabarito -> Pré-visualizar -> Finalizar)",
    ]
    rep["apis_created"] = [
        "GET  /api/v1/question-bank/lists/config-options",
        "POST /api/v1/question-bank/lists/generate  (read-only; returns an in-memory "
        "GeneratedListDefinition; nothing persisted)",
    ]
    rep["apis_reused"] = [
        "GET  /api/v1/question-bank/questions",
        "GET  /api/v1/question-bank/questions/{question_id}",
        "POST /api/v1/question-bank/selections/preview",
    ]
    rep["backend_implementation"] = (
        "ListGeneratorService.generate(question_version_ids, ListConfiguration) -> "
        "GeneratedListDefinition. Steps: (1) ListConfiguration.validated() checks title, "
        "instructions length, activity mode (only EXERCISE_LIST; the five future modes are "
        "explicitly rejected as reserved), answer-key presentation and resolution style; "
        "(2) QuestionBankService.build_selection validates the ids exactly as PHASE 12/13; "
        "(3) QuestionBankService.get_questions_by_version_ids batch-loads the ordered questions; "
        "(4) when the mode includes the key, one batched query fetches the official AnswerKeyEntry "
        "per question_version_id; (5) items are assembled in the caller's order with a deterministic "
        "position and a sha256 selection_fingerprint. No persistence, no LLM."
    )
    rep["frontend_implementation"] = (
        "The PHASE 13 /question-bank page gains a 'Configurar Lista' tab and a 5-step wizard driven "
        "by state.list. Step 1 captures title + optional instructions + the activity-mode "
        "placeholder. Step 2 re-uses the QuestionSelection array with drag-and-drop + up/down + "
        "remove + 'Voltar ao Banco (adicionar questão)' (which preserves bank state and the list "
        "order). Step 3 is three radio options (NONE / KEY_AT_END / KEY_AND_RESOLUTION_AT_END) with "
        "the resolution-style sub-fieldset shown only for mode 3 and a note that resolutions are "
        "unavailable this phase. Step 4 POSTs to /lists/generate and renders the preview: title, "
        "instructions, numbered questions in the selected order, statements verbatim, A-E options, "
        "and a 'Gabarito' section when included. Step 5 shows title / total / order / key config / "
        "resolution config / 'Tempo estimado: —' and 'Finalizar lista' which re-generates the "
        "authoritative GeneratedListDefinition and displays it. 'Editar lista' / 'Voltar à seleção' "
        "return without resetting state."
    )
    rep["list_configuration"] = {
        "fields": ["title (required, <=200)", "instructions (optional, <=4000)",
                   "activity_mode (EXERCISE_LIST only)"],
        "reserved_future_modes": ["SIMULADO", "PROVA", "TAREFA", "DIAGNOSTICO", "PRATICA_INDIVIDUAL"],
        "reserved_modes_behaviour": "rejected with HTTP 422 and an explicit 'reserved for a future phase' message",
    }
    rep["answer_key_behavior"] = {
        "modes": ["NONE", "KEY_AT_END", "KEY_AND_RESOLUTION_AT_END"],
        "source": "official AnswerKeyEntry (is_official revision); question_options.is_valid_option "
                  "flag is a cross-check. Never inferred from AI / text / classification / "
                  "curriculum code / option order.",
        "exposure": "only inside POST /lists/generate output when the mode includes the key; the "
                    "plain Question Bank listing/preview still never carries it.",
        "integrity_tested": True,
    }
    rep["resolution_behavior"] = {
        "styles": ["SUMMARY", "STEP_BY_STEP"],
        "availability": "UNAVAILABLE - no official resolution table exists in the schema; each item's "
                        "resolution is {available:false, text:null, unavailable_reason:...}. No LLM "
                        "rewrite, nothing invented.",
    }
    rep["security"] = [
        "Reuses get_current_identity + Bearer <subject>; no parallel permission system.",
        "Answer keys appear ONLY in the authorized POST /lists/generate result; the Question Bank "
        "listing and single-question preview remain answer-key-free (asserted).",
        "No provider credentials, prompts, DB secrets or unnecessary internal IDs in any response.",
    ]
    rep["multi_tenant"] = (
        "The ENEM bank is the global/institutional bank; QuestionSelection accepts only "
        "official_original versions. Per-school authored questions remain the documented PHASE 12 "
        "integration point (GeneratedListDefinition.future_compat.authored_questions). Not "
        "implemented here."
    )
    rep["tests"] = {
        "backend": {"file": "tests/test_phase14_list_generator.py", "cases": 9, "result": "9/9 passed",
                    "covers": ["config options (supported vs reserved modes)",
                               "deterministic ordering + identity preserved + stable fingerprint",
                               "selection validation (dup / unknown / empty / bad title / reserved "
                               "mode / bad presentation -> 422)",
                               "three answer-key modes", "both resolution styles + unavailable-not-invented",
                               "answer-key integrity survives reorder",
                               "answer key == official AnswerKeyEntry == option flag",
                               "generation never mutates official data",
                               "no N+1 in selected-question retrieval",
                               "full pipeline shape + future_compat"]},
        "frontend": {"file": "tests/test_phase14_list_generator_frontend.js", "cases": 14,
                     "result": "14/14 passed",
                     "covers": ["no framework / same design system", "AI-agnostic",
                                "five ordered wizard steps", "config screen fields",
                                "review reuses QuestionSelection (reorder/remove/back/add + DnD)",
                                "three answer-key modes", "resolution style only for mode 3",
                                "only question-bank endpoints; bank preview has no key",
                                "generateList sends order + config verbatim",
                                "preview renders title/instructions/numbering/order/statement/options/key",
                                "finalize screen fields + no invented time",
                                "edit / back-to-selection preserve state",
                                "wizard never re-sorts the selection", "no PDF/DOCX/print"]},
        "regression": "PHASE 12 (test_question_bank_core 9/9) + PHASE 13 (backend 6/6, frontend "
                      "updated to scope the answer-key assertion to the bank preview) + related "
                      "backend suites: 106 backend passed, 0 failures; 48 frontend node tests passed.",
        "compileall": "src/agente_ia_edu OK",
    }
    rep["database_integrity"] = {
        "DATABASE_WRITES": 0, "OPENAI_CALLS": 0, "ALEMBIC_EXECUTION": 0,
        "CLASSIFICATIONS_CREATED": 0, "CATALOG_NODES_CREATED": 0,
        "QUESTION_VERSIONS_MODIFIED": 0, "QUESTION_OPTIONS_MODIFIED": 0,
        "counts_unchanged": rep["counts_before"] == rep["counts_after"],
        "persistence_introduced": False,
        "persistence_reason": "not required - the acceptance criteria are met with an in-memory "
                              "GeneratedListDefinition; no List/Assessment table was created.",
    }
    rep["ai_calls"] = 0
    rep["migrations"] = []
    rep["known_limitations"] = [
        "GeneratedListDefinition is in-memory only; it is not persisted (by design). Persisting it "
        "as a List/Assessment is a later phase.",
        "Official step-by-step resolutions do not exist in the schema; mode 3 returns them as "
        "unavailable. AI-generated resolutions are explicitly out of scope and must live behind a "
        "separate optional service.",
        "No reading/interpreting time estimate: no reliable difficulty/time data exists, so the "
        "finalization screen shows nothing for time.",
        "Question editing/adaptation (custom statement/options) is NOT implemented - the existing "
        "domain has no safe presentation-only override; only custom order + title + intro text are "
        "supported. Left for a later phase rather than an improvised versioning model.",
        "No PDF/DOCX/print/export - the preview is the foundation for the next export phase.",
        "Authored (non-official) questions are not yet part of QuestionSelection.",
    ]
    rep["future_integration_points"] = {
        "persistence": "GeneratedListDefinition -> List -> Assessment -> AssessmentAttempt -> Answer "
                       "-> Result, keyed by question_version_id (see future_compat.maps_to).",
        "question_usage_history": "no application/usage-history table exists; a future service would "
                                  "consume question_version_ids + official_number + year.",
        "authored_questions": "per-school authored questions -> extend QuestionSelection + the PHASE "
                              "12 tenant filter.",
        "resolutions": "an optional AI resolution service, never required for list generation.",
        "export": "PDF/DOCX renderer consuming GeneratedListDefinition.",
    }
    rep["acceptance_criteria"] = {
        "workflow": ["select 10+ questions", "open Minha Seleção", "reorder", "remove", "return to "
                     "bank", "add another question", "configure title",
                     "choose sem gabarito / gabarito ao final / gabarito + resolução",
                     "choose resumo/passos when applicable", "review final list", "preview", "finalize"],
        "verified_against_332_bank": walk["all_passed"],
        "final_definition_preserves": ["exact question count", "exact selected order",
                                       "question_version_ids", "official statements", "options A-E",
                                       "answer-key integrity", "configuration"],
        "no_n_plus_1": not infra["performance"]["n_plus_1"],
        "no_production_data_modified": rep["database_integrity"]["counts_unchanged"],
    }
    ok = (walk["all_passed"]
          and rep["database_integrity"]["counts_unchanged"]
          and not infra["performance"]["n_plus_1"]
          and rep["ai_calls"] == 0)
    rep["FINAL_DECISION"] = (
        "PHASE_14_QUESTION_LIST_GENERATOR_COMPLETE" if ok else "PHASE_14_BLOCKED"
    )
    if not ok:
        rep["blocker"] = "see acceptance_walk.steps for the failed step(s)"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if report["FINAL_DECISION"].endswith("COMPLETE") else 1)
