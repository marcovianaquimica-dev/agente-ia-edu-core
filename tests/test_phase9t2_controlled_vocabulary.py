import unittest
from dataclasses import dataclass
from pathlib import Path

from agente_ia_edu.services.curriculum_classification import ClassificationProposalService
from agente_ia_edu.services.ingestion_parser import PdfParser


PRIMARY_TERMS = ("cinetica quimica", "estudo cinetico")
SPECIFIC_TERMS = (
    "velocidade da reacao", "velocidade das reacoes", "taxa de reacao",
    "fatores que influenciam a velocidade", "catalisador", "catalisadores",
    "catalise", "energia de ativacao",
)
CONTEXTUAL_EXPRESSIONS = (
    "estudo da velocidade da reacao", "evolucao da concentracao ao longo do tempo",
    "efeito de catalisador na velocidade da reacao", "determinacao da velocidade de uma reacao",
)
GENERIC_TERMS = {"quimica", "reacao", "velocidade", "estudo", "fatores", "concentracao"}


@dataclass(frozen=True)
class VocabularyMatch:
    score: int
    matched_phrases: tuple[str, ...]
    cooccurrence_score: int


def normalize(text: str) -> str:
    terms = ClassificationProposalService._normalized_term_sequence(text)
    return " ".join(terms)


def controlled_match(text: str, phrases: tuple[str, ...]) -> VocabularyMatch | None:
    normalized = normalize(text)
    matches = tuple(phrase for phrase in phrases if normalize(phrase) in normalized)
    terms = set(normalized.split())
    cooccurrence = int({"velocidade", "reacao"}.issubset(terms)) * 8
    if not matches and not cooccurrence:
        return None
    return VocabularyMatch(20 * len(matches) + cooccurrence, matches, cooccurrence)


class Phase9T2ControlledVocabularyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        questions = PdfParser.parse_file(
            Path("var/inep-pilot/2020_PV_impresso_D2_CD5.pdf")
        ).questions
        cls.questions = {question.question_number: question.statement_text for question in questions}

    def evaluate(self, phrases):
        return {
            number: controlled_match(self.questions[number], phrases)
            for number in (91, 92, 93, 95, 104, 128, 135)
        }

    def test_primary_specific_and_contextual_variants_are_deterministic(self):
        variants = {
            "primary": PRIMARY_TERMS,
            "primary_specific": PRIMARY_TERMS + SPECIFIC_TERMS,
            "combined": PRIMARY_TERMS + SPECIFIC_TERMS + CONTEXTUAL_EXPRESSIONS,
        }
        for name, phrases in variants.items():
            with self.subTest(variant=name):
                self.assertEqual(self.evaluate(phrases), self.evaluate(phrases))

    def test_combined_vocabulary_recovers_93_and_128_without_regression_matches(self):
        results = self.evaluate(PRIMARY_TERMS + SPECIFIC_TERMS + CONTEXTUAL_EXPRESSIONS)
        self.assertIsNotNone(results[93])
        self.assertIsNotNone(results[128])
        for number in (91, 92, 95, 104, 135):
            self.assertIsNone(results[number])

    def test_generic_terms_are_not_independent_triggers(self):
        for term in GENERIC_TERMS:
            with self.subTest(term=term):
                self.assertIsNone(controlled_match(term, PRIMARY_TERMS + SPECIFIC_TERMS + CONTEXTUAL_EXPRESSIONS))

    def test_term_only_and_morphology_behavior_is_explicit(self):
        self.assertIsNotNone(controlled_match("estudo cinético", PRIMARY_TERMS))
        self.assertIsNone(controlled_match("catalíticos", SPECIFIC_TERMS))
        self.assertIsNone(controlled_match("cinéticos", PRIMARY_TERMS))
        self.assertIsNotNone(controlled_match("velocidade das reações", SPECIFIC_TERMS))

    def test_score_components_are_auditable(self):
        result = controlled_match("A velocidade da reação aumenta.", SPECIFIC_TERMS)
        self.assertIsNotNone(result)
        self.assertGreater(result.score, 0)
        self.assertEqual(result.cooccurrence_score, 8)
        self.assertIn("velocidade da reacao", result.matched_phrases)