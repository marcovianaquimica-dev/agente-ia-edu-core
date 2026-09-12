import unittest
from collections import Counter

from tests.test_phase9m_expanded_benchmark import GOLD, Phase9MExpandedBenchmarkTests


def coverage_matrix(records: list[dict]) -> list[dict]:
    matrix = []
    for record in records:
        gold = record["gold"]
        retrieved = record["retrieved"]
        if gold.expected_coverage == "AMBIGUOUS":
            result = "AMBIGUOUS"
        elif gold.expected_coverage == "PARTIAL_COVERAGE":
            result = "COVERED_AND_RETRIEVED" if set(gold.expected_codes) & set(retrieved) else "PARTIAL_TAXONOMY_MISS"
        elif gold.expected_codes:
            result = "COVERED_AND_RETRIEVED" if set(gold.expected_codes) & set(retrieved) else "COVERED_BUT_NOT_RETRIEVED"
        elif retrieved:
            result = "FALSE_POSITIVE"
        else:
            result = "NOT_COVERED"
        matrix.append({
            "question": record["number"], "discipline": gold.discipline,
            "coverage": gold.expected_coverage, "gold": gold.expected_codes,
            "retrieved": tuple(retrieved), "result": result,
        })
    return matrix


class Phase9QCoverageMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Phase9MExpandedBenchmarkTests.setUpClass()
        cls.benchmark = Phase9MExpandedBenchmarkTests()

    def test_matrix_is_deterministic_and_preserves_phase9n_gold(self):
        first = coverage_matrix(self.benchmark.run_benchmark())
        second = coverage_matrix(self.benchmark.run_benchmark())
        self.assertEqual(first, second)
        self.assertEqual(len(first), 50)
        self.assertEqual(sum(row["coverage"] != "AMBIGUOUS" for row in first), 22)
        self.assertEqual(GOLD[91].expected_codes, ("CHEMISTRY-SOLUTIONS",))
        self.assertEqual(GOLD[92].expected_codes, ())

    def test_regression_matrix_for_questions_91_to_100(self):
        rows = {row["question"]: row for row in coverage_matrix(self.benchmark.run_benchmark())}
        self.assertEqual(rows[91]["result"], "COVERED_AND_RETRIEVED")
        self.assertEqual(rows[92]["result"], "NOT_COVERED")
        self.assertEqual(rows[93]["result"], "NOT_COVERED")
        self.assertEqual(rows[95]["result"], "NOT_COVERED")
        self.assertEqual(rows[104]["result"], "FALSE_POSITIVE")
        self.assertEqual(rows[135]["result"], "COVERED_AND_RETRIEVED")

    def test_coverage_and_retrieval_metrics_are_separate(self):
        matrix = coverage_matrix(self.benchmark.run_benchmark())
        counts = Counter(row["coverage"] for row in matrix)
        outcomes = Counter(row["result"] for row in matrix)
        represented = counts["FULL_COVERAGE"] + counts["PARTIAL_COVERAGE"]
        self.assertEqual(counts["FULL_COVERAGE"], 3)
        self.assertEqual(counts["PARTIAL_COVERAGE"], 1)
        self.assertEqual(counts["NO_COVERAGE"], 18)
        self.assertEqual(represented / 22, 4 / 22)
        self.assertEqual(outcomes["COVERED_BUT_NOT_RETRIEVED"], 0)
        self.assertEqual(outcomes["FALSE_POSITIVE"], 1)