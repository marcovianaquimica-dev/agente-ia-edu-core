"""Rotas do CEREBRO / Knowledge Engine (Fases 2 e 3).

Superficie ate aqui: cadastrar fonte, registrar documento, EXTRAIR e
CHUNKAR, ler e arquivar. Nenhum endpoint de indice lexical, embedding,
recuperacao ou Knowledge Pack - essas fases nao chegaram, e a infraestrutura
delas existir nao e motivo para antecipa-las.

Toda rota exige PLATFORM_ADMIN. O router tambem entra em ``app.py`` sob
``reception_only_guard``, como todo router nao-publico do projeto, para que o
papel so-recepcao nunca alcance o corpus.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import aliased

from ...bncc_contract.v1 import BNCC_TAXONOMY_CODE, BNCC_VERSION_EM_2018
from ...db.models import Taxonomy, TaxonomyNode
from ...identity import ExternalIdentityContext
from ...services.knowledge_engine.document_ingress import (
    DocumentIngressError,
    configured_document_root,
    resolve_local_path,
)
from ...services.knowledge_engine.documents import (
    KnowledgeDocumentNotFound,
    KnowledgeDocumentProcessingError,
    KnowledgeDocumentService,
)
from ...services.knowledge_engine.rights import KnowledgeRightsViolation
from ...services.knowledge_engine.sources import (
    KnowledgeSourceNotFound,
    KnowledgeSourceService,
)
from ..dependencies import get_session_factory
from ..schemas.knowledge_engine import (
    BnccSkillResponse,
    KnowledgeChunkResponse,
    KnowledgeFrameworkResponse,
    KnowledgeChunkStatsResponse,
    KnowledgeDocumentRegisterRequest,
    KnowledgeExtractionResponse,
    KnowledgeDocumentRegistrationResponse,
    KnowledgeDocumentResponse,
    KnowledgeSourceCreateRequest,
    KnowledgeSourceListResponse,
    KnowledgeSourceResponse,
)
from .admin import require_platform_admin

knowledge_engine_router = APIRouter(
    prefix="/api/v1/knowledge-engine",
    tags=["knowledge-engine"],
)


@knowledge_engine_router.post("/sources", response_model=KnowledgeSourceResponse, status_code=201)
async def register_source(
    payload: KnowledgeSourceCreateRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> KnowledgeSourceResponse:
    async with session_factory() as session:
        service = KnowledgeSourceService(session)
        try:
            snapshot = await service.register(
                **payload.model_dump(),
                created_by_external_identity=identity.external_user_id,
            )
        except KnowledgeRightsViolation as exc:
            # 409: a requisicao esta bem formada, mas descreve uma fonte que a
            # politica de direitos nao admite. Nunca deixar virar um 500 de
            # IntegrityError - o chamador precisa do motivo nomeado.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return KnowledgeSourceResponse(**vars(snapshot))


@knowledge_engine_router.get("/sources", response_model=KnowledgeSourceListResponse)
async def list_sources(
    rights_class: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> KnowledgeSourceListResponse:
    async with session_factory() as session:
        service = KnowledgeSourceService(session)
        snapshots = await service.list_sources(
            rights_class=rights_class, status=status, limit=limit, offset=offset
        )
    sources = [KnowledgeSourceResponse(**vars(snapshot)) for snapshot in snapshots]
    return KnowledgeSourceListResponse(sources=sources, count=len(sources))


@knowledge_engine_router.get("/sources/{source_id}", response_model=KnowledgeSourceResponse)
async def get_source(
    source_id: UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> KnowledgeSourceResponse:
    async with session_factory() as session:
        service = KnowledgeSourceService(session)
        try:
            snapshot = await service.get(source_id)
        except KnowledgeSourceNotFound as exc:
            raise HTTPException(status_code=404, detail="fonte de conhecimento nao encontrada") from exc
    return KnowledgeSourceResponse(**vars(snapshot))


@knowledge_engine_router.get(
    "/sources/{source_id}/documents", response_model=list[KnowledgeDocumentResponse]
)
async def list_documents(
    source_id: UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> list[KnowledgeDocumentResponse]:
    async with session_factory() as session:
        service = KnowledgeSourceService(session)
        try:
            snapshots = await service.list_documents(source_id)
        except KnowledgeSourceNotFound as exc:
            raise HTTPException(status_code=404, detail="fonte de conhecimento nao encontrada") from exc
    return [_document_response(snapshot) for snapshot in snapshots]


@knowledge_engine_router.post(
    "/sources/{source_id}/documents",
    response_model=KnowledgeDocumentRegistrationResponse,
)
async def register_document(
    source_id: UUID,
    payload: KnowledgeDocumentRegisterRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> KnowledgeDocumentRegistrationResponse:
    """Registra um documento por caminho local, confinado a raiz autorizada.

    Upload multipart fica explicitamente fora da Fase 2. Quando entrar,
    constroi o mesmo ``ResolvedDocumentFile`` e chama o mesmo service: nem o
    modelo ``KnowledgeDocument`` nem a idempotencia por hash mudam.
    """
    root = configured_document_root()
    if root is None:
        # Falha fechada: sem raiz configurada nenhum caminho e aceito. Cair
        # num default como o cwd transformaria a maquina em raiz autorizada.
        raise HTTPException(
            status_code=503,
            detail=(
                "KNOWLEDGE_ENGINE_DOCUMENT_ROOT nao esta configurada; "
                "nenhum caminho de documento pode ser aceito"
            ),
        )

    try:
        resolved = resolve_local_path(payload.local_path, root=root)
    except DocumentIngressError as exc:
        status_code = 404 if exc.code == "FILE_NOT_FOUND" else 400
        raise HTTPException(status_code=status_code, detail=f"{exc.code}: {exc}") from exc

    async with session_factory() as session:
        service = KnowledgeSourceService(session)
        try:
            registration = await service.register_document(
                source_id, resolved, page_offset=payload.page_offset
            )
        except KnowledgeSourceNotFound as exc:
            raise HTTPException(status_code=404, detail="fonte de conhecimento nao encontrada") from exc

    return KnowledgeDocumentRegistrationResponse(
        document=_document_response(registration.document),
        created=registration.created,
    )


@knowledge_engine_router.delete("/sources/{source_id}", response_model=KnowledgeSourceResponse)
async def archive_source(
    source_id: UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> KnowledgeSourceResponse:
    """ARQUIVA a fonte. Nunca apaga.

    Um Knowledge Pack cita evidencia por chunk, e toda FK do subsistema e
    RESTRICT: apagar uma fonte invalidaria retroativamente a procedencia de
    trabalho ja entregue.
    """
    async with session_factory() as session:
        service = KnowledgeSourceService(session)
        try:
            snapshot = await service.archive(source_id)
        except KnowledgeSourceNotFound as exc:
            raise HTTPException(status_code=404, detail="fonte de conhecimento nao encontrada") from exc
    return KnowledgeSourceResponse(**vars(snapshot))


@knowledge_engine_router.post(
    "/sources/{source_id}/documents/{document_id}/extract",
    response_model=KnowledgeExtractionResponse,
)
async def extract_document_route(
    source_id: UUID,
    document_id: UUID,
    force: bool = Query(False, description="re-chunka um documento ja processado"),
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> KnowledgeExtractionResponse:
    """Extrai o texto do documento e o transforma em chunks.

    Reentrante: um documento ja processado responde 409 ALREADY_CHUNKED, e
    ``force`` e recusado quando ha embedding referenciando os chunks.

    Um PDF sem camada de texto em nenhum leitor NAO levanta erro HTTP: o
    documento vai para FAILED com ``extraction_error`` comecando por
    ``OCR_REQUIRED``. Isso e um resultado, nao uma falha da requisicao - e e
    um estado consultavel, que permite listar o que espera OCR.
    """
    async with session_factory() as session:
        service = KnowledgeDocumentService(session)
        try:
            snapshot = await service.extract_and_chunk(source_id, document_id, force=force)
        except KnowledgeDocumentNotFound as exc:
            raise HTTPException(status_code=404, detail="documento nao encontrado") from exc
        except KnowledgeDocumentProcessingError as exc:
            status_code = 422 if exc.code == "UNSUPPORTED_SOURCE_KIND_FOR_PHASE" else 409
            raise HTTPException(status_code=status_code, detail=f"{exc.code}: {exc}") from exc

    return KnowledgeExtractionResponse(
        document_id=snapshot.document_id,
        source_id=snapshot.source_id,
        extraction_status=snapshot.extraction_status,
        extraction_method=snapshot.extraction_method,
        extraction_error=snapshot.extraction_error,
        page_count=snapshot.page_count,
        chunks_created=snapshot.chunks_created,
        partial_reasons=list(snapshot.partial_reasons),
        pages_without_text=list(snapshot.pages_without_text),
        duration_seconds=snapshot.duration_seconds,
    )


@knowledge_engine_router.get(
    "/sources/{source_id}/documents/{document_id}/chunks",
    response_model=list[KnowledgeChunkResponse],
)
async def list_chunks(
    source_id: UUID,
    document_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> list[KnowledgeChunkResponse]:
    """Metadados dos chunks. NUNCA o texto literal de fonte comercial.

    ``excerpt`` e governado pela politica de direitos: para
    COMMERCIAL_REFERENCE vem sempre ``None``, e nao ha campo ``raw_text`` em
    schema algum.
    """
    async with session_factory() as session:
        service = KnowledgeDocumentService(session)
        try:
            snapshots = await service.list_chunks(
                source_id, document_id, limit=limit, offset=offset
            )
        except KnowledgeDocumentNotFound as exc:
            raise HTTPException(status_code=404, detail="documento nao encontrado") from exc
    return [KnowledgeChunkResponse(**vars(snapshot)) for snapshot in snapshots]


@knowledge_engine_router.get(
    "/sources/{source_id}/chunks/stats", response_model=KnowledgeChunkStatsResponse
)
async def chunk_stats(
    source_id: UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> KnowledgeChunkStatsResponse:
    """Distribuicao por tipo e cobertura de paginas.

    Existe para que falso positivo estrutural seja OBSERVAVEL: sem numero,
    "o chunker as vezes erra" vira folclore.
    """
    async with session_factory() as session:
        service = KnowledgeSourceService(session)
        try:
            await service.get(source_id)
        except KnowledgeSourceNotFound as exc:
            raise HTTPException(status_code=404, detail="fonte nao encontrada") from exc
        stats = await KnowledgeDocumentService(session).chunk_stats(source_id)
    return KnowledgeChunkStatsResponse(**stats)


@knowledge_engine_router.post(
    "/sources/{source_id}/documents/{document_id}/extract-framework",
    response_model=KnowledgeFrameworkResponse,
)
async def extract_framework_route(
    source_id: UUID,
    document_id: UUID,
    taxonomy_version: str = Query(
        BNCC_VERSION_EM_2018, description="versao da BNCC, ex. EM-2018"
    ),
    force: bool = Query(False),
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> KnowledgeFrameworkResponse:
    """Ingere um documento NORMATIVO: extrai a estrutura e cria um chunk por
    habilidade.

    NAO semeia taxonomia. Semear e mudanca de CURRICULO, feita por script
    proprio com revisao - nunca efeito colateral de uma chamada de API de
    ingestao (spec 22.1).
    """
    async with session_factory() as session:
        service = KnowledgeDocumentService(session)
        try:
            snapshot = await service.extract_framework(
                source_id, document_id, taxonomy_version=taxonomy_version, force=force
            )
        except KnowledgeDocumentNotFound as exc:
            raise HTTPException(status_code=404, detail="documento nao encontrado") from exc
        except KnowledgeDocumentProcessingError as exc:
            status_code = 422 if exc.code == "NOT_A_CURRICULUM_FRAMEWORK" else 409
            raise HTTPException(status_code=status_code, detail=f"{exc.code}: {exc}") from exc

    framework = snapshot.framework or {}

    return KnowledgeFrameworkResponse(
        document_id=snapshot.document_id,
        source_id=snapshot.source_id,
        extraction_status=snapshot.extraction_status,
        extraction_method=snapshot.extraction_method,
        extraction_error=snapshot.extraction_error,
        page_count=snapshot.page_count,
        chunks_created=snapshot.chunks_created,
        taxonomy_code=framework.get("taxonomy_code"),
        taxonomy_version=framework.get("taxonomy_version"),
        area_code=framework.get("area_code"),
        competencies=framework.get("competencies"),
        skills=framework.get("skills"),
        skills_per_competency=framework.get("skills_per_competency"),
        duration_seconds=snapshot.duration_seconds,
    )


@knowledge_engine_router.get(
    "/frameworks/bncc/{taxonomy_version}/skills",
    response_model=list[BnccSkillResponse],
)
async def list_bncc_skills(
    taxonomy_version: str,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> list[BnccSkillResponse]:
    """As habilidades de uma versao da BNCC, como norma semeada.

    A BNCC e OFFICIAL_PUBLIC, entao o enunciado pode ser exposto - ao
    contrario do texto de livro comercial.
    """
    async with session_factory() as session:
        competency = aliased(TaxonomyNode)
        rows = await session.execute(
            select(
                TaxonomyNode.code,
                TaxonomyNode.description,
                competency.code,
                Taxonomy.code,
                Taxonomy.version,
                TaxonomyNode.metadata_,
            )
            .join(Taxonomy, Taxonomy.id == TaxonomyNode.taxonomy_id)
            .outerjoin(competency, competency.id == TaxonomyNode.parent_id)
            .where(
                Taxonomy.code == BNCC_TAXONOMY_CODE,
                Taxonomy.version == taxonomy_version,
                TaxonomyNode.node_type == "skill",
            )
            .order_by(TaxonomyNode.code)
        )
        skills = rows.all()
    if not skills:
        raise HTTPException(
            status_code=404,
            detail=f"nenhuma habilidade semeada para a versao {taxonomy_version!r}",
        )
    return [
        BnccSkillResponse(
            code=code,
            statement=statement or "",
            competency_code=competency_code or "",
            taxonomy_code=taxonomy_code,
            taxonomy_version=version,
            urn=f"{taxonomy_code}:{version}:{code}",
            page=((metadata or {}).get("source") or {}).get("page"),
        )
        for code, statement, competency_code, taxonomy_code, version, metadata in skills
    ]


def _document_response(snapshot) -> KnowledgeDocumentResponse:
    """Projeta o snapshot omitindo ``storage_uri``.

    A omissao e deliberada: e um caminho do sistema de arquivos do servidor.
    """
    fields = vars(snapshot).copy()
    fields.pop("storage_uri", None)
    return KnowledgeDocumentResponse(**fields)
