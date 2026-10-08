"""O MAPA DE ESTEQUIOMETRIA — o conteúdo do piloto.

O grafo genérico tem os seus testes, com um grafo sintético de matemática.
Este arquivo olha a outra metade: se o mapa DESTE conteúdo descreve a
pedagogia certa, e se ele continua ligado ao que o banco já classificou.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.grafo_estequiometria import (
    CONCEITO_MOL,
    GRAFO,
    INTEGRADO,
    LEITURA_COEFICIENTE,
    LEITURA_FORMULA,
    MASSA_MASSA,
    MASSA_MOL,
    MASSA_MOLAR,
    MOL_MOL,
    PROPORCAO,
)


class OMapaDescreveAPedagogiaCerta(unittest.TestCase):

    def test_massa_molar_exige_ler_a_formula(self):
        """Quem não lê o 3 do NH3 não tem como somar três hidrogênios."""
        self.assertIn(LEITURA_FORMULA, GRAFO.prerequisitos(MASSA_MOLAR))

    def test_converter_massa_em_mol_exige_massa_molar_e_o_conceito(self):
        self.assertEqual({MASSA_MOLAR, CONCEITO_MOL},
                         set(GRAFO.prerequisitos(MASSA_MOL)))

    def test_a_proporcao_exige_ler_o_coeficiente(self):
        self.assertIn(LEITURA_COEFICIENTE, GRAFO.prerequisitos(PROPORCAO))

    def test_massa_a_massa_passa_por_mol_e_por_proporcao(self):
        self.assertEqual({MASSA_MOL, PROPORCAO},
                         set(GRAFO.prerequisitos(MASSA_MASSA)))

    def test_o_problema_completo_fica_no_topo(self):
        """Nada depende dele: ele é o que só existe quando o resto existe."""
        dependem = [c for c in GRAFO.codigos()
                    if INTEGRADO in GRAFO.prerequisitos(c)]
        self.assertEqual([], dependem)
        self.assertTrue(GRAFO.prerequisitos(INTEGRADO))

    def test_as_tres_bases_nao_dependem_de_nada(self):
        for base in (LEITURA_FORMULA, CONCEITO_MOL, LEITURA_COEFICIENTE):
            with self.subTest(base=base):
                self.assertEqual((), GRAFO.prerequisitos(base))


class OGargaloDesceAteOndeDaParaAprender(unittest.TestCase):

    def test_quem_vai_mal_no_problema_completo_e_na_leitura_comeca_na_leitura(self):
        self.assertEqual(
            LEITURA_FORMULA,
            GRAFO.primeiro_gargalo({INTEGRADO, LEITURA_FORMULA}))

    def test_quem_so_erra_massa_molar_recebe_massa_molar(self):
        self.assertEqual(MASSA_MOLAR, GRAFO.primeiro_gargalo({MASSA_MOLAR}))

    def test_massa_molar_e_massa_a_massa_juntas_comecam_em_massa_molar(self):
        self.assertEqual(
            MASSA_MOLAR, GRAFO.primeiro_gargalo({MASSA_MOLAR, MASSA_MASSA}))

    def test_a_cadeia_inteira_fraca_comeca_numa_base(self):
        gargalo = GRAFO.primeiro_gargalo(set(GRAFO.codigos()))
        self.assertIn(gargalo,
                      {LEITURA_FORMULA, CONCEITO_MOL, LEITURA_COEFICIENTE})


class OSCODIGOSQUEOBANCOJAUSA(unittest.TestCase):
    """Renomear os quatro já classificados desligaria 20 questões do mapa."""

    JA_CLASSIFICADOS = (PROPORCAO, MASSA_MASSA, MASSA_MOL, MOL_MOL)

    def test_os_quatro_antigos_continuam_com_o_nome_que_tinham(self):
        self.assertEqual(
            ("PROPORCAO_ESTEQUIOMETRICA", "RELACAO_MASSA_MASSA",
             "RELACAO_MASSA_MOL", "RELACAO_MOL_MOL"),
            self.JA_CLASSIFICADOS)

    def test_e_todos_eles_estao_no_grafo(self):
        for code in self.JA_CLASSIFICADOS:
            with self.subTest(code=code):
                self.assertTrue(GRAFO.tem(code))


class OALUNOLEPORTUGUES(unittest.TestCase):

    def test_toda_habilidade_tem_rotulo_e_objetivo(self):
        for code in GRAFO.codigos():
            with self.subTest(code=code):
                h = GRAFO.habilidade(code)
                self.assertTrue(h.label.strip())
                self.assertTrue(h.objetivo.strip())

    def test_nenhum_rotulo_e_o_proprio_codigo(self):
        for code in GRAFO.codigos():
            with self.subTest(code=code):
                self.assertNotEqual(code, GRAFO.rotulo(code))

    def test_nenhum_rotulo_carrega_codigo_interno(self):
        for code in GRAFO.codigos():
            with self.subTest(code=code):
                rotulo = GRAFO.rotulo(code)
                self.assertNotIn("_", rotulo)
                self.assertNotEqual(rotulo, rotulo.upper())


if __name__ == "__main__":
    unittest.main()
