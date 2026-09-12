import unittest
from dataclasses import replace
from pathlib import Path

from agente_ia_edu.services.curriculum_classification import (
    KINETICS_RETRIEVAL_VOCABULARY,
    ClassificationProposalService,
    RetrievalVocabularyEntry,
)
from agente_ia_edu.services.ingestion_parser import PdfParser


class ControlledRetrievalVocabularyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        questions = PdfParser.parse_file(
            Path("var/inep-pilot/2020_PV_impresso_D2_CD5.pdf")
        ).questions
        cls.questions = {question.question_number: question.statement_text for question in questions}

    def match(self, text):
        return ClassificationProposalService.match_retrieval_vocabulary(
            text,
            KINETICS_RETRIEVAL_VOCABULARY,
            known_codes={"CHEMISTRY-PHYSICAL-KINETICS"},
        )

    def test_primary_specific_and_contextual_matches_have_auditable_scores(self):
        primary = self.match("Estudo cinético de uma reação.")
        specific = self.match("A velocidade da reação foi medida.")
        contextual = self.match("O estudo da velocidade da reação foi realizado.")
        self.assertIn("estudo cinético", primary.matched_primary_terms)
        self.assertIn("velocidade da reação", specific.matched_specific_terms)
        self.assertIn("estudo da velocidade da reação", contextual.matched_contextual_expressions)
        self.assertGreater(contextual.phrase_match_score, 0)
        self.assertEqual(
            contextual.total_score,
            contextual.lexical_score + contextual.context_score,
        )

    def test_generic_terms_and_code_do_not_trigger_matches(self):
        for text in (
            "química reação velocidade estudo fatores concentração",
            "CHEMISTRY-PHYSICAL-KINETICS",
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.match(text))

    def test_normalization_handles_accents_case_punctuation_and_regular_plural(self):
        for text in (
            "CINÉTICA QUÍMICA.",
            "estudos cinéticos",
            "velocidade das reações!",
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(self.match(text))

    def test_q93_and_q128_match_without_regression_matches(self):
        for number in (93, 128):
            with self.subTest(question=number):
                self.assertIsNotNone(self.match(self.questions[number]))
        for number in (91, 92, 95, 104, 135):
            with self.subTest(question=number):
                self.assertIsNone(self.match(self.questions[number]))

    def test_vocab_configuration_rejects_empty_duplicate_and_unknown_codes(self):
        invalid = RetrievalVocabularyEntry("", (), (), (), (), "")
        with self.assertRaisesRegex(ValueError, "canonical code and version"):
            ClassificationProposalService.match_retrieval_vocabulary("text", invalid)
        duplicate = replace(
            KINETICS_RETRIEVAL_VOCABULARY,
            primary_terms=("cinética química", "cinética química"),
        )
        with self.assertRaisesRegex(ValueError, "terms must be unique"):
            ClassificationProposalService.match_retrieval_vocabulary("cinética química", duplicate)
        with self.assertRaisesRegex(ValueError, "canonical code is unknown"):
            ClassificationProposalService.match_retrieval_vocabulary(
                "cinética química", KINETICS_RETRIEVAL_VOCABULARY, known_codes=set()
            )

    def test_vocabulary_is_deterministic_and_isolated(self):
        first = self.match(self.questions[93])
        second = self.match(self.questions[93])
        self.assertEqual(first, second)
        self.assertEqual(first.canonical_code, "CHEMISTRY-PHYSICAL-KINETICS")
        self.assertEqual(self.match(self.questions[92]), None)