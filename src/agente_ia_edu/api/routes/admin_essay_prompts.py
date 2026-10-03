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

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..dependencies import get_session_factory
from ...identity import ExternalIdentityContext
from ...services.material_storage import MaterialStorage
from ...services.platform_essay_prompt import PlatformEssayPromptService
from .admin import require_platform_admin

admin_essay_prompts_router = APIRouter(
    prefix="/api/v1/admin/platform-essay-prompts",
    tags=["platform-essay-prompts"],
)

# Mesmo teto que essay_prompts.py's upload de material de proposta normal ja
# usa - nao ha motivo pra permitir mais aqui.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


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
    # Quantos materiais de apoio (texto motivador) ja foram anexados. Uma
    # proposta recem-criada e sempre 0.
    material_count: int = 0


class PlatformPromptMaterialResponse(BaseModel):
    id: UUID
    platform_essay_prompt_id: UUID
    material_type: str
    content: Optional[str] = None
    storage_uri: Optional[str] = None
    position: int


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
        material_counts = await service.count_materials_by_prompt()
        return [
            PlatformEssayPromptResponse(
                id=prompt.id, title=prompt.title, statement=prompt.statement,
                status=prompt.status, created_at=prompt.created_at,
                materialized_school_count=count,
                material_count=material_counts.get(prompt.id, 0),
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


@admin_essay_prompts_router.post(
    "/{platform_essay_prompt_id}/unarchive", response_model=PlatformEssayPromptResponse
)
async def unarchive_platform_essay_prompt(
    platform_essay_prompt_id: UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> PlatformEssayPromptResponse:
    async with session_factory() as session:
        service = PlatformEssayPromptService(session)
        try:
            prompt = await service.unarchive_prompt(
                platform_essay_prompt_id=platform_essay_prompt_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        counts = {p.id: count for p, count in await service.list_with_materialization_counts()}
        response = PlatformEssayPromptResponse(
            id=prompt.id, title=prompt.title, statement=prompt.statement,
            status=prompt.status, created_at=prompt.created_at,
            materialized_school_count=counts.get(prompt.id, 0),
        )
        await session.commit()
        return response


@admin_essay_prompts_router.post(
    "/{platform_essay_prompt_id}/materials/upload",
    status_code=201,
    response_model=PlatformPromptMaterialResponse,
)
async def upload_platform_prompt_material(
    platform_essay_prompt_id: UUID,
    position: int = Form(...),
    file: UploadFile = File(...),
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> PlatformPromptMaterialResponse:
    """Anexa um arquivo (texto motivador) a uma proposta da plataforma.
    Mirrors essay_prompts.py's upload_prompt_material: streama pra um
    arquivo temporario limitado ANTES de entregar pro MaterialStorage (o
    mesmo armazenamento content-addressed que o resto do sistema ja usa),
    pra nunca deixar um upload ruim/gigante virar arquivo parcial no
    storage gerenciado."""
    async with session_factory() as session:
        service = PlatformEssayPromptService(session)
        # Falha rapido ANTES de gastar banda/disco com o upload - mesma
        # ordem que essay_prompts.py's upload_prompt_material ja segue
        # (checar o pai antes de processar qualquer byte do arquivo).
        if await service.get_platform_prompt(platform_essay_prompt_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"PlatformEssayPrompt not found: {platform_essay_prompt_id}",
            )

        tmp_dir = Path(tempfile.mkdtemp(prefix="r5_platform_material_upload_"))
        tmp_path = tmp_dir / (file.filename or "material")
        size = 0
        with open(tmp_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    out.close()
                    tmp_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="file too large (max 25MB)")
                out.write(chunk)

        managed_path, _digest = MaterialStorage().store(tmp_path)

        try:
            material = await service.add_material(
                platform_essay_prompt_id=platform_essay_prompt_id,
                material_type="FILE",
                storage_uri=str(managed_path),
                position=position,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response = PlatformPromptMaterialResponse(
            id=material.id, platform_essay_prompt_id=platform_essay_prompt_id,
            material_type=material.material_type, content=material.content,
            storage_uri=material.storage_uri, position=material.position,
        )
        await session.commit()
        return response


@admin_essay_prompts_router.get(
    "/{platform_essay_prompt_id}/materials",
    response_model=list[PlatformPromptMaterialResponse],
)
async def list_platform_prompt_materials(
    platform_essay_prompt_id: UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> list[PlatformPromptMaterialResponse]:
    async with session_factory() as session:
        service = PlatformEssayPromptService(session)
        materials = await service.list_materials(
            platform_essay_prompt_id=platform_essay_prompt_id
        )
        return [
            PlatformPromptMaterialResponse(
                id=m.id, platform_essay_prompt_id=platform_essay_prompt_id,
                material_type=m.material_type, content=m.content,
                storage_uri=m.storage_uri, position=m.position,
            )
            for m in materials
        ]


__all__ = ["admin_essay_prompts_router"]
