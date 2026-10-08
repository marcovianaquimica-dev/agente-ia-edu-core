"""MEU PROGRESSO - o que o aluno ve sobre o proprio aprendizado.

O motor sabe accuracy, tamanho de amostra, evidencia definitiva x provisoria,
fechamento forcado, dependencia visual, origem de cada resposta. O aluno ve
tres palavras.

A REGRA PEDAGOGICA QUE ESTE ARQUIVO PROTEGE
============================================
FALTA DE EVIDENCIA NAO E EVIDENCIA DE DIFICULDADE.

Um aluno que nunca respondeu nada sobre Soluções nao esta com dificuldade em
Soluções - nos e que nao sabemos. Mostrar "Precisa de atenção" nesse caso e
mentir para ele sobre si mesmo, e e o tipo de mentira que faz um adolescente
achar que e ruim numa materia que ele nunca tentou.

Por isso INSUFFICIENT_SAMPLE tem texto proprio, e ha teste abaixo que falha
se alguem o colapsar em "Precisa de atenção".

OS CORTES NAO MORAM AQUI
=========================
0.60, 0.80 e min_sample_size sao de PerformanceThresholdPolicy, que se declara
"single source of truth for the strong/improvement bands". Esta camada apenas
TRADUZ o que ela decide. Um segundo lugar com esses numeros seria uma segunda
definicao do que significa saber alguma coisa - e as duas divergiriam no dia
em que alguem ajustasse uma delas.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.pedagogical_analysis import PerformanceThresholdPolicy

# "Indo bem", e nao "Consolidado".
#
# A faixa traduz BAND_STRONG - acerto forte numa UNICA ocasiao -, e chama-la
# de consolidacao era a confusao que o §12 pede para separar. A palavra
# passou a ser de `services/consolidacao`, que mede repeticao em ocasioes
# diferentes. Decisao do dono em 2026-10-08.
INDO_BEM = "Indo bem"
EM_DESENVOLVIMENTO = "Em desenvolvimento"
PRECISA_ATENCAO = "Precisa de atenção"
CONHECENDO = "Ainda estamos conhecendo seu aprendizado"

# Nada disto pode vazar para o aluno.
VOCABULARIO_INTERNO = (
    "accuracy", "score", "probability", "confidence", "mastery",
    "PONTO_FORTE", "PONTO_MELHORIA", "DESEMPENHO_INTERMEDIARIO",
    "INSUFFICIENT_SAMPLE", "SEM_DADOS", "evidence_state", "provisional",
    "forced_closure", "origin_breakdown", "taxonomy",
)


def _traduzir(**kw):
    from agente_ia_edu.services.student_progress import faixa_do_aluno

    return faixa_do_aluno(**kw)


class FaixasDoAlunoTests(unittest.TestCase):

    # -- CASO G: falta de evidencia nao vira dificuldade --------------------

    def test_sem_evidencia_suficiente_nao_vira_precisa_de_atencao(self):
        f = _traduzir(answered=0, accuracy=None)
        self.assertEqual(f["faixa"], CONHECENDO)
        self.assertNotEqual(f["faixa"], PRECISA_ATENCAO)

    def test_uma_unica_resposta_errada_nao_condena_o_aluno(self):
        """1 resposta esta abaixo de min_sample_size: ainda nao sabemos nada."""
        f = _traduzir(answered=1, accuracy=0.0)
        self.assertEqual(f["faixa"], CONHECENDO,
                         "uma resposta errada bastou para o sistema dizer que o "
                         "aluno tem dificuldade")

    def test_duas_respostas_ainda_sao_poucas(self):
        politica = PerformanceThresholdPolicy.default()
        self.assertEqual(politica.min_sample_size, 3)   # premissa deste caso
        f = _traduzir(answered=2, accuracy=0.0)
        self.assertEqual(f["faixa"], CONHECENDO)

    def test_amostra_suficiente_e_desempenho_fraco_ai_sim_precisa_de_atencao(self):
        f = _traduzir(answered=3, accuracy=0.0)
        self.assertEqual(f["faixa"], PRECISA_ATENCAO)

    # -- as tres faixas -----------------------------------------------------

    def test_desempenho_forte_vira_indo_bem(self):
        self.assertEqual(_traduzir(answered=5, accuracy=0.90)["faixa"], INDO_BEM)

    def test_desempenho_intermediario_vira_em_desenvolvimento(self):
        self.assertEqual(_traduzir(answered=5, accuracy=0.70)["faixa"],
                         EM_DESENVOLVIMENTO)

    def test_desempenho_fraco_vira_precisa_de_atencao(self):
        self.assertEqual(_traduzir(answered=5, accuracy=0.40)["faixa"],
                         PRECISA_ATENCAO)

    def test_as_fronteiras_sao_as_da_politica_e_nao_outras(self):
        politica = PerformanceThresholdPolicy.default()
        # exatamente no corte forte -> indo bem
        self.assertEqual(_traduzir(answered=5, accuracy=politica.strong_accuracy)["faixa"],
                         INDO_BEM)
        # um fio abaixo -> em desenvolvimento
        self.assertEqual(_traduzir(answered=5, accuracy=politica.strong_accuracy - 0.001)["faixa"],
                         EM_DESENVOLVIMENTO)
        # exatamente no corte de melhoria -> em desenvolvimento (o corte e <)
        self.assertEqual(_traduzir(answered=5, accuracy=politica.improvement_accuracy)["faixa"],
                         EM_DESENVOLVIMENTO)
        # um fio abaixo -> precisa de atencao
        self.assertEqual(
            _traduzir(answered=5, accuracy=politica.improvement_accuracy - 0.001)["faixa"],
            PRECISA_ATENCAO)

    def test_toda_banda_da_politica_tem_traducao(self):
        """Se alguem acrescentar uma banda, o aluno nao pode ver o nome cru."""
        from agente_ia_edu.services.pedagogical_analysis import (
            BAND_IMPROVEMENT, BAND_INSUFFICIENT, BAND_INTERMEDIATE,
            BAND_NO_DATA, BAND_STRONG,
        )
        from agente_ia_edu.services.student_progress import FAIXA_POR_BANDA

        for banda in (BAND_STRONG, BAND_INTERMEDIATE, BAND_IMPROVEMENT,
                      BAND_INSUFFICIENT, BAND_NO_DATA):
            self.assertIn(banda, FAIXA_POR_BANDA, f"banda {banda} sem traducao")

    # -- a politica continua sendo a unica fonte ----------------------------

    def test_a_traducao_nao_reimplementa_os_cortes(self):
        """Procura os numeros no CODIGO, nao no texto.

        A primeira versao deste teste varria o fonte como string e falhava na
        propria docstring do modulo, que cita 0.60 e 0.80 para explicar que
        eles NAO moram la. Procurar texto encontra a explicacao junto com o
        defeito. Entao aqui a busca e na arvore sintatica: um literal
        numerico avaliado em tempo de execucao e um corte duplicado; a mesma
        sequencia dentro de uma docstring e documentacao.
        """
        import ast
        import inspect

        from agente_ia_edu.services import student_progress

        arvore = ast.parse(inspect.getsource(student_progress))
        literais = [n.value for n in ast.walk(arvore)
                    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
                    and not isinstance(n.value, bool)]
        for proibido in (0.6, 0.8, 0.60, 0.80):
            self.assertNotIn(proibido, literais,
                             f"o corte {proibido} foi copiado para a camada de "
                             "apresentacao - agora ha duas definicoes do que "
                             "significa saber alguma coisa")
        # e o teste tem de ver o defeito quando ele existe
        self.assertIn(1, literais, "o extrator de literais nao esta funcionando")

        fonte = inspect.getsource(student_progress)
        self.assertNotIn("min_sample_size =", fonte)
        self.assertIn("band(", fonte, "a traducao precisa CHAMAR a politica")

    def test_uma_politica_diferente_muda_a_faixa_sem_editar_a_traducao(self):
        from agente_ia_edu.services.student_progress import faixa_do_aluno

        frouxa = PerformanceThresholdPolicy(strong_accuracy=0.50,
                                            improvement_accuracy=0.30)
        f = faixa_do_aluno(answered=5, accuracy=0.60, thresholds=frouxa)
        self.assertEqual(f["faixa"], INDO_BEM,
                         "a traducao ignorou a politica que recebeu")

    # -- o que o aluno NAO pode ver -----------------------------------------

    def test_a_faixa_entregue_ao_aluno_nao_carrega_numero_nem_jargao(self):
        for answered, accuracy in ((0, None), (1, 0.0), (5, 0.9), (5, 0.4), (5, 0.7)):
            f = _traduzir(answered=answered, accuracy=accuracy)
            texto = repr(f)
            for proibido in VOCABULARIO_INTERNO:
                self.assertNotIn(proibido, texto,
                                 f"vazou {proibido!r} para o aluno em {f}")
            self.assertEqual(set(f.keys()), {"faixa", "ordem"},
                             f"a faixa carrega campos alem do necessario: {set(f)}")

    def test_as_faixas_tem_ordem_estavel_para_agrupar_na_tela(self):
        ordens = {
            _traduzir(answered=5, accuracy=0.40)["faixa"]: _traduzir(answered=5, accuracy=0.40)["ordem"],
            _traduzir(answered=5, accuracy=0.70)["faixa"]: _traduzir(answered=5, accuracy=0.70)["ordem"],
            _traduzir(answered=5, accuracy=0.95)["faixa"]: _traduzir(answered=5, accuracy=0.95)["ordem"],
            _traduzir(answered=0, accuracy=None)["faixa"]: _traduzir(answered=0, accuracy=None)["ordem"],
        }
        self.assertEqual(ordens[PRECISA_ATENCAO], 1)
        self.assertEqual(ordens[EM_DESENVOLVIMENTO], 2)
        self.assertEqual(ordens[INDO_BEM], 3)
        self.assertEqual(ordens[CONHECENDO], 4,
                         "o que ainda nao sabemos vem por ultimo, nao no meio "
                         "das faixas de desempenho")


class PanoramaDoAlunoTests(unittest.TestCase):
    """A traducao de um mapa de dominio inteiro, sem tocar no banco."""

    def _panorama(self, conteudos):
        from agente_ia_edu.services.student_progress import panorama_do_aluno

        return panorama_do_aluno({"disciplines": [{"contents": conteudos}]})

    def test_agrupa_os_conteudos_nas_faixas(self):
        p = self._panorama([
            {"content_code": "A", "content_name": "Estequiometria",
             "questions_answered": 5, "accuracy": 0.2},
            {"content_code": "B", "content_name": "Soluções",
             "questions_answered": 5, "accuracy": 0.7},
            {"content_code": "C", "content_name": "Ligações",
             "questions_answered": 5, "accuracy": 0.95},
            {"content_code": "D", "content_name": "Cinética",
             "questions_answered": 0, "accuracy": None},
        ])
        por_faixa = {f["faixa"]: f["itens"] for f in p["faixas"]}
        self.assertEqual(por_faixa[PRECISA_ATENCAO], ["Estequiometria"])
        self.assertEqual(por_faixa[EM_DESENVOLVIMENTO], ["Soluções"])
        self.assertEqual(por_faixa[INDO_BEM], ["Ligações"])
        self.assertEqual(por_faixa[CONHECENDO], ["Cinética"])

    def test_faixa_vazia_nao_aparece_na_tela(self):
        p = self._panorama([
            {"content_code": "C", "content_name": "Ligações",
             "questions_answered": 5, "accuracy": 0.95},
        ])
        self.assertEqual([f["faixa"] for f in p["faixas"]], [INDO_BEM])

    def test_o_panorama_nao_vaza_codigo_interno_nem_numero(self):
        p = self._panorama([
            {"content_code": "CHEMISTRY-PHYSICAL-STOICHIOMETRY",
             "content_name": "Estequiometria",
             "questions_answered": 5, "accuracy": 0.2,
             "content_state": "RECOMMENDED", "origin_breakdown": {"PRACTICE": 5}},
        ])
        texto = repr(p)
        self.assertNotIn("CHEMISTRY-PHYSICAL-STOICHIOMETRY", texto,
                         "o codigo curricular interno chegou ao aluno")
        for proibido in VOCABULARIO_INTERNO:
            self.assertNotIn(proibido, texto, f"vazou {proibido!r}")

    def test_sem_nenhum_conteudo_o_panorama_nao_quebra(self):
        p = self._panorama([])
        self.assertEqual(p["faixas"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
