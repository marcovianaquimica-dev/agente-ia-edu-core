"""CEREBRO - tentativas deliberadas de quebrar o contrato de audiencia.

Os outros arquivos perguntam "funciona?". Este pergunta "da para fazer
vazar?". A diferenca importa: um contrato de visibilidade que so foi
testado pelo caminho feliz nao foi testado.

O ALVO
======

    metadado ADMIN na saida publica
    literal comercial na saida publica
    marcador sobrevivendo a sanitizacao
    o modelo ecoando o aparato interno
    entrada que derruba a sanitizacao
    tipo errado virando decisao

NENHUMA CHAMADA PAGA.
"""

from __future__ import annotations

import json
import unittest
import uuid

from agente_ia_edu.services.knowledge_engine.context_builder import (
    ContextBuilder,
)
from agente_ia_edu.services.knowledge_engine.grounded_answer import (
    GROUNDED,
    INVALID_EVIDENCE_REFERENCE,
    SUFFICIENCY_UNDECLARED,
    GroundedAnswerer,
)
from agente_ia_edu.services.knowledge_engine.public_answer import (
    ADMIN_ONLY_FIELDS,
    ANSWERED,
    PUBLIC_FIELDS,
    UNAVAILABLE,
    to_public,
)
from agente_ia_edu.services.knowledge_engine.sanitize import strip_markers

from test_knowledge_context_builder import _hit, _textos
from test_knowledge_grounded_answer import _Roteiro

SEGREDO = "QXZZ-LITERAL-COMERCIAL"
OBRA = "QXZZ-TITULO-DA-OBRA"


def _ctx_comercial(n=3):
    hits = [_hit(rank=r, rights="COMMERCIAL_REFERENCE", titulo=OBRA,
                 raw_text=f"{SEGREDO} trecho {r}") for r in range(1, n + 1)]
    return ContextBuilder().build(hits, texts=_textos(hits))


async def _par(payload, contexto=None, **kwargs):
    r = await GroundedAnswerer(provider=_Roteiro(payload)).answer(
        "pergunta", contexto if contexto is not None else _ctx_comercial(),
        **kwargs)
    return r, to_public(r)


class MetadataLeakAttemptTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_model_quoting_a_chunk_id_is_characterised(self):
        """RISCO EM ABERTO, nao defeito do contrato.

        Se o modelo copiar um ``chunk_id`` para dentro da resposta, ele
        sai - a resposta E a saida publica, e nenhuma camada compara o
        texto gerado contra os metadados. O contrato impede o vazamento
        pelos CAMPOS; nao impede o modelo de recitar o que recebeu.

        O prompt nao envia ``chunk_id``, entao para isso acontecer o
        modelo teria de inventar um UUID igual ao real - possivel apenas
        se um dia o prompt passar a carregar o identificador.
        """
        contexto = _ctx_comercial()
        alvo = str(contexto.evidences[0].chunk_id)
        r, p = await _par({"answer": f"Ver {alvo}. [E1]",
                           "used_evidence": ["E1"], "sufficient": True})
        self.assertIn(alvo, p.answer_text)
        # ... mas nenhum CAMPO administrativo foi junto
        self.assertEqual(set(p.payload()), PUBLIC_FIELDS)

    async def test_the_prompt_never_carries_chunk_id_or_text_hash(self):
        """A defesa real contra o caso acima: o modelo nao PODE citar o
        que nunca viu."""
        contexto = _ctx_comercial()
        prompt = contexto.prompt_payload()
        for e in contexto.evidences:
            with self.subTest(marker=e.marker):
                self.assertNotIn(str(e.chunk_id), prompt)
                self.assertNotIn(e.text_hash, prompt)

    async def test_no_score_or_rank_can_reach_the_public_payload(self):
        r, p = await _par({"answer": "Resposta. [E1]",
                           "used_evidence": ["E1"], "sufficient": True})
        blob = json.dumps(p.payload(), ensure_ascii=False)
        for e in r.cited_evidences:
            self.assertNotIn(str(e.score), blob)
            self.assertNotIn(f"rank={e.retrieval_rank}", blob)

    def test_admin_only_fields_is_not_empty_and_covers_the_obvious(self):
        """Sem isto, esvaziar ``ADMIN_ONLY_FIELDS`` faria os testes de
        nome passarem por vacuidade."""
        self.assertGreater(len(ADMIN_ONLY_FIELDS), 30)
        for obrigatorio in ("chunk_id", "text_hash", "source_title",
                            "page_start", "score", "cited_evidences",
                            "grounding", "sufficiency", "deliverable",
                            "answer", "status"):
            with self.subTest(campo=obrigatorio):
                self.assertIn(obrigatorio, ADMIN_ONLY_FIELDS)


class CommercialLiteralAttemptTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_literal_never_reaches_the_public_payload_by_itself(self):
        r, p = await _par({"answer": "Resposta limpa. [E1]",
                           "used_evidence": ["E1"], "sufficient": True})
        self.assertNotIn(SEGREDO, json.dumps(p.payload(), ensure_ascii=False))

    async def test_the_literal_is_absent_from_the_admin_payload_too(self):
        """Direitos continuam valendo no canal ADMIN."""
        r, _ = await _par({"answer": "x [E1]", "used_evidence": ["E1"]})
        self.assertNotIn(SEGREDO, json.dumps(r.admin_payload(),
                                             ensure_ascii=False, default=str))

    async def test_a_blocked_answer_leaks_nothing_at_all(self):
        """O caminho de bloqueio e o menos exercitado, logo o mais
        provavel de vazar."""
        r, p = await _par({"answer": f"Copiando {SEGREDO}. [E1]",
                           "used_evidence": ["E1"], "sufficient": False})
        self.assertFalse(r.deliverable)
        blob = json.dumps(p.payload(), ensure_ascii=False)
        self.assertNotIn(SEGREDO, blob)
        self.assertNotIn(OBRA, blob)
        self.assertIsNone(p.answer_text)


class MalformedMarkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_markers_glued_together_are_both_removed(self):
        r, p = await _par({"answer": "Resposta.[E1][E2]",
                           "used_evidence": ["E1", "E2"],
                           "sufficient": True})
        self.assertEqual(r.grounding, GROUNDED)
        self.assertNotRegex(p.answer_text or "", r"\[E\d+\]")

    async def test_a_lowercase_marker_in_the_body_is_not_a_marker(self):
        """``[e1]`` nao e citacao - e texto. Nao vira marcador valido nem
        e removido."""
        r, p = await _par({"answer": "Texto [e1] aqui. [E1]",
                           "used_evidence": ["E1"], "sufficient": True})
        self.assertEqual(r.cited_markers, ("E1",))
        self.assertIn("[e1]", p.answer_text)

    async def test_a_zero_padded_marker_does_not_match_an_evidence(self):
        r, _ = await _par({"answer": "Texto [E01].",
                           "used_evidence": ["E01"]})
        self.assertEqual(r.status, INVALID_EVIDENCE_REFERENCE)

    async def test_an_unclosed_marker_blocks_delivery(self):
        """``[E1`` sobrevive a remocao e publicaria vocabulario interno
        quebrado."""
        r, p = await _par({"answer": "Texto [E1 sem fechar. [E2]",
                           "used_evidence": ["E2"], "sufficient": True})
        self.assertTrue(r.stripping_artifacts)
        self.assertFalse(r.deliverable)
        self.assertIsNone(p.answer_text)

    async def test_a_marker_with_inner_space_is_not_recognised(self):
        r, p = await _par({"answer": "Texto [E 1] aqui. [E1]",
                           "used_evidence": ["E1"], "sufficient": True})
        self.assertEqual(r.cited_markers, ("E1",))
        self.assertIn("[E 1]", p.answer_text)


class WeirdUsedEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_deeply_nested_structure_does_not_crash(self):
        r, _ = await _par({"answer": "Texto. [E1]",
                           "used_evidence": [{"a": [{"b": ["E1"]}]}],
                           "sufficient": True})
        self.assertEqual(r.cited_markers, ("E1",))
        self.assertEqual(r.normalized_used_evidence, ())

    async def test_a_very_long_list_is_handled(self):
        r, _ = await _par({"answer": "Texto. [E1]",
                           "used_evidence": ["E1"] * 500,
                           "sufficient": True})
        self.assertEqual(r.normalized_used_evidence, ("E1",))

    async def test_a_dict_instead_of_a_list_is_recorded_not_iterated(self):
        r, _ = await _par({"answer": "Texto. [E1]",
                           "used_evidence": {"E1": True},
                           "sufficient": True})
        self.assertEqual(r.raw_used_evidence, {"E1": True})
        self.assertEqual(r.normalized_used_evidence, ())

    async def test_the_raw_value_survives_to_the_admin_payload_intact(self):
        for bruto in ([], ["E1"], "E1", {"a": 1}, None, 7):
            with self.subTest(bruto=bruto):
                r, _ = await _par({"answer": "Texto. [E1]",
                                   "used_evidence": bruto})
                self.assertEqual(r.admin_payload()["raw_used_evidence"], bruto)


class SufficiencyTypeAttackTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_truthy_value_other_than_true_affirms(self):
        for valor in (1, "true", "sim", [1], {"ok": 1}, 0.0, -1):
            with self.subTest(valor=valor):
                r, _ = await _par({"answer": "Texto. [E1]",
                                   "used_evidence": ["E1"],
                                   "sufficient": valor})
                self.assertEqual(r.sufficiency, SUFFICIENCY_UNDECLARED)

    async def test_no_falsy_value_other_than_false_denies(self):
        """``0`` e ``""`` NAO bloqueiam. Tratar falsy como negativa faria
        o portao disparar por tipo errado, nao por afirmacao."""
        for valor in (0, "", [], {}, "false", "False", None):
            with self.subTest(valor=valor):
                r, p = await _par({"answer": "Texto. [E1]",
                                   "used_evidence": ["E1"],
                                   "sufficient": valor})
                self.assertEqual(r.sufficiency, SUFFICIENCY_UNDECLARED)
                self.assertTrue(r.deliverable)
                self.assertEqual(p.outcome, ANSWERED)


class SanitizationCrashTests(unittest.TestCase):
    """Entrada que poderia derrubar a remocao em vez de reprova-la."""

    def test_a_very_long_text_does_not_blow_up(self):
        texto = ("palavra " * 50_000) + "[E1]"
        limpo, art = strip_markers(texto)
        self.assertNotIn("[E1]", limpo)
        self.assertEqual(art, ())

    def test_hundreds_of_markers_are_all_removed(self):
        texto = " ".join(f"frase {i}. [E{i}]" for i in range(1, 301))
        limpo, _ = strip_markers(texto)
        self.assertNotRegex(limpo, r"\[E\d+\]")

    def test_control_characters_do_not_crash(self):
        for ruim in ("\x00[E1]\x00", "a\r\n[E1]\r\nb", "\t[E1]\t",
                     "a​[E1]​b"):
            with self.subTest(texto=repr(ruim)):
                strip_markers(ruim)

    def test_unicode_oddities_do_not_crash(self):
        for ruim in ("é[E1]ç", "🧪 [E1] 🧪", "ℳ = n/V [E1]",
                     "\U0001F9EA" * 100 + "[E1]"):
            with self.subTest(texto=repr(ruim)):
                limpo, _ = strip_markers(ruim)
                self.assertNotIn("[E1]", limpo)

    def test_a_text_that_is_only_punctuation_is_handled(self):
        limpo, art = strip_markers("... [E1]")
        self.assertEqual(limpo, "...")
        self.assertEqual(art, ())

    def test_nested_brackets_do_not_confuse_the_balance_check(self):
        limpo, art = strip_markers("f([a] + [b]) = 2. [E1]")
        self.assertEqual(art, ())
        self.assertIn("[a] + [b]", limpo)


class EmptyAnswerAttackTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_empty_answer_with_a_valid_marker_in_the_field(self):
        """O campo cita, o corpo esta vazio. Nao pode virar entrega."""
        r, p = await _par({"answer": "", "used_evidence": ["E1"],
                           "sufficient": True})
        self.assertEqual(p.outcome, UNAVAILABLE)
        self.assertIsNone(p.answer_text)

    async def test_an_answer_that_is_only_a_marker_is_not_delivered(self):
        r, p = await _par({"answer": "[E1]", "used_evidence": ["E1"],
                           "sufficient": True})
        self.assertEqual(r.grounding, GROUNDED)
        self.assertTrue(r.stripping_artifacts)
        self.assertFalse(r.deliverable)
        self.assertIsNone(p.answer_text)

    async def test_whitespace_only_with_a_marker_is_not_delivered(self):
        r, p = await _par({"answer": "   [E1]   ", "used_evidence": ["E1"],
                           "sufficient": True})
        self.assertFalse(r.deliverable)
        self.assertIsNone(p.answer_text)


class PublicShapeInvariantTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_public_payload_is_flat_in_every_state(self):
        """Estrutura rasa e o que torna a inspecao por serializacao
        confiavel: nao ha onde esconder dado aninhado."""
        casos = [
            {"answer": "ok [E1]", "used_evidence": ["E1"], "sufficient": True},
            {"answer": "ok [E9]", "used_evidence": ["E9"]},
            {"answer": "", "used_evidence": []},
            {"answer": "ok [E1]", "used_evidence": ["E1"],
             "sufficient": False},
        ]
        for payload in casos:
            with self.subTest(payload=payload):
                _, p = await _par(payload)
                for valor in p.payload().values():
                    self.assertNotIsInstance(valor, (dict, list, tuple, set))

    async def test_the_outcome_vocabulary_is_closed(self):
        casos = [
            {"answer": "ok [E1]", "used_evidence": ["E1"], "sufficient": True},
            {"answer": "ok [E9]", "used_evidence": ["E9"]},
            {"answer": "ok [E1]", "used_evidence": ["E1"],
             "sufficient": False},
        ]
        for payload in casos:
            with self.subTest(payload=payload):
                _, p = await _par(payload)
                self.assertIn(p.outcome, {ANSWERED, UNAVAILABLE})

    def test_every_block_reason_is_explicitly_classified(self):
        """Nenhum motivo pode cair no padrao por omissao.

        ``EMPTY_PUBLIC_ANSWER`` caiu, na primeira versao: eu o criei e
        esqueci de classifica-lo, e ele virou operacional sem ninguem
        decidir. O desenho dizia que isso nao deveria ser possivel - este
        teste e o que torna a promessa verificavel.
        """
        from agente_ia_edu.services.knowledge_engine import (
            grounded_answer, public_answer, structured_answer,
        )

        motivos = {
            grounded_answer.NO_EVIDENCE,
            grounded_answer.DEGRADED_RETRIEVAL,
            grounded_answer.ANSWER_WITHOUT_CITATION,
            grounded_answer.INVALID_EVIDENCE_REFERENCE,
            grounded_answer.PROVIDER_FAILED,
            grounded_answer.PROVIDER_INVALID_RESPONSE,
            grounded_answer.EVIDENCE_DECLARED_INSUFFICIENT,
            grounded_answer.SANITIZATION_FAILED,
            grounded_answer.EMPTY_PUBLIC_ANSWER,
            # o caminho estruturado acrescentou dois, e precisam estar
            # classificados tambem - foi este teste que pegou a omissao
            # quando eles entraram.
            structured_answer.STRUCTURED_UNVERIFIED_CLAIM,
            structured_answer.STRUCTURED_CONTRACT_VIOLATION,
        }
        self.assertEqual(motivos - public_answer.KNOWN_BLOCK_REASONS, set())
        self.assertEqual(public_answer.KNOWN_BLOCK_REASONS - motivos, set())

    async def test_an_empty_answer_is_operational_not_a_corpus_statement(self):
        """Resposta vazia e desvio de contrato do provider. Dizer ao aluno
        que "o acervo nao cobre" seria afirmar algo que nao foi medido."""
        from agente_ia_edu.services.knowledge_engine.public_answer import (
            TEMPORARILY_UNAVAILABLE,
        )

        _, p = await _par({"answer": "", "used_evidence": ["E1"],
                           "sufficient": True})
        self.assertEqual(p.unavailable_reason, TEMPORARILY_UNAVAILABLE)

    async def test_an_unknown_future_block_reason_fails_safe(self):
        """Se alguem criar um motivo novo e esquecer de classifica-lo, a
        saida tem de ser INDISPONIVEL - nunca entregar por omissao."""
        import dataclasses

        r, _ = await _par({"answer": "ok [E1]", "used_evidence": ["E1"],
                           "sufficient": True})
        falsa = dataclasses.replace(r, status="MOTIVO_QUE_NAO_EXISTE")
        p = to_public(falsa)
        self.assertEqual(p.outcome, UNAVAILABLE)
        self.assertIsNone(p.answer_text)


class ContextLiteralIsolationTests(unittest.TestCase):
    def test_a_source_title_cannot_smuggle_the_literal(self):
        """Titulo da obra NAO e literal protegido, mas tambem nao pode
        trazer o texto junto por descuido de montagem."""
        h = _hit(rank=1, rights="COMMERCIAL_REFERENCE",
                 titulo=f"Obra {uuid.uuid4()}", raw_text=SEGREDO)
        ctx = ContextBuilder().build([h], texts=_textos([h]))
        self.assertNotIn(SEGREDO, json.dumps(ctx.admin_payload(), default=str))
        self.assertIn(SEGREDO, ctx.prompt_payload())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
