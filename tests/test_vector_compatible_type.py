"""CEREBRO / Knowledge Engine - Fase 1: o tipo VectorCompatible.

Espelha JSONBCompatible (db/types.py): compila para o tipo nativo no
PostgreSQL (``vector``) e cai para ``TEXT`` no SQLite, sem que o modelo saiba
a diferenca. O impl NAO pode ser JSON - ver o docstring do tipo.

A diferenca decisiva em relacao ao JSONBCompatible: a coluna vector e
declarada SEM dimensao. A dimensao pertence ao espaco de embedding
(knowledge_embedding_spaces.dimensions), nao ao DDL - trocar de modelo nao
pode ser migracao de schema (spec 5.4).
"""

from __future__ import annotations

import unittest

from sqlalchemy import Column, Integer, MetaData, Table, create_engine, insert, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from agente_ia_edu.db.types import VectorCompatible


class VectorCompatibleCompilationTests(unittest.TestCase):
    def test_compiles_to_unsized_vector_on_postgresql(self):
        compiled = VectorCompatible().compile(dialect=postgresql.dialect())
        self.assertEqual(compiled.lower(), "vector")

    def test_compiles_to_text_on_sqlite(self):
        compiled = VectorCompatible().compile(dialect=sqlite.dialect())
        self.assertEqual(compiled.upper(), "TEXT")

    def test_create_table_ddl_carries_no_dimension_on_postgresql(self):
        metadata = MetaData()
        table = Table("t", metadata, Column("embedding", VectorCompatible()))
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        self.assertIn("vector", ddl.lower())
        # Nenhum numero de dimensao pode aparecer no DDL - esse e o ponto.
        self.assertNotIn("vector(", ddl.lower().replace(" ", ""))


class VectorCompatibleBindAndResultTests(unittest.TestCase):
    def test_binds_as_pgvector_literal(self):
        bound = VectorCompatible().bind_processor(postgresql.dialect())([0.5, -1.25, 0.0])
        self.assertEqual(bound, "[0.5,-1.25,0.0]")

    def test_parses_pgvector_literal_back_to_floats(self):
        parsed = VectorCompatible().result_processor(postgresql.dialect(), None)("[0.5,-1.25,0.0]")
        self.assertEqual(parsed, [0.5, -1.25, 0.0])

    def test_representation_is_identical_on_both_dialects(self):
        """Uma unica forma serializada: nada de dois caminhos para depurar."""
        value = [0.5, -1.25]
        self.assertEqual(
            VectorCompatible().bind_processor(postgresql.dialect())(value),
            VectorCompatible().bind_processor(sqlite.dialect())(value),
        )

    def test_none_survives_both_directions(self):
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            self.assertIsNone(VectorCompatible().bind_processor(dialect)(None))
            self.assertIsNone(VectorCompatible().result_processor(dialect, None)(None))

    def test_empty_vector_is_not_confused_with_none(self):
        self.assertEqual(VectorCompatible().bind_processor(postgresql.dialect())([]), "[]")
        self.assertEqual(VectorCompatible().result_processor(postgresql.dialect(), None)("[]"), [])

    def test_impl_is_not_json_so_the_literal_is_never_double_encoded(self):
        """Regressao: com impl=JSON o literal viraria '"[0.5]"' e corromperia
        toda escrita de vetor no PostgreSQL."""
        bound = VectorCompatible().bind_processor(postgresql.dialect())([0.5])
        self.assertFalse(bound.startswith('"'))


class VectorCompatibleSqliteRoundTripTests(unittest.TestCase):
    """O round-trip real em SQLite - o dialeto em que a maior parte da suite roda."""

    def setUp(self):
        self.metadata = MetaData()
        self.table = Table(
            "vectors",
            self.metadata,
            Column("id", Integer, primary_key=True),
            Column("embedding", VectorCompatible()),
        )
        self.engine = create_engine("sqlite://")
        self.metadata.create_all(self.engine)

    def test_round_trip_preserves_values_and_order(self):
        vector = [0.125, -0.5, 3.0, 0.0]
        with self.engine.begin() as conn:
            conn.execute(insert(self.table).values(id=1, embedding=vector))
        with self.engine.connect() as conn:
            stored = conn.execute(select(self.table.c.embedding)).scalar_one()
        self.assertEqual(stored, vector)

    def test_round_trip_preserves_none(self):
        with self.engine.begin() as conn:
            conn.execute(insert(self.table).values(id=1, embedding=None))
        with self.engine.connect() as conn:
            self.assertIsNone(conn.execute(select(self.table.c.embedding)).scalar_one())

    def test_tuple_input_comes_back_as_list_of_floats(self):
        """EmbeddingArtifact.vector e uma tupla; o tipo aceita e normaliza."""
        with self.engine.begin() as conn:
            conn.execute(insert(self.table).values(id=1, embedding=(1, 2, 3)))
        with self.engine.connect() as conn:
            stored = conn.execute(select(self.table.c.embedding)).scalar_one()
        self.assertEqual(stored, [1.0, 2.0, 3.0])


if __name__ == "__main__":
    unittest.main()
