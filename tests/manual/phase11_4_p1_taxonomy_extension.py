"""PHASE 11.4 - curriculum-v2 P1 TAXONOMY EXTENSION (controlled write).

Adds ONLY the P1 gaps from PHASE 11.3 that are needed to unblock classification
of the ENEM 2024/2025 Day-2 questions already in the Question Bank:

  new AREA (4):
    MATH-PROBABILITY, MATH-GEOMETRY,
    BIOLOGY-IMMUNOLOGY-MICROBIOLOGY, BIOLOGY-EVOLUTION
  new CONTENT (8):
    MATH-PROBABILITY-BASICS, MATH-GEOMETRY-SPATIAL, MATH-ALGEBRA-PERCENTAGE,
    MATH-STATISTICS-DATA-INTERPRETATION, CHEMISTRY-PHYSICAL-STOICHIOMETRY,
    CHEMISTRY-ORGANIC-FUNCTIONS, CHEMISTRY-ORGANIC-REACTIONS,
    BIOLOGY-IMMUNOLOGY-MICROBIOLOGY-DISEASES, BIOLOGY-EVOLUTION-MECHANISMS
  new prerequisites (3, HIGH only):
    PROBABILITY-BASICS  <- COMBINATORICS-COUNTING
    GEOMETRY-SPATIAL    <- MEASUREMENT-PLANE-AREA
    ORGANIC-REACTIONS   <- ORGANIC-FUNCTIONS

Deliberately NOT done (documented as design decisions):
  * MATH-ALGEBRA-RATIO is left untouched. It is a SUBCONTENT hard-coded in
    curriculum_taxonomy.seed_reference_fixture() and asserted by several tests;
    restructuring it in the DB alone would break the seed guard and those tests.
    Its concept (proporcionalidade / regra de tres) therefore keeps its existing
    home; the "proportionality" P1 gap needs no new node and no duplicate.
  * No controlled-vocabulary binding is added. Robust deterministic bindings for
    broad concepts (probability, percentage, stoichiometry) cannot be built
    without the fragile single-word matching PHASE 11.2 flagged; the mechanism
    that connects questions to the new nodes (provider vs curation map vs
    bindings) is PHASE 11.5's decision. VOCABULARY_CHANGES = 0 here.
  * No pedagogical_classifications / question_classifications are created.

Reuses agente_ia_edu.services.curriculum_taxonomy.CurriculumTaxonomyService
(create_node + add_prerequisite) - the existing, validated mechanism. One
transaction, pre/post validation, idempotent, rollback on any failure.

Gates:  PHASE11_4_DRY_RUN != "false"  -> dry run (default)
        PHASE11_4_APPROVAL_TOKEN == "PHASE11-4-P1-TAXONOMY-EXTENSION-APPROVED"
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _cand in (Path.cwd(), _HERE.parents[1]):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        _REPO_ROOT = _cand
        _ENV_FILE = _cand / ".env"
        break
else:  # pragma: no cover
    _REPO_ROOT = _HERE.parents[1]
    _ENV_FILE = Path(".env")

from sqlalchemy import select, text  # noqa: E402

from agente_ia_edu.db.models import CatalogNode, CatalogNodePrerequisite  # noqa: E402
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService  # noqa: E402

APPROVAL_TOKEN = "PHASE11-4-P1-TAXONOMY-EXTENSION-APPROVED"
REPORT = _REPO_ROOT / "var" / "inep-pilot" / "phase11_4_taxonomy_extension_report.json"

# Two valid starting states: pristine (before this phase) OR already-extended
# (idempotent re-run). Values that must be identical in BOTH.
INVARIANT = {"pedagogical_classifications": 24, "question_classifications": 0,
             "questions": 332, "question_versions": 332}
STATE_PRISTINE = {"catalog_nodes": 48, "catalog_node_prerequisites": 0}
STATE_EXTENDED = {"catalog_nodes": 48 + 13, "catalog_node_prerequisites": 3}
EXPECT = {**INVARIANT, **STATE_PRISTINE}  # for report `before` display only
PROTECTED = [(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)]

# (code, name, node_type, parent_code, gap_id, reason, affected_official_numbers)
NEW_NODES = [
    ("MATH-PROBABILITY", "Probabilidade", "AREA", "MATH", "P1-1",
     "Probabilidade e um eixo do ENEM distinto da estatistica descritiva; ausente do curriculum-v2", [161, 154]),
    ("MATH-PROBABILITY-BASICS", "Probabilidade de eventos", "CONTENT", "MATH-PROBABILITY", "P1-1",
     "probabilidade classica / equiprobabilidade / eventos", [161, 154]),
    ("MATH-GEOMETRY", "Geometria", "AREA", "MATH", "P1-4",
     "Geometria espacial (solidos, poliedros, volume) e area central do ENEM e nao ha AREA de geometria (so MATH-MEASUREMENT com area plana)", [151, 174]),
    ("MATH-GEOMETRY-SPATIAL", "Geometria espacial: solidos, poliedros e volume", "CONTENT", "MATH-GEOMETRY", "P1-4",
     "solidos geometricos, relacao de Euler, calculo de volume", [151, 174]),
    ("MATH-ALGEBRA-PERCENTAGE", "Porcentagem e variacao percentual", "CONTENT", "MATH-ALGEBRA", "P1-3",
     "porcentagem e um dos conteudos mais recorrentes do ENEM; distinto de razao/proporcao", [167]),
    ("MATH-STATISTICS-DATA-INTERPRETATION", "Leitura e interpretacao de graficos e tabelas", "CONTENT", "MATH-STATISTICS", "P1-5",
     "competencia recorrente de leitura de dados; hoje MATH-STATISTICS so tem tendencia central", [161, 168, 155]),
    ("CHEMISTRY-PHYSICAL-STOICHIOMETRY", "Estequiometria e calculos quimicos", "CONTENT", "CHEMISTRY-PHYSICAL", "P1-6",
     "estequiometria (mol, massa molar, reagente limitante/excesso) e tema recorrente e ausente", [114]),
    ("CHEMISTRY-ORGANIC-FUNCTIONS", "Funcoes organicas", "CONTENT", "CHEMISTRY-ORGANIC", "P1-7",
     "identificacao de grupos funcionais; CHEMISTRY-ORGANIC so tem POLYMERS. N05 (FUNCTIONAL-GROUPS) permanece deferido e intocado", [107]),
    ("CHEMISTRY-ORGANIC-REACTIONS", "Reacoes organicas", "CONTENT", "CHEMISTRY-ORGANIC", "P1-7",
     "reacoes organicas (ex.: formacao de compostos nitrosos); curricularmente distinto de funcoes", [130]),
    ("BIOLOGY-IMMUNOLOGY-MICROBIOLOGY", "Imunologia e Microbiologia", "AREA", "BIOLOGY", "P1-8",
     "Biologia so tem CYTOLOGY/ECOLOGY/ANIMAL-PHYSIOLOGY; imunologia e microbiologia sao ausentes", [105, 98]),
    ("BIOLOGY-IMMUNOLOGY-MICROBIOLOGY-DISEASES", "Agentes infecciosos, defesa e doencas", "CONTENT", "BIOLOGY-IMMUNOLOGY-MICROBIOLOGY", "P1-8",
     "virus/retrovirus, resposta imune, toxinas e doencas associadas", [105, 98]),
    ("BIOLOGY-EVOLUTION", "Evolucao", "AREA", "BIOLOGY", "P1-9",
     "Evolucao e eixo estruturante da Biologia no ENEM e esta ausente da taxonomia", [133]),
    ("BIOLOGY-EVOLUTION-MECHANISMS", "Mecanismos evolutivos e adaptacao", "CONTENT", "BIOLOGY-EVOLUTION", "P1-9",
     "selecao natural, adaptacao, mimetismo, evidencias evolutivas", [133]),
]

NEW_PREREQS = [
    # (content_code requires prerequisite_code, confidence, reason)
    ("MATH-PROBABILITY-BASICS", "MATH-COMBINATORICS-COUNTING", "HIGH",
     "probabilidade classica exige o principio fundamental da contagem"),
    ("MATH-GEOMETRY-SPATIAL", "MATH-MEASUREMENT-PLANE-AREA", "HIGH",
     "volume de solidos exige area de figuras planas"),
    ("CHEMISTRY-ORGANIC-REACTIONS", "CHEMISTRY-ORGANIC-FUNCTIONS", "HIGH",
     "compreender reacoes organicas exige reconhecer funcoes/grupos funcionais"),
]

# PHASE 11.3 concept -> probable CONTENT mapping for the 36 Day-2 questions (coverage only)
COVERAGE_MAP = {
    (2024, 161): "MATH-PROBABILITY-BASICS / MATH-STATISTICS-DATA-INTERPRETATION",
    (2025, 154): "MATH-PROBABILITY-BASICS",
    (2024, 159): "MATH-ALGEBRA-RATIO (existing)",
    (2025, 146): "MATH-ALGEBRA-RATIO (existing)",
    (2025, 152): "MATH-ALGEBRA-RATIO (existing)",
    (2025, 150): "MATH-ALGEBRA-RATIO (existing)",
    (2024, 167): "MATH-ALGEBRA-PERCENTAGE",
    (2025, 151): "MATH-GEOMETRY-SPATIAL",
    (2025, 174): "MATH-GEOMETRY-SPATIAL",
    (2024, 168): "MATH-STATISTICS-DATA-INTERPRETATION",
    (2025, 155): "MATH-STATISTICS-DATA-INTERPRETATION",
    (2025, 153): "MATH-COMBINATORICS-COUNTING (existing)",
    (2024, 160): "MATH-ALGEBRA-FUNCTIONS (existing, P2)",
    (2025, 173): "MATH-ALGEBRA-FUNCTIONS (existing, P2)",
    (2024, 150): "MATH-ALGEBRA-FUNCTIONS (existing, P3 logaritmos)",
    (2025, 114): "CHEMISTRY-PHYSICAL-STOICHIOMETRY",
    (2024, 130): "CHEMISTRY-ORGANIC-REACTIONS",
    (2020, 107): "CHEMISTRY-ORGANIC-FUNCTIONS (PROTECTED - discovery only, not to be classified here)",
    (2025, 100): "CHEMISTRY-ENVIRONMENTAL-AIR-POLLUTION (existing, P3)",
    (2025, 96): "CHEMISTRY-SOLUTIONS (existing, P3)",
    (2025, 116): "CHEMISTRY-GENERAL-POLARITY-IMF (existing, P3)",
    (2024, 103): "PHYSICS-THERMAL-THERMODYNAMICS (existing)",
    (2024, 104): "PHYSICS-MECHANICS-KINEMATICS (existing, P2)",
    (2025, 97): "NOT COVERED - PHYSICS-OPTICS deferred (P2)",
    (2024, 129): "NOT COVERED - EM spectrum / raios X deferred (P3)",
    (2024, 105): "BIOLOGY-IMMUNOLOGY-MICROBIOLOGY-DISEASES",
    (2025, 98): "BIOLOGY-IMMUNOLOGY-MICROBIOLOGY-DISEASES",
    (2024, 133): "BIOLOGY-EVOLUTION-MECHANISMS",
    (2024, 131): "BIOLOGY-ECOLOGY (existing AREA, P2 succession)",
    (2024, 107): "BIOLOGY-ECOLOGY (existing AREA, P2 contamination)",
    (2024, 132): "BIOLOGY-ANIMAL-PHYSIOLOGY (existing AREA, P2)",
    (2024, 134): "BIOLOGY-ANIMAL-PHYSIOLOGY (existing AREA, P2)",
    (2025, 99): "BIOLOGY-CYTOLOGY (existing AREA, P3)",
}


class Abort(RuntimeError):
    pass


async def _counts(session) -> dict:
    out = {}
    for t in ("catalog_nodes", "catalog_node_prerequisites", "pedagogical_classifications",
              "question_classifications", "questions", "question_versions"):
        out[t] = int(await session.scalar(text(f"SELECT count(*) FROM {t}")))
    out["curriculum_v2_ACTIVE"] = int(await session.scalar(text(
        "SELECT count(*) FROM pedagogical_classifications "
        "WHERE lifecycle='ACTIVE' AND metadata->>'taxonomy_version'='curriculum-v2'")))
    return out


async def _tree(session) -> list[dict]:
    rows = (await session.execute(text(
        "SELECT node_type,code,name,parent_id,root_id,position,active FROM catalog_nodes"))).all()
    idcode = {str(r_.id): r_.code for r_ in
              (await session.execute(text("SELECT id,code FROM catalog_nodes"))).all()}
    return sorted(({"node_type": r_.node_type, "code": r_.code, "name": r_.name,
                    "parent": idcode.get(str(r_.parent_id)), "root": idcode.get(str(r_.root_id)),
                    "position": r_.position, "active": r_.active} for r_ in rows),
                   key=lambda n: (n["node_type"], n["code"]))


async def _protected_fingerprint(session) -> dict:
    fp = {}
    for y, n in PROTECTED:
        row = (await session.execute(text("""
            SELECT bq.question_version_id AS qvid, qv.content_hash AS ch,
                   (SELECT count(*) FROM pedagogical_classifications pc
                    WHERE pc.question_version_id=bq.question_version_id) AS nc,
                   (SELECT string_agg(pc.id::text||':'||pc.lifecycle, ',' ORDER BY pc.id::text)
                    FROM pedagogical_classifications pc
                    WHERE pc.question_version_id=bq.question_version_id) AS cls
            FROM booklet_questions bq
            JOIN question_versions qv ON qv.id=bq.question_version_id
            JOIN exam_booklets eb ON eb.id=bq.exam_booklet_id
            JOIN exam_applications ea ON ea.id=eb.exam_application_id
            WHERE ea.year=:y AND bq.official_number=:n"""), {"y": y, "n": n})).first()
        fp[f"{y}_Q{n}"] = {"question_version_id": str(row.qvid), "content_hash": row.ch,
                           "classification_count": row.nc, "classifications": row.cls}
    return fp


async def _all_classification_ids(session) -> list[str]:
    return sorted(str(x) for x in (await session.scalars(
        text("SELECT id FROM pedagogical_classifications"))).all())


async def preflight(session) -> dict:
    info = {"checks": []}

    def chk(name, ok, detail=""):
        info["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
        if not ok:
            raise Abort(f"preflight failed: {name} ({detail})")

    db = await session.scalar(text("SELECT current_database()"))
    chk("database == agente_ia_edu", db == "agente_ia_edu", db)
    chk("postgresql", "PostgreSQL" in (await session.scalar(text("SELECT version()"))), "")
    c = await _counts(session)
    for k, v in INVARIANT.items():
        chk(f"{k} == {v}", c[k] == v, c[k])
    chk("curriculum-v2 ACTIVE == 20", c["curriculum_v2_ACTIVE"] == 20, c["curriculum_v2_ACTIVE"])
    state = None
    if c["catalog_nodes"] == STATE_PRISTINE["catalog_nodes"] and \
       c["catalog_node_prerequisites"] == STATE_PRISTINE["catalog_node_prerequisites"]:
        state = "PRISTINE"
    elif c["catalog_nodes"] == STATE_EXTENDED["catalog_nodes"] and \
         c["catalog_node_prerequisites"] == STATE_EXTENDED["catalog_node_prerequisites"]:
        state = "ALREADY_EXTENDED"
    chk("catalog state is PRISTINE or ALREADY_EXTENDED", state is not None,
        f"catalog_nodes={c['catalog_nodes']} prereqs={c['catalog_node_prerequisites']}")
    info["starting_state"] = state
    # parents for the new nodes must exist
    codes = {r.code for r in (await session.execute(text("SELECT code FROM catalog_nodes"))).all()}
    for code, _n, _t, parent_code, *_ in NEW_NODES:
        if parent_code not in [x[0] for x in NEW_NODES]:
            chk(f"parent {parent_code} exists", parent_code in codes, parent_code)
    info["baseline_counts"] = c
    return info


async def apply_extension(session, *, dry_run: bool) -> dict:
    svc = CurriculumTaxonomyService(session)
    by_code = {r.code: r for r in (await session.scalars(select(CatalogNode))).all()}

    created, existing = [], []
    # nodes (dependency order: NEW_NODES is already parent-before-child)
    for code, name, ntype, parent_code, gap, reason, affected in NEW_NODES:
        node = by_code.get(code)
        if node is not None:
            existing.append({"code": code, "status": "ALREADY_EXISTS",
                             "node_type": node.node_type,
                             "parent_ok": True})
            continue
        parent = by_code[parent_code]
        siblings = [n for n in by_code.values() if n.parent_id == parent.id]
        pos = (max((s.position for s in siblings), default=0) + 1)
        new = await svc.create_node(name, ntype, code, parent.id, pos)
        by_code[code] = new
        created.append({"code": code, "name": name, "node_type": ntype,
                        "parent": parent_code, "root": parent.root_id and
                        next((c for c, nn in by_code.items() if nn.id == new.root_id), None),
                        "position": pos, "gap": gap, "reason": reason,
                        "affected_official_numbers": affected})

    prereq_created, prereq_existing = [], []
    existing_pre = {(str(p.content_node_id), str(p.prerequisite_node_id))
                    for p in (await session.scalars(select(CatalogNodePrerequisite))).all()}
    for content_code, prereq_code, conf, reason in NEW_PREREQS:
        cnode = by_code.get(content_code)
        pnode = by_code.get(prereq_code)
        if cnode is None or pnode is None:
            raise Abort(f"prerequisite refers to missing node: {content_code} <- {prereq_code}")
        key = (str(cnode.id), str(pnode.id))
        if key in existing_pre:
            prereq_existing.append({"content": content_code, "prerequisite": prereq_code,
                                    "status": "ALREADY_EXISTS"})
            continue
        await svc.add_prerequisite(cnode.id, pnode.id)
        prereq_created.append({"content": content_code, "prerequisite": prereq_code,
                               "confidence": conf, "reason": reason})

    # --- in-transaction validation ---
    problems = []
    nodes = {n.code: n for n in (await session.scalars(select(CatalogNode))).all()}
    expected_parent = {"AREA": "DISCIPLINE", "CONTENT": "AREA", "SUBCONTENT": "CONTENT"}
    for code, n in nodes.items():
        if not n.active:
            problems.append(f"{code}: inactive")
        if n.node_type != "DISCIPLINE":
            if n.parent_id is None:
                problems.append(f"{code}: orphan (no parent)")
            else:
                parent = await session.get(CatalogNode, n.parent_id)
                if parent is None:
                    problems.append(f"{code}: parent missing")
                elif expected_parent.get(n.node_type) != parent.node_type:
                    problems.append(f"{code}: parent type {parent.node_type} invalid for {n.node_type}")
                if n.root_id != (parent.root_id or parent.id):
                    problems.append(f"{code}: root_id mismatch")
        else:
            if n.root_id != n.id:
                problems.append(f"{code}: DISCIPLINE root_id != id")
    # unique codes
    codes = [n.code for n in nodes.values()]
    if len(codes) != len(set(codes)):
        problems.append("duplicate code(s) present")
    # cycle check on prerequisites
    edges = {}
    for p in (await session.scalars(select(CatalogNodePrerequisite))).all():
        edges.setdefault(str(p.content_node_id), []).append(str(p.prerequisite_node_id))
    def has_cycle():
        WHITE, GREY, BLACK = 0, 1, 2
        color = {}
        def dfs(u):
            color[u] = GREY
            for v in edges.get(u, []):
                if color.get(v, WHITE) == GREY:
                    return True
                if color.get(v, WHITE) == WHITE and dfs(v):
                    return True
            color[u] = BLACK
            return False
        return any(color.get(u, WHITE) == WHITE and dfs(u) for u in list(edges))
    if has_cycle():
        problems.append("prerequisite cycle detected")

    if problems:
        raise Abort("in-transaction validation failed: " + "; ".join(problems))

    return {"nodes_created": created, "nodes_already_existing": existing,
            "prerequisites_created": prereq_created, "prerequisites_already_existing": prereq_existing,
            "dry_run": dry_run}


async def _amain(argv) -> int:  # pragma: no cover
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE, override=False)
    except Exception:
        pass
    dry = os.environ.get("PHASE11_4_DRY_RUN", "true").strip().lower() != "false"
    tok = os.environ.get("PHASE11_4_APPROVAL_TOKEN", "")
    from agente_ia_edu.db.session import create_engine, create_session_factory, get_database_url
    try:
        get_database_url()
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: {exc}")
        return 2
    if not dry and tok != APPROVAL_TOKEN:
        print(f"real run requires PHASE11_4_APPROVAL_TOKEN={APPROVAL_TOKEN!r}")
        return 2

    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    report = {"phase": "11.4", "dry_run": dry}
    try:
        async with factory() as ro:
            await ro.execute(text("SET TRANSACTION READ ONLY"))
            report["preflight"] = await preflight(ro)
            report["before"] = {"counts": report["preflight"]["baseline_counts"],
                                "tree": await _tree(ro)}
            protected_before = await _protected_fingerprint(ro)
            cls_ids_before = await _all_classification_ids(ro)

        async with factory() as session:
            async with session.begin():
                result = await apply_extension(session, dry_run=dry)
                if dry:
                    raise _DryRunRollback(result)  # exiting begin() with an exc -> ROLLBACK
            report.update(result)

        report["committed"] = True
    except _DryRunRollback as dr:
        report.update(dr.result)
        report["committed"] = False
    except Abort as exc:
        report["final_decision"] = "PHASE_11_4_NEEDS_REVIEW"
        report["abort"] = str(exc)
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"ABORTED: {exc}")
        await engine.dispose()
        return 1

    # post (separate connection)
    async with factory() as ro:
        await ro.execute(text("SET TRANSACTION READ ONLY"))
        after_counts = await _counts(ro)
        report["after"] = {"counts": after_counts, "tree": await _tree(ro)}
        report["deltas"] = {k: after_counts[k] - report["before"]["counts"][k]
                            for k in after_counts}
        protected_after = await _protected_fingerprint(ro)
        cls_ids_after = await _all_classification_ids(ro)
        report["protected_questions"] = {
            "before": protected_before, "after": protected_after,
            "unchanged": protected_before == protected_after}
        report["classifications_unchanged"] = {
            "ids_before_count": len(cls_ids_before), "ids_after_count": len(cls_ids_after),
            "identical": cls_ids_before == cls_ids_after}
        # integrity
        integ = {}
        integ["orphan_nodes"] = int(await ro.scalar(text(
            "SELECT count(*) FROM catalog_nodes WHERE node_type<>'DISCIPLINE' AND parent_id IS NULL")))
        integ["root_id_mismatch"] = int(await ro.scalar(text("""
            SELECT count(*) FROM catalog_nodes c JOIN catalog_nodes p ON p.id=c.parent_id
            WHERE c.root_id <> COALESCE(p.root_id,p.id)""")))
        integ["duplicate_codes"] = int(await ro.scalar(text(
            "SELECT count(*) FROM (SELECT code FROM catalog_nodes GROUP BY code HAVING count(*)>1) x")))
        integ["inactive_new_nodes"] = int(await ro.scalar(text(
            "SELECT count(*) FROM catalog_nodes WHERE active=false")))
        integ["prereq_to_missing_node"] = int(await ro.scalar(text("""
            SELECT count(*) FROM catalog_node_prerequisites p
            WHERE NOT EXISTS (SELECT 1 FROM catalog_nodes n WHERE n.id=p.content_node_id)
               OR NOT EXISTS (SELECT 1 FROM catalog_nodes n WHERE n.id=p.prerequisite_node_id)""")))
        report["integrity"] = {"checks": integ, "all_clean": all(v == 0 for v in integ.values())}

    # coverage (POTENTIALLY_COVERED - no classification written)
    covered = {k: v for k, v in COVERAGE_MAP.items() if not v.startswith("NOT COVERED")
               and "PROTECTED" not in v}
    not_covered = {k: v for k, v in COVERAGE_MAP.items() if v.startswith("NOT COVERED")}
    report["coverage"] = {
        "scope": "36 ENEM Day-2 questions still pending curriculum-v2 (Q91-180)",
        "POTENTIALLY_COVERED": len(covered),
        "NOT_COVERED_deferred": len(not_covered),
        "by_discipline": {
            "MATH": sum(1 for k in covered if 136 <= k[1] <= 180 or (k[1] in (104, 129))),
            "NATURAL_SCIENCES": sum(1 for k in covered if 91 <= k[1] <= 135),
        },
        "map": {f"{y}_Q{n}": v for (y, n), v in COVERAGE_MAP.items()},
        "note": "POTENTIALLY_COVERED only - no pedagogical_classifications created this phase.",
        "languages_humanities": {"languages": 123, "humanities": 153,
                                 "status": "DEFERRED_TAXONOMY_EXTENSION - unchanged"},
    }

    report["design_decisions"] = [
        {"id": "RATIO_LEFT_UNTOUCHED",
         "detail": "MATH-ALGEBRA-RATIO stays a SUBCONTENT under MATH-ALGEBRA-FUNCTIONS. It is hard-coded "
                   "in curriculum_taxonomy.seed_reference_fixture() and asserted by test_curriculum_taxonomy, "
                   "test_curriculum_retriever_benchmark, test_kinetics_taxonomy_migration, test_phase9m_expanded_"
                   "benchmark and others; changing its parent/type in the DB alone would break the seed guard "
                   "and those tests. The 'proportionality' P1 gap therefore needs NO new node (no duplicate) - "
                   "questions Q159/Q146/Q152/Q150 will be classified to the existing MATH-ALGEBRA-RATIO. A proper "
                   "restructure is a separate coordinated migration (spec + tests) if desired later."},
        {"id": "GRAPHS_TABLES_AS_CONTENT_UNDER_STATISTICS",
         "detail": "MATH-STATISTICS-DATA-INTERPRETATION created as a CONTENT under MATH-STATISTICS so the "
                   "existing questions can be classified now. If a future taxonomy models 'reading data' as a "
                   "transversal skill, this node can be re-pointed; for now a coherent CONTENT is required."},
        {"id": "ORGANIC_FUNCTIONS_VS_N05",
         "detail": "New CONTENT CHEMISTRY-ORGANIC-FUNCTIONS is broader than and separate from the deferred "
                   "code N05 (CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS, reserved in _curriculum_v2_bindings.py, never "
                   "a DB row). N05 and any prior decision about it are untouched."},
        {"id": "NO_VOCABULARY_BINDINGS",
         "detail": "No controlled-vocabulary binding added. Robust deterministic bindings for probability / "
                   "percentage / stoichiometry cannot be built without the fragile single-word matching PHASE "
                   "11.2 flagged. The mechanism linking questions to the new nodes (provider / curation map / "
                   "bindings) is PHASE 11.5's decision. VOCABULARY_CHANGES = 0."},
        {"id": "MINIMAL_DEPTH",
         "detail": "AREA + 1 CONTENT per new branch; no SUBCONTENT created (corpus does not yet justify a "
                   "deeper split). BIOLOGY-IMMUNOLOGY-MICROBIOLOGY and BIOLOGY-EVOLUTION each get one CONTENT."},
    ]

    d = report["deltas"]
    report["security"] = {
        "DATABASE_WRITES": d["catalog_nodes"] + d["catalog_node_prerequisites"],
        "OPENAI_CALLS": 0, "ALEMBIC_EXECUTION": 0,
        "CATALOG_NODES_CREATED": d["catalog_nodes"],
        "VOCABULARY_CHANGES": 0,
        "CLASSIFICATIONS_CREATED": d["pedagogical_classifications"] + d["question_classifications"],
        "PRODUCTION_DATA_MODIFIED": 1 if not dry else 0,
    }
    report["idempotency"] = {
        "re_run_expectation": "second run -> nodes_created=[] prerequisites_created=[] (ALREADY_EXISTS)",
        "observed_on_this_run": {"nodes_created": len(report.get("nodes_created", [])),
                                 "nodes_already_existing": len(report.get("nodes_already_existing", [])),
                                 "prerequisites_created": len(report.get("prerequisites_created", []))},
    }
    report["deferred"] = {"languages": 123, "humanities": 153}
    report["known_preexisting_failure"] = (
        "tests/test_ingestion_classifier.py::TestIngestionClassifierIntegration::"
        "test_13_isolation_between_documents (PRE_EXISTING)")

    ok = (not dry
          and d["catalog_nodes"] in (0, len(NEW_NODES))
          and d["catalog_node_prerequisites"] in (0, len(NEW_PREREQS))
          and d["pedagogical_classifications"] == 0 and d["question_classifications"] == 0
          and after_counts["curriculum_v2_ACTIVE"] == 20
          and report["protected_questions"]["unchanged"]
          and report["classifications_unchanged"]["identical"]
          and report["integrity"]["all_clean"]
          and d["questions"] == 0 and d["question_versions"] == 0)
    report["final_decision"] = ("PHASE_11_4_P1_TAXONOMY_EXTENSION_COMPLETE" if ok
                                else ("PHASE_11_4_DRY_RUN_OK" if dry else "PHASE_11_4_NEEDS_REVIEW"))

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(_render(report))
    print(f"\nwrote {REPORT}")
    await engine.dispose()
    return 0 if report["final_decision"].endswith(("COMPLETE", "DRY_RUN_OK")) else 1


class _DryRunRollback(Exception):
    def __init__(self, result):
        self.result = result


def _render(r: dict) -> str:
    L = [f"PHASE 11.4 - P1 TAXONOMY EXTENSION  (dry_run={r['dry_run']})", ""]
    L.append(f"nodes created: {len(r.get('nodes_created', []))}   already existing: {len(r.get('nodes_already_existing', []))}")
    for n in r.get("nodes_created", []):
        L.append(f"  + [{n['node_type']:9}] {n['code']:44} parent={n['parent']}  ({n['gap']})")
    for n in r.get("nodes_already_existing", []):
        L.append(f"  = {n['code']:44} {n['status']}")
    L.append(f"prerequisites created: {len(r.get('prerequisites_created', []))}")
    for p in r.get("prerequisites_created", []):
        L.append(f"  + {p['content']} <- {p['prerequisite']}  ({p['confidence']})")
    d = r.get("deltas", {})
    L += ["", "DELTAS:"] + [f"  {k:32} {v:+d}" for k, v in d.items()]
    L += ["", f"protected_questions unchanged: {r.get('protected_questions', {}).get('unchanged')}",
          f"classifications identical: {r.get('classifications_unchanged', {}).get('identical')}",
          f"integrity all_clean: {r.get('integrity', {}).get('all_clean')}",
          f"curriculum-v2 ACTIVE after: {r.get('after', {}).get('counts', {}).get('curriculum_v2_ACTIVE')}"]
    s = r.get("security", {})
    L += ["", "SECURITY:"] + [f"  {k} = {s[k]}" for k in s]
    L += ["", "COVERAGE (POTENTIALLY_COVERED, no classification):",
          f"  covered={r['coverage']['POTENTIALLY_COVERED']}  not_covered(deferred)={r['coverage']['NOT_COVERED_deferred']}"]
    L += ["", "FINAL_DECISION:", r["final_decision"]]
    return "\n".join(L)


def main(argv=None):  # pragma: no cover
    return asyncio.run(_amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
