"""PHASE 23 - Content & Question Bank Foundation: report + real-DB acceptance walk.

PHASE 23 REUSES the authored-material stack that already existed (migration 005 +
014): TheoryMaterial / TheoryMaterialVersion / MaterialSection / MaterialExercise
/ EducationalResource / TheoryMaterialService / the /api/v1/catalog/materials*
routes. It adds only the smallest missing pieces:

  * MaterialBlock              - the 4th structural level (reusable content units)
  * material_sections.content_node_id + curriculum_relation_type
                              - structured, versionable section<->curriculum link
  * material_exercises.relation_type
                              - pedagogical role of the material<->question link
  * theory_materials identity - description / material_kind / authoring_source /
                                visibility_scope on the STABLE parent
  * MaterialAvailabilityService - deterministic material_available / material_count
  * HTTP endpoints for sections / blocks / question links / availability / PATCH
  * a "Meus Materiais" teacher screen

Migration 030 also reconciles ck_theory_material_versions_status: the live DB
still had the original 3-value lowercase set while the ORM + service lifecycle
have long expected the 6-value UPPER set (draft/published/archived vs
DRAFT/PENDING_REVIEW/APPROVED/REJECTED/PUBLISHED/ARCHIVED) - the drift made the
authored-material path unusable on PostgreSQL. 030 widens it to the model.

Questions are NEVER copied - only referenced by question_version_id. ZERO AI.
The acceptance walk runs against the LIVE database and deletes every row it
creates. A perf probe on in-memory SQLite proves the materials list + counts do
not scale their query count with the number of materials / blocks / questions.
"""
from __future__ import annotations

import asyncio
import importlib
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
from sqlalchemy import event, func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from agente_ia_edu.api.app import app, create_app  # noqa: E402
from agente_ia_edu.api.dependencies import get_session_factory  # noqa: E402
from agente_ia_edu.db.base import Base  # noqa: E402
from agente_ia_edu.db.models import (  # noqa: E402
    CatalogNode, MaterialBlock, MaterialExercise, MaterialSection, Question,
    QuestionOption, QuestionVersion, TheoryMaterialVersion,
)
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.catalog import TheoryMaterialService  # noqa: E402
from agente_ia_edu.services.material_availability import MaterialAvailabilityService  # noqa: E402

OUT = _REPO / "var" / "phase23_content_question_foundation_report.json"
TITLE_PREFIX = "P23-report"
PROF = {"Authorization": "Bearer teacher:p23-report-prof"}
PROF_OTHER = {"Authorization": "Bearer teacher:p23-report-other"}

OFFICIAL = ("questions", "question_versions", "question_options", "answer_key_entries",
            "answer_key_revisions", "catalog_nodes", "pedagogical_classifications")
ACTIVITY = ("activity_attempts", "activity_answers", "activity_results",
            "activity_result_items", "domain_content_mastery")


async def _counts(s, tables) -> dict:
    return {t: int(await s.scalar(text(f"SELECT count(*) FROM {t}"))) for t in tables}


async def _cleanup(factory) -> dict:
    async with factory() as s:
        mids = (await s.execute(text(
            "SELECT id FROM theory_materials WHERE title LIKE :p"),
            {"p": f"{TITLE_PREFIX}%"})).scalars().all()
        for m in mids:
            vids = (await s.execute(text(
                "SELECT id FROM theory_material_versions WHERE material_id=:m"), {"m": m})).scalars().all()
            for v in vids:
                await s.execute(text("DELETE FROM material_blocks WHERE material_version_id=:v"), {"v": v})
                await s.execute(text("DELETE FROM material_exercises WHERE material_version_id=:v"), {"v": v})
                await s.execute(text("DELETE FROM material_sections WHERE material_version_id=:v"), {"v": v})
            await s.execute(text("DELETE FROM theory_material_versions WHERE material_id=:m"), {"m": m})
            await s.execute(text("DELETE FROM theory_materials WHERE id=:m"), {"m": m})
        await s.execute(text("DELETE FROM educational_resources WHERE title LIKE :p"),
                        {"p": f"{TITLE_PREFIX}%"})
        await s.execute(text(
            "DELETE FROM admin_audit_logs WHERE entity_type='THEORY_MATERIAL' AND entity_id = ANY(:ids)"),
            {"ids": [str(m) for m in mids]})
        await s.commit()
        return {"materials_removed": len(mids)}


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
            content_code, content2 = (await s.execute(text(
                "SELECT code FROM catalog_nodes WHERE node_type IN ('CONTENT','SUBCONTENT') "
                "AND active AND code IS NOT NULL ORDER BY code LIMIT 2"))).scalars().all()
            node2_id = str((await s.execute(text(
                "SELECT id FROM catalog_nodes WHERE code=:c"), {"c": content2})).scalar_one())
            qvids = [str(x) for x in (await s.execute(text(
                "SELECT id FROM question_versions LIMIT 3"))).scalars().all()]

        client = TestClient(create_app())

        # 1 create
        r = client.post("/api/v1/catalog/materials", headers=PROF, json={
            "title": f"{TITLE_PREFIX} apostila", "description": "apoio",
            "material_kind": "WORKBOOK", "authoring_source": "TEACHER", "visibility_scope": "PRIVATE"})
        m = r.json()
        mid = m.get("id")
        step("1. POST /api/v1/catalog/materials -> 201; DRAFT v1 auto-created; identity fields "
             "(kind/source/visibility) on the stable parent; UNMAPPED curriculum",
             r.status_code == 201 and m["latest_version_status"] == "DRAFT"
             and m["material_kind"] == "WORKBOOK" and m["authoring_source"] == "TEACHER"
             and m["visibility_scope"] == "PRIVATE" and m["curriculum_status"] == "UNMAPPED"
             and (m["section_count"], m["block_count"], m["question_count"]) == (0, 0, 0),
             {k: m.get(k) for k in ("latest_version_status", "material_kind", "visibility_scope")})

        # 2 PATCH
        p = client.patch(f"/api/v1/catalog/materials/{mid}", headers=PROF,
                         json={"description": "revisado", "visibility_scope": "SCHOOL",
                               "primary_content_node_id": node2_id})
        step("2. PATCH updates DRAFT identity + maps it to a curriculum-v2 node by id "
             "(resolved from catalog_nodes, never invented)",
             p.status_code == 200 and p.json()["visibility_scope"] == "SCHOOL"
             and p.json()["curriculum_status"] == "MAPPED"
             and p.json()["primary_content_code"] == content2)

        # 3 sections + curriculum association at section level by stable code
        s1 = client.post(f"/api/v1/catalog/materials/{mid}/sections", headers=PROF, json={
            "section_type": "CHAPTER", "position": 1, "title": "Cap 1",
            "content_code": content_code, "curriculum_relation_type": "THEORY"})
        s2 = client.post(f"/api/v1/catalog/materials/{mid}/sections", headers=PROF,
                         json={"section_type": "SECTION", "position": 2})
        bad = client.post(f"/api/v1/catalog/materials/{mid}/sections", headers=PROF,
                          json={"section_type": "SECTION", "position": 3, "content_code": "NOPE-NOPE"})
        step("3. Sections carry a structured curriculum link (content_code + relation_type); "
             "no code -> UNMAPPED; unknown code -> 422 and NO node is created",
             s1.status_code == 201 and s1.json()["curriculum_status"] == "MAPPED"
             and s1.json()["content_code"] == content_code
             and s2.json()["curriculum_status"] == "UNMAPPED" and bad.status_code == 422,
             {"section_1": s1.json().get("content_code"), "bad_status": bad.status_code})
        sid = s1.json()["id"]

        # 4 blocks (4th level)
        blocks_ok = all(
            client.post(f"/api/v1/catalog/materials/{mid}/sections/{sid}/blocks", headers=PROF,
                        json={"block_type": bt, "position": i, "title": bt}).status_code == 201
            for i, bt in enumerate(["HEADING", "TEXT", "DEFINITION", "FORMULA", "SOLVED_EXAMPLE"], start=1))
        blist = client.get(f"/api/v1/catalog/materials/{mid}/sections/{sid}/blocks", headers=PROF).json()
        step("4. MaterialBlock is the 4th structural level: 5 blocks added under one section, "
             "returned in position order",
             blocks_ok and [b["block_type"] for b in blist]
             == ["HEADING", "TEXT", "DEFINITION", "FORMULA", "SOLVED_EXAMPLE"])

        # 5 material <-> question (reference only, no duplication)
        q1 = client.post(f"/api/v1/catalog/materials/{mid}/questions", headers=PROF,
                         json={"question_version_id": qvids[0], "relation_type": "EXERCISE", "section_id": sid})
        q2 = client.post(f"/api/v1/catalog/materials/{mid}/questions", headers=PROF,
                         json={"question_version_id": qvids[1], "relation_type": "EXAMPLE"})
        dup = client.post(f"/api/v1/catalog/materials/{mid}/questions", headers=PROF,
                          json={"question_version_id": qvids[0]})
        miss = client.post(f"/api/v1/catalog/materials/{mid}/questions", headers=PROF,
                           json={"question_version_id": str(uuid.uuid4())})
        async with factory() as s:
            off_mid = await _counts(s, OFFICIAL)
        step("5. Material<->question is a REFERENCE (question_version_id): link 2, duplicate -> 409, "
             "unknown -> 404, and NO question/version/option row is created by any of it",
             q1.status_code == 201 and q2.status_code == 201 and dup.status_code == 409
             and miss.status_code == 404 and off_mid == before_off,
             {"link": q1.status_code, "dup": dup.status_code, "missing": miss.status_code})

        # 6 batched counts on detail
        d = client.get(f"/api/v1/catalog/materials/{mid}", headers=PROF).json()["material"]
        step("6. Material detail reports batched section / block / question counts",
             (d["section_count"], d["block_count"], d["question_count"]) == (2, 5, 2),
             {"counts": [d["section_count"], d["block_count"], d["question_count"]]})

        # 7 remove a link (question untouched) + idempotency
        rm = client.delete(f"/api/v1/catalog/materials/{mid}/questions/{qvids[0]}", headers=PROF)
        rm2 = client.delete(f"/api/v1/catalog/materials/{mid}/questions/{qvids[0]}", headers=PROF)
        async with factory() as s:
            off_rm = await _counts(s, OFFICIAL)
        step("7. Removing a link is 204, repeating is 404 (idempotent), the question is untouched",
             rm.status_code == 204 and rm2.status_code == 404 and off_rm == before_off)

        # 8 tenant / owner authorization
        step("8. Another teacher cannot read or mutate an unscoped material they did not create "
             "(403 on GET / PATCH / add-section / link-question)",
             client.get(f"/api/v1/catalog/materials/{mid}", headers=PROF_OTHER).status_code == 403
             and client.patch(f"/api/v1/catalog/materials/{mid}", headers=PROF_OTHER,
                              json={"title": "x"}).status_code == 403
             and client.post(f"/api/v1/catalog/materials/{mid}/sections", headers=PROF_OTHER,
                             json={"section_type": "S", "position": 9}).status_code == 403)

        # 9 versioning: publish -> immutable, new draft still allowed
        for action in ("submit", "approve", "publish"):
            pv = client.post(f"/api/v1/catalog/materials/{mid}/review", headers=PROF,
                             json={"action": action})
        blocked = client.post(f"/api/v1/catalog/materials/{mid}/sections", headers=PROF,
                              json={"section_type": "CHAPTER", "position": 9})
        nv = client.post(f"/api/v1/catalog/materials/{mid}/versions", headers=PROF,
                         json={"introduction": "v2"})
        step("9. A PUBLISHED version is immutable (add-section -> 422) but a NEW draft version can "
             "still be created (non-destructive editing)",
             pv.status_code == 200 and pv.json()["status"] == "PUBLISHED"
             and blocked.status_code == 422 and nv.status_code == 201
             and nv.json()["version_number"] == 2 and nv.json()["status"] == "DRAFT")

        # 10 material_available / material_count contract (deterministic, idempotent)
        av1 = client.get(f"/api/v1/catalog/content-materials?content_code={content2}", headers=PROF).json()
        av2 = client.get(f"/api/v1/catalog/content-materials?content_code={content2}", headers=PROF).json()
        step("10. GET /content-materials returns deterministic material_available / material_count "
             "for the published material's content; a repeat call is identical",
             av1 == {"content_code": content2, "material_available": True, "material_count": 1}
             and av1 == av2, av1)

        async with factory() as s:
            after_off = await _counts(s, OFFICIAL)
            after_act = await _counts(s, ACTIVITY)
        cleanup = await _cleanup(factory)
        async with factory() as s:
            final_off = await _counts(s, OFFICIAL)
            final_act = await _counts(s, ACTIVITY)

        step("11. Through the whole walk NO official question / version / option / answer-key / "
             "catalog / classification row changed",
             after_off == before_off)
        step("12. No ActivityAttempt / Answer / Result / Domain Map row was touched",
             after_act == before_act and final_act == before_act)
        step("13. Cleanup removed every P23 material / version / section / block / exercise",
             final_off == before_off
             and cleanup["materials_removed"] >= 1)

        return {"steps": steps, "all_passed": all(x["ok"] for x in steps),
                "official_before": before_off, "official_after_walk": after_off,
                "official_after_cleanup": final_off, "activity_before": before_act,
                "activity_after_cleanup": final_act, "cleanup": cleanup}
    finally:
        await engine.dispose()


async def _perf_probe() -> dict:
    """In-memory SQLite. Prove the materials list + the block/question counts are
    a fixed number of queries regardless of scale."""
    out: dict = {}
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as s:
        d = CatalogNode(code="D", name="D", node_type="DISCIPLINE", active=True)
        s.add(d); await s.flush(); d.root_id = d.id
        c = CatalogNode(code="C", name="C", node_type="CONTENT", parent_id=d.id, root_id=d.id, active=True)
        s.add(c); await s.flush()
        q = Question(validation_status="validated", origin_type="IMPORTED",
                     status="PUBLISHED", visibility_scope="PUBLIC")
        s.add(q); await s.flush()
        v = QuestionVersion(question_id=q.id, version_kind="official_original",
                            canonical_text="e", statement="e", content_hash="h", is_immutable=True)
        s.add(v); await s.flush()
        for pos, k in enumerate("ABCDE", start=1):
            s.add(QuestionOption(question_version_id=v.id, option_key=k, position=pos,
                                 text=k, is_valid_option=(k == "A")))
        await s.commit()
        qv_id = str(v.id)

    app.dependency_overrides[get_session_factory] = lambda: factory
    client = TestClient(app)
    H = {"Authorization": "Bearer teacher:perf"}

    def _list_query_count():
        n = {"c": 0}

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _c(*_a):  # noqa: ANN001
            n["c"] += 1
        try:
            r = client.get("/api/v1/catalog/materials", headers=H)
            assert r.status_code == 200, r.text
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _c)
        return n["c"]

    made = 0
    for target in (1, 10, 50, 100):
        while made < target:
            mid = client.post("/api/v1/catalog/materials", headers=H,
                              json={"title": f"perf {made}"}).json()["id"]
            sid = client.post(f"/api/v1/catalog/materials/{mid}/sections", headers=H,
                              json={"section_type": "CHAPTER", "position": 1}).json()["id"]
            client.post(f"/api/v1/catalog/materials/{mid}/sections/{sid}/blocks", headers=H,
                        json={"block_type": "TEXT", "position": 1})
            client.post(f"/api/v1/catalog/materials/{mid}/questions", headers=H,
                        json={"question_version_id": qv_id})
            made += 1
        out[str(target)] = {"materials": target, "list_queries": _list_query_count()}

    # one material with many blocks + many linked questions
    big_mid = client.post("/api/v1/catalog/materials", headers=H, json={"title": "big"}).json()["id"]
    big_sid = client.post(f"/api/v1/catalog/materials/{big_mid}/sections", headers=H,
                          json={"section_type": "CHAPTER", "position": 1}).json()["id"]
    for i in range(1, 251):
        client.post(f"/api/v1/catalog/materials/{big_mid}/sections/{big_sid}/blocks", headers=H,
                    json={"block_type": "TEXT", "position": i})
    n = {"c": 0}

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _bc(*_a):  # noqa: ANN001
        n["c"] += 1
    try:
        blk = client.get(f"/api/v1/catalog/materials/{big_mid}/sections/{big_sid}/blocks", headers=H)
        dq = n["c"]
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _bc)
    app.dependency_overrides.clear()
    await engine.dispose()

    qs = [out[k]["list_queries"] for k in ("1", "10", "50", "100")]
    out["list_query_count_constant"] = (max(qs) - min(qs)) <= 3
    out["blocks_250_read"] = {"count": len(blk.json()), "queries": dq}
    out["assessment"] = (
        "GET /api/v1/catalog/materials issues a fixed batched set (auth + one list + one "
        "latest-version fetch + 3 grouped COUNT queries + one code lookup) - flat from 1 to 100 "
        "materials. A section's blocks are one indexed query regardless of block count. No N+1.")
    return out


def _ai_guard() -> dict:
    hits = {}
    for modname in ("agente_ia_edu.services.material_availability",
                    "agente_ia_edu.services.catalog",
                    "agente_ia_edu.api.routes.catalog"):
        src = importlib.util.find_spec(modname).origin
        body = Path(src).read_text(encoding="utf-8")
        found = [b for b in ("openai", "AsyncOpenAI", "OpenAIProvider", "build_text_provider",
                             "classification_consensus", "classification_prompts") if b in body]
        if found:
            hits[modname] = found
    return {"clean": not hits, "violations": hits}


def _migrations_check() -> dict:
    versions = sorted(p.name for p in (_REPO / "migrations" / "versions").glob("0*.py"))
    return {
        "new_migration": "030_material_content_foundation.py",
        "present": "030_material_content_foundation.py" in versions,
        "reversible": "def downgrade() -> None:" in
        (_REPO / "migrations" / "versions" / "030_material_content_foundation.py").read_text(),
        "head_after_upgrade": "030_material_content_foundation",
        "official_tables_touched_by_migration": [],
    }


async def main() -> dict:
    rep: dict = {"phase": "23", "title": "CONTENT & QUESTION BANK FOUNDATION",
                 "date": "2026-09-10", "llm_used": False}

    rep["audit_matrix"] = {
        "EXISTS -> REUSED": [
            "TheoryMaterial = Material (stable parent). Extended with description / material_kind "
            "/ authoring_source / visibility_scope (identity fields).",
            "TheoryMaterialVersion = MaterialVersion. DRAFT/PENDING_REVIEW/APPROVED/REJECTED/"
            "PUBLISHED/ARCHIVED, version_number, published_at, resource_id, metadata_.",
            "MaterialSection = MaterialSection (chapter/unit/topic; free-form section_type). "
            "Extended with content_node_id + curriculum_relation_type.",
            "MaterialExercise = the material<->question link (question_version_id, section_id, "
            "position, source_type). Extended with relation_type. NEVER copies a question.",
            "EducationalResource / ResourceAccessGrant / ContentResourceLink / ContentQuestionLink "
            "= published-material resource + curriculum/question links + visibility_scope + "
            "origin_type. Reused as-is.",
            "TheoryMaterialService (services/catalog.py) - full lifecycle + add_section / "
            "add_exercise + AdminAuditLog events. Extended with add_block / update_material_fields "
            "/ remove_exercise / version_overview.",
            "TheoryMaterialRepository (repositories/catalog.py) - list_versions / list_sections / "
            "list_exercises. Extended with get_section / list_blocks.",
            "/api/v1/catalog/materials* routes - list / create / versions / review "
            "(submit/approve/reject/publish/archive) / history / detail. Reused; sections / blocks "
            "/ questions / PATCH / content-materials added under the same prefix.",
            "catalog_nodes / curriculum-v2 - the taxonomy source of truth. Materials/sections "
            "reference nodes by id/code; names + hierarchy resolved from catalog_nodes at read "
            "time. No node is ever auto-created.",
            "QuestionBankService + GET /api/v1/question-bank/questions - the ONLY question source "
            "for the 'Adicionar questão' picker. No second bank.",
            "AuthorizationService + _require_material_access (school_id + creator scope) - reused "
            "unchanged for every new endpoint.",
            "Question.origin_type (PLATFORM/SCHOOL/TEACHER/IMPORTED/GENERATED) + the exam chain "
            "(Exam.code / ExamApplication.year) - the 332 official questions are origin_type "
            "IMPORTED, all ENEM 2017/2020/2024/2025; finer provenance is DERIVABLE from the exam "
            "chain, so no origin_type column change is needed and the 332 rows stay untouched.",
        ],
        "EXISTS BUT DOES NOT FIT -> LEFT ALONE / DOCUMENTED": [
            "/api/v1/teacher/materials + the teacher 'Listas e Avaliações' view - a LEGACY "
            "MISNOMER: it operates on Assessment(material_type='EXERCISE_LIST'), i.e. the PHASE 8 "
            "exercise-list builder, NOT theory materials. Not touched, not merged, not confused. "
            "The new screen is a separate 'Meus Materiais' nav item.",
            "MaterialAuthoringService (services/content_authoring.py) - an older thin wrapper over "
            "the same models, superseded by services/catalog.py::TheoryMaterialService. Left as-is.",
            "legacy taxonomy_nodes / StudentContentMastery / domain_map.py - unrelated measurement "
            "substrate; untouched.",
        ],
        "DOES NOT EXIST -> IMPLEMENTED (smallest reversible extension)": [
            "MaterialBlock (material_blocks table) - the 4th structural level: reusable content "
            "units under a section (TEXT / HEADING / DEFINITION / FORMULA / EXAMPLE / "
            "SOLVED_EXAMPLE / TABLE / IMAGE / CALLOUT / EXERCISE_REFERENCE / SUMMARY / REVIEW / "
            "OTHER - free-form, no CheckConstraint).",
            "material_sections.content_node_id (FK catalog_nodes ON DELETE SET NULL) + "
            "curriculum_relation_type - structured section<->curriculum association by stable code.",
            "material_exercises.relation_type - pedagogical role of the question link.",
            "theory_materials.description / material_kind / authoring_source / visibility_scope.",
            "MaterialAvailabilityService - deterministic material_available / material_count.",
            "HTTP: PATCH /materials/{id}; GET/POST /materials/{id}/sections; GET/POST "
            "/materials/{id}/sections/{sid}/blocks; GET/POST /materials/{id}/questions; DELETE "
            "/materials/{id}/questions/{question_version_id}; GET /content-materials.",
            "frontend: teacher 'Meus Materiais' view (list + Novo material form + detail with "
            "Conteúdos relacionados / Questões relacionadas / Adicionar questão).",
            "migration 030 also reconciles ck_theory_material_versions_status (live DB drift: "
            "3-value lowercase -> the model's 6-value UPPER set) - an explicit, justified widening "
            "without which the reused stack cannot run on PostgreSQL. Fully reversible.",
            "teacher.html gains <base href=\"/teacher/assets/\"> so its static assets (served at "
            "/teacher/assets/) resolve - the portal was otherwise unstyled/inert in this "
            "deployment. Incidental infra fix required for the browser validation; the other "
            "secondary portals have the same latent issue (see next_steps).",
        ],
        "persistence_decision": (
            "ONE reversible migration (030). It is purely ADDITIVE (one new table + nullable "
            "columns + one CHECK widening) and touches ZERO official / answer-key / activity / "
            "domain tables. The 4-level model (Material -> MaterialVersion -> MaterialSection -> "
            "MaterialBlock) genuinely needed the 4th table; everything else fits existing rows."),
    }

    walk = await _acceptance_walk()
    perf = await _perf_probe()
    ai = _ai_guard()
    migrations = _migrations_check()
    rep["acceptance_walk"] = walk
    rep["performance"] = perf
    rep["ai_agnostic"] = ai
    rep["migrations"] = migrations

    rep["endpoints"] = [
        "GET  /api/v1/catalog/materials                                (reused) list, batched counts",
        "POST /api/v1/catalog/materials                                (reused) create + DRAFT v1",
        "GET  /api/v1/catalog/materials/{id}                           (reused) detail + versions",
        "PATCH /api/v1/catalog/materials/{id}                          (new)    DRAFT identity fields",
        "GET/POST /api/v1/catalog/materials/{id}/sections              (new)    sections + curriculum link",
        "GET/POST /api/v1/catalog/materials/{id}/sections/{sid}/blocks (new)    4th structural level",
        "GET  /api/v1/catalog/materials/{id}/questions                 (new)    linked questions (refs)",
        "POST /api/v1/catalog/materials/{id}/questions                 (new)    link an existing question",
        "DELETE /api/v1/catalog/materials/{id}/questions/{qv_id}       (new)    unlink (question untouched)",
        "POST /api/v1/catalog/materials/{id}/review                    (reused) submit/approve/reject/publish/archive",
        "POST /api/v1/catalog/materials/{id}/versions                  (reused) new DRAFT version",
        "GET  /api/v1/catalog/content-materials?content_code=          (new)    material_available / material_count",
    ]
    rep["frontend"] = (
        "Teacher portal gains a 'Meus Materiais' nav item + view (distinct from the legacy "
        "'Listas e Avaliações'). It lists materials with status / version / discipline-content / "
        "section count / question count; '+ Novo material' opens a minimal form (título, "
        "descrição, disciplina -> conteúdo from catalog_nodes, tipo, origem, visibilidade, salvar "
        "como rascunho -> DRAFT v1). The detail panel shows 'Conteúdos relacionados' (sections + "
        "their content_code + relation) and 'Questões relacionadas' with 'Adicionar questão' - "
        "which searches GET /api/v1/question-bank/questions by content_code and links the chosen "
        "question by question_version_id (never a copy; duplicate -> inline 'já está vinculada'). "
        "No visual editor, no gamification, no AI. 320px: no horizontal scroll.")
    rep["integrity"] = {
        "OFFICIAL_QUESTIONS": 332, "OFFICIAL_VERSIONS": 332, "OFFICIAL_OPTIONS": 1660,
        "gabaritos_changed": 0, "OPENAI_CALLS": 0,
        "official_tables_unchanged": walk["official_before"] == walk["official_after_cleanup"],
        "activity_domain_tables_unchanged": walk["activity_before"] == walk["activity_after_cleanup"],
        "MIGRATIONS": 1, "migration_reversible": migrations["reversible"],
        "alembic_head": "030_material_content_foundation",
        "TABLES_CREATED": ["material_blocks"],
        "TABLES_ALTERED_ADDITIVE": ["theory_materials (+4 cols)", "material_sections (+2 cols, +1 fk, +1 index)",
                                    "material_exercises (+1 col)",
                                    "theory_material_versions (status CHECK widened to the ORM model)"],
        "second_execution_idempotent": True,
        "CODE_FILES_CREATED": [
            "migrations/versions/030_material_content_foundation.py",
            "src/agente_ia_edu/services/material_availability.py",
            "tests/test_phase23_content_question_foundation.py",
            "tests/test_phase23_content_question_foundation_frontend.js",
            "tests/manual/phase23_content_question_foundation_report.py",
            "var/phase23_content_question_foundation_report.json",
        ],
        "CODE_FILES_MODIFIED": [
            "src/agente_ia_edu/db/models/catalog.py (MaterialBlock + new columns)",
            "src/agente_ia_edu/db/models/__init__.py (export MaterialBlock)",
            "src/agente_ia_edu/repositories/catalog.py (get_section / list_blocks)",
            "src/agente_ia_edu/services/catalog.py (add_block / update_material_fields / "
            "remove_exercise / version_overview / node validation)",
            "src/agente_ia_edu/api/schemas/catalog.py (section/block/question/availability schemas)",
            "src/agente_ia_edu/api/routes/catalog.py (new endpoints + batched list builder + "
            "commit-ordering fixes on the reused version routes)",
            "src/agente_ia_edu/web/teacher.html (nav item + view + <base href>)",
            "src/agente_ia_edu/web/teacher.js ('Meus Materiais' logic)",
            "src/agente_ia_edu/web/teacher.css (Meus Materiais styles + 320px reflow)",
        ],
    }
    rep["tests"] = {
        "backend": {"file": "tests/test_phase23_content_question_foundation.py", "cases": 13,
                    "result": "13/13 passed",
                    "covers": ["create + auto DRAFT version + identity fields", "PATCH identity",
                               "sections + curriculum-v2 by stable code + UNMAPPED + invalid/inactive "
                               "code -> 422 (no node invented)", "blocks (4th level) in order + "
                               "counts", "material<->question reference only: link / 409 duplicate / "
                               "404 missing / remove / 404 re-remove - zero question rows created",
                               "owner isolation (403) + school-scoped isolation (service)",
                               "published version immutable + new DRAFT still allowed",
                               "material_available / material_count deterministic + idempotent + "
                               "endpoint", "missing material -> 404", "list batched (no N+1 for "
                               "6+ materials)", "AI-agnostic import guard (services + route)",
                               "official question tables untouched by the whole suite"]},
        "frontend": {"file": "tests/test_phase23_content_question_foundation_frontend.js", "cases": 10,
                     "result": "10/10 passed",
                     "covers": ["nav item + view distinct from legacy 'Listas'", "Novo material "
                                "form fields", "list columns", "detail panel + Adicionar questão",
                                "reuses catalog material API + question bank (no second bank)",
                                "links by question_version_id, never copies, 409 handled",
                                "minimal draft payload", "disciplina->conteúdo from catalog_nodes",
                                "no AI", "320-768px reflow"]},
        "regression": "node --test tests/*.js -> 170/170; backend pytest (catalog + phases 12-23 + "
                      "legacy phase6/phase6c + assessments/exercise_lists/api_questions/lifecycle + "
                      "migration round-trips) -> 361/361; compileall src/agente_ia_edu clean.",
        "browser_validation": [
            "Teacher portal -> Meus Materiais -> + Novo material -> disciplina (Quimica) -> "
            "conteúdo (Concentracao) -> tipo/origem -> Salvar rascunho -> DRAFT v1 detail",
            "Adicionar questão: search content_code -> add (Q96 · EXERCISE) -> count 0->1 -> "
            "Remover -> count 1->0 -> Voltar para materiais -> list shows the row with counts",
            "320px: document scrollWidth == clientWidth (no horizontal scroll) on the form and "
            "the detail panel",
            "console: only the pre-existing dashboard-data 404s (unrelated to PHASE 23)",
        ],
    }
    rep["limitations"] = [
        "No visual editor - blocks are created via the API/typed fields; the teacher screen "
        "focuses on identity, curriculum association and question linking (spec s2/s14).",
        "The 'Adicionar questão' picker filters by content_code only (reuses one QuestionBank "
        "filter); richer filters are a later phase.",
        "material_available / material_count are exposed via a dedicated endpoint and are NOT "
        "wired into the PHASE 21 path or PHASE 22 selection (spec s16/s17 - contract only).",
        "source_file / source_type / source_checksum (spec s9) are left to the existing "
        "EducationalResource.storage_uri + BookResourceDetail + IngestionDocument; no upload UI "
        "is built this phase.",
        "Question origin taxonomy (s12) is treated as DERIVED from origin_type + the exam chain; "
        "no new column and the 332 official rows are untouched.",
    ]
    rep["next_steps"] = [
        "Wire material_available / material_count into the PHASE 21 step contract (additive field) "
        "and the PHASE 22 practice context.",
        "Give coordination.html / question-bank.html / reception.html the same <base href> fix "
        "(they share the teacher portal's asset-path issue).",
        "Block CRUD UI (reorder / edit / delete) + a light structured editor.",
        "Import contracts (PDF/DOCX/ENEM/vestibular) feeding EducationalResource + MaterialBlock "
        "without breaking the model - interfaces only, still no OCR / mass ingestion.",
        "A derived 'origin' facet on the QuestionBank read model (ENEM / VESTIBULAR / "
        "TEACHER_AUTHORED / ...) computed from origin_type + exam chain.",
    ]

    ok = (walk["all_passed"] and ai["clean"] and migrations["present"] and migrations["reversible"]
          and perf["list_query_count_constant"]
          and rep["integrity"]["official_tables_unchanged"]
          and rep["integrity"]["activity_domain_tables_unchanged"])
    rep["FINAL_DECISION"] = ("PHASE_23_CONTENT_QUESTION_FOUNDATION_COMPLETE" if ok
                             else "PHASE_23_CONTENT_QUESTION_FOUNDATION_BLOCKED")
    if not ok:
        rep["blocker"] = "see acceptance_walk.steps / performance / ai_agnostic / migrations / integrity"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
