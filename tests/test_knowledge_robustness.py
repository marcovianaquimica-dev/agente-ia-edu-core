"""CEREBRO - bordas do caminho ponta a ponta. Nenhuma chamada paga.

POR QUE UM ARQUIVO SO PARA BORDAS
=================================

Os testes de ``test_knowledge_context_builder`` e
``test_knowledge_grounded_answer`` cobrem o desenho: o que a peca PROMETE.
Estes cobrem o que acontece quando a entrada e feia - provider devolvendo
lixo, orcamento exatamente na borda, evidencia unica, tipos errados dentro
do JSON.

Separados porque sao perguntas diferentes. Quebrar um teste de desenho
significa que o contrato mudou; quebrar um teste daqui significa que uma
entrada que antes era tolerada deixou de ser.

CARACTERIZACAO NAO E APROVACAO
==============================

Alguns testes aqui comecam com ``test_characterises_``. Eles REGISTRAM o
comportamento atual sem afirmar que ele e o desejado, e cada um diz no
corpo qual e a decisao em aberto. Serve para que a mudanca seja deliberada:
se alguem alterar o comportamento, o teste quebra e a conversa acontece.
"""

from __future__ import annotations

import json
import unittest

from agente_ia_edu.providers.errors import (
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from agente_ia_edu.services.knowledge_engine.context_builder import (
    BUDGET_EXHAUSTED,
    ContextBuilder,
)
from agente_ia_edu.services.knowledge_engine.grounded_answer import (
    ANSWER_WITHOUT_CITATION,
    GROUNDED,
    INVALID_EVIDENCE_REFERENCE,
    PROVIDER_FAILED,
    PROVIDER_INVALID_RESPONSE,
    GroundedAnswerer,
)

from test_knowledge_context_builder import _hit, _textos
from test_knowledge_grounded_answer import _Roteiro


# ----------------------------------------------------------------- contexto


class SingleEvidenceTests(unittest.TestCase):
    def test_one_hit_produces_exactly_one_marker(self):
        hits = [_hit(rank=1)]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        self.assertEqual(len(ctx.evidences), 1)
        self.assertEqual(ctx.evidences[0].marker, "E1")
        self.assertEqual(ctx.marker_set(), {"E1"})
        self.assertEqual(ctx.excluded, ())

    def test_one_hit_still_carries_its_literal_to_the_prompt(self):
        hits = [_hit(rank=1, raw_text="peculiar unico")]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        self.assertIn("peculiar unico", ctx.prompt_payload())

    def test_a_lone_commercial_hit_still_hides_its_literal_in_public(self):
        """O caso de uma evidencia so e o que mais tenta um atalho: sem nada
        para comparar, um ``excerpt`` indevido passaria despercebido."""
        hits = [_hit(rank=1, rights="COMMERCIAL_REFERENCE",
                     raw_text="peculiar unico")]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        self.assertIn("peculiar unico", ctx.prompt_payload())
        self.assertNotIn("peculiar unico", json.dumps(ctx.admin_payload()))
        self.assertIsNone(ctx.evidences[0].excerpt)


class BudgetBoundaryTests(unittest.TestCase):
    """A borda exata, que e onde um ``>`` virado para ``>=`` passa batido."""

    def _tamanho_do_bloco(self, texto):
        hits = [_hit(rank=1, raw_text=texto)]
        ctx = ContextBuilder(budget_chars=10**6).build(hits, texts=_textos(hits))
        return ctx.used_chars

    def test_a_second_evidence_landing_exactly_on_the_budget_is_included(self):
        bloco = self._tamanho_do_bloco("abc")
        hits = [_hit(rank=1, raw_text="abc"), _hit(rank=2, raw_text="abc")]
        ctx = ContextBuilder(budget_chars=2 * bloco).build(
            hits, texts=_textos(hits)
        )
        self.assertEqual(len(ctx.evidences), 2)
        self.assertEqual(ctx.used_chars, 2 * bloco)
        self.assertEqual(ctx.excluded, ())

    def test_one_character_over_the_budget_is_excluded(self):
        bloco = self._tamanho_do_bloco("abc")
        hits = [_hit(rank=1, raw_text="abc"), _hit(rank=2, raw_text="abc")]
        ctx = ContextBuilder(budget_chars=2 * bloco - 1).build(
            hits, texts=_textos(hits)
        )
        self.assertEqual(len(ctx.evidences), 1)
        self.assertEqual(ctx.excluded[0]["reason"], BUDGET_EXHAUSTED)

    def test_an_oversized_evidence_that_is_not_first_is_cut(self):
        hits = [_hit(rank=1, raw_text="curto"),
                _hit(rank=2, raw_text="X" * 50_000)]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        self.assertEqual(len(ctx.evidences), 1)
        self.assertEqual(ctx.excluded[0]["rank"], 2)
        self.assertEqual(ctx.excluded[0]["reason"], BUDGET_EXHAUSTED)

    def test_a_smaller_evidence_after_an_oversized_one_still_enters(self):
        """O corte nao e uma parada: e uma recusa individual.

        Parar no primeiro estouro desperdicaria orcamento que ainda cabe, e
        foi o que os pilotos reais mostraram acontecendo - rank 5 cortado,
        rank 6 dentro.
        """
        hits = [_hit(rank=1, raw_text="curto"),
                _hit(rank=2, raw_text="X" * 50_000),
                _hit(rank=3, raw_text="tambem curto")]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        self.assertEqual([e.retrieval_rank for e in ctx.evidences], [1, 3])
        self.assertEqual([x["rank"] for x in ctx.excluded], [2])

    def test_markers_have_no_gap_when_a_middle_hit_is_dropped(self):
        """Os marcadores numeram o que ENTROU, nao o rank de origem.

        Se ``E2`` sumisse porque o rank 2 foi cortado, o modelo veria E1 e E3
        e poderia inferir que falta algo - ou citar ``[E2]``, que viraria
        marcador invalido por culpa do construtor.
        """
        hits = [_hit(rank=1, raw_text="curto"),
                _hit(rank=2, raw_text="X" * 50_000),
                _hit(rank=3, raw_text="tambem curto")]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        self.assertEqual([e.marker for e in ctx.evidences], ["E1", "E2"])


class MixedRightsTests(unittest.TestCase):
    def test_commercial_and_quotable_coexist_with_the_right_treatment(self):
        hits = [
            _hit(rank=1, rights="COMMERCIAL_REFERENCE",
                 raw_text="literal comercial proibido", titulo="Livro pago"),
            _hit(rank=2, rights="OWN",
                 raw_text="texto proprio liberado", titulo="Apostila propria"),
        ]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        publico = json.dumps(ctx.admin_payload(), ensure_ascii=False)

        # Ambos vao ao prompt - e a decisao explicita do piloto.
        self.assertIn("literal comercial proibido", ctx.prompt_payload())
        self.assertIn("texto proprio liberado", ctx.prompt_payload())

        # So o nao-comercial aparece em publico.
        self.assertNotIn("literal comercial proibido", publico)
        self.assertIn("texto proprio liberado", publico)

        por_marcador = {e.marker: e for e in ctx.evidences}
        self.assertIsNone(por_marcador["E1"].excerpt)
        self.assertIsNotNone(por_marcador["E2"].excerpt)

    def test_traceability_is_identical_for_both_rights_classes(self):
        """Direitos governam o TEXTO, nunca a rastreabilidade."""
        hits = [_hit(rank=1, rights="COMMERCIAL_REFERENCE"),
                _hit(rank=2, rights="OWN")]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        for e in ctx.evidences:
            self.assertIsNotNone(e.chunk_id)
            self.assertIsNotNone(e.text_hash)
            self.assertIsNotNone(e.source_title)
            self.assertIsNotNone(e.document_filename)
            self.assertIsNotNone(e.page_start)


class MissingTextTests(unittest.TestCase):
    def test_characterises_a_hit_whose_text_was_not_supplied(self):
        """DECISAO EM ABERTO.

        Hoje o bloco e montado mesmo com texto vazio: a evidencia entra,
        recebe marcador e gasta orcamento com cabecalho sem conteudo. O
        modelo pode cita-la, e a citacao sera considerada valida.

        Nao e um defeito inequivoco - em producao ``texts`` vem da mesma
        consulta que produziu os hits, entao a chave faltando significa um
        bug a montante, nao uma entrada legitima. Registrado para decisao:
        excluir com motivo nomeado seria mais coerente com o resto do
        desenho, mas e mudanca de comportamento e nao de correcao.
        """
        hits = [_hit(rank=1, raw_text="presente"), _hit(rank=2)]
        textos = {hits[0].chunk_id: "presente"}  # o rank 2 fica de fora
        ctx = ContextBuilder().build(hits, texts=textos)
        self.assertEqual(len(ctx.evidences), 2)
        self.assertEqual(ctx.evidences[1].marker, "E2")


# ------------------------------------------------------------------ geracao


def _ctx(n=3):
    hits = [_hit(rank=r, raw_text=f"conteudo numero {r}") for r in range(1, n + 1)]
    return ContextBuilder().build(hits, texts=_textos(hits))


async def _resp(provider, contexto=None, **kwargs):
    return await GroundedAnswerer(provider=provider).answer(
        "pergunta qualquer", contexto if contexto is not None else _ctx(),
        **kwargs,
    )


class DuplicateMarkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_same_marker_twice_in_the_body_counts_once(self):
        provider = _Roteiro({
            "answer": "Primeiro isto [E1], e tambem aquilo [E1].",
            "used_evidence": ["E1"],
        })
        r = await _resp(provider)
        self.assertEqual(r.status, GROUNDED)
        self.assertEqual(r.cited_markers, ("E1",))
        self.assertEqual(len(r.cited_evidences), 1)

    async def test_duplicates_in_the_field_also_collapse(self):
        provider = _Roteiro({
            "answer": "Resposta [E2].",
            "used_evidence": ["E2", "[E2]", " E2 "],
        })
        r = await _resp(provider)
        self.assertEqual(r.cited_markers, ("E2",))

    async def test_a_duplicated_invalid_marker_is_reported_once(self):
        provider = _Roteiro({
            "answer": "Inventado [E9] e de novo [E9].",
            "used_evidence": ["E9"],
        })
        r = await _resp(provider)
        self.assertEqual(r.status, INVALID_EVIDENCE_REFERENCE)
        self.assertEqual(r.invalid_markers, ("E9",))


class MalformedProviderOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_used_evidence_as_a_string_does_not_crash(self):
        """Iterar uma string daria 'E', '1' - nenhum deles marcador valido.

        O importante e nao explodir e nao inventar citacao.
        """
        provider = _Roteiro({"answer": "Texto sem marcador no corpo.",
                             "used_evidence": "E1"})
        r = await _resp(provider)
        self.assertEqual(r.status, ANSWER_WITHOUT_CITATION)
        self.assertEqual(r.invalid_markers, ())

    async def test_non_string_items_in_used_evidence_are_ignored(self):
        provider = _Roteiro({
            "answer": "Resposta [E1].",
            "used_evidence": ["E1", None, 7, {"marker": "E2"}, ["E3"]],
        })
        r = await _resp(provider)
        self.assertEqual(r.status, GROUNDED)
        self.assertEqual(r.cited_markers, ("E1",))

    async def test_a_null_answer_is_an_invalid_response(self):
        provider = _Roteiro({"answer": None, "used_evidence": []})
        r = await _resp(provider)
        self.assertEqual(r.status, PROVIDER_INVALID_RESPONSE)
        self.assertIsNone(r.answer)

    async def test_a_json_list_instead_of_an_object_is_an_invalid_response(self):
        provider = _Roteiro(None, cru=json.dumps(["E1", "E2"]))
        r = await _resp(provider)
        self.assertEqual(r.status, PROVIDER_INVALID_RESPONSE)

    async def test_a_json_string_instead_of_an_object_is_an_invalid_response(self):
        provider = _Roteiro(None, cru=json.dumps("so um texto"))
        r = await _resp(provider)
        self.assertEqual(r.status, PROVIDER_INVALID_RESPONSE)

    async def test_an_empty_body_is_an_invalid_response(self):
        provider = _Roteiro(None, cru="")
        r = await _resp(provider)
        self.assertEqual(r.status, PROVIDER_INVALID_RESPONSE)

    async def test_the_raw_text_is_kept_for_diagnosis_but_not_in_public(self):
        provider = _Roteiro(None, cru="isto nao e json")
        r = await _resp(provider)
        self.assertEqual(r.status, PROVIDER_INVALID_RESPONSE)
        self.assertNotIn("isto nao e json", json.dumps(r.admin_payload()))
        self.assertNotIn("isto nao e json", repr(r))


class EmptyAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def test_characterises_an_empty_answer_string(self):
        """DECISAO EM ABERTO.

        Uma ``answer`` vazia e tecnicamente um JSON valido no contrato, e
        hoje cai em ``ANSWER_WITHOUT_CITATION`` - que ja e falha fechada,
        com ``is_grounded = False``. Funciona, mas o nome informa mal: o
        problema nao foi a ausencia de citacao, foi a ausencia de resposta.

        Nao corrigi porque criar um estado novo e decisao de produto.
        """
        provider = _Roteiro({"answer": "", "used_evidence": []})
        r = await _resp(provider)
        self.assertEqual(r.status, ANSWER_WITHOUT_CITATION)
        self.assertFalse(r.is_grounded)

    async def test_characterises_a_whitespace_only_answer(self):
        provider = _Roteiro({"answer": "   \n  ", "used_evidence": []})
        r = await _resp(provider)
        self.assertFalse(r.is_grounded)


class ProviderErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_timeout_is_named_and_not_swallowed(self):
        provider = _Roteiro({}, erro=ProviderTimeoutError("estourou"))
        r = await _resp(provider)
        self.assertEqual(r.status, PROVIDER_FAILED)
        self.assertEqual(r.error, "ProviderTimeoutError")
        self.assertFalse(r.is_grounded)

    async def test_an_unavailable_provider_is_named(self):
        provider = _Roteiro({}, erro=ProviderUnavailableError("fora do ar"))
        r = await _resp(provider)
        self.assertEqual(r.status, PROVIDER_FAILED)
        self.assertEqual(r.error, "ProviderUnavailableError")

    async def test_a_failed_call_reports_no_fabricated_tokens(self):
        """Custo nao realizado nao pode virar zero: zero e um numero medido."""
        provider = _Roteiro({}, erro=ProviderTimeoutError("x"))
        r = await _resp(provider)
        self.assertIsNone(r.input_tokens)
        self.assertIsNone(r.output_tokens)


class SufficiencyGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_characterises_sufficient_false_with_valid_citations(self):
        """DECISAO EM ABERTO - e a mais importante deste arquivo.

        O modelo declara que a evidencia NAO basta, cita corretamente, e a
        resposta sai ``GROUNDED`` com ``is_grounded = True``. Os tres ramos
        do servico sao marcador invalido, nenhum marcador, e caso contrario
        fundamentada - nenhum deles olha ``sufficient``.

        O sinal nao e silencioso: o CLI avisa "o modelo declarou as
        evidencias INSUFICIENTES mesmo citando-as". Ele e CONSULTIVO, nao
        vinculante. Quem ler o relatorio ve; quem consumir ``is_grounded``
        programaticamente, nao.

        Nao alterei. Fazer ``sufficient: false`` fechar o portao e mudanca
        de comportamento do sistema - exatamente o tipo de decisao que nao
        cabe a mim tomar sozinho. Fica registrado com teste para que a
        escolha seja deliberada.
        """
        provider = _Roteiro({
            "answer": "O material nao cobre bem isto, mas ha indicio [E1].",
            "used_evidence": ["E1"],
            "sufficient": False,
        })
        r = await _resp(provider)
        self.assertEqual(r.status, GROUNDED)
        self.assertTrue(r.is_grounded)
        self.assertFalse(r.model_says_sufficient)

    async def test_the_sufficiency_flag_is_always_visible_in_public(self):
        """Qualquer que seja a decisao futura, o sinal tem de ser legivel."""
        provider = _Roteiro({"answer": "x [E1]", "used_evidence": ["E1"],
                             "sufficient": False})
        r = await _resp(provider)
        self.assertIn("model_says_sufficient", r.admin_payload())
        self.assertFalse(r.admin_payload()["model_says_sufficient"])

    async def test_an_absent_sufficiency_flag_is_none_not_true(self):
        provider = _Roteiro({"answer": "x [E1]", "used_evidence": ["E1"]})
        r = await _resp(provider)
        self.assertIsNone(r.model_says_sufficient)


class MarkerRangeTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_marker_beyond_the_available_range_is_invalid(self):
        contexto = _ctx(3)
        provider = _Roteiro({"answer": "Fora da faixa [E4].",
                             "used_evidence": ["E4"]})
        r = await _resp(provider, contexto)
        self.assertEqual(r.status, INVALID_EVIDENCE_REFERENCE)
        self.assertEqual(r.invalid_markers, ("E4",))

    async def test_e0_is_invalid_because_markers_start_at_one(self):
        provider = _Roteiro({"answer": "Indice zero [E0].",
                             "used_evidence": ["E0"]})
        r = await _resp(provider)
        self.assertEqual(r.status, INVALID_EVIDENCE_REFERENCE)

    async def test_a_large_index_does_not_wrap_around(self):
        provider = _Roteiro({"answer": "Gigante [E999].",
                             "used_evidence": ["E999"]})
        r = await _resp(provider)
        self.assertEqual(r.status, INVALID_EVIDENCE_REFERENCE)
        self.assertEqual(r.invalid_markers, ("E999",))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
