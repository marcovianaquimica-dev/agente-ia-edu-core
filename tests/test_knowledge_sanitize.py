"""CEREBRO - remocao dos marcadores para apresentacao publica.

DEPOIS DA VALIDACAO, NUNCA ANTES
================================

Os marcadores sao a unica evidencia verificavel de que a resposta cita o
que diz citar. Remove-los antes de conferir destruiria a prova. Por isso
esta peca e PURA e nao sabe nada sobre validacao: quem a chama ja validou.

O QUE ELA NAO TENTA FAZER
=========================

Nao reescreve frase. Se remover o marcador deixar o texto quebrado, o
texto nao e consertado - e REPROVADO, com o defeito nomeado. Consertar
portugues com LLM para publicar e trocar um problema visivel por um
invisivel.

O LIMITE, DECLARADO
===================

Corrupcao SEMANTICA nem sempre deixa marca textual. "segundo [E1], a
concentracao" vira "segundo, a concentracao" sem espaco duplo nem
pontuacao orfa. Para esse caso ha uma heuristica estreita - uma lista
fechada de palavras que regem complemento -, e ela falha para o lado
seguro: na duvida, reprova. Fora dessa lista, o risco permanece e esta
registrado.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.knowledge_engine.sanitize import (
    DOUBLED_PUNCTUATION,
    EMPTY_AFTER_STRIP,
    GOVERNING_WORD_LEFT_DANGLING,
    LEADING_PUNCTUATION,
    MARKER_RESIDUE,
    UNBALANCED_BRACKETS,
    strip_markers,
)

#: As nove respostas REAIS ja produzidas pelo sistema. Texto do modelo,
#: nao literal de obra - pode viver no repositorio.
RESPOSTAS_REAIS = (
    'Calcula-se dividindo a quantidade de matéria do soluto, em mol, pelo '
    'volume da solução, em litros: c = n/V. [E1] [E4]',
    'Calcula-se dividindo a quantidade de matéria do soluto, em mol, pelo '
    'volume da solução, em litros: ℳ = n_soluto / V_solução. A unidade é '
    'mol/L, também podendo ser mol/dm³. [E1] [E2]',
    'Dalton propôs o átomo como uma esfera indivisível e sem considerar a '
    'natureza elétrica da matéria. [E3] Thomson modificou esse modelo ao '
    'incluir elétrons, subpartículas menores, em uma esfera de carga '
    'positiva. [E3] Rutherford, com base no experimento de espalhamento de '
    'partículas alfa em uma lâmina de ouro, concluiu que o átomo possui um '
    'núcleo central de carga positiva e uma eletrosfera com grande espaço '
    'vazio, pois a maioria das partículas atravessava a lâmina sem desvio, '
    'enquanto algumas sofriam grandes desvios. [E1]',
    'Uma substância é ácida quando, em solução aquosa, libera ou forma íons '
    'H+; é básica quando libera ou origina íons OH−, segundo Arrhenius. [E3] '
    'Pela teoria de Brønsted-Lowry, ácido é a espécie que doa prótons, '
    'enquanto base é a que recebe prótons; essa classificação depende das '
    'outras espécies presentes na reação. [E5] Em geral, soluções ácidas '
    'apresentam pH abaixo de 7, e quanto maior a concentração de H+, menor '
    'o pH. [E3] [E4]',
    'A tabela periódica atual organiza os elementos químicos em ordem '
    'crescente de número atômico. [E3] [E4] Suas colunas verticais são '
    'chamadas grupos, e suas linhas horizontais, períodos. [E1] Elementos do '
    'mesmo grupo apresentam propriedades semelhantes, que variam '
    'gradativamente ao longo dos períodos. [E1] Os grupos 1, 2 e 13 a 18 são '
    'formados por elementos representativos, enquanto os grupos 3 a 12 '
    'correspondem aos elementos de transição. [E1] Atualmente, a tabela '
    'reúne 118 elementos químicos. [E3]',
    'Balanceia-se uma equação química ajustando coeficientes numéricos '
    'antes das fórmulas, de modo que o número de átomos de cada elemento '
    'seja igual nos reagentes e nos produtos. [E1] Faça isso por observação, '
    'tentativa e erro, começando por um elemento e recontando os átomos '
    'após cada ajuste. [E1] Ao final, confira se todos os elementos têm a '
    'mesma quantidade nos dois lados. [E2] Devem ser escolhidos, em geral, '
    'os menores coeficientes inteiros possíveis; o coeficiente 1 pode ser '
    'omitido. [E2] Por exemplo, em N₂ + H₂ → NH₃, primeiro se coloca 2 antes '
    'de NH₃ para igualar o nitrogênio e depois 3 antes de H₂ para igualar o '
    'hidrogênio: N₂ + 3 H₂ → 2 NH₃. [E2]',
    'A entalpia de formação do metano é aproximadamente **−75 kJ/mol**, '
    'obtida pela lei de Hess a partir das entalpias de combustão do carbono '
    '(−394 kJ/mol), do hidrogênio (−286 kJ/mol) e do metano (−891 kJ/mol): '
    'ΔHf(CH₄) = [−394 + 2(−286)] − (−891) = −75 kJ/mol. [E5]',
    'As evidências não bastam para definir completamente a hibridização '
    'sp³, pois não explicam o significado de “sp³”. Elas informam que um '
    'átomo de carbono que estabelece quatro ligações simples apresenta '
    'geometria tetraédrica com seus ligantes. [E6]',
    'Não é possível determinar o mecanismo da reação de Diels-Alder nem sua '
    'estereoquímica com as evidências fornecidas. Elas tratam genericamente '
    'de reações de adição [E4] e de alguns casos de estereoisomeria [E3], '
    'mas não descrevem especificamente a reação de Diels-Alder.',
)


class BasicStrippingTests(unittest.TestCase):
    def test_a_marker_after_punctuation_goes_cleanly(self):
        limpo, art = strip_markers("A concentração é n/V. [E1]")
        self.assertEqual(limpo, "A concentração é n/V.")
        self.assertEqual(art, ())

    def test_two_consecutive_markers_go_together(self):
        limpo, art = strip_markers("A concentração é n/V. [E1] [E4]")
        self.assertEqual(limpo, "A concentração é n/V.")
        self.assertEqual(art, ())

    def test_a_marker_in_the_middle_of_a_clause_can_be_fine(self):
        """Nem todo marcador no meio corrompe - foi o caso real de N10."""
        limpo, art = strip_markers(
            "Elas tratam de reações de adição [E4] e de estereoisomeria [E3], "
            "mas não descrevem a reação."
        )
        self.assertEqual(
            limpo,
            "Elas tratam de reações de adição e de estereoisomeria, "
            "mas não descrevem a reação.",
        )
        self.assertEqual(art, ())

    def test_a_marker_at_the_very_start_is_removed(self):
        limpo, art = strip_markers("[E1] A concentração é n/V.")
        self.assertEqual(limpo, "A concentração é n/V.")
        self.assertEqual(art, ())

    def test_surrounding_whitespace_collapses(self):
        limpo, _ = strip_markers("Texto   [E1]    mais texto.")
        self.assertEqual(limpo, "Texto mais texto.")

    def test_space_before_punctuation_is_repaired(self):
        limpo, art = strip_markers("Fim da frase [E2].")
        self.assertEqual(limpo, "Fim da frase.")
        self.assertEqual(art, ())

    def test_the_same_marker_repeated_is_removed_everywhere(self):
        limpo, _ = strip_markers("Um [E1] dois [E1] três [E1].")
        self.assertEqual(limpo, "Um dois três.")

    def test_a_bare_marker_without_brackets_is_left_alone(self):
        """``E1`` solto pode ser conteudo legitimo. So a forma com
        colchetes e removida - a mesma que a validacao reconhece."""
        limpo, art = strip_markers("A constante E1 vale 3.")
        self.assertEqual(limpo, "A constante E1 vale 3.")
        self.assertEqual(art, ())

    def test_text_without_any_marker_is_returned_identical(self):
        texto = "Uma frase comum, sem marcador nenhum."
        limpo, art = strip_markers(texto)
        self.assertEqual(limpo, texto)
        self.assertEqual(art, ())

    def test_stripping_is_idempotent(self):
        uma, _ = strip_markers("A é B. [E1] [E2]")
        duas, _ = strip_markers(uma)
        self.assertEqual(uma, duas)

    def test_empty_text_stays_empty_without_complaining(self):
        """Resposta vazia e problema de outra camada, nao desta."""
        limpo, art = strip_markers("")
        self.assertEqual(limpo, "")
        self.assertEqual(art, ())


class ArtifactTests(unittest.TestCase):
    def test_an_answer_that_is_only_a_marker_empties_out_and_is_flagged(self):
        limpo, art = strip_markers("[E1]")
        self.assertEqual(limpo, "")
        self.assertIn(EMPTY_AFTER_STRIP, art)

    def test_only_markers_and_spaces_also_empties_out(self):
        limpo, art = strip_markers("  [E1] [E2]  ")
        self.assertEqual(limpo, "")
        self.assertIn(EMPTY_AFTER_STRIP, art)

    def test_text_left_starting_with_punctuation_is_flagged(self):
        limpo, art = strip_markers("[E1], o resto da frase.")
        self.assertIn(LEADING_PUNCTUATION, art)

    def test_doubled_punctuation_INTRODUCED_by_the_strip_is_flagged(self):
        """A remocao juntou o ponto com a virgula que vinha depois."""
        limpo, art = strip_markers("Primeira frase. [E1], segunda.")
        self.assertIn(DOUBLED_PUNCTUATION, art)
        self.assertIn(".,", limpo)

    def test_doubled_punctuation_that_was_ALREADY_there_is_not_blamed(self):
        """``Fim!!`` e cacoete do modelo, nao dano da remocao.

        Reprovar por isso bloquearia resposta boa por um defeito que a
        peca nao causou e nao tem como consertar.
        """
        _, art = strip_markers("Fim!! [E1]")
        self.assertEqual(art, ())

    def test_leading_punctuation_that_was_already_there_is_not_blamed(self):
        _, art = strip_markers(", já começava assim [E1]")
        self.assertEqual(art, ())

    def test_an_ellipsis_is_not_doubled_punctuation(self):
        """``...`` e legitimo e nao pode virar falso positivo."""
        limpo, art = strip_markers("E assim por diante... [E1]")
        self.assertEqual(art, ())
        self.assertEqual(limpo, "E assim por diante...")

    def test_a_malformed_marker_leaves_an_unbalanced_bracket(self):
        limpo, art = strip_markers("Texto [E1 sem fechar.")
        self.assertIn(UNBALANCED_BRACKETS, art)

    def test_balanced_brackets_that_are_not_markers_are_fine(self):
        """Caso REAL de N8: ``[−394 + 2(−286)]`` e matematica, nao marcador."""
        texto = "ΔHf(CH₄) = [−394 + 2(−286)] − (−891) = −75 kJ/mol. [E5]"
        limpo, art = strip_markers(texto)
        self.assertEqual(art, ())
        self.assertIn("[−394 + 2(−286)]", limpo)
        self.assertNotIn("[E5]", limpo)

    def test_a_surviving_marker_would_be_flagged(self):
        """Defensivo: se a remocao falhar, que falhe RUIDOSA."""
        from agente_ia_edu.services.knowledge_engine import sanitize

        self.assertIn(MARKER_RESIDUE, sanitize.ARTIFACTS)


class GoverningWordTests(unittest.TestCase):
    """A corrupcao semantica que nao deixa marca textual."""

    def test_segundo_left_dangling_is_flagged(self):
        limpo, art = strip_markers("Segundo [E1], a concentração é n/V.")
        self.assertIn(GOVERNING_WORD_LEFT_DANGLING, art)

    def test_de_acordo_com_left_dangling_is_flagged(self):
        limpo, art = strip_markers("De acordo com [E2], o pH cai.")
        self.assertIn(GOVERNING_WORD_LEFT_DANGLING, art)

    def test_em_left_dangling_is_flagged(self):
        limpo, art = strip_markers("Como se vê em [E1] o valor é alto.")
        self.assertIn(GOVERNING_WORD_LEFT_DANGLING, art)

    def test_a_content_word_before_a_marker_is_not_flagged(self):
        """Caso real de N10 - nao pode virar falso positivo."""
        limpo, art = strip_markers("reações de adição [E4] e de outras.")
        self.assertEqual(art, ())

    def test_punctuation_before_a_marker_is_never_dangling(self):
        limpo, art = strip_markers("Fim da frase. [E1]")
        self.assertEqual(art, ())

    def test_the_governing_check_is_case_insensitive(self):
        _, art = strip_markers("SEGUNDO [E1], o valor sobe.")
        self.assertIn(GOVERNING_WORD_LEFT_DANGLING, art)


class RealAnswerRegressionTests(unittest.TestCase):
    """As nove respostas que o sistema realmente produziu."""

    def test_no_real_answer_produces_any_artifact(self):
        for i, texto in enumerate(RESPOSTAS_REAIS):
            with self.subTest(resposta=i):
                _, art = strip_markers(texto)
                self.assertEqual(art, (), f"artefato em {i}: {art}")

    def test_no_real_answer_keeps_a_marker_after_stripping(self):
        for i, texto in enumerate(RESPOSTAS_REAIS):
            with self.subTest(resposta=i):
                limpo, _ = strip_markers(texto)
                self.assertNotRegex(limpo, r"\[E\d+\]")

    def test_every_real_answer_keeps_its_substance(self):
        """Remover marcador nao pode encolher o texto alem do esperado."""
        for i, texto in enumerate(RESPOSTAS_REAIS):
            with self.subTest(resposta=i):
                limpo, _ = strip_markers(texto)
                marcadores = texto.count("[E")
                # cada marcador tem 4 ou 5 chars mais o espaco
                self.assertGreaterEqual(len(limpo), len(texto) - marcadores * 7)
                self.assertTrue(limpo)

    def test_the_n8_formula_survives_intact(self):
        limpo, _ = strip_markers(RESPOSTAS_REAIS[6])
        self.assertIn("ΔHf(CH₄) = [−394 + 2(−286)] − (−891) = −75 kJ/mol",
                      limpo)

    def test_stripping_every_real_answer_is_idempotent(self):
        for i, texto in enumerate(RESPOSTAS_REAIS):
            with self.subTest(resposta=i):
                uma, _ = strip_markers(texto)
                duas, _ = strip_markers(uma)
                self.assertEqual(uma, duas)


class PurityTests(unittest.TestCase):
    def test_the_function_does_not_mutate_its_input(self):
        texto = "Original [E1] intacto."
        copia = str(texto)
        strip_markers(texto)
        self.assertEqual(texto, copia)

    def test_artifacts_are_a_tuple_of_declared_names(self):
        from agente_ia_edu.services.knowledge_engine import sanitize

        _, art = strip_markers("[E1], texto.")
        self.assertIsInstance(art, tuple)
        for a in art:
            self.assertIn(a, sanitize.ARTIFACTS)

    def test_a_non_string_input_raises_rather_than_guessing(self):
        for valor in (None, 7, ["texto"]):
            with self.subTest(valor=valor):
                with self.assertRaises(TypeError):
                    strip_markers(valor)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
