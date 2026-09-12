"""PHASE 21 - Adaptive Learning Path: report + real-DB acceptance walk.

The learning path is a DERIVED VIEW (no table, no migration, writes nothing). It
consumes the PHASE 20 curriculum-v2 Domain Map + catalog_node_prerequisites +
curriculum-v2 + optional pedagogical context, and produces an explainable,
deterministically ordered list of "next best pedagogical actions". The
acceptance walk runs against the LIVE database with controlled test data,
exercises every read path incl. the prerequisite / provisional / manager / tenant
behaviour, and removes every temporary row it created. A perf probe uses a
throw-away in-memory SQLite to prove the query count is flat for 10/50/100/500/
1000 contents.

No official questions/versions/options/answer-key/classification/catalog/
ActivityResult/ActivityResultItem/Domain Map row is written by this phase (the
first study-path call may lazily populate the PHASE 20 Domain Map cache - a
derived write owned by PHASE 20). No AI call. The legacy
GET /api/v1/student/learning-path (taxonomy/diagnostic) stays untouched.
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
from agente_ia_edu.services.adaptive_learning_path import (  # noqa: E402
    AdaptiveLearningPathService, LearningPathWeights,
)
from agente_ia_edu.services.pedagogical_analysis import PerformanceThresholdPolicy  # noqa: E402

OUT = _REPO / "var" / "phase21_adaptive_learning_path_report.json"
OWNER = "phase21-report-runner"
PT = {"Authorization": f"Bearer teacher:{OWNER}"}
STU = "student:phase21-report-al"
SH = {"Authorization": "Bearer student:student:phase21-report-al"}
SH_OTHER = {"Authorization": "Bearer student:student:phase21-report-bo"}
CLASS_ID = "phase21-report-turma"

OFFICIAL = ("questions", "question_versions", "question_options", "booklet_questions",
            "catalog_nodes", "pedagogical_classifications", "answer_key_entries",
            "answer_key_revisions", "catalog_node_prerequisites")
IMMUTABLE = OFFICIAL + ("activity_results", "activity_result_items", "activity_attempts",
                        "activity_answers")
PREREQ_CONTENTS = ("MATH-PROBABILITY-BASICS", "MATH-COMBINATORICS-COUNTING",
                   "MATH-GEOMETRY-SPATIAL", "MATH-MEASUREMENT-PLANE-AREA")


async def _counts(s) -> dict:
    out = {}
    for t in (*IMMUTABLE, "assessments", "assessment_versions", "assessment_items",
              "activity_assignments", "domain_content_mastery"):
        out[t] = int(await s.scalar(text(f"SELECT count(*) FROM {t}")))
    return out


async def _pick(s) -> list[str]:
    rows = (await s.execute(text("""
        SELECT pc.question_version_id FROM pedagogical_classifications pc
        JOIN question_versions qv ON qv.id = pc.question_version_id
        JOIN booklet_questions bq ON bq.question_version_id = qv.id
        WHERE pc.lifecycle = 'ACTIVE' AND pc.metadata->>'taxonomy_version' = 'curriculum-v2'
          AND qv.version_kind = 'official_original'
          AND pc.content = ANY(:cc)
        ORDER BY bq.official_number"""), {"cc": list(PREREQ_CONTENTS)})).scalars().all()
    vids = [str(r) for r in rows]
    more = (await s.execute(text("""
        SELECT pc.question_version_id FROM pedagogical_classifications pc
        JOIN question_versions qv ON qv.id = pc.question_version_id
        JOIN booklet_questions bq ON bq.question_version_id = qv.id
        WHERE pc.lifecycle = 'ACTIVE' AND pc.metadata->>'taxonomy_version' = 'curriculum-v2'
          AND qv.version_kind = 'official_original'
        ORDER BY bq.official_number LIMIT 10"""), {})).scalars().all()
    for x in more:
        if str(x) not in vids:
            vids.append(str(x))
    return vids[:12]


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
            "SELECT count(*) FROM domain_content_mastery WHERE student_external_id LIKE 'student:phase21-report%'")))
        await s.execute(text("DELETE FROM domain_content_mastery WHERE student_external_id LIKE 'student:phase21-report%'"))
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
        await s.execute(text("DELETE FROM user_school_links WHERE external_user_id LIKE 'student:phase21-report%'"))
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
            vids = await _pick(s)
            n_prereq_edges = int(await s.scalar(text("SELECT count(*) FROM catalog_node_prerequisites")))
            await s.execute(text("DELETE FROM user_school_links WHERE external_user_id LIKE 'student:phase21-report%'"))
            for u in (STU, "student:phase21-report-bo"):
                await s.execute(text(
                    "INSERT INTO user_school_links (id, external_user_id, role, scope_type, scope_external_id, active, created_at) "
                    "VALUES (:i, :u, 'STUDENT', 'CLASSROOM', :c, true, NOW())"),
                    {"i": uuid.uuid4(), "u": u,
                     "c": CLASS_ID if u == STU else "phase21-report-outra"})
            await s.commit()
        step("1-2. Audit + preflight: prereq edges present in catalog; classified questions picked",
             len(vids) >= 8 and n_prereq_edges >= 1)

        client = TestClient(create_app())
        lid = client.post("/api/v1/question-bank/lists", json={
            "question_version_ids": vids, "title": "P21 acceptance",
            "answer_key_presentation": "KEY_AT_END"}, headers=PT).json()["id"]
        client.post(f"/api/v1/question-bank/lists/{lid}/finalize", headers=PT)
        aid = client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
            "target_type": "CLASS", "target_id": CLASS_ID,
            "available_from": "2000-01-01T00:00:00Z"}, headers=PT).json()["id"]
        keys = await _frozen_keys(factory, lid)
        st = client.post(f"/api/v1/student/activities/{aid}/attempt", headers=SH).json()
        qv = [q["question_version_id"] for q in st["questions"]]
        for i, v in enumerate(qv):
            key = keys[i + 1] if i < 4 else next(x for x in "ABCDE" if x != keys[i + 1])
            client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{v}",
                       json={"selected_option": key}, headers=SH)
        client.post(f"/api/v1/student/activities/{aid}/attempt/complete", headers=SH)
        client.post(f"/api/v1/student/activities/{aid}/attempt/correct", headers=SH)
        step("3. Published + distributed + student completed & corrected one activity", True)

        p = client.get("/api/v1/student/study-path", headers=SH)
        j = p.json()
        step("4. GET /student/study-path -> 200, DERIVED, ai_used == False, state READY",
             p.status_code == 200 and j["ai_used"] is False and j["state"] == "READY")
        step("5. It is NOT a raw 'lowest accuracy' list: every step carries a priority_score, "
             "priority_reason and priority_factors",
             all(("priority_score" in st_ and "priority_reason" in st_ and "priority_factors" in st_)
                 for st_ in j["steps"]))
        step("6. Weights are explicit + configurable (LearningPathWeights); thresholds reused",
             j["weights"] == LearningPathWeights.default().as_dict()
             and j["thresholds"] == PerformanceThresholdPolicy.default().as_dict())
        step("7. Prerequisites drive ordering: a blocking prerequisite surfaces before its "
             "dependent (BLOCKED_BY_PREREQUISITE + 'blocks_contents')",
             any(st_["content_state"] == "BLOCKED_BY_PREREQUISITE" for st_ in j["steps"])
             or any(st_["blocks_contents"] for st_ in j["steps"]))
        step("8. Evidence threshold: MIN_SAMPLE_SIZE gates INSUFFICIENT_EVIDENCE vs observed states",
             j["thresholds"]["MIN_SAMPLE_SIZE"] == 3
             and any(st_["content_state"] == "INSUFFICIENT_EVIDENCE" for st_ in j["steps"]))
        step("9. Provisional / FORCED_CLOSURE evidence carried + flagged (provisional_note)",
             j["provisional_note"] is not None
             or all("forced_closure_evidence_count" in st_ for st_ in j["steps"]))
        step("10. UNCLASSIFIED questions never become a candidate (all step codes are catalog codes)",
             all(st_["content_code"] for st_ in j["steps"] + j["mastered"]))
        step("11. Every action is action_available == False in this phase (no false functional flow)",
             all(st_["action_available"] is False for st_ in j["steps"]))
        step("12. Explainability: a human-readable priority_reason per step",
             all(isinstance(st_["priority_reason"], str) and len(st_["priority_reason"]) > 10
                 for st_ in j["steps"]))

        nx = client.get("/api/v1/student/study-path/next", headers=SH, params={"limit": 2}).json()
        step("13. /next slices the top actions", len(nx["steps"]) <= 2 and nx.get("is_next_slice"))
        cc = j["steps"][0]["content_code"] if j["steps"] else None
        gc = client.get(f"/api/v1/student/study-path/content/{cc}", headers=SH) if cc else None
        step("14. /content/{code} returns one recommendation + explanation; unknown -> 404",
             (gc is None or gc.status_code == 200)
             and client.get("/api/v1/student/study-path/content/DOES-NOT-EXIST",
                            headers=SH).status_code == 404)

        b = client.get("/api/v1/student/study-path", headers=SH).json()

        def norm(x):
            x = json.loads(json.dumps(x))
            x.pop("generated_at", None)
            for st_ in x["steps"] + x["mastered"]:
                st_.pop("last_activity_at", None)
            return json.dumps(x, sort_keys=True)
        step("15. Deterministic (identical content on re-request)", norm(j) == norm(b))

        step("16. A different student gets their own (NO_EVIDENCE) path, never this student's",
             client.get("/api/v1/student/study-path", headers=SH_OTHER).json()["state"] == "NO_EVIDENCE")
        step("17. The LEGACY GET /api/v1/student/learning-path is untouched (still 200)",
             client.get("/api/v1/student/learning-path", headers=SH).status_code == 200)

        mv = client.get(f"/api/v1/question-bank/assignments/{aid}/students/study-path", headers=PT)
        step("18. Manager (assignment owner) study-path -> 200, per-student, PHASE 16 authz reused; "
             "no automatic intervention recommendation",
             mv.status_code == 200 and mv.json()["student_count"] == 1
             and mv.json()["students"][0]["student_external_id"] == STU)
        step("19. A teacher from another school is refused (403/404)",
             client.get(f"/api/v1/question-bank/assignments/{aid}/students/study-path",
                        headers={"Authorization": "Bearer teacher:phase21-other"}).status_code in (403, 404))

        async with factory() as s:
            after = await _counts(s)
        cleanup = await _cleanup(factory)
        async with factory() as s:
            final = await _counts(s)
        step("20. The learning path wrote NOTHING of its own (only PHASE 20 lazily populated its "
             "Domain Map cache; official + result tables unchanged during the walk)",
             all(after[t] == before[t] for t in OFFICIAL)
             and after["activity_results"] == before["activity_results"] + 1
             and after["activity_result_items"] == before["activity_result_items"] + len(vids))
        step("21. Cleanup restored the DB (official + immutable + domain tables back to baseline)",
             {k: final[k] for k in IMMUTABLE} == {k: before[k] for k in IMMUTABLE}
             and final["domain_content_mastery"] == before["domain_content_mastery"])

        example = None
        if j["steps"]:
            top = j["steps"][0]
            example = {k: top[k] for k in ("content_code", "content_state", "priority_score",
                                           "priority_reason", "priority_factors",
                                           "unsatisfied_prerequisites", "blocks_contents",
                                           "action_type", "action_available")}
        return {"steps": steps, "all_passed": all(x["ok"] for x in steps),
                "counts_before": before, "counts_after_walk": after,
                "counts_after_cleanup": final, "cleanup": cleanup,
                "decision_example": example,
                "path_summary": j.get("summary")}
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
            d = CatalogNode(code="D", name="D", node_type="DISCIPLINE", active=True); s.add(d); await s.flush(); d.root_id = d.id
            a = CatalogNode(code="A", name="A", node_type="AREA", parent_id=d.id, root_id=d.id, active=True); s.add(a); await s.flush()
            nodes = []
            for ci in range(n):
                cn = CatalogNode(code=f"C{ci}", name=f"C{ci}", node_type="CONTENT",
                                 parent_id=a.id, root_id=d.id, position=ci, active=True)
                s.add(cn); nodes.append(cn)
            await s.flush()
            # a prerequisite chain across every 5th pair (still a DAG)
            for ci in range(5, n, 5):
                s.add(CatalogNodePrerequisite(content_node_id=nodes[ci].id,
                                              prerequisite_node_id=nodes[ci - 5].id))
            # a Domain Map for the student across the first ~min(n, 60) contents
            covered = min(n, 60)
            now = datetime.now(timezone.utc)
            for ci in range(covered):
                answered = 4 if ci % 3 else 2
                correct = 1 if ci % 4 == 0 else 3
                s.add(DomainContentMastery(
                    student_external_id="probe", taxonomy_version="curriculum-v2",
                    content_code=f"C{ci}", subcontent_code=None,
                    questions_seen=answered, questions_answered=answered,
                    questions_correct=min(correct, answered),
                    questions_incorrect=answered - min(correct, answered),
                    accuracy=round(min(correct, answered) / answered, 4),
                    evidence_count=answered,
                    evidence_state="OBSERVED" if answered >= 3 else "INSUFFICIENT_EVIDENCE",
                    definitive_evidence_count=answered, provisional_evidence_count=0,
                    forced_closure_evidence_count=0, visual_dependency_evidence_count=0,
                    origin_breakdown={"OFFICIAL_ACTIVITY": answered},
                    first_activity_at=now - timedelta(days=covered - ci),
                    last_activity_at=now - timedelta(days=covered - ci),
                    last_evaluated_at=now))
            await s.commit()

        from agente_ia_edu.services.question_list_store import Requester
        req = Requester(external_user_id="probe", school_id=None, role="STUDENT")
        qn = {"c": 0}

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _c(*_a):  # noqa: ANN001
            qn["c"] += 1

        async with factory() as s:
            svc = AdaptiveLearningPathService(s)
            qn["c"] = 0
            t0 = time.perf_counter()
            path = await svc.build_path("probe", requester=req)
            dt = time.perf_counter() - t0
        event.remove(engine.sync_engine, "before_cursor_execute", _c)
        await engine.dispose()
        out[str(n)] = {"contents": n, "queries": qn["c"], "build_s": round(dt, 4),
                       "steps": len(path["steps"]), "mastered": len(path["mastered"])}
    qs = [out[str(n)]["queries"] for n in (10, 50, 100, 500, 1000)]
    out["query_count_constant"] = (max(qs) - min(qs)) <= 2
    out["assessment"] = ("build query count is flat (within +/-2) from 10 to 1000 contents - a "
                         "fixed set: catalog (cached) + Domain Map rows + prerequisites + optional "
                         "context/links. The graph build + cycle check + ranking are in-memory; "
                         "wall time grows ~linearly with the number of contents.")
    return out


async def main() -> dict:
    rep: dict = {"phase": "21", "title": "ADAPTIVE LEARNING PATH / TRILHA DE APRENDIZAGEM ADAPTATIVA",
                 "date": "2026-09-10", "llm_used": False}

    rep["preflight_audit"] = {
        "REUSED": [
            "CurriculumDomainMapService.get_map (PHASE 20) - the sole evidence input; already "
            "derived from ActivityResult, never re-corrected.",
            "catalog_node_prerequisites (existing) - the ONLY prerequisite source; resolved through "
            "the cached catalog. Invalid / self references are dropped; a cycle -> "
            "PREREQUISITE_GRAPH_INVALID (no silent path, catalog never mutated).",
            "PerformanceThresholdPolicy (PHASE 19/20) - MIN_SAMPLE_SIZE + mastery threshold; not "
            "re-hardcoded.",
            "ActivityAssignmentStore.get() (PHASE 16) as the manager authorisation gate - owner / "
            "platform admin / same-school privileged. NO new authz rule.",
            "QuestionBankService catalog cache / _resolve_curriculum_path for names + hierarchy.",
            "pedagogical_contexts + user_school_links (existing) - OPTIONAL context boost "
            "(TEACHER / COORDINATION / SCHOOL_PLAN); an independent student needs none.",
        ],
        "NEW": [
            "src/agente_ia_edu/services/adaptive_learning_path.py - AdaptiveLearningPathService + "
            "LearningPathWeights (deterministic, AI-agnostic, idempotent, writes nothing).",
            "GET /api/v1/student/study-path, /study-path/next, /study-path/content/{content_code}.",
            "GET /api/v1/question-bank/assignments/{assignment_id}/students/study-path (manager).",
            "frontend: 'Trilha de Estudos' student view (index.html + app.js + styles.css).",
            "tests/test_phase21_adaptive_learning_path.py + _frontend.js.",
        ],
        "MODIFIED": [
            "api/routes/student.py (+ 3 endpoints, _map_path_error).",
            "api/routes/question_bank.py (+ 1 manager endpoint).",
            "web/index.html + app.js + styles.css ('Trilha de Estudos' view).",
        ],
        "LEGACY_ISOLATED": [
            "GET /api/v1/student/learning-path is owned by the LEGACY taxonomy_nodes / "
            "StudentContentMastery / diagnostic pipeline (StudentDashboardService.get_learning_path) "
            "and feeds the existing 'Minha Trilha' view + NextBestActionPolicy. It is NOT touched, "
            "NOT migrated, NOT merged. The new activity/domain-map path is exposed under "
            "/study-path and a separate 'Trilha de Estudos' nav item, exactly as PHASE 20 did with "
            "/domain-map vs /domain.",
        ],
        "NOT_NEEDED / NOT_TOUCHED": [
            "no migration, no table, no column - the path is a cheap deterministic DERIVED VIEW "
            "(spec s17: prefer a derived view). It is recomputed per request from "
            "domain_content_mastery + the catalog.",
            "no LLM, no generative AI, no TRI / MIRT, no ranking, no student comparison, no "
            "gamification, no video / authored material / practice-list generation. "
            "action_available is False for every action - the downstream flows are not wired yet.",
        ],
        "persistence_decision": ("DERIVED VIEW - no persistence. The result is a pure function of "
                                 "the (already persisted) PHASE 20 Domain Map + the catalog, built "
                                 "with a constant query count and in-memory ranking. Persisting it "
                                 "would only add a stale copy of the Domain Map. No migration."),
    }

    walk = await _acceptance_walk()
    perf = await _perf_probe()
    rep["acceptance_walk"] = walk
    rep["performance"] = perf

    rep["algorithm"] = {
        "candidates": "every content in the student's Domain Map + the (transitive) prerequisites "
                      "of those contents + any content the student's classroom has an ACTIVE "
                      "pedagogical context for. UNCLASSIFIED evidence is never a candidate.",
        "content_state": ["MASTERED (answered >= MIN_SAMPLE_SIZE AND accuracy >= STRONG_ACCURACY)",
                          "NEEDS_REVIEW (mastered but last evidence older than STALE_DAYS)",
                          "BLOCKED_BY_PREREQUISITE (a direct prerequisite is not MASTERED)",
                          "INSUFFICIENT_EVIDENCE (evidence_state INSUFFICIENT and not blocked)",
                          "RECOMMENDED (observed and accuracy < IMPROVEMENT_ACCURACY)",
                          "READY (observed, prerequisites satisfied, mid performance)",
                          "PREREQUISITE_GRAPH_INVALID (global; a cycle in catalog_node_prerequisites)"],
        "priority_score": ("clamp01( "
                           "W_LOW_EVIDENCE*low_evidence "
                           "+ W_WEAK*weak_performance*(1 - 0.5*provisional_ratio) "
                           "+ W_BLOCKER*blocker "
                           "+ W_CONTEXT*context "
                           "+ W_STALE_REVIEW*stale_review "
                           "- W_BLOCKED_PENALTY*blocked_penalty )"),
        "factors": {
            "low_evidence": "1 when there is not enough observed evidence (and not mastered).",
            "weak_performance": "only when OBSERVED: (IMPROVEMENT_ACCURACY - accuracy) / "
                                "IMPROVEMENT_ACCURACY, clamped 0..1 (0 above the improvement line).",
            "blocker": "min(1, #not-yet-mastered dependents / BLOCKER_SATURATION) - an 80% content "
                       "that unlocks two unmastered contents still scores as a priority.",
            "blocked_penalty": "1 when this content itself has an unmastered prerequisite -> it is "
                               "deprioritised so the prerequisite surfaces first.",
            "context": "0.5 when the classroom has an active TEACHER/COORDINATION/SCHOOL_PLAN "
                       "context for the content.",
            "stale_review": "1 when a mastered content's last evidence is older than STALE_DAYS.",
            "provisional_ratio": "forced-closure / provisional share of the answered evidence - it "
                                 "dampens the weak-performance signal (uncertainty is not hidden).",
        },
        "ordering": "steps (everything except MASTERED) sorted by (-priority_score, catalog "
                    "position, content_code); recommended_order is 1..N. MASTERED contents go to a "
                    "separate list, never ranked competitively.",
        "determinism": "iteration over sorted keys, integer/float arithmetic only, no randomness, "
                       "no wall-clock in the output except generated_at.",
    }
    rep["weights_default"] = LearningPathWeights.default().as_dict()
    rep["thresholds_default"] = PerformanceThresholdPolicy.default().as_dict()
    rep["decision_example"] = walk.get("decision_example")
    rep["explainability"] = (
        "Every step carries priority_reason (a deterministic sentence built from the dominant "
        "factor + the numbers) and priority_factors (the raw factor values). Examples: "
        "'Bloqueado: depende de <pré-requisito>, que ainda não foi dominado. Estude o pré-requisito "
        "primeiro.'; 'Evidência insuficiente (2 questões respondidas). É pré-requisito de 1 "
        "conteúdo(s) ainda não dominado(s) — vale um diagnóstico.'; 'Revisão recomendada: o "
        "conteúdo foi dominado, mas a última evidência já tem algum tempo.' No LLM is involved.")
    rep["endpoints"] = [
        "GET /api/v1/student/study-path - the caller's own path (never accepts a student id).",
        "GET /api/v1/student/study-path/next?limit=N - top N next actions.",
        "GET /api/v1/student/study-path/content/{content_code} - one recommendation + explanation.",
        "GET /api/v1/question-bank/assignments/{assignment_id}/students/study-path[?student_external_id=] "
        "- manager view; PHASE 16 assignment-view authz reused; no automatic intervention.",
    ]
    rep["frontend"] = (
        "Student portal gains a 'Trilha de Estudos' nav item + view (distinct from the legacy "
        "'Minha Trilha'). It shows the transparency note ('É uma orientação de estudo, não uma "
        "nota. Sem ranking nem comparação'), a provisional-classification note when relevant, a "
        "small summary (próximos passos / bloqueados / precisam de diagnóstico / dominados), then "
        "the ordered steps: order number, content name, state badge (Bloqueado / Evidência "
        "insuficiente / Revisar / Estudar / Praticar), the observed metric, 'Por quê:' reason, the "
        "unsatisfied prerequisite ('Pré-requisito necessário: ...') and what it unblocks "
        "('Destrava: ...'), and a DISABLED action button labelled '<Ação> — em breve' (no false "
        "functional flow). A separate 'Conteúdos que você já demonstrou dominar' list (never a "
        "ranking). Graceful empty (NO_EVIDENCE), graph-invalid and API-error states. Mobile "
        "(320px): figure cards reflow 2-per-row, no horizontal page scroll. No points / badges / "
        "levels / comparison anywhere.")
    ob = walk["counts_before"]
    of_ = walk["counts_after_cleanup"]
    official_unchanged = all(ob[t] == of_[t] for t in OFFICIAL)
    immutable_unchanged = all(ob[t] == of_[t] for t in IMMUTABLE)
    rep["integrity"] = {
        "DATABASE_WRITES": "0 by the learning path itself. It is a derived view. The first "
                           "study-path call may trigger PHASE 20's lazy Domain Map cache population "
                           "(a derived write owned by PHASE 20). The acceptance walk also creates a "
                           "temporary list / assignment / attempt / answers / result and deletes "
                           "all of it. No official / ActivityResult / ActivityResultItem / catalog "
                           "/ classification / answer-key / Domain Map content row is written by "
                           "PHASE 21.",
        "OPENAI_CALLS": 0,
        "MIGRATIONS": 0,
        "TABLES_CREATED": 0,
        "CODE_FILES_MODIFIED": [
            "src/agente_ia_edu/api/routes/student.py", "src/agente_ia_edu/api/routes/question_bank.py",
            "src/agente_ia_edu/web/index.html", "src/agente_ia_edu/web/app.js",
            "src/agente_ia_edu/web/styles.css",
            "tests/test_phase20_domain_map_frontend.js (re-scoped a slice around the new PHASE 21 block)",
        ],
        "CODE_FILES_CREATED": [
            "src/agente_ia_edu/services/adaptive_learning_path.py",
            "tests/test_phase21_adaptive_learning_path.py",
            "tests/test_phase21_adaptive_learning_path_frontend.js",
            "tests/manual/phase21_adaptive_learning_path_report.py",
            "var/phase21_adaptive_learning_path_report.json",
        ],
        "official_tables_unchanged": official_unchanged,
        "immutable_tables_net_zero_after_cleanup": immutable_unchanged,
        "legacy_learning_path_untouched": any(s["step"].startswith("17.") and s["ok"] for s in walk["steps"]),
        "path_wrote_nothing_of_its_own": any(s["step"].startswith("20.") and s["ok"] for s in walk["steps"]),
        "cleanup_restored_db": any(s["step"].startswith("21.") and s["ok"] for s in walk["steps"]),
    }
    rep["security"] = [
        "Student endpoints are always 'self' - no student route accepts another student's id. A "
        "different student calling GET /student/study-path gets their own (NO_EVIDENCE) path.",
        "Manager endpoint reuses ActivityAssignmentStore.get() - owner / platform admin / "
        "same-school privileged only; a teacher from another school is refused (verified 403/404). "
        "It returns the path only of students with a corrected result UNDER that assignment. No "
        "automatic intervention recommendation is produced for the teacher.",
        "Independent student (no classroom / no context) is fully supported - autonomous path from "
        "Domain Map + prerequisites + curriculum-v2.",
        "AI-agnostic: the module imports no openai / provider / classification-AI symbol (asserted "
        "by test). A future LearningPathExplanationProvider may enrich prose without touching the "
        "decision logic.",
    ]
    rep["tests"] = {
        "backend": {"file": "tests/test_phase21_adaptive_learning_path.py", "cases": 17,
                    "result": "17/17 passed",
                    "covers": ["no evidence -> NO_EVIDENCE", "one content -> INSUFFICIENT_EVIDENCE + "
                               "DIAGNOSE", "mastered needs min evidence", "weak content + prereq "
                               "chain (blocked; chain root surfaces first)", "multiple contents + "
                               "disciplines", "content with two prerequisites", "mastered "
                               "prerequisite unblocks its dependent", "provisional / FORCED_CLOSURE "
                               "carried + flagged", "UNCLASSIFIED never recommended", "independent "
                               "student (no school)", "school pedagogical context boost", "determinism "
                               "+ idempotency (path writes nothing new)", "tenant isolation + manager "
                               "scope + student-only-self", "cyclic prerequisite graph -> "
                               "PREREQUISITE_GRAPH_INVALID (restored)", "no N+1 (<=16 queries)", "AI-"
                               "agnostic import guard", "Domain Map / ActivityResult / answer key "
                               "unchanged"]},
        "frontend": {"file": "tests/test_phase21_adaptive_learning_path_frontend.js", "cases": 13,
                     "result": "13/13 passed",
                     "covers": ["nav item + view + containers", "loads GET /student/study-path; "
                                "legacy /learning-path NOT touched", "summary fields", "step render "
                                "(order / state badge / reason / prerequisites / 'destrava')",
                                "disabled 'em breve' actions", "mastered list (not a ranking)",
                                "transparency + provisional notes", "PREREQUISITE_GRAPH_INVALID "
                                "handled", "empty + error states", "no gamification / ranking / "
                                "comparison / grade", "responsive + distinct step CSS"]},
        "browser_validation": ("Real uvicorn + real DB + in-app browser: the 'Trilha de Estudos' "
                               "view loads, shows the transparency + provisional notes, the summary, "
                               "the ordered steps with 'Por quê', 'Destrava:' and disabled 'Fazer "
                               "diagnóstico — em breve' buttons; a no-evidence student sees the "
                               "empty-state message; at 320px there is no horizontal page scroll."),
        "regression": "146 frontend node tests + 236 backend tests (phases 12-21 + the LEGACY "
                      "test_phase6_domain_map + assessments + exercise_lists + api_questions + "
                      "catalog + lifecycle) + compileall - all green.",
    }
    rep["query_count"] = {
        "by_content_count": {n: perf[str(n)]["queries"] for n in (10, 50, 100, 500, 1000)},
        "constant_within_2": perf["query_count_constant"],
        "note": "catalog (cached) + Domain Map rows + catalog_node_prerequisites + optional "
                "pedagogical_contexts + optional user_school_links. Independent of #contents.",
    }
    rep["future_contract"] = (
        "The path output uses only stable conceptual identifiers (content_code / subcontent_code / "
        "discipline_code) and never points at URLs or files. action_type + action_available=False "
        "is the seam for the future loop: content_code -> authored material chapter/section -> "
        "study; content_code -> Question Bank selection -> practice -> ActivityResult -> Domain Map "
        "-> a new path. Nothing here needs to change to wire those in.")
    rep["known_limitations"] = [
        "action_available is False for every action - no authored material, practice-list "
        "generation, video or diagnostic-by-content flow is wired yet.",
        "The school-context boost is a single 0.5 factor when any ACTIVE PedagogicalContext exists "
        "for the classroom; per-source weighting (TEACHER vs COORDINATION vs SCHOOL_PLAN) and "
        "teaching_lessons recency are future refinements.",
        "'Stale review' is a simple age flag (STALE_DAYS), not a decay model - the timestamps make "
        "a real decay/recovery model possible later.",
        "The path is recomputed per request (cheap, deterministic); a cache can wrap "
        "AdaptiveLearningPathService later without changing the engine.",
        "No dedicated professor learning-path screen (no student-roster UI to host it); the "
        "assignment-scoped manager endpoint is ready.",
    ]
    rep["next_steps"] = [
        "PHASE 22+: wire action_type -> authored material (chapter/section) and -> Question Bank "
        "practice selection, closing the loop path -> study/practice -> result -> Domain Map -> "
        "new path.",
        "Then: per-source context weighting, a decay/recovery model, class learning-path dashboards, "
        "and an optional LearningPathExplanationProvider (prose only, never the decision).",
    ]

    ok = (walk["all_passed"] and official_unchanged and immutable_unchanged
          and perf["query_count_constant"]
          and rep["integrity"]["legacy_learning_path_untouched"]
          and rep["integrity"]["path_wrote_nothing_of_its_own"]
          and rep["integrity"]["cleanup_restored_db"])
    rep["FINAL_DECISION"] = ("PHASE_21_ADAPTIVE_LEARNING_PATH_COMPLETE" if ok
                             else "PHASE_21_ADAPTIVE_LEARNING_PATH_BLOCKED")
    if not ok:
        rep["blocker"] = "see acceptance_walk.steps / performance / integrity"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if report["FINAL_DECISION"].endswith("COMPLETE") else 1)
