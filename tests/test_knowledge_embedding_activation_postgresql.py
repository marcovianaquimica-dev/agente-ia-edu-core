"""CEREBRO - Fase 6, passo 3: o que so o PostgreSQL real prova.

O SQLite nao tem isolamento entre conexoes nem ``SELECT ... FOR UPDATE``.
Entao as tres afirmacoes centrais deste passo so podem ser demonstradas aqui:

1. **A trava topologica existe e morde.** Duas linhas ativas para o mesmo
   chunk, em espacos diferentes, sao recusadas pelo BANCO - com o servico
   contornado por INSERT direto. E o que torna "a busca viu duas
   representacoes do mesmo chunk" um estado nao representavel.

2. **A troca e atomica para quem olha de fora.** Duas conexoes de verdade:
   uma faz a troca e NAO comita; a outra le e tem de ver o estado ANTERIOR,
   inteiro. Depois do commit, ve o POSTERIOR, inteiro. Nunca um meio-termo em
   que parte do corpus responde por um espaco e parte por outro - que seria
   um ranking silenciosamente incoerente, nao um erro.

3. **Concorrencia e serializada.** Duas ativacoes simultaneas: a segunda
   BLOQUEIA no ``FOR UPDATE`` e, ao passar, reavalia o mundo em vez de operar
   sobre uma leitura velha.

Banco DESCARTAVEL proprio. Nao toca o banco de desenvolvimento.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
import agente_ia_edu.db.models  # noqa: F401 - registra todo o metadata
from agente_ia_edu.db.models import (
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeDocument,
    KnowledgeEmbeddingActivation,
    KnowledgeEmbeddingSpace,
    KnowledgeSource,
)
from agente_ia_edu.knowledge_chunking_policy.v1 import retrieval_text_hash
from agente_ia_edu.providers.adapters.fake import FakeProvider
from agente_ia_edu.services.knowledge_engine.embedding import EmbeddingBackfillService
from agente_ia_edu.services.knowledge_engine.embedding_activation import (
    ROLLBACK,
    EmbeddingActivationService,
)

_DIMENSIONS = 3


class EmbeddingActivationPostgreSQL(unittest.IsolatedAsyncioTestCase):
    database_name = "agente_ia_edu_embedding_activation_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres"
    url = f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}"

    @classmethod
    def _admin(cls, statement: str) -> None:
        engine = create_engine(cls.admin_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin("SELECT 1")
        except Exception as exc:  # pragma: no cover - ambiente sem Postgres
            raise unittest.SkipTest("PostgreSQL de teste indisponivel") from exc
        cls._admin(f"DROP DATABASE IF EXISTS {cls.database_name}")
        cls._admin(f"CREATE DATABASE {cls.database_name}")
        sync = create_engine(cls.url)
        try:
            Base.metadata.create_all(sync)
        finally:
            sync.dispose()

    @classmethod
    def tearDownClass(cls):
        cls._admin(f"DROP DATABASE IF EXISTS {cls.database_name}")

    async def asyncSetUp(self):
        self.engine = create_async_engine(self.url)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=True
        )
        async with self.factory() as session:
            for table in (
                "knowledge_embedding_activations",
                "knowledge_chunk_embeddings",
                "knowledge_chunks",
                "knowledge_documents",
                "knowledge_embedding_spaces",
                "knowledge_sources",
            ):
                await session.execute(text(f"DELETE FROM {table}"))
            await session.commit()

        async with self.factory() as session:
            source = KnowledgeSource(
                title="Obra", source_kind="TEXTBOOK",
                rights_class="OWN", authority_level="OWN",
            )
            session.add(source)
            await session.flush()
            document = KnowledgeDocument(
                source_id=source.id, filename="obra.pdf",
                storage_uri="/tmp/obra.pdf", document_hash="h" * 64,
            )
            session.add(document)
            await session.flush()
            self.source_id, self.document_id = source.id, document.id
            self.chunk_ids = []
            for ordinal in range(1, 4):
                raw = f"Prosa numero {ordinal} sobre diluicao e concentracao."
                chunk = KnowledgeChunk(
                    source_id=source.id, document_id=document.id,
                    ordinal=ordinal, chunk_type="PROSE", heading_path=[],
                    page_start=ordinal, page_end=ordinal, raw_text=raw,
                    text_hash=retrieval_text_hash(raw_text=raw, heading_path=[]),
                    char_count=len(raw), editorial_role="CONTENT",
                    editorial_detector_version="v1",
                )
                session.add(chunk)
                await session.flush()
                self.chunk_ids.append(chunk.id)
            await session.commit()

        self.space_a = await self._space("modelo-a", status="ACTIVE")
        self.space_b = await self._space("modelo-b", status="BACKFILLING")
        self.space_c = await self._space("modelo-c", status="BACKFILLING")

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _space(self, model: str, *, status: str) -> uuid.UUID:
        async with self.factory() as session:
            space = KnowledgeEmbeddingSpace(
                provider="fake", model=model, dimensions=_DIMENSIONS,
                distance_metric="cosine", status=status,
            )
            session.add(space)
            await session.flush()
            space_id = space.id
            await session.commit()
        return space_id

    async def _backfill(self, space_id: uuid.UUID) -> None:
        provider = FakeProvider()
        provider.embedding_dimensions = _DIMENSIONS
        async with self.factory() as session:
            await EmbeddingBackfillService(session, provider=provider).backfill(space_id)
            await session.commit()

    async def _activate(self, space_id, *, expected=3, action="ACTIVATE"):
        async with self.factory() as session:
            service = EmbeddingActivationService(session)
            call = service.activate if action == "ACTIVATE" else service.rollback_to
            snapshot = await call(space_id, expected_population=expected)
            await session.commit()
        return snapshot

    async def _active_spaces(self, factory=None) -> list[uuid.UUID]:
        """O que a BUSCA enxergaria: espaco de cada linha ativa."""
        async with (factory or self.factory)() as session:
            rows = await session.scalars(
                select(KnowledgeChunkEmbedding.space_id).where(
                    KnowledgeChunkEmbedding.is_active.is_(True)
                )
            )
            return sorted(rows.all(), key=str)

    # -- 1. a trava topologica -------------------------------------------

    async def test_the_partial_unique_index_exists(self):
        async with self.factory() as session:
            nome = await session.scalar(
                text(
                    "SELECT indexname FROM pg_indexes WHERE indexname = "
                    "'uq_knowledge_chunk_embeddings_one_active_per_chunk'"
                )
            )
        self.assertEqual(
            nome, "uq_knowledge_chunk_embeddings_one_active_per_chunk"
        )

    async def test_two_active_rows_for_one_chunk_are_refused_by_the_database(self):
        """SERVICO CONTORNADO: INSERT direto. Se isto passasse, a garantia
        seria apenas "o servico de busca de hoje nao erra"."""
        async with self.factory() as session:
            for space_id in (self.space_a, self.space_b):
                session.add(
                    KnowledgeChunkEmbedding(
                        chunk_id=self.chunk_ids[0], space_id=space_id,
                        embedding=[0.1, 0.2, 0.3], text_hash="h" * 64,
                        is_active=True,
                    )
                )
            with self.assertRaises(IntegrityError) as caught:
                await session.flush()
        self.assertIn("one_active_per_chunk", str(caught.exception))

    async def test_the_same_chunk_may_hold_many_INACTIVE_rows(self):
        """A trava e sobre o ATIVO. Guardar o vetor de varias versoes do
        texto, e de varios espacos, e exatamente o desenho."""
        async with self.factory() as session:
            for index, space_id in enumerate((self.space_a, self.space_b, self.space_c)):
                session.add(
                    KnowledgeChunkEmbedding(
                        chunk_id=self.chunk_ids[0], space_id=space_id,
                        embedding=[0.1, 0.2, 0.3], text_hash=f"{index:064d}",
                        is_active=False,
                    )
                )
            await session.flush()
            await session.commit()
            total = await session.scalar(
                select(func.count()).select_from(KnowledgeChunkEmbedding)
            )
        self.assertEqual(total, 3)

    # -- 2. atomicidade vista de fora ------------------------------------

    async def test_an_outside_observer_never_sees_a_half_swapped_corpus(self):
        """DUAS CONEXOES. Uma troca sem comitar; a outra le.

        O estado intermediario - parte do corpus respondendo pelo espaco
        antigo e parte pelo novo - nao seria um erro: seria um ranking
        incoerente que ninguem percebe. E por isso que a prova precisa ser
        feita de fora da transacao que escreve.
        """
        await self._backfill(self.space_a)
        await self._activate(self.space_a)
        await self._backfill(self.space_b)

        observador = create_async_engine(self.url)
        observa = async_sessionmaker(
            observador, class_=AsyncSession, expire_on_commit=True
        )
        try:
            self.assertEqual(await self._active_spaces(observa), [self.space_a] * 3)

            async with self.factory() as escritor:
                await EmbeddingActivationService(escritor).activate(
                    self.space_b, expected_population=3
                )
                # A troca inteira ja aconteceu NESTA transacao, e ainda nao
                # foi comitada. De fora: o estado anterior, completo.
                durante = await self._active_spaces(observa)
                self.assertEqual(durante, [self.space_a] * 3)
                await escritor.commit()

            depois = await self._active_spaces(observa)
            self.assertEqual(depois, [self.space_b] * 3)
        finally:
            await observador.dispose()

    async def test_a_failure_mid_transaction_restores_everything(self):
        await self._backfill(self.space_a)
        await self._activate(self.space_a)
        await self._backfill(self.space_b)
        antes = await self._active_spaces()
        total_antes = await self._total_rows()

        class _Boom(RuntimeError):
            pass

        with self.assertRaises(_Boom):
            async with self.factory() as session:
                await EmbeddingActivationService(session).activate(
                    self.space_b, expected_population=3
                )
                raise _Boom("falha provocada no meio da transacao")

        self.assertEqual(await self._active_spaces(), antes)
        self.assertEqual(await self._total_rows(), total_antes)
        async with self.factory() as session:
            self.assertEqual(
                await session.scalar(
                    select(KnowledgeEmbeddingSpace.status).where(
                        KnowledgeEmbeddingSpace.id == self.space_a
                    )
                ),
                "ACTIVE",
            )
            self.assertEqual(
                await session.scalar(
                    select(KnowledgeEmbeddingSpace.status).where(
                        KnowledgeEmbeddingSpace.id == self.space_b
                    )
                ),
                "BACKFILLING",
            )
            self.assertEqual(
                await session.scalar(
                    select(func.count()).select_from(KnowledgeEmbeddingActivation)
                ),
                1,
            )

    # -- 3. concorrencia --------------------------------------------------

    async def test_a_second_activation_blocks_until_the_first_commits(self):
        """``FOR UPDATE`` nas linhas de espaco, em ordem de id.

        A segunda transacao NAO opera sobre uma leitura velha: ela espera, e
        ao passar reavalia qual espaco esta ativo de verdade.
        """
        await self._backfill(self.space_a)
        await self._activate(self.space_a)
        await self._backfill(self.space_b)
        await self._backfill(self.space_c)

        segundo_engine = create_async_engine(self.url)
        segunda = async_sessionmaker(
            segundo_engine, class_=AsyncSession, expire_on_commit=True
        )
        liberado = asyncio.Event()
        try:
            async def _concorrente():
                async with segunda() as session:
                    resultado = await EmbeddingActivationService(session).activate(
                        self.space_c, expected_population=3
                    )
                    await session.commit()
                    return resultado

            async with self.factory() as primeiro:
                await EmbeddingActivationService(primeiro).activate(
                    self.space_b, expected_population=3
                )
                tarefa = asyncio.create_task(_concorrente())
                # Enquanto a primeira nao comita, a segunda nao avanca.
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(tarefa), timeout=2.0)
                await primeiro.commit()
                liberado.set()

            resultado = await asyncio.wait_for(tarefa, timeout=30.0)
            # Ela enxergou o espaco B - que so existiu depois do commit da
            # primeira -, e nao o A que estava ativo quando ela comecou.
            self.assertEqual(resultado.previous_space_id, self.space_b)
            self.assertEqual(resultado.deactivated_rows, 3)
            self.assertEqual(await self._active_spaces(), [self.space_c] * 3)
        finally:
            liberado.set()
            await segundo_engine.dispose()

    async def test_the_topological_guard_is_translated_into_a_clear_conflict(self):
        """A ultima linha de defesa, com o ``FOR UPDATE`` CONTORNADO.

        Uma linha ativa de outro espaco, inserida por fora, e exatamente o
        que o lock nao cobre: outro processo, um script, um bug futuro. A
        ativacao bate no indice parcial e o erro cru do banco vira um erro do
        dominio, que diz o que houve e que repetir e seguro.
        """
        await self._backfill(self.space_b)
        async with self.factory() as session:
            session.add(
                KnowledgeChunkEmbedding(
                    chunk_id=self.chunk_ids[0], space_id=self.space_c,
                    embedding=[0.4, 0.5, 0.6], text_hash="z" * 64, is_active=True,
                )
            )
            await session.commit()

        from agente_ia_edu.services.knowledge_engine.embedding_activation import (
            EmbeddingActivationConflict,
        )

        with self.assertRaises(EmbeddingActivationConflict):
            async with self.factory() as session:
                await EmbeddingActivationService(session).activate(
                    self.space_b, expected_population=3
                )
                await session.commit()

    async def test_exactly_one_space_is_active_after_concurrent_swaps(self):
        await self._backfill(self.space_a)
        await self._activate(self.space_a)
        await self._backfill(self.space_b)
        await self._activate(self.space_b)
        async with self.factory() as session:
            ativos = await session.scalar(
                select(func.count())
                .select_from(KnowledgeEmbeddingSpace)
                .where(KnowledgeEmbeddingSpace.status == "ACTIVE")
            )
            chunks_ativos = await session.scalar(
                select(func.count(func.distinct(KnowledgeChunkEmbedding.chunk_id)))
                .where(KnowledgeChunkEmbedding.is_active.is_(True))
            )
            linhas_ativas = await session.scalar(
                select(func.count())
                .select_from(KnowledgeChunkEmbedding)
                .where(KnowledgeChunkEmbedding.is_active.is_(True))
            )
        self.assertEqual(ativos, 1)
        self.assertEqual(chunks_ativos, linhas_ativas)

    # -- 4. rollback no banco real ---------------------------------------

    async def test_rollback_reactivates_without_touching_generated_at(self):
        await self._backfill(self.space_a)
        await self._activate(self.space_a)
        await self._backfill(self.space_b)
        await self._activate(self.space_b)

        async with self.factory() as session:
            rows = await session.execute(
                select(
                    KnowledgeChunkEmbedding.id, KnowledgeChunkEmbedding.generated_at
                ).where(KnowledgeChunkEmbedding.space_id == self.space_a)
            )
            antes = sorted(rows.all(), key=lambda row: str(row[0]))
        total_antes = await self._total_rows()

        await self._activate(self.space_a, action=ROLLBACK)

        async with self.factory() as session:
            rows = await session.execute(
                select(
                    KnowledgeChunkEmbedding.id, KnowledgeChunkEmbedding.generated_at
                ).where(KnowledgeChunkEmbedding.space_id == self.space_a)
            )
            depois = sorted(rows.all(), key=lambda row: str(row[0]))
        self.assertEqual(depois, antes)
        self.assertEqual(await self._total_rows(), total_antes)
        self.assertEqual(await self._active_spaces(), [self.space_a] * 3)

    async def test_the_vector_column_still_holds_a_real_vector_after_a_swap(self):
        """Regressao da Fase 1: ``VectorCompatible`` com ``impl = JSON``
        gravaria ``"[0.5,-1.25]"`` com aspas. Um UPDATE em ``is_active`` nao
        pode reescrever a coluna - aqui se confirma que nao reescreveu."""
        await self._backfill(self.space_a)
        await self._activate(self.space_a)
        async with self.factory() as session:
            dims = (
                await session.execute(
                    text(
                        "SELECT DISTINCT vector_dims(embedding) "
                        "FROM knowledge_chunk_embeddings WHERE is_active"
                    )
                )
            ).scalars().all()
        self.assertEqual(dims, [_DIMENSIONS])

    async def _total_rows(self) -> int:
        async with self.factory() as session:
            return await session.scalar(
                select(func.count()).select_from(KnowledgeChunkEmbedding)
            )


if __name__ == "__main__":
    unittest.main()
