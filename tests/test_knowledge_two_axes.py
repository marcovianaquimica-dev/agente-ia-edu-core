"""CEREBRO - grounding e suficiencia sao DOIS eixos, nao um enum maior.

POR QUE NAO BASTAVA ACRESCENTAR UM VALOR AO STATUS
==================================================

A causa do problema era ter UM enum para DUAS perguntas. Acrescentar
``EVIDENCE_DECLARED_INSUFFICIENT`` a ``status`` repetiria o erro: a
resposta deixaria de ser ``GROUNDED`` justamente quando ela E fundamentada
- as citacoes conferem, o codigo verificou.

    grounding    a resposta esta apoiada no que citou?
                 responde o CODIGO, deterministicamente

    suficiencia  essas evidencias bastam?
                 responde o MODELO, e nao e verificavel

Colapsar as duas faz o verificavel herdar a confiabilidade do
nao-verificavel.

O QUE A MEDICAO REAL MOSTROU (Etapa A)
======================================

Em 9 execucoes reais, ``sufficient`` veio ``False`` duas vezes - N9 e N10
-, e nas duas estava CERTO. As duas sairam ``GROUNDED`` com
``is_grounded = True``: uma recusa explicita do modelo publicada como
resposta fundamentada.

Por isso ``DENIED`` bloqueia a entrega. E por isso toda ocorrencia e
marcada para revisao humana: duas observacoes nao fazem uma taxa de erro.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.providers.errors import ProviderTimeoutError
from agente_ia_edu.services.knowledge_engine.context_builder import (
    ContextBuilder,
)
from agente_ia_edu.services.knowledge_engine.grounded_answer import (
    ANSWER_WITHOUT_CITATION,
    DEGRADED_RETRIEVAL,
    EVIDENCE_DECLARED_INSUFFICIENT,
    GROUNDED,
    INVALID_EVIDENCE_REFERENCE,
    NO_EVIDENCE,
    PROVIDER_FAILED,
    PROVIDER_INVALID_RESPONSE,
    SANITIZATION_FAILED,
    SUFFICIENCY_AFFIRMED,
    SUFFICIENCY_DENIED,
    SUFFICIENCY_UNDECLARED,
    GroundedAnswerer,
)

from test_knowledge_context_builder import _hit, _textos
from test_knowledge_grounded_answer import _Roteiro


def _ctx(n=3):
    hits = [_hit(rank=r, raw_text=f"conteudo numero {r}")
            for r in range(1, n + 1)]
    return ContextBuilder().build(hits, texts=_textos(hits))


async def _resp(provider, contexto=None, **kwargs):
    return await GroundedAnswerer(provider=provider).answer(
        "pergunta qualquer", contexto if contexto is not None else _ctx(),
        **kwargs,
    )


class SufficiencyAxisTests(unittest.IsolatedAsyncioTestCase):
    async def test_true_becomes_affirmed(self):
        r = await _resp(_Roteiro({"answer": "x [E1]",
                                  "used_evidence": ["E1"],
                                  "sufficient": True}))
        self.assertEqual(r.sufficiency, SUFFICIENCY_AFFIRMED)

    async def test_false_becomes_denied(self):
        r = await _resp(_Roteiro({"answer": "x [E1]",
                                  "used_evidence": ["E1"],
                                  "sufficient": False}))
        self.assertEqual(r.sufficiency, SUFFICIENCY_DENIED)

    async def test_an_absent_field_is_undeclared(self):
        r = await _resp(_Roteiro({"answer": "x [E1]",
                                  "used_evidence": ["E1"]}))
        self.assertEqual(r.sufficiency, SUFFICIENCY_UNDECLARED)

    async def test_null_is_undeclared(self):
        r = await _resp(_Roteiro({"answer": "x [E1]",
                                  "used_evidence": ["E1"],
                                  "sufficient": None}))
        self.assertEqual(r.sufficiency, SUFFICIENCY_UNDECLARED)

    async def test_a_non_boolean_is_undeclared_not_guessed(self):
        """``"false"`` em string NAO vira ``DENIED``.

        Adivinhar intencao a partir de tipo errado e exatamente o que esta
        validacao existe para nao fazer. Um provider que manda string
        quando o contrato pede booleano esta fora do contrato, e o
        resultado honesto e "nao declarou".
        """
        for valor in ("false", "true", 0, 1, [], {}, "sim"):
            with self.subTest(valor=valor):
                r = await _resp(_Roteiro({"answer": "x [E1]",
                                          "used_evidence": ["E1"],
                                          "sufficient": valor}))
                self.assertEqual(r.sufficiency, SUFFICIENCY_UNDECLARED)

    async def test_the_four_terminal_states_are_undeclared(self):
        casos = {
            NO_EVIDENCE: dict(contexto=ContextBuilder().build([], texts={})),
            DEGRADED_RETRIEVAL: dict(retrieval_degraded=True),
        }
        for esperado, kwargs in casos.items():
            with self.subTest(status=esperado):
                r = await _resp(_Roteiro({"answer": "x", "used_evidence": []}),
                                **kwargs)
                self.assertEqual(r.status, esperado)
                self.assertEqual(r.sufficiency, SUFFICIENCY_UNDECLARED)

        r = await _resp(_Roteiro({}, erro=ProviderTimeoutError("x")))
        self.assertEqual(r.status, PROVIDER_FAILED)
        self.assertEqual(r.sufficiency, SUFFICIENCY_UNDECLARED)

        r = await _resp(_Roteiro(None, cru="nao e json"))
        self.assertEqual(r.status, PROVIDER_INVALID_RESPONSE)
        self.assertEqual(r.sufficiency, SUFFICIENCY_UNDECLARED)


class GroundingAxisTests(unittest.IsolatedAsyncioTestCase):
    async def test_grounding_mirrors_the_three_generation_outcomes(self):
        casos = [
            ({"answer": "x [E1]", "used_evidence": ["E1"]}, GROUNDED),
            ({"answer": "sem citar", "used_evidence": []},
             ANSWER_WITHOUT_CITATION),
            ({"answer": "x [E9]", "used_evidence": ["E9"]},
             INVALID_EVIDENCE_REFERENCE),
        ]
        for payload, esperado in casos:
            with self.subTest(esperado=esperado):
                r = await _resp(_Roteiro(payload))
                self.assertEqual(r.grounding, esperado)
                self.assertEqual(r.status, esperado)

    async def test_grounding_is_none_when_no_answer_was_produced(self):
        """Sem resposta nao ha o que fundamentar - e ``None`` diz isso
        melhor que qualquer valor do eixo."""
        r = await _resp(_Roteiro({}, erro=ProviderTimeoutError("x")))
        self.assertIsNone(r.grounding)

    async def test_is_grounded_keeps_its_old_meaning(self):
        """Mudar o sentido de ``is_grounded`` em silencio seria pior que
        acrescentar um campo: quem ja consome continua lendo grounding."""
        r = await _resp(_Roteiro({"answer": "x [E1]",
                                  "used_evidence": ["E1"],
                                  "sufficient": False}))
        self.assertTrue(r.is_grounded)
        self.assertEqual(r.grounding, GROUNDED)
        self.assertFalse(r.deliverable)


class DeliveryDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_grounded_and_affirmed_is_deliverable(self):
        r = await _resp(_Roteiro({"answer": "A resposta. [E1]",
                                  "used_evidence": ["E1"],
                                  "sufficient": True}))
        self.assertTrue(r.deliverable)
        self.assertIsNone(r.delivery_block_reason)

    async def test_grounded_and_undeclared_is_deliverable_but_flagged(self):
        r = await _resp(_Roteiro({"answer": "A resposta. [E1]",
                                  "used_evidence": ["E1"]}))
        self.assertTrue(r.deliverable)
        self.assertEqual(r.sufficiency, SUFFICIENCY_UNDECLARED)
        self.assertIsNone(r.delivery_block_reason)

    async def test_denied_blocks_delivery_without_denying_grounding(self):
        r = await _resp(_Roteiro({"answer": "A resposta. [E1]",
                                  "used_evidence": ["E1"],
                                  "sufficient": False}))
        self.assertFalse(r.deliverable)
        self.assertEqual(r.delivery_block_reason,
                         EVIDENCE_DECLARED_INSUFFICIENT)
        self.assertEqual(r.grounding, GROUNDED)

    async def test_denied_is_marked_for_human_review(self):
        """n=2 ocorrencias reais. Duas observacoes nao fazem uma taxa de
        erro, entao o portao e conservador E auditado."""
        r = await _resp(_Roteiro({"answer": "x [E1]",
                                  "used_evidence": ["E1"],
                                  "sufficient": False}))
        self.assertTrue(r.needs_human_review)

    async def test_an_affirmed_answer_is_not_marked_for_review(self):
        r = await _resp(_Roteiro({"answer": "x [E1]",
                                  "used_evidence": ["E1"],
                                  "sufficient": True}))
        self.assertFalse(r.needs_human_review)

    async def test_grounding_failure_blocks_whatever_sufficiency_says(self):
        for suf in (True, False, None):
            with self.subTest(sufficient=suf):
                r = await _resp(_Roteiro({"answer": "inventado [E9]",
                                          "used_evidence": ["E9"],
                                          "sufficient": suf}))
                self.assertFalse(r.deliverable)
                self.assertEqual(r.delivery_block_reason,
                                 INVALID_EVIDENCE_REFERENCE)

    async def test_the_four_terminal_states_are_never_deliverable(self):
        r = await _resp(_Roteiro({"answer": "x", "used_evidence": []}),
                        ContextBuilder().build([], texts={}))
        self.assertFalse(r.deliverable)
        self.assertEqual(r.delivery_block_reason, NO_EVIDENCE)

        r = await _resp(_Roteiro({}, erro=ProviderTimeoutError("x")))
        self.assertFalse(r.deliverable)
        self.assertEqual(r.delivery_block_reason, PROVIDER_FAILED)

    async def test_a_sanitization_artifact_blocks_delivery(self):
        """Entregar texto corrompido e pior que nao entregar."""
        r = await _resp(_Roteiro({"answer": "Segundo [E1], o valor sobe.",
                                  "used_evidence": ["E1"],
                                  "sufficient": True}))
        self.assertEqual(r.grounding, GROUNDED)
        self.assertTrue(r.stripping_artifacts)
        self.assertFalse(r.deliverable)
        self.assertEqual(r.delivery_block_reason, SANITIZATION_FAILED)

    async def test_the_block_reason_follows_a_declared_precedence(self):
        """grounding -> suficiencia -> sanitizacao.

        Com citacao invalida E suficiencia negada, o motivo e o de
        grounding: e o determinístico, e o que se pode afirmar.
        """
        r = await _resp(_Roteiro({"answer": "Segundo [E9], sobe.",
                                  "used_evidence": ["E9"],
                                  "sufficient": False}))
        self.assertEqual(r.delivery_block_reason, INVALID_EVIDENCE_REFERENCE)


class PublicTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_public_text_has_no_markers(self):
        r = await _resp(_Roteiro({"answer": "A concentração é n/V. [E1] [E2]",
                                  "used_evidence": ["E1", "E2"]}))
        self.assertEqual(r.answer, "A concentração é n/V. [E1] [E2]")
        self.assertEqual(r.answer_text_public, "A concentração é n/V.")

    async def test_the_original_is_preserved_untouched(self):
        """A remocao e DERIVADA. O original continua sendo a prova."""
        r = await _resp(_Roteiro({"answer": "x [E1]",
                                  "used_evidence": ["E1"]}))
        self.assertIn("[E1]", r.answer)

    async def test_the_public_text_exists_even_when_not_deliverable(self):
        """O ADMIN precisa ver o que teria sido mostrado."""
        r = await _resp(_Roteiro({"answer": "Recuso. [E1]",
                                  "used_evidence": ["E1"],
                                  "sufficient": False}))
        self.assertFalse(r.deliverable)
        self.assertEqual(r.answer_text_public, "Recuso.")


class RawAndNormalisedEvidenceTests(unittest.IsolatedAsyncioTestCase):
    """A divida de observabilidade registrada desde a primeira execucao
    real, e que a Etapa A so conseguiu medir com um gravador por fora."""

    async def test_both_shapes_are_recorded_side_by_side(self):
        r = await _resp(_Roteiro({"answer": "x [E1]",
                                  "used_evidence": ["[E1]"]}))
        self.assertEqual(r.raw_used_evidence, ["[E1]"])
        self.assertEqual(r.normalized_used_evidence, ("E1",))

    async def test_the_real_shape_from_etapa_a_is_visible(self):
        """N10 devolveu ``['[E3]', '[E4]']``. Sem o bruto, nao se sabe
        que a normalizacao atuou."""
        r = await _resp(_Roteiro({"answer": "x [E3] [E4]",
                                  "used_evidence": ["[E3]", "[E4]"]}),
                        _ctx(5))
        self.assertEqual(r.raw_used_evidence, ["[E3]", "[E4]"])
        self.assertEqual(r.normalized_used_evidence, ("E3", "E4"))

    async def test_a_raw_value_that_is_not_a_list_is_still_recorded(self):
        r = await _resp(_Roteiro({"answer": "x", "used_evidence": "E1"}))
        self.assertEqual(r.raw_used_evidence, "E1")
        self.assertEqual(r.normalized_used_evidence, ())

    async def test_an_absent_field_records_none_not_an_empty_list(self):
        """Ausente e vazio sao coisas diferentes, e o relatorio precisa
        distinguir."""
        r = await _resp(_Roteiro({"answer": "x [E1]"}))
        self.assertIsNone(r.raw_used_evidence)

    async def test_both_reach_the_admin_payload(self):
        r = await _resp(_Roteiro({"answer": "x [E1]",
                                  "used_evidence": ["[E1]"]}))
        p = r.admin_payload()
        self.assertEqual(p["raw_used_evidence"], ["[E1]"])
        self.assertEqual(p["normalized_used_evidence"], ["E1"])


class AdminPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_new_axes_all_reach_the_admin_payload(self):
        r = await _resp(_Roteiro({"answer": "x [E1]",
                                  "used_evidence": ["E1"],
                                  "sufficient": False}))
        p = r.admin_payload()
        for campo in ("grounding", "sufficiency", "deliverable",
                      "delivery_block_reason", "needs_human_review",
                      "answer_text_public", "stripping_artifacts",
                      "raw_used_evidence", "normalized_used_evidence"):
            self.assertIn(campo, p)
        self.assertEqual(p["sufficiency"], SUFFICIENCY_DENIED)
        self.assertFalse(p["deliverable"])
        self.assertTrue(p["needs_human_review"])

    async def test_the_admin_payload_is_json_serialisable(self):
        import json

        for payload in ({"answer": "x [E1]", "used_evidence": ["[E1]"],
                         "sufficient": False},
                        {"answer": "x", "used_evidence": {"a": 1}},
                        {"answer": "x [E1]", "used_evidence": [None, 7]}):
            with self.subTest(payload=payload):
                r = await _resp(_Roteiro(payload))
                json.dumps(r.admin_payload(), default=str)


class NoReusedNameTests(unittest.TestCase):
    def test_insufficient_evidence_is_not_reused(self):
        """``INSUFFICIENT_EVIDENCE`` ja significa outra coisa no projeto -
        ``evidence_state`` do mastery (migracao 029) e ``status`` do
        diagnostico inicial (migracao 013). Dois conceitos com o mesmo
        rotulo seria pior que um nome feio."""
        from agente_ia_edu.services.knowledge_engine import grounded_answer

        nomes = {n for n in dir(grounded_answer) if n.isupper()}
        valores = {getattr(grounded_answer, n) for n in nomes
                   if isinstance(getattr(grounded_answer, n), str)}
        self.assertNotIn("INSUFFICIENT_EVIDENCE", nomes)
        self.assertNotIn("INSUFFICIENT_EVIDENCE", valores)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
