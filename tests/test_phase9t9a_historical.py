import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parent / "manual" / "phase9t9a_historical.py"
SPEC = importlib.util.spec_from_file_location("phase9t9a_historical", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
historical = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(historical)


class Phase9T9AHistoricalTests(unittest.TestCase):
    def test_missing_database_url_blocks_before_engine(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                historical.validate_environment()

    def test_non_postgresql_url_blocks(self):
        with self.assertRaisesRegex(Exception, "PostgreSQL|postgresql"):
            historical.validate_environment({"DATABASE_URL": "sqlite://"})

    def test_targets_are_limited_to_historical_questions(self):
        self.assertEqual(historical.TARGET_QUESTIONS, (93, 128))
        self.assertEqual(historical.SOURCE_TAXONOMY_VERSION, "023_curriculum_taxonomy")