"""CEREBRO - verificacao de span: o suporte vira propriedade checavel.

O MARCADOR JA FUNCIONAVA ASSIM
==============================

O modelo declara ``[E3]``, o codigo confere que ``E3`` existe. O span
estende a MESMA forma um nivel: o modelo declara um trecho literal, o
codigo confere por substring que aquele trecho esta NAQUELE chunk.

Nenhum juiz. Nenhum score. Nenhuma semantica.

O QUE ISTO NAO PROVA
====================

Que o trecho SUSTENTA a afirmacao. Um modelo pode citar um chunk real e
quotar dele uma frase irrelevante - a verificacao passa e o suporte nao
existe. Esta camada troca "citou um chunk real?" por "quotou texto real
daquele chunk?". Mais forte, e ainda nao o suficiente.

AS NORMALIZACOES SAO MEDIDAS, NAO SUPOSTAS
==========================================

Cada regra de ``SPAN_NORMALIZATION_V1`` tem caso real nos 5.945 chunks do
corpus. Colapso de espaco duplo NAO entra: foram medidos zero chunks com
espaco duplo, e regra sem caso real so acrescenta risco de casar o que
nao deveria.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.knowledge_engine.span_verification import (
    SPAN_CASE_MISMATCH,
    SPAN_EMPTY,
    SPAN_NORMALIZATION_V1,
    SPAN_NOT_FOUND,
    SPAN_VERIFIED,
    normalize_span,
    verify_span,
)


class NormalizationRuleTests(unittest.TestCase):
    """Uma regra por vez, com o artefato real que a justifica."""

    def test_nfkc_resolves_the_script_l_of_aluminium(self):
        """O corpus escreve ``Aℓ``; as respostas reais escreveram
        ``Aℓ2O3``."""
        self.assertEqual(normalize_span("Aℓ2O3"), "Al2O3")

    def test_nfkc_resolves_subscripts_and_superscripts(self):
        self.assertEqual(normalize_span("CH₄"), "CH4")
        self.assertEqual(normalize_span("sp³"), "sp3")

    def test_line_break_hyphenation_is_undone(self):
        """1.878 chunks (32%) tem esse artefato de extracao de PDF."""
        self.assertEqual(normalize_span("comentá- rios"), "comentários")
        self.assertEqual(normalize_span("cientí- ficas"), "científicas")

    def test_the_three_dashes_become_one(self):
        """minus em 820 chunks, en-dash em 1.816, em-dash em 165."""
        self.assertEqual(normalize_span("a − b"), "a - b")
        self.assertEqual(normalize_span("a – b"), "a - b")
        self.assertEqual(normalize_span("a — b"), "a - b")

    def test_curly_quotes_become_straight(self):
        """Aspas curvas em 958 + 951 chunks; a resposta de N9 usou “sp³”."""
        self.assertEqual(normalize_span("“sp³”"), '"sp3"')
        self.assertEqual(normalize_span("‘x’"), "'x'")

    def test_newlines_collapse_to_a_single_space(self):
        """2.711 chunks (46%) tem quebra de linha."""
        self.assertEqual(normalize_span("uma\nfrase\n\nquebrada"),
                         "uma frase quebrada")

    def test_leading_and_trailing_space_is_trimmed(self):
        self.assertEqual(normalize_span("  texto  "), "texto")


class NormalizationOrderTests(unittest.TestCase):
    def test_nfkc_runs_before_dash_unification(self):
        """``NFKC`` PRODUZ um MINUS SIGN a partir do sobrescrito.

        ``O2⁻`` vira ``O2−`` no NFKC. Se a unificacao de travessao rodasse
        antes, esse minus nasceria depois dela e sobreviveria.
        """
        self.assertEqual(normalize_span("O2⁻"), "O2-")

    def test_normalisation_is_idempotent(self):
        for bruto in ("Aℓ2O3", "comentá- rios", "a − b", "“sp³”",
                      "uma\nfrase", "O2⁻"):
            with self.subTest(bruto=bruto):
                uma = normalize_span(bruto)
                self.assertEqual(uma, normalize_span(uma))

    def test_double_space_collapse_is_not_a_rule_of_its_own(self):
        """Medidos ZERO chunks com espaco duplo.

        O colapso acontece como efeito do tratamento de ``\\s+``, nao como
        regra justificada - e o teste existe para registrar que a
        justificativa e essa, nao simetria.
        """
        self.assertEqual(normalize_span("a  b"), "a b")

    def test_the_version_is_declared(self):
        self.assertEqual(SPAN_NORMALIZATION_V1, "SPAN_NORMALIZATION_V1")


class ChemistryCaseTests(unittest.TestCase):
    """Em quimica a caixa muda o significado."""

    def test_cobalt_and_carbon_monoxide_do_not_normalise_together(self):
        self.assertNotEqual(normalize_span("Co"), normalize_span("CO"))

    def test_a_case_difference_is_not_silently_accepted(self):
        status, _ = verify_span("entalpia", "A Entalpia de combustão")
        self.assertEqual(status, SPAN_CASE_MISMATCH)

    def test_cobalt_in_a_chunk_about_carbon_monoxide_is_not_verified(self):
        status, _ = verify_span("Co", "A emissão de CO é perigosa")
        self.assertEqual(status, SPAN_CASE_MISMATCH)


class VerifySpanTests(unittest.TestCase):
    def test_a_literal_span_is_verified(self):
        fonte = "é o quociente entre a massa e o volume"
        status, pos = verify_span("quociente entre", fonte)
        self.assertEqual(status, SPAN_VERIFIED)
        # a posicao e no chunk NORMALIZADO, e tem de apontar o trecho
        self.assertEqual(fonte[pos:pos + len("quociente entre")],
                         "quociente entre")

    def test_a_span_that_is_not_there_is_not_found(self):
        status, pos = verify_span("molho de salada",
                                  "a água e o óleo não se misturam")
        self.assertEqual(status, SPAN_NOT_FOUND)
        self.assertIsNone(pos)

    def test_an_empty_span_is_named_not_silently_accepted(self):
        for vazio in ("", "   ", "\n", "\t"):
            with self.subTest(span=repr(vazio)):
                status, _ = verify_span(vazio, "qualquer texto")
                self.assertEqual(status, SPAN_EMPTY)

    def test_an_empty_chunk_cannot_verify_anything(self):
        status, _ = verify_span("algo", "")
        self.assertEqual(status, SPAN_NOT_FOUND)

    def test_the_span_matches_across_a_line_break_in_the_chunk(self):
        """O chunk tem quebra de linha; o modelo quota em linha unica."""
        status, _ = verify_span("quantidade de matéria",
                                "a quantidade de\nmatéria do soluto")
        self.assertEqual(status, SPAN_VERIFIED)

    def test_the_span_matches_across_pdf_hyphenation(self):
        status, _ = verify_span("comentários",
                                "seguem os comentá- rios finais")
        self.assertEqual(status, SPAN_VERIFIED)

    def test_a_span_with_a_different_dash_still_matches(self):
        status, _ = verify_span("-394 kJ/mol", "valor de −394 kJ/mol")
        self.assertEqual(status, SPAN_VERIFIED)

    def test_a_non_string_span_raises_rather_than_guessing(self):
        for ruim in (None, 7, ["x"]):
            with self.subTest(span=ruim):
                with self.assertRaises(TypeError):
                    verify_span(ruim, "texto")

    def test_a_non_string_chunk_raises(self):
        with self.assertRaises(TypeError):
            verify_span("x", None)


class RealCorpusShapeTests(unittest.TestCase):
    """Formas observadas de verdade no corpus e nas respostas reais."""

    def test_the_n8_derivation_inputs_match_their_chunk(self):
        """Os tres insumos de N8 saem do chunk da p.240."""
        trecho = ("entalpia de combustão do carbono −394 kJ/mol, do "
                  "hidrogênio −286 kJ/mol e do metano −891 kJ/mol")
        for insumo in ("-394", "-286", "-891"):
            with self.subTest(insumo=insumo):
                status, _ = verify_span(insumo, trecho)
                self.assertEqual(status, SPAN_VERIFIED)

    def test_the_n3_formula_matches_despite_the_script_l(self):
        status, _ = verify_span("Al2O3", "a fórmula do óxido é Aℓ2O3")
        self.assertEqual(status, SPAN_VERIFIED)

    def test_a_span_from_another_chunk_is_not_found_here(self):
        """O ponto do contrato: o span e procurado NAQUELE chunk."""
        chunk_certo = "o zinco é o ânodo e o cobre é o cátodo"
        chunk_errado = "a tabela periódica organiza os elementos"
        status, _ = verify_span("o zinco é o ânodo", chunk_errado)
        self.assertEqual(status, SPAN_NOT_FOUND)
        status, _ = verify_span("o zinco é o ânodo", chunk_certo)
        self.assertEqual(status, SPAN_VERIFIED)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
