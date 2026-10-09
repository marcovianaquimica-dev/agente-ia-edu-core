"""O que o aluno LÊ depois do diagnóstico.

O teste humano de 2026-10-04 recebeu, tendo acertado tudo:

    "Essa parte você sabe. Agora falta Reações químicas e balanceamento."

Duas coisas erradas numa frase só. A primeira é que o texto estava montado no
JavaScript, a partir de um `switch` sobre a decisão — regra pedagógica onde o
console do navegador alcança. A segunda é que ele nomeava como "o que falta"
justamente o conteúdo que o aluno acabara de demonstrar.

Aqui o texto é do backend, e depende do resultado REAL. O frontend traduz;
não decide.

A REGRA QUE ESTES TESTES PROTEGEM
==================================
Nunca afirmar domínio que a política não sustenta. Dizer "você domina" a quem
tirou 2 de 3 é pior que não dizer nada: o aluno acredita, e a escola descobre
na prova.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.feedback_pedagogico import feedback_do_diagnostico
from agente_ia_edu.services.micro_diagnostic import (
    DECISION_INSUFFICIENT,
    DECISION_PREPARE,
    DECISION_PROCEED,
)


class ComDominioTests(unittest.TestCase):

    def setUp(self):
        self.f = feedback_do_diagnostico(
            decision=DECISION_PROCEED, band="PONTO_FORTE",
            content_name="Reações químicas e balanceamento",
            objective_name="Estequiometria")

    def test_reconhece_o_que_o_aluno_demonstrou(self):
        self.assertIn("Reações químicas e balanceamento", self.f["titulo"]
                      + " " + self.f["detalhe"])

    def test_diz_para_que_aquilo_serve(self):
        """O aluno precisa saber por que respondeu aquilo."""
        self.assertIn("Estequiometria", self.f["detalhe"])

    def test_tom_de_reconhecimento(self):
        self.assertEqual(self.f["tom"], "BOM")

    def test_NAO_chama_de_falta_o_que_ele_acabou_de_demonstrar(self):
        """O defeito literal observado no teste humano."""
        texto = (self.f["titulo"] + " " + self.f["detalhe"]).lower()
        self.assertNotIn("falta reações", texto)
        self.assertNotIn("falta balanceamento", texto)


class ComLacunaTests(unittest.TestCase):

    def setUp(self):
        self.f = feedback_do_diagnostico(
            decision=DECISION_PREPARE, band="PONTO_MELHORIA",
            content_name="Reações químicas e balanceamento",
            objective_name="Estequiometria")

    def test_convida_a_revisar(self):
        self.assertIn("revisar", self.f["titulo"].lower())

    def test_explica_que_e_base_para_o_objetivo(self):
        self.assertIn("Estequiometria", self.f["detalhe"])

    def test_NAO_afirma_dominio(self):
        """A asserção central deste arquivo."""
        texto = (self.f["titulo"] + " " + self.f["detalhe"]).lower()
        for palavra in ("você domina", "bom domínio", "muito bem", "parabéns"):
            self.assertNotIn(palavra, texto,
                             f"afirmou domínio a quem tem lacuna: {palavra!r}")

    def test_tom_nao_e_de_culpa(self):
        """Preparação é ajuda, nunca falta do aluno."""
        texto = (self.f["titulo"] + " " + self.f["detalhe"]).lower()
        for palavra in ("você não sabe", "errou", "fraco", "deficiência",
                        "ruim", "insuficiente"):
            self.assertNotIn(palavra, texto, f"tom de culpa: {palavra!r}")
        self.assertEqual(self.f["tom"], "REVISAR")


class SemConclusaoTests(unittest.TestCase):

    def setUp(self):
        self.f = feedback_do_diagnostico(
            decision=DECISION_INSUFFICIENT, band="EVIDENCIA_INSUFICIENTE",
            content_name="Reações químicas e balanceamento",
            objective_name="Estequiometria")

    def test_nao_finge_ter_concluido(self):
        texto = (self.f["titulo"] + " " + self.f["detalhe"]).lower()
        self.assertNotIn("você domina", texto)
        self.assertNotIn("pronto para", texto)

    def test_tom_neutro(self):
        self.assertEqual(self.f["tom"], "NEUTRO")


class SemObjetivoTests(unittest.TestCase):
    """Um diagnóstico pode acontecer sem tarefa da escola por trás."""

    def test_nao_inventa_objetivo(self):
        f = feedback_do_diagnostico(
            decision=DECISION_PROCEED, band="PONTO_FORTE",
            content_name="Balanceamento", objective_name=None)
        self.assertNotIn("None", f["titulo"] + f["detalhe"])
        self.assertTrue(f["detalhe"])


class ContratoTests(unittest.TestCase):

    def test_toda_decisao_conhecida_produz_texto(self):
        for d in (DECISION_PROCEED, DECISION_PREPARE, DECISION_INSUFFICIENT):
            with self.subTest(d=d):
                f = feedback_do_diagnostico(decision=d, band="X",
                                            content_name="C", objective_name="O")
                self.assertTrue(f["titulo"])
                self.assertTrue(f["detalhe"])
                self.assertIn(f["tom"], ("BOM", "REVISAR", "NEUTRO"))

    def test_decisao_desconhecida_nao_explode_nem_elogia(self):
        f = feedback_do_diagnostico(decision="COISA_NOVA", band="X",
                                     content_name="C", objective_name="O")
        self.assertEqual(f["tom"], "NEUTRO")
        self.assertNotIn("domina", f["titulo"] + f["detalhe"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
