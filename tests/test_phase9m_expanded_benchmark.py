import unittest
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from agente_ia_edu.services.curriculum_classification import ClassificationProposalService
from agente_ia_edu.services.ingestion_parser import PdfParser
from tests.test_curriculum_retriever_benchmark import candidate_codes, catalog_fixture


@dataclass(frozen=True)
class ExpandedGold:
    question_number: int
    discipline: str | None
    expected_codes: tuple[str, ...]
    expected_coverage: str


# These annotations are independent of retriever output. Remaining items are
# deliberately AMBIGUOUS until a human reviewer can produce defensible gold.
GOLD = {
    91: ExpandedGold(91, "CHEMISTRY", ("CHEMISTRY-SOLUTIONS",), "PARTIAL_COVERAGE"),
    92: ExpandedGold(92, "PHYSICS", (), "NO_COVERAGE"),
    93: ExpandedGold(93, "CHEMISTRY", (), "NO_COVERAGE"),
    94: ExpandedGold(94, "PHYSICS", (), "NO_COVERAGE"),
    95: ExpandedGold(95, "CHEMISTRY", (), "NO_COVERAGE"),
    96: ExpandedGold(96, "PHYSICS", (), "NO_COVERAGE"),
    97: ExpandedGold(97, "BIOLOGY", (), "NO_COVERAGE"),
    98: ExpandedGold(98, "BIOLOGY", (), "NO_COVERAGE"),
    99: ExpandedGold(99, "PHYSICS", (), "NO_COVERAGE"),
    100: ExpandedGold(100, "BIOLOGY", (), "NO_COVERAGE"),
    101: ExpandedGold(101, "PHYSICS", (), "NO_COVERAGE"),
    102: ExpandedGold(102, "BIOLOGY", (), "NO_COVERAGE"),
    103: ExpandedGold(103, "BIOLOGY", (), "NO_COVERAGE"),
    104: ExpandedGold(104, "CHEMISTRY", (), "NO_COVERAGE"),
    105: ExpandedGold(105, "CHEMISTRY", (), "NO_COVERAGE"),
    106: ExpandedGold(106, "PHYSICS", (), "NO_COVERAGE"),
    107: ExpandedGold(107, "CHEMISTRY", (), "NO_COVERAGE"),
    109: ExpandedGold(109, "PHYSICS", (), "NO_COVERAGE"),
    110: ExpandedGold(110, "BIOLOGY", (), "NO_COVERAGE"),
    115: ExpandedGold(115, "CHEMISTRY", ("CHEMISTRY-SOLUTIONS-CONCENTRATION",), "FULL_COVERAGE"),
    128: ExpandedGold(128, "CHEMISTRY", ("CHEMISTRY-SOLUTIONS-CONCENTRATION",), "FULL_COVERAGE"),
    135: ExpandedGold(135, "MATHEMATICS", ("MATH-ALGEBRA-RATIO",), "FULL_COVERAGE"),
}


def metrics(records: list[dict]) -> dict[str, float]:
    eligible = [record for record in records if record["gold"].expected_coverage != "AMBIGUOUS"]
    positives = [record for record in eligible if record["gold"].expected_codes]
    true_positive = sum(len(set(record["gold"].expected_codes) & set(record["retrieved"])) for record in eligible)
    retrieved = sum(len(record["retrieved"]) for record in eligible)
    recall = (
        sum(bool(set(record["gold"].expected_codes) & set(record["retrieved"])) for record in positives) / len(positives)
        if positives
        else 0.0
    )
    precision = true_positive / retrieved if retrieved else 1.0
    top_1 = sum(record["retrieved"][:1] == [record["gold"].expected_codes[0]] for record in positives) / len(positives) if positives else 0.0
    top_3 = sum(bool(set(record["gold"].expected_codes) & set(record["retrieved"][:3])) for record in positives) / len(positives) if positives else 0.0
    top_5 = sum(bool(set(record["gold"].expected_codes) & set(record["retrieved"][:5])) for record in positives) / len(positives) if positives else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "top_1": top_1, "top_3": top_3, "top_5": top_5}


class Phase9MExpandedBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parsed = PdfParser.parse_file(Path("var/inep-pilot/2020_PV_impresso_D2_CD5.pdf"))
        cls.questions = {item.question_number: item for item in parsed.questions}
        cls.numbers = tuple(range(91, 141))
        cls.catalog = catalog_fixture()
        cls.service = ClassificationProposalService(None)

    def run_benchmark(self):
        records = []
        for number in self.numbers:
            question = self.questions[number]
            retrieved = candidate_codes(self.service.retrieve_candidate_classifications(question.statement_text, self.catalog))
            gold = GOLD.get(number, ExpandedGold(number, None, (), "AMBIGUOUS"))
            records.append({"number": number, "retrieved": retrieved, "gold": gold})
        return records

    def test_has_fifty_real_questions_and_deterministic_runs(self):
        first = self.run_benchmark()
        second = self.run_benchmark()
        self.assertEqual(len(first), 50)
        self.assertEqual(first, second)

    def test_gold_regressions_and_metrics_are_explicit(self):
        records = {record["number"]: record for record in self.run_benchmark()}
        self.assertEqual(records[91]["gold"].expected_coverage, "PARTIAL_COVERAGE")
        self.assertEqual(records[92]["gold"].expected_coverage, "NO_COVERAGE")
        self.assertEqual(records[92]["retrieved"], [])
        self.assertIn("CHEMISTRY-SOLUTIONS", records[91]["retrieved"])
        measured = metrics(list(records.values()))
        self.assertGreaterEqual(measured["recall"], 0.75)
        self.assertGreaterEqual(measured["top_1"], 0.75)

    def test_ambiguous_items_are_excluded_from_primary_metrics(self):
        records = self.run_benchmark()
        counts = Counter(record["gold"].expected_coverage for record in records)
        self.assertEqual(counts["AMBIGUOUS"], 28)
        self.assertEqual(sum(counts.values()), 50)
        self.assertEqual(len(self.catalog), 17)