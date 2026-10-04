"""Subscrito quimico: a diferenca entre COEFICIENTE e INDICE.

    2 H₂O
    ^ ^
    |  +-- INDICE: quantos atomos de H existem na molecula -> subscrito
    +----- COEFICIENTE: quantas moleculas -> tamanho normal

Trocar todo digito por subscrito escreveria `₂ H₂O`, que diz outra coisa. Por
isso isto e uma funcao com teste, e nao um replace no JavaScript.

De onde veio o problema: 4 dos 14 itens do Nucleo Diagnostic Bank nasceram com
formula ASCII (`H2 + O2 -> H2O`) e 10 com subscrito Unicode (`H₂ + O₂`). O
gerador nao foi instruido a padronizar, e o aluno ve as duas notacoes na mesma
sessao de tres perguntas - observado no teste humano de 2026-10-04.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.formula_quimica import subscrever


class CoeficienteVersusIndiceTests(unittest.TestCase):
    """O caso que torna um replace ingenuo errado."""

    def test_indice_vira_subscrito(self):
        self.assertEqual(subscrever("H2O"), "H₂O")

    def test_coeficiente_NAO_vira_subscrito(self):
        """O 2 da frente conta moleculas, nao atomos."""
        self.assertEqual(subscrever("2 H2O"), "2 H₂O")

    def test_a_equacao_inteira(self):
        self.assertEqual(subscrever("2 H2 + 1 O2 → 2 H2O"),
                         "2 H₂ + 1 O₂ → 2 H₂O")

    def test_coeficiente_colado_na_formula_continua_coeficiente(self):
        """`2H2O` tambem ocorre. O primeiro digito abre o termo - nao ha letra
        antes dele -, entao e coeficiente."""
        self.assertEqual(subscrever("2H2O"), "2H₂O")

    def test_elemento_de_dois_caracteres(self):
        self.assertEqual(subscrever("Fe2O3"), "Fe₂O₃")

    def test_indice_de_varios_digitos(self):
        self.assertEqual(subscrever("C12H22O11"), "C₁₂H₂₂O₁₁")

    def test_parenteses(self):
        self.assertEqual(subscrever("Ca(OH)2"), "Ca(OH)₂")

    def test_parenteses_com_indice_interno(self):
        self.assertEqual(subscrever("(NH4)2SO4"), "(NH₄)₂SO₄")


class OQueNaoPodeSerTocadoTests(unittest.TestCase):
    """Fail-closed: na duvida, nao mexe. Texto escrito errado e pior que texto
    sem subscrito."""

    def test_carga_de_ion_nao_e_indice(self):
        """Fe3+ e ferro(III), nao tres atomos de ferro."""
        self.assertEqual(subscrever("Fe3+"), "Fe3+")

    def test_carga_negativa_tambem(self):
        self.assertEqual(subscrever("SO4 2-"), "SO₄ 2-")

    def test_ja_subscrito_fica_como_esta(self):
        """Idempotente: os 10 itens que ja nasceram certos nao podem mudar."""
        self.assertEqual(subscrever("2 H₂ + 1 O₂ → 2 H₂O"),
                         "2 H₂ + 1 O₂ → 2 H₂O")

    def test_aplicar_duas_vezes_da_o_mesmo(self):
        uma = subscrever("Fe2O3 + 3 CO → 2 Fe + 3 CO2")
        self.assertEqual(subscrever(uma), uma)

    def test_numero_solto_no_texto_corrido_nao_e_tocado(self):
        """O enunciado tem portugues, nao so equacao."""
        self.assertEqual(
            subscrever("Considere as 3 equações abaixo e marque 1 alternativa."),
            "Considere as 3 equações abaixo e marque 1 alternativa.")

    def test_palavra_com_numero_nao_vira_formula(self):
        """`item2` nao e uma molecula. Exige inicial MAIUSCULA, como todo
        simbolo de elemento."""
        self.assertEqual(subscrever("veja o item2 da lista"),
                         "veja o item2 da lista")

    def test_nao_inventa_elemento(self):
        """`Xy2` nao e elemento nenhum - mas tambem nao e nosso trabalho
        recusar aqui: a conferencia quimica e de chemistry_balance. O que esta
        funcao NAO pode e quebrar o texto."""
        self.assertEqual(subscrever("Xy2"), "Xy₂")

    def test_texto_vazio_e_none(self):
        self.assertEqual(subscrever(""), "")
        self.assertEqual(subscrever(None), None)


class OBancoRealTests(unittest.TestCase):
    """Os 14 itens que o aluno de fato vê."""

    def test_as_quatro_equacoes_ascii_do_banco_ficam_certas(self):
        casos = {
            "1 H2 + 1 O2 → 1 H2O": "1 H₂ + 1 O₂ → 1 H₂O",
            "2 H2 + 1 O2 → 2 H2O": "2 H₂ + 1 O₂ → 2 H₂O",
            "1 CH4 + 1 O2 → 1 CO2 + 1 H2O": "1 CH₄ + 1 O₂ → 1 CO₂ + 1 H₂O",
            "2 CaCO3 → 2 CaO + 1 CO2": "2 CaCO₃ → 2 CaO + 1 CO₂",
            "1 Na + 1 Cl2 → 1 NaCl": "1 Na + 1 Cl₂ → 1 NaCl",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(subscrever(entrada), esperado)

    def test_NaCl_nao_ganha_subscrito_onde_nao_ha_numero(self):
        self.assertEqual(subscrever("NaCl"), "NaCl")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
