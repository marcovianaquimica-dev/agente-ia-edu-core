"""CEREBRO / Knowledge Engine - corpus de conhecimento (Fase 1).

Corpus PARALELO ao material publicavel para aluno, nao uma extensao dele. O
pipeline PHASE 26 (``authorial_material_ingestion.py``) tem como estado
terminal ``publish()`` -> ``TheoryMaterial`` -> ``MaterialAssignment`` ->
aluno. Uma fonte ``COMMERCIAL_REFERENCE`` nao pode nunca alcancar esse
estado, e aqui isso e impossivel por CONSTRUCAO, nao por lembrar de checar:
o CheckConstraint ``ck_knowledge_sources_commercial_has_no_resource`` impede
que ela exista como ``EducationalResource``, e sem isso ela nao pode ser
alvo de ``ContentResourceLink``, nem virar ``TheoryMaterial``, nem ser
distribuida.

Nada aqui e comportamento. Sao seis tabelas, suas colunas, seus defaults e
suas travas. Servicos, recuperacao, Knowledge Pack e endpoints vem depois.

Acervo GLOBAL no piloto: nenhuma tabela tem ``school_id``. A coluna entra
depois, aditiva e nullable, como ``TheoryMaterial`` ja faz.

Ver ``docs/superpowers/specs/2026-09-30-cerebro-knowledge-engine-design.md``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    DDL,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible, VectorCompatible

# ``knowledge_chunk_embeddings.embedding`` compila para o tipo ``vector``, que
# nao existe num banco PostgreSQL sem a extensao pgvector. Sem isto, TODO
# ``Base.metadata.create_all`` contra um Postgres novo passa a falhar com
# ``type "vector" does not exist`` - inclusive o de dezenas de testes
# ``*_postgresql`` que nada tem a ver com o Knowledge Engine e que criam um
# banco descartavel proprio.
#
# Resolver isso editando cada teste seria errado duas vezes: nao conserta os
# testes que ainda serao escritos, e espalha uma preocupacao do Knowledge
# Engine por arquivos que nao deveriam conhece-la. O listener abaixo faz a
# extensao ser garantida uma vez, no lugar unico que sabe que ela e
# necessaria - a mesma coisa que a migracao 058 faz para o caminho Alembic.
#
# ``IF NOT EXISTS`` torna a operacao idempotente e no-op onde ja esta
# instalada. Exige privilegio para criar extensao; em producao o caminho e
# Alembic, nao ``create_all``.
event.listen(
    Base.metadata,
    "before_create",
    DDL("CREATE EXTENSION IF NOT EXISTS vector").execute_if(dialect="postgresql"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeSource(Base):
    """Uma obra cadastrada no corpus: um livro, a BNCC, uma apostila propria.

    Dois eixos DELIBERADAMENTE separados, que nunca devem ser colapsados num
    campo so:

    - ``rights_class``  - o que podemos FAZER com a fonte. E politica, entao
      tem CheckConstraint.
    - ``authority_level`` - quanto CONFIAMOS na fonte. E vocabulario que ainda
      vai crescer, entao e string livre, como ``section_type`` e
      ``block_type`` ja sao em ``catalog.py``.

    Sao independentes: uma apostila propria (``OWN``/``OWN``) pode ter menos
    autoridade que um livro comercial cujo texto nao podemos reproduzir; a
    BNCC e ``OFFICIAL_PUBLIC``/``OFFICIAL`` e pode ser citada literalmente.
    """

    __tablename__ = "knowledge_sources"
    __table_args__ = (
        CheckConstraint(
            "rights_class IN ('COMMERCIAL_REFERENCE', 'LICENSED', 'OWN', "
            "'PUBLIC_DOMAIN', 'OFFICIAL_PUBLIC')",
            name="ck_knowledge_sources_rights_class",
        ),
        CheckConstraint(
            "status IN ('REGISTERED', 'EXTRACTING', 'EXTRACTED', 'CHUNKED', "
            "'EMBEDDED', 'READY', 'FAILED', 'ARCHIVED')",
            name="ck_knowledge_sources_status",
        ),
        # A trava topologica. Sem EducationalResource, uma fonte comercial nao
        # tem caminho algum ate o aluno - nao e um `if` num servico.
        CheckConstraint(
            "rights_class <> 'COMMERCIAL_REFERENCE' OR educational_resource_id IS NULL",
            name="ck_knowledge_sources_commercial_has_no_resource",
        ),
        Index("ix_knowledge_sources_rights_class", "rights_class"),
        Index("ix_knowledge_sources_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    authors: Mapped[str | None] = mapped_column(String(500))
    publisher: Mapped[str | None] = mapped_column(String(255))
    edition: Mapped[str | None] = mapped_column(String(100))
    publication_year: Mapped[int | None] = mapped_column(Integer)
    isbn: Mapped[str | None] = mapped_column(String(20))

    # TEXTBOOK | CURRICULUM_FRAMEWORK | OWN_MATERIAL | ARTICLE | OTHER
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    rights_class: Mapped[str] = mapped_column(String(30), nullable=False)
    # OFFICIAL | ACADEMIC | COMMERCIAL_TEXTBOOK | OWN | OTHER
    authority_level: Mapped[str] = mapped_column(String(30), nullable=False)
    # Reservado: o ranking do MVP nao le esta coluna (todos os pesos sao 1.0).
    source_quality_score: Mapped[float | None] = mapped_column(Numeric(4, 3))

    license_reference: Mapped[str | None] = mapped_column(Text)
    rights_notes: Mapped[str | None] = mapped_column(Text)

    educational_resource_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("educational_resources.id", ondelete="RESTRICT")
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="REGISTERED")
    created_by_external_identity: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    documents: Mapped[list[KnowledgeDocument]] = relationship(back_populates="source")


class KnowledgeDocument(Base):
    """Um arquivo de uma fonte. Uma obra pode ter varios (volumes, capitulos).

    ``page_offset`` e o que faz a rastreabilidade apontar para a pagina
    IMPRESSA da obra e nao para a pagina do PDF: um recorte de capitulo que
    comeca na pagina 312 do livro tem offset 311. Sem isso, toda citacao de um
    recorte sai errada.
    """

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("source_id", "document_hash", name="uq_knowledge_documents_source_hash"),
        CheckConstraint(
            "extraction_method IS NULL OR extraction_method IN "
            "('PDF_TEXT_LAYER', 'PDF_TEXT_LAYER_PYMUPDF', 'VISION_OCR', 'DOCX', 'MARKDOWN')",
            name="ck_knowledge_documents_extraction_method",
        ),
        # PARTIAL (migracao 059) e um estado proprio, nao um EXTRACTED
        # otimista nem um FAILED pessimista: significa que houve perda
        # POTENCIALMENTE RELEVANTE de conteudo na extracao, por criterios
        # deterministas descritos na secao 20.3 da spec. Pagina vazia nao e,
        # por si, perda - paginas podem ser intencionalmente vazias ou
        # predominantemente visuais.
        CheckConstraint(
            "extraction_status IN ('PENDING', 'EXTRACTING', 'EXTRACTED', "
            "'PARTIAL', 'FAILED')",
            name="ck_knowledge_documents_extraction_status",
        ),
        Index("ix_knowledge_documents_source_id", "source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_sources.id", ondelete="RESTRICT"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    document_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    page_count: Mapped[int | None] = mapped_column(Integer)
    page_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    extraction_method: Mapped[str | None] = mapped_column(String(30))
    extraction_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    extraction_error: Mapped[str | None] = mapped_column(Text)

    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    source: Mapped[KnowledgeSource] = relationship(back_populates="documents")
    chunks: Mapped[list[KnowledgeChunk]] = relationship(back_populates="document")


class KnowledgeChunk(Base):
    """A unidade de RECUPERACAO - deliberadamente distinta da unidade de
    LEITURA (``MaterialSection``, dimensionada para o Material Player).

    ``raw_text`` de uma fonte ``COMMERCIAL_REFERENCE`` e conteudo interno
    restrito: legivel em processo pelo indexador, pelo embedder e pelo
    destilador, nunca exposto por endpoint nem reproduzido na saida. Nenhum
    schema de resposta de fonte comercial tem este campo.
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_knowledge_chunks_document_ordinal"),
        CheckConstraint("ordinal > 0", name="ck_knowledge_chunks_ordinal_positive"),
        CheckConstraint(
            "curriculum_match_source IS NULL OR curriculum_match_source IN "
            "('DETERMINISTIC', 'MANUAL', 'INHERITED')",
            name="ck_knowledge_chunks_curriculum_match_source",
        ),
        Index("ix_knowledge_chunks_source_id", "source_id"),
        Index("ix_knowledge_chunks_document_ordinal", "document_id", "ordinal"),
        Index("ix_knowledge_chunks_content_node_id", "content_node_id"),
        Index("ix_knowledge_chunks_text_hash", "text_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Denormalizado: permite filtrar por direitos sem join com documents.
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_sources.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_documents.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    # PROSE | DEFINITION | WORKED_EXAMPLE | TABLE | FORMULA | EXERCISE |
    # SUMMARY | CURRICULUM_ITEM - livre, para nao exigir migracao por tipo novo.
    chunk_type: Mapped[str] = mapped_column(String(30), nullable=False)
    heading_path: Mapped[list[str] | None] = mapped_column(JSONBCompatible)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)

    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int | None] = mapped_column(Integer)
    token_estimate: Mapped[int | None] = mapped_column(Integer)

    # NULL == nao mapeado. O Knowledge Engine NUNCA cria no de curriculo.
    content_node_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("catalog_nodes.id", ondelete="SET NULL")
    )
    curriculum_match_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    curriculum_match_source: Mapped[str | None] = mapped_column(String(30))
    # Preenchido apenas por associacao VALIDADA. A relacao CatalogNode <-> BNCC
    # nao e inferida por similaridade textual (Fase 0 mostrou que produziria
    # ruido); sem curadoria, fica vazio - resultado honesto, nao falha.
    bncc_node_codes: Mapped[list[str] | None] = mapped_column(JSONBCompatible)

    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class KnowledgeChunkTerm(Base):
    """Indice invertido proprio - a perna lexical da busca hibrida.

    Existe como tabela, em vez de depender de ``tsvector``, porque assim a
    mesma consulta BM25 roda identica em PostgreSQL e SQLite: uma
    implementacao so, testavel sem Postgres. O document frequency sai de uma
    CTE no momento da busca - nao ha tabela de estatisticas para sair de
    sincronia.
    """

    __tablename__ = "knowledge_chunk_terms"
    __table_args__ = (
        CheckConstraint("term_frequency > 0", name="ck_knowledge_chunk_terms_frequency_positive"),
        Index("ix_knowledge_chunk_terms_term", "term"),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_chunks.id", ondelete="RESTRICT"), primary_key=True
    )
    term: Mapped[str] = mapped_column(String(80), primary_key=True)
    term_frequency: Mapped[int] = mapped_column(Integer, nullable=False)


class KnowledgeEmbeddingSpace(Base):
    """Uma geracao de embedding: provider + modelo + dimensao + metrica.

    Existe para que 1536 NAO seja acoplamento permanente. A dimensao e DADO
    desta tabela, nunca DDL: a coluna ``embedding`` e declarada como ``vector``
    sem tamanho, e o indice ANN e criado por espaco, como indice parcial com
    cast explicito. Introduzir um espaco de 1024 dimensoes depois e inserir uma
    linha e criar um indice - zero migracao de schema, zero migracao de dados,
    nenhuma releitura do corpus.
    """

    __tablename__ = "knowledge_embedding_spaces"
    __table_args__ = (
        UniqueConstraint(
            "provider", "model", "dimensions", name="uq_knowledge_embedding_spaces_identity"
        ),
        CheckConstraint("dimensions > 0", name="ck_knowledge_embedding_spaces_dimensions_positive"),
        CheckConstraint(
            "status IN ('ACTIVE', 'BACKFILLING', 'RETIRED')",
            name="ck_knowledge_embedding_spaces_status",
        ),
        CheckConstraint(
            "distance_metric IN ('cosine', 'l2', 'inner_product')",
            name="ck_knowledge_embedding_spaces_distance_metric",
        ),
        # No maximo um espaco ACTIVE. Indice parcial: o mesmo recurso que
        # MaterialAssignment ja usa para "um ativo por alvo".
        Index(
            "uq_knowledge_embedding_spaces_single_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_metric: Mapped[str] = mapped_column(String(20), nullable=False, default="cosine")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="BACKFILLING")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeChunkEmbedding(Base):
    """O vetor de um chunk num espaco de embedding.

    Separado do chunk DE PROPOSITO: trocar de modelo e INSERT, nunca UPDATE.
    O corpus (chunks, termos, curriculo, direitos) sobrevive intacto a uma
    troca de provider.

    ``is_active`` e a verdade por linha, e e o UNICO filtro da busca.
    ``KnowledgeEmbeddingSpace.status`` descreve o ciclo de vida do espaco. Um
    espaco recem-ativado cujas linhas ainda nao foram viradas simplesmente nao
    aparece na busca - falha fechada, nunca meio-ativa. O default False e o
    que garante isso.

    A dimensao do vetor NAO e validada pelo banco (a coluna e ``vector`` sem
    tamanho, por desenho). A validacao contra ``space.dimensions`` vive no
    servico de indexacao, na Fase 6.
    """

    __tablename__ = "knowledge_chunk_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "chunk_id", "space_id", "text_hash", name="uq_knowledge_chunk_embeddings_identity"
        ),
        Index(
            "ix_knowledge_chunk_embeddings_active",
            "space_id",
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active"),
        ),
        Index("ix_knowledge_chunk_embeddings_chunk_id", "chunk_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_chunks.id", ondelete="RESTRICT"), nullable=False
    )
    space_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_embedding_spaces.id", ondelete="RESTRICT"), nullable=False
    )
    embedding: Mapped[list[float] | None] = mapped_column(VectorCompatible)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
