"""PHASE 9U.2-A - strictly READ-ONLY investigation of one PedagogicalClassification.

Target: the row whose id starts with ``18b8666b`` that the 9U.2 inventory
pre-flight flagged as ``ACTIVE mode=None``.

Run from the operator terminal (DATABASE_URL configured):

    cd /Users/marcoviana/agente-ia-edu-core
    PYTHONPATH=src .venv/bin/python tests/manual/phase9u2a_investigate_row.py

Guarantees by construction:
  * one PostgreSQL session / one transaction,
  * ``SET TRANSACTION READ ONLY`` as the very first statement,
  * SELECT only afterwards - no INSERT/UPDATE/DELETE/MERGE, no session.add,
    no flush/commit of data, no DDL, no Alembic, no provider, no OpenAI,
  * nothing is modified, nothing is "fixed".

Sanitised output: hashes as 12-char prefixes; question text / statement /
evidence truncated. No secrets are read or printed.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ID_PREFIX = "18b8666b"
TARGET_TAXONOMY = "024_chemistry_kinetics"
PROTECTED_OFFICIAL_NUMBERS = {93, 128}

ENV_FILE = Path(".env")
for _cand in (Path.cwd(), Path("/Users/marcoviana/agente-ia-edu-core")):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        ENV_FILE = _cand / ".env"
        break

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from agente_ia_edu.db.models import (  # noqa: E402
    BookletQuestion,
    PedagogicalClassification,
    QuestionVersion,
)
from agente_ia_edu.db.session import create_engine, create_session_factory  # noqa: E402


def _hp(v) -> str:
    if v is None:
        return "None"
    s = str(v)
    return (s[:12] + "…") if len(s) > 12 else s


def _short(v, n: int = 60):
    if not isinstance(v, str):
        return v
    return f"<{len(v)} chars> {v[:n]!r}…" if len(v) > n else v


_HASHY = {"input_hash", "output_hash", "question_content_hash", "content_hash"}


def _sanitize_metadata(md) -> dict:
    if not isinstance(md, dict):
        return {"<raw>": _short(str(md))}
    out = {}
    for k, v in md.items():
        if k in _HASHY:
            out[k] = _hp(v)
        elif k == "evidence" and isinstance(v, list):
            out[k] = [
                {ek: (_short(ev.get(ek)) if isinstance(ev, dict) else ev) for ek in (ev or {})}
                for ev in v
            ]
        elif isinstance(v, str):
            out[k] = _short(v)
        elif isinstance(v, list):
            out[k] = [_short(x) for x in v]
        else:
            out[k] = v
    return out


async def _run() -> int:
    load_dotenv(ENV_FILE, override=False)
    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                await session.execute(text("SET TRANSACTION READ ONLY"))

                alembic_rev = (
                    await session.execute(text("SELECT version_num FROM alembic_version"))
                ).scalars().first()

                matches = list(
                    (
                        await session.scalars(
                            select(PedagogicalClassification).where(
                                text("CAST(pedagogical_classifications.id AS text) LIKE :p")
                            ).params(p=f"{ID_PREFIX}%")
                        )
                    ).all()
                )
                if len(matches) != 1:
                    print("PHASE 9U.2-A — UNEXPECTED CLASSIFICATION INVESTIGATION")
                    print(f"TARGET_CLASSIFICATION_ID: NOT_VERIFIABLE (matched {len(matches)} rows for prefix {ID_PREFIX!r})")
                    print("FINAL_DECISION:")
                    print("PHASE_9U2_A_INVESTIGATION_COMPLETE")
                    return 1
                row = matches[0]

                version = await session.get(QuestionVersion, row.question_version_id)
                bqs = list(
                    (
                        await session.scalars(
                            select(BookletQuestion).where(
                                BookletQuestion.question_version_id == row.question_version_id
                            )
                        )
                    ).all()
                )

                history = list(
                    (
                        await session.scalars(
                            select(PedagogicalClassification)
                            .where(
                                PedagogicalClassification.question_version_id
                                == row.question_version_id
                            )
                            .order_by(PedagogicalClassification.created_at)
                        )
                    ).all()
                )

                # global cross-checks for this question_version
                every_ids = set(
                    (await session.scalars(select(PedagogicalClassification.id))).all()
                )
    finally:
        await engine.dispose()

    md = row.metadata_ or {}
    taxo = md.get("taxonomy_version")
    mode = md.get("classification_mode")

    active_rows = [r for r in history if r.lifecycle == "ACTIVE"]
    superseded_rows = [r for r in history if r.lifecycle == "SUPERSEDED"]
    active_same_taxo = [
        r
        for r in history
        if r.lifecycle == "ACTIVE" and (r.metadata_ or {}).get("taxonomy_version") == taxo
    ]
    uniqueness_conflict = taxo is not None and len(active_same_taxo) > 1

    official_numbers = sorted({b.official_number for b in bqs if b.official_number is not None})
    is_protected = bool(set(official_numbers) & PROTECTED_OFFICIAL_NUMBERS)

    orphan_supersedes = row.supersedes_id is not None and row.supersedes_id not in every_ids

    # ---------- report ----------
    print("PHASE 9U.2-A — UNEXPECTED CLASSIFICATION INVESTIGATION")
    print()
    print(f"ALEMBIC_REVISION: {alembic_rev}")
    print(f"TARGET_CLASSIFICATION_ID: {row.id}")
    print(f"TARGET_QUESTION_VERSION_ID: {row.question_version_id}")
    print(f"OFFICIAL_NUMBER: {official_numbers if official_numbers else 'NONE (no booklet_question with official_number)'}")
    print()
    print("--- A. identity ---")
    for attr in (
        "discipline", "content", "subcontent", "difficulty",
        "classification_confidence", "difficulty_confidence", "reasoning_type",
        "status", "source", "created_at",
        "model_name", "model_version", "prompt_version", "provider_name",
        "input_tokens", "output_tokens", "total_tokens",
        "lifecycle", "supersedes_id",
    ):
        print(f"  {attr}: {getattr(row, attr, 'NOT_VERIFIABLE')}")
    print()
    print("--- B. metadata (sanitised) ---")
    print(json.dumps(_sanitize_metadata(md), indent=2, ensure_ascii=True, sort_keys=True, default=str))
    print(f"  => taxonomy_version: {taxo!r}")
    print(f"  => classification_mode: {mode!r}")
    print(f"  => input_hash present: {'input_hash' in md}   output_hash present: {'output_hash' in md}   question_content_hash present: {'question_content_hash' in md}")
    print()
    print("--- C. question_version + booklet ---")
    if version is None:
        print("  question_version: NOT_FOUND (dangling question_version_id)")
    else:
        print(f"  id: {version.id}")
        print(f"  question_id: {version.question_id}")
        print(f"  version_kind: {version.version_kind}")
        print(f"  canonical_text: {_short(version.canonical_text)}")
        print(f"  statement: {_short(version.statement)}")
        print(f"  content_hash: {_hp(version.content_hash)}")
        print(f"  recommended_difficulty: {version.recommended_difficulty}")
    for b in bqs:
        print(f"  booklet_question: id={_hp(b.id)} exam_booklet_id={_hp(b.exam_booklet_id)} official_number={b.official_number}")
    if not bqs:
        print("  booklet_question: NONE")
    print()
    print("--- D. full classification history for this question_version (created_at ASC) ---")
    for r in history:
        rmd = r.metadata_ or {}
        print(
            f"  id={_hp(r.id)} lifecycle={r.lifecycle} content={r.content} status={r.status} "
            f"model_version={r.model_version} prompt_version={r.prompt_version} "
            f"taxonomy_version={rmd.get('taxonomy_version')!r} classification_mode={rmd.get('classification_mode')!r} "
            f"supersedes_id={_hp(r.supersedes_id)} source={r.source} created_at={r.created_at}"
        )
    print()
    print("--- E/F/G. computed ---")
    print(f"HISTORY_ROWS: {len(history)}")
    print(f"ACTIVE_ROWS: {len(active_rows)}")
    print(f"SUPERSEDED_ROWS: {len(superseded_rows)}")
    print(f"ACTIVE_ROWS_SAME_TAXONOMY_VERSION({taxo!r}): {len(active_same_taxo)}")
    print(f"UNIQUENESS_CONFLICT: {'YES' if uniqueness_conflict else 'NO'}")
    print(f"HAS_SUPERSEDES_ID: {'YES' if row.supersedes_id is not None else 'NO'}")
    print(f"ORPHAN_SUPERSEDES_ID: {'YES' if orphan_supersedes else 'NO'}")
    print(f"Q93_OR_Q128: {'YES' if is_protected else 'NO'}")
    print(f"TARGET_IS_KINETICS_TAXONOMY: {'YES' if taxo == TARGET_TAXONOMY else 'NO'}")
    print(f"IN_9U2_OFFICIAL_SCOPE: {'YES' if official_numbers else 'NO (not an official booklet question)'}")
    print()
    print("DATABASE_WRITES: 0")
    print("OPENAI_CALLS: 0")
    print("ALEMBIC_EXECUTION: 0")
    print()
    print("FINAL_DECISION:")
    print("PHASE_9U2_A_INVESTIGATION_COMPLETE")
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception as exc:  # fail closed
        import os

        msg = str(exc)
        for key in ("DATABASE_URL", "OPENAI_API_KEY"):
            secret = os.getenv(key)
            if secret:
                msg = msg.replace(secret, "[REDACTED]")
        print("PHASE 9U.2-A — UNEXPECTED CLASSIFICATION INVESTIGATION")
        print(f"FATAL: {type(exc).__name__}: {msg[:300]}")
        print()
        print("FINAL_DECISION:")
        print("PHASE_9U2_A_INVESTIGATION_INCOMPLETE")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
