"""Validacao em PostgreSQL da migration 059 (mass_correction_runs)."""

from __future__ import annotations

import os
import unittest
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests._postgres_test_db import create_database, drop_database


class TestMassCorrectionRunsMigrationPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_mass_correction_test"
    admin_url = os.getenv(
        "MASS_CORRECTION_TEST_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "MASS_CORRECTION_TEST_DATABASE_URL",
        f"postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            engine = create_engine(
                cls.admin_url, connect_args={"autocommit": True},
                execution_options={"isolation_level": "AUTOCOMMIT"},
            )
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as exc:
            raise unittest.SkipTest(
                "PostgreSQL de teste indisponivel; migration 059 nao validada."
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

    def test_upgrade_059_creates_the_table(self):
        config = self._alembic_config()
        # Run migrations until 023 (before 024 which requires catalog seed)
        command.upgrade(config, "023_curriculum_taxonomy")

        # Seed catalog before running migrations 024+
        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                self._seed_catalog(connection)
        finally:
            engine.dispose()

        # Continue with remaining migrations
        command.upgrade(config, "059_mass_correction_runs")

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertIn("mass_correction_runs", inspector.get_table_names())
            columns = {c["name"] for c in inspector.get_columns("mass_correction_runs")}
            self.assertEqual(
                columns,
                {
                    "id", "school_id", "stage", "sequence_number", "openai_batch_id",
                    "input_file_id", "output_file_id", "request_count", "status",
                    "created_at", "updated_at", "completed_at",
                },
            )
        finally:
            engine.dispose()

    def test_downgrade_059_is_clean(self):
        config = self._alembic_config()
        # Run migrations until 023 (before 024 which requires catalog seed)
        command.upgrade(config, "023_curriculum_taxonomy")

        # Seed catalog before running migrations 024+
        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                self._seed_catalog(connection)
        finally:
            engine.dispose()

        # Continue with remaining migrations
        command.upgrade(config, "059_mass_correction_runs")
        command.downgrade(config, "058_platform_essay_prompts")

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertNotIn("mass_correction_runs", inspector.get_table_names())
        finally:
            engine.dispose()

    def _insert_run_with_status(self, connection, status: str) -> None:
        connection.execute(
            text(
                "INSERT INTO mass_correction_runs "
                "(id, school_id, stage, sequence_number, request_count, status, "
                "created_at, updated_at) "
                "VALUES (:id, :school_id, 'OCR', 1, 0, :status, now(), now())"
            ),
            {"id": uuid.uuid4(), "school_id": uuid.uuid4(), "status": status},
        )

    def test_upgrade_060_widens_the_status_constraint(self):
        config = self._alembic_config()
        command.upgrade(config, "023_curriculum_taxonomy")

        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                self._seed_catalog(connection)
        finally:
            engine.dispose()

        command.upgrade(config, "060_mass_correction_cancelling")

        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                self._insert_run_with_status(connection, "cancelling")
        finally:
            engine.dispose()

    def test_downgrade_060_restores_the_old_constraint(self):
        config = self._alembic_config()
        command.upgrade(config, "023_curriculum_taxonomy")

        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                self._seed_catalog(connection)
        finally:
            engine.dispose()

        command.upgrade(config, "060_mass_correction_cancelling")
        command.downgrade(config, "059_mass_correction_runs")

        engine = create_engine(self.database_url)
        try:
            with engine.connect() as connection:
                with self.assertRaises(Exception):
                    with connection.begin():
                        self._insert_run_with_status(connection, "cancelling")
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
