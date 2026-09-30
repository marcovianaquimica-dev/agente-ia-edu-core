"""CEREBRO / Knowledge Engine - Fase 1: o que so o PostgreSQL real prova.

Tres coisas que o SQLite nao consegue responder e que sao exatamente o risco
que a Fase 1 existe para eliminar:

1. a extensao pgvector instala e a coluna e mesmo do tipo ``vector``;
2. a coluna sem dimensao aceita vetores e o operador de distancia ``<=>``
   funciona sobre ela com cast explicito - ou seja, o indice ANN por espaco
   descrito na spec 5.4 e viavel;
3. o CheckConstraint que impede uma fonte COMMERCIAL_REFERENCE de existir
   como EducationalResource rejeita de fato o INSERT.

Banco DESCARTAVEL proprio, criado e destruido pelo teste - o mesmo padrao dos
demais ``*_postgresql`` do projeto. Nao toca o banco de desenvolvimento.
"""

from __future__ import annotations

import os
import unittest
import uuid

from sqlalchemy import create_engine, insert, select, text

from agente_ia_edu.db.base import Base
import agente_ia_edu.db.models  # noqa: F401 - registra todo o metadata
from agente_ia_edu.db.models import (
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeDocument,
    KnowledgeEmbeddingSpace,
    KnowledgeSource,
)


class KnowledgeEngineSchemaPostgreSQLTests(unittest.TestCase):
    database_name = "agente_ia_edu_knowledge_engine_schema_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres"
    database_url = f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}"

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
                "PostgreSQL de teste indisponivel para o schema do Knowledge Engine"
            ) from exc
        cls._admin_execute(f"DROP DATABASE IF EXISTS {cls.database_name}")
        cls._admin_execute(f"CREATE DATABASE {cls.database_name}")
        cls.engine = create_engine(cls.database_url)
        # NAO criamos a extensao aqui de proposito: o listener em
        # db/models/knowledge_engine.py tem de faze-lo sozinho. Ver
        # test_create_all_installs_the_extension_without_being_asked.
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        cls._admin_execute(f"DROP DATABASE IF EXISTS {cls.database_name}")

    # -- 1. a extensao e o tipo da coluna --------------------------------

    def test_create_all_installs_the_extension_without_being_asked(self):
        """Regressao: a coluna ``vector`` fez ``create_all`` quebrar em TODO
        teste ``*_postgresql`` do projeto que cria um banco descartavel -
        dezenas deles, nenhum relacionado ao Knowledge Engine, todos com
        ``type "vector" does not exist``.

        O ``setUpClass`` desta classe nao executa ``CREATE EXTENSION``. Se este
        teste passa, o listener declarativo fez o trabalho, e nenhum teste
        futuro precisa saber que a extensao existe.
        """
        with self.engine.connect() as connection:
            installed = connection.execute(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one()
        self.assertEqual(installed, 1)

    def test_pgvector_extension_is_installed(self):
        with self.engine.connect() as connection:
            installed = connection.execute(
                text("SELECT installed_version FROM pg_available_extensions WHERE name = 'vector'")
            ).scalar_one()
        self.assertIsNotNone(installed)

    def test_embedding_column_is_really_a_vector_and_carries_no_dimension(self):
        with self.engine.connect() as connection:
            udt, modifier = connection.execute(
                text(
                    "SELECT udt_name, atttypmod FROM information_schema.columns c "
                    "JOIN pg_attribute a ON a.attname = c.column_name "
                    "JOIN pg_class cl ON cl.oid = a.attrelid AND cl.relname = c.table_name "
                    "WHERE c.table_name = 'knowledge_chunk_embeddings' "
                    "AND c.column_name = 'embedding'"
                )
            ).one()
        self.assertEqual(udt, "vector")
        # atttypmod == -1 significa "sem modificador de tipo", ou seja, sem
        # dimensao declarada. E o ponto inteiro da spec 5.4.
        self.assertEqual(modifier, -1)

    # -- 2. vetores reais e o operador de distancia ----------------------

    def _seed_chunk_and_space(self, connection, dimensions: int = 3):
        """Semeia pelo ORM - o mesmo caminho que a producao usa."""
        source_id, document_id, chunk_id, space_id = (uuid.uuid4() for _ in range(4))
        connection.execute(
            insert(KnowledgeSource).values(
                id=source_id, title="Fonte", source_kind="TEXTBOOK",
                rights_class="OWN", authority_level="OWN",
            )
        )
        connection.execute(
            insert(KnowledgeDocument).values(
                id=document_id, source_id=source_id, filename="a.pdf",
                storage_uri="/tmp/a.pdf", document_hash=uuid.uuid4().hex,
            )
        )
        connection.execute(
            insert(KnowledgeChunk).values(
                id=chunk_id, source_id=source_id, document_id=document_id, ordinal=1,
                chunk_type="PROSE", raw_text="texto", text_hash=uuid.uuid4().hex,
            )
        )
        connection.execute(
            insert(KnowledgeEmbeddingSpace).values(
                id=space_id, provider="fake", model=f"m{dimensions}-{space_id.hex[:8]}",
                dimensions=dimensions, status="BACKFILLING",
            )
        )
        return chunk_id, space_id

    def test_vector_round_trips_through_the_orm_into_a_real_vector_column(self):
        """O caminho que a producao usa: VectorCompatible vincula o parametro
        com o proprio tipo vector, sem cast intermediario."""
        with self.engine.begin() as connection:
            chunk_id, space_id = self._seed_chunk_and_space(connection)
            connection.execute(
                insert(KnowledgeChunkEmbedding).values(
                    id=uuid.uuid4(), chunk_id=chunk_id, space_id=space_id,
                    embedding=[1.0, 0.0, 0.0], text_hash="h", is_active=True,
                )
            )
            stored = connection.execute(
                select(KnowledgeChunkEmbedding.embedding).where(
                    KnowledgeChunkEmbedding.space_id == space_id
                )
            ).scalar_one()
        self.assertEqual(stored, [1.0, 0.0, 0.0])

    def test_raw_sql_must_cast_a_bound_parameter_to_vector(self):
        """Conhecimento que a Fase 7 vai precisar, fixado como teste.

        psycopg tipa um parametro str como VARCHAR, e o PostgreSQL NAO faz
        cast implicito de VARCHAR para vector. Toda consulta de recuperacao
        escrita em SQL cru precisa de ``::vector`` explicito - pelo ORM o
        problema nao existe, porque o tipo da coluna viaja junto.
        """
        with self.engine.begin() as connection:
            chunk_id, space_id = self._seed_chunk_and_space(connection)
            with self.assertRaises(Exception) as caught:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO knowledge_chunk_embeddings (id, chunk_id, space_id, "
                            "embedding, text_hash, is_active, generated_at) VALUES "
                            "(:id, :c, :s, :v, 'h', true, CURRENT_TIMESTAMP)"
                        ).bindparams(id=uuid.uuid4(), c=chunk_id, s=space_id, v="[1,0,0]")
                    )
            self.assertIn("vector", str(caught.exception).lower())

            # Com o cast explicito, funciona.
            connection.execute(
                text(
                    "INSERT INTO knowledge_chunk_embeddings (id, chunk_id, space_id, "
                    "embedding, text_hash, is_active, generated_at) VALUES "
                    "(:id, :c, :s, CAST(:v AS vector), 'h', true, CURRENT_TIMESTAMP)"
                ).bindparams(id=uuid.uuid4(), c=chunk_id, s=space_id, v="[1,0,0]")
            )
            stored = connection.execute(
                select(KnowledgeChunkEmbedding.embedding).where(
                    KnowledgeChunkEmbedding.space_id == space_id
                )
            ).scalar_one()
        self.assertEqual(stored, [1.0, 0.0, 0.0])

    def test_cosine_distance_operator_works_on_the_unsized_column(self):
        """Sem isto, o indice HNSW parcial por espaco da spec 5.4 nao seria
        viavel e o desenho inteiro de 'dimensao e dado' cairia."""
        with self.engine.begin() as connection:
            chunk_id, space_id = self._seed_chunk_and_space(connection)
            for index, vector in enumerate(([1.0, 0.0, 0.0], [0.0, 1.0, 0.0])):
                connection.execute(
                    insert(KnowledgeChunkEmbedding).values(
                        id=uuid.uuid4(), chunk_id=chunk_id, space_id=space_id,
                        embedding=vector, text_hash=f"hash{index}", is_active=True,
                    )
                )
            ordered = connection.execute(
                text(
                    "SELECT embedding::text FROM knowledge_chunk_embeddings "
                    "WHERE space_id = :space "
                    "ORDER BY embedding::vector(3) <=> CAST(:q AS vector(3))"
                ).bindparams(q="[1,0,0]", space=space_id)
            ).scalars().all()
        self.assertEqual(ordered[0], "[1,0,0]")

    def test_a_partial_hnsw_index_per_space_can_be_created(self):
        with self.engine.begin() as connection:
            _, space_id = self._seed_chunk_and_space(connection)
            connection.execute(
                text(
                    "CREATE INDEX ix_probe_hnsw ON knowledge_chunk_embeddings "
                    "USING hnsw ((embedding::vector(3)) vector_cosine_ops) "
                    f"WHERE space_id = '{space_id}'"
                )
            )
            exists = connection.execute(
                text("SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_probe_hnsw'")
            ).scalar_one()
            connection.execute(text("DROP INDEX ix_probe_hnsw"))
        self.assertEqual(exists, 1)

    # -- 3. a trava de direitos ------------------------------------------

    def test_commercial_source_cannot_reference_an_educational_resource(self):
        resource_id = uuid.uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO educational_resources (id, title, resource_type, origin_type, "
                    "status, visibility_scope, created_at, updated_at) VALUES "
                    "(:id, 'Livro', 'BOOK', 'LICENSED', 'active', 'LICENSED', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ).bindparams(id=resource_id)
            )
        with self.assertRaises(Exception) as caught:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO knowledge_sources (id, title, source_kind, rights_class, "
                        "authority_level, educational_resource_id, status, created_at, "
                        "updated_at) VALUES (:id, 'Livro comercial', 'TEXTBOOK', "
                        "'COMMERCIAL_REFERENCE', 'COMMERCIAL_TEXTBOOK', :rid, 'REGISTERED', "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ).bindparams(id=uuid.uuid4(), rid=resource_id)
                )
        self.assertIn("ck_knowledge_sources_commercial_has_no_resource", str(caught.exception))

    def test_a_non_commercial_source_may_reference_an_educational_resource(self):
        """A trava e cirurgica: a BNCC (OFFICIAL_PUBLIC) pode, sim."""
        resource_id = uuid.uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO educational_resources (id, title, resource_type, origin_type, "
                    "status, visibility_scope, created_at, updated_at) VALUES "
                    "(:id, 'BNCC EM', 'PDF', 'EXTERNAL', 'active', 'PUBLIC', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ).bindparams(id=resource_id)
            )
            connection.execute(
                text(
                    "INSERT INTO knowledge_sources (id, title, source_kind, rights_class, "
                    "authority_level, educational_resource_id, status, created_at, updated_at) "
                    "VALUES (:id, 'BNCC Ensino Medio', 'CURRICULUM_FRAMEWORK', "
                    "'OFFICIAL_PUBLIC', 'OFFICIAL', :rid, 'REGISTERED', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ).bindparams(id=uuid.uuid4(), rid=resource_id)
            )
            stored = connection.execute(
                text(
                    "SELECT count(*) FROM knowledge_sources "
                    "WHERE educational_resource_id = :rid"
                ).bindparams(rid=resource_id)
            ).scalar_one()
        self.assertEqual(stored, 1)

    def test_only_one_embedding_space_may_be_active(self):
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO knowledge_embedding_spaces (id, provider, model, dimensions, "
                    "distance_metric, status, created_at) VALUES "
                    "(:id, 'openai', 'active-a', 8, 'cosine', 'ACTIVE', CURRENT_TIMESTAMP)"
                ).bindparams(id=uuid.uuid4())
            )
        with self.assertRaises(Exception) as caught:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO knowledge_embedding_spaces (id, provider, model, "
                        "dimensions, distance_metric, status, created_at) VALUES "
                        "(:id, 'openai', 'active-b', 16, 'cosine', 'ACTIVE', CURRENT_TIMESTAMP)"
                    ).bindparams(id=uuid.uuid4())
                )
        self.assertIn("uq_knowledge_embedding_spaces_single_active", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
