"""CEREBRO - a saida de AUDIENCIA: o que aluno e professor recebem.

AUSENCIA, NAO FILTRO
====================

``PublicAnswer`` tem tres campos. Os administrativos nao estao ausentes
por terem sido removidos - eles NAO EXISTEM nesta estrutura. Nao ha o que
esquecer de filtrar, e nenhuma rota consegue devolver o que o tipo nao
carrega.

ENTREGA E PROPRIEDADE DO ESTADO
===============================

``to_public()`` devolve ``answer_text = None`` para todo estado nao
entregavel. Um chamador nao consegue publicar resposta nao fundamentada
nem por descuido nem de proposito - nao existe parametro que permita.

O COLAPSO DO MOTIVO E INTENCIONAL
=================================

"o modelo inventou uma citacao", "nao havia evidencia" e "o modelo disse
que nao bastava" viram todos ``NO_ANSWER_FROM_CORPUS``. Distinguir
entregaria a quem perguntou informacao sobre o estado interno do acervo.
Quem precisa distinguir e o ADMIN, e ele tem canal proprio.

O TESTE QUE MAIS IMPORTA AQUI
=============================

``SentinelLeakTests``. Conferir nomes de campo pega o vazamento obvio;
nao pega o dado aninhado sob uma chave renomeada. Por isso cada dado
administrativo vira uma string unica e improvavel, e a exigencia e que
NENHUMA apareca em lugar algum da serializacao.
"""

from __future__ import annotations

import json
import unittest

from agente_ia_edu.providers.errors import ProviderTimeoutError
from agente_ia_edu.services.knowledge_engine.context_builder import (
    ContextBuilder,
)
from agente_ia_edu.services.knowledge_engine.grounded_answer import (
    GroundedAnswerer,
)
from agente_ia_edu.services.knowledge_engine.public_answer import (
    ADMIN_ONLY_FIELDS,
    ANSWERED,
    NO_ANSWER_FROM_CORPUS,
    PUBLIC_FIELDS,
    TEMPORARILY_UNAVAILABLE,
    UNAVAILABLE,
    PublicAnswer,
    to_public,
)

from test_knowledge_context_builder import _hit, _textos
from test_knowledge_grounded_answer import _Roteiro


def _ctx(n=3, rights="OWN", titulo="Apostila propria"):
    hits = [_hit(rank=r, rights=rights, titulo=titulo,
                 raw_text=f"conteudo numero {r}") for r in range(1, n + 1)]
    return ContextBuilder().build(hits, texts=_textos(hits))


async def _publica(payload=None, contexto=None, provider_erro=False, **kwargs):
    provider = _Roteiro(
        payload if payload is not None
        else {"answer": "A resposta. [E1]", "used_evidence": ["E1"],
              "sufficient": True},
        erro=ProviderTimeoutError("estourou") if provider_erro else None,
    )
    r = await GroundedAnswerer(provider=provider).answer(
        "pergunta", contexto if contexto is not None else _ctx(), **kwargs)
    return r, to_public(r)


class ShapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_public_answer_has_exactly_three_fields(self):
        _, p = await _publica()
        self.assertEqual(set(p.payload()), PUBLIC_FIELDS)
        self.assertEqual(PUBLIC_FIELDS,
                         {"answer_text", "outcome", "unavailable_reason"})

    async def test_the_dataclass_itself_has_only_those_fields(self):
        import dataclasses

        nomes = {f.name for f in dataclasses.fields(PublicAnswer)}
        self.assertEqual(nomes, PUBLIC_FIELDS)

    def test_no_public_field_name_is_an_admin_field_name(self):
        self.assertEqual(PUBLIC_FIELDS & ADMIN_ONLY_FIELDS, set())

    async def test_the_payload_is_json_serialisable_as_is(self):
        _, p = await _publica()
        json.loads(json.dumps(p.payload()))


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_deliverable_answer_carries_the_stripped_text(self):
        r, p = await _publica({"answer": "A concentração é n/V. [E1] [E2]",
                               "used_evidence": ["E1", "E2"],
                               "sufficient": True})
        self.assertTrue(r.deliverable)
        self.assertEqual(p.outcome, ANSWERED)
        self.assertEqual(p.answer_text, "A concentração é n/V.")
        self.assertIsNone(p.unavailable_reason)

    async def test_an_undeclared_sufficiency_is_still_delivered(self):
        r, p = await _publica({"answer": "A resposta. [E1]",
                               "used_evidence": ["E1"]})
        self.assertTrue(r.deliverable)
        self.assertEqual(p.outcome, ANSWERED)

    async def test_denied_sufficiency_delivers_nothing(self):
        """Caso real de N9 e N10: o modelo recusou e citou corretamente."""
        r, p = await _publica({"answer": "Não é possível determinar. [E1]",
                               "used_evidence": ["E1"],
                               "sufficient": False})
        self.assertEqual(p.outcome, UNAVAILABLE)
        self.assertIsNone(p.answer_text)
        self.assertEqual(p.unavailable_reason, NO_ANSWER_FROM_CORPUS)

    async def test_an_invalid_citation_delivers_nothing(self):
        _, p = await _publica({"answer": "Inventado [E9].",
                               "used_evidence": ["E9"]})
        self.assertIsNone(p.answer_text)
        self.assertEqual(p.unavailable_reason, NO_ANSWER_FROM_CORPUS)

    async def test_an_answer_without_citation_delivers_nothing(self):
        _, p = await _publica({"answer": "Sem citar nada.",
                               "used_evidence": []})
        self.assertIsNone(p.answer_text)
        self.assertEqual(p.unavailable_reason, NO_ANSWER_FROM_CORPUS)

    async def test_no_evidence_delivers_nothing(self):
        _, p = await _publica(contexto=ContextBuilder().build([], texts={}))
        self.assertIsNone(p.answer_text)
        self.assertEqual(p.unavailable_reason, NO_ANSWER_FROM_CORPUS)

    async def test_a_sanitization_artifact_delivers_nothing(self):
        _, p = await _publica({"answer": "Segundo [E1], o valor sobe.",
                               "used_evidence": ["E1"], "sufficient": True})
        self.assertIsNone(p.answer_text)
        self.assertEqual(p.unavailable_reason, TEMPORARILY_UNAVAILABLE)

    async def test_provider_failure_is_temporary_not_corpus(self):
        _, p = await _publica({}, provider_erro=True)
        self.assertEqual(p.unavailable_reason, TEMPORARILY_UNAVAILABLE)

    async def test_degraded_retrieval_is_temporary_not_corpus(self):
        _, p = await _publica(retrieval_degraded=True)
        self.assertEqual(p.unavailable_reason, TEMPORARILY_UNAVAILABLE)

    async def test_the_reason_vocabulary_has_exactly_two_values(self):
        from agente_ia_edu.services.knowledge_engine import public_answer

        self.assertEqual(public_answer.UNAVAILABLE_REASONS,
                         (NO_ANSWER_FROM_CORPUS, TEMPORARILY_UNAVAILABLE))


class CollapseTests(unittest.IsolatedAsyncioTestCase):
    """Estados internos distintos tem de sair IGUAIS la fora."""

    async def test_three_different_corpus_failures_look_identical(self):
        casos = [
            ({"answer": "Inventado [E9].", "used_evidence": ["E9"]}, {}),
            ({"answer": "Sem citar.", "used_evidence": []}, {}),
            ({"answer": "Recuso. [E1]", "used_evidence": ["E1"],
              "sufficient": False}, {}),
        ]
        saidas = []
        for payload, kwargs in casos:
            _, p = await _publica(payload, **kwargs)
            saidas.append(p.payload())
        self.assertEqual(saidas[0], saidas[1])
        self.assertEqual(saidas[1], saidas[2])

    async def test_the_internal_states_really_were_different(self):
        """Se os estados internos fossem iguais, o teste acima nao
        provaria nada."""
        r1, _ = await _publica({"answer": "Inventado [E9].",
                                "used_evidence": ["E9"]})
        r2, _ = await _publica({"answer": "Recuso. [E1]",
                                "used_evidence": ["E1"],
                                "sufficient": False})
        self.assertNotEqual(r1.delivery_block_reason, r2.delivery_block_reason)


#: Cada dado administrativo recebe uma string unica e improvavel. Se
#: QUALQUER uma aparecer na saida publica, houve vazamento - inclusive
#: aninhada sob uma chave com outro nome.
LITERAL = "ZZQX-LITERAL-COMERCIAL-SECRETO"
TITULO = "ZZQX-TITULO-DA-OBRA"
ARQUIVO = "ZZQX-NOME-DO-ARQUIVO.pdf"


class SentinelLeakTests(unittest.IsolatedAsyncioTestCase):
    async def _montar(self, payload=None):
        hits = [_hit(rank=r, rights="COMMERCIAL_REFERENCE", titulo=TITULO,
                     raw_text=f"{LITERAL} trecho {r}")
                for r in range(1, 4)]
        for h in hits:
            h.document_filename = ARQUIVO
        contexto = ContextBuilder().build(hits, texts=_textos(hits))
        return await _publica(
            payload or {"answer": "Resposta limpa. [E1] [E2]",
                        "used_evidence": ["[E1]", "[E2]"],
                        "sufficient": True},
            contexto=contexto)

    async def test_no_sentinel_reaches_the_public_payload(self):
        r, p = await self._montar()
        blob = json.dumps(p.payload(), ensure_ascii=False)
        for sentinela in (LITERAL, TITULO, ARQUIVO):
            with self.subTest(sentinela=sentinela):
                self.assertNotIn(sentinela, blob)

    async def test_the_sentinels_really_are_in_the_admin_payload(self):
        """Senao o teste acima passaria por nao haver o que vazar."""
        r, _ = await self._montar()
        blob = json.dumps(r.admin_payload(), ensure_ascii=False, default=str)
        self.assertIn(TITULO, blob)
        self.assertIn(ARQUIVO, blob)
        # o literal comercial nao esta nem no ADMIN - direitos continuam
        self.assertNotIn(LITERAL, blob)

    async def test_no_chunk_id_or_text_hash_reaches_the_public_payload(self):
        r, p = await self._montar()
        blob = json.dumps(p.payload(), ensure_ascii=False)
        for e in r.cited_evidences:
            with self.subTest(marker=e.marker):
                self.assertNotIn(str(e.chunk_id), blob)
                self.assertNotIn(e.text_hash, blob)

    async def test_no_marker_reaches_the_public_payload(self):
        r, p = await self._montar()
        blob = json.dumps(p.payload(), ensure_ascii=False)
        self.assertNotRegex(blob, r"\[E\d+\]")
        self.assertNotIn("E1", blob)
        self.assertNotIn("E2", blob)

    async def test_no_admin_field_NAME_reaches_the_public_payload(self):
        _, p = await self._montar()
        blob = json.dumps(p.payload(), ensure_ascii=False)
        for campo in sorted(ADMIN_ONLY_FIELDS):
            with self.subTest(campo=campo):
                self.assertNotIn(f'"{campo}"', blob)

    async def test_the_leak_check_walks_nested_structures(self):
        """Uma sentinela escondida em lista dentro de dict tem de ser
        achada - e por isso a verificacao e sobre a serializacao INTEIRA,
        nao sobre as chaves de primeiro nivel."""
        _, p = await self._montar()
        plano = json.dumps(p.payload(), ensure_ascii=False)
        self.assertEqual(plano.count(LITERAL), 0)
        # a propria estrutura e rasa de proposito
        self.assertTrue(all(not isinstance(v, (dict, list))
                            for v in p.payload().values()))

    async def test_a_model_echoing_admin_metadata_is_still_not_delivered_raw(
        self,
    ):
        """Se o modelo copiar o titulo da obra para a resposta, ele sai -
        a resposta E a saida. Mas marcador e rastreabilidade continuam
        fora, e o ADMIN enxerga a diferenca."""
        r, p = await self._montar(
            {"answer": f"Conforme consta em {TITULO}. [E1]",
             "used_evidence": ["E1"], "sufficient": True})
        self.assertIn(TITULO, p.answer_text)
        self.assertNotIn("[E1]", p.answer_text)
        self.assertNotIn(ARQUIVO, json.dumps(p.payload()))


class ImmutabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_public_answer_is_frozen(self):
        import dataclasses

        _, p = await _publica()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            p.answer_text = "outra coisa"

    async def test_to_public_does_not_mutate_the_grounded_answer(self):
        r, _ = await _publica()
        antes = r.admin_payload()
        to_public(r)
        self.assertEqual(r.admin_payload(), antes)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
