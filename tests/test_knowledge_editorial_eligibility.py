"""CEREBRO - Fase 5.1b: elegibilidade editorial e corpus estatistico, no banco.

Tres propriedades que so se verificam ponta a ponta:

1. **A ortogonalidade funciona em conjunto.** ``chunk_type`` e
   ``editorial_role`` sao dimensoes independentes que convergem na
   elegibilidade, e **basta uma fechar para fechar**.
2. **``df``, ``N`` e ``avgdl`` vem do corpus ELEGIVEL**, nao do corpus todo
   nem do conjunto filtrado pela consulta. Aparato de navegacao sai da
   estatistica porque nunca e recuperavel - era ele que inflava o ``idf``.
3. **Nao classificado nao se confunde com ``UNKNOWN``**, e nenhum dos dois
   esconde conteudo: os dois sao elegiveis.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSource
from agente_ia_edu.knowledge_retrieval_policy.v1 import statistical_corpus_roles
from agente_ia_edu.services.knowledge_engine.editorial_structure import DETECTOR_VERSION
from agente_ia_edu.services.knowledge_engine.lexical_index import LexicalIndexService
from agente_ia_edu.services.knowledge_engine.lexical_search import LexicalSearcher


class _EligibilityCase(unittest.IsolatedAsyncioTestCase):
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
        async with self.factory() as session:
            source = KnowledgeSource(
                title="Obra",
                source_kind="TEXTBOOK",
                rights_class="OWN",
                authority_level="OWN",
            )
            session.add(source)
            await session.flush()
            document = KnowledgeDocument(
                source_id=source.id,
                filename="obra.pdf",
                storage_uri="/tmp/obra.pdf",
                document_hash="h" * 64,
            )
            session.add(document)
            await session.flush()
            self.source_id, self.document_id = source.id, document.id
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _chunk(
        self,
        raw_text: str,
        *,
        role: str = "CONTENT",
        chunk_type: str = "PROSE",
        detector: str | None = DETECTOR_VERSION,
    ) -> uuid.UUID:
        self._ordinal += 1
        async with self.factory() as session:
            chunk = KnowledgeChunk(
                source_id=self.source_id,
                document_id=self.document_id,
                ordinal=self._ordinal,
                chunk_type=chunk_type,
                heading_path=[],
                page_start=self._ordinal,
                page_end=self._ordinal,
                raw_text=raw_text,
                text_hash=f"{self._ordinal:064d}",
                char_count=len(raw_text),
                editorial_role=role,
                editorial_role_confidence=0.9,
                editorial_detector_version=detector,
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


class EligibilityFilterTests(_EligibilityCase):
    async def test_content_is_returned(self):
        chunk_id = await self._chunk("A diluicao reduz a concentracao.")
        await self._index()
        result = await self._search("diluicao")
        self.assertEqual([hit.chunk_id for hit in result.hits], [chunk_id])
        self.assertEqual(result.hits[0].editorial_role, "CONTENT")

    async def test_a_table_of_contents_is_closed_in_every_purpose(self):
        await self._chunk("Capitulo 5 diluicao", role="TABLE_OF_CONTENTS")
        await self._index()
        for purpose in (None, "LEARN", "PRACTICE", "ASSESS", "AUTHOR"):
            result = await self._search("diluicao", retrieval_purpose=purpose)
            self.assertEqual(result.hits, (), purpose)
            # Com o acervo inteiro ineligivel, a razao honesta e que o corpus
            # ELEGIVEL esta vazio - nao que o indice esteja.
            self.assertIn("EMPTY_ELIGIBLE_CORPUS", result.empty_reasons)

    async def test_an_answer_key_follows_the_solution_rule(self):
        await self._chunk("Resposta: a diluicao correta.", role="ANSWER_KEY")
        await self._index()
        for purpose in ("PRACTICE", "ASSESS"):
            self.assertEqual(
                (await self._search("diluicao", retrieval_purpose=purpose)).hits,
                (),
                purpose,
            )
        for purpose in ("LEARN", "AUTHOR"):
            self.assertEqual(
                len((await self._search("diluicao", retrieval_purpose=purpose)).hits),
                1,
                purpose,
            )

    async def test_an_absent_purpose_closes_the_answer_key_but_not_content(self):
        """Proposito ausente = contexto mais restritivo entre os declarados.
        Nao e "fecha tudo": fechar CONTENT tornaria toda busca sem proposito
        vazia, e isso seria um defeito, nao uma politica."""
        conteudo = await self._chunk("A diluicao em prosa.")
        await self._chunk("Resposta: diluicao.", role="ANSWER_KEY")
        await self._index()
        result = await self._search("diluicao")
        self.assertEqual([hit.chunk_id for hit in result.hits], [conteudo])

    async def test_a_teacher_guide_is_for_authoring_only(self):
        await self._chunk("Oriente a turma sobre diluicao.", role="TEACHER_GUIDE")
        await self._index()
        self.assertEqual(
            len((await self._search("diluicao", retrieval_purpose="AUTHOR")).hits), 1
        )
        for purpose in ("LEARN", "PRACTICE", "ASSESS"):
            self.assertEqual(
                (await self._search("diluicao", retrieval_purpose=purpose)).hits,
                (),
                purpose,
            )

    async def test_either_dimension_closing_is_enough_to_close(self):
        """ORTOGONALIDADE: ``SOLUTION`` + ``ANSWER_KEY`` e combinacao legitima,
        e as duas travas convergem. Em LEARN as duas abrem; em PRACTICE as
        duas fecham, e fechariam mesmo isoladamente."""
        await self._chunk(
            "Resposta: diluicao.", role="ANSWER_KEY", chunk_type="SOLUTION"
        )
        await self._index()
        learn = await self._search("diluicao", retrieval_purpose="LEARN")
        self.assertEqual(len(learn.hits), 1)
        self.assertEqual(learn.hits[0].chunk_type, "SOLUTION")
        self.assertEqual(learn.hits[0].editorial_role, "ANSWER_KEY")

        practice = await self._search("diluicao", retrieval_purpose="PRACTICE")
        self.assertEqual(practice.hits, ())

    async def test_a_solution_in_content_still_obeys_the_phase_31_gate(self):
        """A trava da Fase 3.1 nao depende do papel editorial: um SOLUTION
        rotulado CONTENT continua fechado em PRACTICE."""
        await self._chunk("Resposta: diluicao.", role="CONTENT", chunk_type="SOLUTION")
        await self._index()
        result = await self._search("diluicao", retrieval_purpose="PRACTICE")
        self.assertEqual(result.hits, ())
        self.assertEqual(result.filtered_out.get("PURPOSE_SOLUTION"), 1)

    async def test_an_unclassified_chunk_is_never_hidden(self):
        """``editorial_detector_version IS NULL`` = nao processado. O corpus
        anterior a esta fase nao pode desaparecer da busca por causa dela."""
        chunk_id = await self._chunk(
            "A diluicao antiga.", role="UNKNOWN", detector=None
        )
        await self._index()
        result = await self._search("diluicao")
        self.assertEqual([hit.chunk_id for hit in result.hits], [chunk_id])
        self.assertIsNone(result.hits[0].editorial_detector_version)

    async def test_classified_unknown_is_distinguishable_from_unclassified(self):
        classificado = await self._chunk(
            "Diluicao com evidencia insuficiente.", role="UNKNOWN"
        )
        nao_classificado = await self._chunk(
            "Diluicao nunca processada.", role="UNKNOWN", detector=None
        )
        await self._index()
        result = await self._search("diluicao")
        por_id = {hit.chunk_id: hit for hit in result.hits}
        self.assertEqual(
            por_id[classificado].editorial_detector_version, DETECTOR_VERSION
        )
        self.assertIsNone(por_id[nao_classificado].editorial_detector_version)
        self.assertEqual(len(result.hits), 2)

    async def test_the_filter_is_counted_for_diagnosis(self):
        await self._chunk("Sumario diluicao", role="TABLE_OF_CONTENTS")
        await self._chunk("Indice diluicao", role="INDEX")
        await self._chunk("Prosa sobre diluicao.", role="CONTENT")
        await self._index()
        result = await self._search("diluicao")
        self.assertEqual(result.filtered_out.get("EDITORIAL_ROLE"), 2)
        self.assertEqual(len(result.hits), 1)


class StatisticalCorpusTests(_EligibilityCase):
    async def test_navigation_apparatus_is_out_of_n(self):
        """Era o aparato de navegacao que inflava o ``idf``: no baseline,
        ``df(estequiometria) = 74`` incluia ocorrencias de sumario."""
        await self._chunk("Prosa sobre diluicao.", role="CONTENT")
        for _ in range(4):
            await self._chunk("Sumario diluicao", role="TABLE_OF_CONTENTS")
        await self._index()
        result = await self._search("diluicao")
        self.assertEqual(result.corpus_size, 1)

    async def test_df_ignores_ineligible_chunks(self):
        await self._chunk("Prosa unica sobre diluicao.", role="CONTENT")
        await self._chunk("Prosa sobre mol.", role="CONTENT")
        for _ in range(6):
            await self._chunk("Sumario diluicao", role="TABLE_OF_CONTENTS")
        await self._index()
        result = await self._search("diluicao")
        termo = result.hits[0].explanation["query_terms"][0]
        self.assertEqual(termo["df"], 1)
        self.assertEqual(result.hits[0].explanation["corpus_size"], 2)

    async def test_the_answer_key_stays_in_the_statistics(self):
        """Ela e recuperavel em LEARN, logo pertence ao corpus de conhecimento."""
        await self._chunk("Prosa sobre diluicao.", role="CONTENT")
        await self._chunk("Resposta: diluicao.", role="ANSWER_KEY")
        await self._index()
        result = await self._search("diluicao", retrieval_purpose="LEARN")
        self.assertEqual(result.corpus_size, 2)
        self.assertEqual(result.hits[0].explanation["query_terms"][0]["df"], 2)

    async def test_the_statistics_do_not_change_with_the_purpose(self):
        """A estatistica descreve o corpus, nao a consulta. Mudar o proposito
        filtra resultados e NAO mexe no score de quem permanece."""
        conteudo = await self._chunk("Prosa sobre diluicao.", role="CONTENT")
        await self._chunk("Resposta: diluicao.", role="ANSWER_KEY")
        await self._index()
        learn = await self._search("diluicao", retrieval_purpose="LEARN")
        practice = await self._search("diluicao", retrieval_purpose="PRACTICE")
        self.assertEqual(learn.corpus_size, practice.corpus_size)
        self.assertAlmostEqual(learn.avgdl, practice.avgdl, places=9)
        por_id = {hit.chunk_id: hit for hit in learn.hits}
        self.assertAlmostEqual(
            por_id[conteudo].score, practice.hits[0].score, places=9
        )

    async def test_the_roles_are_reported_in_the_envelope(self):
        await self._chunk("Prosa sobre diluicao.")
        await self._index()
        result = await self._search("diluicao")
        self.assertEqual(result.statistical_corpus_roles, statistical_corpus_roles())

    async def test_the_definition_participates_in_the_fingerprint(self):
        """Mudar os papeis elegiveis muda o ``idf`` de TODO termo. Isso nao
        pode passar em silencio entre duas medicoes."""
        from unittest import mock

        await self._chunk("Prosa sobre diluicao.")
        await self._index()
        antes = (await self._search("diluicao")).query_fingerprint
        with mock.patch(
            "agente_ia_edu.services.knowledge_engine.lexical_search"
            ".statistical_corpus_roles",
            return_value=("CONTENT",),
        ):
            depois = (await self._search("diluicao")).query_fingerprint
        self.assertNotEqual(antes, depois)

    async def test_an_empty_eligible_corpus_is_reported_and_not_a_crash(self):
        """Corpus inteiro ineligivel e caso real de um acervo so de sumario."""
        await self._chunk("Sumario diluicao", role="TABLE_OF_CONTENTS")
        await self._index()
        result = await self._search("diluicao")
        self.assertEqual(result.hits, ())
        self.assertIn("EMPTY_ELIGIBLE_CORPUS", result.empty_reasons)
        self.assertEqual(result.filtered_out.get("EDITORIAL_ROLE"), 1)


if __name__ == "__main__":
    unittest.main()
