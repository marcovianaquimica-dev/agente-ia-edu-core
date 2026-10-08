"""CEREBRO - Fase 6, passo 1: conjuntos e qrels da perna vetorial, congelados.

A DISCIPLINA, E POR QUE ELA E O PRIMEIRO PASSO
==============================================

Os julgamentos de relevancia foram definidos **antes de existir embedding
algum**. Julgar relevancia depois de ver o ranking do modelo mede a
concordancia do juiz consigo mesmo, nao a qualidade do modelo - e o erro e
invisivel no resultado.

Estes testes existem para que o congelamento seja estrutural: alterar
qualquer um dos conjuntos, ou qualquer qrel, quebra a suite de proposito.

``EVALUATION_SET_V1`` lexical permanece intocado - ha teste aqui so para
garantir que ninguem o confunda com estes.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.knowledge_retrieval_policy.evaluation_sets import (
    CALIBRATION_SET_V1,
    EVALUATION_SET_V1,
)
from agente_ia_edu.knowledge_retrieval_policy.vector_evaluation_sets import (
    AMBIGUITY_MAX_OVERLAP,
    AMBIGUITY_PAIR,
    NEGATIVE_CONTROL,
    VECTOR_CALIBRATION_SET_V1,
    VECTOR_EVALUATION_SET_V1,
)
from agente_ia_edu.knowledge_retrieval_policy.vector_qrels import (
    VECTOR_QRELS_V1,
    relevant,
)


class FrozenSetTests(unittest.TestCase):
    def test_the_vector_evaluation_set_is_exactly_the_approved_ten(self):
        self.assertEqual(
            VECTOR_EVALUATION_SET_V1,
            (
                "o que sobra quando um dos reagentes acaba primeiro",
                "como saber qual substância acaba antes numa reação química",
                "por que adicionar água deixa a solução mais fraca",
                "quantas partículas existem numa amostra de uma substância",
                "relação entre a massa de uma substância e o número de partículas",
                "preparar uma solução partindo de outra mais concentrada",
                "reagente limitante",
                "fator limitante para a vida de espécies aquáticas",
                "habilidade sobre transformações e conservações em sistemas",
                "fotossíntese nas plantas",
            ),
        )

    def test_the_lexical_evaluation_set_is_untouched(self):
        """A regua da Fase 5 nao muda porque a Fase 6 chegou."""
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

    def test_the_vector_sets_are_disjoint_from_each_other(self):
        self.assertEqual(
            set(VECTOR_EVALUATION_SET_V1) & set(VECTOR_CALIBRATION_SET_V1), set()
        )

    def test_the_vector_calibration_set_does_not_reuse_lexical_calibration(self):
        self.assertEqual(
            set(VECTOR_CALIBRATION_SET_V1) & set(CALIBRATION_SET_V1), set()
        )

    def test_the_only_shared_query_with_the_lexical_set_is_deliberate(self):
        """``reagente limitante`` aparece nos dois conjuntos DE PROPOSITO: e a
        consulta em que a Fase 5.1 expos a ambiguidade, e e ela que permite
        comparar as duas pernas no mesmo termo."""
        self.assertEqual(
            set(VECTOR_EVALUATION_SET_V1) & set(EVALUATION_SET_V1),
            {"reagente limitante"},
        )

    def test_the_ambiguity_pair_is_the_declared_one(self):
        self.assertEqual(
            AMBIGUITY_PAIR,
            (
                "reagente limitante",
                "fator limitante para a vida de espécies aquáticas",
            ),
        )
        self.assertEqual(AMBIGUITY_MAX_OVERLAP, 2)

    def test_the_negative_control_is_declared(self):
        self.assertEqual(NEGATIVE_CONTROL, "fotossíntese nas plantas")


class QrelsTests(unittest.TestCase):
    def test_every_query_has_judgements(self):
        for query in VECTOR_EVALUATION_SET_V1:
            self.assertIn(query, VECTOR_QRELS_V1, query)
            self.assertTrue(VECTOR_QRELS_V1[query], query)

    def test_grades_are_only_one_or_two(self):
        """Grau 0 nao e armazenado - ausencia E o zero."""
        for query, graus in VECTOR_QRELS_V1.items():
            for text_hash, grau in graus.items():
                self.assertIn(grau, (1, 2), f"{query} / {text_hash}")

    def test_keys_are_full_sha256_hashes(self):
        """``text_hash`` e deterministico a partir do conteudo; UUID de chunk
        muda a cada ingestao e nao serviria como chave congelada."""
        for graus in VECTOR_QRELS_V1.values():
            for text_hash in graus:
                self.assertEqual(len(text_hash), 64, text_hash)
                self.assertTrue(
                    all(c in "0123456789abcdef" for c in text_hash), text_hash
                )

    def test_the_ambiguity_pair_has_no_shared_relevant_document(self):
        """A VERDADE DE REFERENCIA do teste de ambiguidade.

        Julgado antes de qualquer embedding: nenhum documento e relevante
        para o conceito quimico E para o sentido ecologico ao mesmo tempo.
        Se o vetor devolver os mesmos chunks para as duas, ele nao distinguiu
        os sentidos - e isso sera registrado como resultado, nao corrigido
        com ajuste."""
        quimico, ecologico = AMBIGUITY_PAIR
        self.assertEqual(relevant(quimico) & relevant(ecologico), frozenset())

    def test_graded_relevance_exists_so_ndcg_is_computable(self):
        for query in VECTOR_EVALUATION_SET_V1:
            graus = set(VECTOR_QRELS_V1[query].values())
            self.assertTrue(graus & {2}, f"{query} nao tem nenhum grau 2")

    def test_relevant_filters_by_minimum_grade(self):
        query = "reagente limitante"
        todos = relevant(query, minimum=1)
        so_fortes = relevant(query, minimum=2)
        self.assertTrue(so_fortes)
        self.assertLess(len(so_fortes), len(todos))
        self.assertTrue(so_fortes <= todos)

    def test_the_negative_control_is_not_empty_and_that_is_the_point(self):
        """O corpus de quimica geral MENCIONA fotossintese - ha uma questao
        da Fuvest sobre a reacao. Entao o controle negativo nao e "nada
        relevante existe": e uma consulta de BAIXA DENSIDADE.

        Por isso a Fase 6 registra a DISTRIBUICAO de scores e nao inventa
        limiar de abstencao. Qualquer limiar futuro nascera do Calibration
        Set."""
        self.assertTrue(relevant(NEGATIVE_CONTROL))
        self.assertLess(
            len(relevant(NEGATIVE_CONTROL)),
            len(relevant("por que adicionar água deixa a solução mais fraca")),
        )

    def test_the_bncc_query_has_a_curriculum_item_as_its_answer(self):
        """So a BNCC responde esta, e ela tem 23 chunks contra ~5.900."""
        query = "habilidade sobre transformações e conservações em sistemas"
        self.assertEqual(len(relevant(query, minimum=2)), 1)

    def test_qrels_cover_only_the_vector_set(self):
        self.assertEqual(set(VECTOR_QRELS_V1), set(VECTOR_EVALUATION_SET_V1))


if __name__ == "__main__":
    unittest.main()
