"""CEREBRO - o caminho ponta a ponta, inteiro, com FakeProvider.

    pergunta -> VectorSearcher -> ContextBuilder -> GroundedAnswerer

Os passos 1 a 4 do plano rodam aqui, sem um centavo gasto. O que o provider
real acrescenta no passo 5 e a qualidade do texto, nao o caminho - e o
caminho e o que pode quebrar em silencio.
"""

from __future__ import annotations

import json
import unittest
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeDocument,
    KnowledgeEmbeddingActivation,
    KnowledgeEmbeddingSpace,
    KnowledgeSource,
)
from agente_ia_edu.knowledge_chunking_policy.v1 import retrieval_text_hash
from agente_ia_edu.providers.models import TextGenerationResult
from agente_ia_edu.services.knowledge_engine.context_builder import (
    ContextBuilder,
)
from agente_ia_edu.services.knowledge_engine.embedding import coverage
from agente_ia_edu.services.knowledge_engine.grounded_answer import (
    GROUNDED, NO_EVIDENCE, GroundedAnswerer,
)
from agente_ia_edu.services.knowledge_engine.vector_search import VectorSearcher

from test_knowledge_vector_search import _ScriptedProvider

_DIMS = 3
_PERGUNTA = "como se mede a concentracao de uma solucao"

#: Corpus minusculo: uma obra comercial e uma propria, para que a politica
#: de direitos seja exercitada no caminho real e nao so em unitario.
_CORPUS = [
    ("comercial", [1.0, 0.0, 0.0], "PROSE", "CONTENT",
     "A concentracao em quantidade de materia e a razao entre mol e volume."),
    ("comercial", [0.9, 0.1, 0.0], "EXERCISE", "CONTENT",
     "Calcule a concentracao de uma solucao com 2 mol em 4 litros."),
    ("propria", [0.8, 0.2, 0.0], "PROSE", "CONTENT",
     "Para preparar a solucao usa-se balao volumetrico."),
    ("comercial", [0.0, 1.0, 0.0], "PROSE", "TABLE_OF_CONTENTS",
     "Sumario ....... 42"),
]


class _Gerador:
    provider = "fake"

    def __init__(self, payload):
        self._payload = payload
        self.prompts = []

    async def generate(self, request):
        self.prompts.append(request.prompt)
        return TextGenerationResult(
            text=json.dumps(self._payload), provider="fake",
            model=request.model or "m", input_tokens=200, output_tokens=60,
        )


class EndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=True
        )
        self.literal_comercial = (
            "A concentracao em quantidade de materia e a razao entre mol e "
            "volume."
        )
        async with self.factory() as s:
            fontes = {}
            for chave, direitos, autoridade, tipo, titulo in (
                ("comercial", "COMMERCIAL_REFERENCE", "COMMERCIAL_TEXTBOOK",
                 "TEXTBOOK", "Livro comercial"),
                ("propria", "OWN", "OWN", "OWN_MATERIAL", "Apostila propria"),
            ):
                fonte = KnowledgeSource(
                    title=titulo, source_kind=tipo,
                    rights_class=direitos, authority_level=autoridade,
                )
                s.add(fonte)
                await s.flush()
                doc = KnowledgeDocument(
                    source_id=fonte.id, filename=f"{chave}.pdf",
                    storage_uri=f"/tmp/{chave}.pdf",
                    document_hash=uuid.uuid4().hex * 2,
                )
                s.add(doc)
                await s.flush()
                fontes[chave] = (fonte.id, doc.id)
            espaco = KnowledgeEmbeddingSpace(
                provider="fake", model="modelo-a", dimensions=_DIMS,
                distance_metric="cosine", status="ACTIVE",
            )
            s.add(espaco)
            await s.flush()
            self.space_id = espaco.id
            for ordinal, (chave, vetor, tipo, papel, texto) in enumerate(
                _CORPUS, start=1
            ):
                fonte_id, doc_id = fontes[chave]
                chunk = KnowledgeChunk(
                    source_id=fonte_id, document_id=doc_id, ordinal=ordinal,
                    chunk_type=tipo, heading_path=["Cap 6"],
                    page_start=100 + ordinal, page_end=100 + ordinal,
                    raw_text=texto,
                    text_hash=retrieval_text_hash(
                        raw_text=texto, heading_path=["Cap 6"]
                    ),
                    char_count=len(texto), editorial_role=papel,
                    editorial_detector_version="v1", bncc_node_codes=[],
                )
                s.add(chunk)
                await s.flush()
                if papel != "TABLE_OF_CONTENTS":
                    s.add(KnowledgeChunkEmbedding(
                        chunk_id=chunk.id, space_id=espaco.id,
                        embedding=vetor, text_hash=chunk.text_hash,
                        is_active=True,
                    ))
            await s.commit()
        async with self.factory() as s:
            elegiveis = (await coverage(s, self.space_id)).eligible_chunks
            s.add(KnowledgeEmbeddingActivation(
                space_id=self.space_id, action="ACTIVATE",
                policy="ELIGIBLE_ROLES", expected_population=elegiveis,
                eligible_chunks=elegiveis, embedded=elegiveis, missing=0,
                stale=0, dimension_violations=0, activated_rows=elegiveis,
                deactivated_rows=0, degraded=False, violations=[],
            ))
            await s.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _fluxo(self, gerador, pergunta=_PERGUNTA, **kwargs):
        provedor = _ScriptedProvider({pergunta: (1.0, 0.0, 0.0)})
        async with self.factory() as s:
            busca = await VectorSearcher(s, provider=provedor).search(
                pergunta, retrieval_purpose="LEARN", limit=10, **kwargs
            )
        async with self.factory() as s:
            textos = dict((await s.execute(
                select(KnowledgeChunk.id, KnowledgeChunk.raw_text).where(
                    KnowledgeChunk.id.in_([h.chunk_id for h in busca.hits])
                )
            )).all())
        contexto = ContextBuilder().build(busca.hits, texts=textos)
        resposta = await GroundedAnswerer(provider=gerador).answer(
            pergunta, contexto,
            retrieval_degraded=busca.degraded,
            degradation_reasons=busca.degradation_reasons,
        )
        return busca, contexto, resposta

    async def test_the_whole_path_produces_a_grounded_answer(self):
        gerador = _Gerador({
            "answer": "A concentracao e mol por litro [E1], e o preparo usa "
                      "balao volumetrico [E3].",
            "used_evidence": ["E1", "E3"], "sufficient": True,
        })
        busca, contexto, resposta = await self._fluxo(gerador)
        self.assertTrue(busca.hits)
        self.assertTrue(contexto.evidences)
        self.assertEqual(resposta.status, GROUNDED)
        self.assertTrue(resposta.is_grounded)

    async def test_the_navigation_apparatus_never_reaches_the_context(self):
        """O sumario nem tem vetor, e a politica editorial o barraria de
        qualquer forma. Confere que as duas travas valem no caminho real."""
        gerador = _Gerador({"answer": "x [E1]", "used_evidence": ["E1"]})
        _, contexto, _ = await self._fluxo(gerador)
        self.assertNotIn(
            "TABLE_OF_CONTENTS",
            [e.editorial_role for e in contexto.evidences],
        )

    async def test_no_commercial_literal_in_any_public_artifact(self):
        """A varredura que importa: o literal esta no prompt e em lugar
        nenhum do que seria impresso, logado ou gravado."""
        gerador = _Gerador({
            "answer": "Conforme [E1], a concentracao e mol por litro.",
            "used_evidence": ["E1"], "sufficient": True,
        })
        busca, contexto, resposta = await self._fluxo(gerador)

        # Esta no prompt - e precisa estar.
        self.assertIn(self.literal_comercial, contexto.prompt_payload())
        self.assertIn(self.literal_comercial, gerador.prompts[0])

        # E em nenhum artefato publico.
        publico = json.dumps({
            "retrieval": [
                {"rank": h.rank, "chunk_id": str(h.chunk_id),
                 "source": h.source_title, "page": h.page_start,
                 "excerpt": h.excerpt}
                for h in busca.hits
            ],
            "context": contexto.public_payload(),
            "answer": resposta.public_payload(),
        }, default=str)
        self.assertNotIn(self.literal_comercial, publico)
        self.assertNotIn(self.literal_comercial[:30], publico)
        self.assertNotIn(self.literal_comercial[:30], repr(contexto))
        self.assertNotIn(self.literal_comercial[:30], repr(resposta))

    async def test_traceability_reaches_the_printed_page(self):
        gerador = _Gerador({"answer": "[E1]", "used_evidence": ["E1"],
                            "sufficient": True})
        _, _, resposta = await self._fluxo(gerador)
        citada = resposta.cited_evidences[0]
        self.assertIsNotNone(citada.page_start)
        self.assertIsNotNone(citada.chunk_id)
        self.assertEqual(len(citada.text_hash), 64)
        self.assertEqual(citada.source_title, "Livro comercial")
        self.assertIsNone(citada.excerpt)  # comercial

    async def test_a_question_with_no_corpus_match_still_behaves(self):
        """Sem evidencia o gerador nem e chamado - e o estado e nomeado."""
        gerador = _Gerador({"answer": "nao deveria", "used_evidence": []})
        async with self.factory() as s:
            await s.execute(KnowledgeChunkEmbedding.__table__.delete())
            await s.commit()
        provedor = _ScriptedProvider({_PERGUNTA: (1.0, 0.0, 0.0)})
        async with self.factory() as s:
            busca = await VectorSearcher(s, provider=provedor).search(
                _PERGUNTA, retrieval_purpose="LEARN", limit=10,
                allow_degraded=True,
            )
        contexto = ContextBuilder().build(busca.hits, texts={})
        resposta = await GroundedAnswerer(provider=gerador).answer(
            _PERGUNTA, contexto
        )
        self.assertEqual(resposta.status, NO_EVIDENCE)
        self.assertEqual(gerador.prompts, [])

    async def test_the_cli_exists_and_declares_the_ten_items(self):
        from pathlib import Path

        cli = (
            Path(__file__).resolve().parents[1] / "scripts" / "cerebro_ask.py"
        )
        self.assertTrue(cli.exists())
        fonte = cli.read_text()
        for marca in ("query_embedding_ms", "retrieval_ms",
                      "context_build_ms", "generation_ms", "total_ms"):
            self.assertIn(marca, fonte, marca)


if __name__ == "__main__":
    unittest.main()
