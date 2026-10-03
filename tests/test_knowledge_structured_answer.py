"""CEREBRO - resposta estruturada: a afirmacao e a unidade de suporte.

O CAMINHO ATUAL CONTINUA INTACTO
================================

``GroundedAnswerer`` e ``INSTRUCAO`` nao sao tocados. Este e um SEGUNDO
caminho, com prompt proprio, para rodar em paralelo sobre o MESMO
contexto e permitir comparacao sem confundir mudanca de contrato com
variacao de recuperacao.

CONTRIBUICAO COMPLEMENTAR, NAO CONJUNTIVA ESTRITA
=================================================

A unidade de suporte e a AFIRMACAO. Varias evidencias podem sustenta-la
em conjunto - uma traz o metodo, outra os insumos. O que nao se admite e
evidencia citada SEM contribuicao declarada e verificavel: uma boa nao
lava uma ruim.

A RESPOSTA E MONTADA PELO SISTEMA
=================================

``answer_text`` e construido deterministicamente a partir dos
``claim.text`` validados. Nao existe texto entregue separado do texto
verificado, entao nao existe deriva entre os dois - e o escopo do
marcador deixa de ser adivinhado, porque cada afirmacao traz o seu.

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
    SPAN_EMPTY,
    SPAN_NOT_FOUND,
    SPAN_VERIFIED,
)
from agente_ia_edu.services.knowledge_engine.structured_answer import (
    CLAIM_CONNECTIVE,
    CLAIM_FACTUAL,
    CLAIM_META,
    DERIVATION_MISMATCH,
    DERIVATION_NOT_MECHANIZED,
    DERIVATION_VERIFIED,
    EVIDENCE_WITHOUT_CONTRIBUTION,
    STRUCTURED_CONTRACT_VIOLATION,
    StructuredAnswerer,
    evaluate_expression,
)

from test_knowledge_context_builder import _hit, _textos
from test_knowledge_grounded_answer import _Roteiro

#: Textos controlados: cada evidencia diz uma coisa distinta, para que
#: "span da evidencia errada" seja testavel.
TEXTOS = {
    1: "A concentracao e o quociente entre a quantidade de materia e o volume.",
    2: "O zinco e o anodo e o cobre e o catodo na pilha de Daniell.",
    3: "A lei de Hess permite somar entalpias de etapas intermediarias.",
    4: "Entalpias de combustao: carbono -394 kJ/mol, hidrogenio -286 kJ/mol, "
       "metano -891 kJ/mol.",
}


def _ctx(n=4):
    hits = [_hit(rank=r, raw_text=TEXTOS[r]) for r in range(1, n + 1)]
    return ContextBuilder().build(hits, texts=_textos(hits))


async def _responder(payload, contexto=None, **kwargs):
    return await StructuredAnswerer(provider=_Roteiro(payload)).answer(
        "pergunta", contexto if contexto is not None else _ctx(), **kwargs)


def _factual(texto, apoios):
    return {"kind": "FACTUAL", "text": texto,
            "support": [{"evidence": e, "span": s, "role": p}
                        for e, s, p in apoios]}


class HappyPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_single_supported_claim_is_verified_and_delivered(self):
        r = await _responder({"claims": [
            _factual("A concentração é massa por volume.",
                     [("E1", "quociente entre a quantidade de materia",
                       "define a grandeza")])
        ], "sufficient": True})
        self.assertTrue(r.deliverable)
        self.assertEqual(len(r.claims), 1)
        self.assertTrue(r.claims[0].verified)
        self.assertEqual(r.claims[0].support[0].status, SPAN_VERIFIED)

    async def test_the_answer_is_assembled_from_the_claim_texts(self):
        r = await _responder({"claims": [
            _factual("Primeira.", [("E1", "quociente", "a")]),
            _factual("Segunda.", [("E2", "o zinco e o anodo", "b")]),
        ], "sufficient": True})
        self.assertEqual(r.answer_text, "Primeira. Segunda.")

    async def test_the_assembled_text_carries_no_marker(self):
        """Nao ha o que remover: a evidencia vive em campo, nao no texto."""
        r = await _responder({"claims": [
            _factual("Uma afirmação.", [("E1", "quociente", "a")])
        ], "sufficient": True})
        self.assertNotRegex(r.answer_text, r"\[E\d+\]")


class ComplementaryEvidenceTests(unittest.IsolatedAsyncioTestCase):
    """A decisao aprovada: complementar sim, sem contribuicao nao."""

    async def test_two_evidences_each_with_its_own_span_are_accepted(self):
        """O caso V-N8: uma traz o metodo, outra os insumos."""
        r = await _responder({"claims": [
            _factual("A entalpia de formação é −75 kJ/mol.",
                     [("E3", "lei de Hess", "nomeia o método"),
                      ("E4", "-394 kJ/mol", "insumo da soma")])
        ], "sufficient": True})
        self.assertTrue(r.claims[0].verified)
        self.assertTrue(r.deliverable)
        self.assertEqual(r.claims[0].evidences_without_contribution, ())

    async def test_an_evidence_without_any_span_is_named(self):
        payload = {"claims": [{
            "kind": "FACTUAL", "text": "Uma afirmação.",
            "support": [{"evidence": "E1", "span": "quociente", "role": "a"}],
            "evidence": ["E1", "E2"],
        }], "sufficient": True}
        r = await _responder(payload)
        self.assertIn("E2", r.claims[0].evidences_without_contribution)
        self.assertFalse(r.claims[0].verified)

    async def test_a_good_evidence_does_not_launder_a_bad_one(self):
        """O ponto da decisao: nao validar pela uniao."""
        r = await _responder({"claims": [
            _factual("Uma afirmação.",
                     [("E1", "quociente entre", "contribui"),
                      ("E2", "lei de Hess", "NAO esta em E2")])
        ], "sufficient": True})
        apoios = {s.evidence_marker: s.status for s in r.claims[0].support}
        self.assertEqual(apoios["E1"], SPAN_VERIFIED)
        self.assertEqual(apoios["E2"], SPAN_NOT_FOUND)
        self.assertFalse(r.claims[0].verified)
        self.assertFalse(r.deliverable)


class WrongEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_span_quoted_from_another_chunk_fails(self):
        """O ataque ao modo dominante: ma atribuicao."""
        r = await _responder({"claims": [
            _factual("O zinco é o ânodo.",
                     [("E1", "o zinco e o anodo", "atribuida ao chunk errado")])
        ], "sufficient": True})
        self.assertEqual(r.claims[0].support[0].status, SPAN_NOT_FOUND)
        self.assertFalse(r.deliverable)

    async def test_the_same_span_on_the_right_chunk_passes(self):
        r = await _responder({"claims": [
            _factual("O zinco é o ânodo.",
                     [("E2", "zinco e o anodo", "correta")])
        ], "sufficient": True})
        self.assertEqual(r.claims[0].support[0].status, SPAN_VERIFIED)
        self.assertTrue(r.deliverable)

    async def test_an_unknown_marker_is_a_contract_violation(self):
        r = await _responder({"claims": [
            _factual("x", [("E9", "qualquer", "inexistente")])
        ], "sufficient": True})
        self.assertEqual(r.status, STRUCTURED_CONTRACT_VIOLATION)
        self.assertFalse(r.deliverable)


class SpanProblemTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_invented_span_is_not_found(self):
        r = await _responder({"claims": [
            _factual("Afirmação.", [("E1", "molho de salada", "inventado")])
        ], "sufficient": True})
        self.assertEqual(r.claims[0].support[0].status, SPAN_NOT_FOUND)

    async def test_an_empty_span_is_named(self):
        r = await _responder({"claims": [
            _factual("Afirmação.", [("E1", "   ", "vazio")])
        ], "sufficient": True})
        self.assertEqual(r.claims[0].support[0].status, SPAN_EMPTY)
        self.assertFalse(r.deliverable)

    async def test_a_case_mismatch_is_reported_and_not_accepted(self):
        r = await _responder({"claims": [
            _factual("Afirmação.", [("E1", "a concentracao", "caixa errada")])
        ], "sufficient": True})
        self.assertEqual(r.claims[0].support[0].status, SPAN_CASE_MISMATCH)
        self.assertFalse(r.claims[0].verified)
        self.assertFalse(r.deliverable)

    async def test_the_raw_and_normalised_span_are_both_recorded(self):
        r = await _responder({"claims": [
            _factual("x", [("E1", "quociente  entre", "espaco duplo")])
        ], "sufficient": True})
        s = r.claims[0].support[0]
        self.assertEqual(s.span_text, "quociente  entre")
        self.assertEqual(s.span_normalized, "quociente entre")


class ClaimKindTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_meta_claim_needs_no_support(self):
        r = await _responder({"claims": [
            {"kind": "META", "text": "As evidências não cobrem sp³."}
        ], "sufficient": False})
        self.assertEqual(r.claims[0].kind, CLAIM_META)
        self.assertTrue(r.claims[0].verified)

    async def test_a_connective_claim_needs_no_support(self):
        r = await _responder({"claims": [
            _factual("Base.", [("E1", "quociente", "a")]),
            {"kind": "CONNECTIVE", "text": "Portanto, segue."},
        ], "sufficient": True})
        self.assertEqual(r.claims[1].kind, CLAIM_CONNECTIVE)
        self.assertTrue(r.deliverable)

    async def test_a_factual_claim_without_support_violates_the_contract(self):
        r = await _responder({"claims": [
            {"kind": "FACTUAL", "text": "Sem suporte nenhum."}
        ], "sufficient": True})
        self.assertEqual(r.status, STRUCTURED_CONTRACT_VIOLATION)
        self.assertFalse(r.deliverable)

    async def test_an_unknown_kind_violates_the_contract(self):
        r = await _responder({"claims": [
            {"kind": "OPINIAO", "text": "acho que sim"}
        ], "sufficient": True})
        self.assertEqual(r.status, STRUCTURED_CONTRACT_VIOLATION)

    async def test_a_marker_inside_the_claim_text_violates_the_contract(self):
        """A evidencia vive em CAMPO. Marcador no texto seria o contrato
        antigo entrando pela janela."""
        r = await _responder({"claims": [
            _factual("Afirmação [E1].", [("E1", "quociente", "a")])
        ], "sufficient": True})
        self.assertEqual(r.status, STRUCTURED_CONTRACT_VIOLATION)

    async def test_characterises_an_answer_made_only_of_meta(self):
        """VETOR DE FUGA: marcar tudo META dispensa todo span.

        Nao e fechavel por substring. Fica registrado e medido - o
        payload ADMIN mostra a proporcao.
        """
        r = await _responder({"claims": [
            {"kind": "META", "text": "Uma observação sobre o material."},
            {"kind": "META", "text": "Outra observação."},
        ], "sufficient": True})
        self.assertTrue(r.deliverable)
        self.assertEqual(r.factual_count, 0)
        self.assertEqual(r.meta_count, 2)


class DerivationTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_n8_derivation_is_verified(self):
        r = await _responder({"claims": [{
            "kind": "FACTUAL",
            "text": "A entalpia de formação do metano é −75 kJ/mol.",
            "derivation": {
                "expression": "(-394 + 2*(-286)) - (-891)",
                "result": "-75", "unit": "kJ/mol",
                "inputs": [
                    {"evidence": "E4", "span": "-394 kJ/mol", "role": "carbono"},
                    {"evidence": "E4", "span": "-286 kJ/mol", "role": "hidrogênio"},
                    {"evidence": "E4", "span": "-891 kJ/mol", "role": "metano"},
                    {"evidence": "E3", "span": "lei de Hess", "role": "método"},
                ],
            },
        }], "sufficient": True})
        self.assertEqual(r.claims[0].derivation.status, DERIVATION_VERIFIED)
        self.assertTrue(r.claims[0].verified)
        self.assertTrue(r.deliverable)

    async def test_a_wrong_result_is_a_mismatch(self):
        r = await _responder({"claims": [{
            "kind": "FACTUAL", "text": "Vale −99 kJ/mol.",
            "derivation": {
                "expression": "(-394 + 2*(-286)) - (-891)", "result": "-99",
                "inputs": [{"evidence": "E4", "span": "-394 kJ/mol",
                            "role": "x"}],
            },
        }], "sufficient": True})
        self.assertEqual(r.claims[0].derivation.status, DERIVATION_MISMATCH)
        self.assertFalse(r.deliverable)

    async def test_an_input_that_is_not_in_its_chunk_fails(self):
        r = await _responder({"claims": [{
            "kind": "FACTUAL", "text": "Vale −75 kJ/mol.",
            "derivation": {
                "expression": "1+1", "result": "2",
                "inputs": [{"evidence": "E1", "span": "-394 kJ/mol",
                            "role": "nao esta em E1"}],
            },
        }], "sufficient": True})
        self.assertEqual(r.claims[0].derivation.inputs[0].status,
                         SPAN_NOT_FOUND)
        self.assertFalse(r.deliverable)

    async def test_an_expression_outside_the_grammar_is_not_rejected(self):
        """Nao mecanizavel vai para revisao humana, nao para o lixo."""
        r = await _responder({"claims": [{
            "kind": "FACTUAL", "text": "Segue por simetria do argumento.",
            "derivation": {
                "expression": "argumento de simetria", "result": "n/a",
                "inputs": [{"evidence": "E3", "span": "lei de Hess",
                            "role": "método"}],
            },
        }], "sufficient": True})
        self.assertEqual(r.claims[0].derivation.status,
                         DERIVATION_NOT_MECHANIZED)
        self.assertTrue(r.claims[0].needs_human_review)


class ExpressionEvaluatorTests(unittest.TestCase):
    """Sem ``eval``. Gramatica fechada."""

    def test_it_evaluates_the_four_operations(self):
        self.assertEqual(evaluate_expression("2+3"), 5)
        self.assertEqual(evaluate_expression("10-4"), 6)
        self.assertEqual(evaluate_expression("3*4"), 12)
        self.assertEqual(evaluate_expression("9/3"), 3)

    def test_it_handles_parentheses_and_unary_minus(self):
        self.assertEqual(evaluate_expression("(-394 + 2*(-286)) - (-891)"),
                         -75)

    def test_it_refuses_names_calls_and_imports(self):
        for perigoso in ("__import__('os')", "open('x')", "x + 1",
                         "len([1])", "1 if True else 2", "[1,2]",
                         "lambda: 1", "2**1000000"):
            with self.subTest(expr=perigoso):
                self.assertIsNone(evaluate_expression(perigoso))

    def test_it_returns_none_for_garbage_instead_of_raising(self):
        for lixo in ("", "   ", "argumento de simetria", "1 +", ")("):
            with self.subTest(expr=lixo):
                self.assertIsNone(evaluate_expression(lixo))

    def test_division_by_zero_is_none_not_an_exception(self):
        self.assertIsNone(evaluate_expression("1/0"))


class UnverifiedReasonTests(unittest.IsolatedAsyncioTestCase):
    """``verified = False`` sozinho obriga o ADMIN a reconstruir o motivo
    lendo span por span. O motivo tem de estar nomeado."""

    async def test_a_missing_span_names_its_reason(self):
        r = await _responder({"claims": [
            _factual("x", [("E1", "molho de salada", "inventado")])
        ], "sufficient": True})
        self.assertIn(SPAN_NOT_FOUND, r.claims[0].unverified_reasons)

    async def test_an_evidence_without_contribution_names_its_reason(self):
        r = await _responder({"claims": [{
            "kind": "FACTUAL", "text": "x",
            "support": [{"evidence": "E1", "span": "quociente", "role": "a"}],
            "evidence": ["E1", "E2"],
        }], "sufficient": True})
        self.assertIn(EVIDENCE_WITHOUT_CONTRIBUTION,
                      r.claims[0].unverified_reasons)

    async def test_a_case_mismatch_names_its_reason(self):
        r = await _responder({"claims": [
            _factual("x", [("E1", "a concentracao", "caixa")])
        ], "sufficient": True})
        self.assertIn(SPAN_CASE_MISMATCH, r.claims[0].unverified_reasons)

    async def test_a_verified_claim_has_no_reasons(self):
        r = await _responder({"claims": [
            _factual("x", [("E1", "quociente", "a")])
        ], "sufficient": True})
        self.assertEqual(r.claims[0].kind, CLAIM_FACTUAL)
        self.assertEqual(r.claims[0].unverified_reasons, ())


class DeliveryBlockReasonTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_clean_answer_has_no_block_reason(self):
        r = await _responder({"claims": [
            _factual("x", [("E1", "quociente", "a")])
        ], "sufficient": True})
        self.assertIsNone(r.delivery_block_reason)

    async def test_an_unverified_claim_blocks_with_its_own_reason(self):
        from agente_ia_edu.services.knowledge_engine.structured_answer import (
            STRUCTURED_UNVERIFIED_CLAIM,
        )

        r = await _responder({"claims": [
            _factual("x", [("E1", "molho de salada", "inventado")])
        ], "sufficient": True})
        self.assertEqual(r.delivery_block_reason, STRUCTURED_UNVERIFIED_CLAIM)

    async def test_denied_sufficiency_blocks_before_verification(self):
        """Precedencia: suficiencia antes de verificacao.

        O modelo declarou que o material nao basta; dizer "span invalido"
        descreveria o sintoma e esconderia a causa.
        """
        from agente_ia_edu.services.knowledge_engine.grounded_answer import (
            EVIDENCE_DECLARED_INSUFFICIENT,
        )

        r = await _responder({"claims": [
            _factual("x", [("E1", "molho de salada", "inventado")])
        ], "sufficient": False})
        self.assertEqual(r.delivery_block_reason,
                         EVIDENCE_DECLARED_INSUFFICIENT)

    async def test_a_contract_violation_blocks_before_everything(self):
        r = await _responder({"claims": [
            {"kind": "FACTUAL", "text": "sem suporte"}
        ], "sufficient": True})
        self.assertEqual(r.delivery_block_reason,
                         STRUCTURED_CONTRACT_VIOLATION)


class AdminInstrumentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_admin_payload_carries_everything_required(self):
        r = await _responder({"claims": [
            _factual("Afirmação.", [("E1", "quociente  entre", "define")]),
            {"kind": "META", "text": "Nota sobre as evidências."},
        ], "sufficient": True})
        p = r.admin_payload()
        for campo in ("claims", "answer_text", "deliverable", "status",
                      "factual_count", "meta_count", "connective_count",
                      "span_case_mismatches", "sufficiency"):
            self.assertIn(campo, p)
        c = p["claims"][0]
        for campo in ("kind", "text", "verified", "support",
                      "evidences_without_contribution"):
            self.assertIn(campo, c)
        s = c["support"][0]
        for campo in ("evidence_marker", "span_text", "span_normalized",
                      "role", "status", "normalization"):
            self.assertIn(campo, s)

    async def test_case_mismatches_are_counted_for_measurement(self):
        r = await _responder({"claims": [
            _factual("x", [("E1", "a concentracao", "caixa")])
        ], "sufficient": True})
        self.assertEqual(r.admin_payload()["span_case_mismatches"], 1)

    async def test_the_admin_payload_is_json_serialisable(self):
        r = await _responder({"claims": [
            _factual("x", [("E1", "quociente", "a")])
        ], "sufficient": True})
        json.dumps(r.admin_payload(), default=str)

    async def test_commercial_literal_does_not_reach_the_admin_payload(self):
        """O span E literal da obra - e o ADMIN precisa dele para auditar.

        Caracterizacao: ao contrario de ``excerpt``, o span VAI ao ADMIN,
        porque sem ele a verificacao nao e auditavel. E escolha de
        desenho, nao descuido.
        """
        hits = [_hit(rank=1, rights="COMMERCIAL_REFERENCE",
                     raw_text="texto comercial com quociente dentro")]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        r = await _responder({"claims": [
            _factual("x", [("E1", "quociente", "a")])
        ], "sufficient": True}, ctx)
        self.assertIn("quociente", json.dumps(r.admin_payload()))


class ParallelPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_old_prompt_is_untouched(self):
        from agente_ia_edu.services.knowledge_engine import (
            grounded_answer, structured_answer,
        )

        self.assertNotEqual(grounded_answer.INSTRUCAO,
                            structured_answer.INSTRUCAO_ESTRUTURADA)
        self.assertIn("used_evidence", grounded_answer.INSTRUCAO)
        self.assertIn("span", structured_answer.INSTRUCAO_ESTRUTURADA)

    async def test_both_answerers_accept_the_same_context_object(self):
        """Comparacao justa exige MESMO contexto, sem nova recuperacao."""
        from agente_ia_edu.services.knowledge_engine.grounded_answer import (
            GroundedAnswerer,
        )

        contexto = _ctx()
        antigo = await GroundedAnswerer(provider=_Roteiro(
            {"answer": "Resposta. [E1]", "used_evidence": ["E1"],
             "sufficient": True})).answer("pergunta", contexto)
        novo = await _responder({"claims": [
            _factual("Resposta.", [("E1", "quociente", "a")])
        ], "sufficient": True}, contexto)
        self.assertTrue(antigo.is_grounded)
        self.assertTrue(novo.deliverable)
        self.assertEqual(antigo.available_markers,
                         tuple(sorted(contexto.marker_set(),
                                      key=lambda m: int(m[1:]))))


class FailureModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_evidence_never_calls_the_provider(self):
        from agente_ia_edu.services.knowledge_engine.grounded_answer import (
            NO_EVIDENCE,
        )

        provider = _Roteiro({"claims": []})
        r = await StructuredAnswerer(provider=provider).answer(
            "p", ContextBuilder().build([], texts={}))
        self.assertEqual(r.status, NO_EVIDENCE)
        self.assertEqual(provider.prompts, [])

    async def test_a_non_json_response_is_named(self):
        from agente_ia_edu.services.knowledge_engine.grounded_answer import (
            PROVIDER_INVALID_RESPONSE,
        )

        r = await _responder(None)
        self.assertEqual(r.status, PROVIDER_INVALID_RESPONSE)

    async def test_claims_that_is_not_a_list_is_a_contract_violation(self):
        r = await _responder({"claims": "nao e lista", "sufficient": True})
        self.assertEqual(r.status, STRUCTURED_CONTRACT_VIOLATION)

    async def test_an_empty_claim_list_is_a_contract_violation(self):
        r = await _responder({"claims": [], "sufficient": True})
        self.assertEqual(r.status, STRUCTURED_CONTRACT_VIOLATION)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
