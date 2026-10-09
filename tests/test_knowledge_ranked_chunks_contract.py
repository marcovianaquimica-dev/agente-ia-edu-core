"""O contrato minimo, identico nas duas pernas. Fase 7, passo 1.

``ranked_chunks()`` e o que o fundidor da Fase 7 vai consumir. Se as duas
pernas nao o oferecerem com a MESMA forma, o fundidor passa a conhecer
``LexicalHit`` e ``VectorHit`` - e com eles excerpt, direitos e paginas, que
nao sao assunto de fusao. A simetria aqui e o que mantem a fronteira.

Estes testes NAO exercitam fusao alguma: ela nao existe ainda.
"""

from __future__ import annotations

import inspect
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
from agente_ia_edu.services.knowledge_engine.embedding import coverage
from agente_ia_edu.services.knowledge_engine.lexical_index import LexicalIndexService
from agente_ia_edu.services.knowledge_engine.lexical_search import LexicalSearcher
from agente_ia_edu.services.knowledge_engine.vector_search import VectorSearcher

from test_knowledge_vector_search import _ScriptedProvider

_DIMS = 3
_QUERY = "diluicao das solucoes aquosas"


class SymmetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=True
        )
        async with self.factory() as session:
            fonte = KnowledgeSource(
                title="Apostila", source_kind="OWN_MATERIAL",
                rights_class="OWN", authority_level="OWN",
            )
            session.add(fonte)
            await session.flush()
            documento = KnowledgeDocument(
                source_id=fonte.id, filename="a.pdf",
                storage_uri="/tmp/a.pdf", document_hash="h" * 64,
            )
            session.add(documento)
            await session.flush()
            espaco = KnowledgeEmbeddingSpace(
                provider="fake", model="modelo-a", dimensions=_DIMS,
                distance_metric="cosine", status="ACTIVE",
            )
            session.add(espaco)
            await session.flush()
            self.space_id = espaco.id

            textos = [
                "A diluicao reduz a concentracao das solucoes aquosas.",
                "A concentracao das solucoes se mede em mol por litro.",
                "O preparo de solucoes usa balao volumetrico.",
            ]
            vetores = [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0], [0.0, 1.0, 0.0]]
            chunks = []
            for ordinal, (texto, vetor) in enumerate(zip(textos, vetores), 1):
                chunk = KnowledgeChunk(
                    source_id=fonte.id, document_id=documento.id,
                    ordinal=ordinal, chunk_type="PROSE", heading_path=["Cap 1"],
                    page_start=ordinal, page_end=ordinal, raw_text=texto,
                    text_hash=retrieval_text_hash(
                        raw_text=texto, heading_path=["Cap 1"]
                    ),
                    char_count=len(texto), editorial_role="CONTENT",
                    editorial_detector_version="v1", bncc_node_codes=[],
                )
                session.add(chunk)
                await session.flush()
                chunks.append(chunk)
                session.add(
                    KnowledgeChunkEmbedding(
                        chunk_id=chunk.id, space_id=espaco.id, embedding=vetor,
                        text_hash=chunk.text_hash, is_active=True,
                    )
                )
            await LexicalIndexService(session).index_chunks(chunks)
            await session.commit()

        async with self.factory() as session:
            elegiveis = (await coverage(session, self.space_id)).eligible_chunks
            session.add(
                KnowledgeEmbeddingActivation(
                    space_id=self.space_id, action="ACTIVATE",
                    policy="ELIGIBLE_ROLES", expected_population=elegiveis,
                    eligible_chunks=elegiveis, embedded=elegiveis, missing=0,
                    stale=0, dimension_violations=0, activated_rows=elegiveis,
                    deactivated_rows=0, degraded=False, violations=[],
                )
            )
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def test_both_legs_expose_the_same_signature(self):
        lexical = inspect.signature(LexicalSearcher.ranked_chunks)
        vetorial = inspect.signature(VectorSearcher.ranked_chunks)
        self.assertEqual(
            list(lexical.parameters), list(vetorial.parameters)
        )

    async def test_the_lexical_leg_returns_the_four_tuple(self):
        async with self.factory() as session:
            ranked = await LexicalSearcher(session).ranked_chunks(
                _QUERY, retrieval_purpose="LEARN", limit=10
            )
        self.assertTrue(ranked)
        for chunk_id, rank, score, explicacao in ranked:
            self.assertIsInstance(chunk_id, uuid.UUID)
            self.assertIsInstance(rank, int)
            self.assertIsInstance(score, float)
            self.assertIsInstance(explicacao, dict)

    async def test_both_legs_return_the_same_shape(self):
        provedor = _ScriptedProvider({_QUERY: (1.0, 0.0, 0.0)})
        async with self.factory() as session:
            lexical = await LexicalSearcher(session).ranked_chunks(
                _QUERY, retrieval_purpose="LEARN", limit=10
            )
        async with self.factory() as session:
            vetorial = await VectorSearcher(
                session, provider=provedor
            ).ranked_chunks(_QUERY, retrieval_purpose="LEARN", limit=10)
        for lado in (lexical, vetorial):
            self.assertTrue(lado)
            self.assertTrue(all(len(item) == 4 for item in lado))
        self.assertEqual(
            [type(x) for x in lexical[0]], [type(x) for x in vetorial[0]]
        )

    async def test_ranks_are_dense_and_start_at_one(self):
        provedor = _ScriptedProvider({_QUERY: (1.0, 0.0, 0.0)})
        async with self.factory() as session:
            lexical = await LexicalSearcher(session).ranked_chunks(
                _QUERY, retrieval_purpose="LEARN", limit=10
            )
        async with self.factory() as session:
            vetorial = await VectorSearcher(
                session, provider=provedor
            ).ranked_chunks(_QUERY, retrieval_purpose="LEARN", limit=10)
        for lado in (lexical, vetorial):
            ranks = [item[1] for item in lado]
            self.assertEqual(ranks, list(range(1, len(ranks) + 1)))

    async def test_the_shortcut_agrees_with_the_full_search(self):
        """``ranked_chunks`` e atalho, nao segunda implementacao."""
        async with self.factory() as session:
            buscador = LexicalSearcher(session)
            completo = await buscador.search(
                _QUERY, retrieval_purpose="LEARN", limit=10
            )
            ranked = await buscador.ranked_chunks(
                _QUERY, retrieval_purpose="LEARN", limit=10
            )
        self.assertEqual(
            [(h.chunk_id, h.rank, h.score) for h in completo.hits],
            [(c, r, s) for c, r, s, _ in ranked],
        )

    async def test_the_score_scales_are_not_comparable_between_legs(self):
        """Fixa a razao de a fusao da Fase 7 usar RANK e nao score.

        BM25F e nao-limitado; cosseno vive em [-1, 1]. Somar os dois seria
        deixar a perna lexical dominar por escala, nao por relevancia.
        """
        provedor = _ScriptedProvider({_QUERY: (1.0, 0.0, 0.0)})
        async with self.factory() as session:
            lexical = await LexicalSearcher(session).ranked_chunks(
                _QUERY, retrieval_purpose="LEARN", limit=10
            )
        async with self.factory() as session:
            vetorial = await VectorSearcher(
                session, provider=provedor
            ).ranked_chunks(_QUERY, retrieval_purpose="LEARN", limit=10)
        self.assertGreater(lexical[0][2], 1.0)
        self.assertLessEqual(vetorial[0][2], 1.0)

    async def test_no_fusion_exists_yet(self):
        """O passo 1 nao implementa fusao. Se este teste comecar a falhar, foi
        porque alguem antecipou a implementacao sem autorizacao."""
        from pathlib import Path

        raiz = (
            Path(__file__).resolve().parents[1]
            / "src/agente_ia_edu/services/knowledge_engine"
        )
        for nome in ("hybrid_search.py", "rank_fusion.py", "fusion.py"):
            self.assertFalse((raiz / nome).exists(), nome)


if __name__ == "__main__":
    unittest.main()
