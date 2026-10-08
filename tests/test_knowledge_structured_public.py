"""CEREBRO - o contrato de audiencia para o caminho estruturado.

O aluno recebe a resposta limpa. Evidencias, spans, fontes, scores,
estados internos e rastreabilidade ficam no canal ADMIN.

A regra que torna isso estrutural, e nao disciplina: ``PublicAnswer`` tem
tres campos e os administrativos NAO EXISTEM nele. A projecao e a unica
porta, e ela devolve ``answer_text = None`` para todo estado nao
entregavel - nao ha parametro que permita o contrario.

Nenhuma chamada paga.
"""

from __future__ import annotations

import json
import unittest

from agente_ia_edu.services.knowledge_engine.context_builder import (
    ContextBuilder,
)
from agente_ia_edu.services.knowledge_engine.public_answer import (
    ADMIN_ONLY_FIELDS,
    ANSWERED,
    NO_ANSWER_FROM_CORPUS,
    PUBLIC_FIELDS,
    TEMPORARILY_UNAVAILABLE,
    UNAVAILABLE,
    structured_to_public,
)
from agente_ia_edu.services.knowledge_engine.structured_answer import (
    StructuredAnswerer,
)

from test_knowledge_context_builder import _hit, _textos
from test_knowledge_grounded_answer import _Roteiro
from test_knowledge_structured_answer import TEXTOS, _ctx, _factual

SEGREDO = "WXYZ-LITERAL-COMERCIAL"
OBRA = "WXYZ-TITULO-DA-OBRA"


async def _par(payload, contexto=None, **kwargs):
    r = await StructuredAnswerer(provider=_Roteiro(payload)).answer(
        "pergunta", contexto if contexto is not None else _ctx(), **kwargs)
    return r, structured_to_public(r)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_verified_answer_is_delivered_clean(self):
        r, p = await _par({"claims": [
            _factual("A concentração é massa por volume.",
                     [("E1", "quociente entre", "define")])
        ], "sufficient": True})
        self.assertTrue(r.deliverable)
        self.assertEqual(p.outcome, ANSWERED)
        self.assertEqual(p.answer_text, "A concentração é massa por volume.")
        self.assertIsNone(p.unavailable_reason)

    async def test_an_unverified_claim_delivers_nothing(self):
        r, p = await _par({"claims": [
            _factual("x", [("E1", "molho de salada", "inventado")])
        ], "sufficient": True})
        self.assertEqual(p.outcome, UNAVAILABLE)
        self.assertIsNone(p.answer_text)
        self.assertEqual(p.unavailable_reason, NO_ANSWER_FROM_CORPUS)

    async def test_denied_sufficiency_delivers_nothing(self):
        _, p = await _par({"claims": [
            _factual("x", [("E1", "quociente", "a")])
        ], "sufficient": False})
        self.assertIsNone(p.answer_text)
        self.assertEqual(p.unavailable_reason, NO_ANSWER_FROM_CORPUS)

    async def test_a_contract_violation_is_operational_not_corpus(self):
        """O provider desviou do formato. Dizer ao aluno que o acervo nao
        cobre seria afirmar algo que nao foi medido."""
        _, p = await _par({"claims": [
            {"kind": "FACTUAL", "text": "sem suporte"}
        ], "sufficient": True})
        self.assertEqual(p.unavailable_reason, TEMPORARILY_UNAVAILABLE)

    async def test_no_evidence_delivers_nothing(self):
        _, p = await _par({"claims": []},
                          ContextBuilder().build([], texts={}))
        self.assertIsNone(p.answer_text)
        self.assertEqual(p.unavailable_reason, NO_ANSWER_FROM_CORPUS)

    async def test_degraded_retrieval_is_temporary(self):
        _, p = await _par({"claims": [
            _factual("x", [("E1", "quociente", "a")])
        ], "sufficient": True}, retrieval_degraded=True)
        self.assertEqual(p.unavailable_reason, TEMPORARILY_UNAVAILABLE)

    async def test_every_block_reason_is_explicitly_classified(self):
        """Nenhum motivo pode cair no padrao por omissao - a mesma regra
        que ja vale para o caminho antigo."""
        from agente_ia_edu.services.knowledge_engine import (
            public_answer, structured_answer,
        )

        novos = {structured_answer.STRUCTURED_CONTRACT_VIOLATION,
                 structured_answer.STRUCTURED_UNVERIFIED_CLAIM}
        self.assertEqual(novos - public_answer.KNOWN_BLOCK_REASONS, set())


class LeakTests(unittest.IsolatedAsyncioTestCase):
    async def _montar(self, payload=None):
        hits = [_hit(rank=r, rights="COMMERCIAL_REFERENCE", titulo=OBRA,
                     raw_text=f"{SEGREDO} {TEXTOS[r]}") for r in range(1, 5)]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        return await _par(payload or {"claims": [
            _factual("Resposta limpa.", [("E1", "quociente entre", "define")])
        ], "sufficient": True}, ctx)

    async def test_no_span_reaches_the_public_payload(self):
        """O span e literal da obra. Vai ao ADMIN, nunca ao aluno."""
        r, p = await self._montar()
        blob = json.dumps(p.payload(), ensure_ascii=False)
        self.assertNotIn("quociente entre", blob)
        self.assertIn("quociente entre",
                      json.dumps(r.admin_payload(), ensure_ascii=False))

    async def test_no_sentinel_reaches_the_public_payload(self):
        r, p = await self._montar()
        blob = json.dumps(p.payload(), ensure_ascii=False)
        for s in (SEGREDO, OBRA):
            with self.subTest(sentinela=s):
                self.assertNotIn(s, blob)

    async def test_no_marker_or_evidence_id_reaches_the_public(self):
        r, p = await self._montar()
        blob = json.dumps(p.payload(), ensure_ascii=False)
        self.assertNotRegex(blob, r"\[E\d+\]")
        for m in r.available_markers:
            self.assertNotIn(f'"{m}"', blob)

    async def test_no_admin_field_name_reaches_the_public(self):
        _, p = await self._montar()
        blob = json.dumps(p.payload(), ensure_ascii=False)
        for campo in sorted(ADMIN_ONLY_FIELDS):
            with self.subTest(campo=campo):
                self.assertNotIn(f'"{campo}"', blob)

    async def test_the_public_payload_has_exactly_three_fields(self):
        _, p = await self._montar()
        self.assertEqual(set(p.payload()), PUBLIC_FIELDS)

    async def test_the_payload_stays_flat_in_every_state(self):
        for payload in (
            {"claims": [_factual("ok", [("E1", "quociente entre", "a")])],
             "sufficient": True},
            {"claims": [_factual("x", [("E1", "inexistente", "a")])],
             "sufficient": True},
            {"claims": [{"kind": "FACTUAL", "text": "sem suporte"}],
             "sufficient": True},
        ):
            with self.subTest(payload=payload):
                _, p = await self._montar(payload)
                for v in p.payload().values():
                    self.assertNotIsInstance(v, (dict, list, tuple, set))

    async def test_a_blocked_answer_leaks_nothing_at_all(self):
        """O caminho de bloqueio e o menos exercitado, logo o mais
        provavel de vazar."""
        r, p = await self._montar({"claims": [
            _factual(f"Copiando {SEGREDO}.",
                     [("E1", "inexistente aqui", "falha")])
        ], "sufficient": True})
        self.assertFalse(r.deliverable)
        self.assertIsNone(p.answer_text)
        self.assertNotIn(SEGREDO, json.dumps(p.payload(), ensure_ascii=False))


class NeverPresentUnverifiedAsValidTests(unittest.IsolatedAsyncioTestCase):
    """Item 5 do fechamento: resposta nao verificavel NUNCA chega ao
    aluno como resposta valida."""

    async def test_one_unverified_claim_blocks_the_whole_answer(self):
        """Nao se entrega o pedaco bom e se omite o ruim: a resposta e
        uma coisa so, e uma parte nao verificada contamina o conjunto."""
        r, p = await _par({"claims": [
            _factual("Verdade sustentada.", [("E1", "quociente", "ok")]),
            _factual("Afirmação sem lastro.", [("E1", "inexistente", "x")]),
        ], "sufficient": True})
        self.assertTrue(r.claims[0].verified)
        self.assertFalse(r.claims[1].verified)
        self.assertIsNone(p.answer_text)

    async def test_the_assembled_text_exists_but_is_not_delivered(self):
        """O ADMIN ve o texto montado; o aluno nao. Sao canais
        diferentes e a diferenca e verificavel."""
        r, p = await _par({"claims": [
            _factual("Texto qualquer.", [("E1", "inexistente", "x")])
        ], "sufficient": True})
        self.assertTrue(r.answer_text)
        self.assertIn("Texto qualquer", r.admin_payload()["answer_text"])
        self.assertIsNone(p.answer_text)

    async def test_an_unknown_future_block_reason_fails_safe(self):
        import dataclasses

        r, _ = await _par({"claims": [
            _factual("ok", [("E1", "quociente", "a")])
        ], "sufficient": True})
        falsa = dataclasses.replace(r, status="ESTADO_QUE_NAO_EXISTE")
        p = structured_to_public(falsa)
        self.assertEqual(p.outcome, UNAVAILABLE)
        self.assertIsNone(p.answer_text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
