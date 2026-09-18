"""Test Fase 16 migration for UserInvitation table using PostgreSQL.

IMPORTANT: This test validates migrations against PostgreSQL, which is the target
database. SQLite is not used for migration validation because:

1. Migrations may use PostgreSQL-specific types (e.g., JSONB) that are not portable.
2. The official target database is PostgreSQL.
3. Validating against PostgreSQL ensures production correctness.

This test:
- Uses the temporary PostgreSQL instance from docker-compose
- Recreates the test database for each test run
- Validates upgrade, downgrade, and re-upgrade behavior
- Inspects the schema to ensure correctness
- Cleans up the test database after completion
"""
import subprocess
import os
import unittest
from alembic.config import Config
from alembic import command
from sqlalchemy import create_engine, inspect, text

from tests._postgres_test_db import create_database, drop_database


class TestFase16Migration(unittest.TestCase):
    """Test UserInvitation migration against PostgreSQL.
    
    This test suite validates the Fase 16 migration (015_user_invitations)
    against a temporary PostgreSQL database. The database is recreated
    before each test to ensure a clean state.
    """

    TEST_DATABASE_URL = "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/agente_ia_edu_test"
    PRODUCTION_DATABASE_URL = "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/agente_ia_edu"

    @classmethod
    def setUpClass(cls):
        """Verify PostgreSQL is available."""
        try:
            engine = create_engine(cls.PRODUCTION_DATABASE_URL)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            print("\n✓ PostgreSQL is available for migration testing")
        except Exception as e:
            raise RuntimeError(
                f"PostgreSQL not available at {cls.PRODUCTION_DATABASE_URL}. "
                "Ensure docker-compose is running: docker-compose up -d postgres"
            ) from e

    def setUp(self):
        """Recreate the test database before each test."""
        self._recreate_test_database()

    def tearDown(self):
        """Drop the test database after each test."""
        self._drop_test_database()

    _ADMIN_URL = "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres"

    def _recreate_test_database(self):
        """Drop and recreate the test database."""
        drop_database(self._ADMIN_URL, "agente_ia_edu_test")
        create_database(self._ADMIN_URL, "agente_ia_edu_test")

    def _drop_test_database(self):
        """Drop the test database."""
        drop_database(self._ADMIN_URL, "agente_ia_edu_test")

    def test_migration_upgrade_creates_user_invitations_table(self):
        """Test that upgrade creates user_invitations table with correct schema."""
        # Configure Alembic to use the test database
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.TEST_DATABASE_URL)

        # Run upgrade to revision 015
        command.upgrade(config, "015_user_invitations")

        # Verify the table exists and has correct schema
        engine = create_engine(self.TEST_DATABASE_URL)
        inspector = inspect(engine)

        # Check table exists
        tables = inspector.get_table_names()
        self.assertIn("user_invitations", tables, "user_invitations table should exist after upgrade")

        # Check columns exist
        columns = {col["name"] for col in inspector.get_columns("user_invitations")}
        expected_columns = {
            "id",
            "school_id",
            "token",
            "external_email",
            "external_user_id",
            "display_name",
            "role",
            "scope_type",
            "scope_external_id",
            "status",
            "invited_by_external_id",
            "accepted_by_external_id",
            "activated_at",
            "expires_at",
            "expired_at",
            "metadata_",
            "created_at",
            "updated_at",
        }
        self.assertEqual(
            columns,
            expected_columns,
            f"Columns mismatch. Expected {expected_columns}, got {columns}"
        )

        # Check unique constraints
        unique_constraints = inspector.get_unique_constraints("user_invitations")
        constraint_names = {uc["name"] for uc in unique_constraints}
        self.assertIn(
            "uq_user_invitations_token",
            constraint_names,
            "uq_user_invitations_token unique constraint should exist"
        )
        self.assertIn(
            "uq_user_invitations_school_email_role",
            constraint_names,
            "uq_user_invitations_school_email_role unique constraint should exist"
        )

        # Check foreign keys
        foreign_keys = inspector.get_foreign_keys("user_invitations")
        fk_names = {fk["name"] for fk in foreign_keys}
        self.assertIn("user_invitations_school_id_fkey", fk_names)

        # Check indexes
        indexes = inspector.get_indexes("user_invitations")
        index_names = {idx["name"] for idx in indexes}
        expected_indexes = {
            "ix_user_invitations_school_id",
            "ix_user_invitations_status",
            "ix_user_invitations_expires_at",
            "ix_user_invitations_created_at",
        }
        for exp_idx in expected_indexes:
            self.assertIn(exp_idx, index_names, f"Index {exp_idx} should exist")

    def test_migration_downgrade_removes_table(self):
        """Test that downgrade removes user_invitations table."""
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.TEST_DATABASE_URL)

        # Upgrade to 015
        command.upgrade(config, "015_user_invitations")

        engine = create_engine(self.TEST_DATABASE_URL)
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        self.assertIn("user_invitations", tables, "Table should exist after upgrade")

        # Downgrade to 014
        command.downgrade(config, "014_material_school_scope")

        inspector = inspect(engine)
        tables = inspector.get_table_names()
        self.assertNotIn("user_invitations", tables, "Table should not exist after downgrade")

    def test_migration_reupgrade_recreates_table(self):
        """Test that re-upgrade after downgrade recreates user_invitations table."""
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.TEST_DATABASE_URL)

        # Upgrade to 015
        command.upgrade(config, "015_user_invitations")
        engine = create_engine(self.TEST_DATABASE_URL)
        inspector = inspect(engine)
        self.assertIn("user_invitations", inspector.get_table_names())

        # Downgrade to 014
        command.downgrade(config, "014_material_school_scope")
        inspector = inspect(engine)
        self.assertNotIn("user_invitations", inspector.get_table_names())

        # Re-upgrade to 015
        command.upgrade(config, "015_user_invitations")
        inspector = inspect(engine)
        self.assertIn("user_invitations", inspector.get_table_names(), "Table should be recreated after re-upgrade")


if __name__ == "__main__":
    unittest.main()
