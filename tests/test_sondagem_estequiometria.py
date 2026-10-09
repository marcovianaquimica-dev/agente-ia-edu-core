"""OS ITENS DO PILOTO — e a conta que confere cada gabarito.

Um item de sondagem errado não falha ruidosamente: ele mede errado, em
silêncio, e o erro entra no mapa de domínio como se fosse evidência. Por isso
os gabaritos aritméticos são REFEITOS aqui, e não conferidos por leitura.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.grafo_estequiometria import (
    GRAFO,
    INTEGRADO,
    LEITURA_FORMULA,
    MASSA_MOL,
    MASSA_MOLAR,
    PROPORCAO,
)
from agente_ia_edu.services.sondagem import roteiro_de_sondagem
from agente_ia_edu.services.sondagem_estequiometria import ITENS, conferir


class OSGABARITOSBATEM(unittest.TestCase):

    def test_as_contas_sao_refeitas_e_fecham(self):
        self.assertEqual([], conferir())

    def test_massa_molar_do_NH3_e_dezessete(self):
        """O item central do ciclo de referência: 14 + 3×1."""
        item = next(i for i in ITENS if i.habilidade == MASSA_MOLAR)
        self.assertEqual("17 g/mol", item.alternativas[item.correta])

    def test_o_integrado_exige_a_cadeia_inteira(self):
        """34 g de NH₃ ÷ 17 = 2 mol; 2 × 3/2 = 3 mol de H₂."""
        item = next(i for i in ITENS if i.habilidade == INTEGRADO)
        self.assertEqual("3 mol", item.alternativas[item.correta])


class CADAITEMMEDEUMAHABILIDADE(unittest.TestCase):

    def test_as_cinco_habilidades_do_piloto_estao_cobertas(self):
        self.assertEqual(
            {LEITURA_FORMULA, MASSA_MOLAR, MASSA_MOL, PROPORCAO, INTEGRADO},
            {i.habilidade for i in ITENS})

    def test_toda_habilidade_sondada_existe_no_grafo(self):
        for item in ITENS:
            with self.subTest(item=item.key):
                self.assertTrue(GRAFO.tem(item.habilidade))

    def test_o_item_de_massa_molar_da_as_massas_atomicas(self):
        """Sem elas, errar poderia significar "não sei calcular" OU "não
        lembro quanto vale o N" — e o mapa não saberia qual."""
        item = next(i for i in ITENS if i.habilidade == MASSA_MOLAR)
        self.assertIn("N = 14", item.enunciado)
        self.assertIn("H = 1", item.enunciado)

    def test_o_item_de_proporcao_da_a_equacao_ja_balanceada(self):
        """Balancear é outra habilidade, e de outro conteúdo."""
        item = next(i for i in ITENS if i.habilidade == PROPORCAO)
        self.assertIn("N₂ + 3 H₂ → 2 NH₃", item.enunciado)


class OROTEIROCOMECAPELABASE(unittest.TestCase):

    def test_a_leitura_da_formula_vem_primeiro(self):
        roteiro = roteiro_de_sondagem(GRAFO, ITENS)
        self.assertEqual(LEITURA_FORMULA, roteiro[0].habilidade)

    def test_o_problema_completo_vem_por_ultimo(self):
        roteiro = roteiro_de_sondagem(GRAFO, ITENS)
        self.assertEqual(INTEGRADO, roteiro[-1].habilidade)

    def test_massa_molar_vem_antes_de_massa_para_mol(self):
        ordem = [i.habilidade for i in roteiro_de_sondagem(GRAFO, ITENS)]
        self.assertLess(ordem.index(MASSA_MOLAR), ordem.index(MASSA_MOL))

    def test_uma_sondagem_curta_nao_comeca_pelo_problema_completo(self):
        curta = roteiro_de_sondagem(GRAFO, ITENS, tamanho=3)
        self.assertNotIn(INTEGRADO, [i.habilidade for i in curta])


class NENHUMITEMENTREGAARESPOSTA(unittest.TestCase):
    """Um enunciado que já diz a resposta mede obediência, não conhecimento."""

    def test_o_enunciado_nao_contem_o_texto_da_correta(self):
        for item in ITENS:
            with self.subTest(item=item.key):
                correta = item.alternativas[item.correta]
                # "3" aparece legitimamente em "3 H₂"; o que não pode é a
                # alternativa inteira ("17 g/mol", "2 mol") estar no enunciado.
                if len(correta) > 2:
                    self.assertNotIn(correta, item.enunciado)

    def test_nenhuma_alternativa_se_repete_dentro_do_item(self):
        for item in ITENS:
            with self.subTest(item=item.key):
                textos = list(item.alternativas.values())
                self.assertEqual(len(textos), len(set(textos)))

    def test_todo_item_tem_pelo_menos_quatro_alternativas(self):
        """Com duas, acertar no chute é metade das vezes."""
        for item in ITENS:
            with self.subTest(item=item.key):
                self.assertGreaterEqual(len(item.alternativas), 4)


class ASONDAGEMNAODEPENDEDEIA(unittest.TestCase):

    def test_o_modulo_nao_importa_provedor(self):
        import pathlib

        fonte = (pathlib.Path(__file__).resolve().parent.parent
                 / "src/agente_ia_edu/services/sondagem_estequiometria.py"
                 ).read_text(encoding="utf-8")
        for proibido in ("provider", "TextGeneration", "openai", "anthropic"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido.lower(), fonte.lower())


if __name__ == "__main__":
    unittest.main()
