"""Schemas HTTP da busca lexical (Fase 5).

DIREITOS POR TIPO, NAO POR FILTRO
=================================

Sao DOIS schemas de resultado, e a escolha e pela ``rights_class`` da fonte:

``LexicalHitResponse``          - nao tem campo de texto. Nenhum.
``QuotableLexicalHitResponse``  - tem ``excerpt``, limitado pela politica.

Isso nao e um ``if`` que alguem possa esquecer de aplicar, nem um campo
preenchido com ``None``: para fonte ``COMMERCIAL_REFERENCE`` o campo nao
existe no tipo devolvido. ``tests/test_knowledge_lexical_http.py`` verifica
estruturalmente que o schema restrito nunca declara texto literal, e que o
corpo da resposta de uma fonte comercial nao carrega a chave.

A uniao e DISCRIMINADA por ``quotable``, para que a serializacao seja
deterministica em vez de depender da inferencia do Pydantic.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ...knowledge_retrieval_policy.v1 import POLICY, RETRIEVAL_PURPOSES
from ...services.knowledge_engine.lexical_search import PHRASE_MODES
from ...services.knowledge_engine.rights import RIGHTS_CLASSES, SOURCE_KINDS

_PURPOSE_DESCRIPTION = (
    " | ".join(RETRIEVAL_PURPOSES)
    + " - ausente e tratado como UNKNOWN, que FECHA SOLUTION"
)


class LexicalSearchRequest(BaseModel):
    query: str = Field(..., max_length=500)
    retrieval_purpose: str | None = Field(None, description=_PURPOSE_DESCRIPTION)
    source_kinds: list[str] | None = Field(None, description=" | ".join(SOURCE_KINDS))
    chunk_types: list[str] | None = None
    content_node_id: UUID | None = None
    include_descendant_nodes: bool = True
    rights_classes: list[str] | None = Field(None, description=" | ".join(RIGHTS_CLASSES))
    exclude_commercial: bool = False
    phrase_mode: str = Field("BONUS", description=" | ".join(PHRASE_MODES))
    #: A/B de calibracao. Default = o da politica. Existe para que o relatorio
    #: possa MEDIR o peso de titulo em vez de supo-lo.
    heading_weight: float | None = Field(None, ge=0.0, le=10.0)
    #: Teto de diversidade, DESLIGADO por default: a Fase 5 mede a
    #: concentracao por fonte antes de limita-la.
    max_per_source: int | None = Field(None, ge=1, le=100)
    limit: int = Field(POLICY.default_limit, ge=1, le=POLICY.max_limit)
    offset: int = Field(0, ge=0, le=POLICY.candidate_cap)
    explain: bool = Field(
        False, description="inclui a decomposicao do score em cada resultado"
    )


class _BaseHitResponse(BaseModel):
    chunk_id: UUID
    rank: int
    score: float
    chunk_type: str
    ordinal: int
    heading_path: list[str]
    #: Pagina IMPRESSA: o ``page_offset`` do documento foi aplicado no
    #: chunking e NAO e reaplicado aqui.
    page_start: int | None
    page_end: int | None
    char_count: int | None
    text_hash: str
    #: Funcao EDITORIAL na obra, ortogonal a ``chunk_type``.
    editorial_role: str
    #: NULO = nao processado por versao alguma do detector. Com versao e
    #: ``editorial_role = 'UNKNOWN'``, significa classificado e evidencia
    #: insuficiente. Sao estados diferentes.
    editorial_detector_version: str | None
    content_node_id: UUID | None
    bncc_node_codes: list[str]
    document_id: UUID
    document_filename: str
    source_id: UUID
    source_title: str
    source_kind: str
    rights_class: str
    authority_level: str
    explanation: dict[str, Any] | None = None


class LexicalHitResponse(_BaseHitResponse):
    """Resultado de fonte cujo literal e INTERNO RESTRITO. Sem campo de texto."""

    quotable: Literal[False] = False


class QuotableLexicalHitResponse(_BaseHitResponse):
    """Resultado de fonte que admite citacao literal, limitada pela politica."""

    quotable: Literal[True] = True
    excerpt: str | None = None


LexicalHitUnion = Annotated[
    QuotableLexicalHitResponse | LexicalHitResponse,
    Field(discriminator="quotable"),
]


class LexicalSearchResponse(BaseModel):
    query: str
    normalized_terms: list[str]
    dropped_tokens: list[dict[str, str]]
    unmatched_terms: list[str]
    hits: list[LexicalHitUnion]

    total_candidates: int
    returned: int
    limit: int
    offset: int
    has_more: bool
    candidate_cap_reached: bool

    #: Identidade da FOTO: consulta + filtros + politica + normalizador +
    #: geracao do indice. Duas paginas com fingerprints diferentes nao
    #: pertencem a mesma foto do corpus.
    query_fingerprint: str
    index_generation: int | None
    corpus_size: int
    avgdl: float
    #: Papeis editoriais que compoem a base de ``df``/``N``/``avgdl``.
    #: Derivado da tabela de elegibilidade, versionado, e participa do
    #: ``query_fingerprint``.
    statistical_corpus_roles: list[str]

    retrieval_mode: str
    lexical_backend: str
    retrieval_purpose: str
    phrase_mode: str
    solution_visible: bool
    policy: dict[str, Any]

    #: STRICT_CORPUS: resultado vazio SEMPRE tem razao nomeada.
    empty_reasons: list[str]
    filtered_out: dict[str, int]

    source_distribution: dict[str, int]
    distinct_sources: int
    coverage_warning: bool
    chunk_type_distribution: dict[str, int]
    duration_seconds: float


class QueryExplanationResponse(BaseModel):
    """Como a consulta foi tokenizada, SEM buscar.

    Responde "por que ``mols`` nao achou nada?" sem adivinhacao: mostra a
    normalizacao de cada token, o que caiu e por que, e o ``df``/``idf`` de
    cada termo no indice atual.
    """

    query: str
    normalizer_version: str
    terms: list[dict[str, Any]]
    dropped_tokens: list[dict[str, str]]
    stream_length: int
    corpus_size: int
    avgdl: float
    index_generation: int | None


class LexicalIndexSourceStatus(BaseModel):
    source_id: UUID
    chunks: int
    indexed: int
    missing: int


class LexicalIndexStatusResponse(BaseModel):
    scope: str
    generation: int | None
    normalizer_version: str | None
    policy_version: str | None
    total_chunks: int
    indexed: int
    missing: int
    stale: int
    normalizer_versions_present: list[str]
    postings: int
    total_indexed_tokens: int
    avgdl: float
    by_source: list[LexicalIndexSourceStatus]


class LexicalReindexResponse(BaseModel):
    document_id: UUID
    chunks_indexed: int
    postings_written: int
    #: Antes e depois, para que o efeito da reindexacao seja visivel.
    generation_before: int | None
    generation_after: int
    normalizer_version: str
    duration_seconds: float
