# tests/test_r0_fase3a_gate.py
"""Proves this phase delivers a service that actually works.

The absence check that used to live here - proving the resolver had no
consumer yet - was retired in Fase 3B. It asserted a claim that was only ever
true through the end of Fase 3A; Fase 3B's job is to wire the resolver into
its first consumer (``coordination_portal.py``), which makes that claim false
on purpose. Keeping the gate would fail the very phase it was written to
protect for doing exactly what the spec ordered.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.services.external_id_resolution import (
    ExternalIdResolver,
    ResolutionState,
)


class Fase3AGateTests(unittest.IsolatedAsyncioTestCase):
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
