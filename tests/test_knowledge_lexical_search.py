"""CEREBRO - Fase 5: BM25F, frase, proximidade, explicabilidade.

O que este arquivo garante, e que nenhum teste de "achou alguma coisa" garante:

- o score e TOTALMENTE explicavel - a soma das parcelas e o score final;
- ``df`` e ``avgdl`` vem do corpus INTEIRO, logo o score de um chunk nao muda
  porque outro foi filtrado;
- resultado vazio sempre tem razao NOMEADA (``STRICT_CORPUS``);
- a paginacao e estavel e o ``query_fingerprint`` carrega a geracao do indice.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeSource,
)
from agente_ia_edu.knowledge_retrieval_policy.v1 import POLICY
from agente_ia_edu.services.knowledge_engine.lexical_index import LexicalIndexService
from agente_ia_edu.services.knowledge_engine.lexical_search import (
    LexicalSearchError,
    LexicalSearcher,
)


class _SearchCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=True
        )
        self.sources: dict[str, uuid.UUID] = {}
        self.documents: dict[str, uuid.UUID] = {}
        self._ordinal = 0

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _source(
        self,
        name: str,
        *,
        rights_class: str = "OWN",
        source_kind: str = "TEXTBOOK",
        authority_level: str = "OWN",
    ) -> uuid.UUID:
        async with self.factory() as session:
            source = KnowledgeSource(
                title=name,
                source_kind=source_kind,
                rights_class=rights_class,
                authority_level=authority_level,
            )
            session.add(source)
            await session.flush()
            document = KnowledgeDocument(
                source_id=source.id,
                filename=f"{name}.pdf",
                storage_uri=f"/tmp/{name}.pdf",
                document_hash=name.ljust(64, "x")[:64],
                page_offset=0,
            )
            session.add(document)
            await session.flush()
            self.sources[name] = source.id
            self.documents[name] = document.id
            await session.commit()
        return self.sources[name]

    async def _chunk(
        self,
        source: str,
        raw_text: str,
        *,
        heading_path: list[str] | None = None,
        chunk_type: str = "PROSE",
        page: int = 42,
        content_node_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        if source not in self.sources:
            await self._source(source)
        self._ordinal += 1
        async with self.factory() as session:
            chunk = KnowledgeChunk(
                source_id=self.sources[source],
                document_id=self.documents[source],
                ordinal=self._ordinal,
                chunk_type=chunk_type,
                heading_path=heading_path or [],
                page_start=page,
                page_end=page,
                raw_text=raw_text,
                text_hash=f"{self._ordinal:064d}",
                char_count=len(raw_text),
                content_node_id=content_node_id,
            )
            session.add(chunk)
            await session.flush()
            chunk_id = chunk.id
            await session.commit()
        return chunk_id

    async def _index(self):
        async with self.factory() as session:
            chunks = (await session.scalars(select(KnowledgeChunk))).all()
            await LexicalIndexService(session).index_chunks(chunks)
            await session.commit()

    async def _search(self, query: str, **kwargs):
        async with self.factory() as session:
            return await LexicalSearcher(session).search(query, **kwargs)


class RankingTests(_SearchCase):
    async def test_the_chunk_that_mentions_the_term_more_often_ranks_higher(self):
        few = await self._chunk("livro", "A estequiometria aparece uma vez aqui. " * 1)
        many = await self._chunk(
            "livro", "Estequiometria, estequiometria e estequiometria outra vez."
        )
        await self._index()
        result = await self._search("estequiometria")
        self.assertEqual([hit.chunk_id for hit in result.hits][:2], [many, few])

    async def test_a_rare_term_outweighs_a_common_one(self):
        """idf em acao: ``limitante`` e raro, ``reagente`` e comum."""
        for index in range(8):
            await self._chunk("livro", f"O reagente numero {index} participa.")
        rare = await self._chunk("livro", "O fator limitante de um processo.")
        await self._index()
        result = await self._search("reagente limitante")
        self.assertEqual(result.hits[0].chunk_id, rare)

    async def test_a_shorter_chunk_wins_when_frequency_ties(self):
        """Normalizacao por comprimento: o mesmo termo numa unidade curta e
        evidencia mais densa."""
        short = await self._chunk("livro", "Diluicao.")
        long = await self._chunk(
            "livro", "Diluicao. " + "Texto de enchimento sobre outro assunto. " * 20
        )
        await self._index()
        result = await self._search("diluicao")
        self.assertEqual([hit.chunk_id for hit in result.hits], [short, long])

    async def test_ranks_start_at_one_and_are_dense(self):
        for text in ("mol aqui", "mol ali", "mol la"):
            await self._chunk("livro", text)
        await self._index()
        result = await self._search("mol")
        self.assertEqual([hit.rank for hit in result.hits], [1, 2, 3])

    async def test_ties_are_broken_by_corpus_position_not_by_uuid(self):
        """Desempate deterministico e o que torna a paginacao reprodutivel -
        ``chunk_id`` e UUID aleatorio e nao serviria."""
        first = await self._chunk("livro", "mol")
        second = await self._chunk("livro", "mol")
        await self._index()
        result = await self._search("mol")
        self.assertEqual([hit.chunk_id for hit in result.hits], [first, second])
        self.assertEqual([hit.ordinal for hit in result.hits], [1, 2])


class HeadingWeightTests(_SearchCase):
    async def test_with_neutral_weight_a_heading_hit_counts_like_a_body_hit(self):
        self.assertEqual(POLICY.heading_weight, 1.0)
        heading = await self._chunk(
            "livro", "Texto neutro.", heading_path=["Chapter 1 - Estequiometria"]
        )
        await self._index()
        result = await self._search("estequiometria")
        self.assertEqual(result.hits[0].chunk_id, heading)

    async def test_the_weight_is_applied_when_raised(self):
        body = await self._chunk("livro", "Fala de estequiometria no corpo.")
        heading = await self._chunk(
            "livro", "Texto neutro qualquer.", heading_path=["Chapter 2 - Estequiometria"]
        )
        await self._index()
        plain = await self._search("estequiometria")
        boosted = await self._search("estequiometria", heading_weight=5.0)
        self.assertEqual(plain.hits[0].chunk_id, body)
        self.assertEqual(boosted.hits[0].chunk_id, heading)


class PhraseAndProximityTests(_SearchCase):
    async def test_the_phrase_outranks_the_scattered_terms(self):
        scattered = await self._chunk(
            "livro",
            "O reagente entra na reacao. Muitas linhas depois, o fator limitante "
            "aparece sozinho em outro contexto completamente diferente.",
        )
        phrase = await self._chunk("livro", "O reagente limitante determina o rendimento.")
        await self._index()
        result = await self._search("reagente limitante")
        self.assertEqual(result.hits[0].chunk_id, phrase)
        self.assertGreater(result.hits[0].explanation["phrase_bonus"], 0)
        self.assertEqual(
            [h for h in result.hits if h.chunk_id == scattered][0].explanation[
                "phrase_bonus"
            ],
            0.0,
        )

    async def test_a_query_with_a_function_word_still_finds_the_phrase(self):
        """Posicoes integras: a consulta espera 2 posicoes entre os termos, e o
        documento as oferece."""
        hit = await self._chunk("livro", "A concentracao das solucoes aquosas varia.")
        await self._index()
        result = await self._search("concentração das soluções")
        self.assertEqual(result.hits[0].chunk_id, hit)
        self.assertEqual(len(result.hits[0].explanation["phrase_hits"]), 1)

    async def test_proximity_pays_less_than_adjacency(self):
        near = await self._chunk("livro", "A concentracao final da solucao preparada.")
        far = await self._chunk(
            "livro",
            "A concentracao importa. " + "Outro assunto qualquer aqui. " * 6
            + "Por fim, a solucao.",
        )
        await self._index()
        result = await self._search("concentracao solucao")
        by_chunk = {hit.chunk_id: hit for hit in result.hits}
        self.assertGreater(
            by_chunk[near].explanation["phrase_bonus"]
            + by_chunk[near].explanation["proximity_bonus"],
            by_chunk[far].explanation["proximity_bonus"],
        )

    async def test_phrase_required_filters_instead_of_boosting(self):
        await self._chunk("livro", "O reagente entra. O limitante e outro tema.")
        phrase = await self._chunk("livro", "O reagente limitante determina tudo.")
        await self._index()
        result = await self._search("reagente limitante", phrase_mode="REQUIRED")
        self.assertEqual([hit.chunk_id for hit in result.hits], [phrase])

    async def test_phrase_mode_must_be_known(self):
        await self._chunk("livro", "mol")
        await self._index()
        with self.assertRaises(LexicalSearchError) as caught:
            await self._search("mol", phrase_mode="MAGIC")
        self.assertEqual(caught.exception.code, "INVALID_PHRASE_MODE")


class ExplainabilityTests(_SearchCase):
    async def test_the_parts_add_up_to_the_final_score(self):
        await self._chunk("livro", "A concentracao das solucoes e a diluicao.")
        await self._chunk("livro", "Outra solucao qualquer com concentracao baixa.")
        await self._index()
        for query in ("concentracao", "concentração das soluções", "diluicao solucao"):
            result = await self._search(query)
            for hit in result.hits:
                parts = sum(
                    term["contribution"] for term in hit.explanation["query_terms"]
                )
                total = (
                    parts
                    + hit.explanation["phrase_bonus"]
                    + hit.explanation["proximity_bonus"]
                )
                self.assertAlmostEqual(total, hit.score, places=9, msg=query)
                self.assertAlmostEqual(
                    hit.explanation["bm25"], parts, places=9, msg=query
                )

    async def test_the_explanation_names_the_policy_and_the_normalizer(self):
        await self._chunk("livro", "mol")
        await self._index()
        hit = (await self._search("mol")).hits[0]
        self.assertEqual(hit.explanation["normalizer_version"], POLICY.normalizer_version)
        self.assertEqual(hit.explanation["policy_version"], POLICY.version)
        self.assertEqual(hit.explanation["k1"], POLICY.k1)
        self.assertEqual(hit.explanation["b"], POLICY.b)

    async def test_the_explanation_carries_df_idf_and_frequencies(self):
        await self._chunk("livro", "mol e mol", heading_path=["Chapter 1 - Mol"])
        await self._chunk("livro", "outro assunto")
        await self._index()
        hit = (await self._search("mol")).hits[0]
        term = hit.explanation["query_terms"][0]
        self.assertEqual(term["term"], "mol")
        self.assertEqual(term["df"], 1)
        self.assertEqual(term["tf_body"], 2)
        self.assertEqual(term["tf_heading"], 1)
        self.assertGreater(term["idf"], 0)

    async def test_dropped_and_unmatched_terms_are_reported(self):
        await self._chunk("livro", "a concentracao de uma solucao")
        await self._index()
        result = await self._search("concentração de bauxita")
        self.assertIn("bauxita", result.unmatched_terms)
        reasons = {item["surface"]: item["reason"] for item in result.dropped_tokens}
        self.assertEqual(reasons["de"], "TOO_SHORT")

    async def test_global_statistics_do_not_depend_on_filters(self):
        """Criterio de aceite 9: o score de um chunk nao muda porque outro foi
        filtrado. Se ``df`` viesse do conjunto filtrado, o mesmo chunk teria
        score diferente em LEARN e em AUTHOR."""
        prose = await self._chunk("livro", "mol e massa molar")
        await self._chunk("livro", "mol no gabarito", chunk_type="SOLUTION")
        await self._index()
        learn = await self._search("mol", retrieval_purpose="LEARN")
        practice = await self._search("mol", retrieval_purpose="PRACTICE")

        # A comparacao e do MESMO chunk nos dois propositos: em LEARN o
        # gabarito aparece (e e ate mais curto, logo mais bem pontuado), e em
        # PRACTICE ele e filtrado. O que nao pode mudar e o score de quem
        # permaneceu.
        by_chunk = {
            "learn": {hit.chunk_id: hit for hit in learn.hits},
            "practice": {hit.chunk_id: hit for hit in practice.hits},
        }
        self.assertEqual(len(learn.hits), 2)
        self.assertEqual(len(practice.hits), 1)
        self.assertEqual(
            by_chunk["learn"][prose].score, by_chunk["practice"][prose].score
        )
        self.assertEqual(
            by_chunk["learn"][prose].explanation["query_terms"][0]["df"],
            by_chunk["practice"][prose].explanation["query_terms"][0]["df"],
        )
        self.assertEqual(
            by_chunk["learn"][prose].explanation["corpus_size"],
            by_chunk["practice"][prose].explanation["corpus_size"],
        )


class EmptyResultTests(_SearchCase):
    async def test_an_empty_query_has_a_named_reason(self):
        await self._chunk("livro", "mol")
        await self._index()
        result = await self._search("   ")
        self.assertEqual(result.hits, ())
        self.assertIn("EMPTY_QUERY", result.empty_reasons)

    async def test_a_query_of_only_short_tokens_has_a_named_reason(self):
        await self._chunk("livro", "mol")
        await self._index()
        result = await self._search("a de o")
        self.assertIn("ALL_TERMS_BELOW_MIN_LENGTH", result.empty_reasons)

    async def test_no_lexical_match_is_named(self):
        await self._chunk("livro", "mol e massa molar")
        await self._index()
        result = await self._search("bauxita")
        self.assertEqual(result.hits, ())
        self.assertIn("NO_LEXICAL_MATCH", result.empty_reasons)
        self.assertEqual(result.unmatched_terms, ("bauxita",))

    async def test_an_empty_index_is_named(self):
        await self._chunk("livro", "mol")
        result = await self._search("mol")
        self.assertIn("EMPTY_INDEX", result.empty_reasons)

    async def test_a_filter_that_empties_the_result_says_which_one(self):
        await self._chunk("livro", "mol no gabarito", chunk_type="SOLUTION")
        await self._index()
        result = await self._search("mol", retrieval_purpose="PRACTICE")
        self.assertEqual(result.hits, ())
        self.assertIn("FILTERED_OUT_BY_PURPOSE", result.empty_reasons)

    async def test_nothing_found_never_relaxes_a_filter(self):
        await self._chunk("livro", "mol no gabarito", chunk_type="SOLUTION")
        await self._index()
        result = await self._search("mol", retrieval_purpose="ASSESS")
        self.assertEqual(result.total_candidates, 0)
        self.assertEqual(result.hits, ())


class PaginationTests(_SearchCase):
    async def test_pages_concatenate_into_the_single_ranking(self):
        for index in range(12):
            await self._chunk("livro", f"mol numero {index} com texto {'x ' * index}")
        await self._index()
        whole = await self._search("mol", limit=12)
        first = await self._search("mol", limit=5)
        second = await self._search("mol", limit=5, offset=5)
        third = await self._search("mol", limit=5, offset=10)
        self.assertEqual(
            [hit.chunk_id for hit in whole.hits],
            [hit.chunk_id for hit in first.hits + second.hits + third.hits],
        )

    async def test_has_more_is_honest(self):
        for index in range(6):
            await self._chunk("livro", f"mol {index}")
        await self._index()
        page = await self._search("mol", limit=4)
        self.assertTrue(page.has_more)
        self.assertEqual(page.total_candidates, 6)
        self.assertEqual(page.returned, 4)
        last = await self._search("mol", limit=4, offset=4)
        self.assertFalse(last.has_more)

    async def test_the_limit_is_capped_by_the_policy(self):
        await self._chunk("livro", "mol")
        await self._index()
        with self.assertRaises(LexicalSearchError) as caught:
            await self._search("mol", limit=POLICY.max_limit + 1)
        self.assertEqual(caught.exception.code, "LIMIT_TOO_LARGE")

    async def test_the_fingerprint_is_stable_for_the_same_query(self):
        await self._chunk("livro", "mol")
        await self._index()
        first = await self._search("mol", limit=1)
        second = await self._search("mol", limit=1, offset=0)
        self.assertEqual(first.query_fingerprint, second.query_fingerprint)

    async def test_the_fingerprint_changes_when_the_filters_change(self):
        await self._chunk("livro", "mol")
        await self._index()
        plain = await self._search("mol")
        filtered = await self._search("mol", chunk_types=("PROSE",))
        self.assertNotEqual(plain.query_fingerprint, filtered.query_fingerprint)

    async def test_the_fingerprint_changes_when_the_index_generation_moves(self):
        """Ajuste 3: postings mudarem entre paginas da mesma consulta tem de
        ser DETECTAVEL."""
        await self._chunk("livro", "mol")
        await self._index()
        before = await self._search("mol")
        async with self.factory() as session:
            chunks = (await session.scalars(select(KnowledgeChunk))).all()
            await LexicalIndexService(session).index_chunks(chunks)
            await session.commit()
        after = await self._search("mol")
        self.assertNotEqual(before.index_generation, after.index_generation)
        self.assertNotEqual(before.query_fingerprint, after.query_fingerprint)


class ResultEnvelopeTests(_SearchCase):
    async def test_the_envelope_carries_the_mode_the_backend_and_the_policy(self):
        await self._chunk("livro", "mol")
        await self._index()
        result = await self._search("mol")
        self.assertEqual(result.retrieval_mode, "STRICT_CORPUS")
        self.assertEqual(result.lexical_backend, POLICY.lexical_backend)
        self.assertEqual(result.policy["version"], POLICY.version)

    async def test_source_distribution_is_reported_and_not_capped(self):
        await self._chunk("livro_a", "mol aqui")
        await self._chunk("livro_a", "mol ali")
        await self._chunk("livro_a", "mol la")
        await self._chunk("livro_b", "mol tambem")
        await self._index()
        result = await self._search("mol")
        self.assertEqual(len(result.hits), 4)
        self.assertEqual(result.distinct_sources, 2)
        self.assertEqual(sum(result.source_distribution.values()), 4)

    async def test_the_coverage_warning_fires_with_a_single_source(self):
        await self._chunk("livro", "mol")
        await self._index()
        result = await self._search("mol")
        self.assertTrue(result.coverage_warning)

    async def test_chunk_type_distribution_is_reported(self):
        await self._chunk("livro", "mol prosa")
        await self._chunk("livro", "mol definicao", chunk_type="DEFINITION")
        await self._index()
        result = await self._search("mol")
        self.assertEqual(
            result.chunk_type_distribution, {"PROSE": 1, "DEFINITION": 1}
        )

    async def test_an_optional_cap_per_source_can_be_measured(self):
        for index in range(4):
            await self._chunk("livro_a", f"mol {index}")
        await self._chunk("livro_b", "mol unico")
        await self._index()
        capped = await self._search("mol", max_per_source=2)
        self.assertEqual(len(capped.hits), 3)
        self.assertEqual(capped.distinct_sources, 2)


if __name__ == "__main__":
    unittest.main()
