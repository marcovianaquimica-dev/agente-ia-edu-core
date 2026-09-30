# src/agente_ia_edu/services/mass_correction_driver.py
"""Driver resumivel de correcao em massa (spec 2026-09-29): dado o estado
atual de um MassCorrectionRun, decide a UNICA proxima acao (submeter, ou
so consultar status, ou finalizar) - nunca reenvia um lote que ja tem
openai_batch_id, e por isso pode ser chamado repetidamente (inclusive apos
o processo cair no meio) sem duplicar trabalho nem gastar em dobro."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import MassCorrectionRun
from ..providers.openai_batch_client import create_batch, get_batch, upload_batch_file


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def advance_run(
    session: AsyncSession, run_id: uuid.UUID, *, api_key: str, pending_lines: list[dict] | None = None,
) -> MassCorrectionRun:
    run = await session.get(MassCorrectionRun, run_id)
    if run is None:
        raise ValueError(f"MassCorrectionRun not found: {run_id}")

    if run.openai_batch_id is None:
        if pending_lines is None:
            raise ValueError(
                f"MassCorrectionRun {run_id} has no openai_batch_id yet and no pending_lines "
                "were provided to submit it"
            )
        file_id = await upload_batch_file(pending_lines, api_key=api_key)
        batch = await create_batch(file_id, api_key=api_key)
        run.input_file_id = file_id
        run.openai_batch_id = batch["id"]
        run.status = batch["status"]
        await session.commit()
        return run

    batch = await get_batch(run.openai_batch_id, api_key=api_key)
    run.status = batch["status"]
    if batch["status"] == "completed":
        run.output_file_id = batch.get("output_file_id")
        run.completed_at = _utcnow()
    await session.commit()
    return run
