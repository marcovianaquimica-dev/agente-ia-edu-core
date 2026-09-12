"""PHASE 11.7 - Controlled persistence of the 6 PHASE 11.6 FINAL_APPROVED classifications.

Mechanism: the existing deterministic official method
``ClassificationProposalService.propose`` (no provider / no OpenAI call - the
``provider``/``model`` args are plain string labels stored on the row). One
single outer transaction; commit is rebound to flush so all 6 land atomically
and a single real commit closes it. Any failure -> full rollback, no partial
state. Idempotent: a qv that already has an ACTIVE curriculum-v2 row is skipped.
"""
import asyncio
import hashlib
import json
import os
import sys

from sqlalchemy import text

from agente_ia_edu.db.session import create_engine, create_session_factory
from agente_ia_edu.services.curriculum_classification import (
    ClassificationProposal,
    ClassificationProposalService,
)

TAXONOMY_VERSION = "curriculum-v2"
CLASSIFIER_VERSION = "phase11_7-persistence-v1"
PROMPT_VERSION = "phase11_7-curriculum-v2-persistence-v1"
PROVIDER_LABEL = "phase-11-6-approved"
MODEL_LABEL = "deterministic-v1"
CONFIDENCE = 0.9
DIFFICULTY = "UNKNOWN"
CONTEXT_NOTE = (
    "PHASE_11_7 persistence of PHASE_11_6 FINAL_APPROVED; "
    "deterministic ClassificationProposalService.propose; no LLM call"
)

# (year, official_number, qv_id, target_content_code, [verbatim evidence excerpts])
AUTHORIZED = [
    (2024, 103, "e72c9178-58f2-4639-91d5-ee10489c0be8", "PHYSICS-THERMAL-THERMODYNAMICS", [
        "ciclo de Otto para um motor de combustão interna",
        "A transformação da energia térmica em energia útil ocorre na etapa",
    ]),
    (2024, 105, "984ed834-d78d-43e5-ad3c-de80a12b7e72", "BIOLOGY-IMMUNOLOGY-MICROBIOLOGY-DISEASES", [
        "é um retrovírus do mesmo grupo do vírus da imunodeficiência humana (HIV)",
        "infectam as mesmas células de defesa do organismo, os linfócitos T",
    ]),
    (2024, 167, "ca30e6a2-ad15-4ed5-8a0a-c0f293172ec6", "MATH-ALGEBRA-PERCENTAGE", [
        "a porcentagem P de acertos de cada participante é convertida em um conceito",
        "Felipe acertou 30% a menos que a quantidade de questões que João acertou",
    ]),
    (2025, 114, "63beeb96-3d76-4ec6-ae92-00861e8ef28d", "CHEMISTRY-PHYSICAL-STOICHIOMETRY", [
        "redução aluminotérmica de Nb2O5 com excesso de 10% de Al",
        "em relação à quantidade estequiométrica da reação",
    ]),
    (2025, 151, "1d807951-db2c-4762-b86d-8a2630b7c886", "MATH-GEOMETRY-SPATIAL", [
        "é um poliedro de Johnson, cujas faces são polígonos regulares",
        "Quantos vértices tem esse poliedro?",
    ]),
    (2025, 174, "863ff724-8e35-4ddd-a8a7-b65b9e55d18f", "MATH-GEOMETRY-SPATIAL", [
        "tem a forma de paralelepípedo reto retângulo",
        "todo o volume de água contida na caixa é despejado no vaso",
    ]),
]

MUST_NOT_PERSIST = {  # (year, number) -> qv_id ; verified untouched afterwards
    (2024, 130): "d91ea3bf-3110-4165-91f0-3edf17f8b073",  # FINAL_REVIEW
    (2025, 116): None, (2025, 154): None, (2024, 132): None,
    (2025, 146): None, (2024, 150): None,
}
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
            """
            SELECT pc.* FROM pedagogical_classifications pc
            JOIN booklet_questions bq ON bq.question_version_id=pc.question_version_id
            JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
            JOIN exam_applications ea ON ea.id=eb.exam_application_id
            WHERE ea.year=:y AND bq.official_number=:n
            ORDER BY pc.created_at
            """), {"y": y, "n": n})).mappings().all()
        fp[f"{y}_Q{n}"] = hashlib.sha256(
            json.dumps([dict(r) for r in rows], sort_keys=True, default=str).encode()
        ).hexdigest()
    return fp


async def _qv_has_active_cv2(session, qv_id):
    return int(await session.scalar(text(
        "SELECT count(*) FROM pedagogical_classifications "
        "WHERE question_version_id=:qv AND lifecycle='ACTIVE' "
        "AND metadata->>'taxonomy_version'='curriculum-v2'"), {"qv": qv_id})) > 0


async def run_batch(factory, *, tag):
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
                # fetch text explicitly for the literal-substring guard
                row = (await session.execute(text(
                    "SELECT canonical_text, statement FROM question_versions WHERE id=:i"),
                    {"i": qv_id})).first()
                source_text = row.canonical_text or row.statement or ""
                for ex in excerpts:
                    if ex not in source_text:
                        raise AssertionError(f"{y} Q{n}: evidence excerpt not literal substring: {ex!r}")
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
                         "reason": "PHASE_11_6 independent audit APPROVE"}
                        for ex in excerpts
                    ],
                )
                record = await svc.propose(
                    __import__("uuid").UUID(qv_id),
                    proposal,
                    classifier_version=CLASSIFIER_VERSION,
                    taxonomy_version=TAXONOMY_VERSION,
                    provider=PROVIDER_LABEL,
                    model=MODEL_LABEL,
                    prompt_version=PROMPT_VERSION,
                )
                if record.status != "CLASSIFIED":
                    raise AssertionError(
                        f"{y} Q{n}: propose() returned status={record.status} "
                        f"(expected CLASSIFIED) - aborting whole batch")
                persisted.append({
                    "q": f"{y} Q{n}", "qv_id": qv_id, "id": str(record.id),
                    "content": record.content, "discipline": record.discipline,
                    "status": record.status, "lifecycle": record.lifecycle,
                    "source": record.source,
                    "taxonomy_version": (record.metadata_ or {}).get("taxonomy_version"),
                    "proposal_status": (record.metadata_ or {}).get("proposal_status"),
                })
            session.commit = original_commit  # type: ignore[method-assign]
            if error is None and not persisted and skipped:
                # nothing to write (idempotent re-run) - end the read txn cleanly
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
    if os.getenv("PHASE11_7_APPROVAL_TOKEN") != "PHASE11-7-PERSIST-11-6-APPROVED":
        print("ABORT: missing/invalid PHASE11_7_APPROVAL_TOKEN")
        sys.exit(2)

    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)

    report = {"phase": "11.7", "mechanism": "ClassificationProposalService.propose (deterministic, no provider call)"}

    # ---- PREFLIGHT (read-only) ----
    async with factory() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        report["current_database"] = await s.scalar(text("SELECT current_database()"))
        report["preflight_counts"] = await _counts(s)
        report["protected_fingerprints_before"] = await _protected_fingerprints(s)
        pf_ok = {}
        for y, n, qv_id, content_code, _ex in AUTHORIZED:
            r = (await s.execute(text(
                """
                SELECT bq.question_version_id qv FROM booklet_questions bq
                JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
                JOIN exam_applications ea ON ea.id=eb.exam_application_id
                WHERE ea.year=:y AND bq.official_number=:n"""), {"y": y, "n": n})).first()
            node = (await s.execute(text(
                "SELECT node_type, active FROM catalog_nodes WHERE code=:c"), {"c": content_code})).first()
            pf_ok[f"{y} Q{n}"] = {
                "qv_matches": str(r.qv) == qv_id,
                "no_active_cv2": not await _qv_has_active_cv2(s, qv_id),
                "content_active_CONTENT": bool(node and node.active and node.node_type == "CONTENT"),
            }
        report["preflight_per_item"] = pf_ok
        report["Q130_excluded"] = "d91ea3bf-3110-4165-91f0-3edf17f8b073" not in {a[2] for a in AUTHORIZED}

    exp = {
        "questions": 332, "question_versions": 332, "question_options": 1660,
        "booklet_questions": 332, "catalog_nodes": 61, "catalog_node_prerequisites": 3,
        "pedagogical_classifications": 24, "question_classifications": 0,
        "curriculum_v2_ACTIVE": 20,
    }
    checks = [
        report["current_database"] == "agente_ia_edu",
        report["preflight_counts"] == exp,
        all(all(v.values()) for v in pf_ok.values()),
        report["Q130_excluded"],
    ]
    if not all(checks):
        report["result"] = "PHASE_11_7_NEEDS_REVIEW"
        report["abort_reason"] = "preflight failed"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        await engine.dispose()
        sys.exit(1)

    # ---- RUN 1: persist ----
    persisted, skipped, error = await run_batch(factory, tag="run1")
    report["run1"] = {"persisted": persisted, "skipped": skipped, "error": error}

    # ---- POST-COMMIT VERIFICATION (fresh read-only connection) ----
    async with factory() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        report["postcommit_counts"] = await _counts(s)
        report["protected_fingerprints_after"] = await _protected_fingerprints(s)
        newrows = (await s.execute(text(
            """
            SELECT pc.id, pc.question_version_id, pc.content, pc.discipline, pc.status,
                   pc.lifecycle, pc.source, pc.model_version, pc.prompt_version, pc.provider_name,
                   pc.metadata->>'taxonomy_version' tv, pc.metadata->>'proposal_status' ps
            FROM pedagogical_classifications pc
            WHERE pc.model_version=:cv ORDER BY pc.created_at"""), {"cv": CLASSIFIER_VERSION})).mappings().all()
        report["new_rows_by_classifier_version"] = [dict(r) for r in newrows]
        # Q130 + other HUMAN_REVIEW untouched
        untouched = {}
        for (y, n), qv in MUST_NOT_PERSIST.items():
            if qv is None:
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
        # integrity: no dup ACTIVE per (qv, taxonomy_version); no orphan; nodes active
        dup = (await s.execute(text(
            """SELECT question_version_id, count(*) c FROM pedagogical_classifications
               WHERE lifecycle='ACTIVE' AND metadata->>'taxonomy_version'='curriculum-v2'
               GROUP BY question_version_id HAVING count(*)>1"""))).all()
        report["duplicate_active_cv2"] = [[str(d[0]), d[1]] for d in dup]
        orphan = int(await s.scalar(text(
            """SELECT count(*) FROM pedagogical_classifications pc
               LEFT JOIN question_versions qv ON qv.id=pc.question_version_id
               WHERE qv.id IS NULL""")))
        report["orphan_classifications"] = orphan
        bad_node = int(await s.scalar(text(
            f"""SELECT count(*) FROM pedagogical_classifications pc
               WHERE pc.model_version='{CLASSIFIER_VERSION}'
               AND NOT EXISTS (SELECT 1 FROM catalog_nodes cn
                               WHERE cn.code=pc.content AND cn.active AND cn.node_type='CONTENT')""")))
        report["new_rows_pointing_to_inactive_or_missing_node"] = bad_node

    # ---- RUN 2: idempotency ----
    persisted2, skipped2, error2 = await run_batch(factory, tag="run2")
    report["run2_idempotency"] = {"persisted": persisted2, "skipped": skipped2, "error": error2}
    async with factory() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        report["counts_after_run2"] = await _counts(s)

    # ---- DELTA + VERDICT ----
    before, after = report["preflight_counts"], report["counts_after_run2"]
    report["deltas"] = {k: after[k] - before[k] for k in before}
    fp_unchanged = report["protected_fingerprints_before"] == report["protected_fingerprints_after"]
    report["protected_fingerprints_unchanged"] = fp_unchanged

    ok = (
        error is None
        and len(persisted) == 6
        and report["deltas"]["pedagogical_classifications"] == 6
        and report["deltas"]["curriculum_v2_ACTIVE"] == 6
        and report["deltas"]["question_classifications"] == 0
        and report["deltas"]["catalog_nodes"] == 0
        and report["deltas"]["catalog_node_prerequisites"] == 0
        and len(report["new_rows_by_classifier_version"]) == 6
        and all(r["tv"] == "curriculum-v2" and r["status"] == "CLASSIFIED" and r["lifecycle"] == "ACTIVE"
                for r in report["new_rows_by_classifier_version"])
        and not report["duplicate_active_cv2"]
        and report["orphan_classifications"] == 0
        and report["new_rows_pointing_to_inactive_or_missing_node"] == 0
        and all(v["active_cv2_rows"] in (0, -1) for v in report["must_not_persist_check"].values())
        and fp_unchanged
        and error2 is None and len(persisted2) == 0 and len(skipped2) == 6
        and report["counts_after_run2"] == after and report["deltas"]["pedagogical_classifications"] == 6
    )
    report["FINAL_DECISION"] = "PHASE_11_7_PERSISTENCE_COMPLETE" if ok else "PHASE_11_7_NEEDS_REVIEW"
    report["audit"] = {
        "NEW_CLASSIFICATIONS": len(persisted),
        "DUPLICATE_run1": 0 if error is None else None,
        "ERRORS": 0 if (error is None and error2 is None) else 1,
        "OPENAI_CALLS": 0,
        "ALEMBIC_EXECUTION": 0,
        "CATALOG_NODES_CREATED": report["deltas"]["catalog_nodes"],
        "VOCABULARY_CHANGES": 0,
        "run2_new": len(persisted2),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    await engine.dispose()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
