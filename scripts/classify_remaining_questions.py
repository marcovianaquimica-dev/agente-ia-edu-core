"""Run the real AI classification pipeline over every official ENEM question
that doesn't yet have an ACTIVE curriculum-v2 classification.

Uses AuthorialQuestionClassificationService.classify_question_version (the
shared engine also used by /api/v1/catalog/question-classification's HTTP
routes - "Authorial" in the name is historical, it classifies official
questions the same way). Each question costs 2 real AI calls (one content
classification, one difficulty assessment) against the provider configured
in .env (AI_PROVIDER, default "openai") - real API cost, real money.
Already-classified questions are skipped automatically by the service's own
cache check (0 AI calls, cache_hit=True) - safe to re-run after a partial
failure, it picks up where it left off.

Usage:
    source .env
    export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5433/${POSTGRES_DB}"
    .venv/bin/python scripts/classify_remaining_questions.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.models import QuestionVersion
from agente_ia_edu.providers.factory import build_text_provider
from agente_ia_edu.services.authorial_question_classification_service import (
    AuthorialQuestionClassificationService,
)


async def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    provider = build_text_provider()

    async with session_factory() as session:
        version_ids = (await session.scalars(
            select(QuestionVersion.id).where(QuestionVersion.version_kind == "official_original")
        )).all()

    print(f"[classify] {len(version_ids)} official question(s) in scope (already-classified ones "
          f"are cache-skipped automatically)", flush=True)

    async with session_factory() as session:
        svc = AuthorialQuestionClassificationService(session)
        classified = needs_review = errors = cache_hits = ai_calls = 0
        t0 = time.perf_counter()
        for i, vid in enumerate(version_ids, start=1):
            try:
                outcome = await svc.classify_question_version(vid, provider, actor="admin:classify-batch")
            except Exception as exc:  # noqa: BLE001 - keep going, report at the end
                errors += 1
                print(f"[classify] {i}/{len(version_ids)} {vid} EXCEPTION: {exc}", flush=True)
                # a failed flush/commit leaves the session unusable until rolled
                # back - without this every subsequent item fails immediately
                # (before even the cache-check query), cascading to the end.
                await session.rollback()
                continue
            ai_calls += outcome.ai_calls
            if outcome.cache_hit:
                cache_hits += 1
            elif outcome.error:
                errors += 1
            elif outcome.status == "CLASSIFIED":
                classified += 1
            else:
                needs_review += 1
            if i % 10 == 0 or i == len(version_ids):
                elapsed = time.perf_counter() - t0
                print(
                    f"[classify] {i}/{len(version_ids)} - classified={classified} "
                    f"needs_review={needs_review} errors={errors} cache_hits={cache_hits} "
                    f"ai_calls={ai_calls} elapsed={elapsed:.0f}s",
                    flush=True,
                )

        elapsed = time.perf_counter() - t0
        print(
            f"[classify] DONE. {len(version_ids)} processed in {elapsed:.0f}s - "
            f"classified={classified} needs_review={needs_review} errors={errors} "
            f"cache_hits={cache_hits} total_ai_calls={ai_calls}",
            flush=True,
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
