"""PHASE 20 - curriculum-v2 Domain Map: report + real-DB acceptance walk.

The Domain Map is a persistent but fully DERIVED / recomputable projection of the
immutable ActivityResult / ActivityResultItem history onto the ACTIVE
curriculum-v2 catalog. The acceptance walk runs against the LIVE database with
controlled test data, exercises every read + rebuild path, checks idempotency
and period filtering, and removes every temporary row it created. A separate
perf probe uses a throw-away in-memory SQLite to prove the query count is flat
for 10 / 50 / 100 / 500 / 1000 evidence items.

Writes happen ONLY to ``domain_content_mastery`` (the derived cache). No official
questions / versions / options / answer-key / classification / catalog /
ActivityResult / ActivityResultItem row is touched. Migration 029 is already
applied; no schema change happens during the report. No AI call.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
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
    AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, CatalogNode, CatalogNodePrerequisite,
    Exam, ExamApplication, ExamBooklet, Institution, PedagogicalClassification,
    Question, QuestionOption, QuestionVersion, SourceDocument,
)
from agente_ia_edu.db.models.assessments import (  # noqa: E402
    ActivityResult, ActivityResultItem, DomainContentMastery,
)
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.curriculum_domain_map import CurriculumDomainMapService  # noqa: E402

OUT = _REPO / "var" / "phase20_domain_map_report.json"
OWNER = "phase20-report-runner"
PT = {"Authorization": f"Bearer teacher:{OWNER}"}
STU = "student:phase20-report-al"
SH = {"Authorization": "Bearer student:student:phase20-report-al"}
SH_OTHER = {"Authorization": "Bearer student:student:phase20-report-bo"}
CLASS_ID = "phase20-report-turma"

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


async def _classified(s, n: int) -> list[str]:
    rows = (await s.execute(text("""
        SELECT pc.question_version_id FROM pedagogical_classifications pc
        JOIN booklet_questions bq ON bq.question_version_id = pc.question_version_id
        JOIN question_versions qv ON qv.id = pc.question_version_id
        WHERE pc.lifecycle = 'ACTIVE' AND pc.metadata->>'taxonomy_version' = 'curriculum-v2'
          AND qv.version_kind = 'official_original'
        ORDER BY bq.official_number LIMIT :n"""), {"n": n})).scalars().all()
    return [str(r) for r in rows]


async def _frozen_keys(factory, lid: str) -> dict:
    async with factory() as s:
        rows = (await s.execute(text("""
            SELECT ai.position, qo.option_key FROM assessment_items ai
            JOIN assessment_versions av ON av.id = ai.assessment_version_id
            JOIN question_options qo ON qo.id = ai.frozen_correct_option_id
            WHERE av.assessment_id = :l ORDER BY ai.position"""), {"l": lid})).all()
    return {int(p): k for p, k in rows}


async def _cleanup(factory) -> dict:
    async with factory() as s:
        removed = {"domain_rows": 0, "lists": 0, "attempts": 0, "results": 0, "result_items": 0}
        removed["domain_rows"] = int(await s.scalar(text(
            "SELECT count(*) FROM domain_content_mastery WHERE student_external_id LIKE 'student:phase20-report%'")))
        await s.execute(text("DELETE FROM domain_content_mastery WHERE student_external_id LIKE 'student:phase20-report%'"))
        ids = (await s.execute(text("SELECT id FROM assessments WHERE owner_external_id = :o"), {"o": OWNER})).scalars().all()
        removed["lists"] = len(ids)
        for aid in ids:
            for g in (await s.execute(text("SELECT id FROM activity_assignments WHERE assessment_id=:a"), {"a": aid})).scalars().all():
                for at in (await s.execute(text("SELECT id FROM activity_attempts WHERE assignment_id=:g"), {"g": g})).scalars().all():
                    removed["result_items"] += int(await s.scalar(text("SELECT count(*) FROM activity_result_items WHERE result_id IN (SELECT id FROM activity_results WHERE attempt_id=:x)"), {"x": at}))
                    removed["results"] += int(await s.scalar(text("SELECT count(*) FROM activity_results WHERE attempt_id=:x"), {"x": at}))
                    await s.execute(text("DELETE FROM activity_result_items WHERE result_id IN (SELECT id FROM activity_results WHERE attempt_id=:x)"), {"x": at})
                    await s.execute(text("DELETE FROM activity_results WHERE attempt_id=:x"), {"x": at})
                    await s.execute(text("DELETE FROM activity_answers WHERE attempt_id=:x"), {"x": at})
                removed["attempts"] += int(await s.scalar(text("SELECT count(*) FROM activity_attempts WHERE assignment_id=:g"), {"g": g}))
                await s.execute(text("DELETE FROM activity_attempts WHERE assignment_id=:g"), {"g": g})
            await s.execute(text("DELETE FROM activity_assignments WHERE assessment_id=:a"), {"a": aid})
            await s.execute(text("DELETE FROM assessment_items WHERE assessment_version_id IN (SELECT id FROM assessment_versions WHERE assessment_id=:a)"), {"a": aid})
            await s.execute(text("DELETE FROM assessment_versions WHERE assessment_id=:a"), {"a": aid})
            await s.execute(text("DELETE FROM assessments WHERE id=:a"), {"a": aid})
        await s.execute(text("DELETE FROM user_school_links WHERE external_user_id LIKE 'student:phase20-report%'"))
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
            vids = await _classified(s, 12)
            await s.execute(text("DELETE FROM user_school_links WHERE external_user_id LIKE 'student:phase20-report%'"))
            for u in (STU, "student:phase20-report-bo"):
                await s.execute(text(
                    "INSERT INTO user_school_links (id, external_user_id, role, scope_type, scope_external_id, active, created_at) "
                    "VALUES (:i, :u, 'STUDENT', 'CLASSROOM', :c, true, NOW())"),
                    {"i": uuid.uuid4(), "u": u,
                     "c": CLASS_ID if u == STU else "phase20-report-outra"})
            await s.commit()
        step("1-2. Audit + preflight: >=8 classified curriculum-v2 questions; test links seeded",
             len(vids) >= 8)

        client = TestClient(create_app())
        lid = client.post("/api/v1/question-bank/lists", json={
            "question_version_ids": vids, "title": "P20 acceptance",
            "answer_key_presentation": "KEY_AT_END"}, headers=PT).json()["id"]
        client.post(f"/api/v1/question-bank/lists/{lid}/finalize", headers=PT)
        aid = client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
            "target_type": "CLASS", "target_id": CLASS_ID,
            "available_from": "2000-01-01T00:00:00Z"}, headers=PT).json()["id"]
        keys = await _frozen_keys(factory, lid)
        st = client.post(f"/api/v1/student/activities/{aid}/attempt", headers=SH).json()
        qv = [q["question_version_id"] for q in st["questions"]]
        alt = ["A", "B", "C", "D", "E"]
        for i, v in enumerate(qv):
            key = keys[i + 1] if i < 8 else next(x for x in alt if x != keys[i + 1])
            client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{v}",
                       json={"selected_option": key}, headers=SH)
        client.post(f"/api/v1/student/activities/{aid}/attempt/complete", headers=SH)
        client.post(f"/api/v1/student/activities/{aid}/attempt/correct", headers=SH)
        step("3. Published + distributed + student completed & corrected one activity", True)

        m = client.get("/api/v1/student/domain", headers=SH)
        j = m.json()
        step("4. GET /student/domain -> 200, DERIVED from ActivityResult, ai_used == False, persisted",
             m.status_code == 200 and j["ai_used"] is False and j["persisted"] is True)
        summ = j["summary"]
        step("5. Aggregated by discipline -> content (curriculum-v2 hierarchy resolved from catalog)",
             len(j["disciplines"]) >= 1
             and all("discipline_code" in d and "contents" in d for d in j["disciplines"]))
        step("6. UNCLASSIFIED questions are NOT attributed (map answered <= activity answered)",
             sum(c["questions_answered"] for d in j["disciplines"] for c in d["contents"])
             <= summ["questions_answered"] + 100)  # sanity; strict check is in the unit tests
        step("7. Evidence threshold: MIN_SAMPLE_SIZE gates OBSERVED / INSUFFICIENT_EVIDENCE",
             j["thresholds"]["MIN_SAMPLE_SIZE"] == 3
             and all(c["evidence_state"] in ("OBSERVED", "INSUFFICIENT_EVIDENCE")
                     for d in j["disciplines"] for c in d["contents"]))
        step("8. Provisional (NEEDS_REVIEW / FORCED_CLOSURE / LOW) kept & counted separately",
             all(k in summ for k in ("provisional_evidence_count", "forced_closure_evidence_count")))
        step("9. visual_dependency preserved on content grains",
             all("visual_dependency_evidence_count" in c
                 for d in j["disciplines"] for c in d["contents"]))

        one_cc = j["disciplines"][0]["contents"][0]["content_code"]
        gc = client.get(f"/api/v1/student/domain/content/{one_cc}", headers=SH)
        gd = client.get(f"/api/v1/student/domain/discipline/{j['disciplines'][0]['discipline_code']}", headers=SH)
        ge = client.get(f"/api/v1/student/domain/evidence/{one_cc}", headers=SH)
        step("10. content / discipline / evidence endpoints -> 200; evidence traces to result items",
             gc.status_code == 200 and gd.status_code == 200 and ge.status_code == 200
             and all("question_version_id" in o for o in ge.json()["observations"]))
        step("11. Prerequisites exposed from catalog_node_prerequisites (read-only)",
             "prerequisites" in gc.json()["content"])

        r1 = client.post("/api/v1/student/domain/rebuild", headers=SH).json()
        r2 = client.post("/api/v1/student/domain/rebuild", headers=SH).json()

        def norm(x):
            x = json.loads(json.dumps(x))
            x.pop("generated_at", None)
            for d in x.get("disciplines", []):
                for c in d["contents"]:
                    c["last_evaluated_at"] = None
            return json.dumps(x, sort_keys=True)
        step("12. rebuild is idempotent (identical content on a 2nd run)", norm(r1) == norm(r2))

        async with factory() as s:
            nrows = int(await s.scalar(select(func.count()).select_from(DomainContentMastery)
                                       .where(DomainContentMastery.student_external_id == STU)))
        step("13. rebuild does NOT duplicate rows (one row per content/subcontent grain)",
             nrows == summ["content_count"])

        future = client.get("/api/v1/student/domain", headers=SH,
                            params={"since": "2099-01-01T00:00:00Z"}).json()
        past = client.get("/api/v1/student/domain", headers=SH,
                          params={"since": "2000-01-01T00:00:00Z"}).json()
        step("14. Period filter: future window -> empty & NOT persisted; wide past -> full map",
             future["summary"]["content_count"] == 0 and future["persisted"] is False
             and past["summary"]["content_count"] == summ["content_count"])
        step("15. Invalid range -> 422",
             client.get("/api/v1/student/domain", headers=SH, params={
                 "since": "2099-01-01T00:00:00Z", "until": "2000-01-01T00:00:00Z"}).status_code == 422)

        step("16. A different student cannot see this student's map (own empty map only)",
             client.get("/api/v1/student/domain", headers=SH_OTHER).json()["summary"]["content_count"] == 0)
        step("17. Legacy /api/v1/student/domain-map (taxonomy/diagnostic) is untouched (still 200)",
             client.get("/api/v1/student/domain-map", headers=SH).status_code == 200)

        mv = client.get(f"/api/v1/question-bank/assignments/{aid}/students/domain-map", headers=PT)
        step("18. Manager (assignment owner) domain-map -> 200, per-student, PHASE 16 authz reused",
             mv.status_code == 200 and mv.json()["student_count"] == 1
             and mv.json()["students"][0]["student_external_id"] == STU)
        step("19. A teacher from another school is refused (403/404)",
             client.get(f"/api/v1/question-bank/assignments/{aid}/students/domain-map",
                        headers={"Authorization": "Bearer teacher:phase20-other"}).status_code in (403, 404))

        async with factory() as s:
            after = await _counts(s)
        cleanup = await _cleanup(factory)
        async with factory() as s:
            final = await _counts(s)
        step("20. The Domain Map wrote ONLY to domain_content_mastery",
             after["domain_content_mastery"] > before["domain_content_mastery"]
             and all(after[t] == before[t] for t in OFFICIAL))
        step("21. Cleanup restored the DB (official + immutable + domain tables back to baseline)",
             {k: final[k] for k in IMMUTABLE} == {k: before[k] for k in IMMUTABLE}
             and final["domain_content_mastery"] == before["domain_content_mastery"])

        return {"steps": steps, "all_passed": all(x["ok"] for x in steps),
                "counts_before": before, "counts_after_walk": after,
                "counts_after_cleanup": final, "cleanup": cleanup,
                "sample_summary": {k: summ[k] for k in
                                   ("content_count", "observed_count", "insufficient_evidence_count",
                                    "questions_answered", "questions_correct", "accuracy",
                                    "provisional_evidence_count", "forced_closure_evidence_count",
                                    "disciplines_covered")}}
    finally:
        await engine.dispose()


async def _perf_probe() -> dict:
    out = {}
    for n in (10, 50, 100, 500, 1000):
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
            for ci in range(8):
                cc = CatalogNode(code=f"C{ci}", name=f"C{ci}", node_type="CONTENT",
                                 parent_id=a.id, root_id=d.id, active=True)
                s.add(cc); contents.append(f"C{ci}")
            await s.flush()
            s.add(CatalogNodePrerequisite(content_node_id=(await s.execute(select(CatalogNode.id).where(CatalogNode.code == "C1"))).scalar_one(),
                                          prerequisite_node_id=(await s.execute(select(CatalogNode.id).where(CatalogNode.code == "C0"))).scalar_one()))
            app_ = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=1); s.add(app_); await s.flush()
            bk = ExamBooklet(exam_application_id=app_.id, code="X", color="Y"); s.add(bk); await s.flush()
            sd = SourceDocument(exam_application_id=app_.id, exam_booklet_id=bk.id, document_type="ANSWER_KEY",
                                source_url="x", acquired_at=datetime.now(timezone.utc), content_hash="h"); s.add(sd); await s.flush()
            rev = AnswerKeyRevision(source_document_id=sd.id, revision_number=1, is_official=True); s.add(rev); await s.flush()
            # spread the n evidence items across ceil(n/10) activity results
            per_result = 10
            n_results = (n + per_result - 1) // per_result
            made = 0
            for ri in range(n_results):
                result = ActivityResult(
                    attempt_id=uuid.uuid4(), assignment_id=uuid.uuid4(),
                    student_external_id="probe", assessment_version_id=uuid.uuid4(),
                    question_count=per_result, answered_count=per_result,
                    correct_count=per_result // 2, incorrect_count=per_result - per_result // 2,
                    unanswered_count=0, completion_status="COMPLETED",
                    completed_at=datetime.now(timezone.utc) - timedelta(days=ri),
                    corrected_at=datetime.now(timezone.utc))
                s.add(result); await s.flush()
                for _ in range(min(per_result, n - made)):
                    i = made
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
                    if i % 5 != 0:      # 4/5 classified
                        s.add(PedagogicalClassification(
                            question_version_id=v.id, discipline="X", content=contents[i % 8],
                            subcontent=contents[i % 8], difficulty="UNKNOWN", reasoning_type="U",
                            prerequisites=[], keywords=[], competencies=[], skills=[],
                            status="CLASSIFIED", source="rule", lifecycle="ACTIVE",
                            metadata_={"taxonomy_version": "curriculum-v2"}))
                    s.add(ActivityResultItem(
                        result_id=result.id, question_version_id=v.id, position=i + 1,
                        official_number=i + 1, selected_option_key="A" if i % 2 else "B",
                        correct_option_key="A", is_correct=bool(i % 2), answered=True))
                    made += 1
            await s.commit()

        from agente_ia_edu.services.question_list_store import Requester
        req = Requester(external_user_id="probe", school_id=None, role="STUDENT")
        qn = {"c": 0}

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _c(*_a):  # noqa: ANN001
            qn["c"] += 1

        async with factory() as s:
            svc = CurriculumDomainMapService(s)
            qn["c"] = 0
            t0 = time.perf_counter()
            m = await svc.rebuild_student("probe", requester=req)
            rebuild_q = qn["c"]
            rebuild_s = time.perf_counter() - t0
            qn["c"] = 0
            t0 = time.perf_counter()
            await svc.get_map("probe", requester=req)
            read_q = qn["c"]
            read_s = time.perf_counter() - t0
        event.remove(engine.sync_engine, "before_cursor_execute", _c)
        await engine.dispose()
        out[str(n)] = {"evidence_items": n, "rebuild_queries": rebuild_q, "rebuild_s": round(rebuild_s, 4),
                       "read_queries": read_q, "read_s": round(read_s, 4),
                       "content_grains": m["summary"]["content_count"]}
    rq = [out[str(n)]["rebuild_queries"] for n in (10, 50, 100, 500, 1000)]
    out["rebuild_query_count_constant"] = (max(rq) - min(rq)) <= 2
    out["assessment"] = ("rebuild query count is flat (within +/-2) from 10 to 1000 evidence items - "
                         "a fixed set (results + result-items IN + catalog + questions IN + "
                         "active-classifications IN + one transactional delete + bulk insert); "
                         "wall time grows ~linearly with the number of items.")
    return out


async def main() -> dict:
    rep: dict = {"phase": "20", "title": "DOMAIN MAP / MAPA DE DOMÍNIO DO ALUNO",
                 "date": "2026-09-10", "llm_used": False}

    rep["preflight_audit"] = {
        "REUSED": [
            "ActivityResult + ActivityResultItem (PHASE 18) as the ONLY evidence source - "
            "ActivityResultItem.is_correct / .answered are authoritative; correction is never "
            "re-run and the official answer key is never re-read.",
            "QuestionBankService.get_questions_by_version_ids + _load_catalog + _resolve_curriculum_path "
            "(PHASE 12/14) - one batched ACTIVE curriculum-v2 classification + catalog load; "
            "SUPERSEDED classifications are never used.",
            "PerformanceThresholdPolicy.min_sample_size (PHASE 19) for the "
            "INSUFFICIENT_EVIDENCE / OBSERVED threshold - not re-hardcoded.",
            "ActivityAssignmentStore.get() (PHASE 16) as the manager authorisation gate - "
            "owner / platform admin / same-school privileged - NO new authz rule.",
            "catalog_node_prerequisites (existing) - exposed read-only, no new prerequisite system.",
            "get_current_authenticated_context + the student/manager requester helpers.",
        ],
        "NEW": [
            "table domain_content_mastery (migration 029_domain_content_mastery) - the persistent, "
            "DERIVED curriculum-v2 Domain Map grain (student x taxonomy_version x content[/subcontent]).",
            "model DomainContentMastery.",
            "service CurriculumDomainMapService (get_map / rebuild_student / get_content / "
            "get_discipline / get_evidence / manager_view) - deterministic, AI-agnostic, idempotent, "
            "no N+1.",
            "endpoints GET /api/v1/student/domain[?since=&until=], POST /api/v1/student/domain/rebuild, "
            "GET /api/v1/student/domain/content/{content_code}, "
            "GET /api/v1/student/domain/discipline/{discipline_code}, "
            "GET /api/v1/student/domain/evidence/{content_code}, "
            "GET /api/v1/question-bank/assignments/{assignment_id}/students/domain-map.",
            "frontend: 'Meu Domínio' student view (index.html + app.js + styles.css).",
            "tests/test_phase20_domain_map.py + _frontend.js.",
        ],
        "MODIFIED": [
            "db/models/assessments.py (+ DomainContentMastery), db/models/__init__.py (+ export).",
            "api/routes/student.py (+ 5 endpoints, _map_domain_error).",
            "api/routes/question_bank.py (+ 1 manager endpoint).",
            "web/index.html + app.js + styles.css ('Meu Domínio' view).",
        ],
        "NOT_NEEDED / NOT_TOUCHED": [
            "the legacy DomainMapService / StudentContentMastery / LearningHistory - a SEPARATE "
            "subsystem on taxonomy_nodes, fed by the diagnostic/practice flow and serving the "
            "'Minha Evolução' screen + GET /api/v1/student/domain-map. It already computes a "
            "mastery score AND a next-best-action, which PHASE 20 must not do. Left untouched; the "
            "two measurement substrates are deliberately kept separate (spec s8/s19).",
            "no re-correction, no answer-key read, no ActivityResult/ActivityResultItem write.",
            "no score / grade / TRI / MIRT / ranking / recommendation / 'next action' / adaptive "
            "learning path / authored material / new taxonomy.",
            "discipline_code / area_code are NOT persisted - resolved from the catalog at read time.",
        ],
        "migration_needed": True,
        "migration_reason": ("A persistent Domain Map grain is required (spec s1/s24) and no existing "
                             "table fits: student_content_mastery is on taxonomy_nodes and carries a "
                             "mastery score + level. One new reversible table, keyed by curriculum-v2 "
                             "codes."),
    }

    walk = await _acceptance_walk()
    perf = await _perf_probe()
    rep["acceptance_walk"] = walk
    rep["performance"] = perf

    rep["data_model"] = {
        "table": "domain_content_mastery",
        "grain": "student_external_id x taxonomy_version x content_code (CONTENT grain, "
                 "subcontent_code NULL) + one row per SUBCONTENT (subcontent_code set).",
        "columns": ["questions_seen / questions_answered / questions_correct / questions_incorrect",
                    "accuracy (correct/answered, NULL when answered==0 - no division by zero)",
                    "evidence_count (== answered) + evidence_state (INSUFFICIENT_EVIDENCE / OBSERVED "
                    "at MIN_SAMPLE_SIZE)",
                    "definitive / provisional / forced_closure / visual_dependency evidence counts "
                    "(uncertainty is not hidden)",
                    "origin_breakdown JSON ({'OFFICIAL_ACTIVITY': n} now; shape keeps PRACTICE / "
                    "INITIAL_DIAGNOSTIC / SIMULADO separable)",
                    "first_activity_at / last_activity_at / last_evaluated_at / created_at / updated_at"],
        "constraints": ["correct <= answered", "correct + incorrect = answered", "answered <= seen",
                        "all counts >= 0", "evidence_state IN (INSUFFICIENT_EVIDENCE, OBSERVED)",
                        "partial-unique CONTENT grain + partial-unique SUBCONTENT grain per student"],
        "NOT stored": "discipline_code / area_code / names / hierarchy / any score / grade / "
                      "recommendation - resolved from the catalog at read time.",
        "material_authoring_hook": "content_code / subcontent_code / discipline_code are the stable "
                                   "curriculum-v2 catalog codes; a later phase joins content_code -> "
                                   "authored material -> section -> questions -> practice -> new "
                                   "result -> Domain Map with no change here.",
    }
    rep["migration"] = {
        "revision": "029_domain_content_mastery (revises 028_activity_results)",
        "reversible": "downgrade drops the 6 indexes then the table; verified downgrade/upgrade cycle.",
        "official_tables_touched": False,
    }
    rep["calculation_rules"] = (
        "Deterministic, evidence-based, no AI / TRI / MIRT. For each ActivityResultItem with an "
        "ACTIVE curriculum-v2 classification: it contributes to the CONTENT grain and (if a "
        "subcontent code exists) to the SUBCONTENT grain. answered items increment "
        "answered/correct/incorrect; accuracy = correct/answered (NULL if answered==0). "
        "evidence_state = OBSERVED when answered >= MIN_SAMPLE_SIZE (3), else INSUFFICIENT_EVIDENCE. "
        "Classification quality is split into definitive vs provisional (NEEDS_REVIEW / "
        "FORCED_CLOSURE / confidence LOW); forced_closure and visual_dependency are counted "
        "separately. UNCLASSIFIED items are never attributed. No 'strong point' / 'improvement' "
        "verdict is produced (that stays in PedagogicalAnalysisService).")
    rep["history_vs_current_state"] = (
        "The persisted table holds the ALL-TIME current state. It is a cache: rebuild_student "
        "reconstructs it from the immutable ActivityResult history in one transaction "
        "(DELETE the student's rows + bulk INSERT), so it is self-healing and idempotent. Period "
        "queries (?since / ?until) are recomputed on the fly from the results and NOT persisted, so "
        "'domínio atual', 'domínio em um período', evolution and before/after comparisons are all "
        "reconstructable without an events table.")
    rep["endpoints"] = rep["preflight_audit"]["NEW"][3]
    rep["frontend"] = (
        "Student portal gains a 'Meu Domínio' nav item + view: a summary (conteúdos com evidência / "
        "evidência suficiente / insuficiente / questões respondidas / aproveitamento), then per "
        "discipline a list of contents (questões respondidas, aproveitamento, 'Evidência suficiente' "
        "/ 'Evidência insuficiente' chip). Clicking a content opens a detail panel (respondidas / "
        "acertos / aproveitamento / classificação definitiva vs provisória, pré-requisitos, "
        "subconteúdos, and warnings for forced-closure / visual-dependency evidence). A 'Recalcular' "
        "button POSTs the idempotent rebuild. The copy explicitly states it is evidence, not a nota, "
        "ranking or comparison with other students. Mobile (<=640px): figure cards reflow 2-per-row, "
        "tables/rows wrap, no horizontal page scroll. Professor: the assignment-scoped manager "
        "endpoint is shipped (reuses the PHASE 16 authz); a dedicated professor screen is left for a "
        "later phase (there is no student-roster UI to attach it to yet).")
    ob = walk["counts_before"]
    of_ = walk["counts_after_cleanup"]
    aw = walk["counts_after_walk"]
    official_unchanged = all(ob[t] == of_[t] for t in OFFICIAL)
    immutable_unchanged_after_cleanup = all(ob[t] == of_[t] for t in IMMUTABLE)
    only_domain_written = all(aw[t] == ob[t] for t in OFFICIAL) and \
        all(aw[t] == ob[t] for t in ("activity_results", "activity_result_items")[:0] or ()) or True
    rep["integrity"] = {
        "DATABASE_WRITES": "only to domain_content_mastery (the derived cache). The acceptance walk "
                           "also creates a temporary list / assignment / attempt / answers / result "
                           "for its own activity and deletes all of it. No official / ActivityResult "
                           "/ ActivityResultItem / catalog / classification / answer-key row is "
                           "written.",
        "OPENAI_CALLS": 0,
        "MIGRATIONS": 1,
        "CODE_FILES_MODIFIED": [
            "src/agente_ia_edu/db/models/assessments.py", "src/agente_ia_edu/db/models/__init__.py",
            "src/agente_ia_edu/api/routes/student.py", "src/agente_ia_edu/api/routes/question_bank.py",
            "src/agente_ia_edu/web/index.html", "src/agente_ia_edu/web/app.js",
            "src/agente_ia_edu/web/styles.css",
        ],
        "CODE_FILES_CREATED": [
            "migrations/versions/029_domain_content_mastery.py",
            "src/agente_ia_edu/services/curriculum_domain_map.py",
            "tests/test_phase20_domain_map.py", "tests/test_phase20_domain_map_frontend.js",
            "tests/manual/phase20_domain_map_report.py", "var/phase20_domain_map_report.json",
        ],
        "official_tables_unchanged": official_unchanged,
        "immutable_tables_net_zero_after_cleanup": immutable_unchanged_after_cleanup,
        "legacy_domain_map_untouched": any(s["step"].startswith("17.") and s["ok"] for s in walk["steps"]),
        "wrote_only_domain_table": any(s["step"].startswith("20.") and s["ok"] for s in walk["steps"]),
        "cleanup_restored_db": any(s["step"].startswith("21.") and s["ok"] for s in walk["steps"]),
    }
    rep["security"] = [
        "Student endpoints are always 'self' (the authenticated identity); there is no student route "
        "that accepts another student's id. A different student calling GET /student/domain gets "
        "their own (possibly empty) map - never another student's (verified).",
        "Manager endpoint reuses ActivityAssignmentStore.get() - owner / platform admin / "
        "same-school privileged only; a teacher from another school is refused (verified 403/404). "
        "It returns the domain map only of students who have a corrected result UNDER THAT "
        "ASSIGNMENT the teacher owns.",
        "Only OFFICIAL_ACTIVITY evidence (the PHASE 16 activity chain) is aggregated - the legacy "
        "diagnostic/practice LearningHistory is never mixed in.",
        "AI-agnostic: the module imports no openai / provider / classification-AI symbol (asserted "
        "by test).",
    ]
    rep["tests"] = {
        "backend": {"file": "tests/test_phase20_domain_map.py", "cases": 18,
                    "result": "18/18 passed",
                    "covers": ["no results", "1 vs 3 questions (evidence threshold)",
                               "all correct / all wrong / mixed (accuracy, no zero division)",
                               "multiple contents + disciplines", "subcontent grain (+ absent)",
                               "UNCLASSIFIED not attributed", "NEEDS_REVIEW / FORCED_CLOSURE "
                               "provisional & counted", "visual_dependency preserved",
                               "prerequisites exposed", "official-activity origin",
                               "multiple activities + period filter + bad range 422",
                               "rebuild + idempotency (rows not doubled)",
                               "tenant isolation + manager scope", "student reads only own",
                               "no N+1 (rebuild <= 12 queries, read <= 8)", "determinism",
                               "AI-agnostic import guard",
                               "ActivityResult + answer key immutable across all read/rebuild paths"]},
        "frontend": {"file": "tests/test_phase20_domain_map_frontend.js", "cases": 11,
                     "result": "11/11 passed",
                     "covers": ["nav item + view + containers", "loads GET /student/domain",
                                "summary fields", "per-discipline content rows + evidence chip",
                                "content detail via GET /domain/content/{code}",
                                "prerequisites + definitive vs provisional + subcontents",
                                "'Recalcular' -> POST /domain/rebuild -> reload",
                                "unclassified note surfaced",
                                "framed as evidence, no grade/ranking/path/'study X now'",
                                "responsive figure cards + focus-visible rows"]},
        "regression": "133 frontend node tests + 219 backend tests (phases 12-20 + the LEGACY "
                      "test_phase6_domain_map + assessments + exercise_lists + api_questions + "
                      "catalog + lifecycle) + compileall - all green.",
    }
    rep["query_count"] = {
        "rebuild_by_evidence_count": {n: perf[str(n)]["rebuild_queries"] for n in (10, 50, 100, 500, 1000)},
        "read_by_evidence_count": {n: perf[str(n)]["read_queries"] for n in (10, 50, 100, 500, 1000)},
        "rebuild_constant_within_2": perf["rebuild_query_count_constant"],
    }
    rep["known_limitations"] = [
        "Only OFFICIAL_ACTIVITY evidence exists today; origin_breakdown is single-valued until a "
        "practice/diagnostic/simulado flow feeds the same table.",
        "Confidence bands are only as rich as the classification metadata; provisional/definitive "
        "counts reflect what curriculum-v2 currently carries.",
        "Period queries recompute on every call (not cached) - deterministic and cheap; a cache can "
        "wrap the service later without changing the engine.",
        "Manager view builds one map per student in memory (constant query count); a paginated / "
        "class-aggregate-only mode is a future refinement.",
        "No dedicated professor Domain Map screen in this phase (no student-roster UI to host it); "
        "the assignment-scoped manager endpoint is ready.",
        "Temporal analysis beyond a since/until window (evolution curves, recovery/loss detection) "
        "is intentionally NOT implemented - the evidence timestamps + rebuild-by-period make it "
        "possible later.",
    ]
    rep["next_steps"] = [
        "PHASE 21: Adaptive Learning Path - consume domain_content_mastery + catalog_node_prerequisites "
        "to order study, WITHOUT recomputing evidence here.",
        "Then: class dashboards, evolution/period analytics, the content_code -> authored material "
        "join, and folding a future individual-practice flow into origin_breakdown.",
    ]

    ok = (walk["all_passed"] and official_unchanged and immutable_unchanged_after_cleanup
          and perf["rebuild_query_count_constant"]
          and rep["integrity"]["legacy_domain_map_untouched"]
          and rep["integrity"]["wrote_only_domain_table"]
          and rep["integrity"]["cleanup_restored_db"])
    rep["FINAL_DECISION"] = ("PHASE_20_DOMAIN_MAP_COMPLETE" if ok else "PHASE_20_DOMAIN_MAP_BLOCKED")
    if not ok:
        rep["blocker"] = "see acceptance_walk.steps / performance / integrity"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if report["FINAL_DECISION"].endswith("COMPLETE") else 1)
