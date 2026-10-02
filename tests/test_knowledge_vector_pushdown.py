"""CEREBRO - Fase 6, passo 4.1: filtros ANTES do corte, e cobertura sem corpus.

O DEFEITO QUE ISTO CORRIGE FOI MEDIDO NO CORPUS REAL
====================================================

No passo 5, com 5.911 vetores, uma busca restrita a ``OFFICIAL_PUBLIC`` (a
BNCC, 23 chunks) nao achava NENHUM deles em 4 de 7 consultas sanitarias.
Nao porque o material faltasse - ele estava la -, mas porque nenhum dos 23
estava entre os 2.000 vetores mais proximos do acervo INTEIRO, e o filtro so
corria depois do corte.

Falso vazio: a busca dizia "nada encontrado" sobre um corpus que tinha a
resposta. O erro invisivel, de novo - ninguem percebe o resultado que nao
veio.

Os testes abaixo reproduzem isso em escala pequena, com ``candidate_cap``
reduzido. A constante da politica NAO mudou: ela continua 2.000, e o
parametro so aceita valores MENORES, justamente para nao virar atalho para o
defeito que ele existe para demonstrar.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import event, func, select
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
from agente_ia_edu.knowledge_retrieval_policy.v1 import POLICY
from agente_ia_edu.services.knowledge_engine.embedding import coverage
from agente_ia_edu.services.knowledge_engine.vector_search import (
    FILTERED_OUT_SCOPE,
    VectorSearcher,
    VectorSearchError,
)

from test_knowledge_vector_search import _ScriptedProvider

_DIMS = 3
_QUERY = "consulta"


class _PushdownCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=True
        )
        self.chunks: dict[str, uuid.UUID] = {}
        self.sources: dict[str, uuid.UUID] = {}
        self.documents: dict[str, uuid.UUID] = {}
        self._ordinal = 0
        async with self.factory() as session:
            space = KnowledgeEmbeddingSpace(
                provider="fake", model="modelo-a", dimensions=_DIMS,
                distance_metric="cosine", status="ACTIVE",
            )
            session.add(space)
            await session.flush()
            self.space_id = space.id
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _source(self, key, *, rights, authority, kind, title):
        async with self.factory() as session:
            source = KnowledgeSource(
                title=title, source_kind=kind,
                rights_class=rights, authority_level=authority,
            )
            session.add(source)
            await session.flush()
            document = KnowledgeDocument(
                source_id=source.id, filename=f"{key}.pdf",
                storage_uri=f"/tmp/{key}.pdf", document_hash=uuid.uuid4().hex * 2,
            )
            session.add(document)
            await session.flush()
            self.sources[key], self.documents[key] = source.id, document.id
            await session.commit()

    async def _chunk_with_vector(
        self, key, vector, *, source, chunk_type="PROSE", role="CONTENT"
    ):
        self._ordinal += 1
        raw = f"Texto do chunk {key}."
        async with self.factory() as session:
            chunk = KnowledgeChunk(
                source_id=self.sources[source], document_id=self.documents[source],
                ordinal=self._ordinal, chunk_type=chunk_type, heading_path=["Cap"],
                page_start=self._ordinal, page_end=self._ordinal, raw_text=raw,
                text_hash=retrieval_text_hash(raw_text=raw, heading_path=["Cap"]),
                char_count=len(raw), editorial_role=role,
                editorial_detector_version="v1", bncc_node_codes=[],
            )
            session.add(chunk)
            await session.flush()
            self.chunks[key] = chunk.id
            session.add(
                KnowledgeChunkEmbedding(
                    chunk_id=chunk.id, space_id=self.space_id, embedding=vector,
                    text_hash=chunk.text_hash, is_active=True,
                )
            )
            await session.commit()

    async def _activate(self):
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

    async def _search(self, **kwargs):
        provider = _ScriptedProvider({_QUERY: (1.0, 0.0, 0.0)})
        async with self.factory() as session:
            return await VectorSearcher(session, provider=provider).search(
                _QUERY, limit=10, **kwargs
            )


class FalseEmptyTests(_PushdownCase):
    """O defeito exato do corpus real, em escala pequena.

    Oito chunks comerciais colados na consulta e UM da BNCC, longe. Com corte
    em 3, a BNCC jamais entraria num pool formado antes do filtro - e era
    assim que a busca respondia "nada encontrado" sobre material existente.
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._source(
            "comercial", rights="COMMERCIAL_REFERENCE",
            authority="COMMERCIAL_TEXTBOOK", kind="TEXTBOOK", title="Livro",
        )
        await self._source(
            "bncc", rights="OFFICIAL_PUBLIC", authority="OFFICIAL",
            kind="CURRICULUM_FRAMEWORK", title="BNCC",
        )
        # Oito comerciais quase colados em (1,0,0).
        for indice in range(8):
            await self._chunk_with_vector(
                f"com{indice}", [1.0, 0.001 * (indice + 1), 0.0], source="comercial"
            )
        # A BNCC, bem mais longe - rank global 9 de 9.
        await self._chunk_with_vector(
            "bncc0", [0.0, 1.0, 0.0], source="bncc",
            chunk_type="CURRICULUM_ITEM",
        )
        await self._activate()

    async def test_without_the_filter_the_bncc_chunk_is_the_last_one(self):
        """A premissa do teste, afirmada em vez de suposta."""
        resultado = await self._search(retrieval_purpose="LEARN")
        self.assertEqual(resultado.hits[-1].chunk_id, self.chunks["bncc0"])

    async def test_a_restrictive_filter_finds_what_is_outside_the_global_top_n(self):
        """O CORACAO DO PASSO 4.1.

        Corte em 3: o top-3 global e todo comercial, e a BNCC esta fora dele.
        Com o filtro aplicado ANTES do corte, o pool de 3 e formado entre as
        ELEGIVEIS - e a BNCC aparece.
        """
        resultado = await self._search(
            retrieval_purpose="LEARN",
            rights_classes=["OFFICIAL_PUBLIC"],
            candidate_cap=3,
        )
        self.assertEqual(resultado.returned, 1)
        self.assertEqual(resultado.hits[0].chunk_id, self.chunks["bncc0"])
        self.assertEqual(resultado.empty_reasons, ())

    async def test_the_same_holds_for_exclude_commercial(self):
        resultado = await self._search(
            retrieval_purpose="LEARN", exclude_commercial=True, candidate_cap=3
        )
        self.assertEqual(
            [hit.chunk_id for hit in resultado.hits], [self.chunks["bncc0"]]
        )

    async def test_the_same_holds_for_source_kind(self):
        resultado = await self._search(
            retrieval_purpose="LEARN",
            source_kinds=["CURRICULUM_FRAMEWORK"], candidate_cap=3,
        )
        self.assertEqual(
            [hit.chunk_id for hit in resultado.hits], [self.chunks["bncc0"]]
        )

    async def test_the_same_holds_for_source_id(self):
        resultado = await self._search(
            retrieval_purpose="LEARN",
            source_ids=[self.sources["bncc"]], candidate_cap=3,
        )
        self.assertEqual(
            [hit.chunk_id for hit in resultado.hits], [self.chunks["bncc0"]]
        )

    async def test_the_same_holds_for_document_id(self):
        resultado = await self._search(
            retrieval_purpose="LEARN",
            document_ids=[self.documents["bncc"]], candidate_cap=3,
        )
        self.assertEqual(
            [hit.chunk_id for hit in resultado.hits], [self.chunks["bncc0"]]
        )

    async def test_the_same_holds_for_chunk_type(self):
        resultado = await self._search(
            retrieval_purpose="LEARN",
            chunk_types=["CURRICULUM_ITEM"], candidate_cap=3,
        )
        self.assertEqual(
            [hit.chunk_id for hit in resultado.hits], [self.chunks["bncc0"]]
        )

    async def test_the_cap_still_limits_within_the_eligible_set(self):
        """O corte nao sumiu - ele passou a valer sobre o conjunto certo."""
        resultado = await self._search(retrieval_purpose="LEARN", candidate_cap=2)
        self.assertEqual(resultado.total_candidates, 2)
        self.assertTrue(resultado.candidate_cap_reached)

    async def test_the_cap_cannot_be_raised_above_the_policy(self):
        """O parametro so desce. Subir seria contornar o defeito em vez de
        corrigi-lo."""
        resultado = await self._search(
            retrieval_purpose="LEARN", candidate_cap=10_000_000
        )
        self.assertEqual(
            resultado.applied_filters["candidate_cap"], POLICY.candidate_cap
        )

    async def test_a_cap_below_one_is_refused(self):
        with self.assertRaises(VectorSearchError) as caught:
            await self._search(candidate_cap=0)
        self.assertEqual(caught.exception.code, "INVALID_CANDIDATE_CAP")


class PolicyPushdownTests(_PushdownCase):
    """Elegibilidade editorial e SOLUTION tambem descem - e a politica
    continua decidindo em Python."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._source(
            "propria", rights="OWN", authority="OWN",
            kind="OWN_MATERIAL", title="Apostila",
        )
        for indice in range(6):
            await self._chunk_with_vector(
                f"gab{indice}", [1.0, 0.001 * (indice + 1), 0.0],
                source="propria", chunk_type="SOLUTION", role="ANSWER_KEY",
            )
        await self._chunk_with_vector(
            "sumario", [1.0, 0.0, 0.0], source="propria",
            role="TABLE_OF_CONTENTS",
        )
        await self._chunk_with_vector(
            "prosa", [0.0, 1.0, 0.0], source="propria"
        )
        await self._activate()

    async def test_solution_pushdown_finds_content_beyond_the_cap(self):
        """Seis gabaritos mais perto que a prosa. Em PRACTICE eles estao
        fechados, e com o filtro antes do corte a prosa aparece mesmo com o
        pool em 3."""
        resultado = await self._search(
            retrieval_purpose="PRACTICE", candidate_cap=3
        )
        self.assertEqual(
            [hit.chunk_id for hit in resultado.hits], [self.chunks["prosa"]]
        )

    async def test_editorial_role_pushdown_also_applies(self):
        resultado = await self._search(retrieval_purpose="LEARN", candidate_cap=10)
        achados = {hit.chunk_id for hit in resultado.hits}
        self.assertNotIn(self.chunks["sumario"], achados)

    async def test_an_unknown_editorial_role_still_fails_open(self):
        """A falha ABERTA da politica sobrevive ao pushdown: papel que a
        politica nao conhece nao entra na lista de inelegiveis, logo o SQL
        nao o rejeita.

        O ``allow_degraded`` aqui nao e contorno - e o encontro das DUAS
        politicas independentes do passo 2. Elegibilidade de RECUPERACAO
        falha aberta, porque esconder conteudo e o erro invisivel.
        Elegibilidade de EMBEDDING e uma lista FECHADA, porque e decisao de
        custo e ninguem paga por papel que nao conhece. Entao um papel
        inedito continua recuperavel e deixa de contar na populacao
        elegivel - e o portao de cobertura percebe a mudanca, que e o
        trabalho dele.
        """
        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, self.chunks["prosa"])
            chunk.editorial_role = "PAPEL_QUE_NAO_EXISTE_AINDA"
            await session.commit()
        resultado = await self._search(
            retrieval_purpose="LEARN", allow_degraded=True
        )
        self.assertIn("POPULATION_BELOW_EXPECTED", resultado.degradation_reasons)
        self.assertIn(
            self.chunks["prosa"], {hit.chunk_id for hit in resultado.hits}
        )


class AttributionTests(_PushdownCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._source(
            "propria", rights="OWN", authority="OWN",
            kind="OWN_MATERIAL", title="Apostila",
        )
        await self._chunk_with_vector("prosa", [1.0, 0.0, 0.0], source="propria")
        await self._chunk_with_vector(
            "gabarito", [0.9, 0.1, 0.0], source="propria",
            chunk_type="SOLUTION", role="ANSWER_KEY",
        )
        await self._chunk_with_vector(
            "sumario", [0.8, 0.2, 0.0], source="propria",
            role="TABLE_OF_CONTENTS",
        )
        await self._activate()

    async def test_attribution_survives_the_pushdown(self):
        """O pushdown NAO custou a atribuicao - era o risco obvio dele."""
        resultado = await self._search(retrieval_purpose="PRACTICE")
        self.assertEqual(resultado.filtered_out["PURPOSE_SOLUTION"], 1)
        self.assertEqual(resultado.filtered_out["EDITORIAL_ROLE"], 1)

    async def test_the_scope_of_the_count_is_declared(self):
        """Mudou de "dentro do pool cortado" para "corpus ativo inteiro". A
        contagem antiga variava com a consulta, o que a tornava um artefato
        do corte em vez de um fato sobre o acervo."""
        resultado = await self._search(retrieval_purpose="PRACTICE")
        self.assertEqual(resultado.filtered_out_scope, FILTERED_OUT_SCOPE)
        self.assertEqual(FILTERED_OUT_SCOPE, "ACTIVE_CORPUS")

    async def test_the_count_does_not_change_with_the_cap(self):
        """A prova de que a contagem virou um fato sobre o acervo: cortar o
        pool em 1 nao muda quantos a politica excluiu."""
        largo = await self._search(retrieval_purpose="PRACTICE")
        estreito = await self._search(retrieval_purpose="PRACTICE", candidate_cap=1)
        self.assertEqual(largo.filtered_out, estreito.filtered_out)

    async def test_the_first_matching_reason_wins(self):
        """Mesma precedencia do passo 4: o gabarito e SOLUTION e tambem
        ANSWER_KEY, e e contado uma vez so, pela politica de maior
        precedencia."""
        resultado = await self._search(retrieval_purpose="PRACTICE")
        self.assertEqual(sum(resultado.filtered_out.values()), 2)


class CoverageSqlTests(_PushdownCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._source(
            "comercial", rights="COMMERCIAL_REFERENCE",
            authority="COMMERCIAL_TEXTBOOK", kind="TEXTBOOK", title="Livro",
        )
        self.literal = "LITERAL PROTEGIDO QUE NAO PODE SER LIDO PARA CONTAR"
        self._ordinal += 1
        async with self.factory() as session:
            chunk = KnowledgeChunk(
                source_id=self.sources["comercial"],
                document_id=self.documents["comercial"],
                ordinal=99, chunk_type="PROSE", heading_path=["Cap"],
                page_start=1, page_end=1, raw_text=self.literal,
                text_hash=retrieval_text_hash(
                    raw_text=self.literal, heading_path=["Cap"]
                ),
                char_count=len(self.literal), editorial_role="CONTENT",
                editorial_detector_version="v1", bncc_node_codes=[],
            )
            session.add(chunk)
            await session.flush()
            self.chunks["comercial"] = chunk.id
            session.add(
                KnowledgeChunkEmbedding(
                    chunk_id=chunk.id, space_id=self.space_id,
                    embedding=[1.0, 0.0, 0.0], text_hash=chunk.text_hash,
                    is_active=True,
                )
            )
            await session.commit()

    async def test_coverage_never_selects_raw_text(self):
        """A versao anterior carregava os chunks como entidades ORM
        COMPLETAS so para conta-los - 230 ms por busca no corpus real, e com
        literal de obra comercial indo para a memoria do processo sem
        necessidade alguma."""
        emitidos: list[str] = []

        def _capturar(conn, cursor, statement, parameters, context, many):
            emitidos.append(statement)

        event.listen(self.engine.sync_engine, "before_cursor_execute", _capturar)
        try:
            async with self.factory() as session:
                await coverage(session, self.space_id)
        finally:
            event.remove(
                self.engine.sync_engine, "before_cursor_execute", _capturar
            )

        self.assertTrue(emitidos)
        for statement in emitidos:
            self.assertNotIn("raw_text", statement, statement)

    async def test_coverage_is_a_single_aggregate_query(self):
        contadas: list[str] = []

        def _capturar(conn, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith("SELECT"):
                contadas.append(statement)

        event.listen(self.engine.sync_engine, "before_cursor_execute", _capturar)
        try:
            async with self.factory() as session:
                await coverage(session, self.space_id)
        finally:
            event.remove(
                self.engine.sync_engine, "before_cursor_execute", _capturar
            )
        # Uma para ler o espaco, uma para agregar. Nenhuma varredura de corpus.
        self.assertLessEqual(len(contadas), 2, contadas)

    async def test_coverage_still_answers_the_same_numbers(self):
        """Semantica preservada: a refatoracao e de custo, nao de
        significado."""
        async with self.factory() as session:
            antes = await coverage(session, self.space_id)
        self.assertEqual(antes.eligible_chunks, 1)
        self.assertEqual(antes.embedded, 1)
        self.assertEqual(antes.missing, 0)
        self.assertEqual(antes.stale, 0)

        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, self.chunks["comercial"])
            chunk.raw_text = "Texto revisado."
            chunk.text_hash = retrieval_text_hash(
                raw_text=chunk.raw_text, heading_path=["Cap"]
            )
            await session.commit()
        async with self.factory() as session:
            depois = await coverage(session, self.space_id)
        self.assertEqual(depois.eligible_chunks, 1)
        self.assertEqual(depois.embedded, 0)
        self.assertEqual(depois.missing, 1)
        self.assertEqual(depois.stale, 1)

    async def test_coverage_of_an_empty_corpus_is_zero_not_an_error(self):
        async with self.factory() as session:
            await session.execute(
                KnowledgeChunkEmbedding.__table__.delete()
            )
            await session.execute(KnowledgeChunk.__table__.delete())
            await session.commit()
        async with self.factory() as session:
            vazio = await coverage(session, self.space_id)
        self.assertEqual(vazio.eligible_chunks, 0)
        self.assertEqual(vazio.embedded, 0)
        self.assertEqual(vazio.missing, 0)
        self.assertEqual(vazio.stale, 0)


if __name__ == "__main__":
    unittest.main()
