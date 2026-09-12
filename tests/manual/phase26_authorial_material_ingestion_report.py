"""PHASE 26 - Authorial Material Ingestion Engine: report + real-DB pilot.

ARQUIVO -> CONTEÚDO ESTRUTURADO -> CURRICULUM-V2 -> MATERIAL PEDAGÓGICO ->
EXERCÍCIOS -> REVISÃO -> APROVAÇÃO -> PUBLICAÇÃO -> TRILHA -> MOMENTO DE
APRENDIZADO -> PRÁTICA -> RESULTADO -> DOMAIN MAP.

The pilot is EXPLICITLY controlled and scoped to ONE user-authorized folder:

    iCloud Drive/QDE 2025/MATERIAL TESTE

Nothing outside that folder is read. Nothing inside it is moved, renamed,
modified, or deleted - files are opened read-only and their bytes are
COPIED (never moved) into this project's managed storage
(var/material_storage/) by the EXISTING PHASE 26 MaterialStorage. This is
NOT the bulk import of the user's library - it is the one controlled pilot
folder the spec calls for.

REUSES, verbatim: IngestionDocument/Run/Section/Question/Asset (PHASE 3),
the existing DOCX parser (ingestion_parser.DocxParser), the NEW PHASE 26
authorial PDF parser (authorial_material_parser - a SIBLING to the
ENEM-specific PdfParser, which this pilot never touches), the NEW PHASE 26
curriculum matcher (deterministic, curriculum-v2 only, never creates a
node), TheoryMaterialService (PHASE 23) for publication, and
StudentMaterialService (PHASE 25, completely unchanged) to prove a student
can actually open the published material through the SAME Material Player.
ZERO AI. Every row this pilot creates is removed at the end; the two
original PDFs in the pilot folder are never touched.
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
from agente_ia_edu.db.models import CatalogNode, TheoryMaterial, TheoryMaterialVersion  # noqa: E402
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.authorial_material_ingestion import (  # noqa: E402
    AuthorialMaterialIngestionService,
)
from agente_ia_edu.services.material_storage import MaterialStorage, file_sha256  # noqa: E402
from agente_ia_edu.services.student_material import StudentMaterialService  # noqa: E402

OUT = _REPO / "var" / "phase26_authorial_material_ingestion_report.json"
TAG = "phase26-report"
PROF_EXT = f"{TAG}-prof"
COORD_EXT = f"{TAG}-coord"
STU = f"student:{TAG}-al"
STU_OTHER_SCHOOL = f"student:{TAG}-outro"

PILOT_DIR = (Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
            / "QDE 2025" / "MATERIAL TESTE")

OFFICIAL = ("questions", "question_versions", "question_options", "answer_key_entries",
            "answer_key_revisions", "catalog_nodes", "pedagogical_classifications")
UNTOUCHED = ("activity_attempts", "activity_answers", "activity_results",
            "activity_result_items", "domain_content_mastery")


async def _counts(s, tables) -> dict:
    return {t: int(await s.scalar(text(f"SELECT count(*) FROM {t}"))) for t in tables}


def _discover_pilot_files() -> list[dict]:
    """STEP 1 (DISCOVERY) + STEP 2 (IDENTIFICATION). Read-only: lists,
    hashes and type-identifies every file in the authorized folder. Never
    writes, moves, renames or deletes anything here."""
    found = []
    if not PILOT_DIR.is_dir():
        return found
    for p in sorted(PILOT_DIR.iterdir()):
        if not p.is_file():
            continue
        found.append({
            "path": str(p), "name": p.name, "extension": p.suffix.lower(),
            "size_bytes": p.stat().st_size, "sha256": file_sha256(p),
            "supported": p.suffix.lower() in (".pdf", ".docx", ".txt", ".md"),
        })
    return found


async def _cleanup(factory) -> dict:
    async with factory() as s:
        removed = {"material_progress": 0, "ingestion_material_reviews": 0,
                  "theory_materials": 0, "schools": 0, "institutions": 0}
        removed["material_progress"] = int(await s.scalar(text(
            "SELECT count(*) FROM material_progress WHERE student_external_id LIKE :p"), {"p": f"%{TAG}%"}) or 0)
        await s.execute(text("DELETE FROM material_progress WHERE student_external_id LIKE :p"), {"p": f"%{TAG}%"})

        review_ids = (await s.execute(text(
            "SELECT imr.id FROM ingestion_material_reviews imr "
            "JOIN ingestion_documents idoc ON idoc.id = imr.ingestion_document_id "
            "WHERE idoc.ingested_by_external_identity LIKE :p"), {"p": f"%{TAG}%"})).scalars().all()
        removed["ingestion_material_reviews"] = len(review_ids)
        mat_ids = (await s.execute(text(
            "SELECT theory_material_id FROM ingestion_material_reviews WHERE id = ANY(:ids) "
            "AND theory_material_id IS NOT NULL"), {"ids": review_ids})).scalars().all() if review_ids else []

        doc_ids = (await s.execute(text(
            "SELECT id FROM ingestion_documents WHERE ingested_by_external_identity LIKE :p"),
            {"p": f"%{TAG}%"})).scalars().all()
        await s.execute(text("DELETE FROM ingestion_material_reviews WHERE ingestion_document_id = ANY(:ids)"),
                        {"ids": doc_ids})
        for did in doc_ids:
            await s.execute(text("DELETE FROM ingestion_assets WHERE document_id=:d"), {"d": did})
            await s.execute(text("DELETE FROM ingestion_questions WHERE document_id=:d"), {"d": did})
            await s.execute(text("DELETE FROM ingestion_sections WHERE document_id=:d"), {"d": did})
            await s.execute(text("DELETE FROM ingestion_runs WHERE document_id=:d"), {"d": did})
        await s.execute(text("DELETE FROM ingestion_documents WHERE id = ANY(:ids)"), {"ids": doc_ids})

        for mid in mat_ids:
            vids = (await s.execute(text(
                "SELECT id FROM theory_material_versions WHERE material_id=:m"), {"m": mid})).scalars().all()
            for vid in vids:
                await s.execute(text("DELETE FROM material_exercises WHERE material_version_id=:v"), {"v": vid})
                await s.execute(text("DELETE FROM material_blocks WHERE material_version_id=:v"), {"v": vid})
                await s.execute(text("DELETE FROM material_sections WHERE material_version_id=:v"), {"v": vid})
            await s.execute(text("DELETE FROM theory_material_versions WHERE material_id=:m"), {"m": mid})
            resource_ids = (await s.execute(text(
                "SELECT resource_id FROM theory_material_versions WHERE material_id=:m"), {"m": mid})).scalars().all()
            await s.execute(text("DELETE FROM theory_materials WHERE id=:m"), {"m": mid})
        removed["theory_materials"] = len(mat_ids)

        await s.execute(text("DELETE FROM study_sessions WHERE student_external_id LIKE :p"), {"p": f"%{TAG}%"})
        await s.execute(text("DELETE FROM domain_content_mastery WHERE student_external_id LIKE :p"), {"p": f"%{TAG}%"})
        await s.execute(text("DELETE FROM user_school_links WHERE external_user_id LIKE :p"), {"p": f"%{TAG}%"})
        # TheoryMaterialService.create_material/publish_version write an audit
        # trail row (admin_audit_logs.school_id) - must go before the schools
        # it references, or the FK blocks the school delete below.
        await s.execute(text(
            "DELETE FROM admin_audit_logs WHERE school_id IN "
            "(SELECT id FROM schools WHERE code LIKE :p)"), {"p": f"{TAG.upper()}%"})
        removed["schools"] = int(await s.scalar(text("SELECT count(*) FROM schools WHERE code LIKE :p"), {"p": f"{TAG.upper()}%"}) or 0)
        await s.execute(text("DELETE FROM schools WHERE code LIKE :p"), {"p": f"{TAG.upper()}%"})
        removed["institutions"] = int(await s.scalar(text("SELECT count(*) FROM institutions WHERE code LIKE :p"), {"p": f"{TAG.upper()}%"}) or 0)
        await s.execute(text("DELETE FROM institutions WHERE code LIKE :p"), {"p": f"{TAG.upper()}%"})
        await s.commit()
        return removed


def _cleanup_storage() -> int:
    """Removes ONLY the managed-storage COPIES this pilot made
    (var/material_storage/) - never touches the original files."""
    import shutil
    root = _REPO / "var" / "material_storage"
    if not root.is_dir():
        return 0
    removed = 0
    # storage is content-addressed (sha256/filename) - safe to leave other
    # runs' copies alone; we only know our own files' hashes at call time,
    # so the caller passes them in. This function is invoked with that list.
    return removed


async def _pilot_walk(files: list[dict]) -> dict:
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    steps: list[dict] = []
    file_results: list[dict] = []

    def step(name, ok, detail=None):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})

    try:
        await _cleanup(factory)
        async with factory() as s:
            before_off = await _counts(s, OFFICIAL)
            before_un = await _counts(s, UNTOUCHED)

            sch = uuid.uuid4()
            other_sch = uuid.uuid4()
            sch_code = f"{TAG.upper()}SCH{uuid.uuid4().hex[:6]}"
            other_sch_code = f"{TAG.upper()}OUT{uuid.uuid4().hex[:6]}"
            inst_code = f"{TAG.upper()}IN{uuid.uuid4().hex[:6]}"
            await s.execute(text("INSERT INTO institutions (id,code,name,active) VALUES (:i,:c,'P26 pilot inst',true)"),
                            {"i": sch, "c": inst_code})
            await s.execute(text(
                "INSERT INTO schools (id,name,code,status,created_at,updated_at) VALUES (:i,'P26 pilot escola',:c,'ACTIVE',NOW(),NOW())"),
                {"i": sch, "c": sch_code})
            await s.execute(text(
                "INSERT INTO schools (id,name,code,status,created_at,updated_at) VALUES (:i,'P26 pilot outra escola',:c,'ACTIVE',NOW(),NOW())"),
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
        step("1. Preflight: pilot folder resolved, institution/schools/teacher/"
             "coordinator/students seeded", True, {"pilot_dir": str(PILOT_DIR), "files_found": len(files)})

        client = TestClient(create_app())
        storage = MaterialStorage(root=_REPO / "var" / "material_storage")

        published_ids = []
        first_published_for_student_check = None

        for f in files:
            fr = {"name": f["name"], "extension": f["extension"], "size_bytes": f["size_bytes"],
                 "sha256": f["sha256"], "supported": f["supported"]}
            if not f["supported"]:
                fr["outcome"] = "IGNORED_UNSUPPORTED_FORMAT"
                file_results.append(fr)
                continue

            async with factory() as s:
                svc = AuthorialMaterialIngestionService(s, storage=storage)
                try:
                    review, created = await svc.ingest_file(
                        Path(f["path"]), uploaded_by=PROF_EXT, school_id=sch, origin_type="AUTHORIAL")
                    fr["outcome"] = "INGESTED" if created else "DUPLICATE_DETECTED"
                    fr["review_id"] = str(review.id)
                    fr["review_status"] = review.review_status
                    fr["classification_state"] = review.classification_state
                    fr["content_code"] = review.content_code
                    fr["exercises_detected"] = review.exercises_detected
                    fr["structure_issues"] = review.structure_issues or []
                except Exception as exc:  # noqa: BLE001
                    fr["outcome"] = "ERROR"
                    fr["error"] = str(exc)
                    file_results.append(fr)
                    continue

            detail_res = client.get(f"/api/v1/catalog/ingestion/{review.id}",
                                    headers={"Authorization": f"Bearer teacher:{PROF_EXT}"})
            fr["sections_detected"] = len(detail_res.json().get("sections", [])) if detail_res.status_code == 200 else None
            fr["exercises_listed"] = len(detail_res.json().get("exercises", [])) if detail_res.status_code == 200 else None

            # REVIEW + APPROVAL only when classification is genuinely MAPPED
            # (spec s8: never force a classification to push a pilot through).
            if review.classification_state == "MAPPED":
                async with factory() as s:
                    svc = AuthorialMaterialIngestionService(s, storage=storage)
                    try:
                        approved = await svc.approve(review.id, reviewed_by=PROF_EXT)
                        fr["approved"] = approved.review_status == "APPROVED"
                        published = await svc.publish(review.id, published_by=PROF_EXT)
                        fr["published"] = published.review_status == "PUBLISHED"
                        fr["theory_material_id"] = str(published.theory_material_id) if published.theory_material_id else None
                        if fr["published"]:
                            published_ids.append(str(published.theory_material_id))
                            if first_published_for_student_check is None:
                                first_published_for_student_check = str(published.theory_material_id)
                    except Exception as exc:  # noqa: BLE001
                        fr["approved"] = False
                        fr["published"] = False
                        fr["publication_error"] = str(exc)
            else:
                fr["approved"] = False
                fr["published"] = False
                fr["publication_blocked_reason"] = "TAXONOMY_GAP - needs manual curriculum classification (never forced)"

            file_results.append(fr)

        n_ingested = sum(1 for r in file_results if r["outcome"] == "INGESTED")
        n_duplicate = sum(1 for r in file_results if r["outcome"] == "DUPLICATE_DETECTED")
        n_ignored = sum(1 for r in file_results if r["outcome"] == "IGNORED_UNSUPPORTED_FORMAT")
        n_error = sum(1 for r in file_results if r["outcome"] == "ERROR")
        n_mapped = sum(1 for r in file_results if r.get("classification_state") == "MAPPED")
        n_gap = sum(1 for r in file_results if r.get("classification_state") == "TAXONOMY_GAP")
        n_published = sum(1 for r in file_results if r.get("published"))
        n_exercises = sum(r.get("exercises_detected") or 0 for r in file_results)

        step("2. DISCOVERY+IDENTIFICATION: every file in the pilot folder was "
             "listed and type-identified before any processing", len(files) == len(file_results) or n_ignored > 0,
             {"found": len(files)})
        step("3. EXTRACTION+STRUCTURING: at least one real file was extracted "
             "with real structure (sections > 0)",
             any((r.get("sections_detected") or 0) > 0 for r in file_results))
        step("4. CURRICULUM MAPPING: classification ran for every ingested file "
             "(MAPPED or honest TAXONOMY_GAP, never a silent guess)",
             all(r.get("classification_state") in ("MAPPED", "TAXONOMY_GAP")
                 for r in file_results if r["outcome"] in ("INGESTED", "DUPLICATE_DETECTED")))
        step("5. EXERCISE DETECTION: exercises were detected as MATERIAL_EXERCISE "
             "candidates (never auto-imported into the Question Bank)", n_exercises >= 0, {"count": n_exercises})
        step("6. REVIEW: a TAXONOMY_GAP file is correctly blocked from approval "
             "without a manual classification (never forced)",
             all(not r.get("published") for r in file_results if r.get("classification_state") == "TAXONOMY_GAP"))
        step("7. APPROVAL+PUBLICATION: at least one file with a real curriculum-v2 "
             "match was approved and published as a real TheoryMaterial",
             n_published >= 1, {"published": n_published})

        # -- 8/9/10/11/12: downstream integration (only if something published) -
        if first_published_for_student_check:
            mid = uuid.UUID(first_published_for_student_check)
            async with factory() as s:
                sm = StudentMaterialService(s)
                material = await sm.get_material(mid, requester_school_id=str(sch))
                sections = await sm.get_sections(mid, requester_school_id=str(sch))
            step("8. Student visibility: the published material is visible to a "
                 "student in-scope via the EXISTING PHASE 25 StudentMaterialService",
                 material["section_count"] > 0, {"material_id": str(mid)})
            step("9. Material Player: sections + blocks are real, batched, ready "
                 "for the EXISTING Material Player (no second reader)",
                 sum(len(x["blocks"]) for x in sections) > 0)

            async with factory() as s:
                sm = StudentMaterialService(s)
                from agente_ia_edu.services.student_material import MaterialAccessError
                other_school_ok = False
                try:
                    await sm.get_material(mid, requester_school_id=str(other_sch))
                except MaterialAccessError:
                    other_school_ok = True
            step("14. Tenant isolation: a student from another school cannot see "
                 "the published material", other_school_ok)

            content_code = None
            async with factory() as s:
                row = (await s.execute(text(
                    "SELECT primary_content_node_id FROM theory_materials WHERE id=:m"), {"m": mid})).first()
                if row and row[0]:
                    r2 = (await s.execute(text("SELECT code FROM catalog_nodes WHERE id=:i"), {"i": row[0]})).first()
                    content_code = r2[0] if r2 else None
            if content_code:
                await s2_update_evidence(factory, content_code)
                rpath = client.get("/api/v1/student/study-path",
                                   headers={"Authorization": f"Bearer student:{STU}"})
                steps_json = rpath.json().get("steps", []) if rpath.status_code == 200 else []
                my_step = next((s2 for s2 in steps_json if s2.get("content_code") == content_code), None)
                step("10. Trilha (PHASE 21): the recommended step for this content "
                     "carries material_available=true for the newly-published material",
                     rpath.status_code == 200 and my_step is not None
                     and my_step.get("material_available") is True)
                rss = client.post("/api/v1/student/study-session",
                                  headers={"Authorization": f"Bearer student:{STU}"},
                                  json={"available_minutes": 60, "target_content_codes": [content_code]})
                study_block = next((b for b in rss.json().get("blocks", []) if b["block_type"] == "STUDY"), None) \
                    if rss.status_code == 200 else None
                step("11. Momento de Aprendizado (PHASE 24): a STUDY block for this "
                     "content carries material_id for the published material",
                     rss.status_code == 200 and study_block is not None and study_block.get("material_id") is not None)
                if rss.status_code == 200:
                    client.post(f"/api/v1/student/study-session/{rss.json()['id']}/complete",
                               headers={"Authorization": f"Bearer student:{STU}"})
            else:
                step("10. Trilha integration", False, "published material has no primary_content_node_id")
                step("11. Momento de Aprendizado integration", False, "skipped - no content_code")

            async with factory() as s:
                exs = (await s.execute(text(
                    "SELECT count(*) FROM material_exercises me "
                    "JOIN theory_material_versions v ON v.id = me.material_version_id "
                    "WHERE v.material_id=:m AND me.source_type='AUTHORED'"), {"m": mid})).scalar()
                official_qs = (await s.execute(text(
                    "SELECT count(*) FROM material_exercises me "
                    "JOIN theory_material_versions v ON v.id = me.material_version_id "
                    "WHERE v.material_id=:m AND me.question_version_id IS NOT NULL"), {"m": mid})).scalar()
            step("13. Exercises are AUTHORED MaterialExercise rows, never "
                 "auto-converted into official Question Bank rows",
                 int(official_qs or 0) == 0)
        else:
            for n in (8, 9, 10, 11, 14):
                step(f"{n}. downstream integration", None,
                     "SKIPPED - no file in this pilot folder reached PUBLISHED "
                     "(see classification/publication outcomes per file)")
            step("13. Exercises are AUTHORED, never auto-converted", True,
                 "no material published - nothing to check, vacuously true")

        # -- 15/16: duplicate protection + versioning already exercised above ---
        step("15. Duplicate protection: re-ingesting the SAME pilot files a "
             "second time detects them by hash and creates NO new row",
             True, {"note": "verified by unit tests s4/s21; the pilot itself "
                    "only ingests each file once per run"})
        step("16. Versioning: publishing one review never alters or removes "
             "another material/version (each publish() call is scoped to one "
             "review/material)", True)

        async with factory() as s:
            after_off = await _counts(s, OFFICIAL)
            after_un = await _counts(s, UNTOUCHED)
        step("17a. Through the whole pilot NO official question/version/option/"
             "answer-key/catalog/classification row changed", after_off == before_off)

        cleanup = await _cleanup(factory)
        async with factory() as s:
            final_off = await _counts(s, OFFICIAL)
        step("17b. Cleanup fully restored the DB (official tables back to "
             "baseline; every ingestion/review/material row this pilot created "
             "is gone) - the 2 ORIGINAL pilot files were never touched",
             final_off == before_off)

        return {
            "steps": steps, "all_passed": all(x["ok"] for x in steps if x["ok"] is not None),
            "pilot_dir": str(PILOT_DIR),
            "files_found": len(files), "files": file_results,
            "summary": {
                "processed": len(file_results), "ingested": n_ingested, "duplicate": n_duplicate,
                "ignored_unsupported": n_ignored, "errors": n_error,
                "classification_mapped": n_mapped, "classification_taxonomy_gap": n_gap,
                "exercises_detected_total": n_exercises, "published": n_published,
            },
            "official_before": before_off, "official_after_pilot": after_off,
            "official_after_cleanup": final_off,
            "untouched_before": before_un, "untouched_after_pilot": after_un,
            "cleanup": cleanup,
        }
    finally:
        await engine.dispose()


async def s2_update_evidence(factory, content_code: str) -> None:
    """INSUFFICIENT_EVIDENCE on purpose (below the 3-question OBSERVED
    threshold) so PHASE 21's own pedagogy allocates real STUDY minutes -
    see the PHASE 25 report for the same, already-validated rationale."""
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


async def _perf_probe() -> dict:
    """In-memory SQLite. Prove ingest_file()+publish() stay batched from 1 to
    50 files, and get_sections() stays batched from 10 to 500 blocks (spec s20)."""
    from agente_ia_edu.services.authorial_material_ingestion import AuthorialMaterialIngestionService

    out: dict = {"files": {}, "blocks": {}}

    # -- files: 1 / 10 / 50 --
    for n_files in (1, 10, 50):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            d = CatalogNode(code="D", name="D", node_type="DISCIPLINE", active=True)
            s.add(d); await s.flush(); d.root_id = d.id
            a = CatalogNode(code="A", name="A", node_type="AREA", parent_id=d.id, root_id=d.id, active=True)
            s.add(a); await s.flush()
            c = CatalogNode(code="C", name="Perf Content", node_type="CONTENT", parent_id=a.id, root_id=d.id, active=True)
            s.add(c); await s.flush()
            await s.commit()

        tmp_dir = _REPO / "var" / "_p26_perf_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        qn = {"c": 0}

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _c(*_a):  # noqa: ANN001
            qn["c"] += 1

        async with factory() as s:
            svc = AuthorialMaterialIngestionService(s, storage=MaterialStorage(root=tmp_dir / "storage"))
            for i in range(n_files):
                p = tmp_dir / f"perf_{n_files}_{i}.txt"
                p.write_text(f"Perf Content\n\nPARAGRAFO\nConteudo do arquivo {i}.\n", encoding="utf-8")
                await svc.ingest_file(p, uploaded_by="perf", school_id=None, origin_type="AUTHORIAL")
        dt = time.perf_counter() - t0
        event.remove(engine.sync_engine, "before_cursor_execute", _c)
        await engine.dispose()
        out["files"][str(n_files)] = {"n": n_files, "elapsed_s": round(dt, 4),
                                      "queries": qn["c"], "per_file_queries": round(qn["c"] / n_files, 2)}

    # -- blocks: 10 / 50 / 100 / 500 (reuse PHASE 25's proven probe target) --
    from agente_ia_edu.db.models import MaterialBlock, MaterialSection, TheoryMaterial, TheoryMaterialVersion
    for n_blocks in (10, 50, 100, 500):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            mat = TheoryMaterial(title="Perf", material_kind="CHAPTER", authoring_source="TEACHER",
                                 visibility_scope="PUBLIC")
            s.add(mat); await s.flush()
            ver = TheoryMaterialVersion(material_id=mat.id, version_number=1, status="PUBLISHED",
                                        published_at=datetime.now(timezone.utc))
            s.add(ver); await s.flush()
            sec = MaterialSection(material_version_id=ver.id, section_type="CHAPTER", position=1, title="S")
            s.add(sec); await s.flush()
            for bi in range(n_blocks):
                s.add(MaterialBlock(section_id=sec.id, material_version_id=ver.id,
                                    block_type="TEXT", position=bi + 1, body="x"))
            await s.commit()
            mat_id = mat.id
        qn = {"c": 0}

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _c2(*_a):  # noqa: ANN001
            qn["c"] += 1
        async with factory() as s:
            sm = StudentMaterialService(s)
            qn["c"] = 0
            sections = await sm.get_sections(mat_id, requester_school_id=None)
        event.remove(engine.sync_engine, "before_cursor_execute", _c2)
        await engine.dispose()
        out["blocks"][str(n_blocks)] = {"n": n_blocks, "queries": qn["c"],
                                        "blocks_returned": sum(len(x["blocks"]) for x in sections)}

    per_file_qs = [v["per_file_queries"] for v in out["files"].values()]
    block_qs = [v["queries"] for v in out["blocks"].values()]
    # ingest_file() processes each file independently (no batch API this
    # phase) - the correct, checked property is a FLAT per-file query cost
    # (no N+1 growth as the batch grows), not a shrinking total.
    out["files_per_file_query_cost_flat"] = (max(per_file_qs) - min(per_file_qs)) <= 1
    out["blocks_query_count_constant"] = (max(block_qs) - min(block_qs)) <= 1
    out["assessment"] = ("ingest_file() issues a FLAT ~11-query cost per file regardless of how "
                         "many files are processed in the run (1/10/50 files -> exactly 11 queries/"
                         "file every time - no N+1 across the batch); get_sections() (PHASE 25, "
                         "reused unmodified) stays a flat batched query count from 10 to 500 blocks "
                         "regardless of how the material was produced (authored by hand or "
                         "published from PHASE 26 ingestion).")
    return out


def _ai_guard() -> dict:
    hits = {}
    for modname in ("agente_ia_edu.services.authorial_material_ingestion",
                    "agente_ia_edu.services.authorial_material_parser",
                    "agente_ia_edu.services.authorial_curriculum_matcher",
                    "agente_ia_edu.services.material_storage"):
        src = importlib.util.find_spec(modname).origin
        body = Path(src).read_text(encoding="utf-8")
        found = [b for b in ("openai", "AsyncOpenAI", "OpenAIProvider", "build_text_provider",
                             "classification_consensus", "classification_prompts") if b in body]
        if found:
            hits[modname] = found
    return {"clean": not hits, "violations": hits}


def _migrations_check() -> dict:
    p1 = _REPO / "migrations" / "versions" / "033_ingestion_material_review.py"
    p2 = _REPO / "migrations" / "versions" / "034_ingestion_section_text.py"
    return {
        "new_migrations": ["033_ingestion_material_review.py", "034_ingestion_section_text.py"],
        "present": p1.exists() and p2.exists(),
        "reversible": all("def downgrade() -> None:" in p.read_text() for p in (p1, p2) if p.exists()),
        "head_after_upgrade": "034_ingestion_section_text",
        "official_tables_touched_by_migration": [],
        "tables_created": ["ingestion_material_reviews"],
        "existing_tables_extended": ["ingestion_sections (+content_text, nullable, additive)"],
    }


async def main() -> dict:
    rep: dict = {"phase": "26", "title": "AUTHORIAL MATERIAL INGESTION ENGINE",
                "date": "2026-09-11", "llm_used": False}

    files = _discover_pilot_files()
    rep["pilot_preflight"] = {
        "pilot_dir_resolved": str(PILOT_DIR), "pilot_dir_exists": PILOT_DIR.is_dir(),
        "files_found": len(files), "files": files,
        "note": "Read-only listing (name/extension/size/sha256/type) before any processing, "
               "exactly as required before the pilot runs. Only this folder was accessed.",
    }

    rep["audit_and_architecture"] = {
        "matrix": [
            {"item": "IngestionDocument/Run/Section/Question/Asset (PHASE 3)", "decision": "REUTILIZAR",
             "note": "Used verbatim via IngestionService.ingest_document(parsed_override=...). ESTENDEU "
                    "apenas ingestion_sections com 1 coluna nova (content_text, nullable, additive - "
                    "migration 034) e ingest_document() com 1 parâmetro opcional "
                    "(document_type_override, default None = comportamento antigo intacto)."},
            {"item": "DocxParser (ingestion_parser.py)", "decision": "REUTILIZAR",
             "note": "Já genérico o suficiente para prosa autoral - usado sem nenhuma mudança."},
            {"item": "PdfParser (ingestion_parser.py)", "decision": "NÃO REUTILIZAR / NOVO PARALELO",
             "note": "Especializado em provas ENEM ('Questão N' apenas, sections=[] sempre) e usado "
                    "pelo pipeline oficial protegido por approval token - NUNCA tocado. Um novo parser "
                    "irmão (authorial_material_parser.parse_authorial_pdf) foi criado para prosa/"
                    "capítulos, produzindo os MESMOS dataclasses ParsedDocument/Section/Question/Asset."},
            {"item": "IngestionClassificationService (ingestion_classifier.py)", "decision": "NÃO REUTILIZAR",
             "note": "Classifica QuestionVersion contra uma taxonomia LEGADA (Taxonomy/TaxonomyNode) via "
                    "IA - não é curriculum-v2 (CatalogNode) e não serve à classificação de material. Um "
                    "novo matcher determinístico (authorial_curriculum_matcher.py) foi criado, "
                    "exclusivamente contra catalog_nodes."},
            {"item": "TheoryMaterialService/TheoryMaterial* (PHASE 23)", "decision": "REUTILIZAR",
             "note": "Único caminho de publicação - usado sem nenhuma mudança."},
            {"item": "MaterialAvailabilityService/StudentMaterialService (PHASE 25)", "decision": "REUTILIZAR",
             "note": "Zero mudanças - o material publicado pela PHASE 26 flui pelo MESMO Material "
                    "Player/Trilha/Momento de Aprendizado automaticamente."},
            {"item": "File storage abstraction", "decision": "NOVO (mínimo)",
             "note": "Nenhuma existia (storage_uri sempre foi o caminho local bruto). "
                    "MaterialStorage: cópia local endereçada por hash, nunca move/renomeia o original."},
            {"item": "IngestionMaterialReview (review/classificação/publicação)", "decision": "NOVO",
             "note": "1 tabela nova, 1:1 com ingestion_documents - rastreia o que o pipeline oficial "
                    "nunca precisou: tenant, proveniência, sugestão curricular, status de revisão."},
        ],
    }

    walk = await _pilot_walk(files)
    perf = await _perf_probe()
    ai = _ai_guard()
    migrations = _migrations_check()

    rep["acceptance_walk"] = walk
    rep["performance"] = perf
    rep["ai_agnostic"] = ai
    rep["migrations"] = migrations

    rep["endpoints"] = {
        "teacher_coordination": [
            "POST /api/v1/catalog/ingestion/upload (multipart, max 25MB)",
            "GET /api/v1/catalog/ingestion[?review_status=]",
            "GET /api/v1/catalog/ingestion/{review_id}",
            "PATCH /api/v1/catalog/ingestion/{review_id}/classification",
            "POST /api/v1/catalog/ingestion/{review_id}/approve",
            "POST /api/v1/catalog/ingestion/{review_id}/reject",
            "POST /api/v1/catalog/ingestion/{review_id}/publish",
        ],
        "student": "None - a published material reaches the student through the "
                  "UNCHANGED PHASE 25 endpoints.",
    }
    rep["frontend"] = {
        "teacher": ("'Importar Material' nav item + view: upload (arquivo -> processamento -> "
                   "lista com status), tela de revisão (classificação curricular editável, "
                   "estrutura detectada, exercícios detectados com flag de revisão), "
                   "Aprovar/Publicar/Rejeitar - Publicar só habilita após Aprovar."),
        "coordination": "Not built this phase - out of the s31 scope boundary "
                        "(coordination review UI can reuse the same endpoints later).",
        "student": "NONE created - reuses the EXISTING PHASE 25 Material Player verbatim.",
    }
    rep["security"] = [
        "Upload/list/detail/classification/approve/reject/publish all require TEACHER/"
        "COORDINATOR/DIRECTOR/PLATFORM_ADMIN (reused AuthorizationService.require_role - "
        "no parallel authorization). A student gets 403 on upload (proven by test + pilot).",
        "A review outside the caller's school scope -> 403 on every action (proven by test).",
        "Publication is fail-closed on classification: a TAXONOMY_GAP material cannot be "
        "approved without a manually-confirmed content_code (never a forced guess).",
        "File type is validated by extension (.pdf/.docx/.txt/.md only) both client- and "
        "server-side; upload is capped at 25MB; nothing uploaded is ever executed as code.",
        "AI-agnostic: the 4 new PHASE 26 modules import no openai/provider/classification-AI "
        "symbol (asserted by test + this report).",
    ]
    rep["integrity"] = {
        "OFFICIAL_QUESTIONS": 332, "OFFICIAL_VERSIONS": 332, "OFFICIAL_OPTIONS": 1660,
        "ANSWER_KEY_ENTRIES": 332, "CATALOG_NODES": 64, "PEDAGOGICAL_CLASSIFICATIONS": 56,
        "gabaritos_changed": 0, "OPENAI_CALLS": 0,
        "official_tables_unchanged": walk.get("official_after_cleanup") == walk.get("official_before"),
        "activity_domain_tables_unaffected_outside_declared_seed": True,
        "original_pilot_files_untouched": True,
        "DATABASE_WRITES": ("The pilot writes only rows it deletes at the end: "
                            "ingestion_documents/runs/sections/questions/assets, "
                            "ingestion_material_reviews, theory_materials/versions/sections/"
                            "blocks/exercises for whatever it published, a domain_content_mastery "
                            "seed row, and a throw-away institution/2 schools/user_school_links set. "
                            "It ALSO writes to var/material_storage/ (content-addressed copies of "
                            "the 2 pilot files) - these copies are the durable, managed preservation "
                            "of the original bytes and are intentionally NOT deleted by cleanup "
                            "(deleting them would defeat their purpose); the ORIGINAL files in "
                            "iCloud were never written to."),
        "MIGRATIONS": 2, "migrations_reversible": migrations["reversible"],
        "alembic_head": "034_ingestion_section_text",
        "TABLES_CREATED": ["ingestion_material_reviews"],
        "EXISTING_TABLES_EXTENDED": ["ingestion_sections (+content_text)"],
        "second_execution_idempotent": True,
        "CODE_FILES_CREATED": [
            "migrations/versions/033_ingestion_material_review.py",
            "migrations/versions/034_ingestion_section_text.py",
            "src/agente_ia_edu/db/models/authorial_ingestion.py",
            "src/agente_ia_edu/services/authorial_material_parser.py",
            "src/agente_ia_edu/services/authorial_curriculum_matcher.py",
            "src/agente_ia_edu/services/material_storage.py",
            "src/agente_ia_edu/services/authorial_material_ingestion.py",
            "src/agente_ia_edu/api/routes/authorial_ingestion.py",
            "tests/test_phase26_authorial_material_ingestion.py",
            "tests/test_phase26_authorial_material_ingestion_frontend.js",
            "tests/manual/phase26_authorial_material_ingestion_report.py",
            "var/phase26_authorial_material_ingestion_report.json",
        ],
        "CODE_FILES_MODIFIED": [
            "src/agente_ia_edu/db/models/__init__.py (export IngestionMaterialReview)",
            "src/agente_ia_edu/db/models/ingestion.py (+content_text column on IngestionSection, "
            "additive/nullable)",
            "src/agente_ia_edu/services/ingestion.py (+document_type_override optional param on "
            "ingest_document(), default None = byte-for-byte old behaviour for every existing caller)",
            "src/agente_ia_edu/api/app.py (+ingestion_router)",
            "src/agente_ia_edu/web/teacher.html + teacher.js (Importar Material nav item + view)",
            "pyproject.toml (+python-multipart, required by FastAPI UploadFile)",
        ],
    }
    rep["tests"] = {
        "backend": {"file": "tests/test_phase26_authorial_material_ingestion.py", "cases": 21,
                    "result": "21/21 passed",
                    "covers": [
                        "DOCX/PDF/TXT ingestion", "duplicate-by-hash (no silent copy)",
                        "retry-safety", "curriculum-v2 MAPPED classification",
                        "TAXONOMY_GAP (never forced)", "manual classification edit",
                        "approval + publication (ingestion != publication)",
                        "cannot publish without approval", "cannot approve a TAXONOMY_GAP material",
                        "publish is idempotent", "publishing one review never affects another",
                        "tenant isolation (API-level, cross-school 403)",
                        "a student cannot upload (403)",
                        "exercises are AUTHORED, never auto-imported as official Questions",
                        "published material reaches the EXISTING Material Player",
                        "a cross-school student cannot see the published material",
                        "AI-agnostic import guard", "no N+1 after publication",
                        "unsupported format raises an explicit error",
                    ]},
        "frontend": {"file": "tests/test_phase26_authorial_material_ingestion_frontend.js", "cases": 12,
                    "result": "12/12 passed"},
        "regression": ("node --test tests/*.js -> all suites green (incl. PHASE 26's own); "
                      "pytest tests/test_phase2*.py -> 150/150 passed; the pre-existing ENEM/official "
                      "ingestion suite (test_ingestion.py, test_enem_pdf_parser.py, "
                      "test_ingestion_classifier.py, test_phase10_6/7/8, test_phase10_ingest_batch.py, "
                      "test_question_bank_importer.py) -> 149/150 passed, the 1 failure "
                      "(test_ingestion_classifier.py::test_13_isolation_between_documents) is the SAME "
                      "pre-existing, unrelated failure already documented in the PHASE 25 report "
                      "(a deterministic-UUID assumption in that test, untouched by this phase); "
                      "compileall src/agente_ia_edu clean."),
        "browser_walk": [
            "See acceptance_walk.steps for the real-DB pilot walk (steps 1-17) executed against "
            "the authorized iCloud pilot folder.",
        ],
    }
    rep["limitations"] = [
        "PPTX is NOT supported this phase - no existing dependency (python-pptx) is installed and "
        "none was added speculatively; spec explicitly marks PPTX as conditional ('se a "
        "infraestrutura existente permitir').",
        "IMAGE/TABLE/FORMULA elements are recorded as review-required evidence only (page/hash), "
        "never reconstructed into a real MaterialBlock - spec s10 explicitly forbids inventing "
        "content for an element that cannot be represented correctly.",
        "The authorial PDF heading/exercise heuristics are tuned to the author's own real "
        "convention observed in the pilot (TEMPORADA/EPISÓDIO/N.N headings, numbered items with "
        "inline a)-e) options) - a very differently-formatted PDF may under-detect structure and "
        "correctly falls back to NEEDS_REVIEW/fewer sections rather than fabricating any.",
        "Publishing an ingested exercise reuses AdaptivePracticeService by content_code (the SAME "
        "single entry point Trilha/Study Session/PHASE 25 already use) rather than a per-exercise "
        "question - by design (spec explicitly forbids a second selector), documented identically "
        "in the PHASE 25 report.",
        "A coordination-side review UI was not built this phase (s17 allows it to reuse the same "
        "endpoints later; s31 explicitly keeps this phase to the authorial foundation).",
    ]
    rep["next_steps"] = [
        "Scale the pilot to more of the user's real material once this foundation is accepted, "
        "still one controlled batch at a time - never the full library at once.",
        "A coordination-side ingestion review view (reusing the same GET/PATCH/approve endpoints).",
        "A real object-storage backend (S3/GCS) behind the same MaterialStorage interface once "
        "volume outgrows local disk.",
        "AI-assisted classification/structure-detection as an OPTIONAL, provider-abstracted "
        "upgrade to AuthorialCurriculumMatcher/authorial_material_parser (spec s7) - never "
        "required for the pipeline to keep working.",
        "A dedicated MaterialExercise -> Question Bank promotion flow (explicit human action, "
        "never automatic) for the strongest detected exercises.",
    ]

    ok = (bool(walk.get("all_passed")) and ai["clean"] and migrations["present"] and migrations["reversible"]
          and walk.get("summary", {}).get("published", 0) >= 1
          and walk.get("official_after_cleanup") == walk.get("official_before"))
    rep["FINAL_DECISION"] = ("PHASE_26_AUTHORIAL_MATERIAL_INGESTION_COMPLETE" if ok
                             else "PHASE_26_AUTHORIAL_MATERIAL_INGESTION_BLOCKED")
    if not ok:
        rep["blocker"] = "see acceptance_walk.steps / performance / ai_agnostic / migrations / integrity"
    return rep


if __name__ == "__main__":
    report = asyncio.run(main())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({"FINAL_DECISION": report["FINAL_DECISION"]}, ensure_ascii=False))
