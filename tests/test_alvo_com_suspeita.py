"""O ALVO QUANDO SÓ HÁ SUSPEITA — e a ordem entre medida e suspeita.

A decisão passa a ter duas fontes, e a ordem entre elas importa:

    1. LACUNA MEDIDA     a política concluiu, com amostra suficiente
    2. SUSPEITA          a sondagem viu um erro, sem amostra para concluir

Medida vence suspeita. Uma habilidade com três respostas e 0,2 de acerto é
informação melhor que um erro isolado em outra — e trocar a ordem faria o
sistema abandonar o que sabe para perseguir o que apenas desconfia.

O cenário do bloco (A–I) está coberto aqui e em `test_sinal_diagnostico.py`.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.assessor_pedagogico import habilidade_que_trava
from agente_ia_edu.services.grafo_estequiometria import (
    LEITURA_FORMULA,
    MASSA_MOLAR,
    PROPORCAO,
)
from agente_ia_edu.services.grafos_pedagogicos import grafo_de
from agente_ia_edu.services.grafo_estequiometria import CONTEUDO
from agente_ia_edu.services.pedagogical_analysis import (
    BAND_IMPROVEMENT,
    BAND_INSUFFICIENT,
)

GRAFO = grafo_de(CONTEUDO)


def _sondagem(**pares) -> dict:
    """Uma resposta por habilidade — amostra insuficiente, por construção."""
    return {"por_habilidade": {
        s: {"band": BAND_INSUFFICIENT, "answered": 1,
            "correct": 1 if certo else 0, "accuracy": 1.0 if certo else 0.0,
            "name": s}
        for s, certo in pares.items()}}


class CenarioC_FormulaCertaMassaMolarErrada(unittest.TestCase):
    """O cenário central do bloco."""

    def test_o_alvo_e_massa_molar(self):
        alvo = habilidade_que_trava(
            _sondagem(**{LEITURA_FORMULA: True, MASSA_MOLAR: False}),
            grafo=GRAFO)
        self.assertEqual(MASSA_MOLAR, alvo)

    def test_sem_grafo_a_sondagem_nao_produz_alvo(self):
        """A assimetria é do motor V2. Conteúdo legado não muda."""
        self.assertIsNone(habilidade_que_trava(
            _sondagem(**{LEITURA_FORMULA: True, MASSA_MOLAR: False})))


class CenarioD_AsDuasErradas(unittest.TestCase):

    def test_o_alvo_e_a_base_da_cadeia(self):
        alvo = habilidade_que_trava(
            _sondagem(**{LEITURA_FORMULA: False, MASSA_MOLAR: False}),
            grafo=GRAFO)
        self.assertEqual(LEITURA_FORMULA, alvo)

    def test_tres_erradas_tambem_comecam_na_base(self):
        alvo = habilidade_que_trava(
            _sondagem(**{LEITURA_FORMULA: False, MASSA_MOLAR: False,
                         PROPORCAO: False}),
            grafo=GRAFO)
        self.assertEqual(LEITURA_FORMULA, alvo)


class CenarioE_TudoCertoComUmaAmostra(unittest.TestCase):

    def test_nao_ha_alvo(self):
        self.assertIsNone(habilidade_que_trava(
            _sondagem(**{LEITURA_FORMULA: True, MASSA_MOLAR: True}),
            grafo=GRAFO))


class MedidaVenceSuspeita(unittest.TestCase):
    """Uma lacuna concluída vale mais que um erro isolado em outra."""

    def test_a_lacuna_medida_e_escolhida_mesmo_sendo_mais_alta_na_cadeia(self):
        dados = {"por_habilidade": {
            # medida, com amostra suficiente - e mais alta na cadeia
            MASSA_MOLAR: {"band": BAND_IMPROVEMENT, "answered": 5,
                          "correct": 1, "accuracy": 0.2, "name": MASSA_MOLAR},
            # so suspeita, e mais basica
            LEITURA_FORMULA: {"band": BAND_INSUFFICIENT, "answered": 1,
                              "correct": 0, "accuracy": 0.0,
                              "name": LEITURA_FORMULA},
        }}
        self.assertEqual(MASSA_MOLAR, habilidade_que_trava(dados, grafo=GRAFO))

    def test_sem_medida_nenhuma_a_suspeita_assume(self):
        self.assertEqual(
            MASSA_MOLAR,
            habilidade_que_trava(_sondagem(**{MASSA_MOLAR: False}), grafo=GRAFO))


class ASUSPEITANAOVIRADOMINIO(unittest.TestCase):
    """Escolher alvo não é concluir sobre o aluno."""

    def test_o_mapa_do_aluno_continua_dizendo_nao_medido(self):
        from agente_ia_edu.services.modelo_do_aluno import mapa_do_aluno
        from agente_ia_edu.services.sondagem import ESTADO_NAO_MEDIDO

        mapa = mapa_do_aluno(_sondagem(**{MASSA_MOLAR: False}))
        self.assertEqual(ESTADO_NAO_MEDIDO, mapa[MASSA_MOLAR])

    def test_a_suspeita_nao_entra_nas_fracas_do_mapa(self):
        """`fracas_do_mapa` alimenta o motor de domínio - e lá a suspeita
        não pode entrar, ou viraria evidência."""
        from agente_ia_edu.services.modelo_do_aluno import mapa_do_aluno
        from agente_ia_edu.services.sondagem import fracas_do_mapa

        mapa = mapa_do_aluno(_sondagem(**{MASSA_MOLAR: False}))
        self.assertEqual((), fracas_do_mapa(mapa))


class ATELAFALAPELOBACKEND(unittest.TestCase):
    """O passo de prática também fala pela voz do backend.

    Medido no navegador em 2026-10-06: o motor já tinha escolhido
    `MASSA_MOLAR` como alvo e a tela dizia "Vamos praticar Estequiometria e
    cálculos químicos um pouco" — o conteúdo inteiro, montado no JavaScript.
    VERIFY e ESCALATE já falavam pelo backend; PRACTICE não.

    Este teste é estrutural de propósito: não há DOM aqui, e o que ele guarda
    é a consulta que alguém poderia remover sem perceber.
    """

    import pathlib as _pathlib

    ALUNO_JS = (_pathlib.Path(__file__).resolve().parent.parent
                / "src/agente_ia_edu/web/aluno.js")

    def _bloco_da_pratica(self) -> str:
        fonte = self.ALUNO_JS.read_text(encoding="utf-8")
        inicio = fonte.index("if (passo.kind === 'PRACTICE') {")
        return fonte[inicio:inicio + 1200]

    def test_a_pratica_consulta_o_feedback_do_backend(self):
        self.assertIn("passo.feedback", self._bloco_da_pratica())

    def test_e_o_texto_local_continua_como_queda(self):
        """Removê-lo deixaria a tela muda se o campo faltasse."""
        self.assertIn("Vamos praticar", self._bloco_da_pratica())


if __name__ == "__main__":
    unittest.main()
