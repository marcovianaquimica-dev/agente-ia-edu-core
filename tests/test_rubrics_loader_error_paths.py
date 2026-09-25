"""Coverage for the structural-validation error branches in
src/agente_ia_edu/rubrics/loader.py.

The module's docstring says its whole job is to "refuse a file that is
structurally incomplete, so a half-transcribed rubric can never reach the
seed" - but tests/test_r1_rubric_file.py and friends only ever exercise the
real, valid enem_2025.yaml, so none of the refusal branches (RubricFileError)
themselves had a test. This file builds a minimal valid rubric mapping and
then mutates it, one structural defect at a time, to prove each guard
actually fires - and fires with the right, human-actionable message.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.rubrics.loader import (
    RubricFileError,
    load_rubric_file,
    parse_rubric_mapping,
)


def _level(points: int, page: int = 10) -> dict:
    return {"points": points, "descriptor": f"descriptor for {points}", "source_page": page}


def _competency(code: str, ordinal: int) -> dict:
    return {
        "code": code,
        "ordinal": ordinal,
        "official_title": f"Official title for {code}",
        "source_page": 1,
        "levels": [_level(p) for p in (0, 40, 80, 120, 160, 200)],
        "signals": [],
    }


def _valid_rubric() -> dict:
    return {
        "rubric_version": "TEST_1",
        "label": "Test rubric",
        "effective_year": 2026,
        "max_total_points": 1000,
        "official_source_title": "Test source",
        "official_source_url": "https://example.org/test.pdf",
        "official_source_sha256": "deadbeef",
        "competencies": [
            _competency(code, ordinal)
            for ordinal, code in enumerate(("C1", "C2", "C3", "C4", "C5"), start=1)
        ],
        "scoring_rules": [],
    }


class TestValidRubricSanityCheck(unittest.TestCase):
    """Confirms the fixture itself is accepted, so every failure below is
    attributable to the specific mutation, not to a broken fixture."""

    def test_the_minimal_fixture_parses_cleanly(self):
        rubric = parse_rubric_mapping(_valid_rubric())
        self.assertEqual([c.code for c in rubric.competencies], ["C1", "C2", "C3", "C4", "C5"])


class TestLoadRubricFile(unittest.TestCase):
    def test_unknown_rubric_name_raises(self):
        with self.assertRaises(RubricFileError) as ctx:
            load_rubric_file("does_not_exist_as_a_rubric")
        self.assertIn("Unknown rubric file", str(ctx.exception))


class TestParseRubricMappingTopLevel(unittest.TestCase):
    def test_non_mapping_input_raises(self):
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(["not", "a", "mapping"])
        self.assertIn("must be a mapping", str(ctx.exception))

    def test_wrong_competency_codes_or_order_raises(self):
        raw = _valid_rubric()
        # Drop C5 so the declared codes no longer match COMPETENCY_CODES.
        raw["competencies"] = raw["competencies"][:4]
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("must declare", str(ctx.exception))

    def test_missing_required_top_level_field_raises(self):
        raw = _valid_rubric()
        del raw["rubric_version"]
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("Missing required field 'rubric_version'", str(ctx.exception))


class TestParseCompetency(unittest.TestCase):
    def test_non_mapping_competency_entry_raises(self):
        raw = _valid_rubric()
        raw["competencies"][0] = "not a mapping"
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("Each competency must be a mapping", str(ctx.exception))


class TestParseLevel(unittest.TestCase):
    def test_non_mapping_level_entry_raises(self):
        raw = _valid_rubric()
        raw["competencies"][0]["levels"][0] = "not a mapping"
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("Each level must be a mapping", str(ctx.exception))

    def test_missing_source_page_on_a_level_raises(self):
        raw = _valid_rubric()
        del raw["competencies"][0]["levels"][0]["source_page"]
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("must cite a positive source_page", str(ctx.exception))

    def test_non_positive_source_page_on_a_level_raises(self):
        raw = _valid_rubric()
        raw["competencies"][0]["levels"][0]["source_page"] = 0
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("must cite a positive source_page", str(ctx.exception))


class TestParseSignal(unittest.TestCase):
    def _rubric_with_signal(self, signal: dict) -> dict:
        raw = _valid_rubric()
        raw["competencies"][0]["signals"] = [signal]
        return raw

    def test_non_mapping_signal_entry_raises(self):
        raw = self._rubric_with_signal("not a mapping")
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("Each signal must be a mapping", str(ctx.exception))

    def test_unknown_provenance_raises(self):
        raw = self._rubric_with_signal(
            {
                "key": "sig1",
                "label": "Signal 1",
                "provenance": "MADE_UP_PROVENANCE",
                "source_ref": "p.1",
            }
        )
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("Unknown provenance", str(ctx.exception))

    def test_engine_heuristic_signal_without_rationale_raises(self):
        raw = self._rubric_with_signal(
            {
                "key": "sig1",
                "label": "Signal 1",
                "provenance": "HEURISTICA_MOTOR",
                # rationale intentionally omitted
            }
        )
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("must carry a rationale", str(ctx.exception))

    def test_externally_sourced_signal_without_source_ref_raises(self):
        raw = self._rubric_with_signal(
            {
                "key": "sig1",
                "label": "Signal 1",
                "provenance": "OFICIAL_INEP",
                # source_ref intentionally omitted
            }
        )
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("must cite it", str(ctx.exception))

    def test_valid_signals_of_both_kinds_parse_cleanly(self):
        raw = _valid_rubric()
        raw["competencies"][0]["signals"] = [
            {
                "key": "sig1",
                "label": "Signal 1",
                "provenance": "OFICIAL_INEP",
                "source_ref": "p.1",
            },
            {
                "key": "sig2",
                "label": "Signal 2",
                "provenance": "HEURISTICA_MOTOR",
                "rationale": "Because the motor infers it",
            },
        ]
        rubric = parse_rubric_mapping(raw)
        self.assertEqual(len(rubric.competencies[0].signals), 2)


class TestParseScoringRule(unittest.TestCase):
    def _rubric_with_rule(self, rule: dict) -> dict:
        raw = _valid_rubric()
        raw["scoring_rules"] = [rule]
        return raw

    def test_non_mapping_scoring_rule_raises(self):
        raw = self._rubric_with_rule("not a mapping")
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("Each scoring rule must be a mapping", str(ctx.exception))

    def test_unknown_effect_raises(self):
        raw = self._rubric_with_rule(
            {
                "key": "rule1",
                "label": "Rule 1",
                "effect": "MADE_UP_EFFECT",
                "provenance": "OFICIAL_INEP",
            }
        )
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("unknown effect", str(ctx.exception))

    def test_unknown_competency_code_raises(self):
        raw = self._rubric_with_rule(
            {
                "key": "rule1",
                "label": "Rule 1",
                "effect": "ANULA_REDACAO",
                "provenance": "OFICIAL_INEP",
                "competency_code": "C9",
            }
        )
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("unknown competency_code", str(ctx.exception))

    def test_max_points_declared_without_limita_pontuacao_effect_raises(self):
        raw = self._rubric_with_rule(
            {
                "key": "rule1",
                "label": "Rule 1",
                "effect": "ANULA_REDACAO",
                "provenance": "OFICIAL_INEP",
                "max_points": 120,
            }
        )
        with self.assertRaises(RubricFileError) as ctx:
            parse_rubric_mapping(raw)
        self.assertIn("max_points but effect is not LIMITA_PONTUACAO", str(ctx.exception))

    def test_valid_limita_pontuacao_rule_parses_cleanly(self):
        raw = self._rubric_with_rule(
            {
                "key": "rule1",
                "label": "Rule 1",
                "effect": "LIMITA_PONTUACAO",
                "provenance": "OFICIAL_INEP",
                "competency_code": "C1",
                "max_points": 80,
            }
        )
        rubric = parse_rubric_mapping(raw)
        self.assertEqual(rubric.scoring_rules[0].max_points, 80)


if __name__ == "__main__":
    unittest.main()
