"""Orquestra extracao -> chunking -> persistencia de UM documento.

REENTRANCIA. Um documento que ja tem chunks responde ``ALREADY_CHUNKED``;
re-chunkar exige ``force``. E ``force`` e RECUSADO quando algum embedding
referencia aqueles chunks: as FKs do subsistema sao ``RESTRICT``, e esta fase
nao vai contorna-las. A alternativa - apagar chunks com embedding - destruiria
a procedencia de um Knowledge Pack ja entregue.

TEXTBOOK x CURRICULUM_FRAMEWORK. Esta fase implementa SO o caminho de prosa
e recusa ``CURRICULUM_FRAMEWORK`` com ``UNSUPPORTED_SOURCE_KIND_FOR_PHASE``.
Rodar o chunker de prosa sobre a BNCC produziria chunks plausiveis e errados:
janelas de 700 tokens cortando habilidades ao meio e perdendo o codigo
``EM13CNT301``, que e a unica chave util. Recusar e mais correto que produzir
lixo convincente.

MissingGreenlet: todo metodo publico devolve dataclass CONGELADA, montada
antes do commit - mesma disciplina da Fase 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import (
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeDocument,
    KnowledgeSource,
)
from ..authorial_material_parser import parse_authorial_pdf, parse_authorial_text
from ..ingestion_parser import DocxParser
from .bncc_extraction import BnccExtractionError, extract_bncc_cnt
from .chunking import ChunkDraft, CurriculumFrameworkChunker, ProseChunker
from .editorial_structure import classify_editorial, detect_profiles, detect_regions
from .lexical_index import LexicalIndexService
from .rights import max_excerpt_chars, may_expose_literal_text
from .extraction import DocumentExtractionError, extract_document

#: Fontes cuja estrutura documental e prosa continua. CURRICULUM_FRAMEWORK
#: NAO esta aqui de proposito - ver o docstring do modulo.
PROSE_SOURCE_KINDS = ("TEXTBOOK", "OWN_MATERIAL", "ARTICLE")


class KnowledgeDocumentNotFound(LookupError):
    """Documento inexistente, ou que nao pertence a fonte indicada."""


class KnowledgeDocumentProcessingError(ValueError):
    """A fase nao pode processar este documento. ``code`` e estavel."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ChunkSnapshot:
    """Leitura de um chunk para a API.

    NAO tem ``raw_text``. O texto literal de fonte COMMERCIAL_REFERENCE e
    conteudo interno restrito: legivel em processo pelo indexador, pelo
    embedder e pelo destilador, nunca exposto.

    ``excerpt`` e governado pela politica de direitos da Fase 2 -
    ``may_expose_literal_text`` e ``max_excerpt_chars``, que nasceram
    testadas e ociosas justamente para este momento. Comercial -> sempre
    ``None``.
    """

    id: UUID
    document_id: UUID
    ordinal: int
    chunk_type: str
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    char_count: int | None
    token_estimate: int | None
    text_hash: str
    excerpt: str | None
    metadata: dict[str, Any] | None


@dataclass(frozen=True)
class ExtractionSnapshot:
    document_id: UUID
    source_id: UUID
    extraction_status: str
    extraction_method: str | None
    extraction_error: str | None
    page_count: int | None
    chunks_created: int
    partial_reasons: tuple[str, ...]
    pages_without_text: tuple[int, ...]
    duration_seconds: float
    updated_at: datetime
    #: Resumo da estrutura normativa, so na ingestao de CURRICULUM_FRAMEWORK.
    #: Existe para que a ROTA nao precise ler KnowledgeDocument - a fronteira
    #: do spec 2 proibe modulo de fora do subsistema tocar o modelo do corpus,
    #: e o teste de fronteira pegou essa violacao quando ela foi introduzida.
    framework: dict[str, Any] | None = None


class KnowledgeDocumentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def extract_and_chunk(
        self, source_id: UUID, document_id: UUID, *, force: bool = False
    ) -> ExtractionSnapshot:
        started = datetime.now()

        source, document = await self._load(source_id, document_id)

        if source.source_kind not in PROSE_SOURCE_KINDS:
            raise KnowledgeDocumentProcessingError(
                "UNSUPPORTED_SOURCE_KIND_FOR_PHASE",
                f"source_kind {source.source_kind!r} nao e processado nesta fase; "
                f"o chunker de prosa serve {list(PROSE_SOURCE_KINDS)}. "
                "CURRICULUM_FRAMEWORK tem estrutura e funcao pedagogica distintas "
                "e ganha um chunker proprio na Fase 4",
            )

        existing = await self._chunk_count(document_id)
        if existing and not force:
            raise KnowledgeDocumentProcessingError(
                "ALREADY_CHUNKED",
                f"o documento ja tem {existing} chunks; use force para re-chunkar",
            )
        if existing and force:
            embedded = await self.session.scalar(
                select(func.count())
                .select_from(KnowledgeChunkEmbedding)
                .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeChunkEmbedding.chunk_id)
                .where(KnowledgeChunk.document_id == document_id)
            )
            if embedded:
                raise KnowledgeDocumentProcessingError(
                    "EMBEDDINGS_PRESENT",
                    f"{embedded} embeddings referenciam os chunks deste documento; "
                    "apaga-los destruiria a procedencia de qualquer Knowledge Pack "
                    "que os tenha citado",
                )
            # As FKs do subsistema sao RESTRICT: os postings do indice
            # lexical referenciam os chunks, logo apaga-los vem ANTES. Sem
            # isso o DELETE abaixo levanta IntegrityError.
            await LexicalIndexService(self.session).purge_document(document_id)
            await self.session.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
            )

        path = Path(document.storage_uri)
        page_offset = document.page_offset

        try:
            extraction = extract_document(path)
        except DocumentExtractionError as exc:
            document.extraction_status = "FAILED"
            document.extraction_error = str(exc)
            document.extraction_method = None
            snapshot = _snapshot(
                document,
                chunks_created=0,
                partial_reasons=(exc.code,),
                pages_without_text=(),
                started=started,
            )
            await self.session.commit()
            return snapshot

        parsed = _parse(path, extraction.page_texts)
        drafts = ProseChunker().chunk(
            page_texts=extraction.page_texts, parsed=parsed, page_offset=page_offset
        )

        rows = [_row(document, draft) for draft in drafts]
        # ESTRUTURA EDITORIAL (Fase 5.1b). Classifica antes de persistir, com
        # o documento inteiro em maos: regiao e perfil sao propriedades do
        # DOCUMENTO, nao do chunk, e e a regiao que pega o gabarito sem
        # marcador - 34% a 66% dos chunks do fim de cada livro.
        _classify_editorial_roles(rows, extraction.page_texts)
        self.session.add_all(rows)
        # Flush ANTES de indexar: os postings precisam do ``id`` dos chunks.
        await self.session.flush()
        # MESMA TRANSACAO que os chunks, de proposito: nao existe janela em
        # que um chunk esteja no corpus e fora do indice. Um corpus
        # parcialmente indexado produz busca que parece funcionar e esconde
        # material. Custo medido: 1,3% do tempo de extracao.
        await LexicalIndexService(self.session).index_chunks(rows)

        document.page_count = extraction.page_count
        document.extraction_method = extraction.method
        document.extraction_status = extraction.status
        document.extraction_error = None
        document.metadata_ = {
            **(document.metadata_ or {}),
            "extraction": {
                "method": extraction.method,
                "page_count": extraction.page_count,
                # Registrado SEMPRE, inclusive em EXTRACTED: pagina vazia nao
                # e, por si, perda, mas saber onde elas estao e util.
                "pages_without_text": extraction.pages_without_text,
                "partial_reasons": list(extraction.reasons),
                "chunks_created": len(drafts),
            },
        }
        await self.session.flush()

        snapshot = _snapshot(
            document,
            chunks_created=len(drafts),
            partial_reasons=extraction.reasons,
            pages_without_text=tuple(extraction.pages_without_text),
            started=started,
        )
        await self.session.commit()
        return snapshot

    async def extract_framework(
        self, source_id: UUID, document_id: UUID, *, taxonomy_version: str, force: bool = False
    ) -> ExtractionSnapshot:
        """Ingestao de documento NORMATIVO (Fase 4, spec 22.1).

        Caminho separado do ``extract_and_chunk`` de proposito: a BNCC nao e
        prosa, e tratar as duas pelo mesmo metodo com um `if` dentro
        esconderia justamente a diferenca que importa.

        NAO semeia taxonomia. Semear e mudanca de CURRICULO, feita por
        ``services/bncc_taxonomy_seed.py`` com script e revisao proprios -
        nunca efeito colateral de uma ingestao de documento.
        """
        started = datetime.now()
        source, document = await self._load(source_id, document_id)

        if source.source_kind != "CURRICULUM_FRAMEWORK":
            raise KnowledgeDocumentProcessingError(
                "NOT_A_CURRICULUM_FRAMEWORK",
                f"source_kind {source.source_kind!r} nao e documento normativo; "
                "use o caminho de prosa",
            )

        existing = await self._chunk_count(document_id)
        if existing and not force:
            raise KnowledgeDocumentProcessingError(
                "ALREADY_CHUNKED",
                f"o documento ja tem {existing} chunks; use force para reprocessar",
            )
        if existing and force:
            embedded = await self.session.scalar(
                select(func.count())
                .select_from(KnowledgeChunkEmbedding)
                .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeChunkEmbedding.chunk_id)
                .where(KnowledgeChunk.document_id == document_id)
            )
            if embedded:
                raise KnowledgeDocumentProcessingError(
                    "EMBEDDINGS_PRESENT",
                    f"{embedded} embeddings referenciam os chunks deste documento",
                )
            await LexicalIndexService(self.session).purge_document(document_id)
            await self.session.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
            )

        path = Path(document.storage_uri)
        try:
            extraction = extract_document(path)
            framework = extract_bncc_cnt(
                extraction.page_texts, taxonomy_version=taxonomy_version
            )
        except (DocumentExtractionError, BnccExtractionError) as exc:
            document.extraction_status = "FAILED"
            document.extraction_error = f"{getattr(exc, 'code', 'ERROR')}: {exc}"
            document.extraction_method = None
            snapshot = _snapshot(
                document,
                chunks_created=0,
                partial_reasons=(getattr(exc, "code", "ERROR"),),
                pages_without_text=(),
                started=started,
            )
            await self.session.commit()
            return snapshot

        drafts = CurriculumFrameworkChunker().chunk(
            framework=framework, page_offset=document.page_offset
        )
        rows = []
        for draft in drafts:
            row = _row(document, draft)
            # A coluna existe desde a Fase 1 e e o filtro rapido de
            # recuperacao; a tripla normativa completa vive em metadata.
            row.bncc_node_codes = draft.metadata.get("bncc_node_codes")
            rows.append(row)
        _classify_editorial_roles(rows, extraction.page_texts)
        self.session.add_all(rows)
        await self.session.flush()
        # A norma tambem e indexada: sem isto a BNCC ficaria inbuscavel, e o
        # codigo da habilidade e a sua unica chave util.
        await LexicalIndexService(self.session).index_chunks(rows)

        document.page_count = extraction.page_count
        document.extraction_method = extraction.method
        document.extraction_status = extraction.status
        document.extraction_error = None
        document.metadata_ = {
            **(document.metadata_ or {}),
            "extraction": {
                "method": extraction.method,
                "page_count": extraction.page_count,
                "pages_without_text": extraction.pages_without_text,
                "partial_reasons": list(extraction.reasons),
                "chunks_created": len(drafts),
            },
            "framework": {
                "taxonomy_code": framework.taxonomy_code,
                "taxonomy_version": framework.taxonomy_version,
                "area_code": framework.area_code,
                "competencies": len(framework.competencies),
                "skills": len(framework.skills),
                "skills_per_competency": [
                    len(c.skills) for c in framework.competencies
                ],
                "extractor_version": framework.extractor_version,
            },
        }
        await self.session.flush()
        snapshot = _snapshot(
            document,
            chunks_created=len(drafts),
            partial_reasons=extraction.reasons,
            pages_without_text=tuple(extraction.pages_without_text),
            started=started,
            framework=document.metadata_["framework"],
        )
        await self.session.commit()
        return snapshot

    async def list_chunks(
        self,
        source_id: UUID,
        document_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChunkSnapshot]:
        source, _ = await self._load(source_id, document_id)
        quotable = may_expose_literal_text(source.rights_class)
        limit_chars = max_excerpt_chars(source.rights_class)

        chunks = await self.session.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document_id)
            .order_by(KnowledgeChunk.ordinal)
            .limit(limit)
            .offset(offset)
        )
        snapshots = []
        for chunk in chunks.all():
            excerpt = None
            if quotable and limit_chars:
                excerpt = (chunk.raw_text or "")[:limit_chars]
            snapshots.append(
                ChunkSnapshot(
                    id=chunk.id,
                    document_id=chunk.document_id,
                    ordinal=chunk.ordinal,
                    chunk_type=chunk.chunk_type,
                    heading_path=list(chunk.heading_path or []),
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    char_count=chunk.char_count,
                    token_estimate=chunk.token_estimate,
                    text_hash=chunk.text_hash,
                    excerpt=excerpt,
                    metadata=chunk.metadata_,
                )
            )
        return snapshots

    async def chunk_stats(self, source_id: UUID) -> dict[str, Any]:
        """Distribuicao por tipo e cobertura de paginas.

        Existe para que falso positivo estrutural seja OBSERVAVEL (spec 20.4):
        sem isso, "o chunker as vezes erra" viraria folclore em vez de numero.
        """
        rows = await self.session.execute(
            select(KnowledgeChunk.chunk_type, func.count())
            .where(KnowledgeChunk.source_id == source_id)
            .group_by(KnowledgeChunk.chunk_type)
        )
        by_type = {chunk_type: count for chunk_type, count in rows.all()}
        total = sum(by_type.values())
        without_page = await self.session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.source_id == source_id,
                KnowledgeChunk.page_start.is_(None),
            )
        )
        pages = await self.session.execute(
            select(func.min(KnowledgeChunk.page_start), func.max(KnowledgeChunk.page_end)).where(
                KnowledgeChunk.source_id == source_id
            )
        )
        page_min, page_max = pages.one()
        return {
            "total_chunks": total,
            "by_chunk_type": by_type,
            "chunks_without_page": without_page or 0,
            "page_start_min": page_min,
            "page_end_max": page_max,
        }

    # -- internos --------------------------------------------------------

    async def _load(
        self, source_id: UUID, document_id: UUID
    ) -> tuple[KnowledgeSource, KnowledgeDocument]:
        document = await self.session.get(KnowledgeDocument, document_id)
        if document is None or document.source_id != source_id:
            raise KnowledgeDocumentNotFound(str(document_id))
        source = await self.session.get(KnowledgeSource, source_id)
        if source is None:
            raise KnowledgeDocumentNotFound(str(source_id))
        return source, document

    async def _chunk_count(self, document_id: UUID) -> int:
        return await self.session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document_id)
        ) or 0


def _parse(path: Path, page_texts: list[str]):
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        # page_texts ja em maos: evita uma segunda leitura do arquivo e
        # garante que o esqueleto venha do MESMO texto que a substancia.
        return parse_authorial_pdf(path, page_texts=page_texts)
    if suffix == ".docx":
        return DocxParser.parse_file(path)
    return parse_authorial_text(path)


def _classify_editorial_roles(
    rows: list[KnowledgeChunk], page_texts: list[str]
) -> None:
    """Atribui ``editorial_role`` a cada chunk, com o documento em maos.

    Regiao e perfil sao propriedades do DOCUMENTO: a regiao do manual docente
    e contigua (medido: p477-543, p467-543, p465-541 nas tres obras do
    piloto), e os perfis sao ativados por evidencia observada no proprio
    arquivo, nunca por nome de editora.

    ``editorial_detector_version`` e gravado SEMPRE que a classificacao roda -
    e isso que distingue "classificado como UNKNOWN por evidencia
    insuficiente" de "nao processado por esta versao".
    """
    if not rows:
        return
    profiles = detect_profiles(page_texts)
    regions = detect_regions(page_texts)
    total = len(page_texts)
    for row in rows:
        verdict = classify_editorial(
            row.raw_text,
            heading_path=row.heading_path or (),
            page=row.page_start,
            page_count=total,
            regions=regions,
            profiles=profiles,
            chunk_type=row.chunk_type,
        )
        row.editorial_role = verdict.role
        row.editorial_role_confidence = verdict.confidence
        row.editorial_detector_version = verdict.detector_version
        row.metadata_ = {**(row.metadata_ or {}), "editorial": verdict.as_metadata()}


def _row(document: KnowledgeDocument, draft: ChunkDraft) -> KnowledgeChunk:
    return KnowledgeChunk(
        source_id=document.source_id,
        document_id=document.id,
        ordinal=draft.ordinal,
        chunk_type=draft.chunk_type,
        heading_path=list(draft.heading_path),
        page_start=draft.page_start,
        page_end=draft.page_end,
        raw_text=draft.raw_text,
        text_hash=draft.text_hash,
        char_count=draft.char_count,
        token_estimate=draft.token_estimate,
        # content_node_id fica NULL nesta fase: casar chunk com curriculo e
        # trabalho do matcher (Fase 4), e misturar as duas coisas tornaria
        # ambas mais dificeis de testar.
        metadata_=draft.metadata,
    )


def _snapshot(
    document: KnowledgeDocument,
    *,
    chunks_created: int,
    partial_reasons: tuple[str, ...],
    pages_without_text: tuple[int, ...],
    started: datetime,
    framework: dict[str, Any] | None = None,
) -> ExtractionSnapshot:
    return ExtractionSnapshot(
        document_id=document.id,
        source_id=document.source_id,
        extraction_status=document.extraction_status,
        extraction_method=document.extraction_method,
        extraction_error=document.extraction_error,
        page_count=document.page_count,
        chunks_created=chunks_created,
        partial_reasons=tuple(partial_reasons),
        pages_without_text=pages_without_text,
        duration_seconds=round((datetime.now() - started).total_seconds(), 3),
        updated_at=document.updated_at,
        framework=framework,
    )
