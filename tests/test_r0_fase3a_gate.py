# tests/test_r0_fase3a_gate.py
"""Proves this phase delivers a service that actually works.

The absence check that used to live here - proving the resolver had no
consumer yet - was retired in Fase 3B. It asserted a claim that was only ever
true through the end of Fase 3A; Fase 3B's job is to wire the resolver into
its first consumer (``coordination_portal.py``), which makes that claim false
on purpose. Keeping the gate would fail the very phase it was written to
protect for doing exactly what the spec ordered.

What replaced it is a named allowlist, not another absence check - see
``test_the_resolver_has_exactly_its_known_consumers``. The scan itself (same
directories, same walk) is the one commit 2161f58 had just hardened after a
probe proved an import planted in ``scripts/seed_demo_data.py`` left the old
gate green; only the assertion changed.
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


ROOT = pathlib.Path(__file__).resolve().parent.parent

# Every directory of this repository where a consumer could actually live.
# ``src`` alone is not enough: ``scripts/seed_demo_data.py`` already imports
# from ``agente_ia_edu.services`` today, and a review probe proved a real
# import there left the retired gate green. ``tests`` is deliberately absent -
# the resolver's own tests, this file included, are legitimate importers.
SCANNED_DIRS = (
    pathlib.Path("src") / "agente_ia_edu",
    pathlib.Path("scripts"),
    pathlib.Path("tools"),
    pathlib.Path("migrations"),
)

# The consumers Fase 3B (wave 1) migrated, by name. Fase 3C - and any later
# wave - adds its own consumers here, as a step of that phase's own plan.
KNOWN_CONSUMERS = {
    "src/agente_ia_edu/services/coordination_portal.py",
    "src/agente_ia_edu/services/teacher_portal.py",
}


class Fase3AGateTests(unittest.IsolatedAsyncioTestCase):
    def test_the_resolver_has_exactly_its_known_consumers(self):
        """Replaces Fase 3A's retired absence check with a named allowlist.

        The old assertion - nobody imports the resolver - was true only until
        Fase 3B wired in the first consumer, so it had to go. But nothing took
        its place, and an unintended third consumer would then land with no
        test noticing. This asserts equality, not absence: the scan must find
        exactly the two files this wave migrated. A new consumer fails here by
        name, and whoever plans the phase that adds it updates KNOWN_CONSUMERS
        as a step of that plan - which is the point, since §7 calls the
        migration order non-negotiable and every consumer is supposed to
        arrive with its own commit and its own test.
        """
        importers = set()
        searched = []
        for relative in SCANNED_DIRS:
            directory = ROOT / relative
            if not directory.is_dir():
                continue
            searched.append(str(relative))
            for path in directory.rglob("*.py"):
                if path.name == "external_id_resolution.py":
                    continue
                text = path.read_text(encoding="utf-8")
                if "external_id_resolution" in text or "ExternalIdResolver" in text:
                    importers.add(path.relative_to(ROOT).as_posix())
        self.assertEqual(
            searched,
            [str(d) for d in SCANNED_DIRS],
            f"diretório varrido pelo gate desapareceu da árvore: {searched}",
        )
        self.assertEqual(
            importers,
            KNOWN_CONSUMERS,
            "a lista de consumidores do resolvedor mudou sem que a lista-branca "
            f"deste gate fosse atualizada: encontrado {sorted(importers)}, "
            f"esperado {sorted(KNOWN_CONSUMERS)} (varrido: {searched}; 'tests' "
            "fica de fora de propósito, porque os testes do próprio resolvedor "
            "o importam). Se este é um consumidor novo e intencional, some-o a "
            "KNOWN_CONSUMERS no passo do plano que o introduz.",
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
