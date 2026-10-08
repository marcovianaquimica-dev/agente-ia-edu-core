"""CEREBRO - Fase 5: o que so o PostgreSQL real prova, e a comparacao de dialetos.

Tres coisas que o SQLite nao consegue demonstrar:

1. **``positions`` e um array JSONB de verdade**, nao uma string. A licao da
   Fase 1 foi caríssima: ``VectorCompatible`` com ``impl = JSON`` gravava
   ``"[0.5,-1.25]"`` com aspas e corromperia todo vetor silenciosamente. Aqui
   a verificacao e feita com operador JSONB nativo - se a serializacao
   estivesse dupla, ``jsonb_array_length`` falharia.
2. **Os CheckConstraints recusam** o que o service nunca enviaria, com o
   service CONTORNADO por INSERT direto.
3. **As FKs sao RESTRICT**: apagar chunk com posting falha, o que e
   exatamente por que a ordem de purga em ``documents.py`` importa.

E o criterio de aceite mais forte da fase: **o MESMO corpus, a MESMA consulta,
e ranking identico em SQLite e PostgreSQL**, com score igual a 9 casas
decimais. E a prova de que existe UMA implementacao, nao duas que concordam
por sorte. Os UUIDs sao deterministicos (uuid5) para que a comparacao possa
ser por identidade, e nao por posicao.

Banco DESCARTAVEL proprio. Nao toca o banco de desenvolvimento.
"""

from __future__ import annotations

import os
import unittest
import uuid

from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
import agente_ia_edu.db.models  # noqa: F401
from agente_ia_edu.db.models import (
    KnowledgeChunk,
    KnowledgeChunkTerm,
    KnowledgeDocument,
    KnowledgeLexicalIndexState,
    KnowledgeSource,
)
from agente_ia_edu.knowledge_retrieval_policy.evaluation_sets import (
    CALIBRATION_SET_V1,
    EVALUATION_SET_V1,
)
from agente_ia_edu.services.knowledge_engine.lexical_index import LexicalIndexService
from agente_ia_edu.services.knowledge_engine.lexical_search import LexicalSearcher

_NAMESPACE = uuid.UUID("5b5a1b1e-0000-4000-8000-000000000000")

#: Corpus fixo e deliberadamente variado: expressao exata, termos espalhados,
#: titulo com boilerplate, gabarito, norma com codigo, fonte comercial e
#: fonte oficial. Mesmos bytes nos dois dialetos.
_CORPUS = [
    (
        "comercial",
        "PROSE",
        ["Chapter 9 - Solucoes"],
        312,
        "A diluicao reduz a concentracao das solucoes aquosas preparadas em "
        "laboratorio, e o calculo usa o numero de mol do soluto.",
        None,
    ),
    (
        "comercial",
        "PROSE",
        ["Chapter 5"],
        94,
        "A estequiometria relaciona as quantidades de reagentes e produtos. O "
        "reagente limitante determina o rendimento da reacao.",
        None,
    ),
    (
        "comercial",
        "PROSE",
        ["Chapter 5"],
        95,
        "Em muitas reacoes o reagente aparece em excesso, e o fator limitante "
        "do processo industrial e outro, de natureza economica.",
        None,
    ),
    (
        "comercial",
        "SOLUTION",
        ["Chapter 9"],
        500,
        "Resposta: a concentracao final das solucoes e de 0,2 mol por litro.",
        None,
    ),
    (
        "propria",
        "DEFINITION",
        ["Apostila - Quantidade de materia"],
        12,
        "Mol e a unidade de quantidade de materia do Sistema Internacional.",
        None,
    ),
    (
        "bncc",
        "CURRICULUM_ITEM",
        ["Ciencias da Natureza e suas Tecnologias", "Competencia especifica 1"],
        117,
        "Analisar e representar as transformacoes e conservacoes em sistemas "
        "que envolvam quantidade de materia e energia.",
        ["EM13CNT101"],
    ),
]
_SOURCES = {
    "comercial": ("Quimica na abordagem do cotidiano", "TEXTBOOK", "COMMERCIAL_REFERENCE", "COMMERCIAL_TEXTBOOK"),
    "propria": ("Apostila propria", "OWN_MATERIAL", "OWN", "OWN"),
    "bncc": ("BNCC Ensino Medio", "CURRICULUM_FRAMEWORK", "OFFICIAL_PUBLIC", "OFFICIAL"),
}


def _identifier(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{kind}:{key}")


async def _seed(factory) -> None:
    """Semeia o corpus fixo com ids DETERMINISTICOS, em qualquer dialeto."""
    async with factory() as session:
        for key, (title, kind, rights, authority) in _SOURCES.items():
            session.add(
                KnowledgeSource(
                    id=_identifier("source", key),
                    title=title,
                    source_kind=kind,
                    rights_class=rights,
                    authority_level=authority,
                )
            )
            session.add(
                KnowledgeDocument(
                    id=_identifier("document", key),
                    source_id=_identifier("source", key),
                    filename=f"{key}.pdf",
                    storage_uri=f"/tmp/{key}.pdf",
                    document_hash=key.ljust(64, "x")[:64],
                )
            )
        await session.flush()

        rows = []
        for ordinal, (key, chunk_type, heading, page, raw, codes) in enumerate(
            _CORPUS, start=1
        ):
            rows.append(
                KnowledgeChunk(
                    id=_identifier("chunk", str(ordinal)),
                    source_id=_identifier("source", key),
                    document_id=_identifier("document", key),
                    ordinal=ordinal,
                    chunk_type=chunk_type,
                    heading_path=heading,
                    page_start=page,
                    page_end=page,
                    raw_text=raw,
                    text_hash=f"{ordinal:064d}",
                    char_count=len(raw),
                    bncc_node_codes=codes,
                )
            )
        session.add_all(rows)
        await session.flush()
        await LexicalIndexService(session).index_chunks(rows)
        await session.commit()


class LexicalPostgreSQL(unittest.IsolatedAsyncioTestCase):
    database_name = "agente_ia_edu_lexical_test"
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
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=True
        )
        async with self.factory() as session:
            for table in (
                "knowledge_chunk_terms",
                "knowledge_chunk_lexical_index",
                "knowledge_lexical_index_state",
                "knowledge_chunks",
                "knowledge_documents",
                "knowledge_sources",
            ):
                await session.execute(text(f"DELETE FROM {table}"))
            await session.commit()
        await _seed(self.factory)

    async def asyncTearDown(self):
        await self.engine.dispose()

    # -- o tipo da coluna ------------------------------------------------

    async def test_positions_is_a_real_jsonb_array_and_not_a_string(self):
        """Se houvesse serializacao dupla, isto falharia - e foi exatamente a
        armadilha que ``VectorCompatible`` tomou na Fase 1."""
        async with self.factory() as session:
            lengths = (
                await session.execute(
                    text(
                        "SELECT term, jsonb_array_length(positions) "
                        "FROM knowledge_chunk_terms WHERE term = 'solucao' "
                        "ORDER BY jsonb_array_length(positions) DESC"
                    )
                )
            ).all()
        self.assertTrue(lengths)
        for _, length in lengths:
            self.assertGreaterEqual(length, 1)

    async def test_the_first_position_is_queryable_as_a_number(self):
        async with self.factory() as session:
            value = await session.scalar(
                text(
                    "SELECT (positions->>0)::int FROM knowledge_chunk_terms "
                    "WHERE term = 'diluicao' LIMIT 1"
                )
            )
        self.assertIsInstance(value, int)

    async def test_a_heading_position_is_above_the_displacement(self):
        """Corpo e contexto em espacos separados, verificado no banco."""
        async with self.factory() as session:
            maximum = await session.scalar(
                text(
                    "SELECT max((p)::int) FROM knowledge_chunk_terms t, "
                    "jsonb_array_elements_text(t.positions) p "
                    "WHERE t.term = 'em13cnt101'"
                )
            )
        self.assertGreater(maximum, 999_999)

    # -- as travas, com o service contornado -----------------------------

    async def test_a_posting_with_no_frequency_at_all_is_refused(self):
        with self.assertRaises(IntegrityError) as caught:
            async with self.factory() as session:
                session.add(
                    KnowledgeChunkTerm(
                        chunk_id=_identifier("chunk", "1"),
                        term="vazio",
                        term_frequency=0,
                        heading_frequency=0,
                        positions=[],
                    )
                )
                await session.commit()
        self.assertIn("ck_knowledge_chunk_terms_any_frequency", str(caught.exception))

    async def test_a_heading_only_posting_is_accepted(self):
        """O CHECK antigo ``term_frequency > 0`` proibia este caso legitimo."""
        async with self.factory() as session:
            session.add(
                KnowledgeChunkTerm(
                    chunk_id=_identifier("chunk", "1"),
                    term="apenasnotitulo",
                    term_frequency=0,
                    heading_frequency=2,
                    positions=[1_000_000],
                )
            )
            await session.commit()
        async with self.factory() as session:
            row = await session.scalar(
                select(KnowledgeChunkTerm).where(
                    KnowledgeChunkTerm.term == "apenasnotitulo"
                )
            )
        self.assertEqual(row.heading_frequency, 2)

    async def test_a_zero_generation_is_refused(self):
        with self.assertRaises(IntegrityError) as caught:
            async with self.factory() as session:
                session.add(
                    KnowledgeLexicalIndexState(
                        scope="OUTRO",
                        generation=0,
                        normalizer_version="v1",
                        policy_version="v1",
                    )
                )
                await session.commit()
        self.assertIn(
            "ck_knowledge_lexical_index_state_generation", str(caught.exception)
        )

    async def test_deleting_an_indexed_chunk_is_refused_by_the_foreign_key(self):
        """E por isso que ``documents.py`` purga o indice ANTES do DELETE."""
        with self.assertRaises(IntegrityError):
            async with self.factory() as session:
                await session.execute(
                    text("DELETE FROM knowledge_chunks WHERE ordinal = 1")
                )
                await session.commit()

    async def test_the_composite_index_exists_for_the_hot_query(self):
        async with self.factory() as session:
            indexes = (
                await session.scalars(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE tablename = 'knowledge_chunk_terms'"
                    )
                )
            ).all()
        self.assertIn("ix_knowledge_chunk_terms_term_chunk", indexes)
        self.assertIn("ix_knowledge_chunk_terms_term", indexes)

    # -- comportamento end-to-end no Postgres ----------------------------

    async def test_accent_insensitivity_holds_against_a_real_database(self):
        async with self.factory() as session:
            searcher = LexicalSearcher(session)
            results = [
                await searcher.search(spelling)
                for spelling in ("diluição", "diluicao", "DILUIÇÃO", "Diluicao")
            ]
        rankings = {tuple(str(hit.chunk_id) for hit in r.hits) for r in results}
        self.assertEqual(len(rankings), 1)
        self.assertTrue(next(iter(rankings)))

    async def test_singular_and_plural_return_the_same_set(self):
        async with self.factory() as session:
            searcher = LexicalSearcher(session)
            singular = await searcher.search("reagente")
            plural = await searcher.search("reagentes")
        self.assertEqual(
            [str(h.chunk_id) for h in singular.hits],
            [str(h.chunk_id) for h in plural.hits],
        )

    async def test_the_phrase_outranks_the_scattered_terms_on_postgres(self):
        async with self.factory() as session:
            result = await LexicalSearcher(session).search("reagente limitante")
        self.assertEqual(result.hits[0].chunk_id, _identifier("chunk", "2"))

    async def test_solution_is_closed_by_default_on_postgres(self):
        async with self.factory() as session:
            closed = await LexicalSearcher(session).search("concentracao")
            opened = await LexicalSearcher(session).search(
                "concentracao", retrieval_purpose="LEARN"
            )
        self.assertNotIn("SOLUTION", [h.chunk_type for h in closed.hits])
        self.assertIn("SOLUTION", [h.chunk_type for h in opened.hits])

    async def test_no_commercial_excerpt_ever_comes_back(self):
        async with self.factory() as session:
            result = await LexicalSearcher(session).search("mol")
        commercial = [
            h for h in result.hits if h.rights_class == "COMMERCIAL_REFERENCE"
        ]
        self.assertTrue(commercial)
        self.assertTrue(all(h.excerpt is None for h in commercial))

    async def test_source_diversity_is_visible_across_three_sources(self):
        async with self.factory() as session:
            result = await LexicalSearcher(session).search("mol")
        self.assertGreaterEqual(result.distinct_sources, 2)
        self.assertFalse(result.coverage_warning)


class CrossDialectEquality(unittest.IsolatedAsyncioTestCase):
    """O criterio de aceite mais forte: ranking IDENTICO nos dois dialetos.

    Mesmo corpus, mesmos ids, mesmas consultas. Se houvesse duas
    implementacoes - ou um ``if dialect ==`` escondido -, divergiriam aqui.
    """

    database_name = "agente_ia_edu_lexical_cross_test"
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
        self.postgres = create_async_engine(self.url)
        self.pg_factory = async_sessionmaker(
            self.postgres, class_=AsyncSession, expire_on_commit=True
        )
        async with self.pg_factory() as session:
            for table in (
                "knowledge_chunk_terms",
                "knowledge_chunk_lexical_index",
                "knowledge_lexical_index_state",
                "knowledge_chunks",
                "knowledge_documents",
                "knowledge_sources",
            ):
                await session.execute(text(f"DELETE FROM {table}"))
            await session.commit()
        await _seed(self.pg_factory)

        self.sqlite = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.sqlite.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sqlite_factory = async_sessionmaker(
            self.sqlite, class_=AsyncSession, expire_on_commit=True
        )
        await _seed(self.sqlite_factory)

    async def asyncTearDown(self):
        await self.postgres.dispose()
        await self.sqlite.dispose()

    async def _both(self, query: str, **kwargs):
        async with self.pg_factory() as session:
            postgres = await LexicalSearcher(session).search(query, **kwargs)
        async with self.sqlite_factory() as session:
            sqlite = await LexicalSearcher(session).search(query, **kwargs)
        return postgres, sqlite

    async def test_the_evaluation_set_ranks_identically_in_both_dialects(self):
        for query in EVALUATION_SET_V1:
            with self.subTest(query=query):
                postgres, sqlite = await self._both(query, limit=20)
                self.assertEqual(
                    [str(h.chunk_id) for h in postgres.hits],
                    [str(h.chunk_id) for h in sqlite.hits],
                )
                for left, right in zip(postgres.hits, sqlite.hits):
                    self.assertAlmostEqual(left.score, right.score, places=9)

    async def test_the_calibration_set_ranks_identically_in_both_dialects(self):
        for query in CALIBRATION_SET_V1:
            with self.subTest(query=query):
                postgres, sqlite = await self._both(query, limit=20)
                self.assertEqual(
                    [str(h.chunk_id) for h in postgres.hits],
                    [str(h.chunk_id) for h in sqlite.hits],
                )

    async def test_the_statistics_are_identical(self):
        postgres, sqlite = await self._both("mol")
        self.assertEqual(postgres.corpus_size, sqlite.corpus_size)
        self.assertAlmostEqual(postgres.avgdl, sqlite.avgdl, places=9)
        self.assertEqual(postgres.total_candidates, sqlite.total_candidates)

    async def test_every_score_component_is_identical(self):
        postgres, sqlite = await self._both("concentração das soluções")
        for left, right in zip(postgres.hits, sqlite.hits):
            for key in ("bm25", "phrase_bonus", "proximity_bonus", "doc_length"):
                self.assertAlmostEqual(
                    left.explanation[key], right.explanation[key], places=9, msg=key
                )
            self.assertEqual(
                left.explanation["phrase_hits"], right.explanation["phrase_hits"]
            )

    async def test_the_query_fingerprint_is_identical_across_dialects(self):
        """O fingerprint nao depende do banco: ele identifica a FOTO - consulta,
        filtros, politica, normalizador e geracao."""
        postgres, sqlite = await self._both("estequiometria")
        self.assertEqual(postgres.query_fingerprint, sqlite.query_fingerprint)

    async def test_the_postings_are_byte_identical(self):
        async with self.pg_factory() as session:
            pg_rows = (
                await session.execute(
                    select(
                        KnowledgeChunkTerm.chunk_id,
                        KnowledgeChunkTerm.term,
                        KnowledgeChunkTerm.term_frequency,
                        KnowledgeChunkTerm.heading_frequency,
                        KnowledgeChunkTerm.positions,
                    ).order_by(KnowledgeChunkTerm.chunk_id, KnowledgeChunkTerm.term)
                )
            ).all()
        async with self.sqlite_factory() as session:
            lite_rows = (
                await session.execute(
                    select(
                        KnowledgeChunkTerm.chunk_id,
                        KnowledgeChunkTerm.term,
                        KnowledgeChunkTerm.term_frequency,
                        KnowledgeChunkTerm.heading_frequency,
                        KnowledgeChunkTerm.positions,
                    ).order_by(KnowledgeChunkTerm.chunk_id, KnowledgeChunkTerm.term)
                )
            ).all()
        self.assertEqual(len(pg_rows), len(lite_rows))
        self.assertEqual(
            [(str(r[0]), r[1], r[2], r[3], list(r[4])) for r in pg_rows],
            [(str(r[0]), r[1], r[2], r[3], list(r[4])) for r in lite_rows],
        )


if __name__ == "__main__":
    unittest.main()
