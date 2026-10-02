"""Busca lexical BM25F sobre o indice invertido proprio.

CONTRATO ESTAVEL, BACKEND SUBSTITUIVEL (spec 23.1)
==================================================

``LexicalSearcher.search()`` devolvendo ``(chunk_id, rank, score, explanation)``
e o contrato publico. O indice invertido proprio NAO e parte permanente da
arquitetura: PostgreSQL FTS, OpenSearch ou outro mecanismo poderao
substitui-lo sem alterar consumidor algum - nem o RRF da Fase 7, nem o
Knowledge Pack. ``lexical_backend`` sai na resposta para que a troca seja um
dado observavel em vez de uma surpresa entre duas medicoes.

ONDE CADA COISA E CALCULADA
===========================

SQL recupera os postings dos termos da consulta, com as estatisticas globais;
Python faz BM25F, frase, proximidade, ordenacao e explicacao. Uma
implementacao so, identica em SQLite e PostgreSQL, zero ``if dialect ==``.

Pior caso medido no livro real: "concentracao das solucoes" traz 991 linhas
(df 376 + 615). BM25 sobre isso em Python e microssegundos.

POR QUE OS FILTROS SAO APLICADOS EM PYTHON
==========================================

Porque filtrar em SQL devolveria o conjunto certo e perderia a ATRIBUICAO: o
numero que o relatorio pede - quantos ``SOLUTION`` o proposito ``PRACTICE``
excluiu - deixaria de existir, e "nada encontrado" nao poderia nomear o filtro
responsavel. O conjunto candidato e limitado por ``df``, e portanto pequeno.
Acima de ~10^6 chunks isso deixa de valer, e o filtro volta para o SQL junto
da troca de backend.

ESTATISTICA GLOBAL, SCORE ESTAVEL
=================================

``df``, ``N`` e ``avgdl`` vem do indice INTEIRO, nunca do conjunto filtrado.
Consequencia: o score de um chunk nao muda porque outro foi filtrado. Sem
isso, o mesmo chunk teria score diferente em ``LEARN`` e em ``AUTHOR``, e
nenhuma comparacao entre execucoes valeria.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import (
    CatalogNode,
    KnowledgeChunk,
    KnowledgeChunkLexicalIndex,
    KnowledgeChunkTerm,
    KnowledgeDocument,
    KnowledgeLexicalIndexState,
    KnowledgeSource,
)
from ...knowledge_retrieval_policy.v1 import (
    POLICY,
    RETRIEVAL_PURPOSES,
    UNKNOWN_PURPOSE,
    editorial_role_is_eligible,
    solution_is_visible,
    statistical_corpus_roles,
)
from .lexical_index import GLOBAL_SCOPE
from .lexical_tokenizer import (
    TokenStream,
    best_proximity_span,
    find_phrase_occurrences,
    tokenize,
)
from .rights import max_excerpt_chars, may_expose_literal_text

PHRASE_MODES: tuple[str, ...] = ("BONUS", "REQUIRED")

_SOLUTION = "SOLUTION"


class LexicalSearchError(ValueError):
    """Pedido mal formado. ``code`` e estavel e vira 422 na rota."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LexicalHit:
    """Um resultado, com rastreabilidade completa e sem texto de obra comercial.

    ``page_start``/``page_end`` sao paginas IMPRESSAS: o ``page_offset`` do
    documento foi aplicado no chunking, e esta camada NAO o reaplica.

    ``excerpt`` existe apenas para fonte que admite citacao literal. Para
    ``COMMERCIAL_REFERENCE`` e sempre ``None``, e o schema de resposta da rota
    nem tem o campo.
    """

    chunk_id: UUID
    rank: int
    score: float
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
class LexicalSearchResult:
    query: str
    normalized_terms: tuple[str, ...]
    dropped_tokens: tuple[dict[str, str], ...]
    unmatched_terms: tuple[str, ...]
    hits: tuple[LexicalHit, ...]
    total_candidates: int
    returned: int
    limit: int
    offset: int
    has_more: bool
    candidate_cap_reached: bool
    query_fingerprint: str
    index_generation: int | None
    corpus_size: int
    avgdl: float
    statistical_corpus_roles: tuple[str, ...]
    retrieval_mode: str
    lexical_backend: str
    retrieval_purpose: str
    phrase_mode: str
    policy: dict[str, Any]
    empty_reasons: tuple[str, ...]
    filtered_out: dict[str, int]
    source_distribution: dict[str, int]
    distinct_sources: int
    coverage_warning: bool
    chunk_type_distribution: dict[str, int]
    duration_seconds: float
    solution_visible: bool = False
    applied_filters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QueryExplanation:
    """Como a consulta foi tokenizada, com ``df``/``idf``, SEM buscar.

    Existe para que "por que ``mols`` nao achou nada?" tenha resposta sem
    adivinhacao. Vive aqui, e nao na rota, porque calcular ``df`` exige ler
    ``knowledge_chunk_terms`` - e a fronteira do subsistema (spec 2) proibe
    modulo de fora tocar o modelo do corpus. O teste de fronteira pegou essa
    violacao quando ela foi introduzida, pela segunda fase consecutiva.
    """

    query: str
    normalizer_version: str
    terms: tuple[dict[str, Any], ...]
    dropped_tokens: tuple[dict[str, str], ...]
    stream_length: int
    corpus_size: int
    avgdl: float
    index_generation: int | None


@dataclass
class _Candidate:
    chunk_id: UUID
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
    token_count: int
    heading_token_count: int
    body: dict[str, int]
    heading: dict[str, int]
    positions: dict[str, tuple[int, ...]]


class LexicalSearcher:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(
        self,
        query: str,
        *,
        retrieval_purpose: str | None = None,
        source_kinds: Sequence[str] | None = None,
        chunk_types: Sequence[str] | None = None,
        content_node_id: UUID | None = None,
        include_descendant_nodes: bool = True,
        rights_classes: Sequence[str] | None = None,
        exclude_commercial: bool = False,
        phrase_mode: str = "BONUS",
        heading_weight: float | None = None,
        limit: int | None = None,
        offset: int = 0,
        max_per_source: int | None = None,
    ) -> LexicalSearchResult:
        started = datetime.now()
        limit = POLICY.default_limit if limit is None else limit
        heading_weight = POLICY.heading_weight if heading_weight is None else heading_weight
        purpose = self._validated_purpose(retrieval_purpose)
        self._validate(phrase_mode=phrase_mode, limit=limit, offset=offset)

        applied = {
            "retrieval_purpose": purpose,
            "source_kinds": list(source_kinds) if source_kinds else None,
            "chunk_types": list(chunk_types) if chunk_types else None,
            "content_node_id": str(content_node_id) if content_node_id else None,
            "include_descendant_nodes": include_descendant_nodes,
            "rights_classes": list(rights_classes) if rights_classes else None,
            "exclude_commercial": exclude_commercial,
            "phrase_mode": phrase_mode,
            "heading_weight": heading_weight,
            "max_per_source": max_per_source,
        }
        stream = tokenize(query)
        dropped = tuple(
            {"surface": surface, "reason": reason} for surface, reason in stream.dropped
        )
        generation = await self.session.scalar(
            select(KnowledgeLexicalIndexState.generation).where(
                KnowledgeLexicalIndexState.scope == GLOBAL_SCOPE
            )
        )
        # CORPUS ESTATISTICO (decisao 2 da Fase 5.1b). ``df``, ``N`` e
        # ``avgdl`` sobre o corpus ELEGIVEL da politica versionada - nunca
        # sobre o conjunto filtrado por uma consulta ocasional.
        #
        # A definicao e DERIVADA da tabela de elegibilidade (papeis com ao
        # menos um proposito aberto): determinista, versionada, observavel na
        # resposta e participante do ``query_fingerprint``.
        #
        # Era o aparato de navegacao que inflava o idf - no baseline,
        # df(estequiometria) = 74 incluia ocorrencias de sumario.
        corpus_roles = statistical_corpus_roles()
        corpus_size = (
            await self.session.scalar(
                select(func.count())
                .select_from(KnowledgeChunkLexicalIndex)
                .join(
                    KnowledgeChunk,
                    KnowledgeChunk.id == KnowledgeChunkLexicalIndex.chunk_id,
                )
                .where(KnowledgeChunk.editorial_role.in_(corpus_roles))
            )
        ) or 0

        async def empty(reasons: Iterable[str], **extra: Any) -> LexicalSearchResult:
            return await self._result(
                query=query,
                stream=stream,
                dropped=dropped,
                unmatched=extra.get("unmatched", ()),
                ranked=[],
                limit=limit,
                offset=offset,
                generation=generation,
                corpus_size=corpus_size,
                avgdl=extra.get("avgdl", 0.0),
                purpose=purpose,
                phrase_mode=phrase_mode,
                applied=applied,
                filtered_out=extra.get("filtered_out", {}),
                empty_reasons=tuple(reasons),
                started=started,
                candidate_cap_reached=False,
            )

        if not query.strip():
            return await empty(("EMPTY_QUERY",))
        if not stream.terms:
            return await empty(("ALL_TERMS_BELOW_MIN_LENGTH",))
        if generation is None:
            return await empty(("EMPTY_INDEX",))
        if not corpus_size:
            # Ha indice, mas nenhum chunk no corpus ESTATISTICO elegivel -
            # um acervo so de sumario, por exemplo. Dizer "indice vazio"
            # seria mentir sobre o estado do corpus.
            indexados = (
                await self.session.scalar(
                    select(func.count()).select_from(KnowledgeChunkLexicalIndex)
                )
            ) or 0
            return await empty(
                ("EMPTY_ELIGIBLE_CORPUS",) if indexados else ("EMPTY_INDEX",),
                filtered_out={"EDITORIAL_ROLE": indexados},
            )

        terms = tuple(dict.fromkeys(stream.terms))
        document_frequency = dict(
            (
                await self.session.execute(
                    select(KnowledgeChunkTerm.term, func.count())
                    .join(
                        KnowledgeChunk,
                        KnowledgeChunk.id == KnowledgeChunkTerm.chunk_id,
                    )
                    .where(
                        KnowledgeChunkTerm.term.in_(terms),
                        KnowledgeChunk.editorial_role.in_(corpus_roles),
                    )
                    .group_by(KnowledgeChunkTerm.term)
                )
            ).all()
        )
        total_tokens = (
            await self.session.scalar(
                select(func.coalesce(func.sum(KnowledgeChunkLexicalIndex.token_count), 0))
                .select_from(KnowledgeChunkLexicalIndex)
                .join(
                    KnowledgeChunk,
                    KnowledgeChunk.id == KnowledgeChunkLexicalIndex.chunk_id,
                )
                .where(KnowledgeChunk.editorial_role.in_(corpus_roles))
            )
        ) or 0
        avgdl = float(total_tokens) / corpus_size if corpus_size else 0.0

        unmatched = tuple(term for term in terms if not document_frequency.get(term))
        matched = tuple(term for term in terms if document_frequency.get(term))
        if not matched:
            return await empty(("NO_LEXICAL_MATCH",), unmatched=unmatched, avgdl=avgdl)

        candidates = await self._candidates(matched)
        kept, filtered_out = await self._apply_filters(
            candidates,
            purpose=purpose,
            source_kinds=source_kinds,
            chunk_types=chunk_types,
            content_node_id=content_node_id,
            include_descendant_nodes=include_descendant_nodes,
            rights_classes=rights_classes,
            exclude_commercial=exclude_commercial,
        )

        scored = [
            self._score(
                candidate,
                stream=stream,
                matched=matched,
                document_frequency=document_frequency,
                corpus_size=corpus_size,
                avgdl=avgdl,
                heading_weight=heading_weight,
            )
            for candidate in kept
        ]

        if phrase_mode == "REQUIRED":
            before = len(scored)
            scored = [
                item for item in scored if item[1]["phrase_hits"]
            ]
            removed = before - len(scored)
            if removed:
                filtered_out["PHRASE_REQUIRED"] = removed

        scored.sort(
            key=lambda item: (
                -item[0],
                str(item[2].source_id),
                str(item[2].document_id),
                item[2].ordinal,
            )
        )
        candidate_cap_reached = len(scored) > POLICY.candidate_cap
        scored = scored[: POLICY.candidate_cap]

        if max_per_source:
            scored = self._cap_per_source(scored, max_per_source)

        if not scored:
            return await empty(
                self._filter_reasons(filtered_out) or ("NO_LEXICAL_MATCH",),
                unmatched=unmatched,
                avgdl=avgdl,
                filtered_out=filtered_out,
            )

        return await self._result(
            query=query,
            stream=stream,
            dropped=dropped,
            unmatched=unmatched,
            ranked=scored,
            limit=limit,
            offset=offset,
            generation=generation,
            corpus_size=corpus_size,
            avgdl=avgdl,
            purpose=purpose,
            phrase_mode=phrase_mode,
            applied=applied,
            filtered_out=filtered_out,
            empty_reasons=(),
            started=started,
            candidate_cap_reached=candidate_cap_reached,
        )

    async def explain_query(self, query: str) -> QueryExplanation:
        """Tokeniza e estatistica, sem recuperar nada."""
        stream = tokenize(query)
        terms = tuple(dict.fromkeys(stream.terms))

        corpus_size = (
            await self.session.scalar(
                select(func.count()).select_from(KnowledgeChunkLexicalIndex)
            )
        ) or 0
        total_tokens = (
            await self.session.scalar(
                select(func.coalesce(func.sum(KnowledgeChunkLexicalIndex.token_count), 0))
            )
        ) or 0
        frequencies: dict[str, int] = {}
        if terms:
            frequencies = dict(
                (
                    await self.session.execute(
                        select(KnowledgeChunkTerm.term, func.count())
                        .where(KnowledgeChunkTerm.term.in_(terms))
                        .group_by(KnowledgeChunkTerm.term)
                    )
                ).all()
            )
        generation = await self.session.scalar(
            select(KnowledgeLexicalIndexState.generation).where(
                KnowledgeLexicalIndexState.scope == GLOBAL_SCOPE
            )
        )

        return QueryExplanation(
            query=query,
            normalizer_version=POLICY.normalizer_version,
            terms=tuple(
                {
                    "term": term,
                    "positions_in_query": list(stream.positions[term]),
                    "df": frequencies.get(term, 0),
                    "idf": (
                        math.log(
                            1
                            + (corpus_size - frequencies[term] + 0.5)
                            / (frequencies[term] + 0.5)
                        )
                        if frequencies.get(term)
                        else None
                    ),
                    "matched": bool(frequencies.get(term)),
                }
                for term in terms
            ),
            dropped_tokens=tuple(
                {"surface": surface, "reason": reason}
                for surface, reason in stream.dropped
            ),
            stream_length=stream.stream_length,
            corpus_size=corpus_size,
            avgdl=(float(total_tokens) / corpus_size) if corpus_size else 0.0,
            index_generation=generation,
        )

    # -- validacao -------------------------------------------------------

    @staticmethod
    def _validated_purpose(purpose: str | None) -> str:
        if purpose is None:
            return UNKNOWN_PURPOSE
        if purpose not in RETRIEVAL_PURPOSES:
            raise LexicalSearchError(
                "INVALID_RETRIEVAL_PURPOSE",
                f"retrieval_purpose {purpose!r} desconhecido; "
                f"esperado um de {list(RETRIEVAL_PURPOSES)}. Ausencia e tratada "
                f"como {UNKNOWN_PURPOSE}, que FECHA SOLUTION",
            )
        return purpose

    @staticmethod
    def _validate(*, phrase_mode: str, limit: int, offset: int) -> None:
        if phrase_mode not in PHRASE_MODES:
            raise LexicalSearchError(
                "INVALID_PHRASE_MODE",
                f"phrase_mode {phrase_mode!r} desconhecido; esperado {list(PHRASE_MODES)}",
            )
        if limit < 1 or limit > POLICY.max_limit:
            raise LexicalSearchError(
                "LIMIT_TOO_LARGE",
                f"limit {limit} fora de 1..{POLICY.max_limit}",
            )
        if offset < 0 or offset > POLICY.candidate_cap:
            raise LexicalSearchError(
                "OFFSET_OUT_OF_RANGE",
                f"offset {offset} fora de 0..{POLICY.candidate_cap}",
            )

    # -- candidatura -----------------------------------------------------

    async def _candidates(self, terms: Sequence[str]) -> list[_Candidate]:
        """Postings dos termos da consulta, com o minimo de colunas.

        NAO carrega ``raw_text``. Medido na avaliacao real: a consulta
        "concentracao das solucoes" tem 1.493 candidatos de ~1,9 mil
        caracteres cada - carregar a entidade ORM inteira para devolver 10
        resultados custava ~2,8 MB de texto e levava a p95 a 138 ms.

        E ha a razao que pesa mais que desempenho: o literal de fonte
        COMMERCIAL_REFERENCE e conteudo interno restrito. Traze-lo para a
        memoria de 1.493 candidatos quando so 10 serao devolvidos - e nenhum
        deles citavel - e manusear material restrito sem necessidade. O
        excerpt e buscado DEPOIS, so para a pagina devolvida e so para fonte
        que admite citacao (ver ``_excerpts``).
        """
        rows = (
            await self.session.execute(
                select(
                    KnowledgeChunkTerm.term,
                    KnowledgeChunkTerm.term_frequency,
                    KnowledgeChunkTerm.heading_frequency,
                    KnowledgeChunkTerm.positions,
                    KnowledgeChunk.id,
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
                    KnowledgeChunk.source_id,
                    KnowledgeChunkLexicalIndex.token_count,
                    KnowledgeChunkLexicalIndex.heading_token_count,
                    KnowledgeDocument.filename,
                    KnowledgeSource.title,
                    KnowledgeSource.source_kind,
                    KnowledgeSource.rights_class,
                    KnowledgeSource.authority_level,
                )
                .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeChunkTerm.chunk_id)
                .join(
                    KnowledgeChunkLexicalIndex,
                    KnowledgeChunkLexicalIndex.chunk_id == KnowledgeChunk.id,
                )
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .join(KnowledgeSource, KnowledgeSource.id == KnowledgeChunk.source_id)
                .where(KnowledgeChunkTerm.term.in_(terms))
            )
        ).all()

        found: dict[UUID, _Candidate] = {}
        for row in rows:
            candidate = found.get(row.id)
            if candidate is None:
                candidate = _Candidate(
                    chunk_id=row.id,
                    ordinal=row.ordinal,
                    chunk_type=row.chunk_type,
                    heading_path=tuple(row.heading_path or ()),
                    page_start=row.page_start,
                    page_end=row.page_end,
                    text_hash=row.text_hash,
                    char_count=row.char_count,
                    editorial_role=row.editorial_role,
                    editorial_detector_version=row.editorial_detector_version,
                    content_node_id=row.content_node_id,
                    bncc_node_codes=tuple(row.bncc_node_codes or ()),
                    document_id=row.document_id,
                    document_filename=row.filename,
                    source_id=row.source_id,
                    source_title=row.title,
                    source_kind=row.source_kind,
                    rights_class=row.rights_class,
                    authority_level=row.authority_level,
                    token_count=row.token_count,
                    heading_token_count=row.heading_token_count,
                    body={},
                    heading={},
                    positions={},
                )
                found[row.id] = candidate
            candidate.body[row.term] = row.term_frequency
            candidate.heading[row.term] = row.heading_frequency
            candidate.positions[row.term] = tuple(row.positions or ())
        return list(found.values())

    async def _excerpts(
        self, candidates: Sequence[_Candidate]
    ) -> dict[UUID, str]:
        """Excerpt SO da pagina devolvida, e SO de fonte citavel.

        O literal de fonte comercial nunca e lido aqui: ela nao esta na lista.
        """
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
        return {identifier: text or "" for identifier, text in rows}

    # -- filtros ---------------------------------------------------------

    async def _apply_filters(
        self,
        candidates: Sequence[_Candidate],
        *,
        purpose: str,
        source_kinds: Sequence[str] | None,
        chunk_types: Sequence[str] | None,
        content_node_id: UUID | None,
        include_descendant_nodes: bool,
        rights_classes: Sequence[str] | None,
        exclude_commercial: bool,
    ) -> tuple[list[_Candidate], dict[str, int]]:
        allowed_nodes: set[UUID] | None = None
        if content_node_id is not None:
            allowed_nodes = await self._node_subtree(
                content_node_id, include_descendants=include_descendant_nodes
            )

        counters: dict[str, int] = {}
        kept: list[_Candidate] = []
        for candidate in candidates:
            reason = self._rejection(
                candidate,
                purpose=purpose,
                source_kinds=source_kinds,
                chunk_types=chunk_types,
                allowed_nodes=allowed_nodes,
                rights_classes=rights_classes,
                exclude_commercial=exclude_commercial,
            )
            if reason is None:
                kept.append(candidate)
            else:
                counters[reason] = counters.get(reason, 0) + 1
        return kept, counters

    @staticmethod
    def _rejection(
        candidate: _Candidate,
        *,
        purpose: str,
        source_kinds: Sequence[str] | None,
        chunk_types: Sequence[str] | None,
        allowed_nodes: set[UUID] | None,
        rights_classes: Sequence[str] | None,
        exclude_commercial: bool,
    ) -> str | None:
        # O portao de SOLUTION vem primeiro: e politica, nao preferencia de
        # consulta. Em PRACTICE e ASSESS entregar gabarito destroi o proposito,
        # e proposito ausente ou desconhecido FECHA.
        if candidate.chunk_type == _SOLUTION and not solution_is_visible(purpose):
            return "PURPOSE_SOLUTION"
        # ELEGIBILIDADE EDITORIAL. Duas dimensoes ORTOGONAIS que convergem, e
        # basta UMA fechar para fechar: ``chunk_type == SOLUTION`` e forma
        # pedagogica local, ``editorial_role == ANSWER_KEY`` e funcao
        # editorial na obra.
        #
        # Falha ABERTA, ao contrario do portao de SOLUTION: papel que a
        # politica nao conhece e elegivel, porque esconder conteudo e o erro
        # invisivel - ninguem percebe um resultado que nao veio.
        if not editorial_role_is_eligible(candidate.editorial_role, purpose):
            return "EDITORIAL_ROLE"
        if source_kinds and candidate.source_kind not in source_kinds:
            return "SOURCE_KIND"
        if chunk_types and candidate.chunk_type not in chunk_types:
            return "CHUNK_TYPE"
        if allowed_nodes is not None and candidate.content_node_id not in allowed_nodes:
            return "CONTENT_NODE"
        if rights_classes and candidate.rights_class not in rights_classes:
            return "RIGHTS"
        if exclude_commercial and candidate.rights_class == "COMMERCIAL_REFERENCE":
            return "RIGHTS"
        return None

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
            "SOURCE_KIND": "FILTERED_OUT_BY_SOURCE_KIND",
            "CHUNK_TYPE": "FILTERED_OUT_BY_CHUNK_TYPE",
            "CONTENT_NODE": "FILTERED_OUT_BY_CONTENT_NODE",
            "RIGHTS": "FILTERED_OUT_BY_RIGHTS",
            "EDITORIAL_ROLE": "FILTERED_OUT_BY_EDITORIAL_ROLE",
            "PHRASE_REQUIRED": "FILTERED_OUT_BY_PHRASE",
        }
        return tuple(
            mapping[key] for key in counters if counters[key] and key in mapping
        )

    # -- score -----------------------------------------------------------

    def _score(
        self,
        candidate: _Candidate,
        *,
        stream: TokenStream,
        matched: Sequence[str],
        document_frequency: Mapping[str, int],
        corpus_size: int,
        avgdl: float,
        heading_weight: float,
    ) -> tuple[float, dict[str, Any], _Candidate]:
        document_length = candidate.token_count + candidate.heading_token_count
        normalizer = POLICY.k1 * (
            1 - POLICY.b + POLICY.b * (document_length / avgdl if avgdl else 1.0)
        )

        explained: list[dict[str, Any]] = []
        bm25 = 0.0
        for term in matched:
            if term not in candidate.body and term not in candidate.heading:
                continue
            frequency = document_frequency[term]
            idf = math.log(
                1 + (corpus_size - frequency + 0.5) / (frequency + 0.5)
            )
            weighted = candidate.body.get(term, 0) + heading_weight * candidate.heading.get(
                term, 0
            )
            contribution = (
                idf * (weighted * (POLICY.k1 + 1)) / (weighted + normalizer)
                if weighted
                else 0.0
            )
            bm25 += contribution
            explained.append(
                {
                    "term": term,
                    "df": frequency,
                    "idf": idf,
                    "tf_body": candidate.body.get(term, 0),
                    "tf_heading": candidate.heading.get(term, 0),
                    "wtf": weighted,
                    "contribution": contribution,
                }
            )

        document = TokenStream(
            terms=tuple(candidate.positions),
            positions=candidate.positions,
            frequencies={term: len(found) for term, found in candidate.positions.items()},
            token_count=candidate.token_count,
            stream_length=0,
            dropped=(),
        )
        phrase_hits = find_phrase_occurrences(document, stream, slack=POLICY.phrase_slack)
        phrase_idf = sum(item["idf"] for item in explained)
        phrase_bonus = (
            POLICY.phrase_bonus_weight * phrase_idf
            if phrase_hits and len(stream.terms) > 1
            else 0.0
        )

        proximity_bonus = 0.0
        span = None
        if not phrase_hits and len(set(stream.terms)) > 1:
            span = best_proximity_span(document, tuple(dict.fromkeys(stream.terms)))
            if span is not None and span <= POLICY.proximity_window:
                proximity_bonus = POLICY.proximity_bonus_weight * phrase_idf

        explanation = {
            "normalizer_version": POLICY.normalizer_version,
            "policy_version": POLICY.version,
            "lexical_backend": POLICY.lexical_backend,
            "query_terms": explained,
            "doc_length": document_length,
            "token_count": candidate.token_count,
            "heading_token_count": candidate.heading_token_count,
            "avgdl": avgdl,
            "corpus_size": corpus_size,
            "k1": POLICY.k1,
            "b": POLICY.b,
            "heading_weight": heading_weight,
            "phrase_slack": POLICY.phrase_slack,
            "phrase_hits": list(phrase_hits),
            "phrase_bonus": phrase_bonus,
            "proximity_span": span,
            "proximity_window": POLICY.proximity_window,
            "proximity_bonus": proximity_bonus,
            "bm25": bm25,
            "final_score": bm25 + phrase_bonus + proximity_bonus,
        }
        return bm25 + phrase_bonus + proximity_bonus, explanation, candidate

    @staticmethod
    def _cap_per_source(
        scored: Sequence[tuple[float, dict[str, Any], _Candidate]], cap: int
    ) -> list[tuple[float, dict[str, Any], _Candidate]]:
        seen: dict[UUID, int] = {}
        kept = []
        for item in scored:
            source = item[2].source_id
            if seen.get(source, 0) >= cap:
                continue
            seen[source] = seen.get(source, 0) + 1
            kept.append(item)
        return kept

    # -- montagem --------------------------------------------------------

    async def _result(
        self,
        *,
        query: str,
        stream: TokenStream,
        dropped: tuple[dict[str, str], ...],
        unmatched: tuple[str, ...],
        ranked: Sequence[tuple[float, dict[str, Any], _Candidate]],
        limit: int,
        offset: int,
        generation: int | None,
        corpus_size: int,
        avgdl: float,
        purpose: str,
        phrase_mode: str,
        applied: dict[str, Any],
        filtered_out: dict[str, int],
        empty_reasons: tuple[str, ...],
        started: datetime,
        candidate_cap_reached: bool,
    ) -> LexicalSearchResult:
        page = ranked[offset : offset + limit]
        # O excerpt e buscado AQUI, so para a pagina devolvida e so para
        # fonte citavel - nunca para os milhares de candidatos descartados,
        # e nunca para fonte comercial.
        excerpts = await self._excerpts([item[2] for item in page])
        hits = tuple(
            self._hit(item, rank=offset + index + 1, excerpts=excerpts)
            for index, item in enumerate(page)
        )
        distribution: dict[str, int] = {}
        types: dict[str, int] = {}
        for hit in hits:
            key = str(hit.source_id)
            distribution[key] = distribution.get(key, 0) + 1
            types[hit.chunk_type] = types.get(hit.chunk_type, 0) + 1

        return LexicalSearchResult(
            query=query,
            normalized_terms=tuple(dict.fromkeys(stream.terms)),
            dropped_tokens=dropped,
            unmatched_terms=unmatched,
            hits=hits,
            total_candidates=len(ranked),
            returned=len(hits),
            limit=limit,
            offset=offset,
            has_more=offset + len(hits) < len(ranked),
            candidate_cap_reached=candidate_cap_reached,
            query_fingerprint=_fingerprint(
                terms=tuple(dict.fromkeys(stream.terms)),
                applied=applied,
                generation=generation,
            ),
            index_generation=generation,
            corpus_size=corpus_size,
            avgdl=avgdl,
            statistical_corpus_roles=statistical_corpus_roles(),
            retrieval_mode=POLICY.retrieval_mode,
            lexical_backend=POLICY.lexical_backend,
            retrieval_purpose=purpose,
            phrase_mode=phrase_mode,
            policy=POLICY.snapshot(),
            empty_reasons=empty_reasons,
            filtered_out=dict(filtered_out),
            source_distribution=distribution,
            distinct_sources=len(distribution),
            coverage_warning=len(distribution) < POLICY.min_distinct_sources,
            chunk_type_distribution=types,
            duration_seconds=round((datetime.now() - started).total_seconds(), 4),
            solution_visible=solution_is_visible(purpose),
            applied_filters=applied,
        )

    @staticmethod
    def _hit(
        item: tuple[float, dict[str, Any], _Candidate],
        *,
        rank: int,
        excerpts: Mapping[UUID, str],
    ) -> LexicalHit:
        score, explanation, candidate = item
        quotable = may_expose_literal_text(candidate.rights_class)
        ceiling = max_excerpt_chars(candidate.rights_class)
        return LexicalHit(
            chunk_id=candidate.chunk_id,
            rank=rank,
            score=score,
            chunk_type=candidate.chunk_type,
            ordinal=candidate.ordinal,
            heading_path=tuple(
                level[: POLICY.heading_level_max_chars]
                for level in candidate.heading_path
            ),
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
            excerpt=(
                excerpts.get(candidate.chunk_id, "")[:ceiling] if ceiling else None
            ),
            explanation=explanation,
        )


def _fingerprint(
    *, terms: Sequence[str], applied: Mapping[str, Any], generation: int | None
) -> str:
    """Identidade da FOTO: consulta normalizada + filtros + politica + normalizador
    + geracao do indice.

    A geracao e o ajuste 3: duas paginas com fingerprints diferentes nao
    pertencem a mesma foto do corpus. Nao e snapshot pagination - e tornar a
    incoerencia detectavel em vez de silenciosa.
    """
    payload = {
        "terms": list(terms),
        "filters": {key: applied[key] for key in sorted(applied)},
        "policy_version": POLICY.version,
        "normalizer_version": POLICY.normalizer_version,
        "lexical_backend": POLICY.lexical_backend,
        "index_generation": generation,
        # A definicao do corpus estatistico participa da identidade da foto:
        # mudar os papeis elegiveis muda o idf de TODO termo, e isso nao pode
        # passar em silencio entre duas medicoes.
        "editorial_detector_version": POLICY.editorial_detector_version,
        "statistical_corpus_roles": list(statistical_corpus_roles()),
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
