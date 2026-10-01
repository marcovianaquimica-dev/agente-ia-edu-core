"""CEREBRO / Knowledge Engine - Fase 3.1: o portao de promocao de EXERCISE.

A auditoria mediu precisao real de ~25% na classe EXERCISE: dos 1.535 chunks
rotulados no livro real, ~1.150 eram falso positivo. A maior fonte
identificavel sao 261 gabaritos - este livro e edicao do professor e traz as
respostas.

O portao e PURO: entra texto, sai veredito. ``authorial_material_parser.py``
nao e alterado, e a PHASE 26 nao muda de comportamento - o parser continua
entregando candidatos como sempre, e o Knowledge Engine passa a decidir.

Ha tantos casos NEGATIVOS aqui quanto positivos. Um portao testado so no caso
feliz troca um falso positivo por um falso negativo, e perder exercicio
verdadeiro e pior que manter ruido.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.knowledge_engine.structure import (
    ExerciseVerdict,
    judge_exercise_candidate,
)


class PositiveEvidenceTests(unittest.TestCase):
    """Evidencia positiva e NECESSARIA para promover (requisito 2)."""

    def test_multiple_choice_alternatives_promote(self):
        verdict = judge_exercise_candidate(
            "Qual das substancias a seguir e um oxido?\n"
            "a) NaCl\nb) CO2\nc) HCl\nd) NaOH\ne) CH4"
        )
        self.assertEqual(verdict.chunk_type, "EXERCISE")
        self.assertIn("alternatives", verdict.evidence)

    def test_lowercase_dotted_alternatives_also_promote(self):
        """O livro real usa "a." em varios exercicios; exigir ")" perdia
        exercicio verdadeiro - achado da auditoria."""
        verdict = judge_exercise_candidate(
            "Em uma amostra de agua liquida totalmente pura: "
            "a. nao existem ions. b. os unicos ions presentes sao H+ e OH-. "
            "c. a condutividade e nula."
        )
        self.assertEqual(verdict.chunk_type, "EXERCISE")
        self.assertIn("alternatives", verdict.evidence)

    def test_an_exam_attribution_promotes(self):
        verdict = judge_exercise_candidate(
            "(Unievangelica-GO) O coeficiente de solubilidade de um sal e de "
            "40 g por 100 g de agua a 80 graus."
        )
        self.assertEqual(verdict.chunk_type, "EXERCISE")
        self.assertIn("exam_source", verdict.evidence)

    def test_an_imperative_at_the_start_promotes(self):
        for opener in ("Calcule", "Determine", "Explique", "Justifique", "Pesquisem"):
            verdict = judge_exercise_candidate(f"{opener} a massa molar do dioxido de carbono.")
            self.assertEqual(verdict.chunk_type, "EXERCISE", opener)
            self.assertIn("command", verdict.evidence)

    def test_a_short_question_promotes(self):
        verdict = judge_exercise_candidate(
            "O que acontece com a concentracao quando se adiciona solvente?"
        )
        self.assertEqual(verdict.chunk_type, "EXERCISE")
        self.assertIn("short_question", verdict.evidence)

    def test_the_decision_reason_is_recorded_on_promotion(self):
        verdict = judge_exercise_candidate("Calcule a massa molar do CO2.")
        self.assertEqual(verdict.decision_reason, "PROMOTED")
        self.assertIsNone(verdict.demoted_from)


class NoEvidenceTests(unittest.TestCase):
    def test_prose_without_any_signal_is_not_promoted(self):
        verdict = judge_exercise_candidate(
            "Empreendimentos de impacto social sao voltados a individuos de "
            "baixa renda, permitindo-lhes acesso a bens e servicos."
        )
        self.assertIsNone(verdict.chunk_type)
        self.assertEqual(verdict.decision_reason, "NO_EVIDENCE")
        self.assertEqual(verdict.demoted_from, "EXERCISE")
        self.assertEqual(verdict.evidence, ())

    def test_a_conjugated_verb_in_prose_is_not_a_command(self):
        """A primeira versao da auditoria procurava comando em todo o texto e
        classificava isto como exercicio."""
        verdict = judge_exercise_candidate(
            "Em diversas plataformas, algoritmos de inteligencia artificial "
            "distribuem o trabalho e determinam a remuneracao de cada entrega."
        )
        self.assertIsNone(verdict.chunk_type)
        self.assertEqual(verdict.decision_reason, "NO_EVIDENCE")

    def test_a_long_text_with_a_question_mark_is_not_a_short_question(self):
        long_text = (
            "A quimica estuda a materia e suas transformacoes. " * 40
            + " Sera que isso vale sempre?"
        )
        verdict = judge_exercise_candidate(long_text)
        self.assertIsNone(verdict.chunk_type)
        self.assertNotIn("short_question", verdict.evidence)


class VetoTests(unittest.TestCase):
    """Vetos PREVALECEM sobre qualquer evidencia (requisito 3)."""

    def test_an_answer_opener_becomes_solution_even_with_alternatives(self):
        """Um gabarito costuma CITAR as alternativas do enunciado - e por isso
        que o veto vem antes da evidencia."""
        verdict = judge_exercise_candidate(
            "Alternativa A. CH2O: trigonal plana; HCN: linear; H2O: angular; "
            "a) errada b) errada c) errada"
        )
        self.assertEqual(verdict.chunk_type, "SOLUTION")
        self.assertIn("answer_opener", verdict.vetoes)
        self.assertEqual(verdict.decision_reason, "ANSWER_KEY")
        self.assertEqual(verdict.demoted_from, "EXERCISE")

    def test_every_answer_opener_form_is_recognised(self):
        for opener in (
            "Alternativa D. O soluto e acido nitrico.",
            "Resposta: a concentracao e 3,92 g/L.",
            "Resposta pessoal. Convide um grupo a complementar.",
            "Resolucao: aplicando a regra de tres chega-se a 44 g.",
            "Gabarito das atividades do capitulo.",
            "Comentario: o aluno deve perceber que a massa se conserva.",
        ):
            verdict = judge_exercise_candidate(opener)
            self.assertEqual(verdict.chunk_type, "SOLUTION", opener)

    def test_a_caption_opener_is_vetoed_and_reclassified(self):
        verdict = judge_exercise_candidate(
            "Figura 9. Representacao grafica da energia em funcao da "
            "coordenada de reacao. Calcule a variacao de entalpia."
        )
        self.assertIsNone(verdict.chunk_type)
        self.assertIn("caption_opener", verdict.vetoes)
        self.assertEqual(verdict.decision_reason, "VETOED")
        self.assertEqual(verdict.demoted_from, "EXERCISE")

    def test_a_bibliographic_box_opener_is_vetoed(self):
        verdict = judge_exercise_candidate(
            "(Colecao de Quimica conceitual.) Escrito por pesquisador "
            "brasileiro de nanotecnologia, esse livro apresenta."
        )
        self.assertIsNone(verdict.chunk_type)
        self.assertIn("box_opener", verdict.vetoes)
        self.assertEqual(verdict.decision_reason, "VETOED")

    def test_a_veto_beats_an_exam_attribution(self):
        verdict = judge_exercise_candidate(
            "Resolucao: (ENEM 2019) a resposta correta usa a lei de Hess."
        )
        self.assertEqual(verdict.chunk_type, "SOLUTION")


class VerdictShapeTests(unittest.TestCase):
    """A decisao tem de ser rastreavel (requisito 5)."""

    def test_the_verdict_is_immutable(self):
        verdict = judge_exercise_candidate("Calcule a massa.")
        self.assertIsInstance(verdict, ExerciseVerdict)
        with self.assertRaises(Exception):
            verdict.chunk_type = "PROSE"  # type: ignore[misc]

    def test_every_decision_reason_is_from_the_closed_vocabulary(self):
        reasons = {
            judge_exercise_candidate(text).decision_reason
            for text in (
                "Calcule a massa molar do CO2.",
                "Alternativa D. O soluto e acido.",
                "Figura 3. Esquema do experimento.",
                "A quimica estuda a materia e suas transformacoes.",
            )
        }
        self.assertEqual(reasons, {"PROMOTED", "ANSWER_KEY", "VETOED", "NO_EVIDENCE"})

    def test_an_empty_candidate_has_no_evidence(self):
        verdict = judge_exercise_candidate("   ")
        self.assertIsNone(verdict.chunk_type)
        self.assertEqual(verdict.decision_reason, "NO_EVIDENCE")

    def test_solution_is_never_the_result_of_missing_evidence(self):
        """SOLUTION e uma identificacao DETERMINISTICA de gabarito, nunca um
        destino para "nao sei o que isto e"."""
        verdict = judge_exercise_candidate(
            "Transmutacao nuclear. Em 1914 o fisico Ernest Rutherford constatou."
        )
        self.assertNotEqual(verdict.chunk_type, "SOLUTION")


if __name__ == "__main__":
    unittest.main()
