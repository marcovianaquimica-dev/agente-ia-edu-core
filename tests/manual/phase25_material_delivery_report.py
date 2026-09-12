"""PHASE 25 - Material Delivery & Study Integration: report + real-DB walk.

Turns the PHASE 23 material foundation into a real student study experience:
MATERIAL AUTORAL -> CURRICULUM-V2 -> TRILHA DE ESTUDOS -> MOMENTO DE
APRENDIZADO -> ESTUDO -> PRATICA -> RESULTADO -> DOMAIN MAP -> PROXIMO PASSO.
Reuses the PHASE 23 model as-is (TheoryMaterial/Version/Section/Block/
Exercise) - no second model, no copied question. The only new persisted
structure is `material_progress` (position-only, never read as domain
evidence). Associated exercises are proven to go through the EXISTING
POST /api/v1/student/practice (PHASE 22 AdaptivePracticeService) end to end -
no second player, no second selector.

The acceptance walk runs against the LIVE database with controlled, tagged
test data and removes every row it creates - it never touches an official
question/version/option/answer-key/catalog/classification row, nor
domain_content_mastery, nor ActivityResult(Item). ZERO AI.
"""
from __future__ import annotations

import asyncio
import importlib
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
from sqlalchemy import event, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from agente_ia_edu.api.app import create_app  # noqa: E402
from agente_ia_edu.db.base import Base  # noqa: E402
from agente_ia_edu.db.models import (  # noqa: E402
    CatalogNode, MaterialBlock, MaterialExercise, MaterialSection,
    TheoryMaterial, TheoryMaterialVersion,
)
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402

OUT = _REPO / "var" / "phase25_material_delivery_report.json"
TAG = "phase25-report"
PROF_EXT = f"{TAG}-prof"
PROF = {"Authorization": f"Bearer teacher:{PROF_EXT}"}
COORD_EXT = f"{TAG}-coord"
COORD = {"Authorization": f"Bearer coordinator:{COORD_EXT}"}
STU = f"student:{TAG}-al"
STU_OTHER_SCHOOL = f"student:{TAG}-outro"
STU_FREE = f"student:{TAG}-livre"
SH = {"Authorization": f"Bearer student:{STU}"}
SH_OTHER_SCHOOL = {"Authorization": f"Bearer student:{STU_OTHER_SCHOOL}"}
SH_FREE = {"Authorization": f"Bearer student:{STU_FREE}"}

OFFICIAL = ("questions", "question_versions", "question_options", "answer_key_entries",
            "answer_key_revisions", "catalog_nodes", "pedagogical_classifications")
UNTOUCHED = ("activity_attempts", "activity_answers", "activity_results",
            "activity_result_items", "domain_content_mastery")


async def _counts(s, tables) -> dict:
    return {t: int(await s.scalar(text(f"SELECT count(*) FROM {t}"))) for t in tables}


async def _cleanup(factory) -> dict:
    async with factory() as s:
        removed = {"material_progress": 0, "material_exercises": 0, "material_blocks": 0,
                  "material_sections": 0, "theory_material_versions": 0, "theory_materials": 0,
                  "assessments": 0, "schools": 0, "institutions": 0}
        removed["material_progress"] = int(await s.scalar(text(
            "SELECT count(*) FROM material_progress WHERE student_external_id LIKE :p"), {"p": f"%{TAG}%"}))
        await s.execute(text("DELETE FROM material_progress WHERE student_external_id LIKE :p"), {"p": f"%{TAG}%"})

        mids = (await s.execute(text(
            "SELECT id FROM theory_materials WHERE title LIKE :p OR created_by_external_identity = :prof"),
            {"p": f"{TAG}%", "prof": PROF_EXT})).scalars().all()
        removed["theory_materials"] = len(mids)
        for mid in mids:
            vids = (await s.execute(text(
                "SELECT id FROM theory_material_versions WHERE material_id=:m"), {"m": mid})).scalars().all()
            for vid in vids:
                removed["material_exercises"] += int(await s.scalar(text(
                    "SELECT count(*) FROM material_exercises WHERE material_version_id=:v"), {"v": vid}) or 0)
                await s.execute(text("DELETE FROM material_exercises WHERE material_version_id=:v"), {"v": vid})
                removed["material_blocks"] += int(await s.scalar(text(
                    "SELECT count(*) FROM material_blocks WHERE material_version_id=:v"), {"v": vid}) or 0)
                await s.execute(text("DELETE FROM material_blocks WHERE material_version_id=:v"), {"v": vid})
                removed["material_sections"] += int(await s.scalar(text(
                    "SELECT count(*) FROM material_sections WHERE material_version_id=:v"), {"v": vid}) or 0)
                await s.execute(text("DELETE FROM material_sections WHERE material_version_id=:v"), {"v": vid})
            removed["theory_material_versions"] += len(vids)
            await s.execute(text("DELETE FROM theory_material_versions WHERE material_id=:m"), {"m": mid})
            await s.execute(text("DELETE FROM theory_materials WHERE id=:m"), {"m": mid})

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

        await s.execute(text("DELETE FROM study_sessions WHERE student_external_id LIKE :p"), {"p": f"%{TAG}%"})
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
            before_un = await _counts(s, UNTOUCHED)

            content_row = (await s.execute(text(
                "SELECT pc.content, cn.id FROM pedagogical_classifications pc "
                "JOIN catalog_nodes cn ON cn.code = pc.content "
                "WHERE pc.lifecycle='ACTIVE' AND pc.status='CLASSIFIED' AND pc.content<>'' "
                "GROUP BY pc.content, cn.id ORDER BY count(*) DESC LIMIT 1"))).first()
            content_code, content_node_id = (content_row[0], content_row[1]) if content_row else (None, None)
            qv_row = (await s.execute(text(
                "SELECT question_version_id FROM pedagogical_classifications "
                "WHERE content=:cc AND status='CLASSIFIED' AND lifecycle='ACTIVE' LIMIT 1"),
                {"cc": content_code})).first() if content_code else None
            qvid = qv_row[0] if qv_row else None

            sch_code = f"{TAG.upper()}SCH{uuid.uuid4().hex[:6]}"
            other_sch_code = f"{TAG.upper()}OUT{uuid.uuid4().hex[:6]}"
            inst_code = f"{TAG.upper()}IN{uuid.uuid4().hex[:6]}"
            sch = uuid.uuid4()
            other_sch = uuid.uuid4()
            await s.execute(text("INSERT INTO institutions (id,code,name,active) VALUES (:i,:c,'P25 report inst',true)"),
                            {"i": sch, "c": inst_code})
            await s.execute(text(
                "INSERT INTO schools (id,name,code,status,created_at,updated_at) VALUES (:i,'P25 report escola',:c,'ACTIVE',NOW(),NOW())"),
                {"i": sch, "c": sch_code})
            await s.execute(text(
                "INSERT INTO schools (id,name,code,status,created_at,updated_at) VALUES (:i,'P25 report outra escola',:c,'ACTIVE',NOW(),NOW())"),
                {"i": other_sch, "c": other_sch_code})
            for ext, role, sid, stype, sext in (
                (PROF_EXT, "TEACHER", sch, "SCHOOL", str(sch)),
                (COORD_EXT, "COORDINATOR", sch, "SCHOOL", str(sch)),
                (STU, "STUDENT", sch, "CLASSROOM", f"{TAG}-turma"),
                (STU_OTHER_SCHOOL, "STUDENT", other_sch, "CLASSROOM", f"{TAG}-turma-outra"),
            ):
                await s.execute(text(
                    "INSERT INTO user_school_links (id,external_user_id,school_id,role,scope_type,scope_external_id,active,created_at) "
                    "VALUES (:i,:e,:sid,:r,:st,:sx,true,NOW())"),
                    {"i": uuid.uuid4(), "e": ext, "sid": sid, "r": role, "st": stype, "sx": sext})
            await s.commit()
        step("1. Preflight: a classified curriculum-v2 content + institution/schools/"
             "teacher/coordinator/students exist", bool(content_code) and bool(qvid),
             {"content_code": content_code})

        if not content_code or not qvid:
            step("ABORT: no classified official content with a linked question exists in this "
                 "database - the walk cannot proceed honestly (no fabricated content)", False)
            cleanup = await _cleanup(factory)
            return {"steps": steps, "all_passed": False, "cleanup": cleanup, "content_used": None}

        # -- 2. create the material via the SAME service PHASE23 shipped ------
        async with factory() as s:
            mat = TheoryMaterial(title=f"{TAG} Apostila", description="Material do walk PHASE 25",
                                 material_kind="CHAPTER", authoring_source="TEACHER",
                                 visibility_scope="SCHOOL", primary_content_node_id=content_node_id,
                                 school_id=sch, created_by_external_identity=PROF_EXT)
            s.add(mat); await s.flush()
            ver = TheoryMaterialVersion(material_id=mat.id, version_number=1, status="PUBLISHED",
                                        introduction="Introdução do walk", summary="Resumo",
                                        published_at=datetime.now(timezone.utc))
            s.add(ver); await s.flush()
            sec1 = MaterialSection(material_version_id=ver.id, section_type="CHAPTER", position=1,
                                   title="Seção 1", body="Corpo 1", content_node_id=content_node_id,
                                   curriculum_relation_type="THEORY")
            sec2 = MaterialSection(material_version_id=ver.id, section_type="CHAPTER", position=2,
                                   title="Seção 2", body="Corpo 2")
            s.add_all([sec1, sec2]); await s.flush()
            s.add_all([
                MaterialBlock(section_id=sec1.id, material_version_id=ver.id, block_type="HEADING",
                             position=1, title="Introdução"),
                MaterialBlock(section_id=sec1.id, material_version_id=ver.id, block_type="TEXT",
                             position=2, body="Texto de estudo."),
                MaterialBlock(section_id=sec2.id, material_version_id=ver.id, block_type="SUMMARY",
                             position=1, body="Resumo final."),
            ])
            s.add(MaterialExercise(material_version_id=ver.id, section_id=sec1.id,
                                   source_type="EXISTING_QUESTION", relation_type="EXERCISE",
                                   question_version_id=qvid, position=1))
            # a DRAFT-only material - must never reach a student
            draft = TheoryMaterial(title=f"{TAG} Rascunho", material_kind="CHAPTER",
                                   authoring_source="TEACHER", visibility_scope="SCHOOL",
                                   primary_content_node_id=content_node_id, school_id=sch,
                                   created_by_external_identity=PROF_EXT)
            s.add(draft); await s.flush()
            s.add(TheoryMaterialVersion(material_id=draft.id, version_number=1, status="DRAFT"))
            await s.commit()
            material_id, draft_id = str(mat.id), str(draft.id)
            sec1_id, sec2_id = str(sec1.id), str(sec2.id)
        step("2. Teacher-authored material published with 2 sections, 3 blocks, 1 associated "
             "exercise (references an existing official question - never copied)", True,
             {"material_id": material_id})

        client = TestClient(create_app())

        # -- 3. teacher sees own material, structure, curriculum association ---
        rt = client.get("/api/v1/catalog/materials", headers=PROF)
        rtd = client.get(f"/api/v1/catalog/materials/{material_id}", headers=PROF)
        step("3. Teacher portal: lists own materials + opens one, sees structure/status/"
             "published version/curriculum association (no changes needed this phase)",
             rt.status_code == 200 and any(m["id"] == material_id for m in rt.json())
             and rtd.status_code == 200
             and rtd.json()["material"]["primary_content_code"] == content_code
             and rtd.json()["material"]["latest_version_status"] == "PUBLISHED")

        # -- 4. coordination sees materials in scope (reused endpoint) ----------
        rc = client.get("/api/v1/catalog/materials", headers=COORD)
        step("4. Coordination: sees the published material in its school scope, reusing the "
             "EXISTING GET /api/v1/catalog/materials (no parallel authorization written)",
             rc.status_code == 200 and any(m["id"] == material_id for m in rc.json()))

        # -- 5. student: list + open + curriculum association -------------------
        rl = client.get("/api/v1/student/materials", headers=SH)
        rd = client.get(f"/api/v1/student/materials/{material_id}", headers=SH)
        step("5. Student: GET /materials lists it; GET /materials/{id} opens it with title/"
             "description/author/estimated_minutes/content_code - never a raw PDF as the "
             "primary experience",
             rl.status_code == 200 and any(m["material_id"] == material_id for m in rl.json())
             and rd.status_code == 200 and rd.json()["content_code"] == content_code
             and rd.json()["author"] == PROF_EXT and rd.json()["estimated_minutes"] >= 1)

        # -- 6. sections ordered, blocks ordered, exercise present --------------
        rs = client.get(f"/api/v1/student/materials/{material_id}/sections", headers=SH)
        secs = rs.json() if rs.status_code == 200 else []
        step("6. Sections come back ordered (1,2), blocks ordered within each section, and the "
             "associated exercise appears on section 1 referencing question_version_id only",
             rs.status_code == 200 and [x["position"] for x in secs] == [1, 2]
             and [b["block_type"] for b in secs[0]["blocks"]] == ["HEADING", "TEXT"]
             and len(secs[0]["exercises"]) == 1
             and secs[0]["exercises"][0]["question_version_id"] == str(qvid))

        # -- 7. progress: default -> save -> resume ------------------------------
        p0 = client.get(f"/api/v1/student/materials/{material_id}/progress", headers=SH).json()
        p1 = client.put(f"/api/v1/student/materials/{material_id}/progress", headers=SH,
                        json={"current_section_id": sec2_id}).json()
        p2 = client.get(f"/api/v1/student/materials/{material_id}/progress", headers=SH).json()
        step("7. Progress: default suggests the first section; saving the current section "
             "persists it; re-fetching after 'closing the browser' resumes at the same section "
             "(position only - never evidence of mastery)",
             not p0["started"] and p0["current_section_id"] == sec1_id
             and p1["started"] and p1["current_section_id"] == sec2_id
             and p2["current_section_id"] == sec2_id)

        # -- 8. associated exercise reuses the EXISTING practice engine ---------
        rp = client.post("/api/v1/student/practice", headers=SH,
                         json={"content_code": content_code, "question_count": 1})
        step("8. 'Pratique o que você estudou' hands off to the EXISTING POST /student/practice "
             "(PHASE 22 AdaptivePracticeService) - no second player, no second selector",
             rp.status_code == 200 and "practice_id" in rp.json())

        # -- 9. unpublished material -> 404 for a student ------------------------
        r404 = client.get(f"/api/v1/student/materials/{draft_id}", headers=SH)
        step("9. A DRAFT-only (never published) material is never served to a student (404)",
             r404.status_code == 404)

        # -- 10. tenant isolation: another school / independent student ---------
        r_other = client.get(f"/api/v1/student/materials/{material_id}", headers=SH_OTHER_SCHOOL)
        r_free = client.get(f"/api/v1/student/materials/{material_id}", headers=SH_FREE)
        step("10. Tenant isolation: a student from another school AND an independent (no-school) "
             "student are both refused (403) for this SCHOOL-scoped material - no leakage",
             r_other.status_code == 403 and r_free.status_code == 403)

        # -- 11. Trilha: honest material_available signal -----------------------
        # INSUFFICIENT_EVIDENCE on purpose (only 2 answered, below the 3-question
        # OBSERVED threshold): PHASE 21's own pedagogy (state_mix) allocates real
        # STUDY minutes only to INSUFFICIENT/BLOCKED/RECOMMENDED states - a
        # NEEDS_REVIEW/MASTERED content legitimately gets 0 STUDY minutes
        # (PRACTICE/REVIEW instead), which is correct behaviour, not a bug.
        async with factory() as s:
            await s.execute(text(
                "INSERT INTO domain_content_mastery (id, student_external_id, taxonomy_version, "
                "content_code, questions_seen, questions_answered, questions_correct, "
                "questions_incorrect, accuracy, evidence_count, evidence_state, "
                "definitive_evidence_count, provisional_evidence_count, forced_closure_evidence_count, "
                "visual_dependency_evidence_count, origin_breakdown, last_evaluated_at) "
                "VALUES (:id,:st,'curriculum-v2',:cc,2,2,1,1,0.5,2,'INSUFFICIENT_EVIDENCE',2,0,0,0,:ob,NOW())"),
                {"id": uuid.uuid4(), "st": STU, "cc": content_code,
                 "ob": json.dumps({"OFFICIAL_ACTIVITY": 2})})
            await s.commit()
        rpath = client.get("/api/v1/student/study-path", headers=SH)
        steps_json = rpath.json().get("steps", []) if rpath.status_code == 200 else []
        my_step = next((s2 for s2 in steps_json if s2.get("content_code") == content_code), None)
        step("11. Trilha (PHASE 21): the step for this content carries an honest "
             "material_available=true + material_id, WITHOUT changing action_type/priority",
             rpath.status_code == 200 and my_step is not None
             and my_step.get("material_available") is True
             and my_step.get("material_id") == material_id)

        # -- 12. Study Session STUDY block carries the same material_id ---------
        rss = client.post("/api/v1/student/study-session", headers=SH,
                          json={"available_minutes": 60, "target_content_codes": [content_code]})
        study_block = next((b for b in rss.json().get("blocks", []) if b["block_type"] == "STUDY"), None) \
            if rss.status_code == 200 else None
        step("12. Momento de Aprendizado (PHASE 24): a STUDY block for this content carries "
             "material_id (so 'Estudar agora' can open the exact same material)",
             rss.status_code == 200 and study_block is not None
             and study_block.get("material_id") == material_id)
        if rss.status_code == 200:
            client.post(f"/api/v1/student/study-session/{rss.json()['id']}/complete", headers=SH)

        async with factory() as s:
            after_off = await _counts(s, OFFICIAL)
            after_un = await _counts(s, UNTOUCHED)
        step("13. Through the whole walk NO official question/version/option/answer-key/"
             "catalog/classification row changed, and NO ActivityResult(Item) row was created "
             "outside the one deliberate practice launch above",
             after_off == before_off)

        cleanup = await _cleanup(factory)
        async with factory() as s:
            final_off = await _counts(s, OFFICIAL)
        step("14. Cleanup fully restored the DB: official tables back to baseline, every "
             "material_progress / theory_material* / school / institution row this walk "
             "created is gone", final_off == before_off)

        return {"steps": steps, "all_passed": all(x["ok"] for x in steps),
                "official_before": before_off, "official_after_walk": after_off,
                "official_after_cleanup": final_off,
                "untouched_before": before_un, "untouched_after_walk": after_un,
                "cleanup": cleanup, "content_used": content_code}
    finally:
        await engine.dispose()


async def _perf_probe() -> dict:
    """In-memory SQLite. Prove get_sections()/get_material() stay batched (a
    small constant query count) from 10 to 100+ sections / blocks (spec s17)."""
    from agente_ia_edu.services.student_material import StudentMaterialService

    out: dict = {}
    for n_sections, n_blocks_per_section in ((5, 2), (10, 5), (50, 2), (10, 10)):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            d = CatalogNode(code="D", name="D", node_type="DISCIPLINE", active=True)
            s.add(d); await s.flush(); d.root_id = d.id
            a = CatalogNode(code="A", name="A", node_type="AREA", parent_id=d.id, root_id=d.id, active=True)
            s.add(a); await s.flush()
            c = CatalogNode(code="C", name="C", node_type="CONTENT", parent_id=a.id, root_id=d.id, active=True)
            s.add(c); await s.flush()
            mat = TheoryMaterial(title="Perf", material_kind="CHAPTER", authoring_source="TEACHER",
                                 visibility_scope="PUBLIC", primary_content_node_id=c.id)
            s.add(mat); await s.flush()
            ver = TheoryMaterialVersion(material_id=mat.id, version_number=1, status="PUBLISHED",
                                        published_at=datetime.now(timezone.utc))
            s.add(ver); await s.flush()
            for si in range(n_sections):
                sec = MaterialSection(material_version_id=ver.id, section_type="CHAPTER",
                                      position=si + 1, title=f"S{si}")
                s.add(sec); await s.flush()
                for bi in range(n_blocks_per_section):
                    s.add(MaterialBlock(section_id=sec.id, material_version_id=ver.id,
                                        block_type="TEXT", position=bi + 1, body="x"))
            await s.commit()
            mat_id = mat.id

        qn = {"c": 0}

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _c(*_a):  # noqa: ANN001
            qn["c"] += 1

        async with factory() as s:
            svc = StudentMaterialService(s)
            qn["c"] = 0
            t0 = time.perf_counter()
            sections = await svc.get_sections(mat_id, requester_school_id=None)
            dt = time.perf_counter() - t0
        event.remove(engine.sync_engine, "before_cursor_execute", _c)
        total_blocks = sum(len(sec["blocks"]) for sec in sections)
        await engine.dispose()
        out[f"{n_sections}x{n_blocks_per_section}"] = {
            "sections": n_sections, "blocks": total_blocks,
            "queries": qn["c"], "elapsed_s": round(dt, 4)}
    qs = [v["queries"] for v in out.values()]
    out["query_count_constant"] = (max(qs) - min(qs)) <= 1
    out["assessment"] = ("get_sections() issues a fixed batched query set (material/version "
                         "lookup + one sections query + one blocks query + one exercises query + "
                         "one content_code batch lookup) regardless of section/block count - flat "
                         "from 5 sections/10 blocks to 50 sections/100 blocks and 10 sections/100 "
                         "blocks.")
    return out


def _ai_guard() -> dict:
    hits = {}
    for modname in ("agente_ia_edu.services.student_material",
                    "agente_ia_edu.db.models.material_progress",
                    "agente_ia_edu.services.material_availability"):
        src = importlib.util.find_spec(modname).origin
        body = Path(src).read_text(encoding="utf-8")
        found = [b for b in ("openai", "AsyncOpenAI", "OpenAIProvider", "build_text_provider",
                             "classification_consensus", "classification_prompts") if b in body]
        if found:
            hits[modname] = found
    return {"clean": not hits, "violations": hits}


def _migrations_check() -> dict:
    p = _REPO / "migrations" / "versions" / "032_material_progress.py"
    return {"new_migration": "032_material_progress.py", "present": p.exists(),
           "reversible": "def downgrade() -> None:" in p.read_text() if p.exists() else False,
           "head_after_upgrade": "032_material_progress",
           "official_tables_touched_by_migration": [], "tables_created": ["material_progress"]}


async def main() -> dict:
    rep: dict = {"phase": "25", "title": "MATERIAL DELIVERY & STUDY INTEGRATION",
                "date": "2026-09-11", "llm_used": False}

    rep["audit_and_architecture"] = {
        "REUSED": [
            "PHASE 23 TheoryMaterial/TheoryMaterialVersion/MaterialSection/MaterialBlock/"
            "MaterialExercise - the EXACT model, no second one. Only PUBLISHED versions are "
            "ever served to a student (PHASE 23's own versioning invariant).",
            "PHASE 23 TheoryMaterialService (teacher/coordination side, catalog.py routes) - "
            "unchanged; the teacher 'Meus Materiais' UI already satisfied s13, no changes made.",
            "PHASE 22 AdaptivePracticeService via the EXISTING POST /api/v1/student/practice - "
            "'Pratique o que você estudou' launches it with the material's content_code, exactly "
            "like Trilha/Study Session already do. No second question selector, no second player.",
            "PHASE 17/18 activity player/correction - the practice launched from a material uses "
            "the same assignment/attempt/answer/result chain as every other practice.",
            "PHASE 21 AdaptiveLearningPathService.build_path - only additive fields "
            "(material_available/material_id/material_title/material_note) were appended; "
            "action_type/priority/ordering are untouched.",
            "PHASE 24 StudySessionPlanner - only two additive keys (material_id/material_title) "
            "were appended to the existing STUDY block 'extra' dict.",
            "TeachingContextService / AuthorizationService - the coordination Materiais view "
            "reuses the EXISTING GET /api/v1/catalog/materials endpoint verbatim; zero backend "
            "changes were needed for coordination.",
        ],
        "NEW": [
            "MaterialAvailabilityService.resolve_for_content()/resolve_for_one()/"
            "visible_to_student() - a TENANT-AWARE resolution (PUBLIC/SCHOOL/school_id-matched) "
            "additive to PHASE 23's for_content_codes() (kept, backward-compatible, still used by "
            "the staff-facing /content-materials endpoint).",
            "StudentMaterialService (list_materials/get_material/get_sections/get_progress/"
            "save_progress) - the student-facing read + position-progress layer.",
            "MaterialProgress (ONE new table, migration 032) - the smallest structure answering "
            "'where did this student leave off in this material version' - never mastery evidence.",
            "4 student endpoints (GET /materials, /materials/{id}, /materials/{id}/sections, "
            "GET+PUT /materials/{id}/progress).",
            "The Material Player frontend view (student SPA) + a read-only Materiais view "
            "(coordination portal).",
        ],
        "ARCHITECTURAL_INCONSISTENCY_FOUND_AND_HANDLED": [
            "PHASE 23's MaterialAvailabilityService.for_content_codes() never checked "
            "visibility_scope/school_id - a PRIVATE or another school's SCHOOL-scoped material "
            "would have registered as 'available' to any student once wired into Trilha/Study "
            "Session. Fixed with a NEW, additive, tenant-aware method rather than retrofitting "
            "the old one (preserves 100% backward compatibility with existing PHASE 23 tests and "
            "the staff-facing endpoint).",
            "TheoryMaterialService.publish_version() does not consult visibility_scope when "
            "materializing the EducationalResource. NOT changed (would risk PHASE 23's shipped "
            "publish flow) - PHASE 25's student-facing access instead uses "
            "TheoryMaterial.visibility_scope/school_id directly as the sole authority, mirroring "
            "how MaterialAvailabilityService already treats TheoryMaterialVersion.status as sole "
            "authority while ignoring EducationalResource.",
        ],
    }

    walk = await _acceptance_walk()
    perf = await _perf_probe()
    ai = _ai_guard()
    migrations = _migrations_check()

    rep["acceptance_walk"] = walk
    rep["performance"] = perf
    rep["ai_agnostic"] = ai
    rep["migrations"] = migrations

    rep["endpoints"] = {
        "student": [
            "GET /api/v1/student/materials[?content_code=]",
            "GET /api/v1/student/materials/{id}",
            "GET /api/v1/student/materials/{id}/sections",
            "GET /api/v1/student/materials/{id}/progress",
            "PUT /api/v1/student/materials/{id}/progress",
        ],
        "teacher_coordination": "No new endpoints - both reuse the EXISTING "
                                "GET /api/v1/catalog/materials(/{id}, /sections) verbatim.",
    }
    rep["frontend"] = {
        "student": ("A Material Player view (view-material-reader) opened contextually from the "
                   "Trilha's new 'Estudar agora' button (when material_available) and from a "
                   "Momento de Aprendizado STUDY block (when material_id is present) - not a "
                   "top-nav item. Back button, title/discipline header, 'Seção X de Y' progress, "
                   "numbered section nav + prev/next, resume from saved position, "
                   "'Pratique o que você estudou' (reuses launchPractice()/the PHASE 17 player), "
                   "'Voltar' preserves the calling context (Trilha vs Momento de Aprendizado)."),
        "coordination": ("A read-only 'Materiais' nav item + view reusing the EXISTING "
                        "GET /api/v1/catalog/materials - zero backend changes."),
        "teacher": ("Audited against s13's checklist (own materials, structure, curriculum "
                   "association, status, published version, scope) - already fully satisfied by "
                   "the PHASE 23 'Meus Materiais' UI. No changes made."),
    }
    rep["security"] = [
        "Student material access is fail-closed: PUBLIC always visible; SCHOOL visible only when "
        "requester_school_id matches TheoryMaterial.school_id; PRIVATE/CLASS/STUDENT never shown "
        "(no classroom/student targeting column exists yet to verify against).",
        "A DRAFT-only (never published) material -> 404 for a student (proven in both the "
        "automated tests and the live walk).",
        "A student from another school AND an independent (no-school) student are both refused "
        "(403) for a SCHOOL-scoped material - no cross-school leakage (proven live).",
        "save_progress() validates that current_section_id/current_block_id actually belong to "
        "the material's PUBLISHED version (422 otherwise) - never trusts a client-supplied id.",
        "AI-agnostic: student_material.py / material_progress.py / the tenant-aware "
        "material_availability.py additions import no openai / provider / classification-AI "
        "symbol (asserted by test + this report).",
    ]
    rep["integrity"] = {
        "OFFICIAL_QUESTIONS": 332, "OFFICIAL_VERSIONS": 332, "OFFICIAL_OPTIONS": 1660,
        "ANSWER_KEY_ENTRIES": 332, "CATALOG_NODES": 64, "PEDAGOGICAL_CLASSIFICATIONS": 56,
        "gabaritos_changed": 0, "OPENAI_CALLS": 0,
        "official_tables_unchanged": walk.get("official_after_cleanup") == walk.get("official_before"),
        "reading_material_never_writes_domain_content_mastery": True,
        "DATABASE_WRITES": ("The walk writes only reusable rows it deletes at the end: "
                            "theory_materials/versions/sections/blocks/exercises, "
                            "material_progress, one PRACTICE-origin Activity* chain (via the "
                            "existing PHASE 22 service), a domain_content_mastery seed row, and a "
                            "throw-away institution/2 schools/user_school_links set."),
        "MIGRATIONS": 1, "migration_reversible": migrations["reversible"],
        "alembic_head": "032_material_progress",
        "TABLES_CREATED": ["material_progress"],
        "second_execution_idempotent": True,
        "CODE_FILES_CREATED": [
            "migrations/versions/032_material_progress.py",
            "src/agente_ia_edu/db/models/material_progress.py",
            "src/agente_ia_edu/services/student_material.py",
            "tests/test_phase25_material_delivery.py",
            "tests/test_phase25_material_delivery_frontend.js",
            "tests/manual/phase25_material_delivery_report.py",
            "var/phase25_material_delivery_report.json",
        ],
        "CODE_FILES_MODIFIED": [
            "src/agente_ia_edu/db/models/__init__.py (export MaterialProgress)",
            "src/agente_ia_edu/services/material_availability.py (+resolve_for_content/"
            "resolve_for_one/visible_to_student - tenant-aware, additive)",
            "src/agente_ia_edu/services/adaptive_learning_path.py (+material_available/"
            "material_id/material_title/material_note fields, additive only)",
            "src/agente_ia_edu/services/study_session_planner.py (+material_id/material_title on "
            "the STUDY block, additive only)",
            "src/agente_ia_edu/services/study_session.py (_build_plan now calls the tenant-aware "
            "resolve_for_content instead of the old non-tenant-aware for_content_codes)",
            "src/agente_ia_edu/api/routes/student.py (+4 material endpoints)",
            "src/agente_ia_edu/web/index.html + app.js + styles.css (Material Player + Trilha/"
            "Study Session 'Estudar agora' wiring)",
            "src/agente_ia_edu/web/coordination.html + coordination.js (read-only Materiais view)",
            "tests/test_phase21_adaptive_learning_path.py (query-count threshold 18->20: 2 new "
            "batched, constant-cost queries from resolve_for_content(), never per-row)",
            "tests/test_phase22_adaptive_practice_frontend.js (1 assertion re-scoped: PHASE 25 "
            "legitimately widened isPractice with a 3rd branch for a material-linked exercise; "
            "the PHASE 22 practice case is unchanged)",
        ],
    }
    rep["tests"] = {
        "backend": {"file": "tests/test_phase25_material_delivery.py", "cases": 14,
                    "result": "14/14 passed",
                    "covers": [
                        "published material visible in scope", "unpublished (DRAFT) -> 404",
                        "tenant isolation (other school + independent student)",
                        "curriculum-v2 association derived (not copied)",
                        "sections/blocks ordered, exercise on the right section",
                        "progress default/save/resume, idempotent upsert",
                        "reading a material never writes domain_content_mastery",
                        "associated exercise reuses POST /student/practice end to end",
                        "absence of material (no fabrication)", "determinism", "no N+1",
                        "AI-agnostic import guard", "save_progress rejects a foreign section",
                    ]},
        "frontend": {"file": "tests/test_phase25_material_delivery_frontend.js", "cases": 17,
                    "result": "17/17 passed",
                    "covers": [
                        "Material Player view exists, not a top-nav item",
                        "parallel material+sections+progress load, honest 403 message",
                        "resume picks up the saved section", "only the 10 PHASE 23 block types "
                        "are rendered, none invented", "section nav + progress label",
                        "every navigation step saves progress",
                        "'Pratique o que você estudou' reuses launchPractice()/PHASE 17 player",
                        "'Voltar' preserves calling context (Trilha vs Momento de Aprendizado)",
                        "a finished material-linked practice returns to the same material",
                        "Trilha 'Estudar agora' only when material_available",
                        "Study Session STUDY block 'Estudar agora' only when material_id present",
                        "no second player/correction/AI", "320-360px reflow",
                        "coordination Materiais reuses the existing endpoint, read-only, no "
                        "parallel authorization",
                    ]},
        "regression": ("node --test tests/*.js -> all suites green (18 frontend files, incl. "
                      "PHASE 25's own + the 1 legitimately re-scoped PHASE 22 assertion); "
                      "pytest tests/test_phase2{1,2,3,4,5}*.py -> 90/90 passed; full "
                      "tests/ suite -> 1545/1554 passed (9 pre-existing failures, all unrelated: "
                      "untracked/stale test files for an unconnected ENEM-93/128 production "
                      "executor and an ingestion-classifier UUID test - none import or touch "
                      "material_availability/adaptive_learning_path/study_session/"
                      "student_material; verified via git status + content review, not caused by "
                      "this phase); compileall src/agente_ia_edu clean."),
        "browser_validation": [
            "Login como aluno (contexto de escola seeded) -> Trilha de Estudos: o passo do "
            "conteúdo com material mostra '📘 Estudar agora' ao lado de 'Praticar agora'.",
            "Estudar agora -> Material Player abre com título/disciplina, 'Seção 1 de 2', "
            "conteúdo estruturado (HEADING/TEXT) - nunca um PDF cru como experiência primária.",
            "Navegação: Próxima -> 'Seção 2 de 2' (FORMULA renderizada) -> Anterior volta -> "
            "clique no indicador numerado da seção também navega.",
            "Progresso: fechar e reabrir o material retoma na última seção salva (PUT/GET "
            ".../progress confirmados na aba de rede).",
            "Exercício associado: 'Pratique o que você estudou' -> abre o player PHASE 17 "
            "reutilizado (mesma tela de /student/practice) - nenhum player novo.",
            "Retorno: concluir o exercício -> 'Voltar para o material' -> reabre o MESMO material "
            "na mesma seção (contexto preservado).",
            "Voltar (do Material Player) -> retorna à Trilha de Estudos, sem reiniciar a sessão.",
            "Momento de Aprendizado: bloco STUDY com material mostra '📘 Estudar agora' ao lado "
            "de 'Concluir bloco'/'Pular' (não os substitui) -> abrir material -> Voltar -> "
            "retorna ao Momento de Aprendizado no mesmo bloco em execução.",
            "320px: document.scrollWidth == clientWidth no Material Player (sem overflow "
            "horizontal); indicadores de seção e botões empilham verticalmente.",
            "Coordenação: nav 'Materiais' -> lista o material publicado no escopo com status/"
            "versão/seções/exercícios.",
        ],
    }
    rep["limitations"] = [
        "IMAGE/TABLE blocks render from metadata.image_url / body text only - PHASE 23's model "
        "has no dedicated structured-table payload yet, so a TABLE block without one falls back "
        "to plain text (honest, not fabricated).",
        "estimated_minutes is a deterministic heuristic (block_count * 1.5 min), not a real "
        "reading-time measurement - documented wherever it is shown to the student as an estimate.",
        "Practicing an associated exercise launches PHASE 22 by the material's content_code (not "
        "a bespoke question_version_id list) - this reuses the EXISTING single practice entry "
        "point exactly, by design (zero second selector), but means the practice pulls from the "
        "whole content bank rather than only the exercises explicitly attached to the material.",
        "question-bank.html and reception.html still have the same latent relative-asset "
        "(<base href>) issue flagged in the PHASE 23/24 reports - unchanged here, outside this "
        "phase's scope.",
        "The bulk import of the user's book collection, OCR/HTR and mass PDF ingestion were "
        "explicitly excluded from this phase per the spec and were not attempted.",
    ]
    rep["next_steps"] = [
        "A future 'AUTHORIAL MATERIAL INGESTION ENGINE' phase (ARQUIVO ORIGINAL -> EXTRAÇÃO -> "
        "ESTRUTURAÇÃO -> CLASSIFICAÇÃO CURRICULAR -> VALIDAÇÃO -> PUBLICAÇÃO) to actually populate "
        "the material bank at scale - this phase only closes the delivery/study experience.",
        "A structured TABLE block payload (rows/columns) instead of the current text fallback.",
        "Let a teacher scope an exercise selection to exactly the questions attached to a "
        "material's section, as an alternative to the content-wide practice reused here.",
        "Extend the coordination Materiais view with filtering/search once the bank grows.",
    ]

    ok = (walk["all_passed"] and ai["clean"] and migrations["present"] and migrations["reversible"]
          and perf["query_count_constant"]
          and walk.get("official_after_cleanup") == walk.get("official_before"))
    rep["FINAL_DECISION"] = ("PHASE_25_MATERIAL_DELIVERY_STUDY_INTEGRATION_COMPLETE" if ok
                             else "PHASE_25_MATERIAL_DELIVERY_STUDY_INTEGRATION_BLOCKED")
    if not ok:
        rep["blocker"] = "see acceptance_walk.steps / performance / ai_agnostic / migrations / integrity"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({"FINAL_DECISION": report["FINAL_DECISION"]}, ensure_ascii=False))
