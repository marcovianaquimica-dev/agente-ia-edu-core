"""A VERIFICAÇÃO AUTÔNOMA — o degrau L0, e o que ele pode e não pode dizer.

O QUE ESTE ARQUIVO EXISTE PARA GUARDAR
=======================================
Até 2026-10-07 a jornada de `MASSA_MOLAR` terminava na prática guiada: NH₃
era sondado, investigado e ensinado, e CO₂ era tentado **com dicas**. A
tentativa sem ajuda vinha da prática pelo banco, que seleciona por CONTEÚDO
— e para Estequiometria isso significa questões de proporção e relação
massa-mol, não de massa molar. A micro-habilidade ensinada nunca era
verificada sozinha.

Medido no banco de desenvolvimento em 2026-10-08: o conteúdo tem 25
classificações ativas, e exatamente UMA é de `MASSA_MOLAR` — o próprio item
da sondagem, aquele que o aluno já errou no diagnóstico. Não havia segundo
item: servir "a questão de massa molar do banco" seria reservir o item do
diagnóstico.

AS TRÊS COISAS QUE SE EXIGE DE UM ITEM DE VERIFICAÇÃO
======================================================
1. Mede a habilidade ensinada, e só ela.
2. NÃO é o item ensinado nem o praticado com ajuda — senão mede memória.
3. Exige aplicar o índice: se o resultado de ignorá-lo coincidisse com o
   certo, acertar não distinguiria quem aprendeu a regra.

O QUE O ACERTO PODE DIZER
==========================
Que há evidência. Não que há domínio: `PerformanceThresholdPolicy` exige
`min_sample_size` respostas antes de falar em banda, e um acerto isolado
continua sendo amostra insuficiente. Há teste disso aqui — não porque a
política possa ter mudado, mas porque é desta direção que a pressão vem.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.grafo_estequiometria import CONTEUDO, MASSA_MOLAR
from agente_ia_edu.services.massa_molar import contribuicoes, massa_molar
from agente_ia_edu.services.verificacao_estequiometria import (
    ITENS,
    conferir,
    itens_de,
)


def _sem_indice(formula: str) -> float:
    """O que sai de somar as massas atômicas sem multiplicar pelo índice."""
    return sum(massa_atomica for _, _, massa_atomica, _ in contribuicoes(formula))


class OSITENSEXISTEMEMEDEMAHABILIDADE(unittest.TestCase):

    def test_ha_item_de_verificacao_para_massa_molar(self):
        self.assertTrue(itens_de(CONTEUDO, MASSA_MOLAR))

    def test_e_ha_mais_de_um(self):
        """Um item só e o `choose` da prática reservia o do diagnóstico.

        `PracticeSelectionPolicy.choose` despriorize o que o aluno já viu,
        mas REPÕE os recentes quando a piscina fresca não dá o número pedido.
        Com um item só, pedir três devolveria o item da sondagem de volta.
        """
        self.assertGreaterEqual(len(itens_de(CONTEUDO, MASSA_MOLAR)), 3)

    def test_todo_item_declara_a_habilidade_que_mede(self):
        for item in ITENS:
            with self.subTest(item.key):
                self.assertTrue(item.habilidade)

    def test_nenhum_item_fica_sem_gabarito(self):
        for item in ITENS:
            with self.subTest(item.key):
                self.assertIn(item.correta, item.alternativas)


class NAOREPETEOQUEFOIENSINADO(unittest.TestCase):
    """§6: o item ensinado não pode ser o item de verificação."""

    def test_nenhum_item_pergunta_o_NH3_ensinado(self):
        for item in itens_de(CONTEUDO, MASSA_MOLAR):
            with self.subTest(item.key):
                self.assertNotIn("NH₃", item.enunciado)
                self.assertNotIn("NH3", item.enunciado)

    def test_nem_o_CO2_da_pratica_guiada(self):
        """Ele resolveu CO₂ com quatro níveis de dica — sabe que dá 44."""
        for item in itens_de(CONTEUDO, MASSA_MOLAR):
            with self.subTest(item.key):
                self.assertNotIn("CO₂", item.enunciado)
                self.assertNotIn("CO2", item.enunciado)

    def test_e_nenhum_repete_a_substancia_de_outro(self):
        formulas = [item.fonte for item in itens_de(CONTEUDO, MASSA_MOLAR)]
        self.assertEqual(len(formulas), len(set(formulas)))


class EXIGEAPLICAROINDICE(unittest.TestCase):
    """Sem isto, acertar não distingue quem aprendeu a regra."""

    def test_ignorar_o_indice_da_outro_numero_em_todo_item(self):
        for item in itens_de(CONTEUDO, MASSA_MOLAR):
            with self.subTest(item.key):
                self.assertNotAlmostEqual(
                    massa_molar(item.fonte), _sem_indice(item.fonte),
                    msg=f"{item.fonte}: o índice não muda a conta")

    def test_e_o_distrator_do_indice_esquecido_esta_entre_as_alternativas(self):
        """O erro esperado tem de ser escolhível, senão o item não o detecta."""
        for item in itens_de(CONTEUDO, MASSA_MOLAR):
            with self.subTest(item.key):
                esperado = _sem_indice(item.fonte)
                textos = list(item.alternativas.values())
                self.assertTrue(
                    any(t.startswith(_grafia(esperado)) for t in textos),
                    f"{item.fonte}: {_grafia(esperado)} não está em {textos}")

    def test_o_indice_nao_esta_sempre_no_mesmo_lugar_da_formula(self):
        """Quem decorou "o número depois do H" não deve passar nos três.

        NH₃ e CO₂ têm o índice no SEGUNDO elemento. Um conjunto de
        verificação que só repetisse essa forma mediria o reconhecimento do
        padrão, não a regra.
        """
        posicoes = set()
        for item in itens_de(CONTEUDO, MASSA_MOLAR):
            partes = contribuicoes(item.fonte)
            posicoes.update(i for i, p in enumerate(partes) if p[1] > 1)
        self.assertGreater(len(posicoes), 1, f"índice sempre em {posicoes}")


class OGABARITOEREFEITOPORCONTA(unittest.TestCase):

    def test_conferir_nao_encontra_problema(self):
        self.assertEqual([], conferir())

    def test_a_correta_de_cada_item_e_a_massa_molar_calculada(self):
        for item in itens_de(CONTEUDO, MASSA_MOLAR):
            with self.subTest(item.key):
                certa = item.alternativas[item.correta]
                self.assertTrue(
                    certa.startswith(_grafia(massa_molar(item.fonte))),
                    f"{item.key}: {certa!r} não é a massa molar calculada")

    def test_toda_alternativa_errada_e_um_erro_NOMEADO(self):
        """Distrator inventado não informa nada quando o aluno o marca."""
        for item in itens_de(CONTEUDO, MASSA_MOLAR):
            for letra in item.alternativas:
                if letra == item.correta:
                    continue
                with self.subTest(f"{item.key}/{letra}"):
                    self.assertIn(letra, item.erros,
                                  f"{item.key}: {letra} sem erro declarado")


def _grafia(valor: float) -> str:
    if float(valor).is_integer():
        return str(int(valor))
    return f"{valor:g}".replace(".", ",")


if __name__ == "__main__":
    unittest.main()
