"""O QUE O EDU DIZ NA PRIMEIRA VEZ QUE O ALUNO ERRA UMA ETAPA.

MEDIDO NO NAVEGADOR, EM 2026-10-06
===================================
Aluno QA, investigação de massa molar, etapa 1 ("Na fórmula NH₃, quantos
átomos de hidrogênio?"), marcou A. O Edu respondeu:

    "O número pequeno colado no símbolo conta os átomos daquele elemento:
     em NH₃ são três hidrogênios."

A regra está certa, e a frase é gentil. Mas ela entrega o número. O aluno
clica em C sem ter recontado nada, a etapa fica registrada como resolvida, e
o sistema conclui que aquela micro-habilidade está de pé quando a única
coisa que aconteceu foi ele ler a resposta.

Isso é o §10 do bloco acontecendo dentro da investigação: a resposta certa
chegando antes da recuperação.

A CORREÇÃO
===========
Dois níveis de retorno, e o que os separa é a TENTATIVA:

    1ª vez   a REGRA, sem aplicá-la a este item — ele reconta
    2ª vez   a regra APLICADA, porque insistir em esconder vira castigo

Não é esconder para sempre: é esconder por uma tentativa. Quem errou duas
vezes já mostrou que a regra sozinha não bastou.

O QUE ESTE ARQUIVO TRAVA
=========================
Que a PISTA — o retorno da primeira vez — não contenha o valor da resposta,
nem em algarismo nem por extenso. É a asserção que o teste anterior não
fazia, e por isso o problema chegou ao navegador.
"""

from __future__ import annotations

import re
import unittest

from agente_ia_edu.services.investigacao_do_erro import (
    INVESTIGACOES,
    para_o_aluno,
)

# "3" e "três" são a mesma entrega. Sem a segunda coluna, a frase que
# derrubou este teste no navegador passaria.
#
# A COMPARAÇÃO É POR PALAVRA INTEIRA, e não por substring: "alguma" contém
# "uma", e proibir substring reprovaria texto correto.
#
# E "um"/"uma" NÃO estão na tabela, de propósito. A regra que a pista precisa
# ensinar é "a massa molar diz quanto pesa UM MOL da substância" — proibir a
# palavra proibiria a própria explicação. Para o valor 1 sobra a checagem do
# algarismo, que é o que o aluno procuraria entre as alternativas.
POR_EXTENSO = {
    "0,5": ("meio", "metade"),
    "2": ("dois", "duas"),
    "3": ("três", "tres"),
    "4": ("quatro",),
    "6": ("seis",),
    "9": ("nove",),
    "12": ("doze",),
    "17": ("dezessete",),
    "17,0": ("dezessete",),
    "18": ("dezoito",),
    "44": ("quarenta e quatro",),
}


def _valor(texto: str) -> str:
    """O número da alternativa, sem a unidade: "17,0 g" -> "17,0"."""
    m = re.match(r"^\s*([\d.,]+)", texto)
    return (m.group(1).rstrip(",.") if m else texto).strip()


class APISTANAOENTREGAORESULTADO(unittest.TestCase):
    """A asserção que faltava."""

    def test_a_pista_nao_traz_o_algarismo_da_resposta(self):
        for inv in INVESTIGACOES:
            for e in inv.etapas:
                valor = _valor(e.alternativas[e.correta])
                with self.subTest(inv=inv.key, ordem=e.ordem, valor=valor):
                    # \b não funciona com vírgula decimal; a comparação é por
                    # ocorrência cercada de não-dígito.
                    self.assertFalse(
                        re.search(rf"(?<![\d,]){re.escape(valor)}(?![\d,])",
                                  e.pista),
                        f"a pista entrega {valor!r}: {e.pista!r}")

    def test_nem_o_resultado_por_extenso(self):
        for inv in INVESTIGACOES:
            for e in inv.etapas:
                valor = _valor(e.alternativas[e.correta])
                for palavra in POR_EXTENSO.get(valor, ()):
                    with self.subTest(inv=inv.key, ordem=e.ordem,
                                      palavra=palavra):
                        self.assertIsNone(
                            re.search(rf"\b{re.escape(palavra)}\b",
                                      e.pista.lower()),
                            f"a pista entrega {palavra!r}: {e.pista!r}")

    def test_e_a_pista_ainda_ENSINA(self):
        """Esconder o número sem dizer nada seria só esconder."""
        for inv in INVESTIGACOES:
            for e in inv.etapas:
                with self.subTest(inv=inv.key, ordem=e.ordem):
                    self.assertGreaterEqual(len(e.pista.split()), 10)

    def test_a_pista_nao_acusa_o_aluno(self):
        for inv in INVESTIGACOES:
            for e in inv.etapas:
                baixo = e.pista.lower()
                for frase in ("você errou", "você esqueceu", "você confundiu",
                              "você não"):
                    with self.subTest(inv=inv.key, ordem=e.ordem, frase=frase):
                        self.assertNotIn(frase, baixo)

    def test_a_pista_e_diferente_do_retorno_completo(self):
        """Se fossem iguais, não haveria dois níveis — haveria um repetido."""
        for inv in INVESTIGACOES:
            for e in inv.etapas:
                with self.subTest(inv=inv.key, ordem=e.ordem):
                    self.assertNotEqual(e.pista.strip(), e.se_errar.strip())


class OSEGUNDOERROJAPODEAPLICARAREGRA(unittest.TestCase):
    """Esconder para sempre vira castigo, não pedagogia."""

    def test_o_retorno_completo_existe_para_toda_etapa(self):
        for inv in INVESTIGACOES:
            for e in inv.etapas:
                with self.subTest(inv=inv.key, ordem=e.ordem):
                    self.assertGreaterEqual(len(e.se_errar.split()), 12)


class AVISAOESCOLHEPELATENTATIVA(unittest.TestCase):

    def setUp(self):
        self.inv = INVESTIGACOES[0]
        self.etapa = self.inv.etapas[0]
        self.errada = next(k for k in self.etapa.alternativas
                           if k != self.etapa.correta)

    def _visao(self, tentativas):
        return para_o_aluno(self.inv, {1: self.errada},
                            tentativas=tentativas)

    def test_na_primeira_tentativa_vem_a_pista(self):
        self.assertEqual(self.etapa.pista,
                         self._visao({1: 1})["retorno"]["comentario"])

    def test_na_segunda_vem_a_regra_aplicada(self):
        self.assertEqual(self.etapa.se_errar,
                         self._visao({1: 2})["retorno"]["comentario"])

    def test_na_terceira_continua_a_regra_aplicada(self):
        self.assertEqual(self.etapa.se_errar,
                         self._visao({1: 5})["retorno"]["comentario"])

    def test_sem_contagem_nenhuma_o_padrao_e_a_pista(self):
        """Na dúvida, esconder o número — o caminho que ensina mais."""
        self.assertEqual(self.etapa.pista,
                         para_o_aluno(self.inv, {1: self.errada})
                         ["retorno"]["comentario"])

    def test_quem_nao_errou_nao_recebe_retorno_nenhum(self):
        self.assertIsNone(para_o_aluno(self.inv, {})["retorno"])

    def test_o_nivel_viaja_para_a_tela_saber_que_ha_mais(self):
        self.assertEqual(1, self._visao({1: 1})["retorno"]["nivel"])
        self.assertEqual(2, self._visao({1: 2})["retorno"]["nivel"])


if __name__ == "__main__":
    unittest.main()
