"""A ARITMÉTICA DA ESTEQUIOMETRIA, CONFERIDA — porque ensinar errado é pior.

Quando o sistema só PERGUNTA química, um enunciado errado o aluno contesta.
Quando ele ENSINA, uma conta errada o aluno decora. O conteúdo curado deste
bloco afirma, entre outras coisas, que a massa molar do NH₃ é 17 g/mol — e
essa afirmação precisa ser refeita por uma conta, não revisada por leitura.

É o mesmo papel que `chemistry_balance` já cumpre para o balanceamento: a
suíte reconta os átomos e derruba o conteúdo antes do aluno.

O QUE ESTE ARQUIVO TRAVA
=========================
1. a massa molar sai da fórmula e da tabela, nunca de um número escrito à mão;
2. a tabela tem só os elementos que o conteúdo usa, com valores de livro;
3. as conversões (massa→mol, mol→massa) fecham nos dois sentidos;
4. fórmula que o parser não entende levanta erro em vez de devolver zero.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.chemistry_balance import FormulaInvalida
from agente_ia_edu.services.massa_molar import (
    MASSAS_ATOMICAS,
    contribuicoes,
    massa_molar,
    massa_para_mol,
    mol_para_massa,
)


class AMassaMolarSaiDaFormula(unittest.TestCase):

    def test_nh3_da_17(self):
        """O caso curado do bloco: 1x14 + 3x1."""
        self.assertAlmostEqual(17.0, massa_molar("NH3"))

    def test_n2_da_28(self):
        self.assertAlmostEqual(28.0, massa_molar("N2"))

    def test_h2_da_2(self):
        self.assertAlmostEqual(2.0, massa_molar("H2"))

    def test_agua_da_18(self):
        self.assertAlmostEqual(18.0, massa_molar("H2O"))

    def test_elemento_sem_massa_na_tabela_falha(self):
        """Devolver zero seria ensinar que o xenônio não pesa."""
        with self.assertRaises(KeyError):
            massa_molar("Xe")

    def test_formula_que_o_parser_nao_entende_falha(self):
        with self.assertRaises(FormulaInvalida):
            massa_molar("")


class AsContribuicoesSaoAEXPLICACAO(unittest.TestCase):
    """O aluno não precisa do total: precisa de ver de onde ele vem."""

    def test_nh3_mostra_14_de_n_e_3_de_h(self):
        self.assertEqual(
            [("N", 1, 14.0, 14.0), ("H", 3, 1.0, 3.0)],
            contribuicoes("NH3"))

    def test_a_soma_das_contribuicoes_e_a_massa_molar(self):
        for formula in ("NH3", "N2", "H2O", "H2", "CO2"):
            with self.subTest(formula=formula):
                self.assertAlmostEqual(
                    massa_molar(formula),
                    sum(c[3] for c in contribuicoes(formula)))

    def test_a_ordem_segue_a_formula_e_nao_o_alfabeto(self):
        """Em H₂O o aluno lê H primeiro; listar O antes confundiria."""
        self.assertEqual(["H", "O"], [c[0] for c in contribuicoes("H2O")])


class AsConversoesFecham(unittest.TestCase):

    def test_14_g_de_n2_sao_meio_mol(self):
        """O caso de aceitação N2/NH3 começa exatamente aqui."""
        self.assertAlmostEqual(0.5, massa_para_mol(14.0, "N2"))

    def test_1_mol_de_nh3_pesa_17_g(self):
        self.assertAlmostEqual(17.0, mol_para_massa(1.0, "NH3"))

    def test_ir_e_voltar_devolve_o_mesmo(self):
        for formula, massa in (("NH3", 34.0), ("N2", 14.0), ("H2O", 36.0)):
            with self.subTest(formula=formula):
                self.assertAlmostEqual(
                    massa, mol_para_massa(massa_para_mol(massa, formula), formula))


class ATabelaEDELIVRO(unittest.TestCase):

    def test_os_valores_usados_pelo_conteudo(self):
        esperado = {"H": 1.0, "C": 12.0, "N": 14.0, "O": 16.0}
        for el, m in esperado.items():
            with self.subTest(el=el):
                self.assertAlmostEqual(m, MASSAS_ATOMICAS[el])

    def test_nenhuma_massa_e_negativa_ou_zero(self):
        for el, m in MASSAS_ATOMICAS.items():
            with self.subTest(el=el):
                self.assertGreater(m, 0)


if __name__ == "__main__":
    unittest.main()
