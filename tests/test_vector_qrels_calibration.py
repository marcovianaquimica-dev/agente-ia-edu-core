"""A regua de Calibration e separada da de Evaluation. Fase 7.

A separacao nao e organizacional, e metodologica: Calibration escolhe
parametros e pode ser olhado a vontade; Evaluation mede uma vez, depois de
tudo decidido. Misturar os dois invalida qualquer numero publicado depois, e
a mistura seria silenciosa - por isso ha teste.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.knowledge_retrieval_policy.vector_evaluation_sets import (
    VECTOR_CALIBRATION_SET_V1,
    VECTOR_EVALUATION_SET_V1,
)
from agente_ia_edu.knowledge_retrieval_policy.vector_qrels import (
    VECTOR_QRELS_V1,
)
from agente_ia_edu.knowledge_retrieval_policy.vector_qrels_calibration import (
    CALIBRATION_ADJUDICATION_DATE,
    CALIBRATION_ADJUDICATION_LOG,
    CALIBRATION_ADJUDICATION_PROVENANCE,
    VECTOR_QRELS_CALIBRATION,
    relevant_calibration,
)
from agente_ia_edu.knowledge_retrieval_policy.vector_qrels_v2 import (
    VECTOR_QRELS_V2,
)


class SeparationTests(unittest.TestCase):
    def test_the_two_rulers_cover_disjoint_query_sets(self):
        self.assertEqual(
            set(VECTOR_QRELS_CALIBRATION) & set(VECTOR_QRELS_V2), set()
        )
        self.assertEqual(
            set(VECTOR_QRELS_CALIBRATION) & set(VECTOR_QRELS_V1), set()
        )

    def test_calibration_covers_only_the_calibration_set(self):
        self.assertEqual(
            set(VECTOR_QRELS_CALIBRATION), set(VECTOR_CALIBRATION_SET_V1)
        )

    def test_no_evaluation_query_leaked_in(self):
        for registro in CALIBRATION_ADJUDICATION_LOG:
            self.assertNotIn(registro["query"], VECTOR_EVALUATION_SET_V1)

    def test_v1_and_v2_are_untouched_by_this_module(self):
        """Este modulo nao importa nem reexporta os graus do Evaluation. Se
        um dia importar, a separacao vira convencao em vez de estrutura."""
        from pathlib import Path

        fonte = (
            Path(__file__).resolve().parents[1]
            / "src/agente_ia_edu/knowledge_retrieval_policy"
            / "vector_qrels_calibration.py"
        ).read_text()
        self.assertNotIn("from .vector_qrels import", fonte)
        self.assertNotIn("VECTOR_QRELS_V1", fonte.split('"""', 2)[-1])
        self.assertEqual(sum(len(v) for v in VECTOR_QRELS_V1.values()), 133)
        self.assertEqual(sum(len(v) for v in VECTOR_QRELS_V2.values()), 165)


class AdjudicationTests(unittest.TestCase):
    def test_all_276_judgements_are_recorded(self):
        self.assertEqual(len(CALIBRATION_ADJUDICATION_LOG), 276)

    def test_grades_use_the_v1_scale(self):
        for registro in CALIBRATION_ADJUDICATION_LOG:
            self.assertIn(registro["grade"], (0, 1, 2))

    def test_grade_zero_is_logged_but_not_stored(self):
        zeros = [r for r in CALIBRATION_ADJUDICATION_LOG if r["grade"] == 0]
        self.assertEqual(len(zeros), 93)
        for registro in zeros:
            self.assertNotIn(
                registro["text_hash"],
                VECTOR_QRELS_CALIBRATION.get(registro["query"], {}),
            )

    def test_the_stored_ruler_has_the_relevant_ones(self):
        self.assertEqual(
            sum(len(v) for v in VECTOR_QRELS_CALIBRATION.values()), 183
        )

    def test_every_record_carries_provenance(self):
        self.assertEqual(CALIBRATION_ADJUDICATION_PROVENANCE,
                         "ADJUDICATED_HUMAN")
        for registro in CALIBRATION_ADJUDICATION_LOG:
            self.assertEqual(registro["date"], CALIBRATION_ADJUDICATION_DATE)
            self.assertEqual(
                registro["provenance"], CALIBRATION_ADJUDICATION_PROVENANCE
            )

    def test_each_pair_appears_exactly_once(self):
        pares = [(r["query"], r["text_hash"])
                 for r in CALIBRATION_ADJUDICATION_LOG]
        self.assertEqual(len(set(pares)), len(pares))

    def test_every_query_has_relevant_documents(self):
        for query in VECTOR_CALIBRATION_SET_V1:
            self.assertTrue(relevant_calibration(query), query)

    def test_minimum_grade_filters(self):
        for query in VECTOR_CALIBRATION_SET_V1:
            self.assertTrue(
                relevant_calibration(query, 2) <= relevant_calibration(query, 1)
            )


class NoFusionYetTests(unittest.TestCase):
    def test_no_fusion_module_was_created(self):
        """A investigacao da Fase 7 terminou em resultado NEGATIVO: RRF,
        Borda e CombSUM nao passaram os criterios pre-registrados. Nenhum
        fundidor foi implementado, e este teste guarda essa decisao."""
        from pathlib import Path

        raiz = (
            Path(__file__).resolve().parents[1]
            / "src/agente_ia_edu/services/knowledge_engine"
        )
        for nome in ("hybrid_search.py", "rank_fusion.py", "fusion.py",
                     "router.py"):
            self.assertFalse((raiz / nome).exists(), nome)


if __name__ == "__main__":
    unittest.main()
