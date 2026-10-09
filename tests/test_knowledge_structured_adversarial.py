"""CEREBRO - tentativas de burlar a ancora por span.

A pergunta nao e "funciona?", e "da para passar sem suporte real?".

Span verificado prova que o trecho EXISTE naquele chunk. Nao prova que
sustenta a afirmacao. Entao os vetores de fuga previsiveis - span
generico, tudo META, derivacao nao mecanizavel - continuam abertos por
construcao. Estes testes nao os fecham: eles os REGISTRAM e MEDEM, para
que a decisao sobre cada um seja deliberada e baseada em dado.

Nenhuma chamada paga.
"""

from __future__ import annotations

import json
import unittest

from agente_ia_edu.services.knowledge_engine.context_builder import (
    ContextBuilder,
)
from agente_ia_edu.services.knowledge_engine.span_verification import (
    SPAN_CASE_MISMATCH,
    SPAN_NOT_FOUND,
    SPAN_VERIFIED,
    verify_span,
)
from agente_ia_edu.services.knowledge_engine.structured_answer import (
    DERIVATION_NOT_MECHANIZED,
    STRUCTURED_CONTRACT_VIOLATION,
    StructuredAnswerer,
    evaluate_expression,
)

from test_knowledge_context_builder import _hit, _textos
from test_knowledge_grounded_answer import _Roteiro
from test_knowledge_structured_answer import TEXTOS, _ctx, _factual


async def _resp(payload, contexto=None):
    return await StructuredAnswerer(provider=_Roteiro(payload)).answer(
        "pergunta", contexto if contexto is not None else _ctx())


class GenericSpanEscapeTests(unittest.IsolatedAsyncioTestCase):
    """VETOR DE FUGA 1: quotar algo trivial que existe em qualquer lugar."""

    async def test_characterises_a_single_common_word_as_span(self):
        """``de`` existe em quase todo chunk e passa a verificacao.

        O span prova existencia, nao suporte. Registrado para que o
        tamanho minimo seja decidido com dado, nao por palpite.
        """
        r = await _resp({"claims": [
            _factual("Qualquer afirmação inventada.",
                     [("E1", "de", "span trivial")])
        ], "sufficient": True})
        self.assertEqual(r.claims[0].support[0].status, SPAN_VERIFIED)
        self.assertTrue(r.deliverable)

    async def test_the_span_length_is_recorded_so_it_can_be_measured(self):
        r = await _resp({"claims": [
            _factual("x", [("E1", "de", "trivial")])
        ], "sufficient": True})
        s = r.admin_payload()["claims"][0]["support"][0]
        self.assertEqual(s["span_normalized"], "de")
        self.assertEqual(len(s["span_normalized"]), 2)


class MetaEscapeTests(unittest.IsolatedAsyncioTestCase):
    """VETOR DE FUGA 2: marcar conteudo factual como META."""

    async def test_characterises_factual_content_disguised_as_meta(self):
        r = await _resp({"claims": [
            {"kind": "META",
             "text": "A água ferve a 100 °C ao nível do mar."}
        ], "sufficient": True})
        self.assertTrue(r.deliverable)
        self.assertEqual(r.factual_count, 0)
        self.assertEqual(r.meta_count, 1)

    async def test_the_proportion_is_visible_for_measurement(self):
        r = await _resp({"claims": [
            {"kind": "META", "text": "Uma."},
            {"kind": "META", "text": "Duas."},
            _factual("Três.", [("E1", "quociente", "a")]),
        ], "sufficient": True})
        p = r.admin_payload()
        self.assertEqual((p["meta_count"], p["factual_count"]), (2, 1))


class DerivationEscapeTests(unittest.IsolatedAsyncioTestCase):
    """VETOR DE FUGA 3: declarar derivacao nao mecanizavel."""

    async def test_characterises_an_unmechanisable_derivation_passing(self):
        r = await _resp({"claims": [{
            "kind": "FACTUAL", "text": "Logo, o valor dobra.",
            "derivation": {
                "expression": "por analogia", "result": "dobro",
                "inputs": [{"evidence": "E1", "span": "quociente",
                            "role": "base"}],
            },
        }], "sufficient": True})
        self.assertEqual(r.claims[0].derivation.status,
                         DERIVATION_NOT_MECHANIZED)
        self.assertTrue(r.claims[0].verified)
        self.assertTrue(r.claims[0].needs_human_review)
        self.assertTrue(r.needs_human_review)

    async def test_a_derivation_with_no_inputs_is_a_contract_violation(self):
        r = await _resp({"claims": [{
            "kind": "FACTUAL", "text": "Vale 2.",
            "derivation": {"expression": "1+1", "result": "2", "inputs": []},
        }], "sufficient": True})
        self.assertEqual(r.status, STRUCTURED_CONTRACT_VIOLATION)


class MalformedPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_support_that_is_not_a_list(self):
        r = await _resp({"claims": [
            {"kind": "FACTUAL", "text": "x", "support": "E1"}
        ], "sufficient": True})
        self.assertEqual(r.status, STRUCTURED_CONTRACT_VIOLATION)

    async def test_a_support_entry_that_is_not_an_object(self):
        r = await _resp({"claims": [
            {"kind": "FACTUAL", "text": "x", "support": ["E1"]}
        ], "sufficient": True})
        self.assertEqual(r.status, STRUCTURED_CONTRACT_VIOLATION)

    async def test_a_span_that_is_not_text(self):
        r = await _resp({"claims": [
            {"kind": "FACTUAL", "text": "x",
             "support": [{"evidence": "E1", "span": 42, "role": "r"}]}
        ], "sufficient": True})
        self.assertEqual(r.status, STRUCTURED_CONTRACT_VIOLATION)

    async def test_a_claim_that_is_not_an_object(self):
        r = await _resp({"claims": ["uma frase solta"], "sufficient": True})
        self.assertEqual(r.status, STRUCTURED_CONTRACT_VIOLATION)

    async def test_text_missing_or_blank(self):
        for ruim in (None, "", "   ", 7):
            with self.subTest(text=ruim):
                r = await _resp({"claims": [
                    {"kind": "FACTUAL", "text": ruim,
                     "support": [{"evidence": "E1", "span": "quociente",
                                  "role": "r"}]}
                ], "sufficient": True})
                self.assertEqual(r.status, STRUCTURED_CONTRACT_VIOLATION)

    async def test_derivation_that_is_not_an_object(self):
        r = await _resp({"claims": [
            {"kind": "FACTUAL", "text": "x", "derivation": "1+1"}
        ], "sufficient": True})
        self.assertEqual(r.status, STRUCTURED_CONTRACT_VIOLATION)

    async def test_a_hundred_claims_do_not_crash(self):
        r = await _resp({"claims": [
            _factual(f"Frase {i}.", [("E1", "quociente", "a")])
            for i in range(100)
        ], "sufficient": True})
        self.assertEqual(len(r.claims), 100)
        self.assertTrue(r.deliverable)


class EvaluatorHardeningTests(unittest.TestCase):
    def test_it_never_executes_anything(self):
        perigosos = [
            "__import__('os').system('echo x')",
            "().__class__.__bases__[0].__subclasses__()",
            "globals()", "locals()", "exec('1')", "eval('1')",
            "[x for x in range(10)]", "{'a': 1}", "f'{1}'",
            "1 and 2", "not 1", "1 < 2", "x := 1",
        ]
        for expr in perigosos:
            with self.subTest(expr=expr):
                self.assertIsNone(evaluate_expression(expr))

    def test_power_is_outside_the_grammar(self):
        """Potencia abre caminho para negacao de servico por memoria."""
        self.assertIsNone(evaluate_expression("2**10"))
        self.assertIsNone(evaluate_expression("9**9**9"))

    def test_a_very_long_expression_does_not_hang(self):
        self.assertEqual(evaluate_expression("+".join(["1"] * 500)), 500)

    def test_non_string_input_is_none_not_an_exception(self):
        for ruim in (None, 7, [1], {"a": 1}):
            with self.subTest(v=ruim):
                self.assertIsNone(evaluate_expression(ruim))


class CrossChunkConfusionTests(unittest.IsolatedAsyncioTestCase):
    """O modo de falha dominante nos 9 adjudicados: ma atribuicao."""

    async def test_every_wrong_pairing_of_the_four_chunks_fails(self):
        """Cada texto so pertence ao seu chunk - varredura completa."""
        contexto = _ctx()
        trechos = {f"E{i}": TEXTOS[i][:28] for i in range(1, 5)}
        for marcador, trecho in trechos.items():
            for outro in trechos:
                with self.subTest(span_de=marcador, atribuido_a=outro):
                    status, _ = verify_span(
                        trecho, contexto.evidence_text(outro))
                    if marcador == outro:
                        self.assertEqual(status, SPAN_VERIFIED)
                    else:
                        self.assertIn(status,
                                      (SPAN_NOT_FOUND, SPAN_CASE_MISMATCH))

    async def test_a_claim_citing_four_chunks_with_one_bad_span_fails(self):
        r = await _resp({"claims": [
            _factual("Afirmação ampla.",
                     [("E1", "quociente", "ok"),
                      ("E2", "zinco e o anodo", "ok"),
                      ("E3", "lei de Hess", "ok"),
                      ("E4", "molho de salada", "INVENTADO")])
        ], "sufficient": True})
        estados = [s.status for s in r.claims[0].support]
        self.assertEqual(estados.count(SPAN_VERIFIED), 3)
        self.assertIn(SPAN_NOT_FOUND, estados)
        self.assertFalse(r.deliverable)


class RightsTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_evidence_literal_stays_out_of_repr(self):
        hits = [_hit(rank=1, rights="COMMERCIAL_REFERENCE",
                     raw_text="SEGREDO COMERCIAL com quociente")]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        self.assertNotIn("SEGREDO COMERCIAL", repr(ctx))
        r = await _resp({"claims": [
            _factual("x", [("E1", "quociente", "a")])
        ], "sufficient": True}, ctx)
        self.assertNotIn("SEGREDO COMERCIAL", repr(r))

    async def test_only_the_declared_span_reaches_the_admin_payload(self):
        """O span vai ao ADMIN por desenho - sem ele a verificacao nao e
        auditavel. O RESTO do literal nao vai."""
        hits = [_hit(rank=1, rights="COMMERCIAL_REFERENCE",
                     raw_text="SEGREDO COMERCIAL com quociente dentro")]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        r = await _resp({"claims": [
            _factual("x", [("E1", "quociente", "a")])
        ], "sufficient": True}, ctx)
        blob = json.dumps(r.admin_payload(), ensure_ascii=False)
        self.assertIn("quociente", blob)
        self.assertNotIn("SEGREDO COMERCIAL", blob)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
