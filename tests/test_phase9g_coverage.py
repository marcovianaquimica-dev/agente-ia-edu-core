import unittest
from pathlib import Path

from agente_ia_edu.services.curriculum_classification import ClassificationProposalService
from agente_ia_edu.services.ingestion_parser import PdfParser
from tests.test_curriculum_retriever_benchmark import catalog_fixture


def coverage_result(candidates: list[dict]) -> tuple[str, str]:
    """Classify catalog coverage without making a pedagogical classification."""
    if not candidates:
        return "NO_COVERAGE", "NO_COMPATIBLE_NODE"
    if len(candidates) > 1 and candidates[0]["score"] == candidates[1]["score"]:
        return "MULTIPLE_PLAUSIBLE", "MULTIPLE_CANDIDATES"
    top = candidates[0]
    if top["subcontent_code"] is not None:
        return "FULL_COVERAGE", "NONE"
    return "PARTIAL_COVERAGE", (
        "MISSING_CONTENT" if top["content_code"] is None else "MISSING_SUBCONTENT"
    )


class Phase9GCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = catalog_fixture()
        cls.service = ClassificationProposalService(None)
        parsed = PdfParser.parse_file(Path("var/inep-pilot/2020_PV_impresso_D2_CD5.pdf"))
        cls.questions = {item.question_number: item for item in parsed.questions}

    def retrieve(self, number: int) -> list[dict]:
        return self.service.retrieve_candidate_classifications(
            self.questions[number].statement_text, self.catalog
        )

    def test_questions_93_to_100_have_deterministic_coverage_report(self):
        report = {
            number: coverage_result(self.retrieve(number))
            for number in range(93, 101)
        }
        for number in (93, 94, 95, 96, 97, 98, 99, 100):
            self.assertEqual(report[number], ("NO_COVERAGE", "NO_COMPATIBLE_NODE"))
        self.assertEqual(report, {
            number: coverage_result(self.retrieve(number))
            for number in range(93, 101)
        })

    def test_coverage_categories_are_distinct_and_deterministic(self):
        full = [{"score": 30, "content_code": "CONTENT", "subcontent_code": "SUBCONTENT"}]
        partial = [{"score": 20, "content_code": "CONTENT", "subcontent_code": None}]
        empty = []
        multiple = [
            {"score": 20, "content_code": "A", "subcontent_code": "A1"},
            {"score": 20, "content_code": "B", "subcontent_code": "B1"},
        ]
        self.assertEqual(coverage_result(full), ("FULL_COVERAGE", "NONE"))
        self.assertEqual(coverage_result(partial), ("PARTIAL_COVERAGE", "MISSING_SUBCONTENT"))
        self.assertEqual(coverage_result(empty), ("NO_COVERAGE", "NO_COMPATIBLE_NODE"))
        self.assertEqual(coverage_result(multiple), ("MULTIPLE_PLAUSIBLE", "MULTIPLE_CANDIDATES"))

    def test_question91_and_92_regressions_remain_isolated(self):
        candidates91 = self.service.retrieve_candidate_classifications(
            self.questions[91].statement_text, self.catalog
        )
        candidates92 = self.service.retrieve_candidate_classifications(
            self.questions[92].statement_text, self.catalog
        )
        self.assertTrue(candidates91)
        self.assertIsNone(candidates91[0]["subcontent_code"])
        self.assertEqual(coverage_result(candidates91), ("PARTIAL_COVERAGE", "MISSING_SUBCONTENT"))
        self.assertEqual(candidates92, [])
        self.assertEqual(coverage_result(candidates92), ("NO_COVERAGE", "NO_COMPATIBLE_NODE"))