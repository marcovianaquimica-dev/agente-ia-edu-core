"""Propostas de redacao da plataforma - lado do PLATFORM_ADMIN (spec
2026-09-29 s2).

Reusa a dependencia require_platform_admin de routes/admin.py (as tres vias
de autorizacao dela - role no token, atalho por external_user_id e vinculo
PLATFORM_ADMIN no banco - ja sao as mesmas que /api/v1/admin/schools usa),
em vez de inventar uma checagem propria: e o padrao exato de autorizacao de
admin que o sistema ja tem.

Prefixo proprio (/api/v1/admin/platform-essay-prompts) e router proprio, e
nao rotas soltas dentro de admin.py, porque este e um dominio diferente do
de multi-tenancy que aquele arquivo cobre.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..dependencies import get_session_factory
from ...identity import ExternalIdentityContext
from ...services.platform_essay_prompt import PlatformEssayPromptService
from .admin import require_platform_admin

admin_essay_prompts_router = APIRouter(
    prefix="/api/v1/admin/platform-essay-prompts",
    tags=["platform-essay-prompts"],
)


class PlatformEssayPromptCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    statement: str = Field(..., min_length=1)


class PlatformEssayPromptResponse(BaseModel):
    id: UUID
    title: str
    statement: str
    status: str
    created_at: datetime
    # Quantas escolas ja materializaram uma copia desta proposta - a metrica
    # de uso simples da tela do admin (spec s2). Uma proposta recem-criada e
    # sempre 0.
    materialized_school_count: int = 0


@admin_essay_prompts_router.post("", status_code=201, response_model=PlatformEssayPromptResponse)
async def create_platform_essay_prompt(
    request: PlatformEssayPromptCreateRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> PlatformEssayPromptResponse:
    async with session_factory() as session:
        service = PlatformEssayPromptService(session)
        prompt = await service.create_prompt(
            title=request.title,
            statement=request.statement,
            created_by_external_identity=identity.external_user_id,
        )
        # Resposta montada ANTES do commit: commit() expira o objeto
        # (expire_on_commit=True em producao) e ler atributos depois disso
        # dispara um lazy-load sincrono que estoura com MissingGreenlet em
        # contexto async - mesmo cuidado de essay_prompts.py.
        response = PlatformEssayPromptResponse(
            id=prompt.id, title=prompt.title, statement=prompt.statement,
            status=prompt.status, created_at=prompt.created_at,
            materialized_school_count=0,
        )
        await session.commit()
        return response


@admin_essay_prompts_router.get("", response_model=list[PlatformEssayPromptResponse])
async def list_platform_essay_prompts(
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> list[PlatformEssayPromptResponse]:
    async with session_factory() as session:
        service = PlatformEssayPromptService(session)
        rows = await service.list_with_materialization_counts()
        return [
            PlatformEssayPromptResponse(
                id=prompt.id, title=prompt.title, statement=prompt.statement,
                status=prompt.status, created_at=prompt.created_at,
                materialized_school_count=count,
            )
            for prompt, count in rows
        ]


@admin_essay_prompts_router.post(
    "/{platform_essay_prompt_id}/archive", response_model=PlatformEssayPromptResponse
)
async def archive_platform_essay_prompt(
    platform_essay_prompt_id: UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> PlatformEssayPromptResponse:
    async with session_factory() as session:
        service = PlatformEssayPromptService(session)
        try:
            prompt = await service.archive_prompt(
                platform_essay_prompt_id=platform_essay_prompt_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # Recontagem depois do arquivamento: a tela mostra a mesma linha
        # atualizada, e arquivar NUNCA muda a contagem (nenhuma copia e
        # tocada) - pegar o valor real aqui e o que prova isso na resposta.
        counts = {p.id: count for p, count in await service.list_with_materialization_counts()}
        response = PlatformEssayPromptResponse(
            id=prompt.id, title=prompt.title, statement=prompt.statement,
            status=prompt.status, created_at=prompt.created_at,
            materialized_school_count=counts.get(prompt.id, 0),
        )
        await session.commit()
        return response


__all__ = ["admin_essay_prompts_router"]
