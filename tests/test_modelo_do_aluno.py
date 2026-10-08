"""O MODELO DO ALUNO POR MICRO-HABILIDADE — sobre o que já se mede.

A PONTE QUE ESTE MÓDULO É
==========================
O motor pedagógico pede um mapa `{habilidade: estado}`. O sistema já mede
`{habilidade: banda}` — `diagnostico_por_habilidade` agrega as respostas
corrigidas e a `PerformanceThresholdPolicy` diz em que faixa cada uma caiu.

Falta só a tradução. E ela tem de ser conservadora numa direção específica:
**amostra insuficiente não é lacuna**. Chamar de "precisa de apoio" uma
habilidade medida por uma resposta só mandaria o aluno estudar o que ele
talvez já saiba — e a chance de acertar no chute é 1 em 5.

NENHUM CORTE NOVO
==================
As faixas chegam prontas. Há teste de AST proibindo literal float aqui, pelo
mesmo motivo dos outros módulos da política: um número neste arquivo seria
uma segunda política, divergindo da primeira no primeiro ajuste.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from agente_ia_edu.services.modelo_do_aluno import (
    historico_por_habilidade,
    mapa_do_aluno,
)
from agente_ia_edu.services.pedagogical_analysis import (
    BAND_IMPROVEMENT,
    BAND_INSUFFICIENT,
    BAND_INTERMEDIATE,
    BAND_NO_DATA,
    BAND_STRONG,
)
from agente_ia_edu.services.sondagem import (
    ESTADO_CONFIRMADO,
    ESTADO_NAO_MEDIDO,
    ESTADO_PRECISA_APOIO,
)


def _medido(**bandas) -> dict:
    return {"por_habilidade": {s: {"band": b} for s, b in bandas.items()}}


class ATraducaoEConservadora(unittest.TestCase):

    def test_faixa_forte_vira_confirmado(self):
        self.assertEqual({"A": ESTADO_CONFIRMADO},
                         mapa_do_aluno(_medido(A=BAND_STRONG)))

    def test_faixa_de_melhoria_vira_precisa_apoio(self):
        self.assertEqual({"A": ESTADO_PRECISA_APOIO},
                         mapa_do_aluno(_medido(A=BAND_IMPROVEMENT)))

    def test_amostra_insuficiente_NAO_vira_lacuna(self):
        """Uma resposta só não distingue quem sabe de quem chutou."""
        self.assertEqual({"A": ESTADO_NAO_MEDIDO},
                         mapa_do_aluno(_medido(A=BAND_INSUFFICIENT)))

    def test_sem_dado_fica_nao_medido(self):
        self.assertEqual({"A": ESTADO_NAO_MEDIDO},
                         mapa_do_aluno(_medido(A=BAND_NO_DATA)))

    def test_faixa_intermediaria_tambem_nao_vira_lacuna(self):
        """A política já se recusa a chamar isso de lacuna; o modelo também.
        Dizer "precisa de apoio" a quem está no meio é intervir sem base."""
        self.assertEqual({"A": ESTADO_NAO_MEDIDO},
                         mapa_do_aluno(_medido(A=BAND_INTERMEDIATE)))

    def test_banda_desconhecida_nao_vira_lacuna(self):
        """Fail closed: uma faixa nova não pode virar intervenção por padrão."""
        self.assertEqual({"A": ESTADO_NAO_MEDIDO},
                         mapa_do_aluno(_medido(A="FAIXA_NOVA")))

    def test_sem_medida_nenhuma_o_mapa_e_vazio(self):
        self.assertEqual({}, mapa_do_aluno({}))
        self.assertEqual({}, mapa_do_aluno(None))

    def test_varias_habilidades_de_uma_vez(self):
        mapa = mapa_do_aluno(_medido(A=BAND_STRONG, B=BAND_IMPROVEMENT,
                                     C=BAND_INSUFFICIENT))
        self.assertEqual(ESTADO_CONFIRMADO, mapa["A"])
        self.assertEqual(ESTADO_PRECISA_APOIO, mapa["B"])
        self.assertEqual(ESTADO_NAO_MEDIDO, mapa["C"])


class OHistoricoVEMDOCICLO(unittest.TestCase):
    """As estratégias já oferecidas não são persistidas em lugar nenhum.

    Derivá-las do ciclo é a escolha desta V1: a estratégia do ciclo N é a
    N-ésima da escada, então ela nunca repete — que é a propriedade que
    importa. A limitação está documentada.
    """

    def test_ciclo_zero_nao_usou_estrategia_nenhuma(self):
        h = historico_por_habilidade("A", ciclo=0)
        self.assertEqual((), h.estrategias_usadas)

    def test_cada_ciclo_consome_mais_uma_estrategia(self):
        self.assertEqual(1, len(historico_por_habilidade("A", ciclo=1)
                                .estrategias_usadas))
        self.assertEqual(3, len(historico_por_habilidade("A", ciclo=3)
                                .estrategias_usadas))

    def test_as_estrategias_derivadas_nunca_repetem(self):
        usadas = historico_por_habilidade("A", ciclo=4).estrategias_usadas
        self.assertEqual(len(usadas), len(set(usadas)))

    def test_ciclo_alem_da_escada_nao_estoura(self):
        from agente_ia_edu.services.estrategia_de_ensino import ESTRATEGIAS

        usadas = historico_por_habilidade("A", ciclo=99).estrategias_usadas
        self.assertEqual(len(ESTRATEGIAS), len(usadas))

    def test_o_nivel_e_o_acerto_passam_adiante(self):
        h = historico_por_habilidade("A", ciclo=1, nivel="L1",
                                     ultimo_acerto=True)
        self.assertEqual("L1", h.nivel_de_apoio)
        self.assertTrue(h.ultimo_acerto)


class NENHUMCORTENOVOAQUI(unittest.TestCase):

    def test_o_modulo_nao_escreve_numero_de_corte(self):
        fonte = (pathlib.Path(__file__).resolve().parent.parent
                 / "src/agente_ia_edu/services/modelo_do_aluno.py"
                 ).read_text(encoding="utf-8")
        for no in ast.walk(ast.parse(fonte)):
            if isinstance(no, ast.Constant) and isinstance(no.value, float):
                self.fail(f"literal float no modelo do aluno: {no.value} - "
                          f"um corte aqui seria uma segunda politica")

    def test_o_modulo_nao_toca_banco_nem_provedor(self):
        fonte = (pathlib.Path(__file__).resolve().parent.parent
                 / "src/agente_ia_edu/services/modelo_do_aluno.py"
                 ).read_text(encoding="utf-8").lower()
        for proibido in ("asyncsession", "sqlalchemy", "provider", "openai"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, fonte)


if __name__ == "__main__":
    unittest.main()
