"""Recuperacao vetorial por similaridade de cosseno - Fase 6, passo 4.

CONTRATO ESTAVEL, BACKEND SUBSTITUIVEL - a mesma regra da Fase 5
================================================================

``VectorSearcher.search()`` devolvendo ``(chunk_id, rank, score, explanation)``
e o contrato publico, e ``ranked_chunks()`` o devolve exatamente nessa forma
para a Fase 7. Nenhum consumidor conhece fornecedor, modelo ou pgvector.

O que SAI na resposta e o que permite auditar sem acoplar:
``embedding_space_id``, ``dimensions``, ``distance_metric`` e
``vector_backend``. O que NAO sai e o nome do fornecedor e o nome do modelo -
expo-los convidaria um consumidor a ramificar em ``if provider == ...``, que
e precisamente o acoplamento que "dimensao e dado, nao DDL" existe para
impedir. Quem opera chega a provider e modelo pelo ``space_id``, na tabela de
espacos, que e onde essa informacao mora.

``embedding_space_fingerprint`` e o sha256 de ``provider|model|dimensions``:
permite dizer "estes dois resultados vieram do mesmo espaco" sem revelar
qual.

SEMANTICA DO SCORE - DECLARADA, NUNCA MISTURADA
===============================================

    score      = similaridade de cosseno, em [-1, 1], MAIOR e melhor
    distance   = distancia de cosseno, em [0, 2],    MENOR e melhor
    score + distance == 1, sempre

Os dois viajam juntos e ``score_semantics`` nomeia a convencao na resposta.
A razao de nao escolher so um: a perna lexical pontua BM25, que e
nao-limitado-e-maior-melhor, e a vetorial mede distancia, que e
menor-melhor. Quem compara as duas pernas num relatorio precisa que a
convencao esteja escrita, nao inferida. Para o RRF da Fase 7 nada disso
importa - ele usa o RANK -, e e exatamente por isso que o rank tambem e
parte do contrato.

NAO HA LIMIAR DE SIMILARIDADE NESTA FASE
=========================================

A busca devolve RANKING. Abstencao e limiar sao assunto do Calibration Set, e
inventar um numero aqui seria calibrar no conjunto de teste. O controle
negativo da Fase 6 registra a DISTRIBUICAO de scores justamente para que o
limiar futuro nasca de evidencia.

DOIS BACKENDS, E SO UM DELES E DE PRODUCAO
===========================================

``PGVECTOR``          PostgreSQL, operador ``<=>`` sobre o indice HNSW parcial
                      do espaco ativo. E o backend de producao. O indice e
                      criado por ``EmbeddingActivationService.activate()``,
                      nao por ``create_all`` - ver ``ann_index_ddl``.
``EXACT_IN_PROCESS``  varredura exata em Python. Existe para TESTE, e nao
                      simula ANN nem se equipara em desempenho ao PostgreSQL.
                      Ele e exato, o que para um corpus de teste e uma
                      vantagem - o ranking esperado e calculavel a mao.

O que os dois compartilham: tudo depois da distancia. Filtro, ordenacao,
desempate, paginacao e montagem sao uma implementacao so, e e por isso que a
equivalencia de ORDENACAO entre os dialetos pode ser testada. Equivalencia de
DESEMPENHO nao e afirmada em lugar nenhum.

Nem equivalencia de BITS: o tipo ``vector`` do pgvector guarda float4,
enquanto a varredura em processo calcula em float64. Os scores divergem na
ordem de 1e-8, a ordem nao. Quem comparar scores das duas pernas num
relatorio precisa saber que o ultimo digito vem do tipo da coluna - esta
medido em ``test_the_two_backends_differ_in_precision_and_that_is_declared``.

O FILTRO VEM ANTES DO CORTE - passo 4.1
=======================================

O passo 4 filtrava em Python, DEPOIS de formar o conjunto candidato, para
preservar a atribuicao. O corpus real mostrou o preco disso, e ele era alto
demais: com 5.911 vetores, uma busca restrita a ``OFFICIAL_PUBLIC`` nao
achava nenhum dos 23 chunks da BNCC em 4 de 7 consultas - nao porque o
material faltasse, mas porque nenhum deles estava entre os 2.000 mais
proximos do acervo inteiro. Falso vazio: a busca dizia "nada encontrado"
sobre um corpus que tinha a resposta.

Agora a escada de rejeicao (``_rejection_ladder``) e UMA expressao SQL usada
em dois lugares: no ``WHERE`` que forma o conjunto candidato e no
``GROUP BY`` que conta quantos cada politica excluiu. Sendo a mesma
expressao, filtro e atribuicao nao tem como divergir - o risco obvio do
pushdown.

A atribuicao MUDOU DE ESCOPO, e isso e observavel em ``filtered_out_scope``:
ela conta sobre o corpus ATIVO inteiro, nao mais sobre o pool ja cortado. O
numero antigo variava com a consulta, o que fazia dele um artefato do corte
em vez de um fato sobre o acervo.

A politica continua em Python. ``editorial_role_is_eligible`` e
``solution_is_visible`` sao avaliadas aqui e o que desce para o banco e uma
lista de valores concretos - inclusive a falha ABERTA de papel desconhecido,
que se preserva naturalmente porque papel que a politica nao conhece nao
entra na lista de inelegiveis.

``candidate_cap`` continua 2.000 e continua sendo atingido em toda consulta
ampla, porque no vetorial todo chunk ativo e candidato. A diferenca e que
agora ele corta entre as ELEGIVEIS, que e o que ele sempre quis dizer.

COBERTURA: RANKING PARCIAL NAO PASSA POR CORPUS COMPLETO
=========================================================

Esta e a regra que o passo 4 acrescenta, e ela reutiliza o passo 3 em vez de
inventar criterio novo. Antes de buscar, a busca compara a cobertura ATUAL do
espaco ativo com o que foi DECLARADO quando ele foi ativado
(``knowledge_embedding_activations.expected_population``).

Falha FECHADA: corpus incompleto recusa a busca. Devolver um ranking sobre
80% do acervo com cara de ranking completo e o erro invisivel - ninguem
percebe o resultado que nao veio.

``allow_degraded=True`` e explicitamente opt-in, para diagnostico, e a
resposta sai com ``degraded=True`` e ``degradation_reasons`` preenchido.

NENHUM LITERAL DE FONTE COMERCIAL
=================================

``excerpt`` so existe para fonte que admite citacao, e o ``raw_text`` de fonte
comercial nunca e sequer lido: ela nao entra na lista de quem o buscador le.
A rastreabilidade permanece completa - ``source_id``, ``document_id``,
``chunk_id``, paginas impressas, ``text_hash``, papel editorial.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence
from uuid import UUID

from sqlalchemy import case, func, literal, literal_column, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import Case

from ...db.models import (
    CatalogNode,
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeDocument,
    KnowledgeEmbeddingActivation,
    KnowledgeEmbeddingSpace,
    KnowledgeSource,
)
from ...knowledge_retrieval_policy.v1 import (
    EDITORIAL_ROLES,
    POLICY,
    RETRIEVAL_PURPOSES,
    UNKNOWN_PURPOSE,
    editorial_role_is_eligible,
    solution_is_visible,
)
from ...providers.contracts import EmbeddingProvider
from ...providers.errors import ProviderError
from ...providers.models import EmbeddingRequest
from .embedding import coverage as corpus_coverage
from .rights import max_excerpt_chars, may_expose_literal_text

#: Backend de producao: pgvector sobre o indice HNSW parcial do espaco ativo.
PGVECTOR_BACKEND = "PGVECTOR"
#: Backend de TESTE: varredura exata em processo. Nao e ANN e nao se equipara
#: em desempenho - ver o docstring do modulo.
EXACT_BACKEND = "EXACT_IN_PROCESS"

#: Convencao do ``score``, declarada na resposta. Ver o docstring do modulo.
SCORE_SEMANTICS = "COSINE_SIMILARITY_HIGHER_IS_BETTER"

_SOLUTION = "SOLUTION"
_COMMERCIAL = "COMMERCIAL_REFERENCE"

#: Distancia atribuida a um vetor de norma zero, para que ele ordene POR
#: ULTIMO. Alinha o backend em processo ao PostgreSQL, onde ``<=>`` devolve
#: NaN e NaN ordena depois de qualquer numero. Nenhum modelo real produz
#: vetor nulo; a regra existe para que o caso degenerado nao derrube a busca
#: nem divirja entre dialetos.
_DEGENERATE_DISTANCE = 2.0

#: Rotulo do ramo "nao rejeitado" da escada. Participa do WHERE e do
#: GROUP BY, e por isso precisa ser uma constante e nao um literal solto.
_KEPT = "KEPT"

#: Escopo de ``filtered_out``. Ver o campo homonimo em VectorSearchResult.
FILTERED_OUT_SCOPE = "ACTIVE_CORPUS"


class VectorSearchError(ValueError):
    """Pedido que nao pode ser atendido. ``code`` e estavel e vira 422/409."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class VectorHit:
    """Um resultado, com rastreabilidade ate a pagina e sem literal comercial.

    ``page_start``/``page_end`` sao paginas IMPRESSAS - o ``page_offset`` do
    documento foi aplicado no chunking e esta camada nao o reaplica.
    """

    chunk_id: UUID
    rank: int
    #: Similaridade de cosseno. MAIOR e melhor. Ver ``SCORE_SEMANTICS``.
    score: float
    #: Distancia de cosseno. MENOR e melhor. ``score + distance == 1``.
    distance: float
    chunk_type: str
    ordinal: int
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    text_hash: str
    char_count: int | None
    editorial_role: str
    editorial_detector_version: str | None
    content_node_id: UUID | None
    bncc_node_codes: tuple[str, ...]
    document_id: UUID
    document_filename: str
    source_id: UUID
    source_title: str
    source_kind: str
    rights_class: str
    authority_level: str
    quotable: bool
    excerpt: str | None
    explanation: dict[str, Any]


@dataclass(frozen=True)
class VectorSearchResult:
    query: str
    hits: tuple[VectorHit, ...]
    total_candidates: int
    returned: int
    limit: int
    offset: int
    has_more: bool
    candidate_cap_reached: bool
    query_fingerprint: str
    #: Identidade do espaco. Provider e modelo NAO saem - ver o docstring.
    embedding_space_id: UUID | None
    embedding_space_fingerprint: str | None
    dimensions: int | None
    distance_metric: str | None
    vector_backend: str
    score_semantics: str
    retrieval_purpose: str
    solution_visible: bool
    #: Cobertura do espaco ativo no momento da busca.
    coverage: dict[str, Any]
    #: Verdadeiro so com ``allow_degraded=True`` sobre corpus incompleto.
    degraded: bool
    degradation_reasons: tuple[str, ...]
    policy: dict[str, Any]
    empty_reasons: tuple[str, ...]
    filtered_out: dict[str, int]
    #: Sobre QUE conjunto ``filtered_out`` conta. Desde o passo 4.1 e o corpus
    #: ATIVO inteiro; antes era o conjunto candidato ja cortado, o que fazia a
    #: contagem variar com a consulta. Sai nomeado para que a mudanca seja
    #: observavel em vez de uma surpresa entre duas medicoes.
    filtered_out_scope: str
    source_distribution: dict[str, int]
    distinct_sources: int
    chunk_type_distribution: dict[str, int]
    duration_seconds: float
    applied_filters: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Candidate:
    chunk_id: UUID
    distance: float
    ordinal: int
    chunk_type: str
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    text_hash: str
    char_count: int | None
    editorial_role: str
    editorial_detector_version: str | None
    content_node_id: UUID | None
    bncc_node_codes: tuple[str, ...]
    document_id: UUID
    document_filename: str
    source_id: UUID
    source_title: str
    source_kind: str
    rights_class: str
    authority_level: str


class VectorSearcher:
    """Busca por similaridade de cosseno sobre o espaco de embedding ATIVO.

    ``provider`` e qualquer coisa que satisfaca ``EmbeddingProvider``. O
    modelo com que a CONSULTA e vetorizada vem da linha do espaco ativo - a
    mesma com que os chunks foram vetorizados. Embeddar a consulta com outro
    modelo produziria distancias sem significado, e nao ha como isso acontecer
    aqui: o modelo nao e parametro deste servico.
    """

    def __init__(self, session: AsyncSession, *, provider: EmbeddingProvider) -> None:
        self.session = session
        self._provider = provider

    async def search(
        self,
        query: str,
        *,
        retrieval_purpose: str | None = None,
        source_kinds: Sequence[str] | None = None,
        source_ids: Sequence[UUID] | None = None,
        document_ids: Sequence[UUID] | None = None,
        chunk_types: Sequence[str] | None = None,
        content_node_id: UUID | None = None,
        include_descendant_nodes: bool = True,
        rights_classes: Sequence[str] | None = None,
        exclude_commercial: bool = False,
        limit: int | None = None,
        offset: int = 0,
        max_per_source: int | None = None,
        allow_degraded: bool = False,
        candidate_cap: int | None = None,
    ) -> VectorSearchResult:
        started = datetime.now()
        limit = POLICY.default_limit if limit is None else limit
        purpose = self._validated_purpose(retrieval_purpose)
        self._validate(limit=limit, offset=offset)

        applied: dict[str, Any] = {
            "retrieval_purpose": purpose,
            "source_kinds": list(source_kinds) if source_kinds else None,
            "source_ids": [str(item) for item in source_ids] if source_ids else None,
            "document_ids": (
                [str(item) for item in document_ids] if document_ids else None
            ),
            "chunk_types": list(chunk_types) if chunk_types else None,
            "content_node_id": str(content_node_id) if content_node_id else None,
            "include_descendant_nodes": include_descendant_nodes,
            "rights_classes": list(rights_classes) if rights_classes else None,
            "exclude_commercial": exclude_commercial,
            "max_per_source": max_per_source,
            "allow_degraded": allow_degraded,
            "candidate_cap": _effective_cap(candidate_cap),
            # Nao e coluna: e o VEREDITO da politica versionada sobre papel
            # editorial e proposito. Sai nomeado para que "por que este chunk
            # nao veio?" tenha resposta sem ler codigo.
            "retrieval_eligibility": "POLICY_V1_BY_ROLE_AND_PURPOSE",
        }

        space = await self._active_space()
        if space is None:
            return self._empty(
                query=query, reasons=("NO_ACTIVE_EMBEDDING_SPACE",), limit=limit,
                offset=offset, purpose=purpose, applied=applied, space=None,
                coverage={}, degraded=False, degradation_reasons=(),
                backend=self._backend_name(), started=started,
            )

        cobertura, reasons = await self._coverage_check(space)
        if reasons and not allow_degraded:
            # Falha FECHADA. Um ranking sobre corpus incompleto com cara de
            # completo e o erro que ninguem percebe.
            raise VectorSearchError(
                "INCOMPLETE_COVERAGE",
                "O espaco de embedding ativo nao cobre o corpus elegivel "
                f"({', '.join(reasons)}). Rode o backfill, ou passe "
                "allow_degraded=True para diagnostico - a resposta sai "
                "marcada como degradada.",
            )
        degraded = bool(reasons)

        if not query.strip():
            return self._empty(
                query=query, reasons=("EMPTY_QUERY",), limit=limit, offset=offset,
                purpose=purpose, applied=applied, space=space, coverage=cobertura,
                degraded=degraded, degradation_reasons=reasons,
                backend=self._backend_name(), started=started,
            )

        vector = await self._embed_query(query, space)

        allowed_nodes: set[UUID] | None = None
        if content_node_id is not None:
            allowed_nodes = await self._node_subtree(
                content_node_id, include_descendants=include_descendant_nodes
            )
        ladder = _rejection_ladder(
            purpose=purpose,
            source_kinds=source_kinds,
            source_ids=source_ids,
            document_ids=document_ids,
            chunk_types=chunk_types,
            allowed_nodes=allowed_nodes,
            rights_classes=rights_classes,
            exclude_commercial=exclude_commercial,
        )
        # ATRIBUICAO sobre o corpus ATIVO INTEIRO, antes de qualquer corte.
        # Uma unica agregacao, e e a MESMA escada que filtra - divergir e
        # impossivel porque e a mesma expressao SQL.
        counters = await self._attribution(space, ladder)

        kept, cap_reached = await self._candidates(
            space, vector, ladder=ladder, cap=candidate_cap
        )
        self._assert_one_active_representation(kept)
        total_candidates = len(kept)

        # DESEMPATE DETERMINISTICO. Dois chunks com o mesmo vetor existem - o
        # mesmo enunciado em duas obras, por exemplo -, e sem criterio
        # secundario a ordem viria da varredura do banco e mudaria entre
        # execucoes. ``chunk_id`` fecha a chave: nunca ha empate total.
        kept.sort(
            key=lambda item: (
                item.distance,
                str(item.source_id),
                str(item.document_id),
                item.ordinal,
                str(item.chunk_id),
            )
        )
        if max_per_source:
            kept = self._cap_per_source(kept, max_per_source)

        if not kept:
            motivos = self._filter_reasons(counters)
            if not motivos:
                motivos = (
                    ("CANDIDATE_CAP_EXHAUSTED",) if cap_reached else ("EMPTY_CORPUS",)
                )
            return self._empty(
                query=query, reasons=motivos, limit=limit, offset=offset,
                purpose=purpose, applied=applied, space=space, coverage=cobertura,
                degraded=degraded, degradation_reasons=reasons,
                backend=self._backend_name(), started=started,
                filtered_out=counters, total_candidates=total_candidates,
                cap_reached=cap_reached,
            )

        page = kept[offset : offset + limit]
        excerpts = await self._excerpts(page)
        hits = tuple(
            self._hit(
                candidate,
                rank=offset + index + 1,
                space=space,
                purpose=purpose,
                degraded=degraded,
                excerpts=excerpts,
            )
            for index, candidate in enumerate(page)
        )

        return VectorSearchResult(
            query=query,
            hits=hits,
            total_candidates=total_candidates,
            returned=len(hits),
            limit=limit,
            offset=offset,
            has_more=offset + len(hits) < len(kept),
            candidate_cap_reached=cap_reached,
            query_fingerprint=_fingerprint(
                query=query, applied=applied, space=space, backend=self._backend_name()
            ),
            embedding_space_id=space.id,
            embedding_space_fingerprint=_space_fingerprint(space),
            dimensions=space.dimensions,
            distance_metric=space.distance_metric,
            vector_backend=self._backend_name(),
            score_semantics=SCORE_SEMANTICS,
            retrieval_purpose=purpose,
            solution_visible=solution_is_visible(purpose),
            coverage=cobertura,
            degraded=degraded,
            degradation_reasons=reasons,
            policy=POLICY.snapshot(),
            empty_reasons=(),
            filtered_out=dict(counters),
            filtered_out_scope=FILTERED_OUT_SCOPE,
            source_distribution=_distribution(hit.source_title for hit in hits),
            distinct_sources=len({hit.source_id for hit in hits}),
            chunk_type_distribution=_distribution(hit.chunk_type for hit in hits),
            duration_seconds=(datetime.now() - started).total_seconds(),
            applied_filters=applied,
        )

    async def ranked_chunks(
        self, query: str, **kwargs: Any
    ) -> tuple[tuple[UUID, int, float, dict[str, Any]], ...]:
        """O contrato minimo que a Fase 7 consome: ``(chunk_id, rank, score,
        explanation)``, sem conhecer backend vetorial algum.

        Existe como metodo proprio para que o fundidor da Fase 7 nao precise
        importar ``VectorHit`` nem saber que ele tem excerpt, direitos ou
        paginas - o que ele precisa e de rank e de poder explicar de onde veio.
        """
        resultado = await self.search(query, **kwargs)
        return tuple(
            (hit.chunk_id, hit.rank, hit.score, hit.explanation)
            for hit in resultado.hits
        )

    # -- espaco ativo e cobertura ----------------------------------------

    async def _active_space(self) -> KnowledgeEmbeddingSpace | None:
        return await self.session.scalar(
            select(KnowledgeEmbeddingSpace).where(
                KnowledgeEmbeddingSpace.status == "ACTIVE"
            )
        )

    async def _coverage_check(
        self, space: KnowledgeEmbeddingSpace
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        """Compara a cobertura de AGORA com o que foi declarado na ATIVACAO.

        Reutiliza deliberadamente os artefatos do passo 3 em vez de inventar
        criterio proprio: ``expected_population`` ja foi declarado por quem
        ativou o espaco, e reusa-lo e o que impede a busca de ter uma segunda
        definicao de "corpus completo" divergindo da primeira.
        """
        cobertura = await corpus_coverage(self.session, space.id)
        ativacao = await self.session.scalar(
            select(KnowledgeEmbeddingActivation)
            .where(KnowledgeEmbeddingActivation.space_id == space.id)
            .order_by(KnowledgeEmbeddingActivation.created_at.desc())
            .limit(1)
        )

        motivos: list[str] = []
        if ativacao is None:
            # O espaco e ACTIVE mas suas linhas nunca foram ligadas - e o
            # estado em que a migracao 058 deixa o piloto.
            motivos.append("NEVER_ACTIVATED")
        else:
            if ativacao.degraded:
                motivos.append("ACTIVATED_DEGRADED")
            if cobertura.eligible_chunks < ativacao.expected_population:
                motivos.append("POPULATION_BELOW_EXPECTED")
        if cobertura.missing:
            motivos.append("MISSING_EMBEDDINGS")
        if cobertura.stale:
            motivos.append("STALE_EMBEDDINGS")

        resumo = {
            "policy": cobertura.policy,
            "eligible_chunks": cobertura.eligible_chunks,
            "embedded": cobertura.embedded,
            "missing": cobertura.missing,
            "stale": cobertura.stale,
            "expected_population": (
                ativacao.expected_population if ativacao else None
            ),
            "activation_id": str(ativacao.id) if ativacao else None,
            "activated_degraded": bool(ativacao.degraded) if ativacao else None,
        }
        return resumo, tuple(motivos)

    # -- consulta vetorizada ---------------------------------------------

    async def _embed_query(
        self, query: str, space: KnowledgeEmbeddingSpace
    ) -> tuple[float, ...]:
        """Vetoriza a consulta com o MODELO DO ESPACO ATIVO.

        O modelo nao e parametro deste servico: ele vem da linha do espaco, a
        mesma com que os chunks foram vetorizados. Nao ha caminho de codigo em
        que a consulta e o corpus sejam embeddados por modelos diferentes.
        """
        try:
            resultado = await self._provider.embed(
                EmbeddingRequest(texts=(query,), model=space.model)
            )
        except ProviderError as error:
            raise VectorSearchError(
                "EMBEDDING_PROVIDER_UNAVAILABLE",
                f"Nao foi possivel vetorizar a consulta: {type(error).__name__}",
            ) from error

        if len(resultado.artifacts) != 1:
            raise VectorSearchError(
                "EMBEDDING_PROVIDER_INVALID_RESPONSE",
                f"Esperado 1 vetor para a consulta, recebido "
                f"{len(resultado.artifacts)}",
            )
        vector = resultado.artifacts[0].vector
        if len(vector) != space.dimensions:
            # Mesma trava do passo 2, do outro lado: consulta e corpus TEM de
            # viver no mesmo espaco, ou a distancia nao significa nada.
            raise VectorSearchError(
                "QUERY_DIMENSION_MISMATCH",
                f"O espaco ativo declara {space.dimensions} dimensoes e o "
                f"provider devolveu {len(vector)} para a consulta",
            )
        if not any(vector):
            raise VectorSearchError(
                "QUERY_VECTOR_DEGENERATE",
                "O provider devolveu vetor nulo para a consulta; cosseno nao "
                "esta definido",
            )
        return tuple(float(component) for component in vector)

    # -- candidatos -------------------------------------------------------

    def _backend_name(self) -> str:
        return (
            PGVECTOR_BACKEND
            if self.session.get_bind().dialect.name == "postgresql"
            else EXACT_BACKEND
        )

    async def _attribution(
        self, space: KnowledgeEmbeddingSpace, ladder
    ) -> dict[str, int]:
        """Quantos chunks cada politica exclui - sobre o corpus ATIVO INTEIRO.

        MUDANCA DE SEMANTICA do passo 4, e deliberada. Antes a contagem era
        "dentro das 2.000 linhas mais proximas", o que fazia dela um artefato
        do corte: o mesmo filtro produzia numeros diferentes conforme a
        consulta. Agora e sobre o corpus ativo, uma unica agregacao, e
        responde a pergunta que de fato se faz - "quanto do acervo esta
        fechado para este proposito?".

        ``filtered_out_scope`` sai na resposta para que a mudanca seja um
        dado observavel e nao uma surpresa entre duas medicoes.
        """
        consulta = (
            select(ladder.label("reason"), func.count().label("n"))
            .select_from(KnowledgeChunkEmbedding)
            .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeChunkEmbedding.chunk_id)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
            .where(KnowledgeChunkEmbedding.space_id == space.id)
            .where(KnowledgeChunkEmbedding.is_active.is_(True))
            .where(KnowledgeChunk.text_hash == KnowledgeChunkEmbedding.text_hash)
            .group_by(ladder)
        )
        linhas = (await self.session.execute(consulta)).all()
        return {motivo: quantos for motivo, quantos in linhas if motivo != _KEPT}

    async def _candidates(
        self,
        space: KnowledgeEmbeddingSpace,
        vector: Sequence[float],
        *,
        ladder,
        cap: int | None,
    ) -> tuple[list[_Candidate], bool]:
        """Conjunto candidato JA FILTRADO, e so depois cortado.

        A ordem importa e foi o defeito do passo 4: formar as 2.000 mais
        proximas do acervo inteiro e so entao filtrar produz FALSO VAZIO -
        medido no corpus real, uma busca restrita a BNCC nao achava nenhum
        dos 23 chunks em 4 de 7 consultas, porque nenhum deles estava entre
        as 2.000 primeiras. O material existia; a busca dizia que nao.

        Com o filtro antes do corte, as 2.000 sao as 2.000 mais proximas
        DENTRE AS ELEGIVEIS, que e o que o cap sempre quis dizer.
        """
        efetivo = _effective_cap(cap)
        if self._backend_name() == PGVECTOR_BACKEND:
            rows = await self._pgvector_rows(space, vector, efetivo + 1, ladder)
        else:
            rows = await self._exact_rows(space, vector, efetivo + 1, ladder)
        cap_reached = len(rows) > efetivo
        return [self._candidate(row) for row in rows[:efetivo]], cap_reached

    def _base_query(self, space: KnowledgeEmbeddingSpace, ladder=None):
        """As colunas e os vinculos comuns aos dois backends.

        Tres condicoes, e nenhuma e redundante:

        - ``space_id == espaco ativo``  - isolamento entre espacos;
        - ``is_active``                 - linha efetivamente ligada;
        - ``text_hash`` casando com o do chunk - um vetor ATIVO cujo texto
          mudou depois da ativacao e OBSOLETO, e obsoleto nunca e recuperado,
          mesmo em busca degradada.

        E a QUARTA, desde o passo 4.1: a escada de rejeicao, aplicada AQUI,
        antes do corte. ``ladder == KEPT`` e literalmente a mesma expressao
        com que a atribuicao conta - nao ha como as duas divergirem.

        O ``raw_text`` NAO esta aqui. O literal de fonte comercial nao e lido
        nem por engano nesta etapa.
        """
        consulta = (
            select(
                KnowledgeChunkEmbedding.chunk_id,
                KnowledgeChunk.ordinal,
                KnowledgeChunk.chunk_type,
                KnowledgeChunk.heading_path,
                KnowledgeChunk.page_start,
                KnowledgeChunk.page_end,
                KnowledgeChunk.text_hash,
                KnowledgeChunk.char_count,
                KnowledgeChunk.editorial_role,
                KnowledgeChunk.editorial_detector_version,
                KnowledgeChunk.content_node_id,
                KnowledgeChunk.bncc_node_codes,
                KnowledgeChunk.document_id,
                KnowledgeDocument.filename,
                KnowledgeSource.id,
                KnowledgeSource.title,
                KnowledgeSource.source_kind,
                KnowledgeSource.rights_class,
                KnowledgeSource.authority_level,
            )
            .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeChunkEmbedding.chunk_id)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
            .where(KnowledgeChunkEmbedding.space_id == space.id)
            .where(KnowledgeChunkEmbedding.is_active.is_(True))
            .where(KnowledgeChunk.text_hash == KnowledgeChunkEmbedding.text_hash)
        )
        if ladder is not None:
            consulta = consulta.where(ladder == _KEPT)
        return consulta

    async def _pgvector_rows(
        self, space: KnowledgeEmbeddingSpace, vector: Sequence[float], cap: int,
        ladder=None,
    ):
        """Distancia calculada pelo pgvector, ordenada pelo indice HNSW.

        O cast ``::vector(n)`` e interpolado porque modificador de tipo nao
        aceita parametro vinculado; ``n`` vem de uma coluna inteira do banco,
        nunca de entrada do usuario. E o mesmo cast com que a migracao 058
        criou o indice parcial - sem ele o indice nao seria usado.
        """
        dims = int(space.dimensions)
        literal = _vector_literal(vector)
        distance = literal_column(
            f"knowledge_chunk_embeddings.embedding::vector({dims}) "
            f"<=> '{literal}'::vector({dims})"
        )
        # O corte do conjunto candidato tem de ser deterministico TAMBEM aqui,
        # e nao so na ordenacao final em Python: com empate na distancia, quem
        # entra no corte e quem fica de fora nao pode depender da varredura.
        consulta = (
            self._base_query(space, ladder)
            .add_columns(distance.label("distance"))
            .order_by(distance, KnowledgeChunkEmbedding.chunk_id)
            .limit(cap)
        )
        return (await self.session.execute(consulta)).all()

    async def _exact_rows(
        self, space: KnowledgeEmbeddingSpace, vector: Sequence[float], cap: int,
        ladder=None,
    ):
        """Varredura EXATA em processo - backend de teste, nao de producao.

        Exata e uma vantagem aqui: num corpus sintetico o ranking esperado e
        calculavel a mao, e o teste afirma um numero em vez de aceitar o que
        saiu. Nao simula ANN e nao se equipara em desempenho ao PostgreSQL.
        """
        consulta = self._base_query(space, ladder).add_columns(
            KnowledgeChunkEmbedding.embedding
        )
        rows = (await self.session.execute(consulta)).all()
        norma = math.sqrt(sum(component * component for component in vector))
        medidos = []
        for row in rows:
            armazenado = row[-1] or ()
            distancia = _cosine_distance(vector, armazenado, query_norm=norma)
            medidos.append(tuple(row[:-1]) + (distancia,))
        medidos.sort(key=lambda row: (row[-1], str(row[0])))
        return medidos[:cap]

    @staticmethod
    def _candidate(row) -> _Candidate:
        return _Candidate(
            chunk_id=row[0],
            ordinal=row[1],
            chunk_type=row[2],
            heading_path=tuple(row[3] or ()),
            page_start=row[4],
            page_end=row[5],
            text_hash=row[6],
            char_count=row[7],
            editorial_role=row[8],
            editorial_detector_version=row[9],
            content_node_id=row[10],
            bncc_node_codes=tuple(row[11] or ()),
            document_id=row[12],
            document_filename=row[13],
            source_id=row[14],
            source_title=row[15],
            source_kind=row[16],
            rights_class=row[17],
            authority_level=row[18],
            distance=float(row[19]),
        )

    @staticmethod
    def _assert_one_active_representation(candidates: Sequence[_Candidate]) -> None:
        """A garantia da migracao 063, reafirmada onde ela importa.

        O indice parcial unico ja a torna irrepresentavel. Esta checagem custa
        quase nada e transforma uma eventual quebra em erro nomeado, aqui, em
        vez de num ranking com o mesmo chunk duas vezes.
        """
        vistos = {candidate.chunk_id for candidate in candidates}
        if len(vistos) != len(candidates):
            raise VectorSearchError(
                "DUPLICATE_ACTIVE_REPRESENTATION",
                "O mesmo chunk apareceu mais de uma vez entre os vetores "
                "ativos; a trava de uma representacao ativa por chunk foi "
                "violada",
            )

    # -- filtros -----------------------------------------------------------

    async def _node_subtree(
        self, content_node_id: UUID, *, include_descendants: bool
    ) -> set[UUID]:
        if not include_descendants:
            return {content_node_id}
        node = await self.session.get(CatalogNode, content_node_id)
        if node is None:
            return {content_node_id}
        rows = (
            await self.session.execute(
                select(CatalogNode.id, CatalogNode.parent_id).where(
                    CatalogNode.root_id == (node.root_id or node.id)
                )
            )
        ).all()
        children: dict[UUID, list[UUID]] = {}
        for identifier, parent in rows:
            children.setdefault(parent, []).append(identifier)
        subtree = {content_node_id}
        frontier = [content_node_id]
        while frontier:
            current = frontier.pop()
            for child in children.get(current, ()):
                if child not in subtree:
                    subtree.add(child)
                    frontier.append(child)
        return subtree

    @staticmethod
    def _filter_reasons(counters: Mapping[str, int]) -> tuple[str, ...]:
        mapping = {
            "PURPOSE_SOLUTION": "FILTERED_OUT_BY_PURPOSE",
            "EDITORIAL_ROLE": "FILTERED_OUT_BY_EDITORIAL_ROLE",
            "SOURCE_KIND": "FILTERED_OUT_BY_SOURCE_KIND",
            "SOURCE": "FILTERED_OUT_BY_SOURCE",
            "DOCUMENT": "FILTERED_OUT_BY_DOCUMENT",
            "CHUNK_TYPE": "FILTERED_OUT_BY_CHUNK_TYPE",
            "CONTENT_NODE": "FILTERED_OUT_BY_CONTENT_NODE",
            "RIGHTS": "FILTERED_OUT_BY_RIGHTS",
        }
        return tuple(
            mapping[key] for key in counters if counters[key] and key in mapping
        )

    @staticmethod
    def _cap_per_source(
        candidates: Sequence[_Candidate], cap: int
    ) -> list[_Candidate]:
        seen: dict[UUID, int] = {}
        kept = []
        for candidate in candidates:
            if seen.get(candidate.source_id, 0) >= cap:
                continue
            seen[candidate.source_id] = seen.get(candidate.source_id, 0) + 1
            kept.append(candidate)
        return kept

    # -- montagem ----------------------------------------------------------

    async def _excerpts(self, candidates: Sequence[_Candidate]) -> dict[UUID, str]:
        """Excerpt SO da pagina devolvida, e SO de fonte citavel.

        O literal de fonte comercial nunca e lido aqui: ela nao entra na
        lista."""
        wanted = [
            candidate.chunk_id
            for candidate in candidates
            if may_expose_literal_text(candidate.rights_class)
        ]
        if not wanted:
            return {}
        rows = (
            await self.session.execute(
                select(KnowledgeChunk.id, KnowledgeChunk.raw_text).where(
                    KnowledgeChunk.id.in_(wanted)
                )
            )
        ).all()
        return {identifier: texto or "" for identifier, texto in rows}

    def _hit(
        self,
        candidate: _Candidate,
        *,
        rank: int,
        space: KnowledgeEmbeddingSpace,
        purpose: str,
        degraded: bool,
        excerpts: Mapping[UUID, str],
    ) -> VectorHit:
        quotable = may_expose_literal_text(candidate.rights_class)
        limite = max_excerpt_chars(candidate.rights_class)
        excerpt = None
        if quotable and limite:
            bruto = excerpts.get(candidate.chunk_id, "")
            excerpt = bruto[:limite] if bruto else None

        similaridade = 1.0 - candidate.distance
        return VectorHit(
            chunk_id=candidate.chunk_id,
            rank=rank,
            score=similaridade,
            distance=candidate.distance,
            chunk_type=candidate.chunk_type,
            ordinal=candidate.ordinal,
            heading_path=candidate.heading_path,
            page_start=candidate.page_start,
            page_end=candidate.page_end,
            text_hash=candidate.text_hash,
            char_count=candidate.char_count,
            editorial_role=candidate.editorial_role,
            editorial_detector_version=candidate.editorial_detector_version,
            content_node_id=candidate.content_node_id,
            bncc_node_codes=candidate.bncc_node_codes,
            document_id=candidate.document_id,
            document_filename=candidate.document_filename,
            source_id=candidate.source_id,
            source_title=candidate.source_title,
            source_kind=candidate.source_kind,
            rights_class=candidate.rights_class,
            authority_level=candidate.authority_level,
            quotable=quotable,
            excerpt=excerpt,
            explanation={
                # Identidade do espaco SEM nomear fornecedor nem modelo.
                "embedding_space_id": str(space.id),
                "embedding_space_fingerprint": _space_fingerprint(space),
                "dimensions": space.dimensions,
                "distance_metric": space.distance_metric,
                "vector_backend": self._backend_name(),
                "score_semantics": SCORE_SEMANTICS,
                "distance": candidate.distance,
                "similarity": similaridade,
                "retrieval_purpose": purpose,
                "retrieval_eligible": True,
                "editorial_role": candidate.editorial_role,
                "editorial_detector_version": candidate.editorial_detector_version,
                "chunk_type": candidate.chunk_type,
                "rights_class": candidate.rights_class,
                "quotable": quotable,
                "policy_version": POLICY.version,
                "degraded": degraded,
            },
        )

    def _empty(
        self,
        *,
        query: str,
        reasons: tuple[str, ...],
        limit: int,
        offset: int,
        purpose: str,
        applied: dict[str, Any],
        space: KnowledgeEmbeddingSpace | None,
        coverage: dict[str, Any],
        degraded: bool,
        degradation_reasons: tuple[str, ...],
        backend: str,
        started: datetime,
        filtered_out: Mapping[str, int] | None = None,
        total_candidates: int = 0,
        cap_reached: bool = False,
    ) -> VectorSearchResult:
        return VectorSearchResult(
            query=query,
            hits=(),
            total_candidates=total_candidates,
            returned=0,
            limit=limit,
            offset=offset,
            has_more=False,
            candidate_cap_reached=cap_reached,
            query_fingerprint=_fingerprint(
                query=query, applied=applied, space=space, backend=backend
            ),
            embedding_space_id=space.id if space else None,
            embedding_space_fingerprint=_space_fingerprint(space) if space else None,
            dimensions=space.dimensions if space else None,
            distance_metric=space.distance_metric if space else None,
            vector_backend=backend,
            score_semantics=SCORE_SEMANTICS,
            retrieval_purpose=purpose,
            solution_visible=solution_is_visible(purpose),
            coverage=coverage,
            degraded=degraded,
            degradation_reasons=degradation_reasons,
            policy=POLICY.snapshot(),
            empty_reasons=reasons,
            filtered_out=dict(filtered_out or {}),
            filtered_out_scope=FILTERED_OUT_SCOPE,
            source_distribution={},
            distinct_sources=0,
            chunk_type_distribution={},
            duration_seconds=(datetime.now() - started).total_seconds(),
            applied_filters=applied,
        )

    # -- validacao ---------------------------------------------------------

    @staticmethod
    def _validated_purpose(purpose: str | None) -> str:
        if purpose is None:
            return UNKNOWN_PURPOSE
        if purpose not in RETRIEVAL_PURPOSES:
            raise VectorSearchError(
                "INVALID_RETRIEVAL_PURPOSE",
                f"retrieval_purpose {purpose!r} desconhecido; esperado um de "
                f"{list(RETRIEVAL_PURPOSES)}. Ausencia e tratada como "
                f"{UNKNOWN_PURPOSE}, que FECHA SOLUTION",
            )
        return purpose

    @staticmethod
    def _validate(*, limit: int, offset: int) -> None:
        if limit < 1 or limit > POLICY.max_limit:
            raise VectorSearchError(
                "LIMIT_TOO_LARGE", f"limit {limit} fora de 1..{POLICY.max_limit}"
            )
        if offset < 0 or offset > POLICY.candidate_cap:
            raise VectorSearchError(
                "OFFSET_OUT_OF_RANGE",
                f"offset {offset} fora de 0..{POLICY.candidate_cap}",
            )


def _effective_cap(cap: int | None) -> int:
    """O cap da politica, ou um MENOR pedido pelo chamador.

    Nunca MAIOR. O parametro existe para teste e diagnostico - um corpus
    sintetico de dez chunks nao consegue demonstrar um corte em 2.000 -, e
    limitar para cima impede que ele vire atalho justamente para o defeito
    que o passo 4.1 corrigiu.
    """
    if cap is None:
        return POLICY.candidate_cap
    if cap < 1:
        raise VectorSearchError(
            "INVALID_CANDIDATE_CAP", f"candidate_cap {cap} precisa ser >= 1"
        )
    return min(cap, POLICY.candidate_cap)


def _rejection_ladder(
    *,
    purpose: str,
    source_kinds: Sequence[str] | None,
    source_ids: Sequence[UUID] | None,
    document_ids: Sequence[UUID] | None,
    chunk_types: Sequence[str] | None,
    allowed_nodes: set[UUID] | None,
    rights_classes: Sequence[str] | None,
    exclude_commercial: bool,
) -> Case:
    """A escada de rejeicao como UMA expressao SQL.

    POR QUE UMA SO EXPRESSAO
    ========================

    Ela e usada em dois lugares: no ``WHERE`` que forma o conjunto candidato
    (``ladder == 'KEPT'``) e no ``GROUP BY`` que conta quantos cada politica
    excluiu. Fossem duas expressoes, divergiriam - e divergiriam em silencio,
    porque um relatorio de atribuicao errado continua parecendo um relatorio.
    Sendo a mesma, nao ha como.

    POR QUE A ORDEM E ESTA
    ======================

    E a mesma da perna lexical, e `CASE` para no primeiro ramo verdadeiro -
    entao o motivo atribuido e o de MAIOR precedencia, exatamente como o
    ``return`` precoce do ``_rejection`` do passo 4 fazia. Politica primeiro,
    preferencia de consulta depois: saber que um chunk foi barrado por
    direitos importa mais do que saber que ele tambem nao era do
    ``chunk_type`` pedido.

    A POLITICA CONTINUA EM PYTHON
    =============================

    O SQL nunca decide elegibilidade. ``editorial_role_is_eligible`` e
    ``solution_is_visible`` sao avaliadas aqui, em Python, e o que desce para
    o banco e uma LISTA DE VALORES concretos. A politica versionada segue
    sendo a unica autoridade, e a falha ABERTA de papel desconhecido se
    preserva naturalmente: papel que a politica nao conhece nao entra na
    lista de inelegiveis, logo nao e rejeitado.
    """
    ramos: list[tuple[Any, str]] = []

    if not solution_is_visible(purpose):
        ramos.append((KnowledgeChunk.chunk_type == _SOLUTION, "PURPOSE_SOLUTION"))

    inelegiveis = tuple(
        papel
        for papel in EDITORIAL_ROLES
        if not editorial_role_is_eligible(papel, purpose)
    )
    if inelegiveis:
        ramos.append(
            (
                func.coalesce(KnowledgeChunk.editorial_role, "UNKNOWN").in_(
                    inelegiveis
                ),
                "EDITORIAL_ROLE",
            )
        )

    if source_kinds:
        ramos.append(
            (KnowledgeSource.source_kind.notin_(tuple(source_kinds)), "SOURCE_KIND")
        )
    if source_ids:
        ramos.append(
            (KnowledgeChunk.source_id.notin_(tuple(source_ids)), "SOURCE")
        )
    if document_ids:
        ramos.append(
            (KnowledgeChunk.document_id.notin_(tuple(document_ids)), "DOCUMENT")
        )
    if chunk_types:
        ramos.append(
            (KnowledgeChunk.chunk_type.notin_(tuple(chunk_types)), "CHUNK_TYPE")
        )
    if allowed_nodes is not None:
        # ``NOT IN`` com NULL devolve NULL, e NULL nao e verdadeiro - um chunk
        # sem no de curriculo escaparia do filtro. O ``IS NULL`` explicito e o
        # que faz "nao mapeado" contar como "fora do no pedido".
        ramos.append(
            (
                or_(
                    KnowledgeChunk.content_node_id.is_(None),
                    KnowledgeChunk.content_node_id.notin_(tuple(allowed_nodes)),
                ),
                "CONTENT_NODE",
            )
        )
    if rights_classes:
        ramos.append(
            (KnowledgeSource.rights_class.notin_(tuple(rights_classes)), "RIGHTS")
        )
    if exclude_commercial:
        ramos.append((KnowledgeSource.rights_class == _COMMERCIAL, "RIGHTS"))

    return case(*ramos, else_=literal(_KEPT))


_VECTOR_LITERAL_CHARS = set("0123456789.,-+e[]")


def _vector_literal(vector: Sequence[float]) -> str:
    """Literal de vetor para o SQL do pgvector.

    Interpolado, e nao vinculado, porque o operando do ``::vector(n)`` precisa
    estar no texto da instrucao junto com o modificador de tipo. A seguranca
    nao vem de confiar na origem: cada componente passa por ``float()``, e o
    resultado e conferido contra um alfabeto fechado. Nenhum caractere que
    pudesse encerrar um literal sobrevive a ``float()``, e a verificacao
    abaixo torna isso uma afirmacao do codigo em vez de uma suposicao.
    """
    literal = "[" + ",".join(repr(float(component)) for component in vector) + "]"
    if set(literal) - _VECTOR_LITERAL_CHARS:
        raise VectorSearchError(
            "QUERY_VECTOR_INVALID",
            "O vetor da consulta produziu um literal fora do alfabeto numerico",
        )
    return literal


def _cosine_distance(
    query: Sequence[float], stored: Sequence[float], *, query_norm: float
) -> float:
    """Distancia de cosseno de verdade - nada presume vetor unitario.

    O passo 2 MEDE a norma dos vetores do provider e a registra como
    observabilidade; aqui ela e calculada. Um provider que deixe de
    normalizar vira um fato registrado, nunca um erro silencioso de ranking.
    """
    if len(stored) != len(query):
        # Vetor gravado fora da dimensao do espaco: nao participa do ranking.
        # O portao de ativacao do passo 3 ja o teria recusado, e esta e a
        # segunda linha de defesa.
        return _DEGENERATE_DISTANCE
    stored_norm = math.sqrt(sum(component * component for component in stored))
    if not stored_norm or not query_norm:
        return _DEGENERATE_DISTANCE
    produto = sum(a * b for a, b in zip(query, stored))
    return 1.0 - produto / (query_norm * stored_norm)


def _space_fingerprint(space: KnowledgeEmbeddingSpace) -> str:
    """Identifica o espaco SEM revelar fornecedor nem modelo.

    Permite afirmar "estes dois resultados vieram do mesmo espaco" num
    relatorio publico. Quem opera chega a provider e modelo pelo ``space_id``,
    na tabela de espacos - que e onde essa informacao mora."""
    blob = f"{space.provider}|{space.model}|{space.dimensions}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _fingerprint(
    *,
    query: str,
    applied: Mapping[str, Any],
    space: KnowledgeEmbeddingSpace | None,
    backend: str,
) -> str:
    """Identidade da FOTO: consulta + filtros + espaco + politica + backend.

    O espaco entra porque trocar de modelo muda TODA distancia: dois
    resultados de espacos diferentes nao sao comparaveis, e o fingerprint tem
    de dizer isso em vez de deixar a comparacao parecer legitima.
    """
    payload = {
        "query": query,
        "filters": {key: applied[key] for key in sorted(applied)},
        "embedding_space_id": str(space.id) if space else None,
        "embedding_space_fingerprint": _space_fingerprint(space) if space else None,
        "distance_metric": space.distance_metric if space else None,
        "vector_backend": backend,
        "score_semantics": SCORE_SEMANTICS,
        "policy_version": POLICY.version,
        "editorial_detector_version": POLICY.editorial_detector_version,
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _distribution(values) -> dict[str, int]:
    saida: dict[str, int] = {}
    for value in values:
        saida[value] = saida.get(value, 0) + 1
    return saida
