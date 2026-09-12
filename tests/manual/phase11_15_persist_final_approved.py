"""PHASE 11.15 - Controlled persistence of the 3 PHASE 11.14 approved classifications.

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
from pathlib import Path

from sqlalchemy import text

from agente_ia_edu.db.session import create_engine, create_session_factory
from agente_ia_edu.services.curriculum_classification import (
    ClassificationProposal, ClassificationProposalService)

TAXONOMY_VERSION = "curriculum-v2"
CLASSIFIER_VERSION = "phase11_15-persistence-v1"
PROMPT_VERSION = "phase11_15-curriculum-v2-persistence-v1"
PROVIDER_LABEL = "phase-11-14-approved"
MODEL_LABEL = "deterministic-v1"
CONFIDENCE = 0.9
DIFFICULTY = "UNKNOWN"

# (year, num, qv_id, content_code, [evidence excerpts], per-item reason)
AUTHORIZED = [
    (2024, 104, "dd7d3e95-7683-4b7f-903b-51f6c8b8f364", "PHYSICS-MECHANICS-KINEMATICS",
     ["compensar os efeitos da corrente marinha",
      "a velocidade do nadador é de 50 metros por minuto"],
     "PHASE_11_13/11_14 curator-approved: concept (composicao vetorial de velocidades / "
     "velocidade relativa) is fully text-supported; the circuit diagram is a solvability "
     "dependency only, not a classification dependency"),
    (2024, 133, "8ce092bc-e728-4ab0-b480-5211e36e64c8", "BIOLOGY-EVOLUTION-MECHANISMS",
     ["Essas espécies apresentam um padrão de coloração muito semelhante",
      "Qual é a vantagem dessa similaridade para as falsas-corais?"],
     "PHASE_11_14: 3/3 CLASSIFIED HIGH, same CONTENT; semantic audit clean (mimetismo "
     "batesiano = adaptacao por selecao natural)"),
    (2025, 99, "a1a6257c-8bfd-49ab-b721-f1bcc50473e5", "BIOLOGY-CYTOLOGY-ORGANELLES",
     ["Essa doença resulta da insuficiência funcional de qual estrutura celular?",
      "células que não degradam colesterol esterificado nem triglicerídeos"],
     "PHASE_11_14: 3/3 CLASSIFIED HIGH, same CONTENT; granularity oscillation resolved "
     "by the PHASE 11.14 binding; existing organelles node is sufficiently granular"),
]

MUST_NOT_PERSIST = {(2024, 150): None}  # remains HUMAN_REVIEW
PROTECTED = [(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)]

COUNT_TABLES = ["questions", "question_versions", "question_options", "booklet_questions",
                "catalog_nodes", "catalog_node_prerequisites",
                "pedagogical_classifications", "question_classifications"]
CV2_ACTIVE_SQL = ("SELECT count(*) FROM pedagogical_classifications "
                  "WHERE lifecycle='ACTIVE' AND metadata->>'taxonomy_version'='curriculum-v2'")
EXPECT_BEFORE = {"questions": 332, "question_versions": 332, "question_options": 1660,
                 "booklet_questions": 332, "catalog_nodes": 64, "catalog_node_prerequisites": 3,
                 "pedagogical_classifications": 33, "question_classifications": 0,
                 "curriculum_v2_ACTIVE": 29}


async def _counts(s):
    d = {t: int(await s.scalar(text(f"SELECT count(*) FROM {t}"))) for t in COUNT_TABLES}
    d["curriculum_v2_ACTIVE"] = int(await s.scalar(text(CV2_ACTIVE_SQL)))
    return d


async def _protected_fp(s):
    fp = {}
    for y, n in PROTECTED:
        rows = (await s.execute(text(
            """SELECT pc.* FROM pedagogical_classifications pc
               JOIN booklet_questions bq ON bq.question_version_id=pc.question_version_id
               JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
               JOIN exam_applications ea ON ea.id=eb.exam_application_id
               WHERE ea.year=:y AND bq.official_number=:n ORDER BY pc.created_at"""),
            {"y": y, "n": n})).mappings().all()
        fp[f"{y}_Q{n}"] = hashlib.sha256(
            json.dumps([dict(r) for r in rows], sort_keys=True, default=str).encode()).hexdigest()
    return fp


async def _qv_has_active_cv2(s, qv_id):
    return int(await s.scalar(text(
        "SELECT count(*) FROM pedagogical_classifications "
        "WHERE question_version_id=:qv AND lifecycle='ACTIVE' "
        "AND metadata->>'taxonomy_version'='curriculum-v2'"), {"qv": qv_id})) > 0


async def run_batch(factory):
    persisted, skipped, error = [], [], None
    session = factory()
    try:
        await session.begin()
        original_commit = session.commit
        session.commit = session.flush  # type: ignore[method-assign]
        svc = ClassificationProposalService(session)
        try:
            for y, n, qv_id, content_code, excerpts, reason in AUTHORIZED:
                if await _qv_has_active_cv2(session, qv_id):
                    skipped.append({"q": f"{y} Q{n}", "qv_id": qv_id,
                                    "content_code": content_code, "reason": "IDEMPOTENT_EXISTS"})
                    continue
                row = (await session.execute(text(
                    "SELECT canonical_text, statement FROM question_versions WHERE id=:i"),
                    {"i": qv_id})).first()
                src = row.canonical_text or row.statement or ""
                for ex in excerpts:
                    if ex not in src:
                        raise AssertionError(f"{y} Q{n}: evidence excerpt not a literal substring: {ex!r}")
                proposal = ClassificationProposal(
                    primary_content_code=content_code, complementary_content_codes=[],
                    concepts=[], prerequisites=[], cognitive_operations=[],
                    context=reason, difficulty=DIFFICULTY, confidence=CONFIDENCE,
                    evidence=[{"content_code": content_code, "text": ex, "reason": reason}
                              for ex in excerpts])
                rec = await svc.propose(
                    uuid.UUID(qv_id), proposal,
                    classifier_version=CLASSIFIER_VERSION, taxonomy_version=TAXONOMY_VERSION,
                    provider=PROVIDER_LABEL, model=MODEL_LABEL, prompt_version=PROMPT_VERSION)
                if rec.status != "CLASSIFIED":
                    raise AssertionError(
                        f"{y} Q{n}: propose() returned status={rec.status} (expected CLASSIFIED)")
                persisted.append({
                    "q": f"{y} Q{n}", "qv_id": qv_id, "id": str(rec.id),
                    "content": rec.content, "discipline": rec.discipline, "status": rec.status,
                    "lifecycle": rec.lifecycle, "source": rec.source,
                    "model_version": rec.model_version, "prompt_version": rec.prompt_version,
                    "provider_name": rec.provider_name,
                    "classification_confidence": str(rec.classification_confidence),
                    "taxonomy_version": (rec.metadata_ or {}).get("taxonomy_version"),
                    "proposal_status": (rec.metadata_ or {}).get("proposal_status"),
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
    if os.getenv("PHASE11_15_APPROVAL_TOKEN") != "PHASE11-15-PERSIST-11-14-APPROVED":
        print("ABORT: missing/invalid PHASE11_15_APPROVAL_TOKEN")
        sys.exit(2)

    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    report = {"phase": "11.15",
              "mechanism": "ClassificationProposalService.propose (deterministic, no provider call)"}

    # ---- PREFLIGHT ----
    async with factory() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        report["current_database"] = await s.scalar(text("SELECT current_database()"))
        report["preflight_counts"] = await _counts(s)
        report["protected_fingerprints_before"] = await _protected_fp(s)
        pf = {}
        for y, n, qv_id, content_code, excerpts, _reason in AUTHORIZED:
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
                "version_kind": r.vkind if r else None,
                "is_immutable": bool(r.immut) if r else None,
                "official_original_immutable": bool(r) and r.vkind == "official_original" and bool(r.immut),
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
        all(v["qv_matches"] and v["official_original_immutable"] and v["exactly_5_options"]
            and v["no_active_cv2"] and v["content_active_CONTENT"] and v["evidence_literal"]
            for v in pf.values()),
    ]
    if not all(checks):
        report["preflight_gate_ok"] = False
        report["FINAL_DECISION"] = "PHASE_11_15_ABORTED_PREFLIGHT"
        _write(report)
        await engine.dispose()
        sys.exit(1)
    report["preflight_gate_ok"] = True

    # ---- RUN 1 ----
    persisted, skipped, error = await run_batch(factory)
    report["run1"] = {"persisted": persisted, "skipped": skipped, "error": error}

    # ---- POST-COMMIT VERIFY ----
    async with factory() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        report["postcommit_counts"] = await _counts(s)
        report["protected_fingerprints_after"] = await _protected_fp(s)
        newrows = (await s.execute(text(
            """SELECT pc.id, pc.question_version_id, pc.content, pc.discipline, pc.status,
                      pc.lifecycle, pc.source, pc.model_version, pc.provider_name,
                      pc.metadata->>'taxonomy_version' tv, pc.metadata->>'proposal_status' ps
               FROM pedagogical_classifications pc
               WHERE pc.model_version=:cv ORDER BY pc.created_at"""),
            {"cv": CLASSIFIER_VERSION})).mappings().all()
        report["new_rows"] = [dict(r) for r in newrows]
        report["new_rows_target_match"] = [
            {"q": f"{a[0]} Q{a[1]}", "qv_expected": a[2], "content_expected": a[3],
             "row_qv": next((str(r["question_version_id"]) for r in newrows if r["content"] == a[3]
                             and str(r["question_version_id"]) == a[2]), None)}
            for a in AUTHORIZED]
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

    # ---- RUN 2 (idempotency) ----
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
        error is None and len(persisted) == 3
        and report["deltas"]["pedagogical_classifications"] == 3
        and report["deltas"]["curriculum_v2_ACTIVE"] == 3
        and report["deltas"]["question_classifications"] == 0
        and report["deltas"]["catalog_nodes"] == 0
        and report["deltas"]["catalog_node_prerequisites"] == 0
        and after["curriculum_v2_ACTIVE"] == 32
        and len(report["new_rows"]) == 3
        and all(r["tv"] == "curriculum-v2" and r["status"] == "CLASSIFIED" and r["lifecycle"] == "ACTIVE"
                for r in report["new_rows"])
        and all(m["row_qv"] == m["qv_expected"] for m in report["new_rows_target_match"])
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
        "CLASSIFICATIONS_CREATED": len(persisted),
        "QUESTION_CLASSIFICATIONS_CREATED": report["deltas"]["question_classifications"],
        "OPENAI_CALLS": 0,
        "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
        "VOCABULARY_CHANGES": 0,
        "ALEMBIC_EXECUTION": 0,
        "OTHER_QUESTIONS_MODIFIED": 0,
        "ERRORS": 0 if (error is None and error2 is None) else 1,
        "run2_new": len(persisted2),
    }
    report["FINAL_DECISION"] = "PHASE_11_15_PERSISTENCE_COMPLETE" if ok else "PHASE_11_15_NEEDS_REVIEW"
    _write(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    await engine.dispose()
    sys.exit(0 if ok else 1)


def _write(report):
    out = Path(__file__).resolve().parents[1] / "var" / "phase11_15_persist_final_approved_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
