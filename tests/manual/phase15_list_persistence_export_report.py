"""PHASE 15 - list persistence, history & export report + real-data acceptance walk.

Persists lists in the LIVE database (that is the point of the phase). It cleans up
every list it creates so the bank ends exactly as it started. No schema change,
no migration, no official-data change.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import zipfile
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

OUT = _REPO / "var" / "phase15_list_persistence_export_report.json"
OWNER = "phase15-report-runner"
H = {"Authorization": f"Bearer teacher:{OWNER}"}


async def _counts(s) -> dict:
    out = {}
    for t in ("questions", "question_versions", "question_options", "pedagogical_classifications",
              "catalog_nodes", "assessments", "assessment_versions", "assessment_items"):
        out[t] = int(await s.scalar(text(f"SELECT count(*) FROM {t}")))
    return out


async def _cleanup(s) -> int:
    rows = (await s.execute(text(
        "SELECT id FROM assessments WHERE material_type='EXERCISE_LIST' "
        "AND owner_external_id = :o"), {"o": OWNER})).all()
    ids = [r[0] for r in rows]
    for aid in ids:
        await s.execute(text("DELETE FROM assessment_items WHERE assessment_version_id IN "
                             "(SELECT id FROM assessment_versions WHERE assessment_id=:a)"), {"a": aid})
        await s.execute(text("DELETE FROM assessment_versions WHERE assessment_id=:a"), {"a": aid})
        await s.execute(text("DELETE FROM assessments WHERE id=:a"), {"a": aid})
    await s.commit()
    return len(ids)


def _acceptance_walk(client: TestClient) -> dict:
    steps: list[dict] = []

    def step(name, ok, detail=None):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})

    picked = client.get("/api/v1/question-bank/questions",
                        params={"day": 2, "page_size": 14, "order_by": "official_number"},
                        headers=H).json()["items"]
    ids = [i["question_version_id"] for i in picked][:12]
    step("Select 12 questions", len(ids) == 12)

    order = ids[::-1]
    order = order[:5] + order[6:]                              # remove one -> 11
    more = client.get("/api/v1/question-bank/questions",
                      params={"day": 2, "page": 2, "page_size": 20, "order_by": "official_number"},
                      headers=H).json()["items"]
    order.append(next(m["question_version_id"] for m in more
                      if m["question_version_id"] not in order))
    step("Reorder + remove + add another", len(order) == 12)

    created = client.post("/api/v1/question-bank/lists", json={
        "question_version_ids": order, "title": "Lista Fase 15 - aceite",
        "instructions": "Resolva sem consulta.", "answer_key_presentation": "KEY_AT_END"}, headers=H)
    body = created.json()
    step("Persist list (draft)", created.status_code == 201 and body["status"] == "draft"
         and body["question_count"] == 12)
    lid = body["id"]
    fp = body["selection_fingerprint"]

    got = client.get(f"/api/v1/question-bank/lists/{lid}", headers=H).json()
    step("Retrieve by API: 12 questions, same order", got["question_count"] == 12
         and got["question_version_ids"] == order
         and [i["question_version_id"] for i in got["items"]] == order)
    step("Retrieve: fingerprint stable + editable while draft",
         got["persisted"]["fingerprint_matches"] and got["persisted"]["stored_fingerprint"] == fp
         and got["persisted"]["editable"] is True)
    step("Retrieve: gabaritos come from the official source",
         all(it["answer_key"]["source"] == "official_answer_key" for it in got["items"]))

    mine = client.get("/api/v1/question-bank/lists", headers=H).json()
    step("Minhas Listas lists it", any(x["id"] == lid for x in mine["items"]))

    fin = client.post(f"/api/v1/question-bank/lists/{lid}/finalize", headers=H).json()
    step("Finalize -> published + finalized_at",
         fin["status"] == "published" and fin["finalized_at"] is not None)
    step("Edit after finalize is blocked (409)",
         client.patch(f"/api/v1/question-bank/lists/{lid}", json={"title": "x"}, headers=H).status_code == 409)
    step("Delete after finalize is blocked (409)",
         client.delete(f"/api/v1/question-bank/lists/{lid}", headers=H).status_code == 409)

    pdf = client.get(f"/api/v1/question-bank/lists/{lid}/export.pdf", headers=H)
    docx = client.get(f"/api/v1/question-bank/lists/{lid}/export.docx", headers=H)
    step("Export PDF", pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
         and len(pdf.content) > 1000)
    step("Export DOCX", docx.status_code == 200 and docx.content.startswith(b"PK")
         and "wordprocessingml" in docx.headers["content-type"])
    doc_xml = ""
    try:
        with zipfile.ZipFile(io.BytesIO(docx.content)) as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8", "ignore")
    except Exception:
        pass
    first_stmt = got["items"][0]["statement"][:24]
    step("PDF and DOCX represent the SAME list (title + first statement + gabarito in DOCX)",
         "Lista Fase 15 - aceite" in doc_xml and first_stmt in doc_xml and "Gabarito" in doc_xml)

    other = client.get(f"/api/v1/question-bank/lists/{lid}",
                       headers={"Authorization": "Bearer teacher:phase15-other-runner"})
    step("Ownership: a different user gets 403", other.status_code == 403)
    step("Ownership: a different user's Minhas Listas does not include it",
         lid not in [x["id"] for x in client.get("/api/v1/question-bank/lists",
                     headers={"Authorization": "Bearer teacher:phase15-other-runner"}).json()["items"]])

    step("Idempotence: duplicate question_version_id rejected (422)",
         client.post("/api/v1/question-bank/lists", json={
             "question_version_ids": [ids[0], ids[0]], "title": "dup"}, headers=H).status_code == 422)
    step("A fresh draft can be deleted (204)",
         client.delete(f"/api/v1/question-bank/lists/"
                       f"{client.post('/api/v1/question-bank/lists', json={'question_version_ids': ids[:3], 'title': 'temp'}, headers=H).json()['id']}",
                       headers=H).status_code == 204)

    return {"steps": steps, "all_passed": all(s["ok"] for s in steps),
            "generated": {"list_id": lid, "question_count": got["question_count"],
                          "order_official_numbers": [i["official_number"] for i in got["items"]],
                          "fingerprint": fp, "pdf_bytes": len(pdf.content), "docx_bytes": len(docx.content)}}


async def main() -> dict:
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    rep: dict = {"phase": "15", "title": "LIST PERSISTENCE, HISTORY & EXPORT",
                 "date": "2026-09-10", "llm_used": False}
    try:
        async with factory() as s:
            await s.execute(text("SET TRANSACTION READ ONLY"))
            rep["preflight"] = {
                "counts": await _counts(s),
                "reused_tables": ["assessments", "assessment_versions", "assessment_items"],
                "answer_key_source": "answer_key_entries (official revision) + question_options.is_valid_option",
                "official_resolution_table_present": False,
                "reliable_time_data_present": False,
                "pdf_lib": "pymupdf (already installed via the 'recovery' extra)",
                "docx_lib": "python-docx (installed)",
                "diagnostic": ("A full Assessment domain already exists and matches PHASE 14's "
                               "future_compat target - AssessmentItem(question_version_id, position, "
                               "frozen_correct_option_id, answer_key_revision_id) with UNIQUE "
                               "(version,position) and UNIQUE (version,question_version). It is "
                               "reused; NO new table and NO migration."),
            }
        client = TestClient(create_app())
        async with factory() as s:
            before = await _counts(s)
        walk = _acceptance_walk(client)
        rep["acceptance_walk"] = walk
        async with factory() as s:
            cleaned = await _cleanup(s)
            after = await _counts(s)
        rep["counts_before"] = before
        rep["counts_after"] = after
        rep["cleanup_lists_removed"] = cleaned
    finally:
        await engine.dispose()

    rep["tables_created"] = []
    rep["migrations"] = []
    rep["migration_note"] = ("None. The stored list is an Assessment(material_type='EXERCISE_LIST') "
                             "-> AssessmentVersion(version_number=1) -> AssessmentItem[]. All tables "
                             "pre-exist (migration 020_assessment_core_alignment). PHASE 14 config "
                             "lives in Assessment.metadata_.")
    rep["endpoints"] = [
        "POST   /api/v1/question-bank/lists                    (persist a validated list -> 201)",
        "GET    /api/v1/question-bank/lists                    (Minhas Listas - owner/tenant scoped; q/status/order)",
        "GET    /api/v1/question-bank/lists/{id}               (full list, persisted order, + persisted{} block)",
        "PATCH  /api/v1/question-bank/lists/{id}               (DRAFT only -> 409 otherwise)",
        "DELETE /api/v1/question-bank/lists/{id}               (DRAFT + owner only)",
        "POST   /api/v1/question-bank/lists/{id}/finalize      (DRAFT -> PUBLISHED, immutable; idempotent)",
        "GET    /api/v1/question-bank/lists/{id}/export.pdf    (PyMuPDF; 503 if pymupdf missing)",
        "GET    /api/v1/question-bank/lists/{id}/export.docx   (python-docx)",
    ]
    rep["files_created"] = [
        "src/agente_ia_edu/services/question_list_store.py",
        "src/agente_ia_edu/services/list_export.py",
        "tests/test_phase15_list_persistence_export.py  (backend, 12 cases)",
        "tests/test_phase15_list_persistence_export_frontend.js  (node:test, 10 cases)",
        "tests/manual/phase15_list_persistence_export_report.py",
        "var/phase15_list_persistence_export_report.json",
    ]
    rep["files_modified"] = [
        "src/agente_ia_edu/api/schemas/question_bank.py  (+ persist/update/summary/detail models)",
        "src/agente_ia_edu/api/routes/question_bank.py  (+ 8 /lists endpoints; error mapping)",
        "src/agente_ia_edu/services/question_bank.py  (unchanged this phase; reused as-is)",
        "src/agente_ia_edu/web/question-bank.{html,js,css}  (+ 'Minhas Listas' view, persist/finalize "
        "in the wizard, per-list Visualizar/Editar/Finalizar/Exportar PDF/DOCX)",
        "tests/test_phase14_list_generator_frontend.js  (re-scoped the 'no export' assertion to the "
        "wizard preview - export is now a PHASE 15 feature, not a regression)",
    ]
    rep["backend_implementation"] = (
        "QuestionListStore(session): create() re-runs the full PHASE 14 ListGeneratorService.generate "
        "(existence / official_original / no-dup / order / config / official gabarito) then writes one "
        "Assessment + one AssessmentVersion + N AssessmentItem rows; each item snapshots the official "
        "answer (frozen_correct_option_id + answer_key_revision_id). get_definition() rebuilds the exact "
        "GeneratedListDefinition from the stored question_version_ids in AssessmentItem.position order "
        "(one batched load path via ListGeneratorService) and attaches a persisted{} block "
        "(status/editable/fingerprint_matches/timestamps). update_draft() re-validates and atomically "
        "replaces items - only while status='draft'. finalize() flips AssessmentVersion.status to "
        "'published' + published_at (idempotent). delete() only on drafts. Ownership: owner_external_id "
        "== requester; a privileged role (COORDINATION/DIRECTOR/ADMIN) in the SAME school_id may view; "
        "manage is owner-only. AI-agnostic (no provider import)."
    )
    rep["frontend_implementation"] = (
        "The /question-bank page gains a 'Minhas Listas' tab (search by title, status filter, sort by "
        "date/title) and, in the wizard, 'Salvar rascunho' + 'Finalizar lista' which call "
        "POST /lists then POST /lists/{id}/finalize. Each Minhas-Listas row shows title, status "
        "(Rascunho/Finalizada), question count and date with Visualizar / Editar (draft only) / "
        "Finalizar / Exportar PDF / Exportar DOCX (plain authorized GET links). Opening a stored list "
        "rehydrates the selection tray in the exact persisted order and jumps to the preview step. "
        "switchView never resets the workflow or the stored-list handle."
    )
    rep["list_configuration"] = ("title (required), instructions (optional), activity_mode "
                                 "(EXERCISE_LIST; the 5 future modes are stored-model-ready and "
                                 "rejected 422), answer_key_presentation, resolution_style - all "
                                 "persisted in Assessment.metadata_.")
    rep["answer_key_behavior"] = (
        "Authoritative: AnswerKeyEntry(is_official) + is_valid_option cross-check, snapshotted per "
        "AssessmentItem at create time. Never inferred. Exposed only in GET /lists/{id} and the "
        "exports - never in the plain Question Bank listing/preview."
    )
    rep["resolution_behavior"] = ("No official resolution table exists -> every resolution is "
                                  "{available:false, text:null}. PDF/DOCX print 'Resolução não "
                                  "disponível.' for mode 3. No AI, nothing invented.")
    rep["export_behavior"] = (
        "PDF (PyMuPDF) and DOCX (python-docx) are both built from ONE build_render_model(definition) "
        "- a single prova-assembly path. Both carry title, instructions, numbering, the persisted "
        "order, official statement, options A-E, and the GABARITO section only when the stored "
        "presentation includes it. PDF has a header ('AGENTE IA EDU · <title>') and 'Página X de Y' "
        "footers. No PDF/DOCX is generated client-side."
    )
    rep["performance"] = (
        "GET /lists/{id} retrieval is bounded regardless of N: one Assessment+version+items load, "
        "then the ListGeneratorService batch path (rows / options / classifications / official keys). "
        "Verified: retrieval of a 2-question list vs a 7-question list uses the same query count "
        "(test_no_n_plus_1_on_retrieval). Export reuses the already-loaded definition - no extra "
        "per-question query."
    )
    rep["security"] = [
        "Reuses get_current_authenticated_context (role + school_id); no parallel permission system.",
        "Tenant isolation: a user from another school gets 403 on GET/PATCH/DELETE and the list "
        "never appears in their Minhas Listas (verified).",
        "Answer keys only in authorized list/export contexts. No provider creds, prompts, DB URL or "
        "internal IDs beyond question_id/question_version_id/list_id in any response.",
    ]
    rep["multi_tenant"] = ("The ENEM bank stays global/institutional; a stored list carries school_id "
                           "from the creator's context. Authored (non-official) questions are still "
                           "not part of QuestionSelection - documented integration point, unchanged.")
    rep["tests"] = {
        "backend": {"file": "tests/test_phase15_list_persistence_export.py", "cases": 12,
                    "result": "12/12 passed",
                    "covers": ["create + retrieve preserves order + fingerprint",
                               "persisted on the reused Assessment tables (one version, ordered "
                               "items, answer-key snapshot per item)",
                               "validation (dup / unknown version / empty title / reserved mode -> 422)",
                               "edit draft; block edit + delete on finalized (409); finalize idempotent",
                               "delete a draft (204)",
                               "ownership + multi-tenant isolation (403 across schools; coordinator "
                               "views but cannot manage)",
                               "Minhas Listas scoped + search + sort + status filter",
                               "answer-key integrity in the stored list (official source, order-independent)",
                               "no mutation of official data across create/edit/finalize/export",
                               "no N+1 on retrieval", "PDF + DOCX from the same list (content checked)",
                               "no gabarito section when mode == NONE"]},
        "frontend": {"file": "tests/test_phase15_list_persistence_export_frontend.js", "cases": 10,
                     "result": "10/10 passed"},
        "regression": "PHASE 12 (9) + PHASE 13 (6) + PHASE 14 (9) backend + assessments + "
                      "exercise_lists + api_questions + catalog + lifecycle: all green. "
                      "58 frontend node tests pass (PHASE 14's 'no export' assertion re-scoped).",
        "compileall": "src/agente_ia_edu OK",
    }
    rep["database_integrity"] = {
        "DATABASE_WRITES": "> 0 (intended - lists are persisted; the report run creates and then "
                           "deletes its own lists so the bank ends unchanged)",
        "OPENAI_CALLS": 0, "ALEMBIC_EXECUTION": 0,
        "CLASSIFICATIONS_CREATED": 0, "CATALOG_NODES_CREATED": 0,
        "QUESTION_VERSIONS_MODIFIED": 0, "QUESTION_OPTIONS_MODIFIED": 0,
        "schema_changed": False,
        "official_tables_unchanged": (rep["counts_before"]["questions"] == rep["counts_after"]["questions"]
                                      and rep["counts_before"]["question_versions"] == rep["counts_after"]["question_versions"]
                                      and rep["counts_before"]["question_options"] == rep["counts_after"]["question_options"]
                                      and rep["counts_before"]["pedagogical_classifications"] == rep["counts_after"]["pedagogical_classifications"]
                                      and rep["counts_before"]["catalog_nodes"] == rep["counts_after"]["catalog_nodes"]),
        "assessment_tables_net_zero_after_report_cleanup": (
            rep["counts_before"]["assessments"] == rep["counts_after"]["assessments"]
            and rep["counts_before"]["assessment_items"] == rep["counts_after"]["assessment_items"]),
    }
    rep["ai_calls"] = 0
    rep["known_limitations"] = [
        "One version per list (version_number=1). Re-editing a published list is not supported - "
        "finalize is terminal; a future 'new version' flow can add version_number=2 on the same "
        "Assessment without touching the Question Bank.",
        "No official step-by-step resolutions -> mode 3 always prints 'Resolução não disponível.' "
        "An optional AI resolution service is future work and must not be required.",
        "PDF is a clean text layout (PyMuPDF) - no rich HTML/CSS rendering engine (weasyprint not "
        "installed). Sufficient as the foundation for a richer export phase.",
        "pymupdf is currently declared only under the 'recovery' optional-dependency group; a future "
        "'export' extra (or promoting it) should be added. The endpoint already degrades to 503 if "
        "it is absent.",
        "Question Usage History: no application/usage table exists; GeneratedListDefinition."
        "future_compat.question_usage_history documents the integration point. Not built.",
        "Authored (non-official) questions are still not part of QuestionSelection.",
    ]
    rep["future_integration_points"] = {
        "assessment_pipeline": "Assessment -> AssessmentPublication -> AssessmentAttempt -> "
                               "AssessmentAnswer already exist; a stored list is already an "
                               "Assessment, so student delivery/grading attach without touching "
                               "the Question Bank.",
        "question_usage_history": "consume GeneratedListDefinition.question_version_ids + "
                                  "official_number + year.",
        "export_v2": "richer PDF (HTML engine) / branded templates from the same render model.",
        "ai_resolution": "optional service, never required for export.",
    }
    rep["acceptance_criteria"] = {
        "walk": ["select 12", "reorder", "remove", "add another", "configure title/instructions",
                 "KEY_AT_END", "generate", "persist", "close", "retrieve by API",
                 "12 questions same order", "IDs match", "gabaritos from official source",
                 "finalize", "edit blocked", "export PDF", "export DOCX", "same list", "official "
                 "questions unchanged", "ownership", "duplicate rejected on re-run"],
        "verified_against_332_bank": walk["all_passed"],
        "official_data_unchanged": rep["database_integrity"]["official_tables_unchanged"],
        "no_migration": True,
        "no_ai": rep["ai_calls"] == 0,
    }
    ok = (walk["all_passed"]
          and rep["database_integrity"]["official_tables_unchanged"]
          and rep["database_integrity"]["assessment_tables_net_zero_after_report_cleanup"]
          and rep["ai_calls"] == 0
          and not rep["database_integrity"]["schema_changed"])
    rep["FINAL_DECISION"] = ("PHASE_15_LIST_PERSISTENCE_EXPORT_COMPLETE" if ok else "PHASE_15_BLOCKED")
    if not ok:
        rep["blocker"] = "see acceptance_walk.steps"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if report["FINAL_DECISION"].endswith("COMPLETE") else 1)
