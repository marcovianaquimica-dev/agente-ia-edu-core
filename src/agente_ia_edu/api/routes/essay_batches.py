"""Envio em lote de redacoes fisicas - superficie do professor.

TEACHER/COORDINATOR/DIRECTOR/PLATFORM_ADMIN, o mesmo conjunto de papeis que
essay_prompts.py e essay_corrections.py ja usam. school_id sempre vem do
contexto resolvido, nunca do corpo; um lote de outra escola e 403, nunca 404.

Spec: docs/superpowers/specs/2026-09-28-envio-lote-redacao-design.md
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_identity, get_session_factory
from ...db.models import EssayBatchPage, EssayBatchUpload
from ...identity import ExternalIdentityContext
from ...services.authorization import AuthorizationService
from ...services.essay_batch import ALLOWED_BATCH_SUFFIXES, EssayBatchService

logger = logging.getLogger(__name__)

essay_batches_router = APIRouter(
    prefix="/api/v1/teacher/essay-batches", tags=["essay-batches"]
)

# Mesmo teto por arquivo que essay_submissions.py e essay_prompts.py ja usam.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def build_batch_service(session: AsyncSession) -> EssayBatchService:
    """Ponto unico de construcao do servico - e o que os testes de rota
    substituem pra injetar um transcritor e um corretor falsos sem precisar de
    nenhuma variavel de ambiente de provedor de IA."""
    return EssayBatchService(session)


class EssayBatchCreatedResponse(BaseModel):
    id: UUID
    school_id: UUID
    essay_prompt_id: UUID
    class_id: UUID
    status: str
    total_pages: int


class EssayBatchNeedsReviewPage(BaseModel):
    id: UUID
    page_number: int
    ocr_name_raw: Optional[str] = None
    ocr_cpf_raw: Optional[str] = None
    has_text: bool


class EssayBatchAvailableStudent(BaseModel):
    student_id: UUID
    full_name: str
    document_number: Optional[str] = None


class EssayBatchStatusResponse(BaseModel):
    id: UUID
    school_id: UUID
    essay_prompt_id: UUID
    class_id: UUID
    status: str
    total_pages: int
    matched_count: int
    needs_review_count: int
    needs_review_pages: list[EssayBatchNeedsReviewPage]
    available_students: list[EssayBatchAvailableStudent]


class ResolveBatchPageRequest(BaseModel):
    student_id: UUID


async def _authorize(identity: ExternalIdentityContext, session: AsyncSession) -> uuid.UUID:
    authz = AuthorizationService(session)
    context = await authz.resolve_context(identity)
    role_check = await authz.require_role(
        context, "TEACHER", "COORDINATOR", "DIRECTOR", "PLATFORM_ADMIN"
    )
    if not role_check.allowed:
        raise HTTPException(
            status_code=403,
            detail="Enviar redacoes em lote requer um papel de professor, coordenador, "
            "diretor ou administrador da plataforma.",
        )
    if context.school_id is None:
        raise HTTPException(status_code=403, detail="An active school context is required.")
    return uuid.UUID(str(context.school_id))


async def _batch_for_own_school_or_403(
    session: AsyncSession, *, batch_id: uuid.UUID, school_id: uuid.UUID
) -> EssayBatchUpload:
    batch = await session.get(EssayBatchUpload, batch_id)
    if batch is None or batch.school_id != school_id:
        raise HTTPException(status_code=403, detail="Este lote nao e seu.")
    return batch


async def _run_batch_processing_in_background(batch_id: UUID, session_factory) -> None:
    """Roda depois que a resposta 202 ja saiu, com a sua propria sessao (a da
    requisicao ja esta fechada). process_batch e best-effort por pagina e ja
    trata falha de OCR; este try/except cobre so uma falha de infraestrutura de
    verdade (o banco fora do ar, um bug), pra que o lote nunca fique travado em
    PROCESSING sem nem uma linha de log pra diagnosticar."""
    try:
        async with session_factory() as session:
            await build_batch_service(session).process_batch(batch_id)
    except Exception:
        logger.exception("processamento em segundo plano falhou para batch_id=%s", batch_id)


@essay_batches_router.post("", status_code=202, response_model=EssayBatchCreatedResponse)
async def create_essay_batch(
    background_tasks: BackgroundTasks,
    essay_prompt_id: UUID = Form(...),
    class_id: UUID = Form(...),
    files: list[UploadFile] = File(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayBatchCreatedResponse:
    """Devolve 202 assim que o lote esta gravado, SEM esperar o processamento -
    o professor acompanha por GET /{batch_id} (spec s5).

    Streaming em pedacos de 1MB pra um arquivo temporario, checando o tamanho a
    cada pedaco, exatamente como upload_prompt_material ja faz: um upload
    gigante nunca chega a virar um arquivo em MaterialStorage.
    """
    async with session_factory() as session:
        school_id = await _authorize(identity, session)

        tmp_dir = Path(tempfile.mkdtemp(prefix="r4_batch_upload_"))
        try:
            source_paths: list[Path] = []
            for index, upload in enumerate(files):
                suffix = Path(upload.filename or "").suffix.lower()
                if suffix not in ALLOWED_BATCH_SUFFIXES:
                    raise HTTPException(
                        status_code=422, detail=f"Formato de arquivo nao suportado: {suffix!r}"
                    )
                tmp_path = tmp_dir / f"{index:03d}_{Path(upload.filename or 'arquivo').name}"
                size = 0
                with open(tmp_path, "wb") as out:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        if size > _MAX_UPLOAD_BYTES:
                            out.close()
                            raise HTTPException(
                                status_code=413,
                                detail=f"Arquivo {upload.filename!r} passa de 25MB.",
                            )
                        out.write(chunk)
                source_paths.append(tmp_path)

            try:
                created = await build_batch_service(session).create_batch(
                    school_id=school_id, essay_prompt_id=essay_prompt_id, class_id=class_id,
                    uploaded_by_external_identity=identity.external_user_id,
                    source_paths=source_paths,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            # MaterialStorage ja copiou tudo o que precisa sobreviver; o que
            # fica aqui e so rascunho (inclusive os "<stem>_pages" que o split
            # de PDF escreveu ao lado do arquivo).
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Resposta montada ANTES do commit (expire_on_commit=True em producao).
        response = EssayBatchCreatedResponse(**created)
        await session.commit()

    background_tasks.add_task(
        _run_batch_processing_in_background, response.id, session_factory
    )
    return response


@essay_batches_router.get("/{batch_id}", response_model=EssayBatchStatusResponse)
async def get_essay_batch(
    batch_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayBatchStatusResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _batch_for_own_school_or_403(session, batch_id=batch_id, school_id=school_id)
        data = await build_batch_service(session).get_batch_status(batch_id)
        return EssayBatchStatusResponse(**data)


async def _run_corrections_in_background(submission_ids, session_factory) -> None:
    try:
        async with session_factory() as session:
            await build_batch_service(session).run_corrections(submission_ids)
    except Exception:
        logger.exception("correcao em segundo plano falhou para %s", submission_ids)


@essay_batches_router.post(
    "/{batch_id}/pages/{page_id}/resolve", response_model=EssayBatchStatusResponse
)
async def resolve_essay_batch_page(
    batch_id: UUID,
    page_id: UUID,
    request: ResolveBatchPageRequest,
    background_tasks: BackgroundTasks,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayBatchStatusResponse:
    """Devolve o status JA atualizado do lote inteiro, pra que a tela de
    resolucao manual nao precise de um segundo GET depois de cada pagina."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _batch_for_own_school_or_403(session, batch_id=batch_id, school_id=school_id)
        service = build_batch_service(session)

        # Desvio intencional em relacao ao brief original (achado na revisao
        # da Tarefa 8, ja registrado no ledger do controlador):
        # EssayBatchService.resolve_page nao valida que a pagina ainda esta em
        # NEEDS_REVIEW antes de realoca-la - uma pagina ja MATCHED_AUTO ou
        # RESOLVED_MANUAL poderia ser movida de novo em silencio, deixando-a
        # "presa" na submissao antiga. A validacao fica aqui na rota, e nao no
        # servico, pra nao mudar a assinatura/contrato ja aprovado do servico.
        page = await session.get(EssayBatchPage, page_id)
        if page is None or page.batch_id != batch_id:
            raise HTTPException(status_code=404, detail="Pagina nao encontrada neste lote.")
        if page.status != "NEEDS_REVIEW":
            raise HTTPException(status_code=422, detail="Esta pagina ja foi resolvida.")

        try:
            submission_ids = await service.resolve_page(
                batch_id=batch_id, page_id=page_id, student_id=request.student_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        data = await service.get_batch_status(batch_id)
        response = EssayBatchStatusResponse(**data)

    if submission_ids:
        # Mesma razao de confirm_essay_submission: a chamada de correcao e a
        # parte lenta e o professor nao deve esperar por ela.
        background_tasks.add_task(
            _run_corrections_in_background, submission_ids, session_factory
        )
    return response


@essay_batches_router.get("/{batch_id}/pages/{page_id}/image")
async def get_essay_batch_page_image(
    batch_id: UUID,
    page_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    """A imagem da folha, pro professor identificar o aluno olhando a letra."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _batch_for_own_school_or_403(session, batch_id=batch_id, school_id=school_id)
        page = await session.get(EssayBatchPage, page_id)
        if page is None or page.batch_id != batch_id:
            raise HTTPException(status_code=404, detail="Pagina nao encontrada neste lote.")
        return FileResponse(page.storage_uri)
