"""CEREBRO / Knowledge Engine - Fase 3: orquestracao de extracao e chunking.

Reentrancia, recusas e atualizacao de estado do documento.
"""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

import pymupdf
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeEmbeddingSpace,
)
from agente_ia_edu.services.knowledge_engine.document_ingress import ResolvedDocumentFile
from agente_ia_edu.services.knowledge_engine.documents import (
    KnowledgeDocumentNotFound,
    KnowledgeDocumentProcessingError,
    KnowledgeDocumentService,
)
from agente_ia_edu.services.knowledge_engine.sources import KnowledgeSourceService
from agente_ia_edu.services.material_storage import MaterialStorage

_PROSE = "O mol participa da reacao em proporcao definida pela equacao. " * 30


def _write_pdf(path: Path, pages: list[str]) -> Path:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((40, 60), text, fontsize=8)
    document.save(str(path))
    document.close()
    return path


class _DocumentsCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=True)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.storage = MaterialStorage(root=self.tmp / "storage")

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _register(self, *, source_kind="TEXTBOOK", pages=None, filename="livro.pdf"):
        path = _write_pdf(
            self.tmp / filename, pages or ["Capítulo 1 - Estequiometria\n\n" + _PROSE]
        )
        async with self.factory() as session:
            sources = KnowledgeSourceService(session, storage=self.storage)
            source = await sources.register(
                title="Fonte",
                source_kind=source_kind,
                rights_class="COMMERCIAL_REFERENCE" if source_kind == "TEXTBOOK" else "OWN",
                authority_level="COMMERCIAL_TEXTBOOK" if source_kind == "TEXTBOOK" else "OWN",
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


class HappyPathTests(_DocumentsCase):
    async def test_a_text_layer_pdf_is_extracted_and_chunked(self):
        """Critério de aceite 1."""
        source_id, document_id = await self._register()
        async with self.factory() as session:
            snapshot = await KnowledgeDocumentService(session).extract_and_chunk(
                source_id, document_id
            )
        self.assertEqual(snapshot.extraction_status, "EXTRACTED")
        self.assertEqual(snapshot.extraction_method, "PDF_TEXT_LAYER")
        self.assertEqual(snapshot.page_count, 1)
        self.assertGreater(snapshot.chunks_created, 0)
        self.assertIsNone(snapshot.extraction_error)

    async def test_pages_without_text_are_recorded_even_when_extracted(self):
        """Pedido explicito: registrar SEMPRE, nao so quando PARTIAL."""
        source_id, document_id = await self._register(
            pages=["Capítulo 1 - T\n\n" + _PROSE, "", "Capítulo 2 - S\n\n" + _PROSE]
        )
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_and_chunk(source_id, document_id)
            from agente_ia_edu.db.models import KnowledgeDocument

            document = await session.get(KnowledgeDocument, document_id)
            extraction = document.metadata_["extraction"]
        self.assertIn("pages_without_text", extraction)
        self.assertEqual(extraction["pages_without_text"], [2])
        self.assertEqual(document.extraction_status, "EXTRACTED")

    async def test_every_persisted_chunk_has_a_page(self):
        """Critério de aceite 2, agora no banco."""
        source_id, document_id = await self._register()
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_and_chunk(source_id, document_id)
        async with self.factory() as session:
            without_page = await session.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.page_start.is_(None))
            )
        self.assertEqual(without_page, 0)

    async def test_the_content_node_is_left_null_in_this_phase(self):
        """Casar chunk com curriculo e trabalho do matcher, na Fase 4."""
        source_id, document_id = await self._register()
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_and_chunk(source_id, document_id)
        async with self.factory() as session:
            mapped = await session.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.content_node_id.isnot(None))
            )
        self.assertEqual(mapped, 0)


class ReentrancyTests(_DocumentsCase):
    async def test_a_second_call_without_force_is_refused(self):
        """Critério de aceite 4."""
        source_id, document_id = await self._register()
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_and_chunk(source_id, document_id)
        async with self.factory() as session:
            before = await session.scalar(select(func.count()).select_from(KnowledgeChunk))
            with self.assertRaises(KnowledgeDocumentProcessingError) as caught:
                await KnowledgeDocumentService(session).extract_and_chunk(
                    source_id, document_id
                )
            self.assertEqual(caught.exception.code, "ALREADY_CHUNKED")
        async with self.factory() as session:
            after = await session.scalar(select(func.count()).select_from(KnowledgeChunk))
        self.assertEqual(before, after)

    async def test_force_rechunks_and_keeps_the_hashes_identical(self):
        """Critério de aceite 3, pelo caminho do servico."""
        source_id, document_id = await self._register()
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_and_chunk(source_id, document_id)
        async with self.factory() as session:
            first = list(
                (
                    await session.scalars(
                        select(KnowledgeChunk.text_hash).order_by(KnowledgeChunk.ordinal)
                    )
                ).all()
            )
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_and_chunk(
                source_id, document_id, force=True
            )
        async with self.factory() as session:
            second = list(
                (
                    await session.scalars(
                        select(KnowledgeChunk.text_hash).order_by(KnowledgeChunk.ordinal)
                    )
                ).all()
            )
        self.assertEqual(first, second)

    async def test_force_is_refused_when_an_embedding_references_the_chunks(self):
        """Critério de aceite 5. Apagar chunk com embedding destruiria a
        procedencia de um Pack ja entregue - e as FKs sao RESTRICT."""
        source_id, document_id = await self._register()
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_and_chunk(source_id, document_id)
        async with self.factory() as session:
            chunk = await session.scalar(select(KnowledgeChunk))
            space = KnowledgeEmbeddingSpace(
                provider="fake", model="m", dimensions=3, status="ACTIVE"
            )
            session.add(space)
            await session.flush()
            session.add(
                KnowledgeChunkEmbedding(
                    chunk_id=chunk.id,
                    space_id=space.id,
                    embedding=[0.1, 0.2, 0.3],
                    text_hash=chunk.text_hash,
                    is_active=True,
                )
            )
            await session.commit()

        async with self.factory() as session:
            before = await session.scalar(select(func.count()).select_from(KnowledgeChunk))
            with self.assertRaises(KnowledgeDocumentProcessingError) as caught:
                await KnowledgeDocumentService(session).extract_and_chunk(
                    source_id, document_id, force=True
                )
            self.assertEqual(caught.exception.code, "EMBEDDINGS_PRESENT")
        async with self.factory() as session:
            after = await session.scalar(select(func.count()).select_from(KnowledgeChunk))
        self.assertEqual(before, after)


class RefusalTests(_DocumentsCase):
    async def test_a_curriculum_framework_source_is_refused(self):
        """Critério de aceite 8. Janelar habilidades da BNCC por tamanho
        produziria chunks plausiveis e errados."""
        source_id, document_id = await self._register(source_kind="CURRICULUM_FRAMEWORK")
        async with self.factory() as session:
            with self.assertRaises(KnowledgeDocumentProcessingError) as caught:
                await KnowledgeDocumentService(session).extract_and_chunk(
                    source_id, document_id
                )
            self.assertEqual(caught.exception.code, "UNSUPPORTED_SOURCE_KIND_FOR_PHASE")
            self.assertIn("Fase 4", str(caught.exception))
        async with self.factory() as session:
            chunks = await session.scalar(select(func.count()).select_from(KnowledgeChunk))
        self.assertEqual(chunks, 0)

    async def test_own_material_and_article_use_the_prose_path(self):
        for kind in ("OWN_MATERIAL", "ARTICLE"):
            source_id, document_id = await self._register(
                source_kind=kind, filename=f"{kind}.pdf"
            )
            async with self.factory() as session:
                snapshot = await KnowledgeDocumentService(session).extract_and_chunk(
                    source_id, document_id
                )
            self.assertEqual(snapshot.extraction_status, "EXTRACTED", kind)

    async def test_an_image_only_pdf_fails_pointing_at_ocr(self):
        """Critério de aceite 6. OCR esta fora da fase, e o estado resultante
        e CONSULTAVEL - da para listar o que espera OCR."""
        source_id, document_id = await self._register(pages=["", ""], filename="imagem.pdf")
        async with self.factory() as session:
            snapshot = await KnowledgeDocumentService(session).extract_and_chunk(
                source_id, document_id
            )
        self.assertEqual(snapshot.extraction_status, "FAILED")
        self.assertTrue(snapshot.extraction_error.startswith("OCR_REQUIRED"))
        self.assertEqual(snapshot.chunks_created, 0)
        async with self.factory() as session:
            chunks = await session.scalar(select(func.count()).select_from(KnowledgeChunk))
        self.assertEqual(chunks, 0)

    async def test_an_unknown_document_is_refused(self):
        source_id, _ = await self._register()
        async with self.factory() as session:
            with self.assertRaises(KnowledgeDocumentNotFound):
                await KnowledgeDocumentService(session).extract_and_chunk(
                    source_id, uuid.uuid4()
                )

    async def test_a_document_of_another_source_is_refused(self):
        _, document_id = await self._register()
        other_source_id, _ = await self._register(filename="outro.pdf")
        async with self.factory() as session:
            with self.assertRaises(KnowledgeDocumentNotFound):
                await KnowledgeDocumentService(session).extract_and_chunk(
                    other_source_id, document_id
                )


class StatsTests(_DocumentsCase):
    async def test_stats_report_the_distribution_and_page_coverage(self):
        source_id, document_id = await self._register(
            pages=[
                "Capítulo 1 - T\n\n" + _PROSE + "\n\nExemplo resolvido\nCalcule a massa.",
                "Capítulo 2 - S\n\n" + _PROSE,
            ]
        )
        async with self.factory() as session:
            await KnowledgeDocumentService(session).extract_and_chunk(source_id, document_id)
            stats = await KnowledgeDocumentService(session).chunk_stats(source_id)
        self.assertGreater(stats["total_chunks"], 0)
        self.assertEqual(stats["chunks_without_page"], 0)
        self.assertEqual(stats["page_start_min"], 1)
        self.assertGreaterEqual(stats["page_end_max"], 1)
        self.assertIn("PROSE", stats["by_chunk_type"])


if __name__ == "__main__":
    unittest.main()
