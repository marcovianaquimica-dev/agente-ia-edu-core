"""O contrato de aprovação dos itens de Estequiometria.

Mesma arquitetura do banco de Balanceamento, outro conteúdo. O que muda é a
verificação determinística: lá era conservação de átomos, aqui é massa molar,
proporção molar e **o valor numérico da resposta**.

OS CASOS ADVERSARIAIS SÃO O ASSUNTO
====================================
Cada classe abaixo constrói um item DEFEITUOSO de propósito e exige que o
contrato o recuse. Um verificador que só aprova itens bons não prova nada —
o que prova é ele recusar o que um LLM deixaria passar.

O item que mais importa é o da equação não balanceada: a regra de três fecha
perfeitamente sobre coeficientes errados, o número que sai parece certo, e um
segundo LLM refaria a mesma conta sobre a mesma equação errada.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.diagnostic_bank_estequiometria import (
    HABILIDADES,
    SKILL_MASSA_MASSA,
    SKILL_MOL_MOL,
    SKILL_PROPORCAO,
    ItemEstequiometria,
    conferir_calculo,
    conferir_estrutura,
)


def _item(**kw) -> ItemEstequiometria:
    base = dict(
        diagnostic_skill=SKILL_MASSA_MASSA,
        diagnostic_objective=HABILIDADES[SKILL_MASSA_MASSA],
        difficulty="MEDIUM",
        stem="Quantos gramas de H₂O se formam a partir de 4 g de H₂?",
        # 17,9 e 18,0 distariam 0,56%, DENTRO da tolerancia de 1% - o
        # verificador os trataria como a mesma alternativa, e com razao: o
        # aluno nao conseguiria escolher entre eles. Distratores precisam ser
        # distinguiveis, e o contrato cobra isso.
        options={"A": "17,9 g", "B": "35,7 g", "C": "71,5 g",
                 "D": "4,0 g", "E": "9,0 g"},
        correct_answer="B",
        rationale="4 g / 2 g·mol⁻¹ = 2 mol H₂ → 2 mol H₂O → 35,7 g",
        equacao="2 H2 + 1 O2 -> 2 H2O",
        especie_de="H2",
        especie_para="H2O",
        quantidade=4.0,
        unidade_entrada="g",
        unidade_saida="g",
        generator_version="teste",
    )
    base.update(kw)
    return ItemEstequiometria(**base)


class ItemBomTests(unittest.TestCase):

    def test_o_item_de_referencia_passa(self):
        v = conferir_calculo(_item())
        self.assertTrue(v.conferiu, v.problemas)
        self.assertEqual(conferir_estrutura(_item()), [])

    def test_o_valor_calculado_fica_registrado(self):
        """O relatório precisa dizer o número que a aritmética achou, não só
        'passou' — é isso que torna a aprovação auditável."""
        v = conferir_calculo(_item())
        self.assertAlmostEqual(v.detalhes["valor_calculado"], 35.74, places=1)

    def test_arredondamento_escolar_e_aceito(self):
        """Com H=1 e O=16 o livro escreve 36 g. São 0,7% de diferença — um
        item correto não pode ser rejeitado por arredondamento."""
        v = conferir_calculo(_item(options={"A": "17,9 g", "B": "36 g",
                                            "C": "71,5 g", "D": "4,0 g",
                                            "E": "9,0 g"}))
        self.assertTrue(v.conferiu, v.problemas)


class CasosAdversariaisTests(unittest.TestCase):
    """Cada um é um item que um LLM aprovaria."""

    def test_A_equacao_nao_balanceada_e_recusada(self):
        """O caso central. A regra de três fecha sobre coeficientes errados."""
        v = conferir_calculo(_item(equacao="H2 + O2 -> H2O"))
        self.assertFalse(v.conferiu)
        self.assertTrue(any("balanc" in p.lower() for p in v.problemas), v.problemas)

    def test_B_duas_alternativas_numericamente_corretas(self):
        v = conferir_calculo(_item(options={"A": "35,7 g", "B": "35,74 g",
                                            "C": "71,5 g", "D": "4,0 g",
                                            "E": "9,0 g"}))
        self.assertFalse(v.conferiu)
        self.assertTrue(any("mais de uma" in p.lower() for p in v.problemas),
                        v.problemas)

    def test_C_nenhuma_alternativa_correta(self):
        v = conferir_calculo(_item(options={"A": "1 g", "B": "2 g", "C": "3 g",
                                            "D": "4 g", "E": "5 g"}))
        self.assertFalse(v.conferiu)
        self.assertTrue(any("nenhuma" in p.lower() for p in v.problemas),
                        v.problemas)

    def test_D_gabarito_aponta_alternativa_errada(self):
        """O cálculo dá 35,7 (B), mas o gerador declarou C."""
        v = conferir_calculo(_item(correct_answer="C"))
        self.assertFalse(v.conferiu)
        self.assertTrue(any("gabarito" in p.lower() for p in v.problemas),
                        v.problemas)

    def test_E_especie_fora_da_equacao(self):
        v = conferir_calculo(_item(especie_para="CO2"))
        self.assertFalse(v.conferiu)

    def test_F_massa_negativa(self):
        v = conferir_calculo(_item(quantidade=-4.0))
        self.assertFalse(v.conferiu)

    def test_G_dado_ausente(self):
        v = conferir_calculo(_item(quantidade=None))
        self.assertFalse(v.conferiu)

    def test_H_elemento_inexistente(self):
        v = conferir_calculo(_item(equacao="2 Xx2 + 1 O2 -> 2 Xx2O",
                                   especie_de="Xx2", especie_para="Xx2O"))
        self.assertFalse(v.conferiu)

    def test_I_alternativas_numericamente_iguais(self):
        v = conferir_calculo(_item(options={"A": "35,7 g", "B": "35,7 g",
                                            "C": "71,5 g", "D": "4,0 g",
                                            "E": "9,0 g"}))
        self.assertFalse(v.conferiu)

    def test_J_alternativa_sem_numero_legivel(self):
        """Fail-closed: se não dá para ler o número, não dá para conferir."""
        v = conferir_calculo(_item(options={"A": "mais de 30 g", "B": "35,7 g",
                                            "C": "71,5 g", "D": "4,0 g",
                                            "E": "9,0 g"}))
        self.assertFalse(v.conferiu)


class EstruturaTests(unittest.TestCase):

    def test_habilidade_desconhecida_e_recusada(self):
        problemas = conferir_estrutura(_item(diagnostic_skill="INVENTADA"))
        self.assertTrue(problemas)

    def test_dependencia_visual_e_recusada(self):
        """'Observe o gráfico' num item que não tem gráfico."""
        problemas = conferir_estrutura(_item(
            stem="Observe o gráfico a seguir e calcule a massa de H₂O."))
        self.assertTrue(any("visual" in p.lower() for p in problemas), problemas)

    def test_cinco_alternativas_sao_exigidas(self):
        problemas = conferir_estrutura(_item(options={"A": "1 g", "B": "2 g"}))
        self.assertTrue(problemas)

    def test_gabarito_fora_das_letras(self):
        problemas = conferir_estrutura(_item(correct_answer="F"))
        self.assertTrue(problemas)

    def test_enunciado_vazio(self):
        problemas = conferir_estrutura(_item(stem="   "))
        self.assertTrue(problemas)


class MatrizTests(unittest.TestCase):

    def test_as_habilidades_tem_objetivo_declarado(self):
        for skill, objetivo in HABILIDADES.items():
            with self.subTest(skill=skill):
                self.assertTrue(objetivo.strip())
                self.assertIn("?", objetivo,
                              "o objetivo diagnostico e uma PERGUNTA sobre o aluno")

    def test_mol_mol_e_massa_massa_sao_habilidades_distintas(self):
        self.assertNotEqual(SKILL_MOL_MOL, SKILL_MASSA_MASSA)
        self.assertIn(SKILL_PROPORCAO, HABILIDADES)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
