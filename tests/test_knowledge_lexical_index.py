"""CEREBRO - Fase 5: escrita do indice lexical, obsolescencia e geracao.

Tres propriedades que este arquivo fixa:

1. **Corpo e titulo sao campos separados.** ``term_frequency`` conta a obra,
   ``heading_frequency`` conta o contexto que o sistema acrescentou.
2. **Indice obsoleto e detectavel.** ``text_hash`` gravado no indice divergir
   do ``text_hash`` do chunk significa reindexar - e isso se responde por SQL.
3. **A geracao se move a cada escrita** (ajuste 3). E o que torna "os postings
   mudaram entre as paginas desta consulta?" uma pergunta respondivel.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    KnowledgeChunk,
    KnowledgeChunkLexicalIndex,
    KnowledgeChunkTerm,
    KnowledgeDocument,
    KnowledgeLexicalIndexState,
    KnowledgeSource,
)
from agente_ia_edu.knowledge_retrieval_policy.v1 import POLICY
from agente_ia_edu.services.knowledge_engine.lexical_index import LexicalIndexService


class _IndexCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=True
        )
        async with self.factory() as session:
            source = KnowledgeSource(
                title="Livro de teste",
                source_kind="TEXTBOOK",
                rights_class="COMMERCIAL_REFERENCE",
                authority_level="COMMERCIAL_TEXTBOOK",
            )
            session.add(source)
            await session.flush()
            document = KnowledgeDocument(
                source_id=source.id,
                filename="livro.pdf",
                storage_uri="/tmp/livro.pdf",
                document_hash="h" * 64,
            )
            session.add(document)
            await session.flush()
            self.source_id, self.document_id = source.id, document.id
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _add_chunk(
        self,
        *,
        ordinal: int,
        raw_text: str,
        heading_path: list[str] | None = None,
        chunk_type: str = "PROSE",
        text_hash: str | None = None,
    ) -> uuid.UUID:
        async with self.factory() as session:
            chunk = KnowledgeChunk(
                source_id=self.source_id,
                document_id=self.document_id,
                ordinal=ordinal,
                chunk_type=chunk_type,
                heading_path=heading_path or [],
                page_start=10,
                page_end=10,
                raw_text=raw_text,
                text_hash=text_hash or f"{ordinal:064d}",
                char_count=len(raw_text),
            )
            session.add(chunk)
            await session.flush()
            chunk_id = chunk.id
            await session.commit()
        return chunk_id

    async def _index_all(self, *, operation: str = "INDEX_DOCUMENT"):
        async with self.factory() as session:
            chunks = (
                await session.scalars(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.document_id == self.document_id
                    )
                )
            ).all()
            snapshot = await LexicalIndexService(session).index_chunks(
                chunks, operation=operation
            )
            await session.commit()
        return snapshot

    async def _terms(self, chunk_id: uuid.UUID) -> dict[str, KnowledgeChunkTerm]:
        async with self.factory() as session:
            rows = (
                await session.scalars(
                    select(KnowledgeChunkTerm).where(
                        KnowledgeChunkTerm.chunk_id == chunk_id
                    )
                )
            ).all()
            return {row.term: row for row in rows}


class PostingsTests(_IndexCase):
    async def test_body_and_heading_are_counted_separately(self):
        chunk_id = await self._add_chunk(
            ordinal=1,
            raw_text="A diluicao de uma solucao reduz a concentracao da solucao.",
            heading_path=["Chapter 7 - Solucoes e diluicao"],
        )
        await self._index_all()
        terms = await self._terms(chunk_id)

        self.assertEqual(terms["solucao"].term_frequency, 2)
        self.assertEqual(terms["solucao"].heading_frequency, 1)
        self.assertEqual(terms["concentracao"].term_frequency, 1)
        self.assertEqual(terms["concentracao"].heading_frequency, 0)

    async def test_a_term_only_in_the_heading_is_indexed(self):
        """O CHECK antigo ``term_frequency > 0`` proibia exatamente isto."""
        chunk_id = await self._add_chunk(
            ordinal=1,
            raw_text="Texto sem o termo do titulo.",
            heading_path=["Chapter 3 - Radioatividade"],
        )
        await self._index_all()
        terms = await self._terms(chunk_id)

        self.assertIn("radioatividade", terms)
        self.assertEqual(terms["radioatividade"].term_frequency, 0)
        self.assertEqual(terms["radioatividade"].heading_frequency, 1)

    async def test_heading_boilerplate_never_becomes_a_posting(self):
        chunk_id = await self._add_chunk(
            ordinal=1, raw_text="Massa molar.", heading_path=["Chapter 5"]
        )
        await self._index_all()
        terms = await self._terms(chunk_id)
        self.assertNotIn("chapter", terms)

    async def test_positions_are_stored_and_are_not_compressed(self):
        chunk_id = await self._add_chunk(
            ordinal=1, raw_text="concentracao das solucoes aquosas"
        )
        await self._index_all()
        terms = await self._terms(chunk_id)
        self.assertEqual(terms["concentracao"].positions, [0])
        self.assertEqual(terms["solucao"].positions, [2])

    async def test_no_posting_has_null_positions(self):
        await self._add_chunk(ordinal=1, raw_text="mol e massa molar")
        await self._index_all()
        async with self.factory() as session:
            nulls = await session.scalar(
                select(func.count())
                .select_from(KnowledgeChunkTerm)
                .where(KnowledgeChunkTerm.positions.is_(None))
            )
        self.assertEqual(nulls, 0)

    async def test_an_empty_chunk_yields_no_postings_but_is_recorded_as_indexed(self):
        """Chunk sem termo util existe - e preciso distinguir "indexado e vazio"
        de "nao indexado", ou a cobertura nunca fecha em 100%."""
        chunk_id = await self._add_chunk(ordinal=1, raw_text="a e o de")
        snapshot = await self._index_all()
        self.assertEqual(snapshot.chunks_indexed, 1)
        self.assertEqual(snapshot.postings_written, 0)
        async with self.factory() as session:
            state = await session.get(KnowledgeChunkLexicalIndex, chunk_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.token_count, 0)


class ChunkStateTests(_IndexCase):
    async def test_the_indexed_text_hash_is_recorded(self):
        chunk_id = await self._add_chunk(
            ordinal=1, raw_text="massa molar", text_hash="a" * 64
        )
        await self._index_all()
        async with self.factory() as session:
            state = await session.get(KnowledgeChunkLexicalIndex, chunk_id)
        self.assertEqual(state.text_hash, "a" * 64)
        self.assertEqual(state.normalizer_version, POLICY.normalizer_version)

    async def test_token_count_is_the_bm25_document_length(self):
        chunk_id = await self._add_chunk(
            ordinal=1,
            raw_text="concentracao das solucoes",
            heading_path=["Chapter 2 - Solucoes"],
        )
        await self._index_all()
        async with self.factory() as session:
            state = await session.get(KnowledgeChunkLexicalIndex, chunk_id)
        self.assertEqual(state.token_count, 2)
        self.assertEqual(state.heading_token_count, 1)

    async def test_reindexing_the_same_chunk_replaces_instead_of_duplicating(self):
        await self._add_chunk(ordinal=1, raw_text="massa molar")
        await self._index_all()
        await self._index_all(operation="REINDEX_DOCUMENT")
        async with self.factory() as session:
            postings = await session.scalar(
                select(func.count()).select_from(KnowledgeChunkTerm)
            )
            states = await session.scalar(
                select(func.count()).select_from(KnowledgeChunkLexicalIndex)
            )
        self.assertEqual(postings, 2)
        self.assertEqual(states, 1)


class ContextFieldTests(_IndexCase):
    """O campo de contexto carrega ``heading_path`` MAIS os codigos BNCC.

    ``CurriculumFrameworkChunker`` grava ``raw_text = skill.statement``, e o
    enunciado nao contem o proprio codigo. Sem indexar o codigo, buscar
    ``EM13CNT301`` nao devolveria nada - e o codigo e, nas palavras do proprio
    chunker, "a unica chave util" da habilidade.
    """

    async def test_a_bncc_code_becomes_an_indexed_term(self):
        chunk_id = await self._add_chunk(
            ordinal=1,
            raw_text="Analisar e representar as transformacoes em sistemas.",
            heading_path=["Ciencias da Natureza", "Competencia especifica 1"],
        )
        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, chunk_id)
            chunk.bncc_node_codes = ["EM13CNT101"]
            await session.commit()
        await self._index_all()
        terms = await self._terms(chunk_id)
        self.assertIn("em13cnt101", terms)
        self.assertEqual(terms["em13cnt101"].heading_frequency, 1)
        self.assertEqual(terms["em13cnt101"].term_frequency, 0)

    async def test_the_code_does_not_contaminate_the_body_count(self):
        """O codigo e contexto do SISTEMA, nao texto da obra. Conta-lo como
        corpo mentiria sobre o que a fonte diz."""
        chunk_id = await self._add_chunk(
            ordinal=1, raw_text="Analisar sistemas.", heading_path=["Area"]
        )
        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, chunk_id)
            chunk.bncc_node_codes = ["EM13CNT301"]
            await session.commit()
        await self._index_all()
        async with self.factory() as session:
            state = await session.get(KnowledgeChunkLexicalIndex, chunk_id)
        self.assertEqual(state.token_count, 2)
        self.assertEqual(state.heading_token_count, 2)

    async def test_heading_positions_never_collide_with_body_positions(self):
        chunk_id = await self._add_chunk(
            ordinal=1, raw_text="solucao aquosa", heading_path=["Chapter 1 - Solucoes"]
        )
        await self._index_all()
        terms = await self._terms(chunk_id)
        self.assertEqual(len(terms["solucao"].positions), 2)
        self.assertEqual(min(terms["solucao"].positions), 0)
        self.assertGreater(max(terms["solucao"].positions), 1000)


class StalenessTests(_IndexCase):
    async def test_a_changed_chunk_is_reported_as_stale(self):
        chunk_id = await self._add_chunk(
            ordinal=1, raw_text="massa molar", text_hash="a" * 64
        )
        await self._index_all()
        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, chunk_id)
            chunk.raw_text = "massa molar e volume molar"
            chunk.text_hash = "b" * 64
            await session.commit()

        async with self.factory() as session:
            status = await LexicalIndexService(session).status()
        self.assertEqual(status.stale, 1)
        self.assertEqual(status.indexed, 1)
        self.assertEqual(status.missing, 0)

    async def test_an_unindexed_chunk_is_reported_as_missing(self):
        await self._add_chunk(ordinal=1, raw_text="massa molar")
        await self._add_chunk(ordinal=2, raw_text="volume molar")
        async with self.factory() as session:
            chunks = (
                await session.scalars(
                    select(KnowledgeChunk).where(KnowledgeChunk.ordinal == 1)
                )
            ).all()
            await LexicalIndexService(session).index_chunks(chunks)
            await session.commit()

        async with self.factory() as session:
            status = await LexicalIndexService(session).status()
        self.assertEqual(status.total_chunks, 2)
        self.assertEqual(status.indexed, 1)
        self.assertEqual(status.missing, 1)

    async def test_reindex_document_clears_staleness(self):
        chunk_id = await self._add_chunk(
            ordinal=1, raw_text="massa molar", text_hash="a" * 64
        )
        await self._index_all()
        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, chunk_id)
            chunk.text_hash = "b" * 64
            await session.commit()
        async with self.factory() as session:
            await LexicalIndexService(session).reindex_document(self.document_id)
            await session.commit()
        async with self.factory() as session:
            status = await LexicalIndexService(session).status()
        self.assertEqual(status.stale, 0)

    async def test_avgdl_and_n_come_from_the_live_index(self):
        """``N`` e ``avgdl`` NAO sao materializados: a estatistica global que
        poderia derivar nao foi guardada, justamente para nao dessincronizar."""
        await self._add_chunk(ordinal=1, raw_text="massa molar atomica")
        await self._add_chunk(ordinal=2, raw_text="mol")
        await self._index_all()
        async with self.factory() as session:
            status = await LexicalIndexService(session).status()
        self.assertEqual(status.indexed, 2)
        self.assertAlmostEqual(status.avgdl, 2.0)


class GenerationTests(_IndexCase):
    """Ajuste 3 da Fase 5."""

    async def test_the_first_index_creates_generation_one(self):
        await self._add_chunk(ordinal=1, raw_text="massa molar")
        snapshot = await self._index_all()
        self.assertEqual(snapshot.generation, 1)

    async def test_every_write_moves_the_generation(self):
        await self._add_chunk(ordinal=1, raw_text="massa molar")
        first = await self._index_all()
        second = await self._index_all(operation="REINDEX_DOCUMENT")
        self.assertEqual(second.generation, first.generation + 1)

    async def test_the_state_row_records_the_versions_and_the_operation(self):
        await self._add_chunk(ordinal=1, raw_text="massa molar")
        await self._index_all(operation="REINDEX_DOCUMENT")
        async with self.factory() as session:
            state = await session.get(KnowledgeLexicalIndexState, "GLOBAL")
        self.assertEqual(state.normalizer_version, POLICY.normalizer_version)
        self.assertEqual(state.policy_version, POLICY.version)
        self.assertEqual(state.last_operation, "REINDEX_DOCUMENT")

    async def test_purging_also_moves_the_generation(self):
        """Apagar postings muda o que a busca ve tanto quanto acrescentar."""
        await self._add_chunk(ordinal=1, raw_text="massa molar")
        first = await self._index_all()
        async with self.factory() as session:
            service = LexicalIndexService(session)
            await service.purge_document(self.document_id)
            generation = await service.current_generation()
            await session.commit()
        self.assertEqual(generation, first.generation + 1)

    async def test_purge_removes_postings_and_state(self):
        await self._add_chunk(ordinal=1, raw_text="massa molar")
        await self._index_all()
        async with self.factory() as session:
            await LexicalIndexService(session).purge_document(self.document_id)
            await session.commit()
        async with self.factory() as session:
            postings = await session.scalar(
                select(func.count()).select_from(KnowledgeChunkTerm)
            )
            states = await session.scalar(
                select(func.count()).select_from(KnowledgeChunkLexicalIndex)
            )
        self.assertEqual(postings, 0)
        self.assertEqual(states, 0)

    async def test_the_generation_never_goes_backwards(self):
        await self._add_chunk(ordinal=1, raw_text="massa molar")
        generations = [(await self._index_all()).generation for _ in range(3)]
        self.assertEqual(generations, sorted(generations))
        self.assertEqual(len(set(generations)), 3)


if __name__ == "__main__":
    unittest.main()
