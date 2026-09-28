"""Validacao em PostgreSQL da migration 056 (envio em lote de redacao).

Ler a migration nao prova nada: uma coluna nova em tabela ja existente
(schools.logo_storage_uri) so aparece rodando o upgrade de verdade. Banco
descartavel na porta 5433, mesmo alvo dos outros testes de migration.
"""

from __future__ import annotations

import os
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests._postgres_test_db import create_database, drop_database


class TestEssayBatchMigrationPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_essay_batch_test"
    admin_url = os.getenv(
        "ESSAY_BATCH_TEST_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "ESSAY_BATCH_TEST_DATABASE_URL",
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
                "PostgreSQL de teste indisponivel; migration 056 nao validada."
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

    def test_upgrade_056_creates_batch_tables_and_logo_column(self):
        config = self._alembic_config()
        command.upgrade(config, "056_essay_batch_upload")

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            table_names = set(inspector.get_table_names())
            self.assertIn("essay_batch_uploads", table_names)
            self.assertIn("essay_batch_pages", table_names)

            school_columns = {c["name"] for c in inspector.get_columns("schools")}
            self.assertIn("logo_storage_uri", school_columns)

            upload_fks = {
                tuple(fk["constrained_columns"]): (fk["referred_table"], tuple(fk["referred_columns"]))
                for fk in inspector.get_foreign_keys("essay_batch_uploads")
            }
            self.assertEqual(
                upload_fks[("school_id", "essay_prompt_id")],
                ("essay_prompts", ("school_id", "id")),
            )
            self.assertEqual(
                upload_fks[("school_id", "class_id")], ("classes", ("school_id", "id"))
            )

            page_checks = {
                item["name"]: item["sqltext"]
                for item in inspector.get_check_constraints("essay_batch_pages")
            }
            self.assertIn("MATCHED_AUTO", page_checks["ck_essay_batch_pages_status"])
            self.assertIn("RESOLVED_MANUAL", page_checks["ck_essay_batch_pages_status"])

            page_fks = {
                tuple(fk["constrained_columns"]): fk
                for fk in inspector.get_foreign_keys("essay_batch_pages")
            }
            self.assertEqual(page_fks[("batch_id",)]["referred_table"], "essay_batch_uploads")
            self.assertEqual(page_fks[("batch_id",)]["options"]["ondelete"], "CASCADE")
            self.assertEqual(page_fks[("matched_student_id",)]["referred_table"], "students")
            self.assertEqual(
                page_fks[("essay_submission_id",)]["referred_table"], "essay_submissions"
            )
        finally:
            engine.dispose()

    def test_downgrade_056_is_clean(self):
        config = self._alembic_config()
        command.upgrade(config, "056_essay_batch_upload")
        command.downgrade(config, "055_essay_prompt_soft_delete")

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            table_names = set(inspector.get_table_names())
            self.assertNotIn("essay_batch_uploads", table_names)
            self.assertNotIn("essay_batch_pages", table_names)
            school_columns = {c["name"] for c in inspector.get_columns("schools")}
            self.assertNotIn("logo_storage_uri", school_columns)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
