"""CEREBRO - geracao fundamentada e validacao deterministica das citacoes.

A pergunta que estes testes respondem: quando a resposta NAO pode ser
confiada, isso e detectavel sem ler o texto?

Quatro estados nomeados, e nenhum deles e silencioso. A alternativa -
devolver a resposta e deixar o leitor julgar - transforma alucinacao em
texto bem formatado, que e o pior formato possivel para um erro.

Tudo com ``FakeProvider`` e dubles. Nenhuma chamada paga.
"""

from __future__ import annotations

import json
import unittest
import uuid

from agente_ia_edu.providers.errors import ProviderRateLimitError
from agente_ia_edu.providers.models import TextGenerationResult
from agente_ia_edu.services.knowledge_engine.context_builder import (
    ContextBuilder,
)
from agente_ia_edu.services.knowledge_engine.grounded_answer import (
    ANSWER_WITHOUT_CITATION,
    DEGRADED_RETRIEVAL,
    GROUNDED,
    INVALID_EVIDENCE_REFERENCE,
    NO_EVIDENCE,
    PROVIDER_FAILED,
    PROVIDER_INVALID_RESPONSE,
    GroundedAnswerer,
)

from test_knowledge_context_builder import _hit, _textos


class _Roteiro:
    """Provider que devolve um JSON combinado. Nada de rede."""

    provider = "fake"

    def __init__(self, payload, *, erro=None, cru=None):
        self._payload = payload
        self._erro = erro
        self._cru = cru
        self.prompts: list[str] = []

    async def generate(self, request):
        self.prompts.append(request.prompt)
        if self._erro:
            raise self._erro
        texto = self._cru if self._cru is not None else json.dumps(self._payload)
        return TextGenerationResult(
            text=texto, provider=self.provider, model=request.model or "m",
            input_tokens=120, output_tokens=40,
        )


def _contexto(n=3):
    hits = [_hit(rank=r, raw_text=f"conteudo numero {r}") for r in range(1, n + 1)]
    return ContextBuilder().build(hits, texts=_textos(hits))


async def _responder(provider, contexto=None, **kwargs):
    return await GroundedAnswerer(provider=provider).answer(
        "Como se calcula a concentracao?",
        contexto if contexto is not None else _contexto(),
        **kwargs,
    )


class GroundedTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_well_cited_answer_is_grounded(self):
        provider = _Roteiro({
            "answer": "A concentracao e massa por volume [E1], medida em "
                      "mol por litro [E2].",
            "used_evidence": ["E1", "E2"],
        })
        r = await _responder(provider)
        self.assertEqual(r.status, GROUNDED)
        self.assertTrue(r.is_grounded)
        self.assertEqual(r.cited_markers, ("E1", "E2"))
        self.assertEqual(r.invalid_markers, ())

    async def test_the_cited_evidences_are_resolved_to_their_sources(self):
        """O ponto da fundamentacao: do marcador se chega a pagina."""
        provider = _Roteiro({"answer": "Resposta [E2].",
                             "used_evidence": ["E2"]})
        r = await _responder(provider)
        self.assertEqual(len(r.cited_evidences), 1)
        citada = r.cited_evidences[0]
        self.assertEqual(citada.marker, "E2")
        self.assertIsNotNone(citada.page_start)
        self.assertIsNotNone(citada.source_title)
        self.assertIsNotNone(citada.chunk_id)

    async def test_markers_are_taken_from_the_text_not_only_the_field(self):
        """Um modelo pode citar no texto e esquecer o campo. O que vale e o
        que o leitor ve."""
        provider = _Roteiro({"answer": "Vale isto [E3].", "used_evidence": []})
        r = await _responder(provider)
        self.assertEqual(r.status, GROUNDED)
        self.assertIn("E3", r.cited_markers)

    async def test_the_prompt_carries_the_evidence_and_the_question(self):
        provider = _Roteiro({"answer": "x [E1]", "used_evidence": ["E1"]})
        await _responder(provider)
        prompt = provider.prompts[0]
        self.assertIn("[E1]", prompt)
        self.assertIn("conteudo numero 1", prompt)
        self.assertIn("Como se calcula a concentracao?", prompt)


class FailureModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_evidence_never_calls_the_provider(self):
        """Sem evidencia nao ha o que fundamentar, e gastar uma chamada para
        descobrir isso seria desperdicio."""
        provider = _Roteiro({"answer": "nao deveria", "used_evidence": []})
        r = await _responder(provider, ContextBuilder().build([], texts={}))
        self.assertEqual(r.status, NO_EVIDENCE)
        self.assertFalse(r.is_grounded)
        self.assertIsNone(r.answer)
        self.assertEqual(provider.prompts, [])

    async def test_degraded_retrieval_is_refused_and_named(self):
        provider = _Roteiro({"answer": "x [E1]", "used_evidence": ["E1"]})
        r = await _responder(provider, retrieval_degraded=True,
                             degradation_reasons=("MISSING_EMBEDDINGS",))
        self.assertEqual(r.status, DEGRADED_RETRIEVAL)
        self.assertFalse(r.is_grounded)
        self.assertEqual(provider.prompts, [])
        self.assertIn("MISSING_EMBEDDINGS", r.degradation_reasons)

    async def test_degraded_retrieval_can_be_overridden_explicitly(self):
        provider = _Roteiro({"answer": "x [E1]", "used_evidence": ["E1"]})
        r = await _responder(provider, retrieval_degraded=True,
                             degradation_reasons=("MISSING_EMBEDDINGS",),
                             allow_degraded=True)
        self.assertEqual(r.status, GROUNDED)
        self.assertTrue(r.degraded)

    async def test_an_answer_without_any_citation_is_not_grounded(self):
        provider = _Roteiro({
            "answer": "A concentracao e massa por volume.",
            "used_evidence": [],
        })
        r = await _responder(provider)
        self.assertEqual(r.status, ANSWER_WITHOUT_CITATION)
        self.assertFalse(r.is_grounded)
        self.assertIsNotNone(r.answer)  # o texto fica, para inspecao

    async def test_an_invented_marker_is_an_observable_failure(self):
        """O caso que mais importa: o modelo citou algo que nao existe. Se
        isso passasse, a rastreabilidade seria decorativa."""
        provider = _Roteiro({
            "answer": "Segundo [E1] e tambem [E99], a resposta e esta.",
            "used_evidence": ["E1", "E99"],
        })
        r = await _responder(provider)
        self.assertEqual(r.status, INVALID_EVIDENCE_REFERENCE)
        self.assertFalse(r.is_grounded)
        self.assertEqual(r.invalid_markers, ("E99",))

    async def test_an_invalid_marker_wins_over_a_valid_one(self):
        """Uma citacao inventada contamina a resposta inteira - nao da para
        confiar no resto quando uma parte foi fabricada."""
        provider = _Roteiro({"answer": "[E1] e [E42]",
                             "used_evidence": ["E1", "E42"]})
        r = await _responder(provider)
        self.assertEqual(r.status, INVALID_EVIDENCE_REFERENCE)
        self.assertIn("E1", r.cited_markers)

    async def test_a_provider_failure_is_named(self):
        provider = _Roteiro(None, erro=ProviderRateLimitError("limite"))
        r = await _responder(provider)
        self.assertEqual(r.status, PROVIDER_FAILED)
        self.assertFalse(r.is_grounded)
        self.assertIn("RateLimit", r.error or "")

    async def test_a_non_json_response_is_named(self):
        provider = _Roteiro(None, cru="isto nao e json")
        r = await _responder(provider)
        self.assertEqual(r.status, PROVIDER_INVALID_RESPONSE)
        self.assertFalse(r.is_grounded)

    async def test_json_without_the_answer_field_is_named(self):
        provider = _Roteiro({"resposta": "campo errado"})
        r = await _responder(provider)
        self.assertEqual(r.status, PROVIDER_INVALID_RESPONSE)


class MarkerNormalisationTests(unittest.IsolatedAsyncioTestCase):
    """O marcador identifica uma evidencia; a sua grafia nao e a identidade.

    Observado na PRIMEIRA execucao real: o modelo preencheu
    ``used_evidence`` com ``["[E1]", "[E4]"]`` - com colchetes -, enquanto o
    corpo trazia ``[E1]`` e ``[E4]``. O validador comparava a grafia crua
    contra ``{E1..E5}``, nao achava, e rotulava uma resposta correta como
    referencia inventada.

    A linha a nao cruzar: normalizar GRAFIA e certo, inferir marcador de
    texto arbitrario seria inventar citacao que o modelo nao fez.
    """

    async def test_brackets_in_used_evidence_are_normalised(self):
        provider = _Roteiro({
            "answer": "A concentracao e mol por litro. [E1] [E2]",
            "used_evidence": ["[E1]", "[E2]"],
        })
        r = await _responder(provider)
        self.assertEqual(r.status, GROUNDED)
        self.assertEqual(r.invalid_markers, ())
        self.assertEqual(set(r.cited_markers), {"E1", "E2"})

    async def test_surrounding_whitespace_is_normalised(self):
        provider = _Roteiro({"answer": "x [E1]",
                             "used_evidence": ["  E1  ", "\t[E2]\n"]})
        r = await _responder(provider)
        self.assertEqual(r.status, GROUNDED)
        self.assertEqual(set(r.cited_markers), {"E1", "E2"})

    async def test_a_bare_marker_keeps_working(self):
        provider = _Roteiro({"answer": "x [E1]", "used_evidence": ["E1"]})
        r = await _responder(provider)
        self.assertEqual(r.status, GROUNDED)
        self.assertEqual(r.cited_markers, ("E1",))

    async def test_a_nonexistent_identifier_is_still_invalid(self):
        """Normalizar nao pode virar tolerar: ``[E99]`` continua inventado."""
        provider = _Roteiro({"answer": "x [E1] y [E99]",
                             "used_evidence": ["[E1]", "[E99]"]})
        r = await _responder(provider)
        self.assertEqual(r.status, INVALID_EVIDENCE_REFERENCE)
        self.assertEqual(r.invalid_markers, ("E99",))

    async def test_arbitrary_text_does_not_become_a_marker(self):
        """A linha a nao cruzar. Nada disto cita E1 - e tratar como se
        citasse seria fabricar fundamentacao."""
        for lixo in ("evidencia 1", "primeira", "E", "1", "fonte E1 e E2",
                     "[E1] e [E2]", "EE1", "e1x", ""):
            provider = _Roteiro({"answer": "sem citacao no corpo",
                                 "used_evidence": [lixo]})
            r = await _responder(provider)
            self.assertNotEqual(r.status, GROUNDED, lixo)
            self.assertNotIn("E1", r.cited_markers, lixo)

    async def test_lowercase_is_not_silently_accepted(self):
        """``e1`` nao e ``E1``. Aceitar seria assumir que o modelo quis
        dizer outra coisa, e assumir e o que esta validacao existe para
        evitar."""
        provider = _Roteiro({"answer": "sem citacao", "used_evidence": ["e1"]})
        r = await _responder(provider)
        self.assertNotEqual(r.status, GROUNDED)
        self.assertNotIn("E1", r.cited_markers)

    async def test_cross_validation_between_body_and_field_is_preserved(self):
        """A validacao cruzada continua: o conjunto citado e a UNIAO do
        corpo e do campo, e ambos sao conferidos contra as evidencias que o
        modelo de fato recebeu."""
        provider = _Roteiro({"answer": "No corpo so cito [E1].",
                             "used_evidence": ["[E2]", "[E77]"]})
        r = await _responder(provider)
        self.assertEqual(r.status, INVALID_EVIDENCE_REFERENCE)
        self.assertEqual(set(r.cited_markers), {"E1", "E2", "E77"})
        self.assertEqual(r.invalid_markers, ("E77",))

    async def test_the_same_marker_in_both_places_is_counted_once(self):
        provider = _Roteiro({"answer": "x [E1] y [E1]",
                             "used_evidence": ["E1", "[E1]"]})
        r = await _responder(provider)
        self.assertEqual(r.cited_markers, ("E1",))

    async def test_regression_the_exact_shape_of_the_first_real_run(self):
        """REGRESSAO - a forma exata observada na primeira execucao real,
        contra o corpus de 5.911 vetores, em 2026-10-02.

        O modelo citou [E1] e [E4] no corpo e devolveu
        ``used_evidence = ["[E1]", "[E4]"]``. A resposta estava correta e as
        duas evidencias eram as certas; o sistema a reprovou por grafia.
        """
        contexto = _contexto(5)
        provider = _Roteiro({
            "answer": "Calcula-se dividindo a quantidade de materia do "
                      "soluto, em mol, pelo volume da solucao, em litros: "
                      "c = n/V. [E1] [E4]",
            "used_evidence": ["[E1]", "[E4]"],
            "sufficient": True,
        })
        r = await _responder(provider, contexto)
        self.assertEqual(r.status, GROUNDED)
        self.assertTrue(r.is_grounded)
        self.assertEqual(r.invalid_markers, ())
        self.assertEqual(set(r.cited_markers), {"E1", "E4"})
        self.assertEqual(
            {e.marker for e in r.cited_evidences}, {"E1", "E4"}
        )


class CostTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_token_usage_is_recorded(self):
        provider = _Roteiro({"answer": "x [E1]", "used_evidence": ["E1"]})
        r = await _responder(provider)
        self.assertEqual(r.input_tokens, 120)
        self.assertEqual(r.output_tokens, 40)

    async def test_absent_usage_is_none_and_not_a_fabricated_zero(self):
        class _SemUso(_Roteiro):
            async def generate(self, request):
                self.prompts.append(request.prompt)
                return TextGenerationResult(
                    text=json.dumps(self._payload), provider="fake", model="m"
                )

        provider = _SemUso({"answer": "x [E1]", "used_evidence": ["E1"]})
        r = await _responder(provider)
        self.assertIsNone(r.input_tokens)
        self.assertIsNone(r.output_tokens)


class RightsTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_public_result_carries_no_commercial_literal(self):
        literal = "LITERAL COMERCIAL QUE NAO PODE VAZAR NA RESPOSTA"
        hits = [_hit(rank=1, rights="COMMERCIAL_REFERENCE",
                     raw_text=literal, titulo="Livro")]
        contexto = ContextBuilder().build(hits, texts=_textos(hits))
        provider = _Roteiro({"answer": "Conforme [E1], a resposta e esta.",
                             "used_evidence": ["E1"]})
        r = await _responder(provider, contexto)
        blob = json.dumps(r.public_payload(), default=str)
        self.assertNotIn(literal, blob)
        self.assertNotIn(literal[:20], blob)
        self.assertNotIn(literal[:20], repr(r))

    async def test_traceability_is_preserved_for_commercial_evidence(self):
        hits = [_hit(rank=1, rights="COMMERCIAL_REFERENCE",
                     raw_text="literal", titulo="Livro", page=314)]
        contexto = ContextBuilder().build(hits, texts=_textos(hits))
        provider = _Roteiro({"answer": "[E1]", "used_evidence": ["E1"]})
        r = await _responder(provider, contexto)
        payload = r.public_payload()
        fonte = payload["cited_evidences"][0]
        self.assertEqual(fonte["page_start"], 314)
        self.assertEqual(fonte["source_title"], "Livro")
        self.assertIsNone(fonte["excerpt"])
        self.assertIn("chunk_id", fonte)
        self.assertIn("text_hash", fonte)


if __name__ == "__main__":
    unittest.main()
