"""PHASE 9U.2-G5 — controlled creation of the curriculum-v2 catalog nodes.

Run from the operator terminal, with the PRODUCTION DATABASE_URL configured:

    cd /Users/marcoviana/agente-ia-edu-core
    # dry-run (default):
    PYTHONPATH=src .venv/bin/python tests/manual/phase9u2g5_catalog.py
    # authorised write:
    PHASE9U2_G5_DRY_RUN=false \
    PHASE9U2_G5_CATALOG_REVIEWED='PHASE9U2_G5_CATALOG_REVIEWED' \
    PYTHONPATH=src .venv/bin/python tests/manual/phase9u2g5_catalog.py

Creates ONLY the 11 new AREAs + 19 new CONTENTs from
``agente_ia_edu.services._curriculum_v2_bindings`` (N05 is deferred). The
RetrievalVocabularyEntry / DeterministicInitialBinding objects are already in the
code registry (Phase 9U.2-G5 source change) and become live the moment their
node exists.

Guarantees by construction:
  * a read-only pre-flight (``SET TRANSACTION READ ONLY`` first) and a read-only
    post-audit;
  * one write transaction; rollback on any error; idempotent (existing +
    compatible node -> SKIP, incompatible -> STOP);
  * NO classification, NO Batch-0, NO provider, NO OpenAI, NO Alembic,
    NO supersession, NO reclassification;
  * Q91 / Q93 / Q128 read only for the immutability check, never changed;
  * dry-run unless ``PHASE9U2_G5_DRY_RUN`` is exactly "false" AND
    ``PHASE9U2_G5_CATALOG_REVIEWED`` is present.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _cand in (Path.cwd(), _HERE.parents[1]):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        _ENV_FILE = _cand / ".env"
        break
else:  # pragma: no cover
    _ENV_FILE = Path(".env")

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402

from agente_ia_edu.db.models import BookletQuestion, CatalogNode, PedagogicalClassification, QuestionVersion  # noqa: E402
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService  # noqa: E402
import agente_ia_edu.services.curriculum_classification as cc  # noqa: E402
from agente_ia_edu.services._curriculum_v2_bindings import (  # noqa: E402
    DEFERRED_CONTENTS,
    NEW_AREAS,
    NEW_CONTENTS,
    TAXONOMY_VERSION,
    build_curriculum_v2_bindings,
)

DRY_RUN_ENV = "PHASE9U2_G5_DRY_RUN"
APPROVAL_ENV = "PHASE9U2_G5_CATALOG_REVIEWED"
PROTECTED_OFFICIAL_NUMBERS = (91, 93, 128)
Q107 = 107


def is_dry_run() -> bool:
    return os.environ.get(DRY_RUN_ENV, "true").strip().lower() != "false"


def approved() -> bool:
    return bool(os.environ.get(APPROVAL_ENV, "").strip())


def _hp(v) -> str:
    if v is None:
        return "None"
    s = str(v)
    return (s[:12] + "…") if len(s) > 12 else s


class G5Stop(RuntimeError):
    pass


async def _protected_fingerprint(session) -> dict[int, tuple]:
    out: dict[int, tuple] = {}
    for number in sorted(PROTECTED_OFFICIAL_NUMBERS + (Q107,)):
        qv_ids = {
            b.question_version_id
            for b in (await session.scalars(select(BookletQuestion).where(BookletQuestion.official_number == number))).all()
        }
        keys: list[tuple] = []
        for qv_id in sorted(qv_ids, key=str):
            for r in (
                await session.scalars(
                    select(PedagogicalClassification)
                    .where(PedagogicalClassification.question_version_id == qv_id)
                    .order_by(PedagogicalClassification.created_at)
                )
            ).all():
                md = r.metadata_ or {}
                keys.append((str(r.id), r.lifecycle, r.content, md.get("taxonomy_version"),
                             md.get("classification_mode"), str(r.supersedes_id) if r.supersedes_id else None, r.status))
        out[number] = tuple(sorted(keys))
    return out


async def _read_catalog(session) -> dict[str, CatalogNode]:
    return {n.code: n for n in (await session.scalars(select(CatalogNode))).all() if n.code}


def _plan(existing: dict[str, CatalogNode]) -> tuple[list, list, list]:
    """Return (to_create, to_skip, conflicts) for AREAs then CONTENTs."""
    to_create, to_skip, conflicts = [], [], []
    wanted = [(c, n, p, "AREA") for c, n, p in NEW_AREAS] + [(c, n, p, "CONTENT") for c, n, p in NEW_CONTENTS]
    for code, name, parent_code, node_type in wanted:
        node = existing.get(code)
        if node is None:
            to_create.append((code, name, parent_code, node_type))
            continue
        parent = next((x for x in existing.values() if x.id == node.parent_id), None)
        ok = (
            node.node_type == node_type
            and node.active
            and parent is not None
            and parent.code == parent_code
        )
        (to_skip if ok else conflicts).append((code, name, parent_code, node_type, node))
    return to_create, to_skip, conflicts


def _registry_consistency() -> tuple[bool, list[str]]:
    problems: list[str] = []
    bindings = {b.canonical_code: b for b in build_curriculum_v2_bindings(cc.RetrievalVocabularyEntry, cc.DeterministicInitialBinding)}
    content_codes = {c for c, _n, _p in NEW_CONTENTS}
    if set(bindings) != content_codes:
        problems.append(f"registry bindings {sorted(set(bindings) ^ content_codes)} differ from NEW_CONTENTS")
    for code, b in bindings.items():
        if (TAXONOMY_VERSION, code) not in cc._DETERMINISTIC_INITIAL_BINDINGS:
            problems.append(f"binding ({TAXONOMY_VERSION}, {code}) not in the live registry")
        if b.vocabulary.canonical_code != code:
            problems.append(f"{code}: vocabulary.canonical_code mismatch")
    for d in DEFERRED_CONTENTS:
        if (TAXONOMY_VERSION, d) in cc._DETERMINISTIC_INITIAL_BINDINGS:
            problems.append(f"deferred content {d} is unexpectedly registered")
    return (not problems), problems


async def _run() -> int:
    load_dotenv(_ENV_FILE, override=False)
    dry = is_dry_run()
    do_write = (not dry) and approved()

    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    created_areas = created_contents = skipped = 0
    try:
        # ---- PRE-FLIGHT (read only) ----
        async with factory() as session:
            async with session.begin():
                if session.bind.dialect.name == "postgresql":
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                total_nodes_before = int(await session.scalar(select(func.count()).select_from(CatalogNode)))
                total_cls_before = int(await session.scalar(select(func.count()).select_from(PedagogicalClassification)))
                existing = await _read_catalog(session)
                protected_before = await _protected_fingerprint(session)

        reg_ok, reg_problems = _registry_consistency()
        missing_area_parents = [p for _c, _n, p in NEW_AREAS if p not in existing]
        to_create, to_skip, conflicts = _plan(existing)
        # content parents that are neither existing nor in the AREA create-set
        area_codes = {c for c, _n, _p in NEW_AREAS}
        bad_content_parents = [
            (c, p) for c, _n, p, _t in [(x[0], x[1], x[2], x[3]) for x in to_create if x[3] == "CONTENT"]
            if p not in existing and p not in area_codes
        ]

        print("PHASE 9U.2-G5 — CATALOG CREATION")
        print()
        print(f"DRY_RUN: {dry}   APPROVAL_PRESENT: {approved()}   WILL_WRITE: {do_write}")
        print(f"TOTAL_CATALOG_NODES (before): {total_nodes_before}")
        print(f"TOTAL_CLASSIFICATIONS (before): {total_cls_before}")
        print(f"REGISTRY_CONSISTENCY: {'PASS' if reg_ok else 'FAIL'}  {reg_problems or ''}")
        print(f"MISSING_AREA_PARENTS: {missing_area_parents or 'none'}")
        print(f"CONTENT_PARENTS_NOT_PLANNED: {bad_content_parents or 'none'}")
        print()
        print(f"CREATE ({len(to_create)}):")
        for code, _n, parent, node_type in to_create:
            print(f"  {node_type:<7} {code}   parent={parent}")
        print(f"SKIP / EXISTING ({len(to_skip)}):")
        for code, *_ in to_skip:
            print(f"  {code}")
        print(f"CONFLICTS ({len(conflicts)}):")
        for code, _n, parent, node_type, node in conflicts:
            print(f"  {code}: have node_type={node.node_type} active={node.active} parent_id={_hp(node.parent_id)}; want {node_type} under {parent}")
        print(f"DEFERRED (never created here): {list(DEFERRED_CONTENTS)}")
        print(f"PROTECTED (read-only fingerprint captured): {sorted(PROTECTED_OFFICIAL_NUMBERS)} + Q{Q107}")
        print()

        if not reg_ok or missing_area_parents or bad_content_parents or conflicts:
            print("STOP — pre-flight found a blocker; nothing written.")
            print("\nFINAL_DECISION:\nPHASE_9U2_G5_NEEDS_REVIEW")
            return 1

        if not do_write:
            print("DRY-RUN ONLY — set PHASE9U2_G5_DRY_RUN=false and PHASE9U2_G5_CATALOG_REVIEWED to write.")
            print("\nCATALOG_NODES_CREATED: 0\nCLASSIFICATIONS_CREATED: 0\nDATABASE_WRITES: 0\nOPENAI_CALLS: 0\nALEMBIC_EXECUTION: 0")
            print("\nFINAL_DECISION:\nPHASE_9U2_G5_DRY_RUN_COMPLETE")
            return 0

        # ---- WRITE (single transaction) ----
        async with factory() as session:
            svc = CurriculumTaxonomyService(session)
            by_code = await _read_catalog(session)
            try:
                pos = 90
                for code, name, parent_code, node_type in to_create:
                    if node_type != "AREA":
                        continue
                    node = await svc.create_node(name, "AREA", code, by_code[parent_code].id, pos)
                    by_code[code] = node
                    created_areas += 1
                    pos += 1
                for code, name, parent_code, node_type in to_create:
                    if node_type != "CONTENT":
                        continue
                    node = await svc.create_node(name, "CONTENT", code, by_code[parent_code].id, 1)
                    by_code[code] = node
                    created_contents += 1
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        skipped = len(to_skip)

        # ---- POST-AUDIT (read only) ----
        async with factory() as session:
            async with session.begin():
                if session.bind.dialect.name == "postgresql":
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                after = await _read_catalog(session)
                total_cls_after = int(await session.scalar(select(func.count()).select_from(PedagogicalClassification)))
                protected_after = await _protected_fingerprint(session)
                audit_problems: list[str] = []
                for code, _n, parent, node_type in [(c, n, p, "AREA") for c, n, p in NEW_AREAS] + [(c, n, p, "CONTENT") for c, n, p in NEW_CONTENTS]:
                    node = after.get(code)
                    if node is None or node.node_type != node_type or not node.active:
                        audit_problems.append(f"{code}: missing/incompatible")
                        continue
                    parent_node = next((x for x in after.values() if x.id == node.parent_id), None)
                    if parent_node is None or parent_node.code != parent:
                        audit_problems.append(f"{code}: parent != {parent}")
                for d in DEFERRED_CONTENTS:
                    if d in after:
                        audit_problems.append(f"deferred {d} was created")
                if total_cls_after != total_cls_before:
                    audit_problems.append(f"classification count changed {total_cls_before} -> {total_cls_after}")
                if protected_after != protected_before:
                    audit_problems.append("Q91/Q93/Q128/Q107 fingerprint changed")
    finally:
        await engine.dispose()

    print(f"AREAS_CREATED: {created_areas}")
    print(f"CONTENTS_CREATED: {created_contents}")
    print(f"DUPLICATES_SKIPPED: {skipped}")
    print(f"POST_CREATION_VALIDATION: {'PASS' if not audit_problems else 'FAIL'}  {audit_problems or ''}")
    print("CLASSIFICATIONS_CREATED: 0")
    print(f"DATABASE_WRITES: {created_areas + created_contents}")
    print("OPENAI_CALLS: 0")
    print("ALEMBIC_EXECUTION: 0")
    print("\nFINAL_DECISION:")
    print("PHASE_9U2_G5_COMPLETE" if not audit_problems else "PHASE_9U2_G5_NEEDS_REVIEW")
    return 0 if not audit_problems else 1


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception as exc:  # fail closed
        msg = str(exc)
        for key in ("DATABASE_URL", "OPENAI_API_KEY"):
            secret = os.getenv(key)
            if secret:
                msg = msg.replace(secret, "[REDACTED]")
        print("PHASE 9U.2-G5 — CATALOG CREATION")
        print(f"FATAL: {type(exc).__name__}: {msg[:300]}")
        print("\nFINAL_DECISION:\nPHASE_9U2_G5_NEEDS_REVIEW")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
