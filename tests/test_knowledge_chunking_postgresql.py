"""CEREBRO / Knowledge Engine - Fase 3: o que so o PostgreSQL async prova.

1. o caminho async real, com ``expire_on_commit=True``, nao levanta
   ``MissingGreenlet`` - a extracao comita varias vezes e e um candidato
   natural a essa armadilha;
2. ``PARTIAL`` e aceito pela CheckConstraint da migracao 059, e as regras que
   levam a ele valem com dados reais no banco;
3. insercao em escala (milhares de chunks) e ``page_start`` nao nulo em todos.

Banco DESCARTAVEL proprio. Nao toca o banco de desenvolvimento.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import pymupdf
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
import agente_ia_edu.db.models  # noqa: F401
from agente_ia_edu.db.models import KnowledgeChunk, KnowledgeDocument
from agente_ia_edu.services.knowledge_engine.document_ingress import ResolvedDocumentFile
from agente_ia_edu.services.knowledge_engine.documents import KnowledgeDocumentService
from agente_ia_edu.services.knowledge_engine.sources import KnowledgeSourceService
from agente_ia_edu.services.material_storage import MaterialStorage

_PROSE = "O mol participa da reacao em proporcao definida pela equacao. " * 40


class KnowledgeChunkingPostgreSQL(unittest.IsolatedAsyncioTestCase):
    database_name = "agente_ia_edu_knowledge_chunking_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres"
    url = f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}"

    @classmethod
    def _admin(cls, statement: str) -> None:
        engine = create_engine(cls.admin_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin("SELECT 1")
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest("PostgreSQL de teste indisponivel") from exc
        cls._admin(f"DROP DATABASE IF EXISTS {cls.database_name}")
        cls._admin(f"CREATE DATABASE {cls.database_name}")
        sync = create_engine(cls.url)
        try:
            Base.metadata.create_all(sync)
        finally:
            sync.dispose()

    @classmethod
    def tearDownClass(cls):
        cls._admin(f"DROP DATABASE IF EXISTS {cls.database_name}")

    async def asyncSetUp(self):
        self.engine = create_async_engine(self.url)
        # O padrao de PRODUCAO. Com False, o teste de MissingGreenlet nao
        # provaria nada.
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=True
        )
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.storage = MaterialStorage(root=self.tmp / "storage")
        async with self.factory() as session:
            # A ORDEM importa, e e a propria topologia do subsistema: as FKs
            # sao RESTRICT, e desde a Fase 5 a ingestao escreve o indice
            # lexical na mesma transacao que os chunks. Apagar chunk antes dos
            # seus postings e recusado pelo banco - exatamente o que
            # ``test_knowledge_lexical_postgresql`` assere como comportamento
            # correto, e exatamente por que ``documents.py`` purga o indice
            # antes de re-chunkar com ``force``.
            await session.execute(text("DELETE FROM knowledge_chunk_terms"))
            await session.execute(text("DELETE FROM knowledge_chunk_lexical_index"))
            await session.execute(text("DELETE FROM knowledge_lexical_index_state"))
            await session.execute(text("DELETE FROM knowledge_chunks"))
            await session.execute(text("DELETE FROM knowledge_documents"))
            await session.execute(text("DELETE FROM knowledge_sources"))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _pdf(self, name: str, pages: list[str]) -> Path:
        path = self.tmp / name
        document = pymupdf.open()
        for body in pages:
            page = document.new_page()
            if body:
                page.insert_text((40, 60), body, fontsize=8)
        document.save(str(path))
        document.close()
        return path

    async def _register(self, path: Path):
        async with self.factory() as session:
            sources = KnowledgeSourceService(session, storage=self.storage)
            source = await sources.register(
                title="Livro", source_kind="TEXTBOOK",
                rights_class="COMMERCIAL_REFERENCE",
                authority_level="COMMERCIAL_TEXTBOOK",
            )
            registration = await sources.register_document(
                source.id,
                ResolvedDocumentFile(
                    path=path, original_filename=path.name, mime_type="application/pdf",
                    size_bytes=path.stat().st_size, ingress="LOCAL_PATH",
                ),
            )
        return source.id, registration.document.id

    async def test_the_async_path_never_raises_missing_greenlet(self):
        path = self._pdf("livro.pdf", [f"Capítulo {i} - Tema {i}\n\n{_PROSE}" for i in range(1, 6)])
        source_id, document_id = await self._register(path)
        async with self.factory() as session:
            snapshot = await KnowledgeDocumentService(session).extract_and_chunk(
                source_id, document_id
            )
            stats = await KnowledgeDocumentService(session).chunk_stats(source_id)
        # Leitura DEPOIS de varios commits e fora da sessao.
        self.assertEqual(snapshot.extraction_status, "EXTRACTED")
        self.assertEqual(snapshot.page_count, 5)
        self.assertGreater(snapshot.chunks_created, 0)
        self.assertEqual(stats["chunks_without_page"], 0)
        self.assertIsNotNone(snapshot.updated_at.isoformat())

    async def test_partial_is_accepted_by_the_059_check_constraint(self):
        """A migracao 059 existe para que este estado seja representavel."""
        path = self._pdf("p.pdf", [f"Capítulo 1 - T\n\n{_PROSE}"])
        _, document_id = await self._register(path)
        async with self.factory() as session:
            document = await session.get(KnowledgeDocument, document_id)
            document.extraction_status = "PARTIAL"
            await session.commit()
        async with self.factory() as session:
            document = await session.get(KnowledgeDocument, document_id)
        self.assertEqual(document.extraction_status, "PARTIAL")

    async def test_an_unknown_extraction_status_is_still_refused(self):
        """A 059 acrescentou UM valor; nao afrouxou a constraint."""
        path = self._pdf("q.pdf", [f"Capítulo 1 - T\n\n{_PROSE}"])
        _, document_id = await self._register(path)
        with self.assertRaises(IntegrityError):
            async with self.factory() as session:
                document = await session.get(KnowledgeDocument, document_id)
                document.extraction_status = "MOSTLY_FINE"
                await session.commit()

    async def test_a_contiguous_gap_marks_the_document_partial(self):
        """Buraco de tres paginas no meio de um capitulo e perda."""
        pages = [f"Capítulo 1 - T\n\n{_PROSE}", "", "", "", f"Capítulo 2 - S\n\n{_PROSE}"]
        path = self._pdf("gap.pdf", pages)
        source_id, document_id = await self._register(path)
        async with self.factory() as session:
            snapshot = await KnowledgeDocumentService(session).extract_and_chunk(
                source_id, document_id
            )
        self.assertEqual(snapshot.extraction_status, "PARTIAL")
        self.assertIn("CONTIGUOUS_GAP", snapshot.partial_reasons)
        self.assertEqual(list(snapshot.pages_without_text), [2, 3, 4])
        # Falha parcial NAO descarta trabalho bom.
        self.assertGreater(snapshot.chunks_created, 0)

    async def test_a_single_blank_interior_page_stays_extracted(self):
        """O criterio nao e "tem pagina vazia" - seria alarme demais."""
        pages = [f"Capítulo {i} - T{i}\n\n{_PROSE}" for i in range(1, 7)]
        pages.insert(3, "")
        pages.extend(f"Capítulo {i} - T{i}\n\n{_PROSE}" for i in range(7, 13))
        path = self._pdf("blank.pdf", pages)
        source_id, document_id = await self._register(path)
        async with self.factory() as session:
            snapshot = await KnowledgeDocumentService(session).extract_and_chunk(
                source_id, document_id
            )
        self.assertEqual(snapshot.extraction_status, "EXTRACTED")
        self.assertEqual(list(snapshot.pages_without_text), [4])

    async def test_thousands_of_chunks_persist_with_a_page_each(self):
        path = self._pdf(
            "grande.pdf",
            [f"Capítulo {i} - Tema {i}\n\n{_PROSE * 3}" for i in range(1, 41)],
        )
        source_id, document_id = await self._register(path)
        async with self.factory() as session:
            snapshot = await KnowledgeDocumentService(session).extract_and_chunk(
                source_id, document_id
            )
        self.assertGreater(snapshot.chunks_created, 100)
        async with self.factory() as session:
            total = await session.scalar(select(func.count()).select_from(KnowledgeChunk))
            without_page = await session.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.page_start.is_(None))
            )
        self.assertEqual(total, snapshot.chunks_created)
        self.assertEqual(without_page, 0)


if __name__ == "__main__":
    unittest.main()
