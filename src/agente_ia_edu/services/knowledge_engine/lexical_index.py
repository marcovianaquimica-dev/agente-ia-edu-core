"""Escrita e estado do indice lexical.

O indice e escrito na MESMA TRANSACAO que persiste os chunks (ver
``documents.py``). Nao existe janela em que um chunk esteja no corpus e fora
do indice - um corpus parcialmente indexado produziria busca que parece
funcionar e esconde material.

Custo medido no livro real de 548 paginas: 0,3 s de tokenizacao sobre 22,4 s
de extracao, ou 1,3%.

GERACAO (ajuste 3 da Fase 5)
============================

Toda escrita incrementa ``knowledge_lexical_index_state.generation``, na mesma
transacao. A geracao entra no ``query_fingerprint`` da busca: se os postings
mudarem entre a pagina 1 e a pagina 2 da mesma consulta, os fingerprints
divergem e isso APARECE, em vez de produzir uma paginacao silenciosamente
incoerente. Nao implementa snapshot pagination - torna a incoerencia
detectavel, que e o que foi pedido.

MissingGreenlet: todo metodo publico devolve dataclass CONGELADA, montada
antes do commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeChunkLexicalIndex,
    KnowledgeChunkTerm,
    KnowledgeLexicalIndexState,
)
from ...knowledge_retrieval_policy.v1 import POLICY
from .lexical_tokenizer import tokenize, tokenize_heading

#: Acervo global no piloto. A coluna existe para que um acervo por escola
#: entre depois como LINHA nova, nao como migracao de chave primaria.
GLOBAL_SCOPE = "GLOBAL"

INDEX_DOCUMENT = "INDEX_DOCUMENT"
REINDEX_DOCUMENT = "REINDEX_DOCUMENT"
PURGE_DOCUMENT = "PURGE_DOCUMENT"


def _context_text(chunk: KnowledgeChunk) -> str:
    """O campo de CONTEXTO: ``heading_path`` mais os codigos BNCC validados.

    Por que o codigo entra aqui e nao no corpo
    ==========================================

    ``CurriculumFrameworkChunker`` grava ``raw_text = skill.statement``, e o
    enunciado da habilidade NAO contem o proprio codigo. Sem isto, buscar
    ``EM13CNT301`` - que o chunker chama, com razao, de "a unica chave util" -
    nao devolveria nada, e a norma ficaria inbuscavel pelo seu identificador.

    Acrescentar o codigo ao ``raw_text`` esta fora de questao: mudaria o texto
    da fonte e o ``text_hash`` da Fase 4. O campo de contexto e o lugar
    correto, porque e exatamente o que ele significa - termo que o SISTEMA
    acrescentou, nao texto da obra. Mesma natureza do ``heading_path``.
    """
    parts = list(chunk.heading_path or ())
    parts.extend(chunk.bncc_node_codes or ())
    return " ".join(parts)


class LexicalIndexError(ValueError):
    """Pedido de indexacao que nao pode ser atendido. ``code`` e estavel.

    Vive aqui, e nao na rota, porque descobrir que o documento nao existe
    exige ler ``KnowledgeDocument`` - e a fronteira do subsistema proibe
    modulo de fora tocar o modelo do corpus.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LexicalIndexSnapshot:
    chunks_indexed: int
    postings_written: int
    generation: int
    normalizer_version: str
    operation: str
    duration_seconds: float


@dataclass(frozen=True)
class LexicalIndexStatus:
    """Cobertura e obsolescencia do indice.

    ``stale`` compara o ``text_hash`` gravado no indice com o do chunk. Isso
    torna "este chunk foi indexado com o texto e as regras atuais?" uma
    pergunta de SQL. ``avgdl`` e ``total_indexed_tokens`` saem de agregacao
    viva - estatistica global nao e materializada.
    """

    scope: str
    generation: int | None
    normalizer_version: str | None
    policy_version: str | None
    total_chunks: int
    indexed: int
    missing: int
    stale: int
    normalizer_versions_present: tuple[str, ...]
    postings: int
    total_indexed_tokens: int
    avgdl: float
    by_source: tuple[dict[str, object], ...] = ()


class LexicalIndexService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- escrita ---------------------------------------------------------

    async def index_chunks(
        self,
        chunks: Sequence[KnowledgeChunk],
        *,
        operation: str = INDEX_DOCUMENT,
    ) -> LexicalIndexSnapshot:
        """Indexa os chunks dados. Substitui postings anteriores de cada um.

        Os chunks precisam ja ter ``id`` - ou seja, ter passado por ``flush``.
        """
        started = datetime.now()
        chunk_ids = [chunk.id for chunk in chunks]
        if chunk_ids:
            await self._purge_chunks(chunk_ids)

        generation = await self._bump(operation)
        postings = 0

        for chunk in chunks:
            body = tokenize(chunk.raw_text or "")
            heading = tokenize_heading(_context_text(chunk))

            for term in sorted(set(body.terms) | set(heading.terms)):
                positions = sorted(
                    set(body.positions.get(term, ()))
                    | set(heading.positions.get(term, ()))
                )
                self.session.add(
                    KnowledgeChunkTerm(
                        chunk_id=chunk.id,
                        term=term,
                        term_frequency=body.frequencies.get(term, 0),
                        heading_frequency=heading.frequencies.get(term, 0),
                        positions=positions,
                    )
                )
                postings += 1

            self.session.add(
                KnowledgeChunkLexicalIndex(
                    chunk_id=chunk.id,
                    token_count=body.token_count,
                    heading_token_count=heading.token_count,
                    text_hash=chunk.text_hash,
                    normalizer_version=POLICY.normalizer_version,
                    generation=generation,
                    indexed_at=datetime.now(timezone.utc),
                )
            )

        await self.session.flush()
        return LexicalIndexSnapshot(
            chunks_indexed=len(chunks),
            postings_written=postings,
            generation=generation,
            normalizer_version=POLICY.normalizer_version,
            operation=operation,
            duration_seconds=round((datetime.now() - started).total_seconds(), 3),
        )

    async def reindex_document(self, document_id: UUID) -> LexicalIndexSnapshot:
        if await self.session.get(KnowledgeDocument, document_id) is None:
            raise LexicalIndexError(
                "DOCUMENT_NOT_FOUND", f"documento {document_id} nao encontrado"
            )
        chunks = (
            await self.session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == document_id)
                .order_by(KnowledgeChunk.ordinal)
            )
        ).all()
        return await self.index_chunks(chunks, operation=REINDEX_DOCUMENT)

    async def purge_document(self, document_id: UUID) -> int:
        """Remove os postings de um documento, e move a geracao.

        Precisa existir separado de ``reindex``: as FKs do subsistema sao
        ``RESTRICT``, logo apagar chunks (re-chunking com ``force``) exige
        apagar os postings ANTES.
        """
        chunk_ids = list(
            (
                await self.session.scalars(
                    select(KnowledgeChunk.id).where(
                        KnowledgeChunk.document_id == document_id
                    )
                )
            ).all()
        )
        removed = await self._purge_chunks(chunk_ids) if chunk_ids else 0
        await self._bump(PURGE_DOCUMENT)
        await self.session.flush()
        return removed

    async def _purge_chunks(self, chunk_ids: Sequence[UUID]) -> int:
        result = await self.session.execute(
            delete(KnowledgeChunkTerm).where(KnowledgeChunkTerm.chunk_id.in_(chunk_ids))
        )
        await self.session.execute(
            delete(KnowledgeChunkLexicalIndex).where(
                KnowledgeChunkLexicalIndex.chunk_id.in_(chunk_ids)
            )
        )
        return result.rowcount or 0

    async def _bump(self, operation: str) -> int:
        state = await self.session.get(KnowledgeLexicalIndexState, GLOBAL_SCOPE)
        if state is None:
            state = KnowledgeLexicalIndexState(
                scope=GLOBAL_SCOPE,
                generation=1,
                normalizer_version=POLICY.normalizer_version,
                policy_version=POLICY.version,
                last_operation=operation,
            )
            self.session.add(state)
            await self.session.flush()
            return 1

        state.generation += 1
        state.normalizer_version = POLICY.normalizer_version
        state.policy_version = POLICY.version
        state.last_operation = operation
        await self.session.flush()
        return state.generation

    # -- leitura ---------------------------------------------------------

    async def current_generation(self) -> int | None:
        return await self.session.scalar(
            select(KnowledgeLexicalIndexState.generation).where(
                KnowledgeLexicalIndexState.scope == GLOBAL_SCOPE
            )
        )

    async def status(self, *, source_id: UUID | None = None) -> LexicalIndexStatus:
        state = await self.session.get(KnowledgeLexicalIndexState, GLOBAL_SCOPE)

        chunk_filter = []
        if source_id is not None:
            chunk_filter.append(KnowledgeChunk.source_id == source_id)

        total_chunks = await self.session.scalar(
            select(func.count()).select_from(KnowledgeChunk).where(*chunk_filter)
        )
        indexed = await self.session.scalar(
            select(func.count())
            .select_from(KnowledgeChunkLexicalIndex)
            .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeChunkLexicalIndex.chunk_id)
            .where(*chunk_filter)
        )
        stale = await self.session.scalar(
            select(func.count())
            .select_from(KnowledgeChunkLexicalIndex)
            .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeChunkLexicalIndex.chunk_id)
            .where(
                *chunk_filter,
                (KnowledgeChunkLexicalIndex.text_hash != KnowledgeChunk.text_hash)
                | (
                    KnowledgeChunkLexicalIndex.normalizer_version
                    != POLICY.normalizer_version
                ),
            )
        )
        postings = await self.session.scalar(
            select(func.count())
            .select_from(KnowledgeChunkTerm)
            .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeChunkTerm.chunk_id)
            .where(*chunk_filter)
        )
        tokens = await self.session.scalar(
            select(func.coalesce(func.sum(KnowledgeChunkLexicalIndex.token_count), 0))
            .select_from(KnowledgeChunkLexicalIndex)
            .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeChunkLexicalIndex.chunk_id)
            .where(*chunk_filter)
        )
        versions = (
            await self.session.scalars(
                select(KnowledgeChunkLexicalIndex.normalizer_version)
                .join(
                    KnowledgeChunk,
                    KnowledgeChunk.id == KnowledgeChunkLexicalIndex.chunk_id,
                )
                .where(*chunk_filter)
                .group_by(KnowledgeChunkLexicalIndex.normalizer_version)
            )
        ).all()

        by_source = (
            await self.session.execute(
                select(
                    KnowledgeChunk.source_id,
                    func.count(KnowledgeChunk.id),
                    func.count(KnowledgeChunkLexicalIndex.chunk_id),
                )
                .outerjoin(
                    KnowledgeChunkLexicalIndex,
                    KnowledgeChunkLexicalIndex.chunk_id == KnowledgeChunk.id,
                )
                .where(*chunk_filter)
                .group_by(KnowledgeChunk.source_id)
            )
        ).all()

        return LexicalIndexStatus(
            scope=GLOBAL_SCOPE,
            generation=state.generation if state else None,
            normalizer_version=state.normalizer_version if state else None,
            policy_version=state.policy_version if state else None,
            total_chunks=total_chunks or 0,
            indexed=indexed or 0,
            missing=(total_chunks or 0) - (indexed or 0),
            stale=stale or 0,
            normalizer_versions_present=tuple(sorted(versions)),
            postings=postings or 0,
            total_indexed_tokens=int(tokens or 0),
            avgdl=(float(tokens or 0) / indexed) if indexed else 0.0,
            by_source=tuple(
                {
                    "source_id": str(source),
                    "chunks": chunks,
                    "indexed": done,
                    "missing": chunks - done,
                }
                for source, chunks, done in by_source
            ),
        )
