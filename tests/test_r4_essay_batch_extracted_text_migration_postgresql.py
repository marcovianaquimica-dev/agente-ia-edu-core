"""Validacao em PostgreSQL da migration 064 (essay_batch_pages ganha
extracted_pdf_text).

Puramente aditiva - mas ler a migration nao prova que a coluna aceita NULL
nem que o downgrade remove ela de verdade. Banco descartavel na porta 5433,
mesmo padrao dos outros testes de migration desta suite.
"""

from __future__ import annotations

import os
import unittest
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests._postgres_test_db import create_database, drop_database


class TestEssayBatchExtractedTextMigrationPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_batch_extracted_text_test"
    admin_url = os.getenv(
        "BATCH_EXTRACTED_TEXT_TEST_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "BATCH_EXTRACTED_TEXT_TEST_DATABASE_URL",
        f"postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            engine = create_engine(
                cls.admin_url,
                connect_args={"autocommit": True},
                execution_options={"isolation_level": "AUTOCOMMIT"},
            )
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as exc:
            raise unittest.SkipTest(
                "PostgreSQL de teste indisponivel; migration 064 nao validada."
            ) from exc

    def setUp(self):
        drop_database(self.admin_url, self.database_name)
        create_database(self.admin_url, self.database_name)

    def tearDown(self):
        drop_database(self.admin_url, self.database_name)

    def _alembic_config(self) -> Config:
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.database_url)
        return config

    def _seed_catalog(self, connection):
        """024_chemistry_kinetics e uma DATA migration que exige o no de
        catalogo CHEMISTRY-PHYSICAL (AREA) ja existir - mesmo contorno
        minimo ja usado em outros testes de migration desta suite."""
        catalog_spec = [
            ("CHEMISTRY", None, "DISCIPLINE", "Quimica", 1),
            ("CHEMISTRY-PHYSICAL", "CHEMISTRY", "AREA", "Fisico-Quimica", 1),
        ]
        for code, parent_code, node_type, name, position in catalog_spec:
            parent_id = None
            root_id = None
            if parent_code:
                parent_row = connection.execute(
                    text("SELECT id, root_id FROM catalog_nodes WHERE code = :code"),
                    {"code": parent_code},
                ).mappings().first()
                if parent_row:
                    parent_id = parent_row["id"]
                    root_id = parent_row["root_id"] or parent_row["id"]
            existing = connection.execute(
                text("SELECT id FROM catalog_nodes WHERE code = :code"),
                {"code": code},
            ).mappings().first()
            if not existing:
                connection.execute(
                    text(
                        "INSERT INTO catalog_nodes "
                        "(id, parent_id, root_id, node_type, code, name, position, active, created_at, updated_at) "
                        "VALUES (:id, :parent_id, :root_id, :node_type, :code, :name, :position, true, now(), now())"
                    ),
                    {
                        "id": uuid.uuid4(), "parent_id": parent_id,
                        "root_id": root_id or parent_id, "node_type": node_type,
                        "code": code, "name": name, "position": position,
                    },
                )

    def _upgrade_to(self, config: Config, target: str) -> None:
        command.upgrade(config, "023_curriculum_taxonomy")
        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                self._seed_catalog(connection)
        finally:
            engine.dispose()
        command.upgrade(config, target)

    def _seed_batch_page(self, connection) -> uuid.UUID:
        school_id = uuid.uuid4()
        connection.execute(text(
            "INSERT INTO schools (id, code, name, status, created_at, updated_at) "
            "VALUES (:id, 'SC-064', 'Escola 064', 'ACTIVE', now(), now())"
        ), {"id": school_id})
        prompt_id = uuid.uuid4()
        connection.execute(text(
            "INSERT INTO essay_prompts "
            "(id, school_id, title, statement, year, created_by_external_identity, created_at, updated_at) "
            "VALUES (:id, :school_id, 'Tema', 'Disserte.', 2026, 'prof', now(), now())"
        ), {"id": prompt_id, "school_id": school_id})
        batch_id = uuid.uuid4()
        connection.execute(text(
            "INSERT INTO essay_batch_uploads "
            "(id, school_id, essay_prompt_id, uploaded_by_external_identity, status, total_pages, created_at, updated_at) "
            "VALUES (:id, :school_id, :prompt_id, 'prof', 'PROCESSING', 1, now(), now())"
        ), {"id": batch_id, "school_id": school_id, "prompt_id": prompt_id})
        page_id = uuid.uuid4()
        connection.execute(text(
            "INSERT INTO essay_batch_pages "
            "(id, batch_id, page_number, storage_uri, status, created_at, updated_at) "
            "VALUES (:id, :batch_id, 1, 'var/page1.png', 'NEEDS_REVIEW', now(), now())"
        ), {"id": page_id, "batch_id": batch_id})
        return page_id

    def test_upgrade_064_extracted_pdf_text_existe_e_aceita_null(self):
        config = self._alembic_config()
        self._upgrade_to(config, "064_essay_batch_extracted_text")

        engine = create_engine(self.database_url)
        inspector = inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("essay_batch_pages")}
        self.assertIn("extracted_pdf_text", columns)
        self.assertTrue(columns["extracted_pdf_text"]["nullable"])

        with engine.begin() as connection:
            page_id = self._seed_batch_page(connection)
            value = connection.execute(
                text("SELECT extracted_pdf_text FROM essay_batch_pages WHERE id = :id"),
                {"id": page_id},
            ).scalar()
            self.assertIsNone(value)

            connection.execute(
                text(
                    "UPDATE essay_batch_pages SET extracted_pdf_text = :text WHERE id = :id"
                ),
                {"id": page_id, "text": "Texto digital extraido do PDF."},
            )
            value = connection.execute(
                text("SELECT extracted_pdf_text FROM essay_batch_pages WHERE id = :id"),
                {"id": page_id},
            ).scalar()
            self.assertEqual(value, "Texto digital extraido do PDF.")
        engine.dispose()

    def test_downgrade_064_remove_extracted_pdf_text(self):
        config = self._alembic_config()
        self._upgrade_to(config, "064_essay_batch_extracted_text")
        command.downgrade(config, "063_platform_material_target")

        engine = create_engine(self.database_url)
        inspector = inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("essay_batch_pages")}
        self.assertNotIn("extracted_pdf_text", columns)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
