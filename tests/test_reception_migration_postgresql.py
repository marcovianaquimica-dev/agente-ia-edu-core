"""PostgreSQL-only validation for reception migration 019."""

from __future__ import annotations

import os
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests._postgres_test_db import create_database, drop_database


class TestReceptionMigrationPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_reception_test"
    admin_url = os.getenv(
        "RECEPTION_TEST_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "RECEPTION_TEST_DATABASE_URL",
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
                "PostgreSQL de teste indisponível; migration 019 não validada."
            ) from exc

    def setUp(self):
        self._drop_database()
        create_database(self.admin_url, self.database_name)

    def tearDown(self):
        self._drop_database()

    @classmethod
    def _admin_execute(cls, statement: str):
        engine = create_engine(
            cls.admin_url,
            connect_args={"autocommit": True},
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        try:
            with engine.connect() as connection:
                connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    def _drop_database(cls):
        drop_database(cls.admin_url, cls.database_name)

    def _alembic_config(self) -> Config:
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.database_url)
        return config

    @staticmethod
    def _check_sql(inspector, table_name: str, constraint_name: str) -> str:
        constraints = {
            item["name"]: item["sqltext"]
            for item in inspector.get_check_constraints(table_name)
        }
        return constraints[constraint_name]

    def test_upgrade_and_downgrade_019_contract(self):
        config = self._alembic_config()
        command.upgrade(config, "019_reception_candidates")

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertIn("reception_candidates", inspector.get_table_names())
            invitation_columns = {
                item["name"]: item for item in inspector.get_columns("user_invitations")
            }
            self.assertIn("metadata", invitation_columns)
            self.assertNotIn("metadata_", invitation_columns)
            self.assertEqual(str(invitation_columns["metadata"]["type"]).upper(), "JSONB")
            self.assertIn(
                "SECRETARY",
                self._check_sql(inspector, "user_school_links", "ck_user_school_links_role"),
            )
            self.assertIn(
                "SECRETARY",
                self._check_sql(inspector, "user_invitations", "ck_user_invitations_role"),
            )
            unique_names = {
                item["name"]
                for item in inspector.get_unique_constraints("reception_candidates")
            }
            self.assertTrue(
                {
                    "uq_reception_candidates_school_email",
                    "uq_reception_candidates_school_phone",
                    "uq_reception_candidates_invitation_id",
                }.issubset(unique_names)
            )
            index_names = {
                item["name"] for item in inspector.get_indexes("reception_candidates")
            }
            self.assertTrue(
                {
                    "ix_reception_candidates_school_created",
                    "ix_reception_candidates_school_name",
                    "ix_reception_candidates_email",
                    "ix_reception_candidates_phone",
                }.issubset(index_names)
            )
            foreign_keys = {
                tuple(item["constrained_columns"]): (
                    item["referred_table"], tuple(item["referred_columns"])
                )
                for item in inspector.get_foreign_keys("reception_candidates")
            }
            self.assertEqual(foreign_keys[("school_id",)], ("schools", ("id",)))
            self.assertEqual(
                foreign_keys[("invitation_id",)], ("user_invitations", ("id",))
            )
        finally:
            engine.dispose()

        command.upgrade(config, "019_reception_candidates")
        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertIn("reception_candidates", inspector.get_table_names())
            invitation_columns = {
                item["name"]: item for item in inspector.get_columns("user_invitations")
            }
            self.assertIn("metadata", invitation_columns)
            self.assertEqual(str(invitation_columns["metadata"]["type"]).upper(), "JSONB")
            self.assertIn(
                "SECRETARY",
                self._check_sql(inspector, "user_school_links", "ck_user_school_links_role"),
            )
        finally:
            engine.dispose()

        command.downgrade(config, "018_pedagogical_universe")
        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertNotIn("reception_candidates", inspector.get_table_names())
            invitation_columns = {
                item["name"]: item for item in inspector.get_columns("user_invitations")
            }
            self.assertIn("metadata_", invitation_columns)
            self.assertNotIn("metadata", invitation_columns)
            self.assertNotIn(
                "SECRETARY",
                self._check_sql(inspector, "user_school_links", "ck_user_school_links_role"),
            )
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
