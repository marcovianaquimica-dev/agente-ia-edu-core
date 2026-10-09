"""CEREBRO / Knowledge Engine - Fase 3: heuristicas de estrutura.

Ambicao limitada de proposito (spec 20.4): estas heuristicas nao resolvem
todo caso extremo. O que elas PRECISAM fazer e nao arruinar o que importa -
texto, pagina, hierarquia e hash seguem corretos qualquer que seja o tipo
decidido.

Por isso ha tantos casos NEGATIVOS aqui quanto positivos: um detector que so
e testado no caso feliz vira um gerador de falso positivo silencioso.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.knowledge_engine.structure import (
    StructureVerdict,
    classify_block,
    describe_block,
    has_figure_caption,
    is_displayed_formula,
    is_formula_suspect,
    is_markdown_table,
    references_table,
)


class WorkedExampleTests(unittest.TestCase):
    def test_an_explicit_worked_example_marker_is_recognised(self):
        for marker in ("Exemplo resolvido", "Exercício resolvido", "Resolução:", "Solução:"):
            verdict = classify_block(f"{marker}\nCalcule a massa de CO2 produzida.")
            self.assertEqual(verdict.chunk_type, "WORKED_EXAMPLE", marker)
            self.assertIn("worked_example_marker", verdict.signals)

    def test_a_worked_example_that_contains_a_formula_is_still_a_worked_example(self):
        """O mais especifico vence: exemplo resolvido com equacao dentro e
        exemplo resolvido, nao formula."""
        verdict = classify_block("Exemplo 3\nN2 + 3H2 → 2NH3\nCalcule o rendimento.")
        self.assertEqual(verdict.chunk_type, "WORKED_EXAMPLE")

    def test_merely_mentioning_the_word_example_is_not_a_worked_example(self):
        verdict = classify_block(
            "Por exemplo, a agua e um solvente universal e dissolve muitos sais."
        )
        self.assertEqual(verdict.chunk_type, "PROSE")


class FormulaTests(unittest.TestCase):
    def test_a_short_displayed_equation_is_a_formula(self):
        self.assertTrue(is_displayed_formula("N2 + 3H2 → 2NH3"))
        self.assertEqual(classify_block("N2 + 3H2 → 2NH3").chunk_type, "FORMULA")

    def test_a_long_paragraph_that_merely_mentions_an_equation_is_prose(self):
        """Paragrafo longo com um '=' e prosa que fala de uma equacao."""
        text = (
            "A equacao de estado dos gases ideais, PV = nRT, relaciona pressao, "
            "volume, quantidade de materia e temperatura. Ela e util para estimar "
            "o comportamento de gases em condicoes moderadas de pressao, e deixa "
            "de valer quando as interacoes intermoleculares deixam de ser "
            "desprezaveis, o que acontece em pressoes elevadas ou temperaturas "
            "muito baixas, conforme discutido adiante neste capitulo."
        )
        self.assertFalse(is_displayed_formula(text))
        self.assertEqual(classify_block(text).chunk_type, "PROSE")

    def test_a_sentence_with_punctuation_is_not_a_displayed_formula(self):
        self.assertFalse(is_displayed_formula("A massa e 10 g. O volume e 2 L."))

    def test_plain_prose_has_no_formula_density(self):
        self.assertFalse(is_displayed_formula("A quimica estuda a materia e suas transformacoes"))

    def test_corrupted_glyphs_are_flagged_not_fixed(self):
        """O projeto ja tem um caso real e irreparavel disso (CambriaMath em
        prova da FUVEST). Marcar e util; fingir conserto nao e."""
        self.assertTrue(is_formula_suspect("a massa � de � 10 g"))
        self.assertFalse(is_formula_suspect("a massa e de 10 g"))


class TableTests(unittest.TestCase):
    def test_a_markdown_table_is_recognised(self):
        table = "| Elemento | Massa |\n|---|---|\n| H | 1 |\n| O | 16 |"
        self.assertTrue(is_markdown_table(table))
        self.assertEqual(classify_block(table).chunk_type, "TABLE")

    def test_a_single_piped_line_is_not_a_table(self):
        self.assertFalse(is_markdown_table("| apenas uma linha |"))

    def test_prose_mentioning_a_table_is_not_a_table(self):
        """Em PDF nao ha reconstrucao de tabela: a referencia e registrada e o
        texto segue como prosa. Um TABLE inventado a partir de texto achatado
        seria pior que nenhum."""
        text = "Os valores estao reunidos na Tabela 3, a seguir, para consulta rapida."
        self.assertFalse(is_markdown_table(text))
        self.assertEqual(classify_block(text).chunk_type, "PROSE")
        self.assertTrue(references_table(text))


class DefinitionAndSummaryTests(unittest.TestCase):
    def test_definition_phrases_are_recognised(self):
        for phrase in (
            "Mol é definido como a quantidade de materia que contem 6,02x10^23 entidades",
            "Chama-se reagente limitante aquele que se esgota primeiro",
            "Denomina-se solucao a mistura homogenea de dois ou mais componentes",
        ):
            self.assertEqual(classify_block(phrase).chunk_type, "DEFINITION", phrase[:30])

    def test_a_summary_heading_is_recognised(self):
        self.assertEqual(classify_block("Resumo\nNeste capitulo vimos...").chunk_type, "SUMMARY")

    def test_ordinary_prose_stays_prose(self):
        verdict = classify_block(
            "A estequiometria permite calcular quanto de cada substancia participa "
            "de uma reacao, a partir das proporcoes da equacao balanceada."
        )
        self.assertEqual(verdict, StructureVerdict("PROSE", ()))


class FigureCaptionTests(unittest.TestCase):
    def test_a_caption_is_recognised(self):
        for caption in ("Figura 3 — Esquema do experimento", "Gráfico 2: variacao da massa"):
            self.assertTrue(has_figure_caption(caption), caption)

    def test_prose_mentioning_a_figure_without_a_caption_is_not_one(self):
        self.assertFalse(has_figure_caption("como mostra a figura ao lado, o nivel sobe"))


class DescribeBlockTests(unittest.TestCase):
    """Sinais auxiliares acompanham QUALQUER chunk, seja qual for o tipo -
    e o que torna o refinamento posterior possivel sem reprocessar tudo."""

    def test_the_auxiliary_signals_are_always_reported(self):
        described = describe_block("Figura 1 — reacao N2 + 3H2 → 2NH3, ver Tabela 2")
        self.assertEqual(
            described,
            {
                "has_formula": True,
                "formula_suspect": False,
                "has_figure_caption": True,
                "references_table": True,
            },
        )

    def test_plain_prose_reports_all_false(self):
        self.assertEqual(
            set(describe_block("Texto simples sem nada de especial.").values()), {False}
        )


class ExerciseIsNotDecidedHereTests(unittest.TestCase):
    def test_exercise_never_comes_from_a_text_heuristic(self):
        """Exercicio vem do parser, que ja o detecta com numero do item e
        pagina. Duplicar isso em heuristica textual criaria duas verdades."""
        for text in ("1. Calcule a massa molar do CO2.", "Exercicios propostos"):
            self.assertNotEqual(classify_block(text).chunk_type, "EXERCISE")


if __name__ == "__main__":
    unittest.main()
