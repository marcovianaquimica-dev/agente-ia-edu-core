# scripts/seed_essay_rubric.py
"""Idempotent seed for the ENEM 2025 essay-correction rubric.

Loads rubrics/enem_2025.yaml (R1) via EssayRubricSeeder. Without this,
EssayCorrectionService's load_rubric_view() call fails with "Unknown rubric
version" for every correction attempt, in every environment whose database
has never had this run against it. Safe to re-run: EssayRubricSeeder.seed()
is a no-op if the version is already present (see its own module docstring
for what "already present" requires - a complete seed, not a bare header
row).

Usage:
    source .env
    export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5433/${POSTGRES_DB}"
    .venv/bin/python scripts/seed_essay_rubric.py
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.rubrics.loader import load_rubric_file
from agente_ia_edu.services.essay_rubric_seed import EssayRubricSeeder


async def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    rubric_file = load_rubric_file("enem_2025")
    async with session_factory() as session:
        rubric = await EssayRubricSeeder(session).seed(rubric_file)
        await session.commit()
        print(f"Rubric {rubric.rubric_version!r} (id={rubric.id}, status={rubric.status}) is seeded.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
