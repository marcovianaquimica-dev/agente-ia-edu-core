"""Rotas de DIAGNOSTICO da busca lexical (Fase 5).

Para que servem, e para que NAO servem
======================================

Sao endpoints internos de diagnostico: existem para MEDIR a qualidade da
recuperacao lexical isoladamente, antes de haver vetor, fusao ou Knowledge
Pack. Todas exigem PLATFORM_ADMIN e nenhuma produz Pack.

Uma fronteira que precisa ser dita em voz alta: a spec 1.1 exige que texto
livre seja OBRIGATORIAMENTE resolvido para um ``CatalogNode``. Estes
endpoints NAO fazem essa resolucao, de proposito - se fizessem, "mol" seria
irrespondivel e a qualidade lexical, imensuravel. A obrigacao continua
intacta no caminho do Pack; aqui ela nao se aplica porque aqui nao ha Pack.

O Knowledge Pack segue sendo a unica interface PUBLICA do subsistema
(spec 2). Diagnostico de plataforma nao e consumo de produto.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ...identity import ExternalIdentityContext
from ...services.knowledge_engine.lexical_index import (
    LexicalIndexError,
    LexicalIndexService,
)
from ...services.knowledge_engine.lexical_search import (
    LexicalHit,
    LexicalSearchError,
    LexicalSearcher,
)
from ..dependencies import get_session_factory
from ..schemas.knowledge_lexical import (
    LexicalHitResponse,
    LexicalIndexStatusResponse,
    LexicalReindexResponse,
    LexicalSearchRequest,
    LexicalSearchResponse,
    QueryExplanationResponse,
    QuotableLexicalHitResponse,
)
from .admin import require_platform_admin

knowledge_lexical_router = APIRouter(
    prefix="/api/v1/knowledge-engine/lexical",
    tags=["knowledge-engine-lexical"],
)


@knowledge_lexical_router.post("/search", response_model=LexicalSearchResponse)
async def lexical_search(
    payload: LexicalSearchRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> LexicalSearchResponse:
    async with session_factory() as session:
        try:
            result = await LexicalSearcher(session).search(
                payload.query,
                retrieval_purpose=payload.retrieval_purpose,
                source_kinds=payload.source_kinds,
                chunk_types=payload.chunk_types,
                content_node_id=payload.content_node_id,
                include_descendant_nodes=payload.include_descendant_nodes,
                rights_classes=payload.rights_classes,
                exclude_commercial=payload.exclude_commercial,
                phrase_mode=payload.phrase_mode,
                heading_weight=payload.heading_weight,
                limit=payload.limit,
                offset=payload.offset,
                max_per_source=payload.max_per_source,
            )
        except LexicalSearchError as exc:
            # 422: a requisicao esta bem formada mas descreve uma busca que a
            # politica nao admite. O chamador precisa do codigo nomeado.
            raise HTTPException(
                status_code=422, detail={"code": exc.code, "message": str(exc)}
            ) from exc

    return LexicalSearchResponse(
        query=result.query,
        normalized_terms=list(result.normalized_terms),
        dropped_tokens=list(result.dropped_tokens),
        unmatched_terms=list(result.unmatched_terms),
        hits=[_hit_response(hit, explain=payload.explain) for hit in result.hits],
        total_candidates=result.total_candidates,
        returned=result.returned,
        limit=result.limit,
        offset=result.offset,
        has_more=result.has_more,
        candidate_cap_reached=result.candidate_cap_reached,
        query_fingerprint=result.query_fingerprint,
        index_generation=result.index_generation,
        corpus_size=result.corpus_size,
        avgdl=result.avgdl,
        retrieval_mode=result.retrieval_mode,
        lexical_backend=result.lexical_backend,
        retrieval_purpose=result.retrieval_purpose,
        phrase_mode=result.phrase_mode,
        solution_visible=result.solution_visible,
        policy=result.policy,
        empty_reasons=list(result.empty_reasons),
        filtered_out=dict(result.filtered_out),
        source_distribution=dict(result.source_distribution),
        distinct_sources=result.distinct_sources,
        coverage_warning=result.coverage_warning,
        chunk_type_distribution=dict(result.chunk_type_distribution),
        duration_seconds=result.duration_seconds,
    )


@knowledge_lexical_router.post("/explain-query", response_model=QueryExplanationResponse)
async def explain_query(
    payload: LexicalSearchRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> QueryExplanationResponse:
    """Tokeniza a consulta e devolve ``df``/``idf``, SEM buscar.

    O calculo vive no subsistema, nao aqui: ler ``knowledge_chunk_terms`` de
    dentro de uma rota violaria a fronteira do spec 2, e o teste de fronteira
    pegou exatamente isso quando a primeira versao desta rota foi escrita.
    """
    async with session_factory() as session:
        explanation = await LexicalSearcher(session).explain_query(payload.query)

    return QueryExplanationResponse(
        query=explanation.query,
        normalizer_version=explanation.normalizer_version,
        terms=list(explanation.terms),
        dropped_tokens=list(explanation.dropped_tokens),
        stream_length=explanation.stream_length,
        corpus_size=explanation.corpus_size,
        avgdl=explanation.avgdl,
        index_generation=explanation.index_generation,
    )


@knowledge_lexical_router.get("/index-status", response_model=LexicalIndexStatusResponse)
async def index_status(
    source_id: UUID | None = Query(None),
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> LexicalIndexStatusResponse:
    async with session_factory() as session:
        status = await LexicalIndexService(session).status(source_id=source_id)
    return LexicalIndexStatusResponse(
        scope=status.scope,
        generation=status.generation,
        normalizer_version=status.normalizer_version,
        policy_version=status.policy_version,
        total_chunks=status.total_chunks,
        indexed=status.indexed,
        missing=status.missing,
        stale=status.stale,
        normalizer_versions_present=list(status.normalizer_versions_present),
        postings=status.postings,
        total_indexed_tokens=status.total_indexed_tokens,
        avgdl=status.avgdl,
        by_source=list(status.by_source),
    )


@knowledge_lexical_router.post(
    "/reindex/{document_id}", response_model=LexicalReindexResponse
)
async def reindex_document(
    document_id: UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> LexicalReindexResponse:
    """Reindexa UM documento, de forma sincrona.

    Um documento e trabalho limitado - 0,3 s de tokenizacao para 548 paginas.
    O acervo inteiro e script (``scripts/index_lexical_corpus.py``), nao
    requisicao HTTP: a forma errada de fazer trabalho longo e um timeout
    esperando por ele.
    """
    async with session_factory() as session:
        service = LexicalIndexService(session)
        before = await service.current_generation()
        try:
            snapshot = await service.reindex_document(document_id)
        except LexicalIndexError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await session.commit()

    return LexicalReindexResponse(
        document_id=document_id,
        chunks_indexed=snapshot.chunks_indexed,
        postings_written=snapshot.postings_written,
        generation_before=before,
        generation_after=snapshot.generation,
        normalizer_version=snapshot.normalizer_version,
        duration_seconds=snapshot.duration_seconds,
    )


def _hit_response(
    hit: LexicalHit, *, explain: bool
) -> QuotableLexicalHitResponse | LexicalHitResponse:
    """Escolhe o TIPO pela politica de direitos.

    Para fonte comercial devolve o schema sem campo de texto - nao um campo
    de texto preenchido com ``None``.
    """
    common = {
        "chunk_id": hit.chunk_id,
        "rank": hit.rank,
        "score": hit.score,
        "chunk_type": hit.chunk_type,
        "ordinal": hit.ordinal,
        "heading_path": list(hit.heading_path),
        "page_start": hit.page_start,
        "page_end": hit.page_end,
        "char_count": hit.char_count,
        "text_hash": hit.text_hash,
        "content_node_id": hit.content_node_id,
        "bncc_node_codes": list(hit.bncc_node_codes),
        "document_id": hit.document_id,
        "document_filename": hit.document_filename,
        "source_id": hit.source_id,
        "source_title": hit.source_title,
        "source_kind": hit.source_kind,
        "rights_class": hit.rights_class,
        "authority_level": hit.authority_level,
        "explanation": hit.explanation if explain else None,
    }
    if hit.quotable:
        return QuotableLexicalHitResponse(excerpt=hit.excerpt, **common)
    return LexicalHitResponse(**common)
