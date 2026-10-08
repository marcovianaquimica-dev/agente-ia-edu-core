"""Verificação DETERMINÍSTICA de estequiometria.

Em Balanceamento, a aritmética que não precisava de LLM era a conservação de
átomos. Em Estequiometria é mais: massa molar, proporção molar, e o **valor
numérico da resposta**. Tudo isso Python calcula — e o que Python calcula,
nenhum modelo precisa opinar.

A regra que estes testes protegem é a mesma de `chemistry_balance`:

    FAIL-CLOSED. "Não sei ler" nunca vira aprovação.

Um item cujo cálculo o verificador não consegue reproduzir vai para
REQUIRES_REVIEW, não para AI_VERIFIED. É melhor um banco menor e confiável que
um banco grande com itens que ninguém conferiu.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.chemistry_balance import FormulaInvalida
from agente_ia_edu.services.estequiometria import (
    DadosInsuficientes,
    massa_molar,
    proporcao_molar,
    resolver_massa_massa,
    resolver_mol_mol,
)


class MassaMolarTests(unittest.TestCase):
    """Massas atômicas da IUPAC, arredondadas como o Ensino Médio usa."""

    def test_agua(self):
        self.assertAlmostEqual(massa_molar("H2O"), 18.0, places=1)

    def test_gas_oxigenio(self):
        self.assertAlmostEqual(massa_molar("O2"), 32.0, places=1)

    def test_oxido_de_ferro_III(self):
        self.assertAlmostEqual(massa_molar("Fe2O3"), 159.7, places=1)

    def test_carbonato_de_calcio(self):
        self.assertAlmostEqual(massa_molar("CaCO3"), 100.1, places=1)

    def test_glicose(self):
        self.assertAlmostEqual(massa_molar("C6H12O6"), 180.2, places=1)

    def test_subscrito_unicode(self):
        """O banco mistura notações; o parser já normaliza."""
        self.assertAlmostEqual(massa_molar("H₂O"), massa_molar("H2O"), places=4)

    def test_parenteses(self):
        self.assertAlmostEqual(massa_molar("Ca(OH)2"), 74.1, places=1)

    def test_elemento_inexistente_e_recusado(self):
        """Fail-closed: um elemento imaginário fecharia a conta com massa
        inventada."""
        with self.assertRaises(FormulaInvalida):
            massa_molar("Xx2O")

    def test_formula_vazia_e_recusada(self):
        with self.assertRaises(FormulaInvalida):
            massa_molar("")


class ProporcaoMolarTests(unittest.TestCase):

    def test_le_os_coeficientes_da_equacao(self):
        p = proporcao_molar("2 H2 + 1 O2 -> 2 H2O")
        self.assertEqual(p["H2"], 2)
        self.assertEqual(p["O2"], 1)
        self.assertEqual(p["H2O"], 2)

    def test_coeficiente_omitido_vale_um(self):
        p = proporcao_molar("CH4 + 2 O2 -> CO2 + 2 H2O")
        self.assertEqual(p["CH4"], 1)
        self.assertEqual(p["CO2"], 1)
        self.assertEqual(p["O2"], 2)

    def test_equacao_desbalanceada_e_recusada(self):
        """Proporção molar lida de equação que não fecha é proporção errada —
        e é exatamente esse o erro que um LLM comete sem perceber."""
        with self.assertRaises(FormulaInvalida):
            proporcao_molar("H2 + O2 -> H2O")

    def test_estado_fisico_nao_esconde_a_especie(self):
        """`O2(g)` e `O2` são a mesma espécie.

        O gerador escreve o estado físico porque é assim que a química se
        escreve. Sem normalizar, o verificador respondia "O2 não está na
        equação: ['KCl(s)', 'KClO3(s)', 'O2(g)']" e rejeitava química
        CORRETA — 4 dos 16 itens gerados caíram por isso, e o fail-closed
        estava certo em recusar o que não entendia: quem estava errado era o
        parser, por ser estreito demais.
        """
        p = proporcao_molar("2 KClO3(s) -> 2 KCl(s) + 3 O2(g)")
        self.assertEqual(p["O2"], 3)
        self.assertEqual(p["KClO3"], 2)

    def test_estado_fisico_tambem_na_resolucao(self):
        r = resolver_mol_mol("2 KClO3(s) -> 2 KCl(s) + 3 O2(g)",
                             de="KClO3", para="O2", mols=2.0)
        self.assertAlmostEqual(r, 3.0, places=4)

    def test_especie_aquosa(self):
        p = proporcao_molar("2 Al(s) + 3 Cl2(g) -> 2 AlCl3(s)")
        self.assertEqual(p["Cl2"], 3)
        self.assertEqual(p["AlCl3"], 2)

    def test_especie_ausente_nao_vira_zero(self):
        p = proporcao_molar("2 H2 + 1 O2 -> 2 H2O")
        self.assertNotIn("CO2", p)


class RelacaoMolMolTests(unittest.TestCase):

    def test_quantos_mols_de_produto(self):
        """4 mol de H2 → 4 mol de H2O (proporção 2:2)."""
        self.assertAlmostEqual(
            resolver_mol_mol("2 H2 + 1 O2 -> 2 H2O", de="H2", para="H2O", mols=4.0),
            4.0, places=4)

    def test_proporcao_diferente_de_um(self):
        """1 mol de O2 → 2 mol de H2O (proporção 1:2)."""
        self.assertAlmostEqual(
            resolver_mol_mol("2 H2 + 1 O2 -> 2 H2O", de="O2", para="H2O", mols=1.0),
            2.0, places=4)

    def test_especie_fora_da_equacao_e_recusada(self):
        with self.assertRaises(DadosInsuficientes):
            resolver_mol_mol("2 H2 + 1 O2 -> 2 H2O", de="CO2", para="H2O", mols=1.0)


class RelacaoMassaMassaTests(unittest.TestCase):

    def test_massa_de_produto_a_partir_de_massa_de_reagente(self):
        """4 g de H2 → 35,7 g de H2O.

        O livro escolar diz 36, usando H=1 e O=16. Com as massas da IUPAC
        (H=1,008; O=15,999) o valor é 35,74 — e é este que o verificador
        calcula. A diferença de 0,7% é exatamente a razão de a comparação com
        o gabarito do item ser por TOLERÂNCIA RELATIVA, e não por igualdade:
        um item escolar correto não pode ser rejeitado por arredondamento.
        """
        self.assertAlmostEqual(
            resolver_massa_massa("2 H2 + 1 O2 -> 2 H2O",
                                 de="H2", para="H2O", massa=4.0),
            35.74, places=1)

    def test_caso_classico_do_ferro(self):
        """160 g de Fe2O3 (1 mol) → 2 mol Fe → 111,7 g."""
        r = resolver_massa_massa("Fe2O3 + 3 CO -> 2 Fe + 3 CO2",
                                 de="Fe2O3", para="Fe", massa=159.7)
        self.assertAlmostEqual(r, 111.7, places=0)

    def test_massa_negativa_e_recusada(self):
        with self.assertRaises(DadosInsuficientes):
            resolver_massa_massa("2 H2 + 1 O2 -> 2 H2O",
                                 de="H2", para="H2O", massa=-1.0)

    def test_equacao_nao_balanceada_e_recusada(self):
        """O erro mais traiçoeiro: a conta FECHA aritmeticamente, mas sobre uma
        proporção que a química não sustenta."""
        with self.assertRaises(FormulaInvalida):
            resolver_massa_massa("H2 + O2 -> H2O",
                                 de="H2", para="H2O", massa=4.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
