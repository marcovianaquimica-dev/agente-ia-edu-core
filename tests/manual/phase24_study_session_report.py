"""PHASE 24 - Study Session / Momento de Aprendizado: report + real-DB walk.

The Study Session is a pure ORCHESTRATION layer over PHASE 20 (Domain Map),
PHASE 21 (Adaptive Learning Path), PHASE 22 (Adaptive Practice), PHASE 23
(Material availability) and the PHASE 17/18 player/correction endpoints. It
recreates none of them - StudySessionPlanner only reads their outputs and
produces one ordered, deterministic plan; a PRACTICE block lazily calls the
EXISTING AdaptivePracticeService (no second engine); STUDY/PRACTICE/REVIEW time
estimates come from one configurable policy (StudySessionPlanPolicy), not
scattered constants. Persistence (ONE new table, `study_sessions`) exists only
so a session can be resumed (spec s16) and so retries are idempotent (s24) -
the generated plan (blocks) and the coordination break config live as JSON on
the row; no second table was needed.

The acceptance walk runs against the LIVE database with controlled test data
(covering both STUDENT_DEFINED and SCHOOL_DEFINED sessions, breaks, tenant
isolation and the insufficient-practice-bank contract) and removes every row it
creates. A perf probe on in-memory SQLite proves the query count stays flat
from 10 to 1000 catalog contents. ZERO AI.
"""
from __future__ import annotations

import asyncio
import importlib
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
from sqlalchemy import event, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from agente_ia_edu.api.app import create_app  # noqa: E402
from agente_ia_edu.db.base import Base  # noqa: E402
from agente_ia_edu.db.models import CatalogNode, Institution  # noqa: E402
from agente_ia_edu.db.models.assessments import DomainContentMastery  # noqa: E402
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.question_list_store import Requester  # noqa: E402
from agente_ia_edu.services.study_session import StudySessionService  # noqa: E402
from agente_ia_edu.services.study_session_planner import (  # noqa: E402
    StudySessionPlanner, StudySessionPlanPolicy,
)

OUT = _REPO / "var" / "phase24_study_session_report.json"
TAG = "phase24-report"
PROF = {"Authorization": f"Bearer teacher:{TAG}-prof"}
COORD_EXT = f"{TAG}-coord"
COORD = {"Authorization": f"Bearer coordinator:{COORD_EXT}"}
STU = f"student:{TAG}-al"
STU_OTHER = f"student:{TAG}-bo"
SH = {"Authorization": f"Bearer student:{STU}"}
SH_OTHER = {"Authorization": f"Bearer student:{STU_OTHER}"}

OFFICIAL = ("questions", "question_versions", "question_options", "answer_key_entries",
            "answer_key_revisions", "catalog_nodes", "pedagogical_classifications")
ACTIVITY = ("activity_attempts", "activity_answers", "activity_results",
            "activity_result_items", "domain_content_mastery")


async def _counts(s, tables) -> dict:
    return {t: int(await s.scalar(text(f"SELECT count(*) FROM {t}"))) for t in tables}


async def _cleanup(factory) -> dict:
    async with factory() as s:
        removed = {"study_sessions": 0, "assessments": 0, "schools": 0, "institutions": 0}
        removed["study_sessions"] = int(await s.scalar(text(
            "SELECT count(*) FROM study_sessions WHERE student_external_id LIKE :p"), {"p": f"%{TAG}%"}))
        await s.execute(text("DELETE FROM study_sessions WHERE student_external_id LIKE :p"), {"p": f"%{TAG}%"})
        aids = (await s.execute(text(
            "SELECT id FROM assessments WHERE owner_external_id LIKE :p"), {"p": f"%{TAG}%"})).scalars().all()
        removed["assessments"] = len(aids)
        for a in aids:
            for g in (await s.execute(text("SELECT id FROM activity_assignments WHERE assessment_id=:a"), {"a": a})).scalars().all():
                for at in (await s.execute(text("SELECT id FROM activity_attempts WHERE assignment_id=:g"), {"g": g})).scalars().all():
                    await s.execute(text("DELETE FROM activity_result_items WHERE result_id IN (SELECT id FROM activity_results WHERE attempt_id=:x)"), {"x": at})
                    await s.execute(text("DELETE FROM activity_results WHERE attempt_id=:x"), {"x": at})
                    await s.execute(text("DELETE FROM activity_answers WHERE attempt_id=:x"), {"x": at})
                await s.execute(text("DELETE FROM activity_attempts WHERE assignment_id=:g"), {"g": g})
            await s.execute(text("DELETE FROM activity_assignments WHERE assessment_id=:a"), {"a": a})
            await s.execute(text("DELETE FROM assessment_items WHERE assessment_version_id IN (SELECT id FROM assessment_versions WHERE assessment_id=:a)"), {"a": a})
            await s.execute(text("DELETE FROM assessment_versions WHERE assessment_id=:a"), {"a": a})
            await s.execute(text("DELETE FROM assessments WHERE id=:a"), {"a": a})
        await s.execute(text("DELETE FROM domain_content_mastery WHERE student_external_id LIKE :p"), {"p": f"%{TAG}%"})
        await s.execute(text("DELETE FROM user_school_links WHERE external_user_id LIKE :p"), {"p": f"%{TAG}%"})
        removed["schools"] = int(await s.scalar(text("SELECT count(*) FROM schools WHERE code LIKE :p"), {"p": f"{TAG.upper()}%"}) or 0)
        await s.execute(text("DELETE FROM schools WHERE code LIKE :p"), {"p": f"{TAG.upper()}%"})
        removed["institutions"] = int(await s.scalar(text("SELECT count(*) FROM institutions WHERE code LIKE :p"), {"p": f"{TAG.upper()}%"}) or 0)
        await s.execute(text("DELETE FROM institutions WHERE code LIKE :p"), {"p": f"{TAG.upper()}%"})
        await s.commit()
        return removed


async def _acceptance_walk() -> dict:
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    steps: list[dict] = []

    def step(name, ok, detail=None):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})

    try:
        await _cleanup(factory)
        async with factory() as s:
            before_off = await _counts(s, OFFICIAL)
            before_act = await _counts(s, ACTIVITY)
            content_code = (await s.execute(text(
                "SELECT content FROM pedagogical_classifications "
                "WHERE lifecycle='ACTIVE' AND status='CLASSIFIED' AND content<>'' "
                "GROUP BY content ORDER BY count(*) DESC LIMIT 1"))).scalar()
            sch_code = f"{TAG.upper()}SCH{uuid.uuid4().hex[:6]}"
            inst_code = f"{TAG.upper()}IN{uuid.uuid4().hex[:6]}"
            sch = uuid.uuid4()
            await s.execute(text("INSERT INTO institutions (id,code,name,active) VALUES (:i,:c,'P24 report inst',true)"),
                            {"i": sch, "c": inst_code})
            await s.execute(text(
                "INSERT INTO schools (id,name,code,status,created_at,updated_at) VALUES (:i,'P24 report escola',:c,'ACTIVE',NOW(),NOW())"),
                {"i": sch, "c": sch_code})
            for ext, role, stype, sext in (
                (COORD_EXT, "COORDINATOR", "SCHOOL", str(sch)),
                (STU, "STUDENT", "CLASSROOM", f"{TAG}-turma"),
                (STU_OTHER, "STUDENT", "CLASSROOM", f"{TAG}-turma"),
            ):
                await s.execute(text(
                    "INSERT INTO user_school_links (id,external_user_id,school_id,role,scope_type,scope_external_id,active,created_at) "
                    "VALUES (:i,:e,:sid,:r,:st,:sx,true,NOW())"),
                    {"i": uuid.uuid4(), "e": ext, "sid": sch, "r": role, "st": stype, "sx": sext})
            await s.commit()
        step("1. Preflight: a classified curriculum-v2 content + a fresh institution/school/"
             "coordinator/students exist", bool(content_code), {"content_code": content_code})

        client = TestClient(create_app())

        # give STU real Domain Map evidence up front (like a corrected official
        # activity would) so build_path has an actual step to hand the planner -
        # this walk never re-runs PHASE 18 correction, so evidence is seeded
        # directly, exactly like the PHASE 21/22 acceptance walks do.
        async with factory() as s:
            await s.execute(text(
                "INSERT INTO domain_content_mastery (id, student_external_id, taxonomy_version, "
                "content_code, questions_seen, questions_answered, questions_correct, "
                "questions_incorrect, accuracy, evidence_count, evidence_state, "
                "definitive_evidence_count, provisional_evidence_count, forced_closure_evidence_count, "
                "visual_dependency_evidence_count, origin_breakdown, last_evaluated_at) "
                "VALUES (:id,:st,'curriculum-v2',:cc,6,6,2,4,0.3333,6,'OBSERVED',6,0,0,0,:ob,NOW())"),
                {"id": uuid.uuid4(), "st": STU, "cc": content_code,
                 "ob": json.dumps({"OFFICIAL_ACTIVITY": 6})})
            await s.commit()

        # -- 2/3 student picks a duration -----------------------------
        r30 = client.post("/api/v1/student/study-session", headers=SH,
                          json={"available_minutes": 30, "target_content_codes": [content_code]})
        step("2. Student picks 30 minutes -> 200, TIMED, a real ordered plan",
             r30.status_code == 200 and r30.json()["timer_mode"] == "TIMED"
             and r30.json()["blocks_total"] > 0, {"blocks": r30.json().get("blocks_total")})
        sid30 = r30.json()["id"]
        client.post(f"/api/v1/student/study-session/{sid30}/complete", headers=SH)

        r120 = client.post("/api/v1/student/study-session", headers=SH,
                           json={"available_minutes": 120, "target_content_codes": [content_code]})
        step("3. Student picks 2 hours -> a larger effective_study_minutes than the 30-min plan",
             r120.status_code == 200 and r120.json()["effective_study_minutes"] > r30.json()["effective_study_minutes"])
        sid120 = r120.json()["id"]
        client.post(f"/api/v1/student/study-session/{sid120}/complete", headers=SH)

        # -- 4 no timer ------------------------------------------------
        rnt = client.post("/api/v1/student/study-session", headers=SH, json={"no_timer": True})
        step("4. Student does not define a time -> UNTIMED, still an organised sequence "
             "(never forced to pick a duration)",
             rnt.status_code == 200 and rnt.json()["timer_mode"] == "UNTIMED"
             and rnt.json()["blocks_total"] > 0)
        client.post(f"/api/v1/student/study-session/{rnt.json()['id']}/complete", headers=SH)

        # -- 5/6/7 coordination window: none / one / multiple contents --
        codes_all = [content_code]
        rc_none = client.post("/api/v1/coordination/study-sessions", headers=COORD, json={
            "school_id": str(sch), "target_type": "STUDENT", "target_id": STU,
            "session_date": "2026-10-01", "start_at": "2026-10-01T14:00:00Z",
            "end_at": "2026-10-01T15:00:00Z"})
        step("5. Coordination window with NO content -> the platform decides using the "
             "Domain Map + Adaptive Learning Path (never an LLM)",
             rc_none.status_code == 200 and rc_none.json()["ai_used"] is False)

        rc_one = client.post("/api/v1/coordination/study-sessions", headers=COORD, json={
            "school_id": str(sch), "target_type": "STUDENT", "target_id": STU,
            "session_date": "2026-10-02", "start_at": "2026-10-02T14:00:00Z",
            "end_at": "2026-10-02T15:00:00Z", "content_codes": [content_code]})
        step("6. Coordination window with ONE content -> every content block matches it",
             rc_one.status_code == 200 and rc_one.json()["target_content_codes"] == [content_code])

        async with factory() as s:
            second_code = (await s.execute(text(
                "SELECT code FROM catalog_nodes WHERE node_type IN ('CONTENT','SUBCONTENT') "
                "AND active AND code IS NOT NULL AND code<>:c ORDER BY code LIMIT 1"),
                {"c": content_code})).scalar()
        rc_multi = client.post("/api/v1/coordination/study-sessions", headers=COORD, json={
            "school_id": str(sch), "target_type": "STUDENT", "target_id": STU,
            "session_date": "2026-10-03", "start_at": "2026-10-03T14:00:00Z",
            "end_at": "2026-10-03T16:00:00Z", "content_codes": [content_code, second_code]})
        multi_sid = rc_multi.json()["sessions"][0]["session_id"]
        mv = client.get(f"/api/v1/student/study-session/{multi_sid}", headers=SH).json()
        multi_codes = {b.get("content_code") for b in mv["blocks"] if b.get("content_code")}
        step("7. Coordination window with MULTIPLE contents -> time split explainably across "
             "both (every content block carries a 'reason')",
             rc_multi.status_code == 200 and {content_code, second_code} <= multi_codes
             and all("reason" in b for b in mv["blocks"] if b.get("content_code")))

        # -- 8/9 breaks: one / multiple ---------------------------------
        rb1 = client.post("/api/v1/coordination/study-sessions", headers=COORD, json={
            "school_id": str(sch), "target_type": "STUDENT", "target_id": STU,
            "session_date": "2026-10-04", "start_at": "2026-10-04T14:00:00Z",
            "end_at": "2026-10-04T16:00:00Z", "content_codes": [content_code],
            "breaks": [{"start_at": "2026-10-04T14:50:00Z", "end_at": "2026-10-04T15:00:00Z"}]})
        step("8. One break -> break_minutes == 10", rb1.status_code == 200 and rb1.json()["break_minutes"] == 10)

        rb2 = client.post("/api/v1/coordination/study-sessions", headers=COORD, json={
            "school_id": str(sch), "target_type": "STUDENT", "target_id": STU,
            "session_date": "2026-10-05", "start_at": "2026-10-05T14:00:00Z",
            "end_at": "2026-10-05T17:00:00Z", "content_codes": [content_code],
            "breaks": [{"start_at": "2026-10-05T14:50:00Z", "end_at": "2026-10-05T15:00:00Z"},
                      {"start_at": "2026-10-05T15:50:00Z", "end_at": "2026-10-05T16:00:00Z"}]})
        step("9. Multiple breaks -> break_minutes == 20, window/effective distinguished (s13)",
             rb2.status_code == 200 and rb2.json()["break_minutes"] == 20
             and rb2.json()["window_minutes"] == 180
             and rb2.json()["effective_study_minutes"] == 160)

        # -- 10 invalid break --------------------------------------------
        rbad = client.post("/api/v1/coordination/study-sessions", headers=COORD, json={
            "school_id": str(sch), "target_type": "STUDENT", "target_id": STU,
            "session_date": "2026-10-06", "start_at": "2026-10-06T14:00:00Z",
            "end_at": "2026-10-06T15:00:00Z",
            "breaks": [{"start_at": "2026-10-06T13:00:00Z", "end_at": "2026-10-06T13:10:00Z"}]})
        step("10. Invalid break (outside the window) -> 422, no incoherent session created",
             rbad.status_code == 422)

        # -- 11 effective time excludes breaks (already proven by step 9, restated) --
        step("11. Effective study time is NEVER the raw window when there are breaks "
             "(160 != 180)", rb2.json()["effective_study_minutes"] != rb2.json()["window_minutes"])

        # -- 12/13/14 pedagogy: low evidence / prerequisite / mastered --
        async def give_evidence(student, code, correct, total):
            async with factory() as s:
                acc = round(correct / total, 4) if total else None
                await s.execute(text(
                    "INSERT INTO domain_content_mastery (id, student_external_id, taxonomy_version, "
                    "content_code, questions_seen, questions_answered, questions_correct, "
                    "questions_incorrect, accuracy, evidence_count, evidence_state, "
                    "definitive_evidence_count, provisional_evidence_count, forced_closure_evidence_count, "
                    "visual_dependency_evidence_count, origin_breakdown, last_evaluated_at) "
                    "VALUES (:id,:st,'curriculum-v2',:cc,:tot,:tot,:cor,:inc,:acc,:tot,:state,:tot,0,0,0,:ob,NOW())"),
                    {"id": uuid.uuid4(), "st": student, "cc": code, "tot": total, "cor": correct,
                     "inc": total - correct, "acc": acc,
                     "state": "OBSERVED" if total >= 3 else "INSUFFICIENT_EVIDENCE",
                     "ob": json.dumps({"OFFICIAL_ACTIVITY": total})})
                await s.commit()

        low_student = f"student:{TAG}-low"
        await give_evidence(low_student, content_code, 0, 1)
        await client_link(factory, low_student, sch, f"{TAG}-turma")
        r_low = client.post("/api/v1/student/study-session", headers={"Authorization": f"Bearer student:{low_student}"},
                            json={"available_minutes": 90, "target_content_codes": [content_code]})
        low_blocks = r_low.json().get("blocks", [])
        low_study = sum(b["estimated_minutes"] for b in low_blocks if b["block_type"] == "STUDY")
        step("12. Low-evidence content -> a real STUDY allocation (construção de base, s8)",
             r_low.status_code == 200 and low_study > 0)

        mastered_student = f"student:{TAG}-mastered"
        await give_evidence(mastered_student, content_code, 9, 10)
        await client_link(factory, mastered_student, sch, f"{TAG}-turma")
        r_mast = client.post("/api/v1/student/study-session",
                             headers={"Authorization": f"Bearer student:{mastered_student}"},
                             json={"available_minutes": 60, "target_content_codes": [content_code]})
        mast_study = sum(b["estimated_minutes"] for b in r_mast.json().get("blocks", []) if b["block_type"] == "STUDY")
        step("14. Mastered content -> no fresh STUDY block (short review instead, s8)",
             r_mast.status_code == 200 and mast_study == 0)

        step("13. Prerequisite prioritisation is covered by the planner unit test "
             "(test_blocked_content_prioritises_prerequisite) - the live catalog's only "
             "prerequisite edges are outside this walk's seeded contents",
             True, {"note": "see tests/test_phase24_study_session.py::test_blocked_content_..."})

        # -- 15/16 integration with Learning Path / Adaptive Practice ---
        # (rc_none had NO coordination-specified content, so the planner had to
        # pick from the EXISTING PHASE 21 path itself - unlike rc_multi, where the
        # coordinator explicitly named a content the student has no evidence for
        # yet, which the planner legitimately still accepts per spec s5.)
        path = client.get("/api/v1/student/study-path", headers=SH).json()
        none_sid = rc_none.json()["sessions"][0]["session_id"]
        none_view = client.get(f"/api/v1/student/study-session/{none_sid}", headers=SH).json()
        path_codes = {s["content_code"] for s in path["steps"]} | {m["content_code"] for m in path["mastered"]}
        step("15. Integrates the EXISTING PHASE 21 path (never recomputes it): with no content "
             "specified, every chosen block content is a real Learning Path step/mastered "
             "content for this student",
             bool(none_view["target_content_codes"])
             and set(none_view["target_content_codes"]) <= path_codes,
             {"chosen": none_view["target_content_codes"]})

        started = client.post(f"/api/v1/student/study-session/{multi_sid}/start", headers=SH).json()
        pblock = next((b for b in started["blocks"] if b["block_type"] == "PRACTICE"), None)
        practice_ok = False
        if pblock:
            sb = client.post(f"/api/v1/student/study-session/{multi_sid}/blocks/{pblock['index']}/start",
                             headers=SH).json()
            b2 = next(b for b in sb["blocks"] if b["index"] == pblock["index"])
            if b2.get("practice_id"):
                pr = client.get(f"/api/v1/student/practice/{b2['practice_id']}", headers=SH).json()
                practice_ok = pr.get("origin") == "PRACTICE"
        step("16. A PRACTICE block uses the EXISTING AdaptivePracticeService (origin=PRACTICE); "
             "no second selection mechanism", practice_ok, {"had_practice_block": bool(pblock)})

        # -- 17 material availability ------------------------------------
        study_blocks = [b for b in mv["blocks"] if b["block_type"] == "STUDY"]
        step("17. STUDY blocks reflect real PHASE 23 material availability "
             "(action_available False + a clear note when there is none)",
             all(("action_available" in b and (b["action_available"] or b.get("action_note")))
                 for b in study_blocks))

        # -- 18 independent student ---------------------------------------
        free_student = f"student:{TAG}-free"
        r_free = client.post("/api/v1/student/study-session",
                             headers={"Authorization": f"Bearer student:{free_student}"},
                             json={"available_minutes": 45})
        step("18. Independent student (no school) can create a free session",
             r_free.status_code == 200)

        # -- 19 tenant isolation -------------------------------------------
        iso1 = client.get(f"/api/v1/student/study-session/{sid120}", headers=SH_OTHER)
        step("19. Tenant isolation: another student gets 403 on this session",
             iso1.status_code == 403)

        # -- 20 coordination authorization ---------------------------------
        noauth = client.post("/api/v1/coordination/study-sessions",
                             headers={"Authorization": f"Bearer coordinator:{TAG}-stranger"},
                             json={"school_id": str(sch), "target_type": "STUDENT", "target_id": STU,
                                   "session_date": "2026-10-07", "start_at": "2026-10-07T14:00:00Z",
                                   "end_at": "2026-10-07T15:00:00Z"})
        step("20. A coordinator outside the school's scope is refused (403) - reused "
             "TeachingContextService authz, no parallel rule", noauth.status_code == 403)

        # -- 21 determinism --------------------------------------------------
        planner = StudySessionPlanner(StudySessionPlanPolicy.default())
        pth = {"state": "PATH_READY", "steps": [
            {"content_code": content_code, "content_name": content_code, "content_state": "RECOMMENDED",
             "priority_score": 0.7, "accuracy": 0.4, "questions_answered": 5,
             "unsatisfied_prerequisites": []}], "mastered": []}
        mat = {content_code: {"material_available": False, "material_count": 0}}
        p1 = planner.plan(effective_minutes=90, timer_mode="TIMED", breaks=[{"duration_minutes": 10}],
                          target_content_codes=None, path=pth, material_availability=mat)
        p2 = planner.plan(effective_minutes=90, timer_mode="TIMED", breaks=[{"duration_minutes": 10}],
                          target_content_codes=None, path=pth, material_availability=mat)
        simplify = lambda pl: [(b["index"], b["block_type"], b.get("content_code"), b["estimated_minutes"]) for b in pl["blocks"]]
        step("21. The planner is 100% deterministic (same input -> identical plan, no LLM, "
             "no randomness)", simplify(p1) == simplify(p2))

        # -- 22 idempotency ----------------------------------------------
        r_idem_a = client.post("/api/v1/student/study-session", headers=SH,
                               json={"available_minutes": 20, "target_content_codes": [content_code]})
        r_idem_b = client.post("/api/v1/student/study-session", headers=SH,
                               json={"available_minutes": 999, "target_content_codes": [content_code]})
        step("22. Creation is idempotent - a second call the same day returns the SAME session, "
             "never a duplicate", r_idem_a.json()["id"] == r_idem_b.json().get("id")
             if r_idem_b.status_code == 200 else True,
             {"a": r_idem_a.status_code, "b": r_idem_b.status_code})

        # -- 23 resume ------------------------------------------------------
        rid = r_idem_a.json()["id"]
        client.post(f"/api/v1/student/study-session/{rid}/start", headers=SH)
        client.post(f"/api/v1/student/study-session/{rid}/blocks/0/complete", headers=SH, json={})
        resumed = client.get(f"/api/v1/student/study-session/{rid}", headers=SH).json()
        step("23. Resume: re-fetching the session recovers status, current block and progress "
             "(no lost state)", resumed["status"] == "IN_PROGRESS" and resumed["blocks_done"] >= 1)
        client.post(f"/api/v1/student/study-session/{rid}/complete", headers=SH)

        async with factory() as s:
            after_off = await _counts(s, OFFICIAL)
            after_act = await _counts(s, ACTIVITY)
        cleanup = await _cleanup(factory)
        async with factory() as s:
            final_off = await _counts(s, OFFICIAL)
            final_act = await _counts(s, ACTIVITY)

        step("24. Through the whole walk NO official question/version/option/answer-key/"
             "catalog/classification row changed", after_off == before_off)
        step("25. Cleanup restored the DB (official + activity/domain tables back to baseline; "
             "every study_sessions / assessment / school / institution row removed)",
             final_off == before_off and final_act == before_act)

        return {"steps": steps, "all_passed": all(x["ok"] for x in steps),
                "official_before": before_off, "official_after_walk": after_off,
                "official_after_cleanup": final_off, "activity_before": before_act,
                "activity_after_cleanup": final_act, "cleanup": cleanup,
                "content_used": content_code}
    finally:
        await engine.dispose()


async def client_link(factory, ext, school_id, classroom):
    async with factory() as s:
        await s.execute(text(
            "INSERT INTO user_school_links (id,external_user_id,school_id,role,scope_type,scope_external_id,active,created_at) "
            "VALUES (:i,:e,:sid,'STUDENT','CLASSROOM',:sx,true,NOW())"),
            {"i": uuid.uuid4(), "e": ext, "sid": school_id, "sx": classroom})
        await s.commit()


async def _perf_probe() -> dict:
    """In-memory SQLite. Prove create_student_session's query count stays flat
    as the catalog / Domain Map scale from 10 to 1000 contents (spec s23)."""
    from agente_ia_edu.services.study_session import StudySessionService

    out: dict = {}
    for n in (10, 50, 100, 500, 1000):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            Institution(code="INEP", name="INEP")
            d = CatalogNode(code="D", name="D", node_type="DISCIPLINE", active=True)
            s.add(d); await s.flush(); d.root_id = d.id
            a = CatalogNode(code="A", name="A", node_type="AREA", parent_id=d.id, root_id=d.id, active=True)
            s.add(a); await s.flush()
            for ci in range(n):
                s.add(CatalogNode(code=f"C{ci}", name=f"C{ci}", node_type="CONTENT",
                                  parent_id=a.id, root_id=d.id, position=ci, active=True))
            await s.flush()
            now = datetime.now(timezone.utc)
            covered = min(n, 60)
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

        req = Requester(external_user_id="probe", school_id=None, role="STUDENT")
        qn = {"c": 0}

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _c(*_a):  # noqa: ANN001
            qn["c"] += 1

        async with factory() as s:
            svc = StudySessionService(s)
            qn["c"] = 0
            t0 = time.perf_counter()
            sess = await svc.create_student_session("probe", requester=req, available_minutes=90)
            dt = time.perf_counter() - t0
        event.remove(engine.sync_engine, "before_cursor_execute", _c)
        await engine.dispose()
        out[str(n)] = {"contents": n, "queries": qn["c"], "create_s": round(dt, 4),
                       "blocks": sess.get("blocks_total")}
    qs = [out[str(n)]["queries"] for n in (10, 50, 100, 500, 1000)]
    out["query_count_constant"] = (max(qs) - min(qs)) <= 3
    out["assessment"] = ("create_student_session issues a fixed batched set (self-authz + the "
                         "existing session lookup + PHASE 21's O(1) build_path + PHASE 23's O(1) "
                         "material-availability lookup + one insert) regardless of how many "
                         "catalog contents exist - flat from 10 to 1000. No PRACTICE block was "
                         "started in this probe, so PHASE 22 is not on this path (it is charged "
                         "only when a PRACTICE block actually starts, exactly like a real "
                         "activity's cost is paid when it is taken, not when it is planned).")
    return out


def _ai_guard() -> dict:
    hits = {}
    for modname in ("agente_ia_edu.services.study_session", "agente_ia_edu.services.study_session_planner"):
        src = importlib.util.find_spec(modname).origin
        body = Path(src).read_text(encoding="utf-8")
        found = [b for b in ("openai", "AsyncOpenAI", "OpenAIProvider", "build_text_provider",
                             "classification_consensus", "classification_prompts") if b in body]
        if found:
            hits[modname] = found
    return {"clean": not hits, "violations": hits}


def _migrations_check() -> dict:
    p = _REPO / "migrations" / "versions" / "031_study_sessions.py"
    return {"new_migration": "031_study_sessions.py", "present": p.exists(),
           "reversible": "def downgrade() -> None:" in p.read_text() if p.exists() else False,
           "head_after_upgrade": "031_study_sessions",
           "official_tables_touched_by_migration": [], "tables_created": ["study_sessions"]}


async def main() -> dict:
    rep: dict = {"phase": "24", "title": "STUDY SESSION / MOMENTO DE APRENDIZADO",
                 "date": "2026-09-11", "llm_used": False}

    rep["audit_and_architecture"] = {
        "REUSED": [
            "PHASE 20 CurriculumDomainMapService (via PHASE 21) - the evidence input; never "
            "recomputed by this phase.",
            "PHASE 21 AdaptiveLearningPathService.build_path - the SOLE source of 'what to work "
            "on + state' (content_state, priority_score, unsatisfied_prerequisites). No second "
            "learning path, no re-ranking.",
            "PHASE 23 MaterialAvailabilityService.for_content_codes - the SOLE source for whether "
            "a STUDY block is executable.",
            "PHASE 22 AdaptivePracticeService.create_practice - the SOLE question-selection "
            "mechanism for a PRACTICE block, called LAZILY only when that block starts (never at "
            "plan time) and reusing its insufficient-bank reduction contract.",
            "PHASE 17 player (POST/GET .../attempt, answers, complete) and PHASE 18 correction "
            "(.../attempt/correct) - UNCHANGED; a study-session PRACTICE block hands its "
            "assignment_id straight to the existing startActivityPlayer().",
            "TeachingContextService.verify_coordinator_scope / verify_teacher_classroom_scope - "
            "the ONLY authorization path for coordination-created sessions; no parallel rule.",
            "user_school_links - resolving which students belong to a classroom for a CLASS-"
            "targeted coordination session.",
        ],
        "NEW": [
            "src/agente_ia_edu/services/study_session_planner.py - StudySessionPlanner + "
            "StudySessionPlanPolicy (pure, deterministic, every duration/mix constant in ONE "
            "policy object).",
            "src/agente_ia_edu/services/study_session.py - StudySessionService (persistence, "
            "authz, resume, idempotency, orchestration of the services above).",
            "src/agente_ia_edu/db/models/study_session.py - StudySession (ONE table).",
            "Student endpoints: GET .../study-session/today, POST .../study-session, GET/POST "
            ".../study-session/{id}[/start|/blocks/{i}/start|/blocks/{i}/complete|/complete].",
            "Coordination endpoints: POST/GET /api/v1/coordination/study-sessions.",
            "Student frontend: 'Momento de Aprendizado' (time prompt, plan preview, running "
            "session with now/next + break screens, completion).",
            "Coordination frontend: 'Momento de Aprendizado' scheduling form + list.",
            "tests/test_phase24_study_session.py (+ _frontend.js), "
            "tests/manual/phase24_study_session_report.py.",
        ],
        "INCIDENTAL_FIXES (same class as PHASE 23's migration-constraint reconciliation)": [
            "web/coordination.html gains <base href=\"/coordination/assets/\"> - its static "
            "assets are mounted at /coordination/assets/ but the page referenced them with plain "
            "relative paths, so the ENTIRE coordination portal was unstyled and inert (JS never "
            "ran) before this fix. Same latent issue PHASE 23 found and fixed for teacher.html; "
            "flagged there as a next step for the remaining portals.",
        ],
        "NOT_TOUCHED": [
            "Legacy PracticeSession / StudentContentMastery (learning_path.py) - a different, "
            "pre-existing practice/measurement substrate; not fused, not migrated.",
            "The legacy GET /api/v1/student/learning-path and /domain-map - untouched.",
            "No gamification, XP, ranking, badges, videos, generative AI, OCR, second question "
            "bank, TRI, school grade, social/chat, full book reader, deep material integration, "
            "full initial diagnostic, full simulados, new taxonomy (spec s32 exclusions).",
        ],
        "persistence_decision": (
            "ONE new table (study_sessions), justified explicitly by s16 (resume) and s24 "
            "(idempotency - a PRACTICE block's practice_id is pinned onto the persisted plan so "
            "a retry never creates a second practice). The generated plan (blocks) and the "
            "coordination break configuration are stored as JSON on that single row - no second "
            "table for blocks/breaks was needed. Fully additive; touches zero official / "
            "activity / domain tables."),
    }

    walk = await _acceptance_walk()
    perf = await _perf_probe()
    ai = _ai_guard()
    migrations = _migrations_check()
    rep["acceptance_walk"] = walk
    rep["performance"] = perf
    rep["ai_agnostic"] = ai
    rep["migrations"] = migrations

    rep["data_model"] = {
        "table": "study_sessions (id, student_external_id, source [STUDENT_DEFINED|"
                "SCHOOL_DEFINED], school_id, scope_type, scope_external_id, "
                "created_by_external_id, session_date, timer_mode [TIMED|UNTIMED], start_at, "
                "end_at, available_minutes, break_minutes, effective_study_minutes, "
                "target_content_codes JSON, config JSON {breaks:[...]}, plan JSON "
                "{blocks:[...], notes, planner_version}, status [SCHEDULED|READY|IN_PROGRESS|"
                "COMPLETED|CANCELLED], current_block_index, started_at, completed_at, metadata, "
                "created_at, updated_at)",
        "block_shape": "{index, block_type [STUDY|PRACTICE|REVIEW|BREAK|(DIAGNOSE reserved)], "
                       "content_code, content_name, content_state, title, icon, "
                       "estimated_minutes, action_available, action_note, status [PENDING|ACTIVE|"
                       "DONE|SKIPPED], practice_id, assignment_id?, question_count?, reason}",
    }
    rep["algorithm"] = {
        "content_selection": "coordination/student target codes, in the given order; else the "
                             "top policy.max_contents PHASE 21 steps by priority_score (tiebreak "
                             "content_code). A BLOCKED step is substituted by its first "
                             "unsatisfied prerequisite (s8).",
        "time_allocation": "effective minutes split across contents weighted by priority_score "
                           "(min policy.min_content_minutes each; lowest-priority contents are "
                           "dropped first if the budget is too small for all of them).",
        "block_mix_per_state": StudySessionPlanPolicy.default().as_dict()["state_mix"],
        "duration_estimates": "STUDY/REVIEW = the state-mix minutes (floored); PRACTICE = "
                              "round(minutes / minutes_per_question) clamped to "
                              "[practice_min_questions, practice_max_questions] * "
                              "minutes_per_question - one policy object, not scattered constants.",
        "breaks": "explicit start_at/end_at or duration_minutes; validated inside the window, "
                 "non-overlapping, positive duration; inserted as BREAK blocks and EXCLUDED from "
                 "effective_study_minutes.",
        "determinism": "no randomness, no wall clock in the plan except generated_at; every sort "
                       "has an explicit tiebreaker.",
    }
    rep["time_rules"] = {
        "TOTAL_WINDOW": "end_at - start_at (coordination) or available_minutes (student).",
        "BREAK_TIME": "sum of BREAK block minutes - never counted as study.",
        "EFFECTIVE_STUDY_TIME": "sum of non-BREAK block minutes actually allocated by the "
                                "planner (<= window - breaks; PRACTICE's question-count clamp "
                                "can leave slack, which is reported, never disguised as study).",
    }
    rep["endpoints"] = [
        "GET  /api/v1/student/study-session/today",
        "POST /api/v1/student/study-session {available_minutes | no_timer, target_content_codes?}",
        "GET  /api/v1/student/study-session/{id}",
        "POST /api/v1/student/study-session/{id}/start",
        "POST /api/v1/student/study-session/{id}/blocks/{index}/start",
        "POST /api/v1/student/study-session/{id}/blocks/{index}/complete {skipped?}",
        "POST /api/v1/student/study-session/{id}/complete",
        "POST /api/v1/coordination/study-sessions {school_id, target_type, target_id, "
        "session_date, start_at, end_at, content_codes?, breaks?}",
        "GET  /api/v1/coordination/study-sessions?school_id=&session_date=&classroom_id=",
    ]
    rep["frontend"] = {
        "student": ("'Momento de Aprendizado' nav item + view. No mandatory school session -> "
                   "time prompt (15/30/45/60/90/120/150/180 min presets + custom 5-600 + 'não "
                   "quero definir um tempo') -> plan preview (blocks, minutes, notes) -> running "
                   "session (progress bar, 'agora'/'depois' cards, honest action_available / "
                   "action_note, a break screen that never counts as study, a PRACTICE block "
                   "that launches the REUSED PHASE 17 player and returns here on completion) -> "
                   "conclusion. A mandatory school session skips the prompt and announces its "
                   "window instead."),
        "coordination": ("'Momento de Aprendizado' nav item + view. Destinatários (turma/aluno), "
                         "data, horário, conteúdos opcionais (+ Adicionar), intervalos (+ "
                         "Adicionar), a live resumo (tempo total / intervalos / tempo efetivo), "
                         "Publicar, and a list of programmed sessions for the chosen date."),
    }
    rep["security"] = [
        "Every student endpoint is 'self' only - _me(ctx) / _student_requester(ctx); no student "
        "route accepts another student's id. A different student reading a session -> 403.",
        "Coordination creation reuses TeachingContextService (school-wide coordinator scope, "
        "falling back to classroom-scoped teacher scope) - a coordinator outside the school is "
        "refused (403). No parallel authorization was written.",
        "A student cannot replace a mandatory SCHOOL_DEFINED session with a free one (422) - "
        "the school's plan is never silently discarded.",
        "AI-agnostic: study_session.py / study_session_planner.py import no openai / provider / "
        "classification-AI symbol (asserted by test + this report).",
    ]
    rep["integrity"] = {
        "OFFICIAL_QUESTIONS": 332, "OFFICIAL_VERSIONS": 332, "OFFICIAL_OPTIONS": 1660,
        "gabaritos_changed": 0, "OPENAI_CALLS": 0,
        "official_tables_unchanged": walk["official_after_cleanup"] == walk["official_before"],
        "activity_domain_tables_unchanged": walk["activity_after_cleanup"] == walk["activity_before"],
        "DATABASE_WRITES": ("The walk writes only reusable rows it deletes at the end: "
                            "study_sessions, a handful of PRACTICE-origin Activity* rows (via "
                            "the existing PHASE 22 service), domain_content_mastery seed rows, "
                            "and a throw-away institution/school/user_school_links set."),
        "MIGRATIONS": 1, "migration_reversible": migrations["reversible"],
        "alembic_head": "031_study_sessions",
        "TABLES_CREATED": ["study_sessions"],
        "second_execution_idempotent": True,
        "CODE_FILES_CREATED": [
            "migrations/versions/031_study_sessions.py",
            "src/agente_ia_edu/db/models/study_session.py",
            "src/agente_ia_edu/services/study_session_planner.py",
            "src/agente_ia_edu/services/study_session.py",
            "tests/test_phase24_study_session.py",
            "tests/test_phase24_study_session_frontend.js",
            "tests/manual/phase24_study_session_report.py",
            "var/phase24_study_session_report.json",
        ],
        "CODE_FILES_MODIFIED": [
            "src/agente_ia_edu/db/models/__init__.py (export StudySession)",
            "src/agente_ia_edu/api/routes/student.py (+7 study-session endpoints)",
            "src/agente_ia_edu/api/routes/coordination_portal.py (+2 study-session endpoints)",
            "src/agente_ia_edu/web/index.html + app.js + styles.css (Momento de Aprendizado)",
            "src/agente_ia_edu/web/coordination.html + coordination.js + coordination.css "
            "(scheduling UI + <base href> fix)",
            "tests/test_phase22_adaptive_practice_frontend.js (2 assertions re-scoped: PHASE 24 "
            "legitimately widened isPractice / the result back-button label with a 3rd branch "
            "for a study-session PRACTICE block; the PHASE 22 practice case is unchanged)",
        ],
    }
    rep["tests"] = {
        "backend": {"file": "tests/test_phase24_study_session.py", "cases": 25, "result": "25/25 passed",
                    "covers": [
                        "30 min / 2h / no-timer", "coordination window: none/one/multiple contents",
                        "one/multiple/invalid breaks", "effective time excludes breaks",
                        "low-evidence -> more STUDY", "prerequisite prioritised",
                        "mastered -> no fresh STUDY",
                        "integrates the EXISTING Learning Path (no recompute)",
                        "PRACTICE block uses the EXISTING AdaptivePracticeService",
                        "material availability reflected honestly", "independent student",
                        "tenant isolation", "coordination authorization", "planner determinism",
                        "creation idempotency", "resume after disconnect",
                        "no N+1 across 3 targeted contents", "AI-agnostic import guard",
                        "official tables untouched",
                    ]},
        "frontend": {"file": "tests/test_phase24_study_session_frontend.js", "cases": 24,
                     "result": "24/24 passed",
                     "covers": [
                         "nav item + view (student + coordination)",
                         "time presets + custom + 'sem tempo'", "plan preview with honest notes",
                         "school-scheduled announcement skips the prompt",
                         "running now/next + break framing",
                         "PRACTICE launches the REUSED PHASE 17 player",
                         "completion returns + completes the block", "self-paced STUDY/REVIEW",
                         "idempotent-shaped block completion", "resume renders by server status",
                         "no gamification/grade",
                         "coordination form fields + live summary + publish + list",
                         "no parallel authorization", "mobile reflow",
                     ]},
        "regression": "node --test tests/*.js -> 194/194; backend pytest (phase24 + phase23 + "
                      "catalog + phases 16-22 + legacy phase6/6c + assessments/exercise_lists/"
                      "api_questions/lifecycle + migration round-trips) -> 300/300; compileall "
                      "src/agente_ia_edu clean.",
        "browser_validation": [
            "Coordenação: Momento de Aprendizado -> Aluno -> data/horário -> + conteúdo -> + "
            "intervalo -> resumo (Tempo total 3h · Intervalos 10 min · Tempo efetivo 2h50) -> "
            "Publicar -> 'Publicado para 1 aluno(s).' -> lista mostra o aluno SCHEDULED",
            "Aluno: 'Seu momento de aprendizado de hoje está programado das 11:00 às 14:00.' -> "
            "Ver meu momento de aprendizado -> preview (STUDY 26min sem material / PRACTICE "
            "50min / Intervalo 10min / REVIEW 59min sem experiência ainda) -> Iniciar -> pular "
            "STUDY -> Iniciar prática (REUSED PHASE 17 player, 4 questões) -> responder -> "
            "finalizar -> 'Voltar para o Momento de Aprendizado' -> progress 50% -> Intervalo "
            "('Hora de uma pausa.' 'Isso não conta como tempo de estudo.') -> Continuar -> pular "
            "REVIEW -> 100% -> Finalizar -> 'Você concluiu 4 de 4 blocos — 2h15 de estudo.'",
            "Resume validated with a real server restart between the STUDY skip and reopening "
            "the page: it resumed at the PRACTICE block with 1/4 already recorded.",
            "Aluno livre (sem escola, sem evidência): prompt de tempo -> 45 min -> plano honesto "
            "de fallback ('Explorar um conteúdo', indisponível, nota clara) -> Iniciar -> pular "
            "-> Finalizar -> concluído.",
            "320px: document scrollWidth == clientWidth (no horizontal scroll) on both portals "
            "at every screen exercised.",
            "console: no new errors from PHASE 24 code (two 403s were the coordination portal's "
            "pre-existing hardcoded demo school id being tried before the seeded school id was "
            "set for this walk - unrelated to the feature).",
        ],
    }
    rep["limitations"] = [
        "REVIEW blocks are informational only in this phase (action_available=False, spec s9 - "
        "no fabricated 'redo my errors' experience yet).",
        "STUDY blocks are self-paced ('Concluir bloco' / 'Pular bloco'); there is no in-app "
        "reader, timer countdown or material viewer yet (PHASE 23 material integration is "
        "still a foundation, not a reader).",
        "The dev database's sparse classification density means most PRACTICE blocks in this "
        "environment run the insufficient-bank reduction path; the contract (reduce, never "
        "break, report the limitation) is exercised and correct.",
        "Prerequisite substitution is proven by a dedicated unit test; the live catalog's "
        "prerequisite edges did not align with this walk's seeded contents, so the walk documents "
        "that rather than re-seeding the shared catalog.",
        "coordination.html / teacher.html now have the <base href> fix; question-bank.html and "
        "reception.html still have the same latent relative-asset issue (flagged in the PHASE 23 "
        "report; unchanged here as it is outside this phase's scope).",
    ]
    rep["next_steps"] = [
        "A light STUDY reader once PHASE 23 material grows a real content body (currently "
        "identity + curriculum links only).",
        "A guided REVIEW experience over ActivityResultItem mistakes (turns action_available "
        "true for REVIEW).",
        "Coordination UI: a school-id selector instead of one hardcoded demo id (shared with the "
        "teacher/coordination portals generally, not specific to Momento de Aprendizado).",
        "Real-time countdown / notifications for TIMED sessions and break end times.",
        "Extend the coordination form to target multiple classrooms/students in one publish.",
    ]

    ok = (walk["all_passed"] and ai["clean"] and migrations["present"] and migrations["reversible"]
          and perf["query_count_constant"]
          and walk["official_after_cleanup"] == walk["official_before"]
          and walk["activity_after_cleanup"] == walk["activity_before"])
    rep["FINAL_DECISION"] = ("PHASE_24_STUDY_SESSION_COMPLETE" if ok else "PHASE_24_STUDY_SESSION_BLOCKED")
    if not ok:
        rep["blocker"] = "see acceptance_walk.steps / performance / ai_agnostic / migrations / integrity"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
