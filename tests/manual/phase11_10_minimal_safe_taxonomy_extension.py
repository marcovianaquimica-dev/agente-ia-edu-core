"""PHASE 11.10 - Minimal safe taxonomy extension.

Creates exactly 3 CONTENT nodes via the existing CurriculumTaxonomyService.create_node,
in a single transaction, strictly gated. No classification, no LLM, no Alembic.
The 3 curriculum-v2 vocabulary bindings (KINEMATICS / ANIMAL-PHYSIOLOGY-ADAPTATIONS /
BIOGEOCHEMICAL-CYCLES) were edited into src/.../_curriculum_v2_bindings.py separately and
false-positive-swept over all 332 statements before this runner is executed.
"""
import asyncio
import hashlib
import json
import os
import sys
from uuid import UUID

from sqlalchemy import select, text

from agente_ia_edu.db.session import create_engine, create_session_factory
from agente_ia_edu.db.models import CatalogNode
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService

NEW_NODES = [
    ("BIOLOGY-CYTOLOGY-ORGANELLES", "Estrutura celular e organelas", "BIOLOGY-CYTOLOGY"),
    ("MATH-ALGEBRA-LOGARITHMS", "Logaritmos e equacoes logaritmicas", "MATH-ALGEBRA"),
    ("BIOLOGY-ECOLOGY-COMMUNITIES-SUCCESSION", "Dinamica de comunidades e sucessao ecologica", "BIOLOGY-ECOLOGY"),
]
EXPECTED_BEFORE = {
    "questions": 332, "question_versions": 332, "question_options": 1660, "booklet_questions": 332,
    "catalog_nodes": 61, "catalog_node_prerequisites": 3,
    "pedagogical_classifications": 30, "question_classifications": 0, "curriculum_v2_ACTIVE": 26,
}
PROTECTED = [(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)]
CV2_ACTIVE = ("SELECT count(*) FROM pedagogical_classifications WHERE lifecycle='ACTIVE' "
              "AND metadata->>'taxonomy_version'='curriculum-v2'")


async def counts(s):
    d = {}
    for t in ("questions", "question_versions", "question_options", "booklet_questions",
              "catalog_nodes", "catalog_node_prerequisites",
              "pedagogical_classifications", "question_classifications"):
        d[t] = int(await s.scalar(text(f"SELECT count(*) FROM {t}")))
    d["curriculum_v2_ACTIVE"] = int(await s.scalar(text(CV2_ACTIVE)))
    return d


async def protected_fp(s):
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


async def taxonomy_integrity(s):
    """Returns dict of integrity checks over the whole catalog."""
    dup = (await s.execute(text(
        "SELECT code, count(*) c FROM catalog_nodes GROUP BY code HAVING count(*)>1"))).all()
    orphan = int(await s.scalar(text(
        "SELECT count(*) FROM catalog_nodes c WHERE c.parent_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM catalog_nodes p WHERE p.id=c.parent_id)")))
    root_mismatch = int(await s.scalar(text(
        """SELECT count(*) FROM catalog_nodes c
           JOIN catalog_nodes p ON p.id=c.parent_id
           WHERE c.root_id IS DISTINCT FROM COALESCE(p.root_id, p.id)""")))
    bad_parent_type = (await s.execute(text(
        """SELECT c.code, c.node_type, p.node_type ptype FROM catalog_nodes c
           JOIN catalog_nodes p ON p.id=c.parent_id
           WHERE (c.node_type='AREA' AND p.node_type<>'DISCIPLINE')
              OR (c.node_type='CONTENT' AND p.node_type<>'AREA')
              OR (c.node_type='SUBCONTENT' AND p.node_type<>'CONTENT')"""))).all()
    # cycle detection
    nodes = (await s.execute(text("SELECT id, parent_id FROM catalog_nodes"))).all()
    parent = {r[0]: r[1] for r in nodes}
    cycles = 0
    for nid in parent:
        seen, cur = set(), nid
        while cur is not None:
            if cur in seen:
                cycles += 1
                break
            seen.add(cur)
            cur = parent.get(cur)
    return {
        "duplicate_codes": [[d[0], d[1]] for d in dup],
        "orphan_nodes": orphan,
        "root_mismatch": root_mismatch,
        "bad_parent_type": [[b[0], b[1], b[2]] for b in bad_parent_type],
        "cycles": cycles,
    }


async def preflight(factory):
    async with factory() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        rep = {"current_database": await s.scalar(text("SELECT current_database()"))}
        rep["counts"] = await counts(s)
        rep["protected_fp"] = await protected_fp(s)
        rep["taxonomy_integrity"] = await taxonomy_integrity(s)
        gates = {}
        for code, _name, parent_code in NEW_NODES:
            exists = await s.scalar(text("SELECT count(*) FROM catalog_nodes WHERE code=:c"), {"c": code})
            par = (await s.execute(text(
                "SELECT id, node_type, active, root_id FROM catalog_nodes WHERE code=:c"),
                {"c": parent_code})).first()
            gates[code] = {
                "already_exists": bool(exists),
                "parent_exists": par is not None,
                "parent_is_AREA_active": bool(par and par.node_type == "AREA" and par.active),
                "parent_code": parent_code,
            }
        rep["node_gates"] = gates
        return rep


async def do_write(factory):
    created = []
    s = factory()
    try:
        await s.begin()
        svc = CurriculumTaxonomyService(s)
        for code, name, parent_code in NEW_NODES:
            parent = await s.scalar(select(CatalogNode).where(CatalogNode.code == parent_code))
            if parent is None or parent.node_type != "AREA" or not parent.active:
                raise AssertionError(f"parent {parent_code} invalid for {code}")
            node = await svc.create_node(name=name, node_type="CONTENT", code=code, parent_id=parent.id)
            created.append({
                "code": node.code, "name": node.name, "type": node.node_type,
                "id": str(node.id), "parent": parent_code, "parent_id": str(parent.id),
                "root_id": str(node.root_id), "active": node.active,
            })
        # in-transaction integrity before commit
        integ = await taxonomy_integrity(s)
        if integ["duplicate_codes"] or integ["orphan_nodes"] or integ["root_mismatch"] \
           or integ["bad_parent_type"] or integ["cycles"]:
            raise AssertionError(f"in-transaction integrity failed: {integ}")
        cnt = int(await s.scalar(text("SELECT count(*) FROM catalog_nodes")))
        if cnt != EXPECTED_BEFORE["catalog_nodes"] + 3:
            raise AssertionError(f"expected {EXPECTED_BEFORE['catalog_nodes']+3} nodes, got {cnt}")
        await s.commit()
        return created, None
    except Exception as exc:  # noqa: BLE001
        await s.rollback()
        return [], f"{type(exc).__name__}: {exc}"
    finally:
        await s.close()


async def main():
    if os.getenv("PHASE11_10_APPROVAL_TOKEN") != "PHASE11-10-MINIMAL-SAFE-TAXONOMY-EXTENSION-APPROVED":
        print("ABORT: missing/invalid PHASE11_10_APPROVAL_TOKEN")
        sys.exit(2)

    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    report = {"phase": "11.10", "mechanism": "CurriculumTaxonomyService.create_node (single transaction)"}

    pre = await preflight(factory)
    report["preflight"] = pre

    gate_ok = (
        pre["current_database"] == "agente_ia_edu"
        and pre["counts"] == EXPECTED_BEFORE
        and not pre["taxonomy_integrity"]["duplicate_codes"]
        and pre["taxonomy_integrity"]["orphan_nodes"] == 0
        and pre["taxonomy_integrity"]["root_mismatch"] == 0
        and not pre["taxonomy_integrity"]["bad_parent_type"]
        and pre["taxonomy_integrity"]["cycles"] == 0
        and all((not g["already_exists"]) and g["parent_is_AREA_active"] for g in pre["node_gates"].values())
    )
    report["preflight_gate_ok"] = gate_ok
    if not gate_ok:
        report["FINAL_DECISION"] = "PHASE_11_10_ABORTED_PREFLIGHT"
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        await engine.dispose()
        sys.exit(1)

    if os.getenv("PHASE11_10_DRY_RUN", "true").lower() != "false":
        report["dry_run"] = {
            "would_create": [{"code": c, "name": n, "type": "CONTENT", "parent": p} for c, n, p in NEW_NODES],
            "bindings_added_in_code": [
                "PHYSICS-MECHANICS-KINEMATICS (new _SPECS entry)",
                "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS (extended)",
                "BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES (extended)",
            ],
            "total_db_changes": 3,
        }
        report["FINAL_DECISION"] = "PHASE_11_10_DRY_RUN_OK"
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        await engine.dispose()
        sys.exit(0)

    created, err = await do_write(factory)
    report["write"] = {"created": created, "error": err}
    if err is not None:
        report["FINAL_DECISION"] = "PHASE_11_10_ABORTED_WRITE_ROLLED_BACK"
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        await engine.dispose()
        sys.exit(1)

    # post-write verification in an independent connection
    async with factory() as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        report["postwrite"] = {
            "counts": await counts(s),
            "protected_fp": await protected_fp(s),
            "taxonomy_integrity": await taxonomy_integrity(s),
        }
        newrows = (await s.execute(text(
            """SELECT c.code, c.name, c.node_type, c.active, p.code parent, p2.code root
               FROM catalog_nodes c JOIN catalog_nodes p ON p.id=c.parent_id
               LEFT JOIN catalog_nodes p2 ON p2.id=c.root_id
               WHERE c.code = ANY(:codes) ORDER BY c.code"""),
            {"codes": [c for c, _n, _p in NEW_NODES]})).mappings().all()
        report["postwrite"]["new_nodes"] = [dict(r) for r in newrows]

    # idempotency: preflight again; the 3 codes must now already exist
    pre2 = await preflight(factory)
    report["idempotency_preflight"] = {
        "node_gates": pre2["node_gates"],
        "counts": pre2["counts"],
        "would_create_now": [c for c, _n, _p in NEW_NODES if not pre2["node_gates"][c]["already_exists"]],
    }

    before, after = pre["counts"], report["postwrite"]["counts"]
    report["deltas"] = {k: after[k] - before[k] for k in before}
    fp_unchanged = pre["protected_fp"] == report["postwrite"]["protected_fp"]
    ti = report["postwrite"]["taxonomy_integrity"]

    ok = (
        err is None
        and len(created) == 3
        and report["deltas"]["catalog_nodes"] == 3
        and all(report["deltas"][k] == 0 for k in before if k != "catalog_nodes")
        and after["catalog_nodes"] == 64
        and not ti["duplicate_codes"] and ti["orphan_nodes"] == 0 and ti["root_mismatch"] == 0
        and not ti["bad_parent_type"] and ti["cycles"] == 0
        and fp_unchanged
        and all(r["node_type"] == "CONTENT" and r["active"] for r in report["postwrite"]["new_nodes"])
        and len(report["idempotency_preflight"]["would_create_now"]) == 0
        and all(g["already_exists"] for g in pre2["node_gates"].values())
    )
    report["protected_fingerprints_unchanged"] = fp_unchanged
    report["security"] = {
        "DATABASE_WRITES": 1,
        "OPENAI_CALLS": 0,
        "CLASSIFICATIONS_CREATED": 0,
        "QUESTION_CLASSIFICATIONS_CREATED": 0,
        "CATALOG_NODES_CREATED": len(created),
        "VOCABULARY_CHANGES": 3,
        "PRODUCTION_DATA_MODIFIED": 1,
        "ALEMBIC_EXECUTION": 0,
    }
    report["FINAL_DECISION"] = (
        "PHASE_11_10_MINIMAL_SAFE_TAXONOMY_EXTENSION_COMPLETE" if ok else "PHASE_11_10_NEEDS_REVIEW")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    await engine.dispose()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
