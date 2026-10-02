"""Tela de status minima da correcao em massa (spec 2026-09-29 Task 8) - so
leitura, agregando MassCorrectionRun por estagio. Sem paginacao/filtro
avancado de proposito: e uma tela de operacao pontual, nao um produto.

Reusa require_platform_admin de routes/admin.py como dependency do FastAPI
(o mesmo padrao ja usado por routes/admin_essay_prompts.py) em vez de
inventar uma checagem propria.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from ..dependencies import get_session_factory
from ...db.models import MassCorrectionRun
from ...identity import ExternalIdentityContext
from .admin import require_platform_admin

mass_correction_status_router = APIRouter(
    prefix="/api/v1/admin/mass-correction-runs", tags=["mass-correction"]
)


@mass_correction_status_router.get("")
async def get_mass_correction_status(
    school_id: uuid.UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    MassCorrectionRun.stage,
                    MassCorrectionRun.status,
                    func.sum(MassCorrectionRun.request_count),
                )
                .where(MassCorrectionRun.school_id == school_id)
                .group_by(MassCorrectionRun.stage, MassCorrectionRun.status)
            )
        ).all()

    result: dict[str, dict[str, int]] = defaultdict(dict)
    for stage, status, total in rows:
        result[stage][status] = int(total)
    return dict(result)


__all__ = ["mass_correction_status_router"]
