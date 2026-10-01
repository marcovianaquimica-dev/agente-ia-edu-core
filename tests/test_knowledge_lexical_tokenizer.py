"""CEREBRO - Fase 5: normalizacao, tokenizacao e posicoes.

Funcoes PURAS. Nenhum banco, nenhuma sessao - a normalizacao e a parte da
busca lexical que mais precisa ser legivel e testavel sozinha, e e a que
define o que o indice significa.

DUAS DECISOES QUE ESTE ARQUIVO FIXA
===================================

**Nao usamos o stemmer ``portuguese`` do PostgreSQL.** Sonda do PG 16.15:
``concentração`` -> ``concentr`` mas ``concentracao`` -> ``concentraca``;
``solucao`` -> ``soluca`` mas ``solucoes`` -> ``soluco``; ``mol`` -> ``mol``
mas ``mols`` -> ``mols``. Quem digita sem acento nao acha nada, e singular e
plural se separam. Nossa normalizacao e uma funcao versionada, com
``normalizer_version`` gravado linha por linha no indice.

**As posicoes nao sao comprimidas por stopword** (ajuste 2 da Fase 5). A
stopword nao gera posting, mas OCUPA posicao:

    "concentracao das solucoes"  ->  concentracao@0 , solucao@2

Comprimir para @0/@1 destruiria informacao que a politica de frase ainda vai
querer: "a de b" e "a b" ficariam indistinguiveis NO INDICE, e nenhuma
politica posterior poderia recuperar a diferenca.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.knowledge_retrieval_policy.v1 import POLICY
from agente_ia_edu.services.knowledge_engine.lexical_tokenizer import (
    HEADING_POSITION_BASE,
    TokenStream,
    best_proximity_span,
    find_phrase_occurrences,
    normalize_term,
    normalize_text,
    tokenize,
    tokenize_heading,
)


class NormalizeTextTests(unittest.TestCase):
    def test_accents_and_case_collapse(self):
        self.assertEqual(normalize_text("Diluição"), "diluicao")
        self.assertEqual(normalize_text("DILUIÇÃO"), "diluicao")
        self.assertEqual(normalize_text("diluicao"), "diluicao")

    def test_cedilla_and_tilde_and_circumflex(self):
        self.assertEqual(normalize_text("Ligação Iônica"), "ligacao ionica")
        self.assertEqual(normalize_text("número"), "numero")


class NormalizeTermTests(unittest.TestCase):
    """A regra: ``oes -> ao``, depois ``-s``. Nada de derivacao."""

    def test_oes_becomes_ao(self):
        self.assertEqual(normalize_term("solucoes"), "solucao")
        self.assertEqual(normalize_term("concentracoes"), "concentracao")
        self.assertEqual(normalize_term("ligacoes"), "ligacao")

    def test_plain_plural_loses_the_s(self):
        self.assertEqual(normalize_term("reagentes"), "reagente")
        self.assertEqual(normalize_term("mols"), "mol")
        self.assertEqual(normalize_term("atomos"), "atomo")

    def test_the_es_rule_is_deliberately_absent(self):
        """A regra ``es -> vazio`` de ``curriculum_classification`` divide
        singular e plural de toda palavra cujo singular termina em ``-e`` -
        classe enorme em quimica. Medido no livro real: sob ela ``reagente``
        tem df 149; sem ela, 347. Metade das ocorrencias ficava inalcancavel.
        """
        self.assertEqual(normalize_term("reagentes"), normalize_term("reagente"))
        self.assertEqual(normalize_term("solventes"), normalize_term("solvente"))
        self.assertEqual(normalize_term("oxidantes"), normalize_term("oxidante"))

    def test_double_s_is_preserved(self):
        self.assertEqual(normalize_term("massa"), "massa")
        self.assertEqual(normalize_term("gauss"), "gauss")

    def test_short_terms_are_not_mutilated(self):
        """``mol`` tem 3 letras e e o termo central da quimica. Nenhuma regra
        pode encurta-lo, nem o ``-s`` (``gas`` -> ``gas``, nao ``ga``)."""
        self.assertEqual(normalize_term("mol"), "mol")
        self.assertEqual(normalize_term("gas"), "gas")
        self.assertEqual(normalize_term("pes"), "pes")

    def test_it_is_idempotent(self):
        for term in ("solucoes", "reagentes", "mols", "massa", "mol"):
            self.assertEqual(normalize_term(normalize_term(term)), normalize_term(term))


class PositionsTests(unittest.TestCase):
    """Ajuste 2 da Fase 5, verificado no exemplo que o aprovou."""

    def test_stopwords_do_not_compress_positions(self):
        stream = tokenize("concentração das soluções")
        self.assertEqual(stream.positions["concentracao"], (0,))
        self.assertEqual(stream.positions["solucao"], (2,))

    def test_a_stopword_produces_no_posting_but_occupies_a_position(self):
        stream = tokenize("concentração das soluções")
        self.assertNotIn("das", stream.positions)
        self.assertEqual(stream.terms, ("concentracao", "solucao"))
        self.assertEqual(stream.stream_length, 3)

    def test_a_short_token_also_occupies_a_position(self):
        """``pH`` tem 2 caracteres e nao vira posting, mas o termo seguinte
        nao pode herdar a posicao dele."""
        stream = tokenize("medida de pH das solucoes")
        self.assertEqual(stream.positions["medida"], (0,))
        self.assertEqual(stream.positions["solucao"], (4,))

    def test_repeated_terms_keep_every_position(self):
        stream = tokenize("mol de mol para mol")
        self.assertEqual(stream.positions["mol"], (0, 2, 4))
        self.assertEqual(stream.frequencies["mol"], 3)

    def test_token_count_counts_postings_not_the_stream(self):
        """``dl`` do BM25 e o numero de tokens INDEXADOS. A stopword nao
        engorda o documento."""
        stream = tokenize("concentração das soluções")
        self.assertEqual(stream.token_count, 2)
        self.assertEqual(stream.stream_length, 3)


class TokenizeTests(unittest.TestCase):
    def test_dropped_tokens_are_reported_with_a_reason(self):
        """A ordem das razoes importa: o filtro de tamanho vem primeiro, logo
        ``de`` sai como TOO_SHORT e nao como STOPWORD. As duas razoes sao
        verdadeiras; a reportada e a que de fato descartou o token."""
        stream = tokenize("a concentração para pH")
        reasons = {token: reason for token, reason in stream.dropped}
        self.assertEqual(reasons["a"], "TOO_SHORT")
        self.assertEqual(reasons["para"], "STOPWORD")
        self.assertEqual(reasons["ph"], "TOO_SHORT")

    def test_punctuation_and_symbols_never_become_terms(self):
        stream = tokenize("H2O, NaCl; 25 °C -- (massa)")
        self.assertIn("massa", stream.terms)
        self.assertIn("nacl", stream.terms)
        self.assertNotIn(",", "".join(stream.terms))

    def test_a_bncc_code_survives_as_one_term(self):
        """``EM13CNT301`` e a unica chave util de uma habilidade. Quebra-la em
        ``em``/``13``/``cnt``/``301`` tornaria a BNCC inbuscavel."""
        stream = tokenize("A habilidade EM13CNT301 trata de energia")
        self.assertIn("em13cnt301", stream.terms)

    def test_a_term_is_truncated_to_the_column_width(self):
        long_term = "a" * 120
        stream = tokenize(long_term)
        self.assertEqual(len(stream.terms[0]), POLICY.max_term_length)

    def test_empty_and_whitespace_are_safe(self):
        for value in ("", "   ", "\n\t"):
            stream = tokenize(value)
            self.assertEqual(stream.terms, ())
            self.assertEqual(stream.token_count, 0)


class TokenizeHeadingTests(unittest.TestCase):
    """O heading real do corpus e ``Chapter N`` em 70,5% dos 2.668 chunks, e
    o token ``chapter`` aparece em 2.662 deles. Pesar titulo sem remover esse
    boilerplate amplificaria texto que o PARSER gerou, nao a obra."""

    def test_structural_boilerplate_is_dropped(self):
        stream = tokenize_heading("Chapter 5")
        self.assertEqual(stream.terms, ())

    def test_bare_digits_are_dropped_in_a_heading(self):
        stream = tokenize_heading("Chapter 101")
        self.assertEqual(stream.terms, ())

    def test_a_real_title_still_yields_terms(self):
        stream = tokenize_heading("Chapter 23 - Radioatividade e origem dos elementos")
        self.assertIn("radioatividade", stream.terms)
        self.assertIn("elemento", stream.terms)
        self.assertNotIn("chapter", stream.terms)

    def test_body_keeps_the_word_capitulo(self):
        """A stoplist de titulo e ESTRUTURAL e nao vale para o corpo: no corpo
        ``capitulo`` e vocabulario da obra."""
        self.assertIn("capitulo", tokenize("neste capitulo veremos").terms)


class HeadingPositionSpaceTests(unittest.TestCase):
    """Corpo e titulo NAO podem compartilhar o espaco de posicoes.

    Bug encontrado durante a implementacao: ambos comecavam em 0, e uma
    expressao casava usando um termo do corpo e outro do titulo - frase que
    nao existe em lugar nenhum. O deslocamento resolve por construcao.
    """

    def test_heading_positions_are_displaced(self):
        stream = tokenize_heading("Chapter 1 - Radioatividade")
        self.assertEqual(stream.positions["radioatividade"], (HEADING_POSITION_BASE + 2,))

    def test_the_displacement_can_be_switched_off_for_unit_testing(self):
        stream = tokenize_heading("Chapter 1 - Radioatividade", offset=0)
        self.assertEqual(stream.positions["radioatividade"], (2,))

    def test_a_phrase_cannot_match_across_the_two_fields(self):
        body = tokenize("a concentracao e importante")
        heading = tokenize_heading("Chapter 4 - Solucoes")
        merged = TokenStream(
            terms=body.terms + heading.terms,
            positions={**body.positions, **heading.positions},
            frequencies={**body.frequencies, **heading.frequencies},
            token_count=body.token_count + heading.token_count,
            stream_length=0,
            dropped=(),
        )
        query = tokenize("concentração das soluções")
        self.assertEqual(find_phrase_occurrences(merged, query, slack=1), ())

    def test_proximity_also_does_not_cross_the_fields(self):
        body = tokenize("a concentracao e importante")
        heading = tokenize_heading("Chapter 4 - Solucoes")
        merged = TokenStream(
            terms=body.terms + heading.terms,
            positions={**body.positions, **heading.positions},
            frequencies={},
            token_count=0,
            stream_length=0,
            dropped=(),
        )
        span = best_proximity_span(merged, ("concentracao", "solucao"))
        self.assertGreater(span, POLICY.proximity_window)


class PhraseOccurrenceTests(unittest.TestCase):
    """Com posicoes integras, a frase da consulta diz quantas palavras de
    funcao esperar; a politica da a folga."""

    def test_adjacent_terms_match(self):
        doc = tokenize("o reagente limitante determina o rendimento")
        query = tokenize("reagente limitante")
        hits = find_phrase_occurrences(doc, query, slack=POLICY.phrase_slack)
        self.assertEqual(len(hits), 1)

    def test_a_query_with_a_stopword_matches_the_same_shape_in_the_document(self):
        doc = tokenize("a concentração das soluções aquosas")
        query = tokenize("concentração das soluções")
        self.assertEqual(len(find_phrase_occurrences(doc, query, slack=1)), 1)

    def test_the_slack_absorbs_one_extra_function_word(self):
        doc = tokenize("a concentração de uma solução aquosa")
        query = tokenize("concentração das soluções")
        self.assertEqual(len(find_phrase_occurrences(doc, query, slack=1)), 1)

    def test_terms_far_apart_are_not_a_phrase(self):
        doc = tokenize(
            "a concentração importa muito para o quimico que estuda as soluções"
        )
        query = tokenize("concentração das soluções")
        self.assertEqual(find_phrase_occurrences(doc, query, slack=1), ())

    def test_inverted_order_is_not_a_phrase(self):
        doc = tokenize("limitante reagente")
        query = tokenize("reagente limitante")
        self.assertEqual(find_phrase_occurrences(doc, query, slack=1), ())

    def test_a_single_term_query_has_no_phrase(self):
        doc = tokenize("mol e a unidade")
        self.assertEqual(find_phrase_occurrences(doc, tokenize("mol"), slack=1), ())

    def test_every_occurrence_is_reported(self):
        doc = tokenize("reagente limitante aqui e reagente limitante ali")
        hits = find_phrase_occurrences(doc, tokenize("reagente limitante"), slack=1)
        self.assertEqual(len(hits), 2)


class ProximityTests(unittest.TestCase):
    def test_the_span_is_the_smallest_window_holding_every_term(self):
        """A janela e contada sobre posicoes INTEGRAS, nao sobre postings: em
        "concentração da solucao" o ``da`` ocupa a posicao do meio, logo a
        janela e 3 e nao 2. E a mesma semantica do ajuste 2 chegando ao
        caminho de proximidade."""
        doc = tokenize("solucao aqui e mais adiante a concentração da solucao")
        self.assertEqual(doc.positions["concentracao"], (6,))
        self.assertEqual(doc.positions["solucao"], (0, 8))
        self.assertEqual(best_proximity_span(doc, ("concentracao", "solucao")), 3)

    def test_without_an_intervening_stopword_the_span_is_two(self):
        doc = tokenize("a concentração solucao aquosa")
        self.assertEqual(best_proximity_span(doc, ("concentracao", "solucao")), 2)

    def test_a_missing_term_has_no_span(self):
        doc = tokenize("apenas concentração")
        self.assertIsNone(best_proximity_span(doc, ("concentracao", "solucao")))

    def test_a_single_term_has_span_one(self):
        doc = tokenize("mol de mol")
        self.assertEqual(best_proximity_span(doc, ("mol",)), 1)


if __name__ == "__main__":
    unittest.main()
