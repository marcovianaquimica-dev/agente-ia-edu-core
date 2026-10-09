"""PRÉ-REQUISITO INFORMA; ELE NÃO TRANCA A PORTA ANTES DE SONDAR.

O QUE FOI MEDIDO, EM 2026-10-06
================================
Um aluno de QA entrou numa atividade de Estequiometria. A rota o mandou para
Balanceamento — o pré-requisito — e lá ficou. Depois de 29 tentativas
roteirizadas ele bateu no teto de ciclos e caiu em ESCALATE **na base**, sem
nunca ter sido perguntado sobre Estequiometria.

A CAUSA, LOCALIZADA
====================
`passo_para` percorre os pré-requisitos do conteúdo-alvo ANTES de olhar para
o alvo. O comentário explica o porquê, e ele era razoável no nível de
conteúdo: "perguntar Estequiometria a quem não sabe balancear uma equação é
perguntar a coisa errada".

O que mudou é que agora existe um grafo. As habilidades básicas de
Estequiometria — ler a fórmula, saber o que é um mol, ler o coeficiente —
estão DENTRO dele. Sondar o alvo já sonda as fundações; subir para o conteúdo
ancestral passou a ser uma segunda volta na mesma pergunta.

A DISTINÇÃO QUE ESTE ARQUIVO TRAVA
===================================
    pré-requisito que INFORMA     o grafo o carrega; a sondagem o mede;
                                  a intervenção pode descer até ele
    pré-requisito que BLOQUEIA    impede entrar antes de medir qualquer coisa

O primeiro fica. O segundo sai — **e só onde há grafo**. Conteúdo sem
contrato V2 mantém exatamente o comportamento de antes, e isso é metade do
teste: `catalog_node_prerequisites` é lido por quatro serviços, e a semântica
global dele não muda.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.proximo_passo import (
    PASSO_DIAGNOSTICO,
    PASSO_PRATICA,
    passo_para,
)

ALVO = "CHEMISTRY-PHYSICAL-STOICHIOMETRY"
BASE = "CHEMISTRY-GENERAL-BALANCING"


def _conteudo(*, estado="INSUFFICIENT_EVIDENCE", respondidas=0, acerto=None,
              prereq_dominado=False, prereq_respondidas=0, prereq_acerto=None):
    return [{
        "content_code": ALVO,
        "content_name": "Estequiometria e cálculos químicos",
        "content_state": estado,
        "answered": respondidas,
        "accuracy": acerto,
        "prerequisites": [{
            "code": BASE,
            "name": "Reações químicas e balanceamento",
            "mastered": prereq_dominado,
            "answered": prereq_respondidas,
            "accuracy": prereq_acerto,
            "content_state": "MASTERED" if prereq_dominado
                             else "INSUFFICIENT_EVIDENCE",
        }],
    }]


class SemGrafoNadaMuda(unittest.TestCase):
    """36 dos 37 conteúdos do catálogo não têm contrato V2."""

    def test_o_passo_continua_sendo_sobre_a_base(self):
        passo = passo_para(_conteudo())
        self.assertEqual(BASE, passo["content_code"])

    def test_e_ele_diz_para_que_conteudo_a_base_serve(self):
        passo = passo_para(_conteudo())
        self.assertEqual(ALVO, passo["for_content_code"])

    def test_com_a_base_dominada_o_passo_ja_era_sobre_o_alvo(self):
        passo = passo_para(_conteudo(prereq_dominado=True))
        self.assertEqual(ALVO, passo["content_code"])


class ComGrafoASondagemCOMECANOALVO(unittest.TestCase):
    """O grafo do alvo já contém as habilidades básicas."""

    def _passo(self, **kw):
        return passo_para(_conteudo(**kw), tem_grafo=lambda c: c == ALVO)

    def test_o_passo_e_sobre_o_proprio_alvo(self):
        self.assertEqual(ALVO, self._passo()["content_code"])

    def test_mesmo_com_a_base_nunca_medida(self):
        """Era exatamente o caso do aluno que ficou preso."""
        passo = self._passo(prereq_dominado=False, prereq_respondidas=0)
        self.assertEqual(ALVO, passo["content_code"])
        self.assertEqual(PASSO_DIAGNOSTICO, passo["kind"])

    def test_e_mesmo_com_a_base_medida_e_fraca(self):
        """A lacuna na base é informação para a intervenção, não uma tranca
        na entrada: o grafo tem como descer até ela depois de medir."""
        passo = self._passo(prereq_respondidas=5, prereq_acerto=0.2)
        self.assertEqual(ALVO, passo["content_code"])

    def test_o_grafo_de_OUTRO_conteudo_nao_muda_este(self):
        passo = passo_para(_conteudo(), tem_grafo=lambda c: c == "OUTRA-COISA")
        self.assertEqual(BASE, passo["content_code"])

    def test_sem_a_funcao_o_comportamento_e_o_legado(self):
        """O parâmetro é opcional: quem não informa não muda de comportamento."""
        self.assertEqual(BASE, passo_para(_conteudo())["content_code"])

    def test_tem_grafo_que_explode_nao_derruba_a_decisao(self):
        """Fail-safe: um registro quebrado não pode deixar o aluno sem passo."""
        def explode(_):
            raise RuntimeError("registro indisponivel")

        passo = passo_para(_conteudo(), tem_grafo=explode)
        self.assertIn(passo["content_code"], (ALVO, BASE))
        self.assertTrue(passo["kind"])


class OALVOCOMEVIDENCIAFRACACONTINUAINDOPARAPRATICA(unittest.TestCase):
    """Com grafo, medir o alvo não significa pular a intervenção nele."""

    def test_alvo_medido_e_fraco_vira_pratica_e_nao_diagnostico(self):
        passo = passo_para(
            _conteudo(estado="RECOMMENDED", respondidas=5, acerto=0.2),
            tem_grafo=lambda c: c == ALVO)
        self.assertEqual(ALVO, passo["content_code"])
        self.assertEqual(PASSO_PRATICA, passo["kind"])


if __name__ == "__main__":
    unittest.main()
