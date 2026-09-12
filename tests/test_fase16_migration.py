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

    def _recreate_test_database(self):
        """Drop and recreate the test database."""
        # Connect to default postgres database to drop/create the test DB
        # Use isolation_level=AUTOCOMMIT because DROP DATABASE must run outside transaction
        from psycopg import sql
        default_url = "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres"
        engine = create_engine(
            default_url,
            connect_args={"autocommit": True},
            execution_options={"isolation_level": "AUTOCOMMIT"}
        )
        
        with engine.connect() as conn:
            # Terminate existing connections to the test database
            conn.execute(text(
                "SELECT pg_terminate_backend(pg_stat_activity.pid) "
                "FROM pg_stat_activity "
                "WHERE datname = 'agente_ia_edu_test' AND pid <> pg_backend_pid();"
            ))
            
            # Drop test database if it exists
            conn.execute(text("DROP DATABASE IF EXISTS agente_ia_edu_test"))
            
            # Create fresh test database
            conn.execute(text("CREATE DATABASE agente_ia_edu_test"))

    def _drop_test_database(self):
        """Drop the test database."""
        default_url = "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres"
        engine = create_engine(
            default_url,
            connect_args={"autocommit": True},
            execution_options={"isolation_level": "AUTOCOMMIT"}
        )
        
        with engine.connect() as conn:
            conn.execute(text(
                "SELECT pg_terminate_backend(pg_stat_activity.pid) "
                "FROM pg_stat_activity "
                "WHERE datname = 'agente_ia_edu_test' AND pid <> pg_backend_pid();"
            ))
            conn.execute(text("DROP DATABASE IF EXISTS agente_ia_edu_test"))

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
