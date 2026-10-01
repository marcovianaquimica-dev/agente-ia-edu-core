"""Schemas HTTP do CEREBRO / Knowledge Engine (Fase 2).

NENHUM schema de resposta aqui tem campo de texto literal de documento. Isso
nao e um filtro em tempo de execucao que alguem possa esquecer de aplicar: o
campo simplesmente nao existe. ``tests/test_knowledge_engine_http.py``
verifica estruturalmente que nenhum schema deste modulo declara ``raw_text``.

Os vocabularios de ``source_kind``, ``rights_class`` e ``authority_level``
sao validados aqui E em ``services/knowledge_engine/rights.py`` E (no caso de
``rights_class``) por CheckConstraint. Tres camadas de proposito: a do schema
da o erro mais cedo e mais legivel, a do service e a regra de dominio, e a do
banco e a que nao se contorna.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from ...services.knowledge_engine.rights import (
    AUTHORITY_LEVELS,
    RIGHTS_CLASSES,
    SOURCE_KINDS,
)

_SOURCE_KIND_DESCRIPTION = " | ".join(SOURCE_KINDS)
_RIGHTS_DESCRIPTION = " | ".join(RIGHTS_CLASSES)
_AUTHORITY_DESCRIPTION = " | ".join(AUTHORITY_LEVELS)


class KnowledgeSourceCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    source_kind: str = Field(..., description=_SOURCE_KIND_DESCRIPTION)
    rights_class: str = Field(..., description=_RIGHTS_DESCRIPTION)
    authority_level: str = Field(..., description=_AUTHORITY_DESCRIPTION)

    authors: str | None = Field(None, max_length=500)
    publisher: str | None = Field(None, max_length=255)
    edition: str | None = Field(None, max_length=100)
    publication_year: int | None = Field(None, ge=1400, le=2200)
    isbn: str | None = Field(None, max_length=20)
    license_reference: str | None = None
    rights_notes: str | None = None
    # Proibido para COMMERCIAL_REFERENCE - a regra vive em rights.py e no
    # CheckConstraint, nao aqui, porque depende de outro campo.
    educational_resource_id: UUID | None = None


class KnowledgeDocumentRegisterRequest(BaseModel):
    """Registro de documento por caminho local (Fase 2).

    ``local_path`` e resolvido e confinado a raiz autorizada
    (``KNOWLEDGE_ENGINE_DOCUMENT_ROOT``). Upload multipart fica
    explicitamente fora desta fase; quando entrar, acrescenta um campo
    alternativo aqui e termina no MESMO service, com o mesmo modelo e a mesma
    idempotencia por hash.
    """

    local_path: str = Field(..., min_length=1)
    # Diferenca entre a pagina do arquivo e a pagina IMPRESSA da obra: um
    # recorte que comeca na pagina 312 do livro tem offset 311. Informado pelo
    # chamador, nunca derivado - derivar exigiria abrir o arquivo, que e
    # extracao (Fase 3).
    page_offset: int = Field(0, ge=0)


class KnowledgeSourceResponse(BaseModel):
    id: UUID
    title: str
    authors: str | None
    publisher: str | None
    edition: str | None
    publication_year: int | None
    isbn: str | None
    source_kind: str
    rights_class: str
    authority_level: str
    license_reference: str | None
    rights_notes: str | None
    educational_resource_id: UUID | None
    status: str
    created_by_external_identity: str | None
    created_at: datetime
    updated_at: datetime
    document_count: int


class KnowledgeDocumentResponse(BaseModel):
    id: UUID
    source_id: UUID
    filename: str
    document_hash: str
    mime_type: str | None
    file_size_bytes: int | None
    page_offset: int
    page_count: int | None
    extraction_method: str | None
    extraction_status: str
    ingress: str | None
    created_at: datetime
    # storage_uri NAO e exposto: e um caminho do sistema de arquivos do
    # servidor, e nenhum consumidor tem o que fazer com ele.


class KnowledgeDocumentRegistrationResponse(BaseModel):
    document: KnowledgeDocumentResponse
    #: ``False`` quando os mesmos bytes ja estavam registrados nesta fonte -
    #: nada foi escrito nem copiado de novo.
    created: bool


class KnowledgeSourceListResponse(BaseModel):
    sources: list[KnowledgeSourceResponse]
    count: int


class KnowledgeChunkResponse(BaseModel):
    """Leitura de um chunk.

    Nao ha campo ``raw_text`` - nao e filtro em tempo de execucao, o campo
    nao existe. ``excerpt`` e governado pela politica de direitos: para fonte
    COMMERCIAL_REFERENCE vem sempre ``None``.
    """

    id: UUID
    document_id: UUID
    ordinal: int
    chunk_type: str
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    char_count: int | None
    token_estimate: int | None
    text_hash: str
    excerpt: str | None
    metadata: dict | None


class KnowledgeExtractionResponse(BaseModel):
    document_id: UUID
    source_id: UUID
    extraction_status: str
    extraction_method: str | None
    extraction_error: str | None
    page_count: int | None
    chunks_created: int
    partial_reasons: list[str]
    pages_without_text: list[int]
    duration_seconds: float


class KnowledgeChunkStatsResponse(BaseModel):
    total_chunks: int
    by_chunk_type: dict[str, int]
    chunks_without_page: int
    page_start_min: int | None
    page_end_max: int | None
