"""Cadastro de fontes e documentos do corpus de conhecimento (Fase 2).

ESCOPO. Uma fonte pode ser cadastrada com direitos e autoridade declarados,
ter arquivos registrados em armazenamento enderecado por conteudo, e ser lida
de volta. Nada mais. Ao fim da fase o corpus tem fontes e documentos e
NENHUM chunk - extracao, chunking, indice lexical, embedding, recuperacao e
Knowledge Pack sao fases posteriores, e o fato de a infraestrutura delas ja
existir (desde a Fase 1) nao e motivo para antecipa-las.

Este modulo NAO sabe de onde o arquivo veio. Ele recebe um
``ResolvedDocumentFile`` ja validado e normalizado, e nenhuma regra aqui
ramifica em ``ingress``. E o que permite acrescentar upload depois sem mexer
no modelo, no armazenamento nem na idempotencia.

SOBRE ``MissingGreenlet``. A armadilha numero um desta base: com
``expire_on_commit=True`` (o padrao de producao, ver ``db/session.py``), um
commit expira TODOS os objetos ORM da sessao, e qualquer leitura sincrona de
atributo depois disso explode. Em vez de pedir a cada chamador que lembre
disso, todo metodo publico aqui devolve uma dataclass CONGELADA, montada com
valores capturados antes do commit. O erro fica impossivel por construcao.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import KnowledgeDocument, KnowledgeSource
from ..material_storage import MaterialStorage, file_sha256
from .document_ingress import ResolvedDocumentFile
from .rights import validate_source_rights


class KnowledgeSourceNotFound(LookupError):
    """A fonte indicada nao existe."""


@dataclass(frozen=True)
class SourceSnapshot:
    """Leitura imutavel de uma fonte. Ver nota sobre MissingGreenlet."""

    id: UUID
    title: str
    authors: str | None
    publisher: str | None
    edition: str | None
    publication_year: int | None
    isbn: str | None
    source_kind: str
    rights_class: str
    authority_level: str
    license_reference: str | None
    rights_notes: str | None
    educational_resource_id: UUID | None
    status: str
    created_by_external_identity: str | None
    created_at: datetime
    updated_at: datetime
    document_count: int


@dataclass(frozen=True)
class DocumentSnapshot:
    id: UUID
    source_id: UUID
    filename: str
    storage_uri: str
    document_hash: str
    mime_type: str | None
    file_size_bytes: int | None
    page_offset: int
    page_count: int | None
    extraction_method: str | None
    extraction_status: str
    ingress: str | None
    created_at: datetime


@dataclass(frozen=True)
class DocumentRegistration:
    """O documento e se esta chamada o criou.

    ``created=False`` e o caso idempotente: os mesmos bytes ja estavam
    registrados nesta fonte, e nada foi escrito nem copiado de novo.
    """

    document: DocumentSnapshot
    created: bool


def _source_snapshot(source: KnowledgeSource, document_count: int) -> SourceSnapshot:
    return SourceSnapshot(
        id=source.id,
        title=source.title,
        authors=source.authors,
        publisher=source.publisher,
        edition=source.edition,
        publication_year=source.publication_year,
        isbn=source.isbn,
        source_kind=source.source_kind,
        rights_class=source.rights_class,
        authority_level=source.authority_level,
        license_reference=source.license_reference,
        rights_notes=source.rights_notes,
        educational_resource_id=source.educational_resource_id,
        status=source.status,
        created_by_external_identity=source.created_by_external_identity,
        created_at=source.created_at,
        updated_at=source.updated_at,
        document_count=document_count,
    )


def _document_snapshot(document: KnowledgeDocument) -> DocumentSnapshot:
    metadata: dict[str, Any] = document.metadata_ or {}
    return DocumentSnapshot(
        id=document.id,
        source_id=document.source_id,
        filename=document.filename,
        storage_uri=document.storage_uri,
        document_hash=document.document_hash,
        mime_type=document.mime_type,
        file_size_bytes=document.file_size_bytes,
        page_offset=document.page_offset,
        page_count=document.page_count,
        extraction_method=document.extraction_method,
        extraction_status=document.extraction_status,
        ingress=metadata.get("ingress"),
        created_at=document.created_at,
    )


class KnowledgeSourceService:
    def __init__(self, session: AsyncSession, storage: MaterialStorage | None = None) -> None:
        self.session = session
        self._storage = storage or MaterialStorage()

    # -- escrita ---------------------------------------------------------

    async def register(
        self,
        *,
        title: str,
        source_kind: str,
        rights_class: str,
        authority_level: str,
        authors: str | None = None,
        publisher: str | None = None,
        edition: str | None = None,
        publication_year: int | None = None,
        isbn: str | None = None,
        license_reference: str | None = None,
        rights_notes: str | None = None,
        educational_resource_id: UUID | None = None,
        created_by_external_identity: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SourceSnapshot:
        """Cadastra uma fonte com ``status=REGISTERED``.

        A politica de direitos e consultada ANTES de qualquer escrita, para
        que o chamador receba um motivo nomeado em vez de um IntegrityError
        cru. O CheckConstraint do banco segue como ultima linha de defesa.
        """
        validate_source_rights(
            rights_class=rights_class,
            authority_level=authority_level,
            source_kind=source_kind,
            educational_resource_id=educational_resource_id,
        )

        source = KnowledgeSource(
            title=title,
            authors=authors,
            publisher=publisher,
            edition=edition,
            publication_year=publication_year,
            isbn=isbn,
            source_kind=source_kind,
            rights_class=rights_class,
            authority_level=authority_level,
            license_reference=license_reference,
            rights_notes=rights_notes,
            educational_resource_id=educational_resource_id,
            status="REGISTERED",
            created_by_external_identity=created_by_external_identity,
            metadata_=metadata,
        )
        self.session.add(source)
        await self.session.flush()

        # Snapshot montado ANTES do commit - ver a nota do modulo.
        snapshot = _source_snapshot(source, document_count=0)
        await self.session.commit()
        return snapshot

    async def register_document(
        self,
        source_id: UUID,
        file: ResolvedDocumentFile,
        *,
        page_offset: int = 0,
    ) -> DocumentRegistration:
        """Registra um arquivo ja validado nesta fonte.

        Idempotente por ``(source_id, document_hash)``: os mesmos bytes nunca
        produzem uma segunda linha nem uma segunda copia em disco, qualquer
        que seja o nome do arquivo ou a via de entrada. A identidade e o
        conteudo.
        """
        source = await self.session.get(KnowledgeSource, source_id)
        if source is None:
            raise KnowledgeSourceNotFound(str(source_id))

        digest = file_sha256(file.path)

        existing = await self.session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.source_id == source_id,
                KnowledgeDocument.document_hash == digest,
            )
        )
        if existing is not None:
            return DocumentRegistration(document=_document_snapshot(existing), created=False)

        # store() e enderecado por conteudo e idempotente; copy2 nunca move,
        # renomeia nem altera o arquivo de origem.
        managed_path, digest = self._storage.store(file.path, document_hash=digest)

        document = KnowledgeDocument(
            source_id=source_id,
            filename=file.original_filename,
            storage_uri=str(managed_path),
            document_hash=digest,
            file_size_bytes=file.size_bytes,
            mime_type=file.mime_type,
            page_offset=page_offset,
            # page_count e extraction_method ficam NULL de proposito: derivar
            # qualquer um dos dois exige abrir o arquivo, o que e extracao -
            # Fase 3. extraction_status nasce PENDING e ninguem o avanca aqui.
            extraction_status="PENDING",
            metadata_={"ingress": file.ingress},
        )
        self.session.add(document)
        await self.session.flush()

        snapshot = _document_snapshot(document)
        await self.session.commit()
        return DocumentRegistration(document=snapshot, created=True)

    async def archive(self, source_id: UUID) -> SourceSnapshot:
        """Marca a fonte como ``ARCHIVED``. NUNCA apaga a linha.

        Um Pack futuro cita evidencia por ``chunk_id``, e toda FK do
        subsistema e ``RESTRICT``: apagar uma fonte invalidaria
        retroativamente a procedencia de trabalho ja entregue.
        """
        source = await self.session.get(KnowledgeSource, source_id)
        if source is None:
            raise KnowledgeSourceNotFound(str(source_id))

        source.status = "ARCHIVED"
        await self.session.flush()
        count = await self._document_count(source_id)
        snapshot = _source_snapshot(source, document_count=count)
        await self.session.commit()
        return snapshot

    # -- leitura ---------------------------------------------------------

    async def get(self, source_id: UUID) -> SourceSnapshot:
        source = await self.session.get(KnowledgeSource, source_id)
        if source is None:
            raise KnowledgeSourceNotFound(str(source_id))
        return _source_snapshot(source, document_count=await self._document_count(source_id))

    async def list_sources(
        self,
        *,
        rights_class: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SourceSnapshot]:
        """Lista fontes. ``ARCHIVED`` fica fora por padrao, mas e encontravel
        pedindo ``status="ARCHIVED"`` - arquivar esconde, nao apaga."""
        query = select(KnowledgeSource)
        if rights_class is not None:
            query = query.where(KnowledgeSource.rights_class == rights_class)
        if status is not None:
            query = query.where(KnowledgeSource.status == status)
        else:
            query = query.where(KnowledgeSource.status != "ARCHIVED")
        query = query.order_by(KnowledgeSource.created_at, KnowledgeSource.id)
        query = query.limit(limit).offset(offset)

        sources = list((await self.session.scalars(query)).all())
        snapshots = []
        for source in sources:
            snapshots.append(
                _source_snapshot(source, document_count=await self._document_count(source.id))
            )
        return snapshots

    async def list_documents(self, source_id: UUID) -> list[DocumentSnapshot]:
        source = await self.session.get(KnowledgeSource, source_id)
        if source is None:
            raise KnowledgeSourceNotFound(str(source_id))
        documents = await self.session.scalars(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.source_id == source_id)
            .order_by(KnowledgeDocument.created_at, KnowledgeDocument.id)
        )
        return [_document_snapshot(document) for document in documents.all()]

    async def _document_count(self, source_id: UUID) -> int:
        return await self.session.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(KnowledgeDocument.source_id == source_id)
        ) or 0
