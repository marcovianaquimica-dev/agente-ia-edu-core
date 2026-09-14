# tests/test_r0_fase3a_gate.py
"""Proves this phase delivers a service and changes nothing else.

Spec section 7 orders the migration: the resolver ships alone, tested, before
any consumer uses it. That ordering exists because the last consumer to migrate
is authorization, and an error there does not crash - it shows the wrong data
to the wrong person. This file is what keeps the ordering honest.
"""

import pathlib
import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.services.external_id_resolution import (
    ExternalIdResolver,
    ResolutionState,
)

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "agente_ia_edu"


class Fase3AGateTests(unittest.IsolatedAsyncioTestCase):
    def test_the_resolver_has_no_consumer_yet(self):
        """The phase's central claim. Consumers arrive in 3B, one at a time,
        each with its own commit and test - not as a side effect of this one."""
        importers = []
        for path in SRC.rglob("*.py"):
            if path.name == "external_id_resolution.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "external_id_resolution" in text or "ExternalIdResolver" in text:
                importers.append(str(path.relative_to(SRC)))
        self.assertEqual(
            importers, [], f"o resolvedor ganhou consumidor antes da Fase 3B: {importers}"
        )

    async def test_the_resolver_works_at_all(self):
        """A gate asserting only absence would pass if the service were broken
        or missing entirely, so it also has to prove the thing exists and runs."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                found = await ExternalIdResolver(session).resolve(
                    uuid.uuid4(), "CLASSROOM", "TURMA-INEXISTENTE"
                )
            self.assertEqual(found.state, ResolutionState.NOT_FOUND)
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
