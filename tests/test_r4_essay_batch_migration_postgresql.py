"""Validacao em PostgreSQL da migration 057 (envio em lote de redacao).

Ler a migration nao prova nada: uma coluna nova em tabela ja existente
(schools.logo_storage_uri) so aparece rodando o upgrade de verdade. Banco
descartavel na porta 5433, mesmo alvo dos outros testes de migration.

Subir direto de um banco vazio passa por 024_chemistry_kinetics, que e
migration de DADOS (nao so de schema): ela insere um CONTENT sob o AREA
CHEMISTRY-PHYSICAL e falha de proposito (RuntimeError) se esse pai nao
existir - protecao deliberada contra inserir um no orfao, nao um bug.
Em todo ambiente real (banco compartilhado, producao) esse pai ja existe
porque o script de seed de taxonomia roda antes. Um upgrade a partir de
um banco genuinamente vazio (como este teste faz) precisa fazer esse
mesmo seed primeiro - exatamente o padrao ja estabelecido em
tests/test_alembic_reconciliacao_063.py (`_subir`/`_semear`), que usa o
service oficial e idempotente CurriculumTaxonomyService.seed_reference_fixture()
em vez de inserir dados a mao."""

from __future__ import annotations

import asyncio
import os
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService
from tests._postgres_test_db import create_database, drop_database

_ANTES_DO_SEED_DE_TAXONOMIA = "023_curriculum_taxonomy"


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

    async def _seed_taxonomy_baseline(self) -> None:
        engine = create_async_engine(self.database_url)
        try:
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                await CurriculumTaxonomyService(session).seed_reference_fixture()
        finally:
            await engine.dispose()

    def _upgrade_through_024_with_taxonomy_seed(self, target: str) -> None:
        """Sobe de um banco vazio ate `target`, semeando o catalogo de
        taxonomia no ponto exato em que 024_chemistry_kinetics precisa dele
        (ela e migration de DADOS, nao so de schema - ver docstring do
        modulo). Mesmo padrao de tests/test_alembic_reconciliacao_063.py."""
        config = self._alembic_config()
        command.upgrade(config, _ANTES_DO_SEED_DE_TAXONOMIA)
        asyncio.run(self._seed_taxonomy_baseline())
        command.upgrade(config, target)

    def test_upgrade_057_creates_batch_tables_and_logo_column(self):
        self._upgrade_through_024_with_taxonomy_seed("057_essay_batch_upload")

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

    def test_downgrade_057_is_clean(self):
        self._upgrade_through_024_with_taxonomy_seed("057_essay_batch_upload")
        config = self._alembic_config()
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
