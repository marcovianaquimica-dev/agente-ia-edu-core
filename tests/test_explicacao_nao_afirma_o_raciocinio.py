"""A IA NÃO AFIRMA O QUE SE PASSOU NA CABEÇA DO ALUNO.

MEDIDO NO NAVEGADOR, EM 2026-10-07, COM PROVEDOR REAL
======================================================
Aluno QA errou a verificação e abriu "Entenda o que aconteceu". Leu:

    "Você provavelmente marcou 6 mol por não perceber que os coeficientes
     da equação representam a proporção entre as substâncias."

Pediu outra explicação e leu:

    "Você provavelmente encontrou 2 mol de NH₃, mas dobrou a proporção de
     H₂ e chegou a 6 mol."

As duas afirmam uma OPERAÇÃO MENTAL. O sistema observou uma letra marcada —
nada mais. "Provavelmente" no começo não desfaz o "por não perceber que" nem
o "dobrou a proporção": o primeiro hesita, o resto afirma.

E A CAUSA ESTAVA NO PEDIDO
===========================
A regra 1 da v1 dizia "comece pelo que ele PROVAVELMENTE FEZ, a partir da
alternativa que marcou". O modelo obedeceu. O defeito é do prompt.

O QUE ESTE ARQUIVO TRAVA
=========================
O prompt corrente PROÍBE a afirmação e OFERECE a forma alternativa — uma
proibição sem alternativa produz texto evasivo. A v1 continua no registro:
ela é o que explicou para quem leu aquelas telas.

O QUE ELE NÃO PODE TRAVAR
==========================
A saída do modelo. Nenhum prompt garante obediência, e um teste que chamasse
o provedor seria lento, caro e intermitente. O que se trava aqui é o PEDIDO;
o comportamento é verificado no navegador, e está registrado no relatório.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.assessor_prompts import (
    VERSAO_ATUAL_DA_EXPLICACAO,
    prompt_da_explicacao,
)
from agente_ia_edu.services.explicacao_do_erro import ESTRATEGIAS

ATUAL = prompt_da_explicacao()


class OPROMPTPROIBEAFIRMAROQUEOALUNOFEZ(unittest.TestCase):

    def _texto(self, estrategia: str) -> str:
        return ATUAL.montar(contexto="Conteúdo: Estequiometria",
                            estrategia=estrategia)

    def _corrido(self, estrategia: str) -> str:
        import re
        return re.sub(r"\s+", " ", self._texto(estrategia)).lower()

    def test_a_proibicao_esta_no_prompt_de_toda_estrategia(self):
        for e in ESTRATEGIAS:
            with self.subTest(estrategia=e):
                self.assertIn("nunca afirme o que o aluno fez",
                              self._corrido(e))

    def test_o_prompt_explica_o_que_foi_observado_de_verdade(self):
        """Uma proibição sem o motivo é obedecida pela metade."""
        baixo = self._texto("CONCEITO").lower()
        self.assertIn("alternativa marcada", baixo)

    def test_o_prompt_OFERECE_a_forma_de_sugerir(self):
        """Proibir sem alternativa produz texto evasivo."""
        baixo = self._texto("CONCEITO").lower()
        self.assertIn("costuma aparecer quando", baixo)

    def test_o_prompt_manda_falar_do_caminho_e_nao_da_pessoa(self):
        baixo = self._texto("CONCEITO").lower()
        self.assertIn("nunca da pessoa", baixo)

    def test_o_prompt_NAO_pede_mais_o_que_ele_provavelmente_fez(self):
        """A frase que causou o defeito não pode voltar."""
        for e in ESTRATEGIAS:
            with self.subTest(estrategia=e):
                self.assertNotIn("comece pelo que ele provavelmente fez",
                                 self._corrido(e))


class OQUECONTINUAVALENDO(unittest.TestCase):
    """A v2 não pode ter perdido nada que a v1 garantia."""

    def _texto(self, estrategia: str = "CONCEITO") -> str:
        return ATUAL.montar(contexto="Conteúdo: X", estrategia=estrategia)

    def _corrido(self, estrategia: str = "CONCEITO") -> str:
        """O prompt numa linha só.

        As regras são escritas com recuo e quebra de linha, para o modelo
        lê-las como lista. Procurar uma frase inteira no texto cru falha por
        causa do `\n` do meio — e falharia reprovando garantia que existe.
        """
        import re
        return re.sub(r"\s+", " ", self._texto(estrategia)).lower()

    def test_continua_proibindo_declarar_dominio(self):
        self.assertIn("quem decide isso é o sistema", self._corrido())

    def test_continua_proibindo_prometer_aviso_a_alguem(self):
        self.assertIn("não prometa que alguém foi avisado", self._corrido())

    def test_continua_proibindo_jargao_do_sistema(self):
        baixo = self._texto().lower()
        for termo in ("banda", "evidência", "prontidão", "readiness"):
            with self.subTest(termo=termo):
                self.assertIn(termo, baixo)

    def test_continua_pedindo_resposta_curta(self):
        self.assertIn("2 a 5 frases", self._texto())

    def test_o_contexto_da_questao_entra(self):
        self.assertIn("Conteúdo: X", self._texto())

    def test_o_campo_da_resposta_nao_mudou(self):
        self.assertEqual("explicacao", ATUAL.CAMPO_DA_RESPOSTA)

    def test_o_formato_json_de_um_campo_continua(self):
        self.assertIn('{"explicacao":', self._texto())

    def test_cada_estrategia_tem_a_sua_instrucao(self):
        for e in ESTRATEGIAS:
            with self.subTest(estrategia=e):
                self.assertIn(f"A ABORDAGEM DESTA VEZ: {e}", self._texto(e))

    def test_o_prompt_nao_nomeia_fornecedor_nem_modelo(self):
        """O prompt e do sistema, nao do fornecedor."""
        baixo = self._texto().lower()
        for vendor in ("openai", "gpt", "claude", "anthropic", "gemini",
                       "llama", "temperature", "max_tokens"):
            with self.subTest(vendor=vendor):
                self.assertNotIn(vendor, baixo)


class OREGISTROGUARDAOPASSADO(unittest.TestCase):

    def test_a_versao_corrente_e_a_v2(self):
        self.assertEqual("assessor-explicacao-v2", VERSAO_ATUAL_DA_EXPLICACAO)

    def test_a_v1_continua_alcancavel(self):
        """Ela é o que explicou para quem leu aquelas telas. Apagá-la seria
        apagar o histórico de um texto que chegou a um aluno."""
        v1 = prompt_da_explicacao("assessor-explicacao-v1")
        self.assertEqual("assessor-explicacao-v1", v1.VERSION)

    def test_a_v1_nao_foi_editada(self):
        """A convenção do projeto: nunca se edita a redação de uma versão
        existente. Se esta asserção cair, alguém reescreveu a história."""
        v1 = prompt_da_explicacao("assessor-explicacao-v1")
        self.assertIn("Comece pelo que ele provavelmente fez",
                      v1.montar(contexto="x", estrategia="CONCEITO"))

    def test_versao_desconhecida_falha_em_vez_de_chutar(self):
        with self.assertRaises(KeyError):
            prompt_da_explicacao("assessor-explicacao-v99")

    def test_a_versao_viaja_na_resposta_do_servico(self):
        """Sem isso não se sabe qual prompt produziu qual texto."""
        import asyncio

        from agente_ia_edu.services.explicacao_do_erro import ExplicacaoDoErro

        async def run():
            # Sem provedor: cai no fallback, e a versao tem de vir junto.
            return await ExplicacaoDoErro(provider=None).explicar(
                resolucao_curada=None, contexto={"conteudo": "X"})

        r = asyncio.run(run())
        self.assertEqual(VERSAO_ATUAL_DA_EXPLICACAO, r["prompt_version"])


if __name__ == "__main__":
    unittest.main()
