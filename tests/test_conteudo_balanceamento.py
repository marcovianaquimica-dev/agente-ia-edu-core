"""A química que o Núcleo ensina é conferida por contagem, não por convicção.

POR QUE ESTE ARQUIVO EXISTE
============================
Até agora o sistema só PERGUNTAVA química. A partir deste bloco ele ENSINA —
e ensinar errado é pior que não ensinar. Um enunciado errado o aluno pode
contestar; uma explicação errada ele decora.

Então o conteúdo da explicação e do exemplo resolvido é dado estruturado, no
código, e cada equação que ele cita passa pelo mesmo verificador
determinístico que já aprova os itens do banco diagnóstico
(`chemistry_balance`, contagem de átomos).

A REGRA QUE MAIS IMPORTA AQUI
==============================
Balancear ajusta COEFICIENTES, nunca índices. Trocar H₂O por H₂O₂ "para
fechar o oxigênio" transforma água em água oxigenada — é outra substância, e
é o erro conceitual exato que esta explicação existe para evitar. Há teste
exigindo que as espécies do último passo sejam as mesmas do primeiro.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.chemistry_balance import (
    atomos_da_formula,
    diferenca_de_atomos,
    equacao_balanceada,
    ler_equacao,
)
from agente_ia_edu.services.conteudo_balanceamento import (
    EXEMPLO_RESOLVIDO,
    MATERIAL,
    SECOES,
    blocos_do_exemplo,
    equacoes_citadas,
    para_exibicao,
)


class TodaEquacaoCitadaEhConferidaTests(unittest.TestCase):

    def test_cada_equacao_do_conteudo_e_legivel(self):
        """Uma equação que o verificador não lê é uma que ninguém conferiu."""
        citadas = equacoes_citadas()
        self.assertTrue(citadas, "o conteudo nao cita nenhuma equacao")
        for eq, _esperado in citadas:
            with self.subTest(equacao=eq):
                ler_equacao(eq)       # levanta FormulaInvalida se nao ler

    def test_cada_equacao_esta_no_estado_que_o_conteudo_declara(self):
        """O conteúdo DECLARA, para cada equação, se ela está balanceada. A
        declaração é conferida por contagem — não aceita na palavra."""
        for eq, esperado in equacoes_citadas():
            with self.subTest(equacao=eq, esperado=esperado):
                self.assertEqual(
                    equacao_balanceada(eq), esperado,
                    f"{eq!r}: o conteudo diz balanceada={esperado} e a "
                    f"contagem de atomos diz o contrario")


class OExemploResolvidoChegaAoFimTests(unittest.TestCase):

    def test_o_primeiro_passo_parte_de_uma_equacao_desbalanceada(self):
        """Um exemplo que começa pronto não ensina nada."""
        self.assertFalse(equacao_balanceada(EXEMPLO_RESOLVIDO["passos"][0]["equacao"]))

    def test_o_ultimo_passo_termina_balanceado(self):
        self.assertTrue(equacao_balanceada(EXEMPLO_RESOLVIDO["passos"][-1]["equacao"]))

    def test_cada_passo_chega_mais_perto_ou_fecha(self):
        """Um passo que piora a situação confundiria quem está aprendendo."""
        faltando = [len(diferenca_de_atomos(p["equacao"]))
                    for p in EXEMPLO_RESOLVIDO["passos"]]
        for anterior, atual in zip(faltando, faltando[1:]):
            self.assertLessEqual(atual, anterior,
                                 f"um passo aumentou o desequilibrio: {faltando}")
        self.assertEqual(faltando[-1], 0)

    def test_a_contagem_mostrada_e_a_contagem_real(self):
        """Os números que o aluno lê na tela vêm do verificador, não de um
        texto escrito à mão que pode envelhecer sem ninguém notar."""
        for passo in EXEMPLO_RESOLVIDO["passos"]:
            esquerda, direita = ler_equacao(passo["equacao"])
            elementos = sorted(set(esquerda) | set(direita))
            with self.subTest(equacao=passo["equacao"]):
                self.assertEqual(
                    passo["contagem"],
                    {el: [esquerda.get(el, 0), direita.get(el, 0)]
                     for el in elementos})


class SoCoeficientesMudamTests(unittest.TestCase):
    """O erro conceitual que esta explicação existe para evitar."""

    @staticmethod
    def _especies(equacao: str) -> list[str]:
        """As fórmulas, sem os coeficientes."""
        fora = []
        for lado in equacao.replace("->", "→").split("→"):
            for termo in lado.split("+"):
                t = termo.strip()
                while t and (t[0].isdigit() or t[0] == " "):
                    t = t[1:].strip()
                if t:
                    fora.append(t)
        return fora

    def test_as_substancias_do_fim_sao_as_do_comeco(self):
        passos = EXEMPLO_RESOLVIDO["passos"]
        self.assertEqual(self._especies(passos[-1]["equacao"]),
                         self._especies(passos[0]["equacao"]),
                         "o exemplo trocou uma substancia por outra para "
                         "'fechar' a conta - isso nao e balancear")

    def test_cada_formula_do_exemplo_e_lida_atomo_a_atomo(self):
        for formula in self._especies(EXEMPLO_RESOLVIDO["passos"][0]["equacao"]):
            with self.subTest(formula=formula):
                self.assertTrue(atomos_da_formula(formula))


class AEquacaoEhLIDAComoQuimicaTests(unittest.TestCase):
    """`H2 + O2 -> H2O` é como a equação se escreve para o verificador. Não é
    como ela se lê: o aluno estuda H₂ + O₂ → H₂O, e ver as duas grafias na
    mesma tela — uma no título, outra no passo — faz parecer que são coisas
    diferentes.

    A formatação mora aqui, junto da química, e não na tela: assim ela é
    conferida, e a string que o verificador recebe continua intocada.
    """

    def test_os_indices_viram_subscrito(self):
        self.assertEqual(para_exibicao("H2 + O2 -> H2O"), "H₂ + O₂ → H₂O")

    def test_o_coeficiente_da_frente_NAO_vira_subscrito(self):
        """Confundir os dois na tela ensinaria exatamente o erro que a
        explicação existe para desfazer."""
        self.assertEqual(para_exibicao("2 H2 + O2 -> 2 H2O"),
                         "2 H₂ + O₂ → 2 H₂O")

    def test_a_seta_vira_seta(self):
        self.assertIn("→", para_exibicao("A -> B"))
        self.assertNotIn("->", para_exibicao("A -> B"))

    def test_toda_equacao_do_exemplo_tem_versao_de_leitura(self):
        for p in blocos_do_exemplo()[0]["metadata"]["passos"]:
            with self.subTest(equacao=p["equacao"]):
                self.assertTrue(p["equacao_exibicao"])
                self.assertNotIn("->", p["equacao_exibicao"])

    def test_a_versao_de_leitura_nao_substitui_a_que_e_conferida(self):
        """A string crua continua no dado: é ela que o verificador lê."""
        for p in blocos_do_exemplo()[0]["metadata"]["passos"]:
            with self.subTest(equacao=p["equacao"]):
                equacao_balanceada(p["equacao"])


class OMaterialEhUtilizavelPelaInfraExistenteTests(unittest.TestCase):
    """Nada aqui inventa uma segunda arquitetura de conteúdo: o material é
    TheoryMaterial/Section/Block da PHASE 23, e `block_type` é livre."""

    def test_o_material_declara_o_conteudo_do_catalogo(self):
        self.assertEqual(MATERIAL["content_code"], "CHEMISTRY-GENERAL-BALANCING")

    def test_ha_secoes_com_blocos_posicionados_a_partir_de_um(self):
        self.assertTrue(SECOES)
        for i, secao in enumerate(SECOES, start=1):
            with self.subTest(secao=secao["title"]):
                self.assertEqual(secao["position"], i)
                self.assertTrue(secao["blocks"])
                for j, bloco in enumerate(secao["blocks"], start=1):
                    self.assertEqual(bloco["position"], j)
                    self.assertTrue((bloco.get("body") or "").strip()
                                    or bloco.get("metadata"))

    def test_o_exemplo_vira_blocos_sem_texto_escrito_a_mao(self):
        blocos = blocos_do_exemplo()
        self.assertTrue(blocos)
        self.assertEqual(blocos[0]["block_type"], "SOLVED_EXAMPLE")
        passos = blocos[0]["metadata"]["passos"]
        self.assertEqual(len(passos), len(EXEMPLO_RESOLVIDO["passos"]))

    def test_o_conteudo_nao_fala_a_lingua_do_sistema(self):
        """O aluno não lê `readiness` nem `BAND_IMPROVEMENT`."""
        textos = [b.get("title", "") + " " + (b.get("body") or "")
                  for s in SECOES for b in s["blocks"]]
        textos += [s["title"] for s in SECOES]
        for t in textos:
            for interno in ("readiness", "origin_breakdown", "BAND_",
                            "DIAGNOSTIC", "content_code", "diagnostic_skill"):
                with self.subTest(interno=interno):
                    self.assertNotIn(interno, t)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
