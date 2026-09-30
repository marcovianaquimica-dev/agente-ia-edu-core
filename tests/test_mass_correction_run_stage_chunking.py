# tests/test_mass_correction_run_stage_chunking.py
"""Task 12: prova que run_stage fraciona lotes grandes demais no ponto de
criar uma run nova, em vez de submeter TODO o trabalho pendente de uma vez
(achado CRITICO C2 da revisao final de todo o branch - ver
.superpowers/sdd/2026-09-29-correcao-em-massa-rede-estadual/task-12-brief.md).

Arquivo separado de tests/test_mass_correction_driver.py porque o alvo aqui e
outro: run_stage (scripts/run_mass_correction.py), nao advance_run
(services/mass_correction_driver.py) - os pontos de mock sao diferentes
(_COLLECTORS e advance_run do modulo do script, nao upload_batch_file/
create_batch/get_batch do modulo do servico), entao criar um arquivo novo,
focado, evita reaproveitar mocks de um alvo diferente so por coincidencia de
nome."""
from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import MassCorrectionRun, School

from scripts.run_mass_correction import _MAX_REQUESTS_PER_BATCH, run_stage


async def _fake_advance_run(session, run_id, *, api_key, pending_lines=None):
    """Substitui o advance_run de verdade (que chamaria a Batch API) por uma
    versao que so grava no banco o que run_stage precisa ler de volta:
    openai_batch_id quando esta submetendo um lote novo, ou um status
    'em voo' quando esta so consultando uma run existente."""
    run = await session.get(MassCorrectionRun, run_id)
    if pending_lines is not None:
        run.openai_batch_id = f"batch-{run.id}"
        run.status = "validating"
    else:
        run.status = "in_progress"
    await session.commit()
    return run


class RunStageChunkingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_school(self, session) -> uuid.UUID:
        school = School(id=uuid.uuid4(), code="T12", name="Escola T12")
        session.add(school)
        await session.flush()
        await session.commit()
        return school.id

    async def test_run_stage_submits_only_the_first_fraction_when_pending_work_exceeds_the_cap(self):
        total_pending = _MAX_REQUESTS_PER_BATCH + 1
        fake_lines = [{"custom_id": f"id-{i}"} for i in range(total_pending)]

        async with self.session_factory() as session:
            school_id = await self._seed_school(session)

            with (
                patch.dict(
                    "scripts.run_mass_correction._COLLECTORS",
                    {"OCR": AsyncMock(return_value=fake_lines)},
                ),
                patch("scripts.run_mass_correction.advance_run", new=_fake_advance_run),
            ):
                await run_stage(session, school_id=school_id, stage="OCR", api_key="sk-test")

            runs = (
                await session.execute(
                    select(MassCorrectionRun).where(
                        MassCorrectionRun.school_id == school_id, MassCorrectionRun.stage == "OCR"
                    )
                )
            ).scalars().all()
            self.assertEqual(len(runs), 1)
            run = runs[0]
            self.assertEqual(run.sequence_number, 1)
            self.assertLess(run.request_count, total_pending)
            self.assertEqual(run.request_count, _MAX_REQUESTS_PER_BATCH)
            self.assertIsNotNone(run.openai_batch_id)

    async def test_a_second_call_while_the_first_fraction_is_still_in_flight_does_not_create_another_run(self):
        total_pending = _MAX_REQUESTS_PER_BATCH + 1
        fake_lines = [{"custom_id": f"id-{i}"} for i in range(total_pending)]

        async with self.session_factory() as session:
            school_id = await self._seed_school(session)
            collector = AsyncMock(return_value=fake_lines)

            with (
                patch.dict("scripts.run_mass_correction._COLLECTORS", {"OCR": collector}),
                patch("scripts.run_mass_correction.advance_run", new=_fake_advance_run),
            ):
                await run_stage(session, school_id=school_id, stage="OCR", api_key="sk-test")
                # Chamada seguinte: a run recem-criada esta "validating" (em voo,
                # nao-terminal) - run_stage deve so consultar o status dela, nunca
                # coletar pendencias de novo nem criar uma segunda run.
                await run_stage(session, school_id=school_id, stage="OCR", api_key="sk-test")

            runs = (
                await session.execute(
                    select(MassCorrectionRun).where(
                        MassCorrectionRun.school_id == school_id, MassCorrectionRun.stage == "OCR"
                    )
                )
            ).scalars().all()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].status, "in_progress")
            # A coleta de pendencias so deve ter acontecido na primeira chamada.
            self.assertEqual(collector.await_count, 1)


if __name__ == "__main__":
    unittest.main()
