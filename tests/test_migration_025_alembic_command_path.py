"""Phase 9U.1-G — migration 025 through the *real* Alembic command path.

The original 40-char revision id failed in production on the final
``UPDATE alembic_version SET version_num=...`` step (``version_num`` is
``VARCHAR(32)``). The direct ``MigrationContext`` / ``Operations`` test harness
never touches ``alembic_version``, so this test drives the migration through
``alembic.command.upgrade`` / ``downgrade`` against a genuine, isolated,
throwaway PostgreSQL database (the local docker instance on :5433 - never
production), so the ``alembic_version`` write is exercised on a real
``VARCHAR(32)`` column.

Skips (does not fail) when the local PostgreSQL is unreachable.
"""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT / "migrations"
ALEMBIC_INI = ROOT / "alembic.ini"

REVISION_NEW = "025_classification_lifecycle"
DOWN_REVISION = "024_chemistry_kinetics"

LEGACY_DDL = """
CREATE TABLE pedagogical_classifications (
    id uuid PRIMARY KEY,
    question_version_id uuid NOT NULL,
    discipline varchar(255) NOT NULL,
    content varchar(255) NOT NULL,
    subcontent varchar(255) NOT NULL,
    difficulty varchar(30) NOT NULL,
    classification_confidence numeric(5,4),
    difficulty_confidence numeric(5,4),
    reasoning_type varchar(100) NOT NULL,
    prerequisites jsonb,
    keywords jsonb,
    competencies jsonb,
    skills jsonb,
    model_name varchar(255),
    model_version varchar(100),
    prompt_version varchar(100),
    provider_name varchar(100),
    input_tokens integer,
    output_tokens integer,
    total_tokens integer,
    status varchar(30) NOT NULL,
    source varchar(20) NOT NULL,
    created_at timestamptz NOT NULL,
    metadata jsonb
)
"""


def _pg_settings() -> dict[str, str] | None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return None
    values: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, val = line.split("=", 1)
            values[key] = val
    if not values.get("POSTGRES_USER") or not values.get("POSTGRES_PASSWORD"):
        return None
    return values


def _can_connect(settings: dict[str, str]) -> bool:
    try:
        import psycopg
    except Exception:
        return False
    try:
        with psycopg.connect(
            host="localhost", port=5433, user=settings["POSTGRES_USER"],
            password=settings["POSTGRES_PASSWORD"], dbname=settings.get("POSTGRES_DB", "postgres"),
            connect_timeout=5,
        ):
            return True
    except Exception:
        return False


_SETTINGS = _pg_settings()
_PG_OK = bool(_SETTINGS) and _can_connect(_SETTINGS)


@unittest.skipUnless(_PG_OK, "local isolated PostgreSQL (localhost:5433) is not reachable")
class Migration025AlembicCommandPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.psycopg = psycopg
        cls.settings = _SETTINGS
        cls.db_name = f"phase9u1g_test_{uuid.uuid4().hex[:12]}"
        cls._admin_dsn = (
            f"host=localhost port=5433 user={cls.settings['POSTGRES_USER']} "
            f"password={cls.settings['POSTGRES_PASSWORD']} "
            f"dbname={cls.settings.get('POSTGRES_DB', 'postgres')}"
        )
        with psycopg.connect(cls._admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{cls.db_name}"')
        cls.test_url = (
            f"postgresql+psycopg://{cls.settings['POSTGRES_USER']}:"
            f"{cls.settings['POSTGRES_PASSWORD']}@localhost:5433/{cls.db_name}"
        )
        cls.test_dsn = (
            f"host=localhost port=5433 user={cls.settings['POSTGRES_USER']} "
            f"password={cls.settings['POSTGRES_PASSWORD']} dbname={cls.db_name}"
        )

    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(cls._admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{cls.db_name}" WITH (FORCE)')

    def _config(self) -> Config:
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        config.set_main_option("sqlalchemy.url", self.test_url)
        return config

    def _query(self, sql: str, params=None):
        with self.psycopg.connect(self.test_dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()

    def _exec(self, sql: str, params=None):
        with self.psycopg.connect(self.test_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql, params or ())

    def test_revision_id_fits_alembic_version_column(self):
        self.assertLessEqual(len(REVISION_NEW), 32)

    def test_full_upgrade_downgrade_round_trip_through_command_path(self):
        # 1. legacy (pre-025) schema + Alembic stamped at 024, with a
        #    pre-existing duplicate (question_version_id, taxonomy_version) pair
        #    that the backfill must resolve, and one untagged row.
        self._exec(LEGACY_DDL)
        qv = str(uuid.uuid4())
        old_id, new_id, untagged_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        insert = (
            "INSERT INTO pedagogical_classifications "
            "(id, question_version_id, discipline, content, subcontent, difficulty, reasoning_type, "
            "status, source, created_at, metadata) VALUES "
            "(%s,%s,'CHEMISTRY',%s,'','UNKNOWN','UNSPECIFIED','CLASSIFIED','ai',%s,%s::jsonb)"
        )
        self._exec(insert, (old_id, qv, "CHEMISTRY-SOLUTIONS", "2026-01-01T00:00:00Z",
                            '{"taxonomy_version": "024_chemistry_kinetics", "input_hash": "a"}'))
        self._exec(insert, (new_id, qv, "CHEMISTRY-PHYSICAL-KINETICS", "2026-01-01T00:00:05Z",
                            '{"taxonomy_version": "024_chemistry_kinetics", "input_hash": "b"}'))
        self._exec(insert, (untagged_id, qv, "SOMETHING-ELSE", "2026-01-01T00:00:00Z",
                            '{"note": "no taxonomy_version key"}'))

        config = self._config()
        command.stamp(config, DOWN_REVISION)
        self.assertEqual(self._query("SELECT version_num FROM alembic_version"), [(DOWN_REVISION,)])

        # 2. THE test: real command.upgrade, which performs the
        #    UPDATE alembic_version ... that failed in production.
        command.upgrade(config, REVISION_NEW)

        # 3a. alembic_version updated on the real VARCHAR(32) column
        self.assertEqual(self._query("SELECT version_num FROM alembic_version"), [(REVISION_NEW,)])

        # 3b. columns
        cols = {r[0] for r in self._query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='pedagogical_classifications'"
        )}
        self.assertIn("lifecycle", cols)
        self.assertIn("supersedes_id", cols)

        # 3c. CHECK + FK constraints
        constraints = {r[0]: r[1] for r in self._query(
            "SELECT conname, contype FROM pg_constraint "
            "WHERE conrelid = 'pedagogical_classifications'::regclass"
        )}
        self.assertEqual(constraints.get("ck_pedagogical_classifications_lifecycle"), "c")
        self.assertEqual(constraints.get("fk_pedagogical_classifications_supersedes_id"), "f")

        # 3d. partial UNIQUE index
        idx = self._query(
            "SELECT i.indisunique, (i.indpred IS NOT NULL) AS is_partial "
            "FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE c.relname = 'uq_pedagogical_classifications_active_taxonomy'"
        )
        self.assertEqual(idx, [(True, True)])

        # 3e. deterministic backfill: older dup -> SUPERSEDED, newer -> ACTIVE,
        #     untagged (no taxonomy_version) -> untouched (default ACTIVE)
        lifecycle = {r[0]: r[1] for r in self._query(
            "SELECT id::text, lifecycle FROM pedagogical_classifications"
        )}
        self.assertEqual(lifecycle[old_id], "SUPERSEDED")
        self.assertEqual(lifecycle[new_id], "ACTIVE")
        self.assertEqual(lifecycle[untagged_id], "ACTIVE")
        self.assertEqual(
            self._query("SELECT count(*) FROM pedagogical_classifications"), [(3,)]
        )

        # 3f. the constraint is real: a 2nd ACTIVE row for the same key is rejected
        with self.assertRaises(self.psycopg.errors.UniqueViolation):
            self._exec(
                "INSERT INTO pedagogical_classifications "
                "(id, question_version_id, discipline, content, subcontent, difficulty, reasoning_type, "
                "status, source, created_at, metadata, lifecycle) VALUES "
                "(%s,%s,'CHEMISTRY','CHEMISTRY-SOLUTIONS','','UNKNOWN','UNSPECIFIED','CLASSIFIED','ai',"
                "%s,%s::jsonb,'ACTIVE')",
                (str(uuid.uuid4()), qv, "2026-01-02T00:00:00Z",
                 '{"taxonomy_version": "024_chemistry_kinetics", "input_hash": "dup"}'),
            )

        # 4. real command.downgrade -> schema reverts, alembic_version back to 024
        command.downgrade(config, DOWN_REVISION)
        self.assertEqual(self._query("SELECT version_num FROM alembic_version"), [(DOWN_REVISION,)])
        cols_after = {r[0] for r in self._query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='pedagogical_classifications'"
        )}
        self.assertNotIn("lifecycle", cols_after)
        self.assertNotIn("supersedes_id", cols_after)
        self.assertEqual(self._query(
            "SELECT count(*) FROM pg_constraint WHERE conrelid = 'pedagogical_classifications'::regclass "
            "AND conname IN ('ck_pedagogical_classifications_lifecycle', "
            "'fk_pedagogical_classifications_supersedes_id')"
        ), [(0,)])
        self.assertEqual(self._query(
            "SELECT count(*) FROM pg_class WHERE relname = 'uq_pedagogical_classifications_active_taxonomy'"
        ), [(0,)])

        # 5. no row lost, substantive content preserved across the round trip
        contents = {r[0]: r[1] for r in self._query(
            "SELECT id::text, content FROM pedagogical_classifications"
        )}
        self.assertEqual(contents[old_id], "CHEMISTRY-SOLUTIONS")
        self.assertEqual(contents[new_id], "CHEMISTRY-PHYSICAL-KINETICS")
        self.assertEqual(contents[untagged_id], "SOMETHING-ELSE")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
