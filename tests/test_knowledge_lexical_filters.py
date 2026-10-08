"""CEREBRO - Fase 5: filtros, direitos, rastreabilidade e o portao de SOLUTION.

O principio da Fase 3.1, agora executavel e nao apenas registrado na spec:

    PRACTICE -> fechado      ASSESS   -> fechado
    LEARN    -> permitido    AUTHOR   -> permitido
    proposito desconhecido ou ausente -> FECHADO

E a politica de direitos: o literal de fonte COMMERCIAL_REFERENCE e legivel
EM PROCESSO pelo indexador - ele e indexado e buscado -, mas nunca sai na
resposta. Buscar e expor sao coisas diferentes, e so a segunda e proibida.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
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
from agente_ia_edu.services.knowledge_engine.rights import NON_COMMERCIAL_EXCERPT_LIMIT


class _FilterCase(unittest.IsolatedAsyncioTestCase):
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
        page_offset: int = 0,
    ) -> uuid.UUID:
        async with self.factory() as session:
            source = KnowledgeSource(
                title=f"Obra {name}",
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
                page_offset=page_offset,
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
        chunk_type: str = "PROSE",
        page: int = 311,
        heading_path: list[str] | None = None,
        content_node_id: uuid.UUID | None = None,
        bncc_node_codes: list[str] | None = None,
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
                bncc_node_codes=bncc_node_codes,
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


class SolutionGateTests(_FilterCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.prose = await self._chunk("livro", "O calculo de mol na teoria.")
        self.solution = await self._chunk(
            "livro", "Resposta: mol calculado.", chunk_type="SOLUTION"
        )
        self.exercise = await self._chunk(
            "livro", "Calcule o mol desta amostra.", chunk_type="EXERCISE"
        )
        await self._index()

    async def test_practice_is_closed(self):
        result = await self._search("mol", retrieval_purpose="PRACTICE")
        self.assertNotIn(self.solution, [hit.chunk_id for hit in result.hits])
        self.assertEqual(result.filtered_out["PURPOSE_SOLUTION"], 1)
        self.assertFalse(result.solution_visible)

    async def test_assess_is_closed(self):
        result = await self._search("mol", retrieval_purpose="ASSESS")
        self.assertNotIn(self.solution, [hit.chunk_id for hit in result.hits])

    async def test_an_absent_purpose_is_closed(self):
        """O default FECHA. Uma permissao nunca nasce de omissao."""
        result = await self._search("mol")
        self.assertNotIn(self.solution, [hit.chunk_id for hit in result.hits])
        self.assertEqual(result.retrieval_purpose, "UNKNOWN")

    async def test_learn_is_open(self):
        result = await self._search("mol", retrieval_purpose="LEARN")
        self.assertIn(self.solution, [hit.chunk_id for hit in result.hits])
        self.assertTrue(result.solution_visible)

    async def test_author_is_open(self):
        result = await self._search("mol", retrieval_purpose="AUTHOR")
        self.assertIn(self.solution, [hit.chunk_id for hit in result.hits])

    async def test_an_unknown_purpose_is_refused_at_the_door(self):
        with self.assertRaises(LexicalSearchError) as caught:
            await self._search("mol", retrieval_purpose="STUDYING")
        self.assertEqual(caught.exception.code, "INVALID_RETRIEVAL_PURPOSE")

    async def test_exercise_is_never_gated_by_purpose(self):
        """O portao e de SOLUTION. Enunciado de exercicio e material legitimo
        em PRACTICE - e justamente o que se quer la."""
        result = await self._search("mol", retrieval_purpose="PRACTICE")
        self.assertIn(self.exercise, [hit.chunk_id for hit in result.hits])

    async def test_the_count_of_excluded_solutions_is_reported(self):
        """O relatorio da fase pede exatamente este numero."""
        await self._chunk("livro", "Resposta: outro mol.", chunk_type="SOLUTION")
        await self._index()
        result = await self._search("mol", retrieval_purpose="ASSESS")
        self.assertEqual(result.filtered_out["PURPOSE_SOLUTION"], 2)


class RightsTests(_FilterCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._source("comercial", rights_class="COMMERCIAL_REFERENCE")
        await self._source("propria", rights_class="OWN")
        await self._source("bncc", rights_class="OFFICIAL_PUBLIC", source_kind="CURRICULUM_FRAMEWORK")
        self.commercial = await self._chunk("comercial", "A diluicao segundo a obra.")
        self.own = await self._chunk("propria", "A diluicao na apostila propria.")
        self.official = await self._chunk("bncc", "Diluicao citada na norma.")
        await self._index()

    async def test_a_commercial_source_is_indexed_and_searched(self):
        """Buscar e permitido (spec 9.3): o indexador le o literal restrito em
        processo. O que e proibido e EXPOR."""
        result = await self._search("diluicao")
        self.assertIn(self.commercial, [hit.chunk_id for hit in result.hits])

    async def test_a_commercial_hit_never_carries_an_excerpt(self):
        result = await self._search("diluicao")
        commercial = [h for h in result.hits if h.chunk_id == self.commercial][0]
        self.assertIsNone(commercial.excerpt)
        self.assertFalse(commercial.quotable)

    async def test_a_non_commercial_hit_may_carry_a_bounded_excerpt(self):
        result = await self._search("diluicao")
        own = [h for h in result.hits if h.chunk_id == self.own][0]
        self.assertIsNotNone(own.excerpt)
        self.assertTrue(own.quotable)
        self.assertLessEqual(len(own.excerpt), NON_COMMERCIAL_EXCERPT_LIMIT)

    async def test_exclude_commercial_removes_it_from_the_ranking(self):
        result = await self._search("diluicao", exclude_commercial=True)
        self.assertNotIn(self.commercial, [hit.chunk_id for hit in result.hits])
        self.assertEqual(result.filtered_out["RIGHTS"], 1)

    async def test_an_explicit_rights_allowlist_is_honoured(self):
        result = await self._search("diluicao", rights_classes=("OFFICIAL_PUBLIC",))
        self.assertEqual([hit.chunk_id for hit in result.hits], [self.official])

    async def test_a_long_commercial_text_is_not_leaked_through_the_heading(self):
        """A Fase 3 produziu headings contaminados com paragrafo inteiro. Sem o
        corte por nivel, o texto escaparia pelo campo de titulo."""
        contaminated = "Chapter 9 - " + "texto longo da obra comercial " * 20
        leaked = await self._chunk(
            "comercial", "Mais diluicao aqui.", heading_path=[contaminated]
        )
        await self._index()
        result = await self._search("diluicao")
        hit = [h for h in result.hits if h.chunk_id == leaked][0]
        self.assertLessEqual(len(hit.heading_path[0]), POLICY.heading_level_max_chars)


class SourceKindTests(_FilterCase):
    async def test_the_filter_separates_textbook_from_framework(self):
        await self._source("livro", source_kind="TEXTBOOK")
        await self._source("bncc", source_kind="CURRICULUM_FRAMEWORK", rights_class="OFFICIAL_PUBLIC")
        book = await self._chunk("livro", "energia no livro didatico")
        norm = await self._chunk("bncc", "energia na norma", chunk_type="CURRICULUM_ITEM")
        await self._index()

        only_book = await self._search("energia", source_kinds=("TEXTBOOK",))
        only_norm = await self._search("energia", source_kinds=("CURRICULUM_FRAMEWORK",))
        self.assertEqual([h.chunk_id for h in only_book.hits], [book])
        self.assertEqual([h.chunk_id for h in only_norm.hits], [norm])

    async def test_an_empty_result_names_the_source_kind_filter(self):
        await self._source("livro", source_kind="TEXTBOOK")
        await self._chunk("livro", "energia")
        await self._index()
        result = await self._search("energia", source_kinds=("ARTICLE",))
        self.assertEqual(result.hits, ())
        self.assertIn("FILTERED_OUT_BY_SOURCE_KIND", result.empty_reasons)


class ChunkTypeTests(_FilterCase):
    async def test_the_filter_keeps_only_the_requested_types(self):
        prose = await self._chunk("livro", "mol em prosa")
        definition = await self._chunk("livro", "mol definido", chunk_type="DEFINITION")
        await self._chunk("livro", "mol tabelado", chunk_type="TABLE")
        await self._index()
        result = await self._search("mol", chunk_types=("PROSE", "DEFINITION"))
        self.assertEqual(
            sorted(str(h.chunk_id) for h in result.hits),
            sorted([str(prose), str(definition)]),
        )

    async def test_asking_only_for_solution_still_obeys_the_purpose_gate(self):
        """Pedir ``chunk_types=("SOLUTION",)`` NAO destrava o portao: direitos
        e proposito sao politica, nao preferencia de consulta."""
        await self._chunk("livro", "Resposta: mol.", chunk_type="SOLUTION")
        await self._index()
        result = await self._search(
            "mol", chunk_types=("SOLUTION",), retrieval_purpose="PRACTICE"
        )
        self.assertEqual(result.hits, ())
        self.assertIn("FILTERED_OUT_BY_PURPOSE", result.empty_reasons)


class ContentNodeTests(_FilterCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.factory() as session:
            discipline = CatalogNode(node_type="DISCIPLINE", name="Quimica", position=1)
            session.add(discipline)
            await session.flush()
            discipline.root_id = discipline.id
            content = CatalogNode(
                parent_id=discipline.id,
                root_id=discipline.id,
                node_type="CONTENT",
                code="CHEMISTRY-SOLUTIONS",
                name="Solucoes",
                position=1,
            )
            subcontent = CatalogNode(
                parent_id=None,
                root_id=discipline.id,
                node_type="SUBCONTENT",
                code="CHEMISTRY-SOLUTIONS-DILUTION",
                name="Diluicao",
                position=1,
            )
            session.add_all([content, subcontent])
            await session.flush()
            subcontent.parent_id = content.id
            self.content_node = content.id
            self.subcontent_node = subcontent.id
            self.other_node = discipline.id
            await session.commit()

    async def test_the_filter_matches_the_node_and_its_descendants(self):
        at_node = await self._chunk(
            "livro", "diluicao no no", content_node_id=self.content_node
        )
        at_child = await self._chunk(
            "livro", "diluicao no filho", content_node_id=self.subcontent_node
        )
        await self._chunk("livro", "diluicao sem no")
        await self._index()
        result = await self._search("diluicao", content_node_id=self.content_node)
        self.assertEqual(
            sorted(str(h.chunk_id) for h in result.hits),
            sorted([str(at_node), str(at_child)]),
        )

    async def test_descendants_can_be_excluded(self):
        at_node = await self._chunk(
            "livro", "diluicao no no", content_node_id=self.content_node
        )
        await self._chunk(
            "livro", "diluicao no filho", content_node_id=self.subcontent_node
        )
        await self._index()
        result = await self._search(
            "diluicao",
            content_node_id=self.content_node,
            include_descendant_nodes=False,
        )
        self.assertEqual([h.chunk_id for h in result.hits], [at_node])

    async def test_an_unmapped_chunk_is_excluded_and_that_is_reported(self):
        """O corpus real tem ``content_node_id`` NULL em todo chunk: a Fase 3
        nao casa curriculo. Este filtro nasce correto e INERTE, e o relatorio
        diz isso em voz alta em vez de esconder."""
        await self._chunk("livro", "diluicao sem no")
        await self._index()
        result = await self._search("diluicao", content_node_id=self.content_node)
        self.assertEqual(result.hits, ())
        self.assertIn("FILTERED_OUT_BY_CONTENT_NODE", result.empty_reasons)
        self.assertEqual(result.filtered_out["CONTENT_NODE"], 1)


class TraceabilityTests(_FilterCase):
    async def test_every_hit_carries_source_document_and_page(self):
        await self._source("livro", rights_class="OWN", page_offset=311)
        await self._chunk("livro", "diluicao aqui", page=312)
        await self._index()
        hit = (await self._search("diluicao")).hits[0]

        self.assertEqual(hit.source_title, "Obra livro")
        self.assertEqual(hit.source_kind, "TEXTBOOK")
        self.assertEqual(hit.rights_class, "OWN")
        self.assertEqual(hit.authority_level, "OWN")
        self.assertEqual(hit.document_filename, "livro.pdf")
        self.assertEqual(hit.page_start, 312)
        self.assertEqual(hit.page_end, 312)

    async def test_the_page_is_the_stored_page_and_the_offset_is_not_reapplied(self):
        """``page_offset`` e aplicado no CHUNKING (chunking.py:186). Reaplicar
        aqui erraria toda citacao de recorte de capitulo."""
        await self._source("recorte", rights_class="OWN", page_offset=311)
        chunk_id = await self._chunk("recorte", "diluicao do recorte", page=312)
        await self._index()
        hit = (await self._search("diluicao")).hits[0]
        async with self.factory() as session:
            stored = await session.get(KnowledgeChunk, chunk_id)
            self.assertEqual(hit.page_start, stored.page_start)

    async def test_the_hit_carries_the_hash_and_the_bncc_codes(self):
        await self._chunk(
            "livro", "energia na norma", bncc_node_codes=["EM13CNT101"]
        )
        await self._index()
        hit = (await self._search("energia")).hits[0]
        self.assertEqual(hit.bncc_node_codes, ("EM13CNT101",))
        self.assertEqual(len(hit.text_hash), 64)

    async def test_a_bncc_code_is_searchable_as_a_term(self):
        """O codigo e a unica chave util de uma habilidade."""
        chunk_id = await self._chunk("bncc", "(EM13CNT301) Analisar situacoes.")
        await self._index()
        result = await self._search("EM13CNT301")
        self.assertEqual([h.chunk_id for h in result.hits], [chunk_id])

    async def test_no_hit_ever_exposes_raw_text(self):
        from dataclasses import fields

        from agente_ia_edu.services.knowledge_engine.lexical_search import LexicalHit

        self.assertNotIn("raw_text", {item.name for item in fields(LexicalHit)})


if __name__ == "__main__":
    unittest.main()
