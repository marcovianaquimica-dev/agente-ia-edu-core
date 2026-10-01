"""CEREBRO / Knowledge Engine - Fase 2: o que so o PostgreSQL async prova.

Tres coisas que o SQLite nao responde:

1. o caminho async REAL com ``expire_on_commit=True`` - o padrao de producao
   deste projeto - nao levanta ``MissingGreenlet``, a armadilha numero um
   desta base. Ela aparece quando se le um atributo de objeto ORM depois de
   um commit, e e por isso que o servico devolve snapshots congelados em vez
   de objetos vivos;
2. a ``CheckConstraint`` segue sendo a ULTIMA linha de defesa quando a
   validacao de dominio e contornada - um INSERT direto no modelo, sem passar
   pelo service, ainda e recusado pelo banco;
3. arquivar uma fonte com documentos nao viola nenhuma FK ``RESTRICT``.

Banco DESCARTAVEL proprio, criado e destruido pelo teste. Nao toca o banco de
desenvolvimento.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
import agente_ia_edu.db.models  # noqa: F401 - registra todo o metadata
from agente_ia_edu.db.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSource
from agente_ia_edu.services.knowledge_engine.document_ingress import ResolvedDocumentFile
from agente_ia_edu.services.knowledge_engine.rights import KnowledgeRightsViolation
from agente_ia_edu.services.knowledge_engine.sources import KnowledgeSourceService
from agente_ia_edu.services.material_storage import MaterialStorage


class KnowledgeSourcesPostgreSQL(unittest.IsolatedAsyncioTestCase):
    database_name = "agente_ia_edu_knowledge_sources_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres"
    sync_url = f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}"
    async_url = f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}"

    @classmethod
    def _admin_execute(cls, statement: str) -> None:
        engine = create_engine(cls.admin_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:  # pragma: no cover - ambiente sem Postgres
            raise unittest.SkipTest(
                "PostgreSQL de teste indisponivel para o cadastro de fontes"
            ) from exc
        cls._admin_execute(f"DROP DATABASE IF EXISTS {cls.database_name}")
        cls._admin_execute(f"CREATE DATABASE {cls.database_name}")
        # A extensao vector e criada pelo listener before_create em
        # db/models/knowledge_engine.py - nao a criamos aqui de proposito.
        sync_engine = create_engine(cls.sync_url)
        try:
            Base.metadata.create_all(sync_engine)
        finally:
            sync_engine.dispose()

    @classmethod
    def tearDownClass(cls):
        cls._admin_execute(f"DROP DATABASE IF EXISTS {cls.database_name}")

    async def asyncSetUp(self):
        self.engine = create_async_engine(self.async_url)
        # expire_on_commit=True: o padrao de PRODUCAO. Com False o teste de
        # MissingGreenlet nao provaria nada.
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=True
        )
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        self.storage = MaterialStorage(root=self.tmp_root / "storage")
        async with self.factory() as session:
            await session.execute(text("DELETE FROM knowledge_documents"))
            await session.execute(text("DELETE FROM knowledge_sources"))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _file(self, name: str, content: bytes = b"%PDF-1.7 conteudo") -> Path:
        path = self.tmp_root / name
        path.write_bytes(content)
        return path

    # -- 1. o caminho async real -----------------------------------------

    async def test_the_full_async_path_never_raises_missing_greenlet(self):
        async with self.factory() as session:
            service = KnowledgeSourceService(session, storage=self.storage)
            source = await service.register(
                title="Moderna Plus - Quimica na abordagem do cotidiano",
                source_kind="TEXTBOOK",
                rights_class="COMMERCIAL_REFERENCE",
                authority_level="COMMERCIAL_TEXTBOOK",
                created_by_external_identity="user:ADMIN",
            )
            registration = await service.register_document(
                source.id,
                ResolvedDocumentFile(
                    path=self._file("volume1.pdf"),
                    original_filename="volume1.pdf",
                    mime_type="application/pdf",
                    size_bytes=17,
                    ingress="LOCAL_PATH",
                ),
            )
            detail = await service.get(source.id)
            archived = await service.archive(source.id)

        # Tudo abaixo e leitura DEPOIS de varios commits e fora da sessao.
        # Se o servico devolvesse objetos ORM, cada linha destas explodiria.
        self.assertEqual(source.title, "Moderna Plus - Quimica na abordagem do cotidiano")
        self.assertEqual(registration.document.filename, "volume1.pdf")
        self.assertEqual(registration.document.extraction_status, "PENDING")
        self.assertEqual(detail.document_count, 1)
        self.assertEqual(archived.status, "ARCHIVED")
        self.assertIsNotNone(archived.updated_at.isoformat())

    async def test_idempotency_holds_on_real_postgresql(self):
        path = self._file("volume1.pdf")
        file = ResolvedDocumentFile(
            path=path,
            original_filename="volume1.pdf",
            mime_type="application/pdf",
            size_bytes=path.stat().st_size,
            ingress="LOCAL_PATH",
        )
        async with self.factory() as session:
            service = KnowledgeSourceService(session, storage=self.storage)
            source = await service.register(
                title="Fonte", source_kind="TEXTBOOK",
                rights_class="OWN", authority_level="OWN",
            )
            first = await service.register_document(source.id, file)
            second = await service.register_document(source.id, file)
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        async with self.factory() as session:
            rows = await session.scalar(select(func.count()).select_from(KnowledgeDocument))
        self.assertEqual(rows, 1)

    # -- 2. a constraint como ultima defesa ------------------------------

    async def test_the_service_refuses_a_commercial_source_with_a_resource(self):
        async with self.factory() as session:
            service = KnowledgeSourceService(session, storage=self.storage)
            with self.assertRaises(KnowledgeRightsViolation):
                await service.register(
                    title="Livro", source_kind="TEXTBOOK",
                    rights_class="COMMERCIAL_REFERENCE",
                    authority_level="COMMERCIAL_TEXTBOOK",
                    educational_resource_id=uuid.uuid4(),
                )

    async def test_the_check_constraint_still_refuses_when_the_service_is_bypassed(self):
        """A validacao de dominio e a primeira linha; a do banco e a ultima.

        Um INSERT direto no modelo contorna ``rights.py`` inteiro - e e
        exatamente por isso que a trava tem de existir no banco tambem.
        """
        resource_id = uuid.uuid4()
        async with self.factory() as session:
            await session.execute(
                text(
                    "INSERT INTO educational_resources (id, title, resource_type, origin_type, "
                    "status, visibility_scope, created_at, updated_at) VALUES "
                    "(:id, 'Livro', 'BOOK', 'LICENSED', 'active', 'LICENSED', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ).bindparams(id=resource_id)
            )
            await session.commit()

        with self.assertRaises(IntegrityError) as caught:
            async with self.factory() as session:
                session.add(
                    KnowledgeSource(
                        title="Contornando o service",
                        source_kind="TEXTBOOK",
                        rights_class="COMMERCIAL_REFERENCE",
                        authority_level="COMMERCIAL_TEXTBOOK",
                        educational_resource_id=resource_id,
                    )
                )
                await session.commit()
        self.assertIn("ck_knowledge_sources_commercial_has_no_resource", str(caught.exception))

    async def test_a_non_commercial_source_may_point_at_a_resource(self):
        """A trava e cirurgica: a BNCC pode, sim."""
        resource_id = uuid.uuid4()
        async with self.factory() as session:
            await session.execute(
                text(
                    "INSERT INTO educational_resources (id, title, resource_type, origin_type, "
                    "status, visibility_scope, created_at, updated_at) VALUES "
                    "(:id, 'BNCC EM', 'PDF', 'EXTERNAL', 'active', 'PUBLIC', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ).bindparams(id=resource_id)
            )
            await session.commit()
        async with self.factory() as session:
            service = KnowledgeSourceService(session, storage=self.storage)
            snapshot = await service.register(
                title="BNCC Ensino Medio",
                source_kind="CURRICULUM_FRAMEWORK",
                rights_class="OFFICIAL_PUBLIC",
                authority_level="OFFICIAL",
                educational_resource_id=resource_id,
            )
        self.assertEqual(snapshot.educational_resource_id, resource_id)

    # -- 3. arquivamento e as FKs RESTRICT -------------------------------

    async def test_archiving_a_source_with_documents_violates_no_restrict_fk(self):
        async with self.factory() as session:
            service = KnowledgeSourceService(session, storage=self.storage)
            source = await service.register(
                title="Fonte com documentos", source_kind="TEXTBOOK",
                rights_class="OWN", authority_level="OWN",
            )
            for index in range(2):
                path = self._file(f"doc{index}.pdf", f"%PDF {index}".encode())
                await service.register_document(
                    source.id,
                    ResolvedDocumentFile(
                        path=path,
                        original_filename=path.name,
                        mime_type="application/pdf",
                        size_bytes=path.stat().st_size,
                        ingress="LOCAL_PATH",
                    ),
                )
            archived = await service.archive(source.id)

        self.assertEqual(archived.status, "ARCHIVED")
        self.assertEqual(archived.document_count, 2)
        async with self.factory() as session:
            sources = await session.scalar(select(func.count()).select_from(KnowledgeSource))
            documents = await session.scalar(
                select(func.count()).select_from(KnowledgeDocument)
            )
        self.assertEqual((sources, documents), (1, 2))

    # -- fronteira da fase -----------------------------------------------

    async def test_the_phase_produced_no_chunk_on_real_postgresql(self):
        """Critério de aceite 6, no banco de verdade."""
        async with self.factory() as session:
            service = KnowledgeSourceService(session, storage=self.storage)
            source = await service.register(
                title="Fonte", source_kind="TEXTBOOK",
                rights_class="OWN", authority_level="OWN",
            )
            path = self._file("v.pdf")
            await service.register_document(
                source.id,
                ResolvedDocumentFile(
                    path=path, original_filename="v.pdf", mime_type="application/pdf",
                    size_bytes=path.stat().st_size, ingress="LOCAL_PATH",
                ),
            )
        async with self.factory() as session:
            chunks = await session.scalar(select(func.count()).select_from(KnowledgeChunk))
        self.assertEqual(chunks, 0)


if __name__ == "__main__":
    unittest.main()
