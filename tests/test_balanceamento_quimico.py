"""Verificacao DETERMINISTICA de balanceamento quimico.

Balanceamento tem uma propriedade rara em conteudo pedagogico: a resposta e
checavel por contagem, nao por opiniao. Conservacao de atomos e aritmetica.

Entao nenhum item de Balanceamento deste banco depende de um LLM dizer que a
equacao esta certa. O LLM gera e julga pedagogia; a QUIMICA e conferida aqui.

ESCOPO PEQUENO, FALHA FECHADA
==============================
Isto nao e um CAS quimico. Suporta o que os itens gerados usam: formulas com
elementos, indices, parenteses, hidratos, coeficientes e carga. Qualquer
sintaxe fora disso levanta excecao em vez de devolver um palpite - uma
equacao que o parser nao entende nao pode ser aprovada como "balanceada".
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.chemistry_balance import (
    FormulaInvalida,
    atomos_da_formula,
    equacao_balanceada,
    ler_equacao,
)


class ParserDeFormulaTests(unittest.TestCase):

    def test_elemento_simples(self):
        self.assertEqual(atomos_da_formula("O2"), {"O": 2})
        self.assertEqual(atomos_da_formula("Fe"), {"Fe": 1})

    def test_elemento_de_duas_letras_nao_vira_dois_elementos(self):
        """'Na' e sodio, nao nitrogenio+actinio. 'NaCl' tem 2 atomos, nao 3."""
        self.assertEqual(atomos_da_formula("NaCl"), {"Na": 1, "Cl": 1})
        self.assertEqual(atomos_da_formula("CO"), {"C": 1, "O": 1})
        self.assertEqual(atomos_da_formula("Co"), {"Co": 1})

    def test_composto_com_indices(self):
        self.assertEqual(atomos_da_formula("H2O"), {"H": 2, "O": 1})
        self.assertEqual(atomos_da_formula("C6H12O6"), {"C": 6, "H": 12, "O": 6})

    def test_parenteses_multiplicam_o_grupo(self):
        self.assertEqual(atomos_da_formula("Ca(OH)2"), {"Ca": 1, "O": 2, "H": 2})
        self.assertEqual(atomos_da_formula("Al2(SO4)3"), {"Al": 2, "S": 3, "O": 12})

    def test_parenteses_aninhados(self):
        self.assertEqual(atomos_da_formula("Fe(NO3)3"), {"Fe": 1, "N": 3, "O": 9})

    def test_hidrato_com_ponto(self):
        self.assertEqual(atomos_da_formula("CuSO4.5H2O"),
                         {"Cu": 1, "S": 1, "O": 9, "H": 10})

    def test_estado_fisico_e_ignorado(self):
        self.assertEqual(atomos_da_formula("H2O(l)"), {"H": 2, "O": 1})
        self.assertEqual(atomos_da_formula("NaCl(aq)"), {"Na": 1, "Cl": 1})

    def test_carga_e_ignorada_na_contagem_de_atomos(self):
        self.assertEqual(atomos_da_formula("SO4^2-"), {"S": 1, "O": 4})

    # -- falha fechada ------------------------------------------------------

    def test_sintaxe_desconhecida_levanta_em_vez_de_adivinhar(self):
        for ruim in ("", "   ", "H2O)", "(H2O", "2H2O2(", "xyz!", "H2@O"):
            with self.subTest(formula=ruim):
                with self.assertRaises(FormulaInvalida):
                    atomos_da_formula(ruim)

    def test_elemento_inexistente_e_recusado(self):
        """'Xx' nao e elemento. Aceitar faria o balanco fechar com atomo
        imaginario dos dois lados."""
        with self.assertRaises(FormulaInvalida):
            atomos_da_formula("Xx2O")


class EquacaoTests(unittest.TestCase):

    def test_le_coeficientes_e_lados(self):
        esq, dir_ = ler_equacao("2 H2 + O2 -> 2 H2O")
        self.assertEqual(esq, {"H": 4, "O": 2})
        self.assertEqual(dir_, {"H": 4, "O": 2})

    def test_aceita_setas_diferentes(self):
        for seta in ("->", "→", "⟶", "="):
            with self.subTest(seta=seta):
                self.assertTrue(equacao_balanceada(f"2 H2 + O2 {seta} 2 H2O"))

    def test_coeficiente_implicito_e_um(self):
        self.assertTrue(equacao_balanceada("CaO + H2O -> Ca(OH)2"))

    def test_coeficiente_colado_na_formula(self):
        self.assertTrue(equacao_balanceada("2H2 + O2 -> 2H2O"))

    # -- o que o verificador precisa acertar -------------------------------

    def test_equacao_balanceada_e_reconhecida(self):
        for eq in (
            "2 H2 + O2 -> 2 H2O",
            "CH4 + 2 O2 -> CO2 + 2 H2O",
            "N2 + 3 H2 -> 2 NH3",
            "2 Na + Cl2 -> 2 NaCl",
            "3 Nb2O5 + 10 Al -> 6 Nb + 5 Al2O3",
            "H2SO4 + 2 KOH -> 2 H2O + K2SO4",
            "2 Al + 3 Cl2 -> 2 AlCl3",
            "Fe2O3 + 3 CO -> 2 Fe + 3 CO2",
        ):
            with self.subTest(equacao=eq):
                self.assertTrue(equacao_balanceada(eq), f"{eq} deveria balancear")

    def test_equacao_NAO_balanceada_e_pega(self):
        for eq in (
            "H2 + O2 -> H2O",            # H ok, O nao
            "CH4 + O2 -> CO2 + H2O",     # falta coeficiente
            "N2 + H2 -> NH3",
            "Na + Cl2 -> NaCl",
            "2 H2 + O2 -> 3 H2O",
            "Fe2O3 + CO -> Fe + CO2",
        ):
            with self.subTest(equacao=eq):
                self.assertFalse(equacao_balanceada(eq), f"{eq} NAO deveria balancear")

    def test_um_elemento_so_de_um_lado_derruba(self):
        self.assertFalse(equacao_balanceada("H2 + O2 -> H2O + S"))

    # -- falha fechada ------------------------------------------------------

    def test_equacao_sem_seta_e_recusada(self):
        with self.assertRaises(FormulaInvalida):
            ler_equacao("2 H2 + O2 2 H2O")

    def test_equacao_com_formula_invalida_propaga_o_erro(self):
        with self.assertRaises(FormulaInvalida):
            equacao_balanceada("2 H2 + Xx2 -> 2 H2O")

    def test_lado_vazio_e_recusado(self):
        for ruim in ("-> 2 H2O", "2 H2 + O2 ->", " -> "):
            with self.subTest(equacao=ruim):
                with self.assertRaises(FormulaInvalida):
                    ler_equacao(ruim)

    def test_coeficiente_zero_ou_negativo_e_recusado(self):
        for ruim in ("0 H2 + O2 -> H2O", "-2 H2 + O2 -> 2 H2O"):
            with self.subTest(equacao=ruim):
                with self.assertRaises(FormulaInvalida):
                    ler_equacao(ruim)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class NotacaoUnicodeTests(unittest.TestCase):
    """Subscrito Unicode e notacao quimica padrao, nao sintaxe exotica.

    A primeira versao do parser so lia digitos ASCII, e rejeitou 6 dos 10
    itens gerados por escreverem H₂O em vez de H2O. O fail-closed funcionou -
    recusou o que nao sabia ler, em vez de chutar - mas estava recusando
    quimica correta por causa de notacao.
    """

    def test_subscrito_unicode_vale_como_indice(self):
        self.assertEqual(atomos_da_formula("H₂O"), {"H": 2, "O": 1})
        self.assertEqual(atomos_da_formula("C₆H₁₂O₆"), {"C": 6, "H": 12, "O": 6})
        self.assertEqual(atomos_da_formula("Fe₂O₃"), {"Fe": 2, "O": 3})

    def test_subscrito_em_grupo_com_parenteses(self):
        self.assertEqual(atomos_da_formula("Ca(OH)₂"), {"Ca": 1, "O": 2, "H": 2})
        self.assertEqual(atomos_da_formula("Al₂(SO₄)₃"), {"Al": 2, "S": 3, "O": 12})

    def test_equacao_inteira_com_subscrito(self):
        self.assertTrue(equacao_balanceada("2 H₂ + O₂ → 2 H₂O"))
        self.assertTrue(equacao_balanceada("4 Fe + 3 O₂ → 2 Fe₂O₃"))
        self.assertTrue(equacao_balanceada("C₃H₈ + 5 O₂ → 3 CO₂ + 4 H₂O"))

    def test_desbalanceada_com_subscrito_continua_sendo_pega(self):
        self.assertFalse(equacao_balanceada("H₂ + O₂ → H₂O"))
        self.assertFalse(equacao_balanceada("C₃H₈ + 4 O₂ → 3 CO₂ + 4 H₂O"))

    def test_mistura_de_notacoes_na_mesma_equacao(self):
        self.assertTrue(equacao_balanceada("2 H2 + O₂ → 2 H₂O"))

    # -- a lacuna continua sendo recusada ----------------------------------

    def test_equacao_com_lacuna_nao_e_adivinhada(self):
        """'2 H₂ + O₂ → ___ H₂O' tem um coeficiente faltando DE PROPOSITO:
        e o enunciado do item. Nao da para conferir conservacao sem saber o
        numero, entao o parser recusa em vez de assumir 1."""
        for lacuna in ("2 H₂ + O₂ → ___ H₂O", "4 Fe + __ O₂ → 2 Fe₂O₃",
                       "N₂ + ? H₂ → 2 NH₃", "2 H₂ + O₂ → x H₂O"):
            with self.subTest(equacao=lacuna):
                with self.assertRaises(FormulaInvalida):
                    equacao_balanceada(lacuna)


class ViesPosicionalTests(unittest.TestCase):
    """O conjunto de itens tambem pode estar errado, mesmo com cada item certo.

    A primeira geracao aprovou 14 itens com a resposta em "B" em 9 deles.
    Cada um passou na conferencia de atomos; o CONJUNTO permitia acertar 64%
    marcando sempre a mesma letra. Num diagnostico isso vira falso-pronto.
    """

    def _itens(self, respostas):
        from agente_ia_edu.services.diagnostic_bank import ItemCandidato

        return [ItemCandidato(
            diagnostic_skill="BALANCEAR_SIMPLES", diagnostic_objective="o",
            difficulty="EASY", stem=f"q{i}",
            options={k: f"alt {k} do item {i}" for k in "ABCDE"},
            correct_answer=r, rationale="r") for i, r in enumerate(respostas)]

    def test_o_vies_da_primeira_geracao_seria_detectado(self):
        from agente_ia_edu.services.diagnostic_bank import vies_posicional

        v = vies_posicional(self._itens(list("BBBBBBBBBCCCCE")))
        self.assertGreater(v["B"], 0.6, "o detector nao viu o vies")

    def test_redistribuir_espalha_pelas_cinco_letras(self):
        from agente_ia_edu.services.diagnostic_bank import (
            redistribuir_gabaritos, vies_posicional,
        )

        depois = redistribuir_gabaritos(self._itens(list("BBBBBBBBBCCCCE")))
        v = vies_posicional(depois)
        for letra, fracao in v.items():
            self.assertLessEqual(fracao, 0.30,
                                 f"a letra {letra} ficou com {fracao:.0%}")
        self.assertGreater(min(v.values()), 0, "alguma letra ficou sem nenhum")

    def test_redistribuir_nao_perde_nem_inventa_alternativa(self):
        from agente_ia_edu.services.diagnostic_bank import redistribuir_gabaritos

        antes = self._itens(list("BBB"))
        depois = redistribuir_gabaritos(antes)
        for a, d in zip(antes, depois):
            self.assertEqual(sorted(a.options.values()), sorted(d.options.values()),
                             "o conjunto de alternativas mudou")
            self.assertEqual(a.options[a.correct_answer],
                             d.options[d.correct_answer],
                             "o TEXTO da resposta correta mudou")

    def test_redistribuir_nao_mexe_na_quimica(self):
        from agente_ia_edu.services.diagnostic_bank import redistribuir_gabaritos

        antes = self._itens(list("BBB"))
        for a, d in zip(antes, redistribuir_gabaritos(antes)):
            self.assertEqual(a.stem, d.stem)
            self.assertEqual(a.equacoes, d.equacoes)
            self.assertEqual(a.equacoes_balanceadas, d.equacoes_balanceadas)

    def test_e_deterministico(self):
        from agente_ia_edu.services.diagnostic_bank import redistribuir_gabaritos

        antes = self._itens(list("BBBBB"))
        um = [i.correct_answer for i in redistribuir_gabaritos(antes)]
        dois = [i.correct_answer for i in redistribuir_gabaritos(antes)]
        self.assertEqual(um, dois)
