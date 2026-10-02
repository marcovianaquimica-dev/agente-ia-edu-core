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
    BigInteger,
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
        Index("ix_knowledge_chunks_editorial_role", "editorial_role"),
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

    # -- estrutura editorial (Fase 5.1b) --------------------------------
    #
    # ORTOGONAL a ``chunk_type``, e a distincao nao e sutil:
    #
    #     chunk_type      forma e funcao pedagogica LOCAL
    #     editorial_role  funcao EDITORIAL na obra
    #
    # Um gabarito pode ser PROSE, EXERCISE ou SOLUTION - e e ANSWER_KEY nos
    # tres casos. SOLUTION + ANSWER_KEY e combinacao legitima e esperada.
    #
    # String livre, como ``chunk_type``: papel novo nao exige migracao.
    editorial_role: Mapped[str] = mapped_column(
        String(30), nullable=False, default="UNKNOWN"
    )
    editorial_role_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    #: NULO significa NAO PROCESSADO por versao alguma do detector. Com versao
    #: preenchida e ``editorial_role = 'UNKNOWN'``, significa CLASSIFICADO e a
    #: evidencia nao bastou. Sao estados diferentes, e ``UNKNOWN`` nao pode
    #: esconder ausencia de processamento - era exatamente o risco apontado.
    editorial_detector_version: Mapped[str | None] = mapped_column(String(20))

    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class KnowledgeChunkTerm(Base):
    """Indice invertido proprio - a perna lexical da busca hibrida.

    Existe como tabela, em vez de depender de ``tsvector``, porque assim a
    mesma consulta BM25 roda identica em PostgreSQL e SQLite: uma
    implementacao so, testavel sem Postgres. O document frequency sai de
    agregacao no momento da busca - nao ha tabela de estatistica GLOBAL para
    sair de sincronia.

    O backend NAO e permanente (spec 23.1). PostgreSQL FTS ou OpenSearch
    poderao substitui-lo sem alterar consumidor algum, porque o contrato
    publico e ``LexicalSearcher.search()``, nao esta tabela.

    CORPO E TITULO SAO CAMPOS SEPARADOS. ``term_frequency`` conta o
    ``raw_text`` da obra; ``heading_frequency`` conta o ``heading_path``, que o
    SISTEMA acrescentou. Indexar a concatenacao (``retrieval_text``) tornaria
    impossivel pesar titulo, explicar score e diagnosticar o ``Chapter N`` que
    e 70,5% dos headings reais.

    ``positions`` guarda a posicao no fluxo normalizado INTEGRO: stopword nao
    gera posting, mas ocupa posicao. "concentracao das solucoes" grava
    ``concentracao@0`` e ``solucao@2``, nunca @0/@1. O indice preserva a
    lacuna; a politica (``phrase_slack``) decide como trata-la.
    """

    __tablename__ = "knowledge_chunk_terms"
    __table_args__ = (
        # Um termo que aparece SO no titulo precisa ser indexavel - era isso
        # que o antigo ``term_frequency > 0`` proibia.
        CheckConstraint(
            "term_frequency >= 0 AND heading_frequency >= 0 "
            "AND term_frequency + heading_frequency > 0",
            name="ck_knowledge_chunk_terms_any_frequency",
        ),
        Index("ix_knowledge_chunk_terms_term", "term"),
        # (term, chunk_id) permite index-only scan no PostgreSQL para a
        # consulta que importa: "quais chunks tem estes termos".
        Index("ix_knowledge_chunk_terms_term_chunk", "term", "chunk_id"),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_chunks.id", ondelete="RESTRICT"), primary_key=True
    )
    term: Mapped[str] = mapped_column(String(80), primary_key=True)
    term_frequency: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    heading_frequency: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    positions: Mapped[list[int] | None] = mapped_column(JSONBCompatible)


class KnowledgeChunkLexicalIndex(Base):
    """Estado da indexacao lexical de UM chunk.

    Guarda o que a busca precisa saber sobre o documento (``token_count``, o
    ``dl`` do BM25) e o que a OPERACAO precisa saber sobre o indice:

    - ``text_hash`` - o texto que foi indexado. Divergir do
      ``KnowledgeChunk.text_hash`` significa indice OBSOLETO, e isso passa a
      ser respondivel por SQL em vez de por fe.
    - ``normalizer_version`` - as regras que produziram os termos. Trocar a
      normalizacao sem reindexar deixa um indice misto, e isso fica visivel.
    - ``generation`` - em que geracao este chunk foi indexado.

    Isto e estatistica POR CHUNK, escrita na mesma transacao dos postings.
    A estatistica GLOBAL (``df``, ``N``, ``avgdl``) continua saindo de
    agregacao na hora da busca, exatamente como a Fase 1 previu: o que
    poderia derivar nao foi materializado.
    """

    __tablename__ = "knowledge_chunk_lexical_index"
    __table_args__ = (
        CheckConstraint(
            "token_count >= 0 AND heading_token_count >= 0",
            name="ck_knowledge_chunk_lexical_index_counts_not_negative",
        ),
        Index("ix_knowledge_chunk_lexical_index_generation", "generation"),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_chunks.id", ondelete="RESTRICT"), primary_key=True
    )
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(20), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class KnowledgeLexicalIndexState(Base):
    """A GERACAO do indice lexical.

    Existe para que "os postings mudaram entre a pagina 1 e a pagina 2 da
    mesma consulta?" seja uma pergunta respondivel. ``generation`` entra no
    ``query_fingerprint``, junto de consulta, filtros, versao da politica e
    versao do normalizador: duas paginas com fingerprints diferentes nao
    pertencem a mesma foto do corpus, e isso aparece em vez de produzir uma
    paginacao silenciosamente incoerente.

    Toda escrita no indice incrementa a geracao, na MESMA transacao.

    ``scope`` e ``'GLOBAL'`` no piloto - acervo global, sem ``school_id``.
    A coluna existe para que um acervo por escola entre depois como linha
    nova, nao como migracao de chave primaria.
    """

    __tablename__ = "knowledge_lexical_index_state"
    __table_args__ = (
        CheckConstraint("generation > 0", name="ck_knowledge_lexical_index_state_generation"),
    )

    scope: Mapped[str] = mapped_column(String(40), primary_key=True, default="GLOBAL")
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    normalizer_version: Mapped[str] = mapped_column(String(20), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Ultima operacao que moveu a geracao: INDEX_DOCUMENT | REINDEX_DOCUMENT |
    #: PURGE_DOCUMENT. Diagnostico, nao controle de fluxo.
    last_operation: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


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
