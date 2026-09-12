import unittest
from pathlib import Path

from agente_ia_edu.services.curriculum_classification import ClassificationProposalService
from agente_ia_edu.services.ingestion_parser import PdfParser
from tests.test_curriculum_retriever_benchmark import catalog_fixture


class Phase9KRetrievalDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = catalog_fixture()
        cls.service = ClassificationProposalService(None)
        questions = PdfParser.parse_file(
            Path("var/inep-pilot/2020_PV_impresso_D2_CD5.pdf")
        ).questions
        cls.questions = {question.question_number: question for question in questions}

    def diagnose(self, text, **options):
        return self.service.diagnose_coverage(text, self.catalog, **options)

    def test_covered_and_partial_coverage_are_distinct(self):
        covered = self.diagnose("A diluição reduz a concentração de soluções.")
        partial = self.diagnose(self.questions[91].statement_text)
        self.assertEqual(covered.diagnosis, "COVERED")
        self.assertEqual(partial.diagnosis, "PARTIAL_COVERAGE")
        self.assertFalse(covered.retrieval_limitation)
        self.assertFalse(partial.retrieval_limitation)

    def test_question92_is_no_candidate_without_automatic_catalog_gap(self):
        diagnosis = self.diagnose(self.questions[92].statement_text)
        self.assertEqual(diagnosis.diagnosis, "NO_CANDIDATE")
        self.assertEqual(diagnosis.retrieved_candidate_count, 0)
        self.assertFalse(diagnosis.retrieval_limitation)

    def test_catalog_gap_requires_explicit_deterministic_confirmation(self):
        diagnosis = self.diagnose(
            self.questions[92].statement_text, taxonomy_gap_confirmed=True
        )
        self.assertEqual(diagnosis.diagnosis, "CATALOG_GAP")
        self.assertEqual(diagnosis.retrieved_candidate_count, 0)

    def test_morphology_variants_are_recovered_after_controlled_normalization(self):
        diagnosis = self.diagnose("Diluições.")
        self.assertEqual(diagnosis.diagnosis, "COVERED")
        self.assertFalse(diagnosis.retrieval_limitation)

    def test_d1_false_positive_is_not_claimed_as_covered_or_catalog_gap(self):
        d1_questions = PdfParser.parse_file(
            Path("var/inep-pilot/2020_PV_impresso_D1_CD1.pdf")
        ).questions
        social_problem = next(question for question in d1_questions if question.question_number == 22)
        diagnosis = self.diagnose(social_problem.statement_text)
        self.assertNotEqual(diagnosis.diagnosis, "COVERED")
        self.assertNotEqual(diagnosis.diagnosis, "CATALOG_GAP")

    def test_diagnosis_is_deterministic_and_has_no_side_effects(self):
        first = self.diagnose(self.questions[91].statement_text)
        second = self.diagnose(self.questions[91].statement_text)
        self.assertEqual(first, second)
        self.assertEqual(len(self.catalog), 17)