"""As consultas sanitarias nao podem virar benchmark. Fase 6, passo 5.

A tentacao e real e silenciosa: uma consulta sanitaria que "deu bom
resultado" migra, meses depois, para o Calibration Set - e o benchmark passa
a medir a concordancia do sistema com o que ja se sabia que ele achava.

Estes testes tornam a migracao impossivel sem quebrar a suite.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.knowledge_retrieval_policy.evaluation_sets import (
    CALIBRATION_SET_V1,
    EVALUATION_SET_V1,
)
from agente_ia_edu.knowledge_retrieval_policy.sanity_queries import (
    SANITY_ONLY,
    SANITY_ONLY_QUERIES,
)
from agente_ia_edu.knowledge_retrieval_policy.vector_evaluation_sets import (
    VECTOR_CALIBRATION_SET_V1,
    VECTOR_EVALUATION_SET_V1,
)
from agente_ia_edu.knowledge_retrieval_policy.vector_qrels import VECTOR_QRELS_V1

_CONGELADOS = {
    "EVALUATION_SET_V1": EVALUATION_SET_V1,
    "CALIBRATION_SET_V1": CALIBRATION_SET_V1,
    "VECTOR_EVALUATION_SET_V1": VECTOR_EVALUATION_SET_V1,
    "VECTOR_CALIBRATION_SET_V1": VECTOR_CALIBRATION_SET_V1,
}


class SanityQueryTests(unittest.TestCase):
    def test_the_marker_is_explicit(self):
        self.assertEqual(SANITY_ONLY, "SANITY_ONLY")

    def test_there_are_between_five_and_eight(self):
        self.assertGreaterEqual(len(SANITY_ONLY_QUERIES), 5)
        self.assertLessEqual(len(SANITY_ONLY_QUERIES), 8)

    def test_none_of_them_is_in_any_frozen_set(self):
        for nome, conjunto in _CONGELADOS.items():
            self.assertEqual(
                set(SANITY_ONLY_QUERIES) & set(conjunto), set(), nome
            )

    def test_none_of_them_is_a_qrels_key(self):
        """Nem por acidente: se uma sanitaria tivesse qrels, ela ja seria
        consulta de avaliacao com outro nome."""
        self.assertEqual(
            set(SANITY_ONLY_QUERIES) & set(VECTOR_QRELS_V1), set()
        )

    def test_they_do_not_reuse_a_frozen_concept_word(self):
        """Barra a PARAFRASE, nao so a repeticao literal.

        Nao e um teste perfeito - sinonimia nao se resolve por lista de
        palavras. E uma rede grossa contra o erro facil: reescrever
        'concentracao das solucoes' como 'solucao concentrada' e chamar de
        consulta nova.
        """
        proibidas = {
            "estequiometria", "estequiometrico", "reagente", "limitante",
            "mol", "mols", "molar", "diluicao", "diluir", "diluida",
            "concentracao", "concentrada", "avogadro", "periodica",
            "ionica", "balanceamento", "acido", "acidos", "base", "bases",
            "entalpia", "atomo", "atomos", "equilibrio", "oxidacao",
            "reducao", "fotossintese", "soluto", "solvente",
        }
        import unicodedata

        def _dobra(texto: str) -> set[str]:
            sem_acento = "".join(
                c
                for c in unicodedata.normalize("NFKD", texto.lower())
                if not unicodedata.combining(c)
            )
            return {
                palavra.strip(".,;:?!")
                for palavra in sem_acento.split()
            }

        for consulta in SANITY_ONLY_QUERIES:
            invasoras = _dobra(consulta) & proibidas
            self.assertEqual(
                invasoras, set(), f"{consulta!r} reusa conceito congelado"
            )

    def test_they_are_all_distinct(self):
        self.assertEqual(len(set(SANITY_ONLY_QUERIES)), len(SANITY_ONLY_QUERIES))


if __name__ == "__main__":
    unittest.main()
