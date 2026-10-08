"""A ESCOLHA DO ALVO: menor acerto, ou primeiro gargalo?

O QUE MUDA NA DECISÃO REAL
===========================
`habilidade_que_trava` escolhia a micro-habilidade de MENOR ACERTO. Para um
aluno que vai mal em leitura de fórmula (0/3) e pior ainda no problema
completo (0/5), ela aponta o problema completo — e o sistema ensina a cadeia
inteira a quem não lê o índice do NH₃.

Com grafo, a pergunta deixa de ser "qual está pior" e passa a ser "em qual
delas ele está PRONTO para aprender agora".

A REGRA DE CONVIVÊNCIA
=======================
Conteúdo sem grafo mantém exatamente o comportamento de antes. Essa metade é
tão importante quanto a outra: 36 dos 37 conteúdos do catálogo não têm
contrato V2, e nenhum deles pode mudar de comportamento hoje.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.assessor_pedagogico import habilidade_que_trava
from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    INTEGRADO,
    LEITURA_FORMULA,
    MASSA_MOLAR,
)
from agente_ia_edu.services.grafos_pedagogicos import (
    conteudos_com_grafo,
    grafo_de,
    tem_contrato_v2,
)
from agente_ia_edu.services.pedagogical_analysis import (
    BAND_IMPROVEMENT,
    BAND_STRONG,
)


def _habilidades(**bandas):
    return {"por_habilidade": {
        s: {"band": b, "accuracy": a, "name": s}
        for s, (b, a) in bandas.items()}}


class ORegistroResponde(unittest.TestCase):

    def test_estequiometria_tem_contrato_v2(self):
        self.assertTrue(tem_contrato_v2(CONTEUDO))
        self.assertIsNotNone(grafo_de(CONTEUDO))

    def test_um_conteudo_qualquer_nao_tem(self):
        self.assertFalse(tem_contrato_v2("BIOLOGY-ECOLOGY-POPULATIONS-CONSERVATION"))
        self.assertIsNone(grafo_de("BIOLOGY-ECOLOGY-POPULATIONS-CONSERVATION"))

    def test_sem_conteudo_nao_ha_grafo(self):
        self.assertIsNone(grafo_de(None))
        self.assertIsNone(grafo_de(""))

    def test_o_piloto_e_um_conteudo_so(self):
        """Se isto crescer sem querer, a convivência deixou de ser gradual."""
        self.assertEqual((CONTEUDO,), conteudos_com_grafo())


class OALVOPASSAASEROGARGALO(unittest.TestCase):

    # O aluno vai mal nas duas, e PIOR no problema completo.
    FRACO_NAS_DUAS = _habilidades(
        **{LEITURA_FORMULA: (BAND_IMPROVEMENT, 0.33),
           INTEGRADO: (BAND_IMPROVEMENT, 0.0)})

    def test_sem_grafo_a_escolha_e_pelo_menor_acerto(self):
        """O comportamento de antes, preservado — e é o que o legado usa."""
        self.assertEqual(INTEGRADO, habilidade_que_trava(self.FRACO_NAS_DUAS))

    def test_com_grafo_a_escolha_e_pela_base_da_cadeia(self):
        self.assertEqual(
            LEITURA_FORMULA,
            habilidade_que_trava(self.FRACO_NAS_DUAS, grafo=grafo_de(CONTEUDO)))

    def test_as_duas_escolhas_sao_mesmo_diferentes(self):
        """Se fossem iguais, este teste não provaria nada."""
        self.assertNotEqual(
            habilidade_que_trava(self.FRACO_NAS_DUAS),
            habilidade_que_trava(self.FRACO_NAS_DUAS, grafo=grafo_de(CONTEUDO)))

    def test_uma_fraca_so_da_na_mesma_com_ou_sem_grafo(self):
        uma = _habilidades(**{MASSA_MOLAR: (BAND_IMPROVEMENT, 0.2)})
        self.assertEqual(habilidade_que_trava(uma),
                         habilidade_que_trava(uma, grafo=grafo_de(CONTEUDO)))

    def test_sem_lacuna_nao_ha_alvo_dos_dois_jeitos(self):
        forte = _habilidades(**{MASSA_MOLAR: (BAND_STRONG, 1.0)})
        self.assertIsNone(habilidade_que_trava(forte))
        self.assertIsNone(habilidade_que_trava(forte, grafo=grafo_de(CONTEUDO)))

    def test_fraca_que_o_grafo_nao_conhece_nao_derruba_a_decisao(self):
        """Um subconteúdo antigo, sem nó, não pode apagar o alvo."""
        misto = _habilidades(
            **{LEITURA_FORMULA: (BAND_IMPROVEMENT, 0.3),
               "SUBCONTEUDO_ANTIGO": (BAND_IMPROVEMENT, 0.1)})
        self.assertEqual(
            LEITURA_FORMULA,
            habilidade_que_trava(misto, grafo=grafo_de(CONTEUDO)))

    def test_so_fracas_desconhecidas_caem_no_comportamento_de_antes(self):
        """Sem nada que o grafo saiba ler, o legado decide — não o silêncio."""
        so_antigas = _habilidades(**{"SUBCONTEUDO_ANTIGO": (BAND_IMPROVEMENT, 0.1)})
        self.assertEqual(
            "SUBCONTEUDO_ANTIGO",
            habilidade_que_trava(so_antigas, grafo=grafo_de(CONTEUDO)))


if __name__ == "__main__":
    unittest.main()
