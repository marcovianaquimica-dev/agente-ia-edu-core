"""PostgreSQL-only migration proof for the Phase 7 assessment core delta."""

import os
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


class Phase7AssessmentMigrationPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_phase7_migration_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "PHASE7_MIGRATION_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "PHASE7_MIGRATION_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest("PostgreSQL indisponivel para migration Phase 7") from exc

    def setUp(self):
        self._drop_database()
        self._admin_execute(f"CREATE DATABASE {self.database_name}")

    def tearDown(self):
        self._drop_database()

    @classmethod
    def _admin_execute(cls, statement):
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
        try:
            cls._admin_execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{cls.database_name}' AND pid <> pg_backend_pid()"
            )
            cls._admin_execute(f"DROP DATABASE IF EXISTS {cls.database_name}")
        except Exception:
            pass

    def config(self):
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.database_url)
        return config

    @staticmethod
    def column_names(inspector, table):
        return {item["name"] for item in inspector.get_columns(table)}

    @staticmethod
    def check_sql(inspector, table, name):
        checks = {item["name"]: item["sqltext"] for item in inspector.get_check_constraints(table)}
        return checks[name]

    def test_upgrade_downgrade_reupgrade(self):
        config = self.config()
        command.upgrade(config, "020_assessment_core_alignment")
        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertTrue({
                "school_id", "owner_external_id", "visibility_scope", "origin_type",
                "scope_type", "scope_external_id", "metadata",
            }.issubset(self.column_names(inspector, "assessments")))
            self.assertTrue({
                "frozen_correct_option_id", "answer_key_revision_id",
            }.issubset(self.column_names(inspector, "assessment_items")))
            self.assertIn("publication_id", self.column_names(inspector, "assessment_assignments"))
            self.assertTrue({"assignment_id", "metadata"}.issubset(
                self.column_names(inspector, "assessment_attempts")
            ))
            self.assertIn(
                "approved", self.check_sql(inspector, "assessment_versions", "ck_assessment_versions_status")
            )
            self.assertIn(
                "UNIT", self.check_sql(inspector, "assessment_assignments", "ck_assessment_assignments_recipient_type")
            )
            assignment_fks = {
                tuple(item["constrained_columns"]): item["referred_table"]
                for item in inspector.get_foreign_keys("assessment_assignments")
            }
            self.assertEqual(assignment_fks[("publication_id",)], "assessment_publications")
            item_fks = {
                tuple(item["constrained_columns"]): item["referred_table"]
                for item in inspector.get_foreign_keys("assessment_items")
            }
            self.assertEqual(item_fks[("frozen_correct_option_id",)], "question_options")
            self.assertEqual(item_fks[("answer_key_revision_id",)], "answer_key_revisions")
        finally:
            engine.dispose()

        command.downgrade(config, "019_reception_candidates")
        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertNotIn("school_id", self.column_names(inspector, "assessments"))
            self.assertNotIn("frozen_correct_option_id", self.column_names(inspector, "assessment_items"))
            self.assertNotIn("publication_id", self.column_names(inspector, "assessment_assignments"))
            self.assertNotIn("assignment_id", self.column_names(inspector, "assessment_attempts"))
        finally:
            engine.dispose()

        command.upgrade(config, "020_assessment_core_alignment")
        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertIn("assignment_id", self.column_names(inspector, "assessment_attempts"))
            self.assertIn("frozen_correct_option_id", self.column_names(inspector, "assessment_items"))
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
