"""CEREBRO - Fase 5: o indice e escrito na MESMA TRANSACAO que os chunks.

Nao existe janela em que um chunk esteja no corpus e fora do indice. Um
corpus parcialmente indexado produziria uma busca que PARECE funcionar e
esconde material - o pior modo de falha possivel aqui, porque nada no
resultado denuncia o que faltou.

Tambem fixa a ordem de remocao: as FKs do subsistema sao ``RESTRICT``, logo
re-chunkar com ``force`` tem de apagar postings ANTES dos chunks.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    KnowledgeChunk,
    KnowledgeChunkLexicalIndex,
    KnowledgeChunkTerm,
)
from agente_ia_edu.services.knowledge_engine.document_ingress import ResolvedDocumentFile
from agente_ia_edu.services.knowledge_engine.documents import KnowledgeDocumentService
from agente_ia_edu.services.knowledge_engine.lexical_index import LexicalIndexService
from agente_ia_edu.services.knowledge_engine.lexical_search import LexicalSearcher
from agente_ia_edu.services.knowledge_engine.sources import KnowledgeSourceService
from agente_ia_edu.services.material_storage import MaterialStorage

_PROSE = "O mol participa da reacao em proporcao definida pela equacao. " * 30
_BNCC = [
    "5.3.1. CIENCIAS DA NATUREZA\nCOMPETENCIA ESPECIFICA 1\n"
    "Analisar fenomenos naturais e processos tecnologicos. Comentario.",
    "HABILIDADES\n(EM13CNT101) Analisar e representar as transformacoes e "
    "conservacoes em sistemas que envolvam quantidade de materia.",
]


def _write_pdf(path: Path, pages: list[str]) -> Path:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((40, 60), text, fontsize=8)
    document.save(str(path))
    document.close()
    return path


class _IngestionCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=True
        )
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.storage = MaterialStorage(root=self.tmp / "storage")

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _register(self, *, source_kind="TEXTBOOK", pages=None, filename="livro.pdf"):
        path = _write_pdf(
            self.tmp / filename,
            pages or ["Capítulo 1 - Estequiometria\n\n" + _PROSE],
        )
        async with self.factory() as session:
            sources = KnowledgeSourceService(session, storage=self.storage)
            source = await sources.register(
                title="Fonte",
                source_kind=source_kind,
                rights_class=(
                    "COMMERCIAL_REFERENCE" if source_kind == "TEXTBOOK" else "OFFICIAL_PUBLIC"
                ),
                authority_level=(
                    "COMMERCIAL_TEXTBOOK" if source_kind == "TEXTBOOK" else "OFFICIAL"
                ),
            )
            registration = await sources.register_document(
                source.id,
                ResolvedDocumentFile(
                    path=path,
                    original_filename=path.name,
                    mime_type="application/pdf",
                    size_bytes=path.stat().st_size,
                    ingress="LOCAL_PATH",
                ),
            )
        return source.id, registration.document.id

    async def _counts(self) -> tuple[int, int, int]:
        async with self.factory() as session:
            chunks = await session.scalar(
                select(func.count()).select_from(KnowledgeChunk)
            )
            indexed = await session.scalar(
                select(func.count()).select_from(KnowledgeChunkLexicalIndex)
            )
            postings = await session.scalar(
                select(func.count()).select_from(KnowledgeChunkTerm)
            )
        return chunks, indexed, postings


class ProseIngestionTests(_IngestionCase):
    async def test_chunking_a_document_indexes_every_chunk(self):
        source_id, document_id = await self._register()
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_and_chunk(
                source_id, document_id
            )
        chunks, indexed, postings = await self._counts()
        self.assertGreater(chunks, 0)
        self.assertEqual(indexed, chunks)
        self.assertGreater(postings, 0)

    async def test_the_coverage_is_complete_right_after_ingestion(self):
        source_id, document_id = await self._register()
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_and_chunk(
                source_id, document_id
            )
        async with self.factory() as session:
            status = await LexicalIndexService(session).status()
        self.assertEqual(status.missing, 0)
        self.assertEqual(status.stale, 0)
        self.assertEqual(status.indexed, status.total_chunks)
        self.assertIsNotNone(status.generation)

    async def test_the_corpus_is_searchable_without_any_extra_step(self):
        source_id, document_id = await self._register()
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_and_chunk(
                source_id, document_id
            )
        async with self.factory() as session:
            result = await LexicalSearcher(session).search("mol")
        self.assertGreater(len(result.hits), 0)
        self.assertEqual(result.hits[0].rights_class, "COMMERCIAL_REFERENCE")
        self.assertIsNone(result.hits[0].excerpt)

    async def test_force_rechunking_purges_the_old_postings_first(self):
        """As FKs sao RESTRICT: sem apagar postings antes, o DELETE dos chunks
        levantaria IntegrityError."""
        source_id, document_id = await self._register()
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_and_chunk(
                source_id, document_id
            )
        before = await self._counts()
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_and_chunk(
                source_id, document_id, force=True
            )
        after = await self._counts()
        self.assertEqual(before[0], after[0])
        self.assertEqual(after[1], after[0])
        async with self.factory() as session:
            status = await LexicalIndexService(session).status()
        self.assertEqual(status.stale, 0)
        self.assertEqual(status.missing, 0)

    async def test_a_failed_extraction_leaves_no_orphan_postings(self):
        source_id, document_id = await self._register(pages=[""])
        async with self.factory() as session:
            snapshot = await KnowledgeDocumentService(session).extract_and_chunk(
                source_id, document_id
            )
        self.assertEqual(snapshot.chunks_created, 0)
        chunks, indexed, postings = await self._counts()
        self.assertEqual((chunks, indexed, postings), (0, 0, 0))


class FrameworkIngestionTests(_IngestionCase):
    async def test_the_bncc_path_also_indexes_its_chunks(self):
        """A BNCC entra por ``extract_framework``, nao pelo chunker de prosa.
        Os dois caminhos tem de indexar - senao a norma fica inbuscavel."""
        source_id, document_id = await self._register(
            source_kind="CURRICULUM_FRAMEWORK", pages=_BNCC, filename="bncc.pdf"
        )
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_framework(
                source_id, document_id, taxonomy_version="EM-2018"
            )
        chunks, indexed, postings = await self._counts()
        self.assertGreater(chunks, 0)
        self.assertEqual(indexed, chunks)
        async with self.factory() as session:
            result = await LexicalSearcher(session).search("EM13CNT101")
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(result.hits[0].chunk_type, "CURRICULUM_ITEM")
        self.assertEqual(result.hits[0].source_kind, "CURRICULUM_FRAMEWORK")

    async def test_an_official_source_may_be_quoted(self):
        source_id, document_id = await self._register(
            source_kind="CURRICULUM_FRAMEWORK", pages=_BNCC, filename="bncc.pdf"
        )
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_framework(
                source_id, document_id, taxonomy_version="EM-2018"
            )
        async with self.factory() as session:
            result = await LexicalSearcher(session).search("quantidade de materia")
        self.assertTrue(result.hits[0].quotable)
        self.assertIsNotNone(result.hits[0].excerpt)


if __name__ == "__main__":
    unittest.main()
