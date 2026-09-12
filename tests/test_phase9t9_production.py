import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parent / "manual" / "phase9t9_production.py"
SPEC = importlib.util.spec_from_file_location("phase9t9_production", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
production = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(production)


def snapshot(*, revision="023_curriculum_taxonomy", parent_type="AREA", parent_active=True, node=None, historical=True):
    parent = {"code": "CHEMISTRY-PHYSICAL", "node_type": parent_type, "active": parent_active, "parent_code": "CHEMISTRY"}
    nodes = (parent,) if node is None else (parent, node)
    proposals = {
        number: {"input_hash": "input", "output_hash": "output"}
        for number in production.TARGET_QUESTIONS
    } if historical else {}
    return production.PreflightSnapshot(revision, nodes, proposals, {}, 0)


class Phase9T9ProductionTests(unittest.TestCase):
    def test_missing_environment_blocks_before_engine_creation(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "environment"):
                production.validate_environment()

    def test_non_postgresql_url_blocks(self):
        environment = {"DATABASE_URL": "sqlite://", "OPENAI_API_KEY": "key", "OPENAI_MODEL": "model"}
        with self.assertRaisesRegex(Exception, "PostgreSQL|postgresql"):
            production.validate_environment(environment)

    def test_preflight_rejects_revision_parent_node_and_history_failures(self):
        invalid_snapshots = (
            snapshot(revision="unexpected"),
            snapshot(parent_type="CONTENT"),
            snapshot(parent_active=False),
            snapshot(historical=False),
            snapshot(node={"code": "CHEMISTRY-PHYSICAL-KINETICS", "name": "wrong", "description": "wrong", "node_type": "CONTENT", "active": True, "parent_code": "CHEMISTRY-PHYSICAL"}),
        )
        for invalid in invalid_snapshots:
            with self.subTest(snapshot=invalid):
                with self.assertRaises(RuntimeError):
                    production.validate_preflight(invalid)

    def test_preflight_accepts_expected_state_without_side_effects(self):
        production.validate_preflight(snapshot())
        existing = {**production.NODE_EXPECTED, "parent_code": "CHEMISTRY-PHYSICAL"}
        production.validate_preflight(snapshot(node=existing))
        self.assertEqual(production.TARGET_QUESTIONS, (93, 128))
        self.assertTrue(production.MIGRATION_FILE.name.endswith("024_chemistry_kinetics.py"))

    def test_post_migration_allows_only_the_expected_node_delta(self):
        before = snapshot()
        node = {**production.NODE_EXPECTED, "parent_code": production.PARENT_CODE}
        after = production.PreflightSnapshot(
            production.TARGET_REVISION, (*before.catalog_nodes, node),
            before.historical_proposals, before.question_states, before.link_count,
        )
        production.validate_post_migration(before, after)
        changed_parent = {**before.catalog_nodes[0], "active": False}
        invalid_after = production.PreflightSnapshot(
            production.TARGET_REVISION, (changed_parent, node),
            before.historical_proposals, before.question_states, before.link_count,
        )
        with self.assertRaisesRegex(RuntimeError, "Existing CatalogNode changed"):
            production.validate_post_migration(before, invalid_after)

    def test_preflight_observations_report_only_safe_state(self):
        observed = production.preflight_observations(snapshot())
        self.assertEqual(observed["FOUND_REVISION"], production.SOURCE_REVISION)
        self.assertEqual(observed["CATALOG_NODE_COUNT"], 1)
        self.assertTrue(observed["PARENT_EXISTS"])
        self.assertFalse(observed["KINETICS_NODE_EXISTS"])
        self.assertTrue(observed["HISTORICAL_PROPOSALS"][93]["input_hash_present"])
        self.assertTrue(observed["HISTORICAL_PROPOSALS"][128]["output_hash_present"])