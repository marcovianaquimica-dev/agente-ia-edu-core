"""INVESTIGAR ENTRA NA DECISÃO — e entra ANTES de ensinar.

O QUE ESTE ARQUIVO MEDE
========================
`decidir_intervencao` passou a ter seis saídas em vez de cinco. A nova, a
investigação, não foi acrescentada no fim da lista: ela entra ANTES do
ensino, e essa posição é a tese do bloco.

Por quê. Despejar a resolução completa em quem errou só a última etapa é
repetir o que ele já sabia; em quem errou a primeira, é construir três
etapas sobre a que falhou. Nos dois casos o sistema termina sem saber nada
de novo sobre o aluno — ele falou, não perguntou. A micropergunta descobre
qual dos dois casos é antes de gastar a explicação.

O QUE NÃO PODE MUDAR
=====================
VERIFICAR continua vencendo tudo, e ESCALAR continua vindo antes de qualquer
nova tentativa. Quem está se recuperando não é interrompido para investigar,
e quem já esgotou os ciclos não recomeça por baixo da escada.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.assessor_pedagogico import (
    ACAO_ENSINAR,
    ACAO_ESCALAR,
    ACAO_GUIADA,
    ACAO_INVESTIGAR,
    ACAO_PRATICAR,
    ACAO_VERIFICAR,
    LIMITE_DE_CICLOS,
    decidir_intervencao,
)
from agente_ia_edu.services.pedagogical_analysis import BAND_IMPROVEMENT

FRACO = {"answered": 5, "correct": 1}
FORTE = {"answered": 5, "correct": 5}


def _decidir(**kw):
    base = dict(
        habilidades={"por_habilidade": {
            "MASSA_MOLAR": {"band": BAND_IMPROVEMENT, "answered": 5,
                            "correct": 1, "accuracy": 0.2,
                            "name": "Massa molar"}}},
        banda_do_conteudo=BAND_IMPROVEMENT,
        ja_ensinado=False,
        praticas_concluidas=0,
        ha_material=True,
        ha_guiada_pendente=True,
        ha_investigacao_pendente=True,
        tentativas=[FRACO],
    )
    base.update(kw)
    return decidir_intervencao(**base)


class AINVESTIGACAOVEMANTESDOENSINO(unittest.TestCase):
    """Teste A do bloco: erro em massa molar → microintervenção adequada."""

    def test_com_tudo_disponivel_a_primeira_acao_e_investigar(self):
        self.assertEqual(ACAO_INVESTIGAR, _decidir()["action"])

    def test_o_alvo_continua_sendo_a_habilidade_que_trava(self):
        self.assertEqual("MASSA_MOLAR", _decidir()["skill"])

    def test_sem_investigacao_escrita_cai_no_ensino(self):
        """Conteúdo sem cadeia curada não inventa uma."""
        self.assertEqual(
            ACAO_ENSINAR, _decidir(ha_investigacao_pendente=False)["action"])

    def test_investigacao_concluida_libera_o_ensino(self):
        self.assertEqual(
            ACAO_ENSINAR, _decidir(ha_investigacao_pendente=False)["action"])

    def test_sem_investigacao_e_sem_material_cai_na_guiada(self):
        self.assertEqual(
            ACAO_GUIADA,
            _decidir(ha_investigacao_pendente=False, ha_material=False)["action"])

    def test_sem_nenhum_apoio_disponivel_sobra_a_pratica(self):
        self.assertEqual(
            ACAO_PRATICAR,
            _decidir(ha_investigacao_pendente=False, ha_material=False,
                     ha_guiada_pendente=False)["action"])


class AESCADADESCEUMDEGRAUPORVEZ(unittest.TestCase):
    """§12 — o fading percorrido pela própria decisão."""

    def test_o_percurso_inteiro(self):
        esperado = [
            (dict(), ACAO_INVESTIGAR),
            (dict(ha_investigacao_pendente=False), ACAO_ENSINAR),
            (dict(ha_investigacao_pendente=False, ja_ensinado=True),
             ACAO_GUIADA),
            (dict(ha_investigacao_pendente=False, ja_ensinado=True,
                  ha_guiada_pendente=False), ACAO_PRATICAR),
        ]
        for kw, acao in esperado:
            with self.subTest(kw=kw):
                self.assertEqual(acao, _decidir(**kw)["action"])


class OQUEAINVESTIGACAONAOPODEATROPELAR(unittest.TestCase):

    def test_verificar_continua_vencendo_tudo(self):
        """Quem está se recuperando não é interrompido para investigar."""
        self.assertEqual(
            ACAO_VERIFICAR,
            _decidir(tentativas=[FRACO, FORTE])["action"])

    def test_escalar_continua_vindo_antes_de_qualquer_nova_tentativa(self):
        self.assertEqual(
            ACAO_ESCALAR,
            _decidir(praticas_concluidas=LIMITE_DE_CICLOS)["action"])

    def test_sem_lacuna_medida_nao_ha_investigacao(self):
        """Investigar quem nunca foi medido é inventar sobre a pessoa."""
        from agente_ia_edu.services.pedagogical_analysis import BAND_NO_DATA
        d = _decidir(banda_do_conteudo=BAND_NO_DATA, habilidades={})
        self.assertIsNone(d["action"])

    def test_a_investigacao_nao_tem_abordagem_de_material(self):
        """`approach` só faz sentido quando a ação é ENSINAR."""
        self.assertIsNone(_decidir()["approach"])


class AINVESTIGACAONAOALTERADOMINIO(unittest.TestCase):
    """Teste D/E/F do bloco, no nível da decisão."""

    def test_a_decisao_nao_devolve_nada_parecido_com_nota(self):
        d = _decidir()
        for proibido in ("mastery", "score", "domain", "evidence"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, {k.lower() for k in d})

    def test_investigar_e_um_degrau_assistido_e_nao_produz_evidencia(self):
        from agente_ia_edu.services.escada_de_apoio import (
            NIVEL_INVESTIGACAO, produz_evidencia,
        )
        self.assertFalse(produz_evidencia(NIVEL_INVESTIGACAO))


if __name__ == "__main__":
    unittest.main()
