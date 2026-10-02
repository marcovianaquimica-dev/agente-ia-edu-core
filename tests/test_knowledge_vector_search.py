"""CEREBRO - Fase 6, passo 4: recuperacao vetorial isolada.

Os vetores aqui sao SINTETICOS e escolhidos a mao, para que o ranking
esperado seja calculavel sem rodar nada: o backend em processo e EXATO, e
exatidao num corpus de teste e uma vantagem - o teste afirma um numero em vez
de aceitar o que saiu.

O que o PostgreSQL prova e outra coisa, e esta em
``test_knowledge_vector_search_postgresql.py``: pgvector de verdade, e a
equivalencia de ORDENACAO entre os dois dialetos. Equivalencia de desempenho
nao e afirmada em lugar nenhum.
"""

from __future__ import annotations

import json
import math
import unittest
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeDocument,
    KnowledgeEmbeddingActivation,
    KnowledgeEmbeddingSpace,
    KnowledgeSource,
)
from agente_ia_edu.knowledge_chunking_policy.v1 import retrieval_text_hash
from agente_ia_edu.providers.errors import ProviderRateLimitError
from agente_ia_edu.providers.models import (
    EmbeddingArtifact,
    EmbeddingResult,
)
from agente_ia_edu.services.knowledge_engine.embedding import (
    coverage as corpus_coverage,
)
from agente_ia_edu.services.knowledge_engine.vector_search import (
    EXACT_BACKEND,
    SCORE_SEMANTICS,
    VectorSearcher,
    VectorSearchError,
)

_DIMENSIONS = 3


class _ScriptedProvider:
    """Devolve um vetor fixo por texto de consulta. Nada de rede, nada de
    aleatorio: o ranking esperado tem de ser calculavel a mao."""

    provider = "fake"

    def __init__(self, vectors: dict[str, tuple[float, ...]], *, model_seen=None):
        self._vectors = vectors
        self.models_seen: list[str | None] = [] if model_seen is None else model_seen
        self.texts_seen: list[tuple[str, ...]] = []

    async def embed(self, request):
        self.models_seen.append(request.model)
        self.texts_seen.append(tuple(request.texts))
        vetor = self._vectors[request.texts[0]]
        return EmbeddingResult(
            artifacts=(
                EmbeddingArtifact(
                    canonical_text=request.texts[0],
                    text_hash="q" * 64,
                    vector=tuple(vetor),
                    dimensions=len(vetor),
                    provider=self.provider,
                    model=request.model or "?",
                    generated_at=__import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ),
                ),
            ),
            provider=self.provider,
            model=request.model or "?",
            dimensions=len(vetor),
        )


class _BrokenProvider:
    provider = "fake"

    async def embed(self, request):
        raise ProviderRateLimitError("limite atingido")


class _VectorCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=True
        )
        self._ordinal = 0
        self.sources: dict[str, uuid.UUID] = {}
        self.documents: dict[str, uuid.UUID] = {}
        self.chunks: dict[str, uuid.UUID] = {}
        await self._source(
            "propria", rights_class="OWN", authority_level="OWN",
            source_kind="OWN_MATERIAL", title="Apostila propria",
        )
        self.space_id = await self._space("modelo-a", status="ACTIVE")

    async def asyncTearDown(self):
        await self.engine.dispose()

    # -- montagem do corpus ----------------------------------------------

    async def _source(self, key, *, rights_class, authority_level, source_kind, title):
        async with self.factory() as session:
            source = KnowledgeSource(
                title=title, source_kind=source_kind,
                rights_class=rights_class, authority_level=authority_level,
            )
            session.add(source)
            await session.flush()
            document = KnowledgeDocument(
                source_id=source.id, filename=f"{key}.pdf",
                storage_uri=f"/tmp/{key}.pdf", document_hash=uuid.uuid4().hex * 2,
            )
            session.add(document)
            await session.flush()
            self.sources[key] = source.id
            self.documents[key] = document.id
            await session.commit()

    async def _space(self, model, *, status, dimensions=_DIMENSIONS) -> uuid.UUID:
        async with self.factory() as session:
            space = KnowledgeEmbeddingSpace(
                provider="fake", model=model, dimensions=dimensions,
                distance_metric="cosine", status=status,
            )
            session.add(space)
            await session.flush()
            space_id = space.id
            await session.commit()
        return space_id

    async def _chunk(
        self,
        key: str,
        raw_text: str,
        *,
        source: str = "propria",
        chunk_type: str = "PROSE",
        role: str = "CONTENT",
        content_node_id: uuid.UUID | None = None,
        bncc: list[str] | None = None,
        page: int | None = None,
    ) -> uuid.UUID:
        self._ordinal += 1
        pagina = page if page is not None else self._ordinal + 100
        async with self.factory() as session:
            chunk = KnowledgeChunk(
                source_id=self.sources[source], document_id=self.documents[source],
                ordinal=self._ordinal, chunk_type=chunk_type, heading_path=["Cap 1"],
                page_start=pagina, page_end=pagina, raw_text=raw_text,
                text_hash=retrieval_text_hash(
                    raw_text=raw_text, heading_path=["Cap 1"]
                ),
                char_count=len(raw_text), editorial_role=role,
                editorial_detector_version="v1", content_node_id=content_node_id,
                bncc_node_codes=bncc or [],
            )
            session.add(chunk)
            await session.flush()
            self.chunks[key] = chunk.id
            await session.commit()
        return self.chunks[key]

    async def _vector(
        self,
        key: str,
        vector: list[float],
        *,
        space_id: uuid.UUID | None = None,
        is_active: bool = True,
        text_hash: str | None = None,
    ) -> uuid.UUID:
        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, self.chunks[key])
            row = KnowledgeChunkEmbedding(
                chunk_id=chunk.id, space_id=space_id or self.space_id,
                embedding=vector, text_hash=text_hash or chunk.text_hash,
                is_active=is_active,
            )
            session.add(row)
            await session.flush()
            row_id = row.id
            await session.commit()
        return row_id

    async def _activate(self, *, space_id=None, expected=None, degraded=False):
        """Registra a ativacao que o passo 3 teria registrado.

        A busca le este registro para saber o que foi DECLARADO como corpus
        completo. Sem ele, o espaco e ACTIVE mas nunca foi ligado."""
        space_id = space_id or self.space_id
        async with self.factory() as session:
            if expected is None:
                # A populacao declarada e a ELEGIVEL para embedding, nao o
                # total de chunks: sumario e indice nunca ganham vetor, e
                # declarar o total faria todo espaco nascer "incompleto".
                expected = (
                    await corpus_coverage(session, space_id)
                ).eligible_chunks
            session.add(
                KnowledgeEmbeddingActivation(
                    space_id=space_id, previous_space_id=None, action="ACTIVATE",
                    policy="ELIGIBLE_ROLES", expected_population=expected,
                    eligible_chunks=expected, embedded=expected, missing=0, stale=0,
                    dimension_violations=0, activated_rows=expected,
                    deactivated_rows=0, degraded=degraded, violations=[],
                )
            )
            await session.commit()

    async def _search(self, query="consulta", *, vectors=None, provider=None, **kwargs):
        provider = provider or _ScriptedProvider(
            vectors or {query: (1.0, 0.0, 0.0)}
        )
        async with self.factory() as session:
            return await VectorSearcher(session, provider=provider).search(
                query, **kwargs
            )


class CosineRankingTests(_VectorCase):
    """Ranking por cosseno CONHECIDO: os angulos foram escolhidos a mao."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # Consulta = (1,0,0). Os tres vetores dao, por construcao:
        #   identico   cos = 1      -> distancia 0
        #   45 graus   cos = 0,7071 -> distancia 0,2929
        #   ortogonal  cos = 0      -> distancia 1
        #   oposto     cos = -1     -> distancia 2
        await self._chunk("identico", "Texto A sobre diluicao.")
        await self._chunk("quarenta_e_cinco", "Texto B sobre diluicao.")
        await self._chunk("ortogonal", "Texto C sobre diluicao.")
        await self._chunk("oposto", "Texto D sobre diluicao.")
        await self._vector("identico", [1.0, 0.0, 0.0])
        await self._vector("quarenta_e_cinco", [1.0, 1.0, 0.0])
        await self._vector("ortogonal", [0.0, 1.0, 0.0])
        await self._vector("oposto", [-1.0, 0.0, 0.0])
        await self._activate()

    async def test_the_ranking_follows_the_known_cosine_order(self):
        resultado = await self._search()
        self.assertEqual(
            [hit.chunk_id for hit in resultado.hits],
            [
                self.chunks["identico"],
                self.chunks["quarenta_e_cinco"],
                self.chunks["ortogonal"],
                self.chunks["oposto"],
            ],
        )
        self.assertEqual([hit.rank for hit in resultado.hits], [1, 2, 3, 4])

    async def test_the_scores_are_the_cosines_themselves(self):
        resultado = await self._search()
        esperados = [1.0, math.sqrt(2) / 2, 0.0, -1.0]
        for hit, esperado in zip(resultado.hits, esperados):
            self.assertAlmostEqual(hit.score, esperado, places=9)

    async def test_score_is_similarity_and_distance_is_its_complement(self):
        """A convencao esta DECLARADA, nunca inferida: misturar distancia e
        similaridade entre as duas pernas produziria um relatorio em que
        'maior e melhor' vale para uma e nao para a outra."""
        resultado = await self._search()
        self.assertEqual(resultado.score_semantics, SCORE_SEMANTICS)
        for hit in resultado.hits:
            self.assertAlmostEqual(hit.score + hit.distance, 1.0, places=12)
        scores = [hit.score for hit in resultado.hits]
        self.assertEqual(scores, sorted(scores, reverse=True))

    async def test_a_non_unit_vector_is_scored_by_real_cosine(self):
        """Nada aqui presume vetor unitario: (1,1,0) tem norma 1,41 e mesmo
        assim pontua exatamente cos 45."""
        resultado = await self._search()
        quarenta_e_cinco = resultado.hits[1]
        self.assertAlmostEqual(quarenta_e_cinco.score, math.sqrt(2) / 2, places=9)

    async def test_no_similarity_threshold_is_applied(self):
        """Ranking, nao abstencao. O vetor OPOSTO - o mais distante possivel -
        continua no resultado. Limiar e assunto do Calibration Set."""
        resultado = await self._search()
        self.assertEqual(resultado.returned, 4)
        self.assertLess(resultado.hits[-1].score, 0)

    async def test_pagination_is_stable_across_pages(self):
        primeira = await self._search(limit=2, offset=0)
        segunda = await self._search(limit=2, offset=2)
        self.assertTrue(primeira.has_more)
        self.assertFalse(segunda.has_more)
        self.assertEqual(
            [hit.chunk_id for hit in primeira.hits + segunda.hits],
            [
                self.chunks["identico"],
                self.chunks["quarenta_e_cinco"],
                self.chunks["ortogonal"],
                self.chunks["oposto"],
            ],
        )
        self.assertEqual([hit.rank for hit in segunda.hits], [3, 4])

    async def test_the_fase_7_contract_is_rank_score_and_explanation(self):
        provider = _ScriptedProvider({"consulta": (1.0, 0.0, 0.0)})
        async with self.factory() as session:
            ranked = await VectorSearcher(session, provider=provider).ranked_chunks(
                "consulta"
            )
        self.assertEqual(len(ranked), 4)
        chunk_id, rank, score, explanation = ranked[0]
        self.assertEqual(chunk_id, self.chunks["identico"])
        self.assertEqual(rank, 1)
        self.assertAlmostEqual(score, 1.0, places=9)
        self.assertEqual(explanation["score_semantics"], SCORE_SEMANTICS)


class DeterministicTieTests(_VectorCase):
    async def test_an_exact_tie_is_broken_by_an_explicit_secondary_key(self):
        """Dois chunks com o MESMO vetor existem - o mesmo enunciado em duas
        obras. Sem criterio secundario a ordem viria da varredura do banco."""
        await self._source(
            "outra", rights_class="OWN", authority_level="OWN",
            source_kind="OWN_MATERIAL", title="Outra apostila",
        )
        await self._chunk("a", "Texto empatado.", source="propria")
        await self._chunk("b", "Texto empatado.", source="outra")
        await self._vector("a", [1.0, 0.0, 0.0])
        await self._vector("b", [1.0, 0.0, 0.0])
        await self._activate()

        resultado = await self._search()
        self.assertEqual(resultado.hits[0].distance, resultado.hits[1].distance)
        # Criterio: (distancia, source_id, document_id, ordinal, chunk_id).
        esperado = sorted(
            [self.chunks["a"], self.chunks["b"]],
            key=lambda chunk_id: str(
                self.sources["propria" if chunk_id == self.chunks["a"] else "outra"]
            ),
        )
        self.assertEqual([hit.chunk_id for hit in resultado.hits], esperado)

    async def test_the_tie_order_is_identical_across_repeated_searches(self):
        await self._chunk("a", "Primeiro texto.")
        await self._chunk("b", "Segundo texto.")
        await self._chunk("c", "Terceiro texto.")
        for key in ("a", "b", "c"):
            await self._vector(key, [1.0, 0.0, 0.0])
        await self._activate()

        ordens = set()
        for _ in range(5):
            resultado = await self._search()
            ordens.add(tuple(str(hit.chunk_id) for hit in resultado.hits))
        self.assertEqual(len(ordens), 1)


class ActiveSpaceTests(_VectorCase):
    async def test_there_is_no_search_without_an_active_space(self):
        async with self.factory() as session:
            space = await session.get(KnowledgeEmbeddingSpace, self.space_id)
            space.status = "RETIRED"
            await session.commit()
        resultado = await self._search()
        self.assertEqual(resultado.empty_reasons, ("NO_ACTIVE_EMBEDDING_SPACE",))
        self.assertIsNone(resultado.embedding_space_id)
        self.assertEqual(resultado.hits, ())

    async def test_only_vectors_of_the_active_space_are_searched(self):
        """Isolamento entre espacos. Um vetor de outro espaco, mesmo ATIVO -
        estado que a trava do passo 3 impediria, mas aqui forcado -, nao
        entra: distancias de espacos diferentes nao sao comparaveis."""
        outro = await self._space("modelo-b", status="BACKFILLING")
        await self._chunk("a", "Texto do espaco ativo.")
        await self._chunk("b", "Texto do outro espaco.")
        await self._vector("a", [1.0, 0.0, 0.0])
        await self._vector("b", [1.0, 0.0, 0.0], space_id=outro)
        await self._activate(expected=2)

        # O chunk "b" TEM vetor ativo - so que noutro espaco. E o unico
        # arranjo que a trava de um ativo por chunk permite para provar o
        # isolamento, e ele e, por natureza, um corpus incompleto.
        resultado = await self._search(allow_degraded=True)
        self.assertEqual(
            [hit.chunk_id for hit in resultado.hits], [self.chunks["a"]]
        )
        self.assertEqual(resultado.embedding_space_id, self.space_id)
        self.assertIn("MISSING_EMBEDDINGS", resultado.degradation_reasons)

    async def test_only_active_embeddings_are_searched(self):
        await self._chunk("ligado", "Texto ligado.")
        await self._chunk("desligado", "Texto desligado.")
        await self._vector("ligado", [1.0, 0.0, 0.0])
        await self._vector("desligado", [1.0, 0.0, 0.0], is_active=False)
        await self._activate(expected=2)

        resultado = await self._search(allow_degraded=True)
        self.assertEqual(
            [hit.chunk_id for hit in resultado.hits], [self.chunks["ligado"]]
        )

    async def test_a_stale_vector_is_never_retrieved_even_when_active(self):
        """``is_active`` nao basta: o texto pode ter mudado DEPOIS da
        ativacao. Um vetor que representa um texto que ja nao existe nao e
        resposta - e nem mesmo em busca degradada ele aparece."""
        await self._chunk("atual", "Texto atual.")
        await self._chunk("mudou", "Texto que vai mudar.")
        await self._vector("atual", [1.0, 0.0, 0.0])
        await self._vector("mudou", [1.0, 0.0, 0.0])
        await self._activate(expected=2)
        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, self.chunks["mudou"])
            chunk.raw_text = "Texto revisado depois da ativacao."
            chunk.text_hash = retrieval_text_hash(
                raw_text=chunk.raw_text, heading_path=["Cap 1"]
            )
            await session.commit()

        resultado = await self._search(allow_degraded=True)
        self.assertEqual(
            [hit.chunk_id for hit in resultado.hits], [self.chunks["atual"]]
        )
        self.assertIn("STALE_EMBEDDINGS", resultado.degradation_reasons)

    async def test_each_chunk_appears_at_most_once(self):
        await self._chunk("a", "Texto A.")
        await self._chunk("b", "Texto B.")
        await self._vector("a", [1.0, 0.0, 0.0])
        await self._vector("b", [0.0, 1.0, 0.0])
        await self._activate(expected=2)
        resultado = await self._search()
        ids = [hit.chunk_id for hit in resultado.hits]
        self.assertEqual(len(ids), len(set(ids)))


class CoverageGateTests(_VectorCase):
    async def test_an_active_space_that_was_never_activated_refuses_the_search(self):
        """A migracao 058 deixa o espaco inicial ACTIVE e sem vetor ligado.
        Buscar nele devolveria silencio com cara de 'nada encontrado'."""
        await self._chunk("a", "Texto A.")
        await self._vector("a", [1.0, 0.0, 0.0])
        with self.assertRaises(VectorSearchError) as caught:
            await self._search()
        self.assertEqual(caught.exception.code, "INCOMPLETE_COVERAGE")

    async def test_a_partial_corpus_refuses_the_search_by_default(self):
        """O requisito central deste passo: ranking parcial NAO passa por
        corpus completo."""
        await self._chunk("com_vetor", "Texto com vetor.")
        await self._chunk("sem_vetor", "Texto SEM vetor.")
        await self._vector("com_vetor", [1.0, 0.0, 0.0])
        await self._activate(expected=2)
        with self.assertRaises(VectorSearchError) as caught:
            await self._search()
        self.assertEqual(caught.exception.code, "INCOMPLETE_COVERAGE")
        self.assertIn("allow_degraded", str(caught.exception))

    async def test_degraded_search_is_opt_in_and_marked(self):
        await self._chunk("com_vetor", "Texto com vetor.")
        await self._chunk("sem_vetor", "Texto SEM vetor.")
        await self._vector("com_vetor", [1.0, 0.0, 0.0])
        await self._activate(expected=2)

        resultado = await self._search(allow_degraded=True)
        self.assertTrue(resultado.degraded)
        self.assertIn("MISSING_EMBEDDINGS", resultado.degradation_reasons)
        self.assertEqual(resultado.coverage["missing"], 1)
        self.assertEqual(resultado.coverage["eligible_chunks"], 2)
        self.assertTrue(all(hit.explanation["degraded"] for hit in resultado.hits))

    async def test_a_shrunken_population_is_a_degradation_too(self):
        """Cobertura e uma RAZAO: 1/1 e 100% mesmo que a ativacao tenha
        declarado 900 chunks."""
        await self._chunk("a", "Texto A.")
        await self._vector("a", [1.0, 0.0, 0.0])
        await self._activate(expected=900)
        with self.assertRaises(VectorSearchError):
            await self._search()
        resultado = await self._search(allow_degraded=True)
        self.assertIn("POPULATION_BELOW_EXPECTED", resultado.degradation_reasons)

    async def test_a_degraded_activation_degrades_every_search_over_it(self):
        await self._chunk("a", "Texto A.")
        await self._vector("a", [1.0, 0.0, 0.0])
        await self._activate(expected=1, degraded=True)
        resultado = await self._search(allow_degraded=True)
        self.assertIn("ACTIVATED_DEGRADED", resultado.degradation_reasons)

    async def test_a_complete_corpus_is_not_degraded(self):
        await self._chunk("a", "Texto A.")
        await self._chunk("b", "Texto B.")
        await self._vector("a", [1.0, 0.0, 0.0])
        await self._vector("b", [0.0, 1.0, 0.0])
        await self._activate(expected=2)
        resultado = await self._search()
        self.assertFalse(resultado.degraded)
        self.assertEqual(resultado.degradation_reasons, ())


class QueryEmbeddingTests(_VectorCase):
    async def test_the_query_uses_the_active_space_model(self):
        """O modelo NAO e parametro do buscador: vem da linha do espaco
        ativo, a mesma com que os chunks foram vetorizados."""
        await self._chunk("a", "Texto A.")
        await self._vector("a", [1.0, 0.0, 0.0])
        await self._activate(expected=1)
        provider = _ScriptedProvider({"consulta": (1.0, 0.0, 0.0)})
        await self._search(provider=provider)
        self.assertEqual(provider.models_seen, ["modelo-a"])

    async def test_the_query_text_is_sent_verbatim(self):
        await self._chunk("a", "Texto A.")
        await self._vector("a", [1.0, 0.0, 0.0])
        await self._activate(expected=1)
        pergunta = "  Por que a diluição reduz a CONCENTRAÇÃO?  "
        provider = _ScriptedProvider({pergunta: (1.0, 0.0, 0.0)})
        await self._search(pergunta, provider=provider)
        self.assertEqual(provider.texts_seen, [(pergunta,)])

    async def test_a_query_vector_of_the_wrong_dimension_is_refused(self):
        await self._chunk("a", "Texto A.")
        await self._vector("a", [1.0, 0.0, 0.0])
        await self._activate(expected=1)
        provider = _ScriptedProvider({"consulta": (1.0, 0.0, 0.0, 0.0, 0.0)})
        with self.assertRaises(VectorSearchError) as caught:
            await self._search(provider=provider)
        self.assertEqual(caught.exception.code, "QUERY_DIMENSION_MISMATCH")
        self.assertIn("3", str(caught.exception))
        self.assertIn("5", str(caught.exception))

    async def test_a_provider_failure_becomes_a_named_domain_error(self):
        await self._chunk("a", "Texto A.")
        await self._vector("a", [1.0, 0.0, 0.0])
        await self._activate(expected=1)
        with self.assertRaises(VectorSearchError) as caught:
            await self._search(provider=_BrokenProvider())
        self.assertEqual(caught.exception.code, "EMBEDDING_PROVIDER_UNAVAILABLE")

    async def test_a_degenerate_query_vector_is_refused(self):
        await self._chunk("a", "Texto A.")
        await self._vector("a", [1.0, 0.0, 0.0])
        await self._activate(expected=1)
        provider = _ScriptedProvider({"consulta": (0.0, 0.0, 0.0)})
        with self.assertRaises(VectorSearchError) as caught:
            await self._search(provider=provider)
        self.assertEqual(caught.exception.code, "QUERY_VECTOR_DEGENERATE")

    async def test_an_empty_query_never_reaches_the_provider(self):
        await self._chunk("a", "Texto A.")
        await self._vector("a", [1.0, 0.0, 0.0])
        await self._activate(expected=1)
        provider = _ScriptedProvider({})
        resultado = await self._search("   ", provider=provider)
        self.assertEqual(resultado.empty_reasons, ("EMPTY_QUERY",))
        self.assertEqual(provider.texts_seen, [])


class FilterTests(_VectorCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._source(
            "comercial", rights_class="COMMERCIAL_REFERENCE",
            authority_level="COMMERCIAL_TEXTBOOK", source_kind="TEXTBOOK",
            title="Livro comercial",
        )
        await self._source(
            "bncc", rights_class="OFFICIAL_PUBLIC", authority_level="OFFICIAL",
            source_kind="CURRICULUM_FRAMEWORK", title="BNCC",
        )
        async with self.factory() as session:
            raiz = CatalogNode(name="Quimica", node_type="SUBJECT")
            session.add(raiz)
            await session.flush()
            raiz.root_id = raiz.id
            filho = CatalogNode(
                name="Solucoes", node_type="TOPIC",
                parent_id=raiz.id, root_id=raiz.id,
            )
            session.add(filho)
            await session.flush()
            self.node_raiz, self.node_filho = raiz.id, filho.id
            outro = CatalogNode(name="Fisica", node_type="SUBJECT")
            session.add(outro)
            await session.flush()
            outro.root_id = outro.id
            self.node_outro = outro.id
            await session.commit()

        await self._chunk("prosa", "Prosa propria sobre diluicao.")
        await self._chunk(
            "exercicio", "Exercicio sobre diluicao.", chunk_type="EXERCISE"
        )
        await self._chunk(
            "solucao", "Resolucao do exercicio.", chunk_type="SOLUTION"
        )
        await self._chunk(
            "gabarito", "1-a 2-b 3-c", role="ANSWER_KEY"
        )
        await self._chunk(
            "sumario", "Capitulo 5 ....... 120", role="TABLE_OF_CONTENTS"
        )
        await self._chunk(
            "comercial", "Texto literal do livro comercial.", source="comercial"
        )
        await self._chunk(
            "habilidade", "Analisar transformacoes e conservacoes em sistemas.",
            source="bncc", chunk_type="CURRICULUM_ITEM",
            bncc=["EM13CNT301"], page=42,
        )
        await self._chunk("no_filho", "Prosa mapeada no no filho.",
                          content_node_id=self.node_filho)
        await self._chunk("no_outro", "Prosa mapeada em outra arvore.",
                          content_node_id=self.node_outro)
        for key in self.chunks:
            await self._vector(key, [1.0, 0.0, 0.0])
        await self._activate()

    async def _keys(self, **kwargs):
        resultado = await self._search(limit=100, **kwargs)
        inverso = {value: key for key, value in self.chunks.items()}
        return {inverso[hit.chunk_id] for hit in resultado.hits}, resultado

    async def test_solution_is_closed_in_practice_and_assess(self):
        for purpose in ("PRACTICE", "ASSESS"):
            keys, resultado = await self._keys(retrieval_purpose=purpose)
            self.assertNotIn("solucao", keys, purpose)
            self.assertFalse(resultado.solution_visible, purpose)
            self.assertIn("PURPOSE_SOLUTION", resultado.filtered_out, purpose)

    async def test_solution_is_open_in_learn_and_author(self):
        for purpose in ("LEARN", "AUTHOR"):
            keys, resultado = await self._keys(retrieval_purpose=purpose)
            self.assertIn("solucao", keys, purpose)
            self.assertTrue(resultado.solution_visible, purpose)

    async def test_an_absent_purpose_closes_solution(self):
        keys, resultado = await self._keys()
        self.assertNotIn("solucao", keys)
        self.assertFalse(resultado.solution_visible)

    async def test_the_answer_key_role_follows_the_same_policy(self):
        """``chunk_type == SOLUTION`` e forma local; ``editorial_role ==
        ANSWER_KEY`` e funcao editorial. Dimensoes ortogonais, e basta uma
        fechar."""
        keys, _ = await self._keys(retrieval_purpose="PRACTICE")
        self.assertNotIn("gabarito", keys)
        keys, _ = await self._keys(retrieval_purpose="LEARN")
        self.assertIn("gabarito", keys)

    async def test_navigation_apparatus_is_never_retrieved(self):
        for purpose in ("LEARN", "PRACTICE", "ASSESS", "AUTHOR", None):
            keys, _ = await self._keys(retrieval_purpose=purpose)
            self.assertNotIn("sumario", keys, str(purpose))

    async def test_retrieval_eligibility_is_named_in_the_response(self):
        """Nao e coluna: e o veredito da politica versionada. Sai nomeado
        para que 'por que este chunk nao veio?' tenha resposta."""
        _, resultado = await self._keys(retrieval_purpose="PRACTICE")
        self.assertEqual(
            resultado.applied_filters["retrieval_eligibility"],
            "POLICY_V1_BY_ROLE_AND_PURPOSE",
        )
        self.assertIn("EDITORIAL_ROLE", resultado.filtered_out)
        self.assertTrue(
            all(hit.explanation["retrieval_eligible"] for hit in resultado.hits)
        )

    async def test_chunk_type_filter(self):
        keys, _ = await self._keys(chunk_types=["EXERCISE"])
        self.assertEqual(keys, {"exercicio"})

    async def test_rights_class_filter(self):
        keys, _ = await self._keys(rights_classes=["OFFICIAL_PUBLIC"])
        self.assertEqual(keys, {"habilidade"})

    async def test_exclude_commercial(self):
        keys, resultado = await self._keys(exclude_commercial=True)
        self.assertNotIn("comercial", keys)
        self.assertIn("RIGHTS", resultado.filtered_out)

    async def test_source_kind_filter(self):
        keys, _ = await self._keys(source_kinds=["CURRICULUM_FRAMEWORK"])
        self.assertEqual(keys, {"habilidade"})

    async def test_source_filter(self):
        keys, _ = await self._keys(source_ids=[self.sources["comercial"]])
        self.assertEqual(keys, {"comercial"})

    async def test_document_filter(self):
        keys, _ = await self._keys(document_ids=[self.documents["bncc"]])
        self.assertEqual(keys, {"habilidade"})

    async def test_content_node_filter_walks_the_subtree(self):
        keys, _ = await self._keys(content_node_id=self.node_raiz)
        self.assertEqual(keys, {"no_filho"})

    async def test_content_node_filter_can_stay_on_the_node(self):
        keys, _ = await self._keys(
            content_node_id=self.node_raiz, include_descendant_nodes=False
        )
        self.assertEqual(keys, set())

    async def test_max_per_source(self):
        _, resultado = await self._keys(max_per_source=1)
        fontes = [hit.source_id for hit in resultado.hits]
        self.assertEqual(len(fontes), len(set(fontes)))

    async def test_curriculum_item_is_retrievable_and_carries_its_codes(self):
        """So a BNCC responde a pergunta normativa, e ela e OFFICIAL_PUBLIC -
        citavel. O codigo vem junto porque e a chave util da habilidade."""
        keys, resultado = await self._keys(chunk_types=["CURRICULUM_ITEM"])
        self.assertEqual(keys, {"habilidade"})
        hit = resultado.hits[0]
        self.assertEqual(hit.bncc_node_codes, ("EM13CNT301",))
        self.assertEqual(hit.source_kind, "CURRICULUM_FRAMEWORK")
        self.assertTrue(hit.quotable)
        self.assertIsNotNone(hit.excerpt)

    async def test_every_filter_rejection_is_counted(self):
        _, resultado = await self._keys(
            retrieval_purpose="PRACTICE", chunk_types=["PROSE"]
        )
        self.assertIn("PURPOSE_SOLUTION", resultado.filtered_out)
        self.assertIn("EDITORIAL_ROLE", resultado.filtered_out)
        self.assertIn("CHUNK_TYPE", resultado.filtered_out)


class CommercialLiteralTests(_VectorCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._source(
            "comercial", rights_class="COMMERCIAL_REFERENCE",
            authority_level="COMMERCIAL_TEXTBOOK", source_kind="TEXTBOOK",
            title="Livro comercial",
        )
        self.literal = "ESTE E O TEXTO LITERAL PROTEGIDO DA OBRA COMERCIAL"
        await self._chunk("comercial", self.literal, source="comercial")
        await self._chunk("propria", "Texto proprio, citavel.")
        await self._vector("comercial", [1.0, 0.0, 0.0])
        await self._vector("propria", [0.9, 0.1, 0.0])
        await self._activate(expected=2)

    async def test_a_commercial_hit_carries_no_literal_anywhere(self):
        resultado = await self._search()
        comercial = next(
            hit for hit in resultado.hits
            if hit.rights_class == "COMMERCIAL_REFERENCE"
        )
        self.assertIsNone(comercial.excerpt)
        self.assertFalse(comercial.quotable)
        # Varredura do resultado INTEIRO serializado: nem o hit, nem a
        # explicacao, nem agregado algum pode conter o literal.
        blob = json.dumps(resultado.__dict__, default=str)
        self.assertNotIn(self.literal, blob)
        self.assertNotIn(self.literal[:20], blob)

    async def test_traceability_survives_the_rights_restriction(self):
        """Nao poder citar nao e nao poder apontar. Pagina impressa, fonte,
        documento, chunk e hash continuam."""
        resultado = await self._search()
        comercial = next(
            hit for hit in resultado.hits
            if hit.rights_class == "COMMERCIAL_REFERENCE"
        )
        self.assertEqual(comercial.source_id, self.sources["comercial"])
        self.assertEqual(comercial.document_id, self.documents["comercial"])
        self.assertEqual(comercial.chunk_id, self.chunks["comercial"])
        self.assertIsNotNone(comercial.page_start)
        self.assertEqual(comercial.page_start, comercial.page_end)
        self.assertEqual(len(comercial.text_hash), 64)
        self.assertEqual(comercial.document_filename, "comercial.pdf")
        self.assertEqual(comercial.heading_path, ("Cap 1",))

    async def test_the_commercial_literal_is_never_even_read_from_the_database(self):
        """DEFESA EM PROFUNDIDADE, e ela precisa de teste proprio.

        A saida ja esta protegida na montagem do hit, que so anexa excerpt
        para fonte citavel. Mas a segunda garantia - a de que o ``raw_text``
        de obra comercial nao e sequer carregado para a memoria do processo -
        nao sobrevive sozinha: uma mutacao que liberasse a leitura passava em
        todos os testes, porque a saida continuava limpa.

        Aqui o SQL emitido e capturado e conferido: nenhuma instrucao que
        leia ``raw_text`` pode mencionar o chunk comercial.
        """
        from sqlalchemy import event

        emitidos: list[tuple[str, object]] = []

        def _capturar(conn, cursor, statement, parameters, context, many):
            emitidos.append((statement, parameters))

        event.listen(self.engine.sync_engine, "before_cursor_execute", _capturar)
        try:
            await self._search()
        finally:
            event.remove(
                self.engine.sync_engine, "before_cursor_execute", _capturar
            )

        comercial = str(self.chunks["comercial"])
        leituras = [
            (statement, parameters)
            for statement, parameters in emitidos
            if "raw_text" in statement and statement.lstrip().upper().startswith("SELECT")
        ]
        self.assertTrue(leituras, "nenhuma leitura de raw_text foi emitida")
        for statement, parameters in leituras:
            blob = f"{statement} {parameters!r}".replace("-", "")
            self.assertNotIn(
                comercial.replace("-", ""), blob,
                "o raw_text do chunk comercial foi lido do banco",
            )

    async def test_a_quotable_source_does_get_an_excerpt(self):
        resultado = await self._search()
        propria = next(hit for hit in resultado.hits if hit.quotable)
        self.assertEqual(propria.excerpt, "Texto proprio, citavel.")


class ExplanationTests(_VectorCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._chunk("a", "Texto A.")
        await self._vector("a", [1.0, 0.0, 0.0])
        await self._activate(expected=1)

    async def test_the_explanation_allows_verifying_how_the_hit_was_found(self):
        resultado = await self._search(retrieval_purpose="LEARN")
        explicacao = resultado.hits[0].explanation
        for chave in (
            "embedding_space_id", "embedding_space_fingerprint", "dimensions",
            "distance_metric", "vector_backend", "score_semantics", "distance",
            "similarity", "retrieval_purpose", "retrieval_eligible",
            "editorial_role", "chunk_type", "rights_class", "policy_version",
            "degraded",
        ):
            self.assertIn(chave, explicacao, chave)
        self.assertEqual(explicacao["distance_metric"], "cosine")
        self.assertEqual(explicacao["vector_backend"], EXACT_BACKEND)
        self.assertEqual(explicacao["retrieval_purpose"], "LEARN")

    async def test_the_explanation_never_carries_the_vector(self):
        resultado = await self._search()
        blob = json.dumps(resultado.hits[0].explanation, default=str)
        self.assertNotIn("1.0, 0.0, 0.0", blob)
        for valor in resultado.hits[0].explanation.values():
            self.assertNotIsInstance(valor, (list, tuple))

    async def test_no_consumer_learns_the_vendor_or_the_model(self):
        """O espaco e identificavel - por id e por fingerprint - sem que o
        consumidor aprenda provider nem modelo. Expo-los convidaria um
        ``if provider == ...`` do outro lado da fronteira."""
        resultado = await self._search()
        blob = json.dumps(
            {
                "resultado": {
                    k: v for k, v in resultado.__dict__.items() if k != "hits"
                },
                "hits": [hit.__dict__ for hit in resultado.hits],
            },
            default=str,
        )
        self.assertNotIn("modelo-a", blob)
        self.assertNotIn("openai", blob.lower())
        self.assertNotIn("pgvector", blob.lower())
        self.assertIn(str(self.space_id), blob)

    async def test_the_fingerprint_changes_with_the_space(self):
        primeiro = await self._search()
        async with self.factory() as session:
            space = await session.get(KnowledgeEmbeddingSpace, self.space_id)
            space.model = "modelo-z"
            await session.commit()
        segundo = await self._search()
        self.assertNotEqual(primeiro.query_fingerprint, segundo.query_fingerprint)

    async def test_the_fingerprint_changes_with_the_filters(self):
        primeiro = await self._search()
        segundo = await self._search(retrieval_purpose="LEARN")
        self.assertNotEqual(primeiro.query_fingerprint, segundo.query_fingerprint)


class ValidationTests(_VectorCase):
    async def test_an_unknown_purpose_is_refused(self):
        with self.assertRaises(VectorSearchError) as caught:
            await self._search(retrieval_purpose="TEACHING")
        self.assertEqual(caught.exception.code, "INVALID_RETRIEVAL_PURPOSE")

    async def test_a_limit_out_of_range_is_refused(self):
        with self.assertRaises(VectorSearchError) as caught:
            await self._search(limit=10_000)
        self.assertEqual(caught.exception.code, "LIMIT_TOO_LARGE")

    async def test_a_negative_offset_is_refused(self):
        with self.assertRaises(VectorSearchError) as caught:
            await self._search(offset=-1)
        self.assertEqual(caught.exception.code, "OFFSET_OUT_OF_RANGE")


if __name__ == "__main__":
    unittest.main()
