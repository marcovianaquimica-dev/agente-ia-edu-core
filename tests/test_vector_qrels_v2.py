"""A regua adjudicada so pode ACRESCENTAR. Fase 6.

O risco que estes testes eliminam nao e teorico: uma regua que muda sob os
pes torna todo numero historico incomparavel, e a mudanca e silenciosa -
ninguem percebe que o baseline de ontem passou a medir outra coisa.

A V1 permanece congelada e reproduzivel; a V2 so acrescenta.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.knowledge_retrieval_policy.vector_evaluation_sets import (
    VECTOR_EVALUATION_SET_V1,
)
from agente_ia_edu.knowledge_retrieval_policy.vector_qrels import (
    VECTOR_QRELS_V1,
    relevant,
)
from agente_ia_edu.knowledge_retrieval_policy.vector_qrels_v2 import (
    ADJUDICATION_DATE,
    ADJUDICATION_LOG,
    ADJUDICATION_PROVENANCE,
    VECTOR_QRELS_V2,
    relevant_v2,
)


class AppendOnlyTests(unittest.TestCase):
    def test_every_v1_judgement_survives_untouched(self):
        """A propriedade central. Se isto quebrar, o baseline oficial deixou
        de ser reproduzivel e qualquer comparacao historica vira ficcao."""
        for query, graus in VECTOR_QRELS_V1.items():
            for text_hash, grau in graus.items():
                self.assertEqual(
                    VECTOR_QRELS_V2[query][text_hash], grau,
                    f"{query} / {text_hash[:12]}",
                )

    def test_v2_is_a_superset(self):
        self.assertGreaterEqual(
            sum(len(v) for v in VECTOR_QRELS_V2.values()),
            sum(len(v) for v in VECTOR_QRELS_V1.values()),
        )
        for query in VECTOR_QRELS_V1:
            self.assertLessEqual(
                set(VECTOR_QRELS_V1[query]), set(VECTOR_QRELS_V2[query]), query
            )

    def test_v2_imports_v1_instead_of_copying_it(self):
        """Copiar permitiria as duas divergirem por edicao distraida. A V2
        importa, entao a divergencia nao e representavel."""
        from pathlib import Path

        fonte = (
            Path(__file__).resolve().parents[1]
            / "src/agente_ia_edu/knowledge_retrieval_policy/vector_qrels_v2.py"
        ).read_text()
        self.assertIn("from .vector_qrels import VECTOR_QRELS_V1", fonte)
        # Nem a V1 inteira, nem um fragmento dela, podem estar transcritos.
        self.assertNotIn("VECTOR_QRELS_V1: dict", fonte)
        self.assertNotIn("VECTOR_QRELS_V1 = {", fonte)

    def test_no_adjudicated_pair_collides_with_v1(self):
        """A invariante de verdade e sobre o PAR, nao sobre o hash.

        Um mesmo ``text_hash`` pode ter grau na V1 para uma consulta e ser
        adjudicado para OUTRA - relevancia e relativa a pergunta, nao
        propriedade do texto. O que nao pode existir e o mesmo par julgado
        duas vezes.
        """
        pares_v1 = {
            (query, text_hash)
            for query, graus in VECTOR_QRELS_V1.items()
            for text_hash in graus
        }
        for registro in ADJUDICATION_LOG:
            self.assertNotIn(
                (registro["query"], registro["text_hash"]), pares_v1,
                f"{registro['query']} / {registro['text_hash'][:12]}",
            )

    def test_the_merge_refuses_to_change_a_v1_grade(self):
        from agente_ia_edu.knowledge_retrieval_policy import vector_qrels_v2 as m

        query = VECTOR_EVALUATION_SET_V1[0]
        existente = next(iter(VECTOR_QRELS_V1[query]))
        original = m.ADJUDICATION_LOG
        m.ADJUDICATION_LOG = original + (
            {"query": query, "text_hash": existente, "grade": 1},
        )
        try:
            with self.assertRaises(ValueError) as caught:
                m._merge()
            self.assertIn("alterar grau da V1", str(caught.exception))
        finally:
            m.ADJUDICATION_LOG = original


class AdjudicationLogTests(unittest.TestCase):
    def test_all_sixty_seven_judgements_are_recorded(self):
        self.assertEqual(len(ADJUDICATION_LOG), 67)

    def test_grade_zero_is_logged_but_not_stored(self):
        """"Julgado irrelevante" e "nunca julgado" sao estados diferentes, e
        confundi-los foi o que distorceu o primeiro baseline. O log guarda a
        diferenca; os qrels mantem a convencao de que ausencia e o zero."""
        zeros = [r for r in ADJUDICATION_LOG if r["grade"] == 0]
        self.assertTrue(zeros)
        for registro in zeros:
            self.assertNotIn(
                registro["text_hash"], VECTOR_QRELS_V2.get(registro["query"], {})
            )

    def test_every_stored_adjudication_carries_provenance(self):
        for registro in ADJUDICATION_LOG:
            self.assertEqual(registro["date"], ADJUDICATION_DATE)
            self.assertEqual(registro["provenance"], ADJUDICATION_PROVENANCE)
        self.assertEqual(ADJUDICATION_PROVENANCE, "ADJUDICATED_HUMAN")

    def test_grades_use_the_v1_scale(self):
        for registro in ADJUDICATION_LOG:
            self.assertIn(registro["grade"], (0, 1, 2))

    def test_the_log_covers_each_pair_exactly_once(self):
        pares = [(r["query"], r["text_hash"]) for r in ADJUDICATION_LOG]
        self.assertEqual(len(set(pares)), len(pares))

    def test_duplicated_content_carries_every_affected_chunk_id(self):
        """R7: um julgamento, varios ``chunk_id``. O registro precisa dizer
        quais, senao a rastreabilidade se perde no colapso."""
        multiplos = [r for r in ADJUDICATION_LOG if len(r["chunk_ids"]) > 1]
        self.assertEqual(len(multiplos), 1)
        self.assertEqual(len(multiplos[0]["chunk_ids"]), 3)

    def test_only_queries_of_the_frozen_set_were_adjudicated(self):
        for registro in ADJUDICATION_LOG:
            self.assertIn(registro["query"], VECTOR_EVALUATION_SET_V1)


class RelevanceHelperTests(unittest.TestCase):
    def test_relevant_v2_is_never_smaller_than_v1(self):
        for query in VECTOR_EVALUATION_SET_V1:
            self.assertTrue(relevant(query) <= relevant_v2(query), query)

    def test_minimum_grade_still_filters(self):
        query = "reagente limitante"
        self.assertTrue(relevant_v2(query, minimum=2) <= relevant_v2(query, 1))


if __name__ == "__main__":
    unittest.main()
