"""CEREBRO - Fase 6, passo 4: o que so o PostgreSQL real prova.

Tres coisas:

1. **O backend de producao e o pgvector.** A distancia e calculada pelo
   operador ``<=>`` do banco, com o mesmo cast ``::vector(n)`` com que a
   migracao 058 criou o indice HNSW parcial - e a busca DIZ que foi ele, em
   ``vector_backend``.

2. **O indice ANN parcial do espaco e usavel pela consulta.** Verificado por
   ``EXPLAIN``: sem isso o desenho de "dimensao e dado, nao DDL" nao se
   sustentaria em escala.

3. **As duas implementacoes ORDENAM igual.** Mesmo corpus sintetico, mesma
   consulta, mesma ordem em SQLite e PostgreSQL. E a prova de que existe UMA
   implementacao de filtro, desempate e montagem, e nao duas que concordam
   por sorte.

O que NAO se afirma em lugar nenhum: que o backend em processo se equipare em
DESEMPENHO ao PostgreSQL. Ele e exato e varre tudo; existe para teste.

Banco DESCARTAVEL proprio. Nao toca o banco de desenvolvimento.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
import uuid

from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
import agente_ia_edu.db.models  # noqa: F401 - registra todo o metadata
from agente_ia_edu.db.models import (
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeDocument,
    KnowledgeEmbeddingActivation,
    KnowledgeEmbeddingSpace,
    KnowledgeSource,
)
from agente_ia_edu.knowledge_chunking_policy.v1 import retrieval_text_hash
from agente_ia_edu.services.knowledge_engine.embedding import (
    coverage as corpus_coverage,
)
from agente_ia_edu.services.knowledge_engine.embedding_activation import (
    EmbeddingActivationService,
)
from agente_ia_edu.services.knowledge_engine.vector_search import (
    EXACT_BACKEND,
    PGVECTOR_BACKEND,
    VectorSearcher,
)

from test_knowledge_vector_search import _ScriptedProvider

_DIMENSIONS = 3
_NAMESPACE = uuid.UUID("7c2f1a40-0000-4000-8000-000000000000")

#: Corpus sintetico, IDENTICO nos dois dialetos - mesmos UUIDs, mesmos
#: vetores, mesma ordem de insercao. UUID deterministico (uuid5) para que a
#: comparacao seja por identidade e nao por posicao, e para que o desempate
#: por ``chunk_id`` produza a mesma ordem dos dois lados.
_CORPUS = [
    # (chave, vetor, chunk_type, editorial_role, fonte)
    ("identico", [1.0, 0.0, 0.0], "PROSE", "CONTENT", "propria"),
    ("quase", [0.95, 0.05, 0.0], "PROSE", "CONTENT", "propria"),
    ("quarenta_e_cinco", [1.0, 1.0, 0.0], "PROSE", "CONTENT", "comercial"),
    ("ortogonal", [0.0, 1.0, 0.0], "PROSE", "CONTENT", "propria"),
    ("oposto", [-1.0, 0.0, 0.0], "PROSE", "CONTENT", "comercial"),
    # Empate exato com "identico": o desempate tem de resolver, e resolver
    # igual nos dois dialetos.
    ("empatado", [1.0, 0.0, 0.0], "PROSE", "CONTENT", "comercial"),
    ("gabarito", [0.9, 0.1, 0.0], "SOLUTION", "ANSWER_KEY", "propria"),
]

#: Populacao elegivel do corpus sintetico: os 7 chunks menos nenhum - todos
#: os papeis usados aqui sao elegiveis para embedding.
_ELEGIVEIS = len(_CORPUS)

_QUERY = "consulta sintetica"
_QUERY_VECTOR = (1.0, 0.0, 0.0)


def _id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, "/".join(parts))


async def _seed(factory, space_id: uuid.UUID) -> None:
    """Semeia o MESMO corpus, byte a byte, em qualquer dialeto."""
    async with factory() as session:
        for key, rights, authority, kind, title in (
            ("propria", "OWN", "OWN", "OWN_MATERIAL", "Apostila propria"),
            (
                "comercial", "COMMERCIAL_REFERENCE", "COMMERCIAL_TEXTBOOK",
                "TEXTBOOK", "Livro comercial",
            ),
        ):
            session.add(
                KnowledgeSource(
                    id=_id("source", key), title=title, source_kind=kind,
                    rights_class=rights, authority_level=authority,
                )
            )
            session.add(
                KnowledgeDocument(
                    id=_id("document", key), source_id=_id("source", key),
                    filename=f"{key}.pdf", storage_uri=f"/tmp/{key}.pdf",
                    document_hash=_id("document", key).hex * 2,
                )
            )
        await session.flush()

        for ordinal, (key, vector, chunk_type, role, fonte) in enumerate(_CORPUS, 1):
            raw = f"Texto sintetico {key}."
            session.add(
                KnowledgeChunk(
                    id=_id("chunk", key), source_id=_id("source", fonte),
                    document_id=_id("document", fonte), ordinal=ordinal,
                    chunk_type=chunk_type, heading_path=["Cap 1"],
                    page_start=100 + ordinal, page_end=100 + ordinal,
                    raw_text=raw,
                    text_hash=retrieval_text_hash(
                        raw_text=raw, heading_path=["Cap 1"]
                    ),
                    char_count=len(raw), editorial_role=role,
                    editorial_detector_version="v1", bncc_node_codes=[],
                )
            )
        await session.flush()

        for key, vector, *_ in _CORPUS:
            session.add(
                KnowledgeChunkEmbedding(
                    id=_id("embedding", key), chunk_id=_id("chunk", key),
                    space_id=space_id, embedding=vector,
                    text_hash=retrieval_text_hash(
                        raw_text=f"Texto sintetico {key}.", heading_path=["Cap 1"]
                    ),
                    is_active=True,
                )
            )
        await session.commit()

    async with factory() as session:
        elegiveis = (await corpus_coverage(session, space_id)).eligible_chunks
        session.add(
            KnowledgeEmbeddingActivation(
                id=_id("activation", "1"), space_id=space_id,
                previous_space_id=None, action="ACTIVATE",
                policy="ELIGIBLE_ROLES", expected_population=elegiveis,
                eligible_chunks=elegiveis, embedded=elegiveis, missing=0,
                stale=0, dimension_violations=0, activated_rows=elegiveis,
                deactivated_rows=0, degraded=False, violations=[],
            )
        )
        await session.commit()


class VectorSearchPostgreSQL(unittest.IsolatedAsyncioTestCase):
    database_name = "agente_ia_edu_vector_search_test"
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
        except Exception as exc:  # pragma: no cover - ambiente sem Postgres
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
            # O indice ANN sobrevive ao DELETE das linhas - ele e DDL. Cada
            # teste precisa comecar no estado de ``create_all``, que e
            # justamente "sem indice", senao a prova de paridade mediria o
            # residuo do teste anterior.
            await session.execute(
                text(f"DROP INDEX IF EXISTS ix_kce_hnsw_{_id('space', 'a').hex}")
            )
            for table in (
                "knowledge_embedding_activations",
                "knowledge_chunk_embeddings",
                "knowledge_chunks",
                "knowledge_documents",
                "knowledge_embedding_spaces",
                "knowledge_sources",
            ):
                await session.execute(text(f"DELETE FROM {table}"))
            session.add(
                KnowledgeEmbeddingSpace(
                    id=_id("space", "a"), provider="fake", model="modelo-a",
                    dimensions=_DIMENSIONS, distance_metric="cosine",
                    status="ACTIVE",
                )
            )
            await session.commit()
        self.space_id = _id("space", "a")
        await _seed(self.factory, self.space_id)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _search(self, **kwargs):
        provider = _ScriptedProvider({_QUERY: _QUERY_VECTOR})
        async with self.factory() as session:
            return await VectorSearcher(session, provider=provider).search(
                _QUERY, limit=100, **kwargs
            )

    # -- 1. pgvector de verdade -------------------------------------------

    async def test_the_production_backend_is_pgvector(self):
        resultado = await self._search()
        self.assertEqual(resultado.vector_backend, PGVECTOR_BACKEND)
        self.assertTrue(
            all(
                hit.explanation["vector_backend"] == PGVECTOR_BACKEND
                for hit in resultado.hits
            )
        )

    async def test_the_distances_come_from_the_cosine_operator(self):
        """Conferidas contra o proprio ``<=>``, nao contra Python: o que se
        verifica aqui e que a busca usa o operador do banco."""
        resultado = await self._search()
        async with self.factory() as session:
            esperado = dict(
                (
                    await session.execute(
                        text(
                            "SELECT chunk_id, embedding::vector(3) <=> "
                            "CAST(:q AS vector(3)) FROM knowledge_chunk_embeddings"
                        ).bindparams(q="[1.0,0.0,0.0]")
                    )
                ).all()
            )
        for hit in resultado.hits:
            self.assertAlmostEqual(hit.distance, esperado[hit.chunk_id], places=9)
            self.assertAlmostEqual(hit.score + hit.distance, 1.0, places=9)

    async def test_the_real_activation_path_creates_the_ann_index(self):
        """PARIDADE DE INFRAESTRUTURA - passo 4.1.

        O teste NAO cria o indice. Ele exercita o caminho que producao e
        piloto usam: ``activate()``. O passo 5 expos a divergencia - um banco
        montado por ``Base.metadata.create_all``, que e como o corpus de
        avaliacao e todos os testes sao montados, nao tinha indice ANN algum,
        enquanto um montado por migracao tinha. O caminho de producao so
        existia no banco que ninguem usava.

        A correcao foi mover o indice para o CICLO DE VIDA DO ESPACO, onde
        ele pertence: ele depende de ``space_id`` e dimensao, que sao DADO, e
        por isso jamais poderia ser um hook de ``create_all`` - na hora em que
        as tabelas nascem nao existe espaco algum.
        """
        from agente_ia_edu.services.knowledge_engine.embedding_activation import (
            ann_index_name,
        )

        esperado = ann_index_name(self.space_id)
        async with self.factory() as session:
            antes = await session.scalar(
                text("SELECT count(*) FROM pg_indexes WHERE indexname = :n")
                .bindparams(n=esperado)
            )
        self.assertEqual(antes, 0, "o indice nao deveria existir antes")

        async with self.factory() as session:
            await EmbeddingActivationService(session).activate(
                self.space_id, expected_population=_ELEGIVEIS
            )
            await session.commit()

        async with self.factory() as session:
            definicao = await session.scalar(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = :n")
                .bindparams(n=esperado)
            )
        self.assertIsNotNone(definicao, "activate() nao criou o indice ANN")
        self.assertIn("hnsw", definicao.lower())
        self.assertIn("vector_cosine_ops", definicao)
        self.assertIn(str(self.space_id), definicao)

    async def test_the_planner_actually_uses_the_index_created_that_way(self):
        """Nao basta o indice existir: o cast da consulta tem de casar com o
        da expressao indexada, senao o planejador o ignora."""
        async with self.factory() as session:
            await EmbeddingActivationService(session).activate(
                self.space_id, expected_population=_ELEGIVEIS
            )
            await session.commit()

        from agente_ia_edu.services.knowledge_engine.embedding_activation import (
            ann_index_name,
        )

        async with self.factory() as session:
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            plano = "\n".join(
                row[0]
                for row in (
                    await session.execute(
                        text(
                            "EXPLAIN SELECT chunk_id FROM knowledge_chunk_embeddings "
                            f"WHERE space_id = '{self.space_id}' ORDER BY "
                            f"embedding::vector({_DIMENSIONS}) <=> "
                            "CAST(:q AS vector(3)) LIMIT 5"
                        ).bindparams(q="[1.0,0.0,0.0]")
                    )
                ).all()
            )
            await session.rollback()
        self.assertIn(ann_index_name(self.space_id), plano)

    async def test_ensure_ann_index_is_idempotent(self):
        """Um banco montado por MIGRACAO ja tem o indice, com este mesmo
        nome. Chamar de novo nao pode criar um segundo nem falhar."""
        from agente_ia_edu.services.knowledge_engine.embedding_activation import (
            ann_index_name,
        )

        async with self.factory() as session:
            servico = EmbeddingActivationService(session)
            self.assertTrue(await servico.ensure_ann_index(self.space_id))
            self.assertTrue(await servico.ensure_ann_index(self.space_id))
            await session.commit()
        async with self.factory() as session:
            quantos = await session.scalar(
                text("SELECT count(*) FROM pg_indexes WHERE indexname = :n")
                .bindparams(n=ann_index_name(self.space_id))
            )
        self.assertEqual(quantos, 1)

    async def test_the_ddl_matches_what_migration_058_produces(self):
        """FONTE UNICA, sem reescrever historia.

        A migracao 058 continua com o seu DDL literal - migracao e registro
        historico e nao se reescreve. O vinculo entre as duas e este teste:
        se a funcao divergir do que a migracao cria, o nome ou a definicao
        param de bater e isto quebra.
        """
        from agente_ia_edu.services.knowledge_engine.embedding_activation import (
            ann_index_ddl, ann_index_name,
        )
        import importlib.util

        caminho = (
            Path(__file__).resolve().parents[1]
            / "migrations" / "versions" / "058_knowledge_engine_embeddings.py"
        )
        spec = importlib.util.spec_from_file_location("mig058", caminho)
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)

        inicial = uuid.UUID(mig.INITIAL_SPACE_ID)
        self.assertEqual(
            ann_index_name(inicial),
            f"ix_kce_hnsw_{mig.INITIAL_SPACE_ID.replace('-', '')}",
        )
        gerado = ann_index_ddl(inicial, mig.INITIAL_DIMENSIONS)
        for fragmento in (
            "USING hnsw",
            f"(embedding::vector({mig.INITIAL_DIMENSIONS})) vector_cosine_ops",
            f"WHERE space_id = '{mig.INITIAL_SPACE_ID}'",
        ):
            self.assertIn(fragmento, gerado, fragmento)

    # -- 2. o contrato, no banco de producao ------------------------------

    async def test_the_known_cosine_ranking_holds_on_postgresql(self):
        resultado = await self._search(retrieval_purpose="LEARN")
        self.assertEqual(
            [hit.chunk_id for hit in resultado.hits][:2],
            sorted([_id("chunk", "identico"), _id("chunk", "empatado")], key=str),
        )
        self.assertEqual(resultado.hits[-1].chunk_id, _id("chunk", "oposto"))

    async def test_solution_is_closed_in_practice_on_postgresql(self):
        resultado = await self._search(retrieval_purpose="PRACTICE")
        self.assertNotIn(
            _id("chunk", "gabarito"), [hit.chunk_id for hit in resultado.hits]
        )
        self.assertIn("PURPOSE_SOLUTION", resultado.filtered_out)

    async def test_no_commercial_literal_crosses_the_boundary_on_postgresql(self):
        resultado = await self._search()
        comerciais = [
            hit for hit in resultado.hits
            if hit.rights_class == "COMMERCIAL_REFERENCE"
        ]
        self.assertTrue(comerciais)
        for hit in comerciais:
            self.assertIsNone(hit.excerpt)
            self.assertFalse(hit.quotable)
            self.assertIsNotNone(hit.page_start)
            self.assertEqual(len(hit.text_hash), 64)

    async def test_an_inactive_vector_is_invisible_on_postgresql(self):
        async with self.factory() as session:
            await session.execute(
                text(
                    "UPDATE knowledge_chunk_embeddings SET is_active = false "
                    "WHERE chunk_id = :chunk"
                ).bindparams(chunk=_id("chunk", "identico"))
            )
            await session.commit()
        resultado = await self._search(allow_degraded=True)
        self.assertNotIn(
            _id("chunk", "identico"), [hit.chunk_id for hit in resultado.hits]
        )

    # -- 3. equivalencia de ORDENACAO entre dialetos ----------------------

    async def test_both_backends_produce_the_same_order(self):
        """UMA implementacao de filtro, desempate e montagem - e nao duas que
        concordam por sorte.

        A afirmacao e sobre ORDEM. Desempenho nao e comparado: o backend em
        processo e exato e varre tudo, e existe para teste.
        """
        postgres = await self._search(retrieval_purpose="LEARN")

        sqlite_engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        try:
            async with sqlite_engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sqlite_factory = async_sessionmaker(
                sqlite_engine, class_=AsyncSession, expire_on_commit=True
            )
            async with sqlite_factory() as session:
                session.add(
                    KnowledgeEmbeddingSpace(
                        id=self.space_id, provider="fake", model="modelo-a",
                        dimensions=_DIMENSIONS, distance_metric="cosine",
                        status="ACTIVE",
                    )
                )
                await session.commit()
            await _seed(sqlite_factory, self.space_id)

            provider = _ScriptedProvider({_QUERY: _QUERY_VECTOR})
            async with sqlite_factory() as session:
                sqlite = await VectorSearcher(session, provider=provider).search(
                    _QUERY, limit=100, retrieval_purpose="LEARN"
                )
        finally:
            await sqlite_engine.dispose()

        self.assertEqual(postgres.vector_backend, PGVECTOR_BACKEND)
        self.assertEqual(sqlite.vector_backend, EXACT_BACKEND)
        self.assertEqual(
            [hit.chunk_id for hit in postgres.hits],
            [hit.chunk_id for hit in sqlite.hits],
        )
        self.assertEqual(
            [hit.rank for hit in postgres.hits], [hit.rank for hit in sqlite.hits]
        )
        # Os VALORES nao sao bit a bit iguais, e a afirmacao nunca foi essa: o
        # tipo ``vector`` do pgvector guarda float4, enquanto o backend em
        # processo calcula em float64 do Python. A divergencia medida fica na
        # ordem de 1e-8 - ver
        # ``test_the_two_backends_differ_in_precision_and_that_is_declared``.
        for esquerda, direita in zip(postgres.hits, sqlite.hits):
            self.assertAlmostEqual(esquerda.score, direita.score, places=6)

    async def test_the_two_backends_differ_in_precision_and_that_is_declared(self):
        """O limite da equivalencia, afirmado em vez de escondido numa
        tolerancia frouxa.

        ``vector`` guarda float4; o backend em processo calcula em float64. A
        igualdade que o passo 4 afirma e de ORDEM, nunca de bits - e muito
        menos de desempenho. Quem um dia comparar scores das duas pernas num
        relatorio precisa saber que o ultimo digito vem do tipo da coluna.
        """
        from agente_ia_edu.services.knowledge_engine.vector_search import (
            _cosine_distance,
        )
        import math as _math

        async with self.factory() as session:
            do_banco = await session.scalar(
                text(
                    "SELECT embedding::vector(3) <=> CAST(:q AS vector(3)) "
                    "FROM knowledge_chunk_embeddings WHERE chunk_id = :chunk"
                ).bindparams(q="[1.0,0.0,0.0]", chunk=_id("chunk", "quase"))
            )
        guardado = [0.95, 0.05, 0.0]
        do_python = _cosine_distance(
            _QUERY_VECTOR,
            guardado,
            query_norm=_math.sqrt(sum(c * c for c in _QUERY_VECTOR)),
        )
        # Diferentes - e e o ponto. O pgvector calcula sobre float4; o backend
        # em processo, sobre float64.
        self.assertNotEqual(do_banco, do_python)
        self.assertAlmostEqual(do_banco, do_python, places=7)
        self.assertLess(abs(do_banco - do_python), 1e-6)

    async def test_the_vector_column_survives_being_read_by_the_searcher(self):
        """Regressao da Fase 1: serializacao dupla corromperia todo vetor. Se
        o buscador lesse de forma diferente da gravacao, isto quebraria."""
        await self._search()
        async with self.factory() as session:
            dims = (
                await session.execute(
                    text(
                        "SELECT DISTINCT vector_dims(embedding) "
                        "FROM knowledge_chunk_embeddings"
                    )
                )
            ).scalars().all()
        self.assertEqual(dims, [_DIMENSIONS])

    async def test_there_is_exactly_one_active_representation_per_chunk(self):
        async with self.factory() as session:
            ativos = await session.scalar(
                select(text("count(*)")).select_from(
                    text("knowledge_chunk_embeddings")
                ).where(text("is_active"))
            )
            distintos = await session.scalar(
                select(text("count(distinct chunk_id)")).select_from(
                    text("knowledge_chunk_embeddings")
                ).where(text("is_active"))
            )
        self.assertEqual(ativos, distintos)


if __name__ == "__main__":
    unittest.main()
