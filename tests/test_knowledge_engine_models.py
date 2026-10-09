"""CEREBRO / Knowledge Engine - Fase 1: os seis modelos do corpus.

Schema puro: nenhum servico, nenhuma rota, nenhum comportamento de negocio.
O que estes testes provam e que as tabelas existem, com as colunas, defaults,
FKs e unicidades que a spec descreve, e que criam em SQLite.

As travas que so o PostgreSQL aplica (CheckConstraint de direitos, indice
parcial, extensao vector) sao provadas em
``test_knowledge_engine_schema_postgresql.py``.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeChunkTerm,
    KnowledgeDocument,
    KnowledgeEmbeddingSpace,
    KnowledgeSource,
)

EXPECTED_TABLES = (
    "knowledge_sources",
    "knowledge_documents",
    "knowledge_chunks",
    "knowledge_chunk_terms",
    "knowledge_embedding_spaces",
    "knowledge_chunk_embeddings",
)


class KnowledgeEngineSchemaTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)

    def test_all_six_tables_are_created(self):
        existing = set(inspect(self.engine).get_table_names())
        for table in EXPECTED_TABLES:
            self.assertIn(table, existing)

    def test_pack_tables_are_not_part_of_phase_1(self):
        """knowledge_packs depende do contrato v1, que e Fase 8."""
        existing = set(inspect(self.engine).get_table_names())
        self.assertNotIn("knowledge_packs", existing)
        self.assertNotIn("knowledge_pack_evidences", existing)

    def test_chunk_has_no_school_id_in_the_pilot(self):
        """Acervo global: school_id entra depois, aditivo e nullable."""
        columns = {c["name"] for c in inspect(self.engine).get_columns("knowledge_sources")}
        self.assertNotIn("school_id", columns)


def _seed_source(session: Session, **overrides) -> KnowledgeSource:
    defaults = dict(
        title="Quimica na abordagem do cotidiano",
        source_kind="TEXTBOOK",
        rights_class="COMMERCIAL_REFERENCE",
        authority_level="COMMERCIAL_TEXTBOOK",
    )
    defaults.update(overrides)
    source = KnowledgeSource(**defaults)
    session.add(source)
    session.flush()
    return source


def _seed_document(session: Session, source: KnowledgeSource, **overrides) -> KnowledgeDocument:
    defaults = dict(
        source_id=source.id,
        filename="volume1.pdf",
        storage_uri="/var/material_storage/ab/abcdef/volume1.pdf",
        document_hash="a" * 64,
        extraction_status="PENDING",
    )
    defaults.update(overrides)
    document = KnowledgeDocument(**defaults)
    session.add(document)
    session.flush()
    return document


def _seed_chunk(session: Session, document: KnowledgeDocument, **overrides) -> KnowledgeChunk:
    defaults = dict(
        source_id=document.source_id,
        document_id=document.id,
        ordinal=1,
        chunk_type="PROSE",
        raw_text="Estequiometria e o estudo das relacoes quantitativas...",
        text_hash="b" * 64,
    )
    defaults.update(overrides)
    chunk = KnowledgeChunk(**defaults)
    session.add(chunk)
    session.flush()
    return chunk


class KnowledgeSourceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()

    def test_status_defaults_to_registered(self):
        source = _seed_source(self.session)
        self.assertEqual(source.status, "REGISTERED")

    def test_rights_and_authority_are_independent_axes(self):
        """Direitos (o que posso fazer) e autoridade (quanto confio) sao
        colunas separadas, e a mesma classe de direitos convive com
        autoridades diferentes."""
        book = _seed_source(self.session)  # COMMERCIAL_REFERENCE / COMMERCIAL_TEXTBOOK
        bncc = _seed_source(
            self.session,
            title="BNCC Ensino Medio",
            source_kind="CURRICULUM_FRAMEWORK",
            rights_class="OFFICIAL_PUBLIC",
            authority_level="OFFICIAL",
        )
        article = _seed_source(
            self.session,
            title="Artigo de QNEsc",
            source_kind="ARTICLE",
            rights_class="COMMERCIAL_REFERENCE",
            authority_level="ACADEMIC",
        )
        self.session.flush()
        self.assertEqual((book.rights_class, book.authority_level),
                         ("COMMERCIAL_REFERENCE", "COMMERCIAL_TEXTBOOK"))
        self.assertEqual((bncc.rights_class, bncc.authority_level),
                         ("OFFICIAL_PUBLIC", "OFFICIAL"))
        # Mesmos direitos, autoridade diferente - os eixos nao se colapsam.
        self.assertEqual(article.rights_class, book.rights_class)
        self.assertNotEqual(article.authority_level, book.authority_level)

    def test_commercial_source_may_not_point_at_an_educational_resource(self):
        """A trava topologica da spec 5.1.1, verificada em SQLite tambem."""
        with self.assertRaises(IntegrityError):
            _seed_source(self.session, educational_resource_id=uuid.uuid4())
            self.session.flush()


class KnowledgeDocumentTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()

    def test_page_offset_defaults_to_zero(self):
        source = _seed_source(self.session)
        document = _seed_document(self.session, source)
        self.assertEqual(document.page_offset, 0)

    def test_same_bytes_cannot_be_registered_twice_for_one_source(self):
        source = _seed_source(self.session)
        _seed_document(self.session, source)
        with self.assertRaises(IntegrityError):
            _seed_document(self.session, source, filename="copia.pdf")
            self.session.flush()


class KnowledgeChunkTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()

    def test_ordinal_is_unique_within_a_document(self):
        source = _seed_source(self.session)
        document = _seed_document(self.session, source)
        _seed_chunk(self.session, document, ordinal=1)
        with self.assertRaises(IntegrityError):
            _seed_chunk(self.session, document, ordinal=1, text_hash="c" * 64)
            self.session.flush()

    def test_heading_path_and_bncc_codes_round_trip_as_json(self):
        source = _seed_source(self.session)
        document = _seed_document(self.session, source)
        chunk = _seed_chunk(
            self.session,
            document,
            heading_path=["Cap. 10 - Estequiometria", "10.2 Reagente limitante"],
            bncc_node_codes=["EM13CNT101"],
        )
        self.session.commit()
        reloaded = self.session.get(KnowledgeChunk, chunk.id)
        self.assertEqual(reloaded.heading_path[1], "10.2 Reagente limitante")
        self.assertEqual(reloaded.bncc_node_codes, ["EM13CNT101"])

    def test_curriculum_node_is_optional_so_unmapped_is_representable(self):
        source = _seed_source(self.session)
        document = _seed_document(self.session, source)
        chunk = _seed_chunk(self.session, document)
        self.assertIsNone(chunk.content_node_id)


class KnowledgeChunkTermTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()

    def test_term_is_unique_per_chunk(self):
        source = _seed_source(self.session)
        document = _seed_document(self.session, source)
        chunk = _seed_chunk(self.session, document)
        self.session.add(KnowledgeChunkTerm(chunk_id=chunk.id, term="mol", term_frequency=3))
        self.session.flush()
        with self.assertRaises(IntegrityError):
            self.session.add(KnowledgeChunkTerm(chunk_id=chunk.id, term="mol", term_frequency=1))
            self.session.flush()


class KnowledgeEmbeddingSpaceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()

    def _space(self, **overrides) -> KnowledgeEmbeddingSpace:
        defaults = dict(
            provider="openai",
            model="text-embedding-3-small",
            dimensions=1536,
            status="ACTIVE",
        )
        defaults.update(overrides)
        space = KnowledgeEmbeddingSpace(**defaults)
        self.session.add(space)
        self.session.flush()
        return space

    def test_dimension_is_data_not_schema(self):
        small = self._space()
        large = self._space(model="text-embedding-3-large", dimensions=3072, status="BACKFILLING")
        self.assertEqual(small.dimensions, 1536)
        self.assertEqual(large.dimensions, 3072)

    def test_distance_metric_defaults_to_cosine(self):
        self.assertEqual(self._space().distance_metric, "cosine")

    def test_the_same_provider_model_dimension_cannot_be_registered_twice(self):
        self._space()
        with self.assertRaises(IntegrityError):
            self._space(status="RETIRED")
            self.session.flush()


class KnowledgeChunkEmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.source = _seed_source(self.session)
        self.document = _seed_document(self.session, self.source)
        self.chunk = _seed_chunk(self.session, self.document)
        self.space = KnowledgeEmbeddingSpace(
            provider="openai", model="text-embedding-3-small", dimensions=4, status="ACTIVE"
        )
        self.session.add(self.space)
        self.session.flush()

    def tearDown(self):
        self.session.close()

    def test_vector_round_trips_through_the_orm(self):
        self.session.add(
            KnowledgeChunkEmbedding(
                chunk_id=self.chunk.id,
                space_id=self.space.id,
                embedding=[0.1, -0.2, 0.3, 0.0],
                text_hash=self.chunk.text_hash,
            )
        )
        self.session.commit()
        stored = self.session.scalar(select(KnowledgeChunkEmbedding))
        self.assertEqual(stored.embedding, [0.1, -0.2, 0.3, 0.0])

    def test_is_active_defaults_to_false_so_a_backfill_never_leaks_early(self):
        embedding = KnowledgeChunkEmbedding(
            chunk_id=self.chunk.id,
            space_id=self.space.id,
            embedding=[0.1, 0.2, 0.3, 0.4],
            text_hash=self.chunk.text_hash,
        )
        self.session.add(embedding)
        self.session.flush()
        self.assertIs(embedding.is_active, False)

    def test_one_embedding_per_chunk_space_and_text(self):
        for _ in range(2):
            self.session.add(
                KnowledgeChunkEmbedding(
                    chunk_id=self.chunk.id,
                    space_id=self.space.id,
                    embedding=[0.1, 0.2, 0.3, 0.4],
                    text_hash=self.chunk.text_hash,
                )
            )
        with self.assertRaises(IntegrityError):
            self.session.flush()


if __name__ == "__main__":
    unittest.main()
