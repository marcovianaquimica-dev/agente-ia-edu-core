"""O DIAGNÓSTICO É SONDAGEM — ELE COMEÇA PELO MAIS SIMPLES.

O QUE FOI MEDIDO, EM 2026-10-06
================================
O microdiagnóstico usa a mesma seleção da prática, que ordena por
`official_number`. Ele não olhava a dificuldade. Para Estequiometria, o
conteúdo da demonstração, o acervo tem:

    EASY     6
    MEDIUM  14
    HARD     1
    (sem)    1

Pedindo três questões em ordem de número oficial, um aluno que nunca foi
medido podia abrir a sondagem numa cadeia completa - massa → mol → proporção
→ resultado - e errar por travar no primeiro elo. O diagnóstico então
registrava "não sabe estequiometria" quando o que ele não sabia era converter
massa em mol.

A REGRA, E O QUE ELA NÃO AFIRMA
================================
Questão marcada FÁCIL vem primeiro. O resto mantém exatamente a ordem de
antes - inclusive as sem dificuldade atribuída, que são 514 das 595 do
acervo. Dizer que uma questão não classificada é "fácil" ou "média" seria
inventar sobre ela; o que se pode afirmar é só o que está escrito.

E a prática comum NÃO muda: ela não é sondagem, e começar sempre pelas fáceis
tornaria a evidência mais fraca do que ela precisa ser.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from agente_ia_edu.services.adaptive_practice import PracticeSelectionPolicy


@dataclass
class _Item:
    question_version_id: str
    official_number: int | None
    recommended_difficulty: str | None


ACERVO = [
    _Item("q1", 1, "MEDIUM"),
    _Item("q2", 2, "HARD"),
    _Item("q3", 3, "EASY"),
    _Item("q4", 4, None),
    _Item("q5", 5, "EASY"),
    _Item("q6", 6, "MEDIUM"),
]


class ASondagemComecaPeloMaisSimples(unittest.TestCase):

    def _ordem(self, policy):
        return [i.question_version_id for i in policy.rank(list(ACERVO))]

    def test_as_faceis_vem_primeiro(self):
        ordem = self._ordem(PracticeSelectionPolicy(prefer_easier=True))
        self.assertEqual(["q3", "q5"], ordem[:2])

    def test_as_tres_primeiras_de_uma_sondagem_sao_as_mais_simples(self):
        """O microdiagnóstico pede três: são estas que o aluno vê."""
        ordem = self._ordem(PracticeSelectionPolicy(prefer_easier=True))[:3]
        self.assertIn("q3", ordem)
        self.assertIn("q5", ordem)
        self.assertNotIn("q2", ordem, "a sondagem abriu numa questão difícil")

    def test_o_resto_mantem_a_ordem_de_antes(self):
        """Nada é afirmado sobre o que não está classificado."""
        ordem = self._ordem(PracticeSelectionPolicy(prefer_easier=True))
        self.assertEqual(["q1", "q2", "q4", "q6"], ordem[2:])

    def test_a_ordem_e_estavel(self):
        policy = PracticeSelectionPolicy(prefer_easier=True)
        self.assertEqual(self._ordem(policy), self._ordem(policy))


class APraticaComumNaoMuda(unittest.TestCase):
    """Prática não é sondagem: começar sempre pelas fáceis tornaria a
    evidência mais fraca do que ela precisa ser."""

    def test_o_padrao_continua_sendo_por_numero_oficial(self):
        ordem = [i.question_version_id
                 for i in PracticeSelectionPolicy.default().rank(list(ACERVO))]
        self.assertEqual(["q1", "q2", "q3", "q4", "q5", "q6"], ordem)

    def test_a_preferencia_e_desligada_por_padrao(self):
        self.assertFalse(PracticeSelectionPolicy.default().prefer_easier)
        self.assertIn("PREFER_EASIER", PracticeSelectionPolicy.default().as_dict())


class OMicroDiagnosticoUsaAPoliticaDeSondagem(unittest.TestCase):

    def test_ele_pede_a_preferencia(self):
        """Senão a regra existe e ninguém a usa."""
        import inspect

        from agente_ia_edu.services import micro_diagnostic

        fonte = inspect.getsource(micro_diagnostic)
        self.assertIn("prefer_easier=True", fonte,
                      "o microdiagnóstico continua sorteando por número")


if __name__ == "__main__":
    unittest.main()
