"""CEREBRO / Knowledge Engine - Fase 2: o servico de fontes e documentos.

Ao fim desta fase o corpus tem fontes e documentos, e NENHUM chunk. Varios
testes aqui existem so para garantir que a fase nao atravessou essa linha.

O servico devolve SNAPSHOTS imutaveis, nunca objetos ORM vivos. Isso nao e
estilo: com ``expire_on_commit=True`` (o padrao de producao deste projeto),
tocar um atributo de objeto ORM depois de um commit levanta
``MissingGreenlet`` - a armadilha numero um da base. Devolver dataclass
congelada torna o erro impossivel para o chamador, em vez de depender de
cada chamador lembrar.
"""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSource
from agente_ia_edu.services.knowledge_engine.document_ingress import ResolvedDocumentFile
from agente_ia_edu.services.knowledge_engine.rights import KnowledgeRightsViolation
from agente_ia_edu.services.knowledge_engine.sources import (
    KnowledgeSourceNotFound,
    KnowledgeSourceService,
)
from agente_ia_edu.services.material_storage import MaterialStorage


def _resolved(path: Path, ingress: str = "LOCAL_PATH") -> ResolvedDocumentFile:
    return ResolvedDocumentFile(
        path=path,
        original_filename=path.name,
        mime_type="application/pdf",
        size_bytes=path.stat().st_size,
        ingress=ingress,
    )


class _ServiceCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=True
        )
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        # Raiz de storage temporaria: nenhum teste toca var/material_storage.
        self.storage = MaterialStorage(root=self.tmp_root / "storage")
        self.source_dir = self.tmp_root / "origem"
        self.source_dir.mkdir()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _file(self, name: str, content: bytes = b"%PDF-1.7 conteudo") -> Path:
        path = self.source_dir / name
        path.write_bytes(content)
        return path

    async def _register(self, session, **overrides):
        service = KnowledgeSourceService(session, storage=self.storage)
        payload = dict(
            title="Quimica na abordagem do cotidiano",
            source_kind="TEXTBOOK",
            rights_class="COMMERCIAL_REFERENCE",
            authority_level="COMMERCIAL_TEXTBOOK",
            created_by_external_identity="user:ADMIN",
        )
        payload.update(overrides)
        return await service.register(**payload)


class RegisterSourceTests(_ServiceCase):
    async def test_a_registered_source_starts_as_registered(self):
        async with self.session_factory() as session:
            snapshot = await self._register(session)
        self.assertEqual(snapshot.status, "REGISTERED")
        self.assertEqual(snapshot.rights_class, "COMMERCIAL_REFERENCE")
        self.assertEqual(snapshot.authority_level, "COMMERCIAL_TEXTBOOK")
        self.assertEqual(snapshot.document_count, 0)

    async def test_the_snapshot_is_readable_after_the_commit(self):
        """O teste de MissingGreenlet: se o servico devolvesse o objeto ORM,
        qualquer leitura aqui explodiria."""
        async with self.session_factory() as session:
            snapshot = await self._register(session)
            # Depois do commit, e fora da sessao, tudo continua legivel.
        self.assertIsInstance(snapshot.id, uuid.UUID)
        self.assertEqual(snapshot.title, "Quimica na abordagem do cotidiano")
        self.assertIsNotNone(snapshot.created_at.isoformat())

    async def test_a_commercial_source_with_a_resource_is_refused_before_the_database(self):
        """Tem de ser KnowledgeRightsViolation, nao IntegrityError: o chamador
        recebe o motivo nomeado, e a CheckConstraint segue como ultima
        defesa."""
        async with self.session_factory() as session:
            with self.assertRaises(KnowledgeRightsViolation):
                await self._register(session, educational_resource_id=uuid.uuid4())

    async def test_nothing_is_written_when_the_rights_check_refuses(self):
        async with self.session_factory() as session:
            with self.assertRaises(KnowledgeRightsViolation):
                await self._register(session, educational_resource_id=uuid.uuid4())
        async with self.session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(KnowledgeSource))
        self.assertEqual(total, 0)

    async def test_an_official_source_may_be_registered_with_authority_official(self):
        async with self.session_factory() as session:
            snapshot = await self._register(
                session,
                title="BNCC Ensino Medio",
                source_kind="CURRICULUM_FRAMEWORK",
                rights_class="OFFICIAL_PUBLIC",
                authority_level="OFFICIAL",
            )
        self.assertEqual(snapshot.source_kind, "CURRICULUM_FRAMEWORK")
        self.assertEqual(snapshot.authority_level, "OFFICIAL")


class RegisterDocumentTests(_ServiceCase):
    async def test_a_document_lands_in_storage_and_in_the_table(self):
        path = self._file("volume1.pdf")
        async with self.session_factory() as session:
            source = await self._register(session)
            service = KnowledgeSourceService(session, storage=self.storage)
            registration = await service.register_document(source.id, _resolved(path))

        self.assertTrue(registration.created)
        document = registration.document
        self.assertEqual(document.filename, "volume1.pdf")
        self.assertEqual(len(document.document_hash), 64)
        self.assertTrue(Path(document.storage_uri).is_file())
        # O arquivo de origem nunca e movido nem alterado.
        self.assertTrue(path.is_file())

    async def test_registering_the_same_bytes_twice_creates_one_row_and_one_copy(self):
        path = self._file("volume1.pdf")
        async with self.session_factory() as session:
            source = await self._register(session)
            service = KnowledgeSourceService(session, storage=self.storage)
            first = await service.register_document(source.id, _resolved(path))
            second = await service.register_document(source.id, _resolved(path))

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.document.id, second.document.id)

        async with self.session_factory() as session:
            rows = await session.scalar(select(func.count()).select_from(KnowledgeDocument))
        self.assertEqual(rows, 1)

        stored = list((self.tmp_root / "storage").rglob("volume1.pdf"))
        self.assertEqual(len(stored), 1)

    async def test_a_renamed_copy_of_the_same_bytes_is_still_the_same_document(self):
        """A identidade e o hash do conteudo, nao o nome do arquivo."""
        first_path = self._file("volume1.pdf")
        second_path = self._file("volume1-copia.pdf")
        async with self.session_factory() as session:
            source = await self._register(session)
            service = KnowledgeSourceService(session, storage=self.storage)
            first = await service.register_document(source.id, _resolved(first_path))
            second = await service.register_document(source.id, _resolved(second_path))
        self.assertEqual(first.document.id, second.document.id)
        self.assertFalse(second.created)

    async def test_different_bytes_are_different_documents(self):
        async with self.session_factory() as session:
            source = await self._register(session)
            service = KnowledgeSourceService(session, storage=self.storage)
            first = await service.register_document(
                source.id, _resolved(self._file("v1.pdf", b"%PDF um"))
            )
            second = await service.register_document(
                source.id, _resolved(self._file("v2.pdf", b"%PDF dois"))
            )
        self.assertNotEqual(first.document.id, second.document.id)
        self.assertNotEqual(first.document.document_hash, second.document.document_hash)

    async def test_a_document_is_pending_with_no_page_count_and_no_method(self):
        """Derivar page_count exige abrir o arquivo, o que e extracao - Fase 3.
        A Fase 2 nao pode avancar esse estado."""
        async with self.session_factory() as session:
            source = await self._register(session)
            service = KnowledgeSourceService(session, storage=self.storage)
            registration = await service.register_document(
                source.id, _resolved(self._file("v.pdf"))
            )
        self.assertEqual(registration.document.extraction_status, "PENDING")
        self.assertIsNone(registration.document.page_count)
        self.assertIsNone(registration.document.extraction_method)

    async def test_page_offset_is_accepted_from_the_caller_and_defaults_to_zero(self):
        async with self.session_factory() as session:
            source = await self._register(session)
            service = KnowledgeSourceService(session, storage=self.storage)
            default = await service.register_document(
                source.id, _resolved(self._file("a.pdf", b"a"))
            )
            offset = await service.register_document(
                source.id, _resolved(self._file("b.pdf", b"b")), page_offset=311
            )
        self.assertEqual(default.document.page_offset, 0)
        self.assertEqual(offset.document.page_offset, 311)

    async def test_the_ingress_is_recorded_for_audit(self):
        async with self.session_factory() as session:
            source = await self._register(session)
            service = KnowledgeSourceService(session, storage=self.storage)
            registration = await service.register_document(
                source.id, _resolved(self._file("v.pdf"))
            )
        self.assertEqual(registration.document.ingress, "LOCAL_PATH")

    async def test_an_upload_ingress_goes_through_the_very_same_path(self):
        """A prova do desacoplamento: o servico nunca ramifica em ``ingress``.

        Quando a tela de administracao existir, UPLOAD constroi o mesmo
        ResolvedDocumentFile e nada aqui muda - nem modelo, nem storage, nem
        idempotencia.
        """
        path = self._file("enviado.pdf")
        async with self.session_factory() as session:
            source = await self._register(session)
            service = KnowledgeSourceService(session, storage=self.storage)
            registration = await service.register_document(
                source.id, _resolved(path, ingress="UPLOAD")
            )
            # Mesmos bytes, agora por LOCAL_PATH: continua sendo o mesmo documento.
            again = await service.register_document(source.id, _resolved(path))
        self.assertEqual(registration.document.ingress, "UPLOAD")
        self.assertTrue(registration.created)
        self.assertFalse(again.created)
        self.assertEqual(registration.document.id, again.document.id)

    async def test_registering_a_document_for_an_unknown_source_is_refused(self):
        async with self.session_factory() as session:
            service = KnowledgeSourceService(session, storage=self.storage)
            with self.assertRaises(KnowledgeSourceNotFound):
                await service.register_document(
                    uuid.uuid4(), _resolved(self._file("v.pdf"))
                )


class ReadAndArchiveTests(_ServiceCase):
    async def test_get_reports_the_document_count(self):
        async with self.session_factory() as session:
            source = await self._register(session)
            service = KnowledgeSourceService(session, storage=self.storage)
            await service.register_document(source.id, _resolved(self._file("a.pdf", b"a")))
            await service.register_document(source.id, _resolved(self._file("b.pdf", b"b")))
            detail = await service.get(source.id)
        self.assertEqual(detail.document_count, 2)

    async def test_get_on_an_unknown_source_is_refused(self):
        async with self.session_factory() as session:
            service = KnowledgeSourceService(session, storage=self.storage)
            with self.assertRaises(KnowledgeSourceNotFound):
                await service.get(uuid.uuid4())

    async def test_list_filters_by_rights_class_and_status(self):
        async with self.session_factory() as session:
            await self._register(session, title="Livro comercial")
            await self._register(
                session,
                title="BNCC",
                source_kind="CURRICULUM_FRAMEWORK",
                rights_class="OFFICIAL_PUBLIC",
                authority_level="OFFICIAL",
            )
            service = KnowledgeSourceService(session, storage=self.storage)
            commercial = await service.list_sources(rights_class="COMMERCIAL_REFERENCE")
            everything = await service.list_sources()
        self.assertEqual([s.title for s in commercial], ["Livro comercial"])
        self.assertEqual(len(everything), 2)

    async def test_archive_changes_the_status_and_keeps_the_row(self):
        async with self.session_factory() as session:
            source = await self._register(session)
            service = KnowledgeSourceService(session, storage=self.storage)
            archived = await service.archive(source.id)
        self.assertEqual(archived.status, "ARCHIVED")
        async with self.session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(KnowledgeSource))
        self.assertEqual(total, 1)

    async def test_archiving_twice_is_harmless(self):
        async with self.session_factory() as session:
            source = await self._register(session)
            service = KnowledgeSourceService(session, storage=self.storage)
            await service.archive(source.id)
            again = await service.archive(source.id)
        self.assertEqual(again.status, "ARCHIVED")

    async def test_an_archived_source_is_excluded_by_default_but_findable(self):
        async with self.session_factory() as session:
            source = await self._register(session)
            service = KnowledgeSourceService(session, storage=self.storage)
            await service.archive(source.id)
            default = await service.list_sources()
            explicit = await service.list_sources(status="ARCHIVED")
        self.assertEqual(default, [])
        self.assertEqual(len(explicit), 1)


class PhaseBoundaryTests(_ServiceCase):
    async def test_the_phase_cannot_produce_a_chunk(self):
        """Critério de aceite 6: nada que a Fase 2 faz cria chunk."""
        path = self._file("volume1.pdf")
        async with self.session_factory() as session:
            source = await self._register(session)
            service = KnowledgeSourceService(session, storage=self.storage)
            await service.register_document(source.id, _resolved(path))
            await service.get(source.id)
            await service.list_sources()
            await service.archive(source.id)
        async with self.session_factory() as session:
            chunks = await session.scalar(select(func.count()).select_from(KnowledgeChunk))
        self.assertEqual(chunks, 0)

    async def test_the_service_exposes_no_extraction_or_chunking_entry_point(self):
        """Nao antecipar fase posterior so porque a infraestrutura existe."""
        for forbidden in ("ingest", "extract", "chunk", "embed", "reembed", "retrieve"):
            self.assertFalse(
                any(
                    name == forbidden or name.startswith(f"{forbidden}_")
                    for name in dir(KnowledgeSourceService)
                ),
                f"KnowledgeSourceService nao deveria expor '{forbidden}' na Fase 2",
            )


if __name__ == "__main__":
    unittest.main()
