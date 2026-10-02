"""Validacao em PostgreSQL da migration 058 (propostas da plataforma).

Ler a migration nao prova nada: a coluna nova numa tabela ja existente
(essay_prompts.materialized_from_platform_prompt_id) e, principalmente, a
UNIQUE (school_id, materialized_from_platform_prompt_id) - de que o
buscar-ou-criar idempotente depende - so aparecem rodando o upgrade de
verdade. Banco descartavel na porta 5433, mesmo alvo dos outros testes de
migration.
"""

from __future__ import annotations

import os
import unittest
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests._postgres_test_db import create_database, drop_database


class TestPlatformEssayPromptsMigrationPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_platform_essay_prompts_test"
    admin_url = os.getenv(
        "PLATFORM_ESSAY_PROMPTS_TEST_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "PLATFORM_ESSAY_PROMPTS_TEST_DATABASE_URL",
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
                "PostgreSQL de teste indisponivel; migration 058 nao validada."
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
        """Seed minimal catalog taxonomy needed for migration 024."""
        from datetime import datetime, timezone

        # Create minimal catalog structure that migration 024 expects
        catalog_spec = [
            ("CHEMISTRY", None, "DISCIPLINE", "Química", 1),
            ("CHEMISTRY-PHYSICAL", "CHEMISTRY", "AREA", "Físico-Química", 1),
        ]

        for code, parent_code, node_type, name, position in catalog_spec:
            parent_id = None
            root_id = None
            if parent_code:
                parent_row = connection.execute(
                    text("SELECT id, root_id FROM catalog_nodes WHERE code = :code"),
                    {"code": parent_code}
                ).mappings().first()
                if parent_row:
                    parent_id = parent_row["id"]
                    root_id = parent_row["root_id"] or parent_row["id"]

            existing = connection.execute(
                text("SELECT id FROM catalog_nodes WHERE code = :code"),
                {"code": code}
            ).mappings().first()

            if not existing:
                connection.execute(
                    text(
                        "INSERT INTO catalog_nodes "
                        "(id, parent_id, root_id, node_type, code, name, position, active, created_at, updated_at) "
                        "VALUES (:id, :parent_id, :root_id, :node_type, :code, :name, :position, true, now(), now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "parent_id": parent_id,
                        "root_id": root_id or (parent_id if root_id is not None else None),
                        "node_type": node_type,
                        "code": code,
                        "name": name,
                        "position": position,
                    }
                )

    def _seed_school_and_origin(self, connection):
        school_id = uuid.uuid4()
        origin_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO schools (id, code, name, status, created_at, updated_at) "
                "VALUES (:id, :code, :name, 'ACTIVE', now(), now())"
            ),
            {"id": school_id, "code": f"PEPM-{str(school_id)[:8]}", "name": "Escola de teste"},
        )
        connection.execute(
            text(
                "INSERT INTO platform_essay_prompts "
                "(id, title, statement, status, created_by_external_identity, created_at) "
                "VALUES (:id, 'Tema', 'Disserte.', 'ACTIVE', 'user:ADMIN', now())"
            ),
            {"id": origin_id},
        )
        return school_id, origin_id

    @staticmethod
    def _insert_prompt_sql() -> str:
        return (
            "INSERT INTO essay_prompts "
            "(id, school_id, title, statement, year, status, is_free_theme, "
            " created_by_external_identity, created_at, updated_at, "
            " materialized_from_platform_prompt_id) "
            "VALUES (:id, :school_id, 'Tema', 'Disserte.', 2026, 'ACTIVE', false, "
            "        'teacher:p1', now(), now(), :origin_id)"
        )

    def test_upgrade_058_creates_table_column_and_unique_constraint(self):
        config = self._alembic_config()
        # Run migrations until 023 (before 024 which requires catalog seed)
        command.upgrade(config, "023_curriculum_taxonomy")

        # Seed catalog before running migrations 024+ (needed for migration 024)
        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                self._seed_catalog(connection)
        finally:
            engine.dispose()

        # Continue with remaining migrations
        command.upgrade(config, "058_platform_essay_prompts")

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertIn("platform_essay_prompts", set(inspector.get_table_names()))

            prompt_columns = {c["name"] for c in inspector.get_columns("essay_prompts")}
            self.assertIn("materialized_from_platform_prompt_id", prompt_columns)

            status_checks = {
                item["name"]: item["sqltext"]
                for item in inspector.get_check_constraints("platform_essay_prompts")
            }
            self.assertIn("ACTIVE", status_checks["ck_platform_essay_prompts_status"])
            self.assertIn("ARCHIVED", status_checks["ck_platform_essay_prompts_status"])

            uniques = {
                item["name"]: tuple(item["column_names"])
                for item in inspector.get_unique_constraints("essay_prompts")
            }
            self.assertEqual(
                uniques["uq_essay_prompts_school_materialized_from"],
                ("school_id", "materialized_from_platform_prompt_id"),
            )
            # A trava composta que todo o resto do sistema referencia continua
            # existindo exatamente como estava (Global Constraints).
            self.assertEqual(uniques["uq_essay_prompts_school_id_id"], ("school_id", "id"))

            fks = {
                tuple(fk["constrained_columns"]): fk
                for fk in inspector.get_foreign_keys("essay_prompts")
            }
            origin_fk = fks[("materialized_from_platform_prompt_id",)]
            self.assertEqual(origin_fk["referred_table"], "platform_essay_prompts")
            self.assertEqual(origin_fk["options"]["ondelete"], "RESTRICT")
        finally:
            engine.dispose()

    def test_unique_constraint_rejects_a_second_materialization_of_the_same_origin(self):
        config = self._alembic_config()
        command.upgrade(config, "023_curriculum_taxonomy")
        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                self._seed_catalog(connection)
        finally:
            engine.dispose()

        command.upgrade(config, "058_platform_essay_prompts")

        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                school_id, origin_id = self._seed_school_and_origin(connection)
                connection.execute(
                    text(self._insert_prompt_sql()),
                    {"id": uuid.uuid4(), "school_id": school_id, "origin_id": origin_id},
                )
            with engine.begin() as connection:
                with self.assertRaises(IntegrityError):
                    connection.execute(
                        text(self._insert_prompt_sql()),
                        {"id": uuid.uuid4(), "school_id": school_id, "origin_id": origin_id},
                    )
        finally:
            engine.dispose()

    def test_unique_constraint_never_blocks_normal_teacher_prompts(self):
        config = self._alembic_config()
        command.upgrade(config, "023_curriculum_taxonomy")
        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                self._seed_catalog(connection)
        finally:
            engine.dispose()

        command.upgrade(config, "058_platform_essay_prompts")

        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                school_id, _ = self._seed_school_and_origin(connection)
                for _ in range(3):
                    connection.execute(
                        text(self._insert_prompt_sql()),
                        {"id": uuid.uuid4(), "school_id": school_id, "origin_id": None},
                    )
                total = connection.execute(
                    text(
                        "SELECT count(*) FROM essay_prompts "
                        "WHERE school_id = :school_id "
                        "AND materialized_from_platform_prompt_id IS NULL"
                    ),
                    {"school_id": school_id},
                ).scalar_one()
            self.assertEqual(total, 3)
        finally:
            engine.dispose()

    def test_downgrade_058_is_clean(self):
        config = self._alembic_config()
        command.upgrade(config, "023_curriculum_taxonomy")
        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                self._seed_catalog(connection)
        finally:
            engine.dispose()

        command.upgrade(config, "058_platform_essay_prompts")
        command.downgrade(config, "057_essay_batch_upload")

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertNotIn("platform_essay_prompts", set(inspector.get_table_names()))
            prompt_columns = {c["name"] for c in inspector.get_columns("essay_prompts")}
            self.assertNotIn("materialized_from_platform_prompt_id", prompt_columns)
            uniques = {item["name"] for item in inspector.get_unique_constraints("essay_prompts")}
            self.assertNotIn("uq_essay_prompts_school_materialized_from", uniques)
            self.assertIn("uq_essay_prompts_school_id_id", uniques)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
