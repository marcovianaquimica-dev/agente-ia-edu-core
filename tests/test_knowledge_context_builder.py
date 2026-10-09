"""CEREBRO - o ContextBuilder: o que vai para o prompt e o que sai em publico.

A PECA ONDE A POLITICA DE DIREITOS PODE VAZAR
=============================================

Ela e a unica camada que precisa do literal de obra comercial E produz um
artefato que alguem vai ler. Confundir as duas coisas e o vazamento, e por
isso o desenho separa fisicamente:

    payload do prompt   tem o literal, vai ao provider, nao e serializavel
    representacao publica   nao tem literal comercial, e o que a CLI imprime

Os testes abaixo tratam esse par como a propriedade central: nao basta a
saida publica estar limpa num caminho feliz - ``repr``, serializacao e log
tem de estar limpos tambem.
"""

from __future__ import annotations

import json
import unittest
import uuid

from agente_ia_edu.services.knowledge_engine.context_builder import (
    DEFAULT_BUDGET_CHARS,
    BUDGET_EXHAUSTED,
    MAX_EVIDENCES_REACHED,
    RIGHTS_NO_PROVIDER,
    ContextBuilder,
)


def _hit(
    *,
    rank,
    rights="OWN",
    raw_text="Texto da evidencia.",
    titulo="Apostila propria",
    page=10,
    chunk_type="PROSE",
    role="CONTENT",
):
    """Dublê de ``VectorHit`` com o minimo que o construtor consome."""

    class _H:
        pass

    h = _H()
    h.chunk_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"chunk{rank}")
    h.text_hash = f"{rank:064d}"
    h.rank = rank
    h.score = 1.0 - rank / 100
    h.distance = rank / 100
    h.chunk_type = chunk_type
    h.editorial_role = role
    h.heading_path = ("Cap 1", "Secao")
    h.page_start = page
    h.page_end = page
    h.source_id = uuid.uuid5(uuid.NAMESPACE_DNS, titulo)
    h.source_title = titulo
    h.document_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"doc{titulo}")
    h.document_filename = "obra.pdf"
    h.rights_class = rights
    h.quotable = rights != "COMMERCIAL_REFERENCE"
    h.bncc_node_codes = ()
    h.char_count = len(raw_text)
    h._raw_text = raw_text
    return h


def _textos(hits):
    return {h.chunk_id: h._raw_text for h in hits}


class MarkerTests(unittest.TestCase):
    def test_markers_are_sequential_from_one(self):
        hits = [_hit(rank=r) for r in range(1, 6)]
        contexto = ContextBuilder().build(hits, texts=_textos(hits))
        self.assertEqual(
            [e.marker for e in contexto.evidences],
            ["E1", "E2", "E3", "E4", "E5"],
        )

    def test_selection_follows_rank_order_deterministically(self):
        hits = [_hit(rank=r) for r in range(1, 6)]
        um = ContextBuilder().build(hits, texts=_textos(hits))
        dois = ContextBuilder().build(hits, texts=_textos(hits))
        self.assertEqual(
            [e.chunk_id for e in um.evidences],
            [e.chunk_id for e in dois.evidences],
        )
        self.assertEqual(
            [e.chunk_id for e in um.evidences], [h.chunk_id for h in hits]
        )


class BudgetTests(unittest.TestCase):
    def test_the_budget_cuts_and_says_why(self):
        hits = [_hit(rank=r, raw_text="x" * 400) for r in range(1, 11)]
        contexto = ContextBuilder(budget_chars=1000).build(
            hits, texts=_textos(hits)
        )
        self.assertLess(len(contexto.evidences), 10)
        self.assertTrue(contexto.excluded)
        self.assertEqual(
            {e["reason"] for e in contexto.excluded}, {BUDGET_EXHAUSTED}
        )
        self.assertLessEqual(contexto.used_chars, 1000)

    def test_every_hit_is_either_included_or_excluded_with_a_reason(self):
        """Nenhuma evidencia some em silencio - a soma tem de fechar."""
        hits = [_hit(rank=r, raw_text="y" * 300) for r in range(1, 9)]
        contexto = ContextBuilder(budget_chars=700).build(
            hits, texts=_textos(hits)
        )
        self.assertEqual(
            len(contexto.evidences) + len(contexto.excluded), len(hits)
        )
        for excluido in contexto.excluded:
            self.assertIn("reason", excluido)
            self.assertIn("chunk_id", excluido)
            self.assertIn("rank", excluido)

    def test_the_cap_on_evidence_count_is_separate_from_the_budget(self):
        hits = [_hit(rank=r, raw_text="z" * 10) for r in range(1, 21)]
        contexto = ContextBuilder(max_evidences=5).build(
            hits, texts=_textos(hits)
        )
        self.assertEqual(len(contexto.evidences), 5)
        self.assertEqual(
            {e["reason"] for e in contexto.excluded}, {MAX_EVIDENCES_REACHED}
        )

    def test_a_single_oversized_evidence_still_enters_alone(self):
        """Cortar a primeira evidencia por tamanho deixaria o contexto vazio
        com resultado existente - pior que um contexto grande."""
        hits = [_hit(rank=1, raw_text="w" * 5000)]
        contexto = ContextBuilder(budget_chars=100).build(
            hits, texts=_textos(hits)
        )
        self.assertEqual(len(contexto.evidences), 1)

    def test_the_default_budget_is_declared(self):
        self.assertGreater(DEFAULT_BUDGET_CHARS, 0)


class RightsTests(unittest.TestCase):
    LITERAL = "ESTE E O LITERAL PROTEGIDO DA OBRA COMERCIAL XYZ"

    def _contexto(self):
        hits = [
            _hit(rank=1, rights="COMMERCIAL_REFERENCE",
                 raw_text=self.LITERAL, titulo="Livro comercial"),
            _hit(rank=2, rights="OWN", raw_text="Texto proprio citavel.",
                 titulo="Apostila propria"),
        ]
        return ContextBuilder().build(hits, texts=_textos(hits)), hits

    def test_the_prompt_payload_does_carry_the_commercial_literal(self):
        """E preciso: sem o texto o modelo nao tem o que fundamentar. O
        envio ao provider e decisao explicita da politica do piloto."""
        contexto, _ = self._contexto()
        self.assertIn(self.LITERAL, contexto.prompt_payload())

    def test_the_public_representation_never_carries_it(self):
        contexto, _ = self._contexto()
        blob = json.dumps(contexto.admin_payload(), default=str)
        self.assertNotIn(self.LITERAL, blob)
        self.assertNotIn(self.LITERAL[:20], blob)

    def test_repr_does_not_leak_it(self):
        """O vazamento mais facil de cometer: alguem imprime o objeto num
        log de diagnostico e o literal vai junto."""
        contexto, _ = self._contexto()
        self.assertNotIn(self.LITERAL[:20], repr(contexto))
        for evidencia in contexto.evidences:
            self.assertNotIn(self.LITERAL[:20], repr(evidencia))

    def test_the_public_evidence_has_no_raw_text_field_at_all(self):
        """Nao basta vir vazio: o campo nao existe, entao nao ha como
        alguem preenche-lo depois sem mudar o tipo."""
        contexto, _ = self._contexto()
        for evidencia in contexto.evidences:
            self.assertFalse(hasattr(evidencia, "raw_text"))

    def test_a_quotable_source_gets_an_excerpt_in_public(self):
        contexto, _ = self._contexto()
        propria = next(e for e in contexto.evidences if e.quotable)
        self.assertEqual(propria.excerpt, "Texto proprio citavel.")

    def test_a_commercial_source_gets_no_excerpt(self):
        contexto, _ = self._contexto()
        comercial = next(e for e in contexto.evidences if not e.quotable)
        self.assertIsNone(comercial.excerpt)

    def test_traceability_survives_for_the_commercial_source(self):
        contexto, hits = self._contexto()
        comercial = next(e for e in contexto.evidences if not e.quotable)
        self.assertEqual(comercial.chunk_id, hits[0].chunk_id)
        self.assertEqual(comercial.text_hash, hits[0].text_hash)
        self.assertEqual(comercial.source_id, hits[0].source_id)
        self.assertEqual(comercial.document_id, hits[0].document_id)
        self.assertEqual(comercial.page_start, hits[0].page_start)
        self.assertEqual(comercial.source_title, "Livro comercial")

    def test_a_source_that_may_not_reach_the_provider_is_excluded(self):
        """Hoje nenhuma classe recusa o envio, entao o caso se testa com
        uma classe DESCONHECIDA - que a politica fecha por omissao."""
        hits = [_hit(rank=1, rights="LICENCA_FUTURA_DESCONHECIDA",
                     raw_text="texto restrito")]
        contexto = ContextBuilder().build(hits, texts=_textos(hits))
        self.assertEqual(contexto.evidences, ())
        self.assertEqual(contexto.excluded[0]["reason"], RIGHTS_NO_PROVIDER)
        self.assertNotIn("texto restrito", contexto.prompt_payload())


class PromptTests(unittest.TestCase):
    def test_the_prompt_numbers_the_evidence_with_the_markers(self):
        hits = [_hit(rank=r, raw_text=f"conteudo {r}") for r in (1, 2)]
        contexto = ContextBuilder().build(hits, texts=_textos(hits))
        payload = contexto.prompt_payload()
        self.assertIn("[E1]", payload)
        self.assertIn("[E2]", payload)
        self.assertIn("conteudo 1", payload)

    def test_the_prompt_carries_page_and_source_for_each_evidence(self):
        hits = [_hit(rank=1, page=212, titulo="Obra X")]
        contexto = ContextBuilder().build(hits, texts=_textos(hits))
        payload = contexto.prompt_payload()
        self.assertIn("212", payload)
        self.assertIn("Obra X", payload)

    def test_an_empty_hit_list_builds_an_empty_context(self):
        contexto = ContextBuilder().build([], texts={})
        self.assertEqual(contexto.evidences, ())
        self.assertEqual(contexto.excluded, ())
        self.assertEqual(contexto.used_chars, 0)


if __name__ == "__main__":
    unittest.main()
