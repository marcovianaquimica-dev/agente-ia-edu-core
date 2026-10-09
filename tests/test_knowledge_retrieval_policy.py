"""CEREBRO - Fase 5: a politica de recuperacao e os dois conjuntos de consultas.

Mesmo regime de ``essay_engine_contract/vN`` e ``knowledge_chunking_policy/v1``:
dataclass CONGELADA, versionada, imutavel. Mudar numero e mudar ``v2``, nunca
editar a v1 - um Knowledge Pack produzido ontem tem de continuar explicavel.

CALIBRATION SET x EVALUATION SET (ajuste 4 da Fase 5)
=====================================================

Ajustar ``heading_weight`` olhando as cinco consultas que depois serao usadas
para declarar a qualidade e treinar no conjunto de teste. Os dois conjuntos
sao disjuntos POR TESTE, e o Evaluation Set e congelado: ele sera reusado sem
ajuste dirigido quando comparamos Lexical v1 x Vector v1 x Hybrid v1.
"""

from __future__ import annotations

import dataclasses
import unittest

from agente_ia_edu.knowledge_retrieval_policy.evaluation_sets import (
    CALIBRATION_SET_V1,
    EVALUATION_SET_V1,
)
from agente_ia_edu.knowledge_retrieval_policy.v1 import (
    POLICY,
    POLICY_VERSION,
    RETRIEVAL_PURPOSES,
    UNKNOWN_PURPOSE,
    RetrievalPolicyV1,
    solution_is_visible,
)
from agente_ia_edu.services.knowledge_engine.lexical_tokenizer import normalize_term


class PolicyShapeTests(unittest.TestCase):
    def test_the_policy_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            POLICY.k1 = 2.0  # type: ignore[misc]

    def test_it_is_versioned(self):
        self.assertEqual(POLICY_VERSION, "v1")
        self.assertEqual(POLICY.version, "v1")
        self.assertEqual(POLICY.normalizer_version, "v1")

    def test_the_retrieval_mode_is_strict_corpus(self):
        self.assertEqual(POLICY.retrieval_mode, "STRICT_CORPUS")

    def test_bm25_constants_are_the_approved_ones(self):
        self.assertEqual(POLICY.k1, 1.2)
        self.assertEqual(POLICY.b, 0.75)

    def test_heading_weight_starts_neutral(self):
        """Medido: 70,5% dos headings reais sao so ``Chapter N``. Subir o peso
        antes do A/B amplificaria boilerplate do parser."""
        self.assertEqual(POLICY.heading_weight, 1.0)

    def test_source_diversity_is_measured_and_not_capped(self):
        """Limitar agora esconderia a medicao que a fase existe para produzir."""
        self.assertIsNone(POLICY.max_chunks_per_source)
        self.assertEqual(POLICY.min_distinct_sources, 2)

    def test_the_term_length_cap_matches_the_column(self):
        self.assertEqual(POLICY.max_term_length, 80)

    def test_a_backend_swap_does_not_change_the_contract(self):
        """Ajuste 1: o indice proprio NAO e parte permanente da arquitetura.
        A politica nomeia o backend para que trocar de mecanismo seja um dado
        observavel na resposta, nao uma reescrita de consumidor."""
        self.assertEqual(POLICY.lexical_backend, "OWN_INVERTED_INDEX")


class SolutionVisibilityTests(unittest.TestCase):
    """Principio da Fase 3.1, agora executavel."""

    def test_practice_and_assess_are_closed(self):
        self.assertFalse(solution_is_visible("PRACTICE"))
        self.assertFalse(solution_is_visible("ASSESS"))

    def test_learn_and_author_are_open(self):
        self.assertTrue(solution_is_visible("LEARN"))
        self.assertTrue(solution_is_visible("AUTHOR"))

    def test_an_absent_purpose_is_closed(self):
        self.assertFalse(solution_is_visible(None))
        self.assertFalse(solution_is_visible(UNKNOWN_PURPOSE))

    def test_an_unknown_purpose_is_closed(self):
        """Falha FECHADA: o default do dicionario e o fechamento, nao uma
        lista de excecoes que alguem esqueceria de atualizar."""
        self.assertFalse(solution_is_visible("WHATEVER"))

    def test_every_declared_purpose_has_a_declared_visibility(self):
        """Se um proposito novo entrar em RETRIEVAL_PURPOSES sem declarar
        visibilidade, este teste falha - em vez de o default silencioso
        decidir por nos."""
        for purpose in RETRIEVAL_PURPOSES:
            self.assertIn(purpose, POLICY.solution_visibility_by_purpose)


class EvaluationSetTests(unittest.TestCase):
    def test_the_evaluation_set_is_exactly_the_five_approved_queries(self):
        self.assertEqual(
            EVALUATION_SET_V1,
            (
                "estequiometria",
                "reagente limitante",
                "mol",
                "diluição",
                "concentração das soluções",
            ),
        )

    def test_the_two_sets_are_disjoint_after_normalization(self):
        """Disjuncao textual nao basta: ``diluição`` e ``diluicoes`` sao a
        mesma consulta para o indice."""

        def key(query: str) -> tuple[str, ...]:
            return tuple(sorted(normalize_term(p) for p in query.lower().split()))

        evaluation = {key(q) for q in EVALUATION_SET_V1}
        calibration = {key(q) for q in CALIBRATION_SET_V1}
        self.assertEqual(evaluation & calibration, set())

    def test_the_calibration_set_is_not_empty(self):
        self.assertGreaterEqual(len(CALIBRATION_SET_V1), 4)

    def test_both_sets_are_immutable(self):
        self.assertIsInstance(EVALUATION_SET_V1, tuple)
        self.assertIsInstance(CALIBRATION_SET_V1, tuple)


class PolicySnapshotTests(unittest.TestCase):
    def test_the_snapshot_carries_every_number_that_affects_a_score(self):
        snapshot = POLICY.snapshot()
        for key in (
            "version",
            "normalizer_version",
            "retrieval_mode",
            "lexical_backend",
            "k1",
            "b",
            "heading_weight",
            "phrase_slack",
            "phrase_bonus_weight",
            "proximity_window",
            "proximity_bonus_weight",
        ):
            self.assertIn(key, snapshot)

    def test_the_snapshot_is_json_serializable(self):
        import json

        json.dumps(POLICY.snapshot())

    def test_a_fresh_policy_equals_the_singleton(self):
        self.assertEqual(RetrievalPolicyV1(), POLICY)


if __name__ == "__main__":
    unittest.main()
