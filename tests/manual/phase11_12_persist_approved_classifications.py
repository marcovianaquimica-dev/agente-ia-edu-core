"""PHASE 11.12 - Controlled persistence of the 3 PHASE 11.11 FINAL_CLASSIFIED classifications.

Mechanism: the existing deterministic official method
``ClassificationProposalService.propose`` (no provider / no OpenAI call - the
``provider``/``model`` args are plain string labels stored on the row). One single
outer transaction; commit rebound to flush so all 3 land atomically and a single
real commit closes it. Any failure -> full rollback, no partial state. Idempotent:
a qv that already has an ACTIVE curriculum-v2 row is skipped.
"""
import asyncio
import hashlib
import json
import os
import sys
import uuid

from sqlalchemy import text

from agente_ia_edu.db.session import create_engine, create_session_factory
from agente_ia_edu.services.curriculum_classification import (
    ClassificationProposal,
    ClassificationProposalService,
)

TAXONOMY_VERSION = "curriculum-v2"
CLASSIFIER_VERSION = "phase11_12-persistence-v1"
PROMPT_VERSION = "phase11_12-curriculum-v2-persistence-v1"
PROVIDER_LABEL = "phase-11-11-approved"
MODEL_LABEL = "deterministic-v1"
CONFIDENCE = 0.9
DIFFICULTY = "UNKNOWN"
CONTEXT_NOTE = (
    "PHASE_11_12 persistence of PHASE_11_11 FINAL_CLASSIFIED (3/3 CLASSIFIED, HIGH, "
    "unanimous, semantic audit clean); deterministic ClassificationProposalService.propose; no LLM call"
)

# (year, official_number, qv_id, target_content_code, [verbatim evidence excerpts from PHASE 11.11])
AUTHORIZED = [
    (2024, 134, "144270ed-55ae-47a4-b729-90ce39509520", "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS", [
        "O exoesqueleto dos crustáceos é formado por quitina e impregnações de sais calcários",
        "por ser duro, limita o crescimento desses animais",
    ]),
    (2024, 131, "66ed0517-a70f-465d-becf-e2f24e71d10d", "BIOLOGY-ECOLOGY-COMMUNITIES-SUCCESSION", [
        "ocorrerão mudanças graduais na estrutura e composição das comunidades vegetais ao longo do tempo",
        "O conjunto dessas mudanças graduais é análogo ao processo natural denominado",
    ]),
    (2025, 100, "475fa38b-75fc-4566-a786-fba4b2050e34", "BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES", [
        "ações mitigadoras, que reduzam tanto as emissões quanto os níveis de CO2 na atmosfera",
        "Qual ação mitigadora auxilia na remoção desse gás presente na atmosfera, reduzindo seus níveis?",
    ]),
]

# HUMAN_REVIEW questions from PHASE 11.11 that must stay untouched
MUST_NOT_PERSIST = {(2024, 104): None, (2024, 150): None, (2024, 133): None, (2025, 99): None}
PROTECTED = [(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)]

COUNT_TABLES = [
    "questions", "question_versions", "question_options", "booklet_questions",
    "catalog_nodes", "catalog_node_prerequisites",
    "pedagogical_classifications", "question_classifications",
]
CV2_ACTIVE_SQL = (
    "SELECT count(*) FROM pedagogical_classifications "
    "WHERE lifecycle='ACTIVE' AND metadata->>'taxonomy_version'='curriculum-v2'"
)
EXPECT_BEFORE = {
    "questions": 332, "question_versions": 332, "question_options": 1660,
    "booklet_questions": 332, "catalog_nodes": 64, "catalog_node_prerequisites": 3,
    "pedagogical_classifications": 30, "question_classifications": 0,
    "curriculum_v2_ACTIVE": 26,
}


async def _counts(session):
    out = {}
    for t in COUNT_TABLES:
        out[t] = int(await session.scalar(text(f"SELECT count(*) FROM {t}")))
    out["curriculum_v2_ACTIVE"] = int(await session.scalar(text(CV2_ACTIVE_SQL)))
    return out


async def _protected_fingerprints(session):
    fp = {}
    for y, n in PROTECTED:
        rows = (await session.execute(text(
            """SELECT pc.* FROM pedagogical_classifications pc
               JOIN booklet_questions bq ON bq.question_version_id=pc.question_version_id
               JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
               JOIN exam_applications ea ON ea.id=eb.exam_application_id
               WHERE ea.year=:y AND bq.official_number=:n ORDER BY pc.created_at"""),
            {"y": y, "n": n})).mappings().all()
        fp[f"{y}_Q{n}"] = hashlib.sha256(
            json.dumps([dict(r) for r in rows], sort_keys=True, default=str).encode()).hexdigest()
    return fp


async def _qv_has_active_cv2(session, qv_id):
    return int(await session.scalar(text(
        "SELECT count(*) FROM pedagogical_classifications "
        "WHERE question_version_id=:qv AND lifecycle='ACTIVE' "
        "AND metadata->>'taxonomy_version'='curriculum-v2'"), {"qv": qv_id})) > 0


async def run_batch(factory):
    """One atomic attempt. Returns (persisted, skipped, error)."""
    persisted, skipped, error = [], [], None
    session = factory()
    try:
        await session.begin()
        original_commit = session.commit
        session.commit = session.flush  # type: ignore[method-assign]
        svc = ClassificationProposalService(session)
        try:
            for y, n, qv_id, content_code, excerpts in AUTHORIZED:
                if await _qv_has_active_cv2(session, qv_id):
                    skipped.append({"q": f"{y} Q{n}", "qv_id": qv_id,
                                    "content_code": content_code, "reason": "IDEMPOTENT_EXISTS"})
                    continue
                row = (await session.execute(text(
                    "SELECT canonical_text, statement FROM question_versions WHERE id=:i"),
                    {"i": qv_id})).first()
                source_text = row.canonical_text or row.statement or ""
                for ex in excerpts:
                    if ex not in source_text:
                        raise AssertionError(f"{y} Q{n}: evidence excerpt not a literal substring: {ex!r}")
                proposal = ClassificationProposal(
                    primary_content_code=content_code,
                    complementary_content_codes=[],
                    concepts=[],
                    prerequisites=[],
                    cognitive_operations=[],
                    context=CONTEXT_NOTE,
                    difficulty=DIFFICULTY,
                    confidence=CONFIDENCE,
                    evidence=[
                        {"content_code": content_code, "text": ex,
                         "reason": "PHASE_11_11 unanimous 3/3 CLASSIFIED HIGH; semantic audit clean"}
                        for ex in excerpts
                    ],
                )
                record = await svc.propose(
                    uuid.UUID(qv_id), proposal,
                    classifier_version=CLASSIFIER_VERSION,
                    taxonomy_version=TAXONOMY_VERSION,
                    provider=PROVIDER_LABEL, model=MODEL_LABEL,
                    prompt_version=PROMPT_VERSION,
                )
                if record.status != "CLASSIFIED":
                    raise AssertionError(
                        f"{y} Q{n}: propose() returned status={record.status} (expected CLASSIFIED) "
                        f"- aborting whole batch")
                persisted.append({
                    "q": f"{y} Q{n}", "qv_id": qv_id, "id": str(record.id),
                    "content": record.content, "discipline": record.discipline,
                    "status": record.status, "lifecycle": record.lifecycle, "source": record.source,
                    "model_version": record.model_version, "prompt_version": record.prompt_version,
                    "provider_name": record.provider_name,
                    "classification_confidence": str(record.classification_confidence),
                    "taxonomy_version": (record.metadata_ or {}).get("taxonomy_version"),
                    "proposal_status": (record.metadata_ or {}).get("proposal_status"),
                    "evidence": (record.metadata_ or {}).get("evidence"),
                })
            session.commit = original_commit  # type: ignore[method-assign]
            if error is None and not persisted and skipped:
                await session.rollback()
            else:
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            session.commit = original_commit  # type: ignore[method-assign]
            await session.rollback()
            error = f"{type(exc).__name__}: {exc}"
            persisted = []
    finally:
        await session.close()
    return persisted, skipped, error


async def main():
    if os.getenv("PHASE11_12_APPROVAL_TOKEN") != "PHASE11-12-PERSIST-11-11-APPROVED":
        print("ABORT: missing/invalid PHASE11_12_APPROVAL_TOKEN")
        sys.exit(2)

    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    report = {"phase": "11.12",
              "mechanism": "ClassificationProposalService.propose (deterministic, no provider call)"}

    # ---- PREFLIGHT (read-only) ----
    async with factory() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        report["current_database"] = await s.scalar(text("SELECT current_database()"))
        report["preflight_counts"] = await _counts(s)
        report["protected_fingerprints_before"] = await _protected_fingerprints(s)
        pf = {}
        for y, n, qv_id, content_code, excerpts in AUTHORIZED:
            r = (await s.execute(text(
                """SELECT bq.question_version_id qv,
                          (SELECT count(*) FROM question_options qo
                           WHERE qo.question_version_id=bq.question_version_id) nopts,
                          qv.version_kind vkind, qv.is_immutable immut,
                          qv.canonical_text ct, qv.statement st
                   FROM booklet_questions bq
                   JOIN question_versions qv ON qv.id=bq.question_version_id
                   JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                   JOIN exam_applications ea ON ea.id=eb.exam_application_id
                   WHERE ea.year=:y AND bq.official_number=:n"""), {"y": y, "n": n})).first()
            node = (await s.execute(text(
                "SELECT node_type, active FROM catalog_nodes WHERE code=:c"), {"c": content_code})).first()
            src = (r.ct or r.st or "") if r else ""
            pf[f"{y} Q{n}"] = {
                "qv_matches": bool(r) and str(r.qv) == qv_id,
                "qv_version_kind": r.vkind if r else None,
                "qv_is_immutable": bool(r.immut) if r else None,
                "options_count": r.nopts if r else None,
                "exactly_5_options": bool(r) and r.nopts == 5,
                "no_active_cv2": not await _qv_has_active_cv2(s, qv_id),
                "content_active_CONTENT": bool(node and node.active and node.node_type == "CONTENT"),
                "evidence_literal": all(ex in src for ex in excerpts),
            }
        report["preflight_per_item"] = pf

    checks = [
        report["current_database"] == "agente_ia_edu",
        report["preflight_counts"] == EXPECT_BEFORE,
        all(v["qv_matches"] and v["exactly_5_options"] and v["no_active_cv2"]
            and v["content_active_CONTENT"] and v["evidence_literal"] for v in pf.values()),
    ]
    if not all(checks):
        report["FINAL_DECISION"] = "PHASE_11_12_ABORTED_PREFLIGHT"
        report["preflight_gate_ok"] = False
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        await engine.dispose()
        sys.exit(1)
    report["preflight_gate_ok"] = True

    # ---- RUN 1: persist ----
    persisted, skipped, error = await run_batch(factory)
    report["run1"] = {"persisted": persisted, "skipped": skipped, "error": error}

    # ---- POST-COMMIT VERIFICATION (fresh read-only connection) ----
    async with factory() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        report["postcommit_counts"] = await _counts(s)
        report["protected_fingerprints_after"] = await _protected_fingerprints(s)
        newrows = (await s.execute(text(
            """SELECT pc.id, pc.question_version_id, pc.content, pc.discipline, pc.status,
                      pc.lifecycle, pc.source, pc.model_version, pc.prompt_version, pc.provider_name,
                      pc.classification_confidence,
                      pc.metadata->>'taxonomy_version' tv, pc.metadata->>'proposal_status' ps
               FROM pedagogical_classifications pc
               WHERE pc.model_version=:cv ORDER BY pc.created_at"""), {"cv": CLASSIFIER_VERSION})).mappings().all()
        report["new_rows"] = [dict(r) for r in newrows]
        untouched = {}
        for (y, n), _ in MUST_NOT_PERSIST.items():
            r = (await s.execute(text(
                """SELECT bq.question_version_id qv FROM booklet_questions bq
                   JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                   JOIN exam_applications ea ON ea.id=eb.exam_application_id
                   WHERE ea.year=:y AND bq.official_number=:n"""), {"y": y, "n": n})).first()
            qv = str(r.qv) if r else None
            c = int(await s.scalar(text(
                "SELECT count(*) FROM pedagogical_classifications WHERE question_version_id=:qv "
                "AND lifecycle='ACTIVE' AND metadata->>'taxonomy_version'='curriculum-v2'"), {"qv": qv})) if qv else -1
            untouched[f"{y} Q{n}"] = {"qv_id": qv, "active_cv2_rows": c}
        report["must_not_persist_check"] = untouched
        dup = (await s.execute(text(
            """SELECT question_version_id, count(*) c FROM pedagogical_classifications
               WHERE lifecycle='ACTIVE' AND metadata->>'taxonomy_version'='curriculum-v2'
               GROUP BY question_version_id HAVING count(*)>1"""))).all()
        report["duplicate_active_cv2"] = [[str(d[0]), d[1]] for d in dup]
        report["orphan_classifications"] = int(await s.scalar(text(
            """SELECT count(*) FROM pedagogical_classifications pc
               LEFT JOIN question_versions qv ON qv.id=pc.question_version_id WHERE qv.id IS NULL""")))
        report["new_rows_pointing_to_inactive_or_missing_node"] = int(await s.scalar(text(
            f"""SELECT count(*) FROM pedagogical_classifications pc
                WHERE pc.model_version='{CLASSIFIER_VERSION}'
                AND NOT EXISTS (SELECT 1 FROM catalog_nodes cn
                                WHERE cn.code=pc.content AND cn.active AND cn.node_type='CONTENT')""")))
        report["new_rows_target_match"] = [
            {"q": f"{a[0]} Q{a[1]}", "qv_expected": a[2], "content_expected": a[3],
             "row": next((dict(r) for r in newrows if str(r["question_version_id"]) == a[2]), None)}
            for a in AUTHORIZED]

    # ---- RUN 2: idempotency ----
    persisted2, skipped2, error2 = await run_batch(factory)
    report["run2_idempotency"] = {"persisted": persisted2, "skipped": skipped2, "error": error2}
    async with factory() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        report["counts_after_run2"] = await _counts(s)

    before, after = report["preflight_counts"], report["counts_after_run2"]
    report["deltas"] = {k: after[k] - before[k] for k in before}
    fp_unchanged = report["protected_fingerprints_before"] == report["protected_fingerprints_after"]
    report["protected_fingerprints_unchanged"] = fp_unchanged

    ok = (
        error is None
        and len(persisted) == 3
        and report["deltas"]["pedagogical_classifications"] == 3
        and report["deltas"]["curriculum_v2_ACTIVE"] == 3
        and report["deltas"]["question_classifications"] == 0
        and report["deltas"]["catalog_nodes"] == 0
        and report["deltas"]["catalog_node_prerequisites"] == 0
        and after["curriculum_v2_ACTIVE"] == 29
        and len(report["new_rows"]) == 3
        and all(r["tv"] == "curriculum-v2" and r["status"] == "CLASSIFIED" and r["lifecycle"] == "ACTIVE"
                for r in report["new_rows"])
        and all(m["row"] is not None and m["row"]["content"] == m["content_expected"]
                for m in report["new_rows_target_match"])
        and not report["duplicate_active_cv2"]
        and report["orphan_classifications"] == 0
        and report["new_rows_pointing_to_inactive_or_missing_node"] == 0
        and all(v["active_cv2_rows"] in (0, -1) for v in report["must_not_persist_check"].values())
        and fp_unchanged
        and error2 is None and len(persisted2) == 0 and len(skipped2) == 3
        and report["counts_after_run2"] == after
    )
    report["security"] = {
        "DATABASE_WRITES": 1,
        "PRODUCTION_DATA_MODIFIED": 1,
        "CLASSIFICATIONS_CREATED": len(persisted),
        "QUESTION_CLASSIFICATIONS_CREATED": report["deltas"]["question_classifications"],
        "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
        "VOCABULARY_CHANGES": 0,
        "OPENAI_CALLS": 0,
        "ALEMBIC_EXECUTION": 0,
        "ERRORS": 0 if (error is None and error2 is None) else 1,
        "OTHER_QUESTIONS_MODIFIED": 0,
        "run2_new": len(persisted2),
    }
    report["FINAL_DECISION"] = "PHASE_11_12_PERSISTENCE_COMPLETE" if ok else "PHASE_11_12_NEEDS_REVIEW"
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    from pathlib import Path
    out = Path(__file__).resolve().parents[1] / "var" / "phase11_12_persist_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    await engine.dispose()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
