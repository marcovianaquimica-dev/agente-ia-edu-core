"""CEREBRO - Fase 6, passo 3: ativacao, rollback e historico.

O que este arquivo fixa (a atomicidade de verdade e no PostgreSQL, em
``test_knowledge_embedding_activation_postgresql.py`` - ``FOR UPDATE`` e
isolamento entre conexoes nao existem no SQLite):

1. vetor recem-gerado NAO aparece na busca sozinho;
2. ``coverage()`` sozinha NAO autoriza ativacao;
3. a troca ativa so o vetor que corresponde ao texto de HOJE;
4. rollback REATIVA linhas existentes - nao gera nada, nao apaga nada;
5. a historia da troca fica gravada.
"""

from __future__ import annotations

import inspect
import unittest
import uuid

from sqlalchemy import func, select
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
from agente_ia_edu.providers.adapters.fake import FakeProvider
from agente_ia_edu.services.knowledge_engine.embedding import EmbeddingBackfillService
from agente_ia_edu.services.knowledge_engine.embedding_activation import (
    ACTIVATE,
    DIMENSION_MISMATCH,
    MISSING_EMBEDDINGS,
    NO_EMBEDDINGS,
    POPULATION_BELOW_EXPECTED,
    ROLLBACK,
    STALE_EMBEDDINGS,
    EmbeddingActivationService,
    EmbeddingNotReady,
)


class _ActivationCase(unittest.IsolatedAsyncioTestCase):
    dimensions = 8

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
            await session.commit()
        # Espaco A ja e o corrente - exatamente como a migracao 058 deixa o
        # piloto: ACTIVE, e sem vetor nenhum.
        self.space_a = await self._space("modelo-a", status="ACTIVE")
        self.space_b = await self._space("modelo-b", status="BACKFILLING")

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _space(self, model: str, *, status: str, dimensions=None) -> uuid.UUID:
        async with self.factory() as session:
            space = KnowledgeEmbeddingSpace(
                provider="fake", model=model,
                dimensions=dimensions or self.dimensions,
                distance_metric="cosine", status=status,
            )
            session.add(space)
            await session.flush()
            space_id = space.id
            await session.commit()
        return space_id

    async def _chunk(self, raw_text: str, *, role: str = "CONTENT") -> uuid.UUID:
        self._ordinal += 1
        async with self.factory() as session:
            chunk = KnowledgeChunk(
                source_id=self.source_id, document_id=self.document_id,
                ordinal=self._ordinal, chunk_type="PROSE", heading_path=[],
                page_start=self._ordinal, page_end=self._ordinal,
                raw_text=raw_text,
                text_hash=retrieval_text_hash(raw_text=raw_text, heading_path=[]),
                char_count=len(raw_text), editorial_role=role,
                editorial_detector_version="v1",
            )
            session.add(chunk)
            await session.flush()
            chunk_id = chunk.id
            await session.commit()
        return chunk_id

    async def _backfill(self, space_id: uuid.UUID, dimensions: int | None = None):
        provider = FakeProvider()
        if dimensions is not None:
            provider.embedding_dimensions = dimensions
        async with self.factory() as session:
            snapshot = await EmbeddingBackfillService(
                session, provider=provider
            ).backfill(space_id)
            await session.commit()
        return snapshot

    async def _activate(self, space_id, *, expected, action=ACTIVATE, **kwargs):
        async with self.factory() as session:
            service = EmbeddingActivationService(session)
            call = service.activate if action == ACTIVATE else service.rollback_to
            snapshot = await call(space_id, expected_population=expected, **kwargs)
            await session.commit()
        return snapshot

    async def _active_rows(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        async with self.factory() as session:
            rows = await session.execute(
                select(
                    KnowledgeChunkEmbedding.chunk_id, KnowledgeChunkEmbedding.space_id
                ).where(KnowledgeChunkEmbedding.is_active.is_(True))
            )
            return sorted(rows.all())

    async def _total_rows(self) -> int:
        async with self.factory() as session:
            return await session.scalar(
                select(func.count()).select_from(KnowledgeChunkEmbedding)
            )

    async def _status(self, space_id: uuid.UUID) -> str:
        async with self.factory() as session:
            return await session.scalar(
                select(KnowledgeEmbeddingSpace.status).where(
                    KnowledgeEmbeddingSpace.id == space_id
                )
            )


class StructuralTests(_ActivationCase):
    def test_the_activation_service_cannot_reach_a_provider(self):
        """Prova ESTRUTURAL de "rollback nao regenera": nao ha por onde.

        ``__init__`` recebe a sessao e nada mais. Sem parametro de provider
        nao existe caminho de codigo - nem futuro, nem por engano - em que
        ativar ou reverter um espaco gaste dinheiro com o fornecedor.
        """
        parametros = list(
            inspect.signature(EmbeddingActivationService.__init__).parameters
        )
        self.assertEqual(parametros, ["self", "session"])


class FreshEmbeddingsAreInvisibleTests(_ActivationCase):
    async def test_a_backfilled_space_is_not_searchable_until_activated(self):
        await self._chunk("Prosa sobre diluicao.")
        await self._chunk("Prosa sobre concentracao.")
        await self._backfill(self.space_b)
        self.assertEqual(await self._total_rows(), 2)
        self.assertEqual(await self._active_rows(), [])


class ReadinessGateTests(_ActivationCase):
    async def test_coverage_alone_does_not_authorise_activation(self):
        """O portao central do passo 3.

        Corpus vazio: ``missing = 0``, cobertura trivialmente completa. Se o
        criterio fosse so a razao, um acervo truncado por acidente passaria
        exibindo 100%. ``expected_population`` e o numero absoluto que
        transforma isso em recusa.
        """
        from agente_ia_edu.services.knowledge_engine.embedding import coverage

        async with self.factory() as session:
            cobertura = await coverage(session, self.space_b)
            self.assertEqual(cobertura.missing, 0)

            prontidao = await EmbeddingActivationService(session).readiness(
                self.space_b, expected_population=2
            )
        codigos = {item["code"] for item in prontidao.violations}
        self.assertFalse(prontidao.is_ready)
        self.assertIn(POPULATION_BELOW_EXPECTED, codigos)
        self.assertIn(NO_EMBEDDINGS, codigos)

    async def test_a_shrunken_corpus_is_refused_even_at_full_coverage(self):
        await self._chunk("Prosa sobre diluicao.")
        await self._backfill(self.space_b)
        async with self.factory() as session:
            prontidao = await EmbeddingActivationService(session).readiness(
                self.space_b, expected_population=5_900
            )
        self.assertEqual(prontidao.coverage.missing, 0)
        self.assertFalse(prontidao.is_ready)
        self.assertEqual(
            [item["code"] for item in prontidao.violations],
            [POPULATION_BELOW_EXPECTED],
        )

    async def test_a_missing_embedding_blocks_activation(self):
        await self._chunk("Prosa sobre diluicao.")
        await self._backfill(self.space_b)
        await self._chunk("Prosa nova, ainda sem vetor.")
        async with self.factory() as session:
            prontidao = await EmbeddingActivationService(session).readiness(
                self.space_b, expected_population=2
            )
        self.assertIn(
            MISSING_EMBEDDINGS, {item["code"] for item in prontidao.violations}
        )

    async def test_a_stale_embedding_blocks_activation(self):
        chunk_id = await self._chunk("Texto original.")
        await self._backfill(self.space_b)
        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, chunk_id)
            chunk.raw_text = "Texto revisado."
            chunk.text_hash = retrieval_text_hash(
                raw_text=chunk.raw_text, heading_path=[]
            )
            await session.commit()
        async with self.factory() as session:
            prontidao = await EmbeddingActivationService(session).readiness(
                self.space_b, expected_population=1
            )
        self.assertIn(
            STALE_EMBEDDINGS, {item["code"] for item in prontidao.violations}
        )

    async def test_a_stored_vector_of_the_wrong_length_blocks_activation(self):
        """Redundante com a validacao do backfill DE PROPOSITO: aquela ve o
        que o provider devolveu, esta ve o que esta no disco - inclusive o
        que entrou por script ou por uma versao anterior do servico."""
        chunk_id = await self._chunk("Prosa sobre diluicao.")
        await self._backfill(self.space_b)
        async with self.factory() as session:
            row = await session.scalar(
                select(KnowledgeChunkEmbedding).where(
                    KnowledgeChunkEmbedding.chunk_id == chunk_id
                )
            )
            row.embedding = [0.1, 0.2, 0.3]  # o espaco declara 8
            await session.commit()
        async with self.factory() as session:
            prontidao = await EmbeddingActivationService(session).readiness(
                self.space_b, expected_population=1
            )
        self.assertEqual(prontidao.dimension_violations, 1)
        self.assertIn(
            DIMENSION_MISMATCH, {item["code"] for item in prontidao.violations}
        )

    async def test_readiness_reports_every_violation_at_once(self):
        """Quem opera precisa ver o quadro completo numa passada."""
        async with self.factory() as session:
            prontidao = await EmbeddingActivationService(session).readiness(
                self.space_b, expected_population=10
            )
        self.assertGreaterEqual(len(prontidao.violations), 2)

    async def test_activate_refuses_when_not_ready(self):
        await self._chunk("Prosa sobre diluicao.")
        with self.assertRaises(EmbeddingNotReady):
            await self._activate(self.space_b, expected=1)
        self.assertEqual(await self._active_rows(), [])
        self.assertEqual(await self._status(self.space_b), "BACKFILLING")


class SwapTests(_ActivationCase):
    async def test_the_first_activation_turns_on_the_current_space(self):
        """A migracao 058 deixou o espaco inicial ACTIVE e sem vetor algum.
        Ligar as linhas desse mesmo espaco e a operacao que falta, nao um
        conflito."""
        await self._chunk("Prosa sobre diluicao.")
        await self._backfill(self.space_a)
        snapshot = await self._activate(self.space_a, expected=1)
        self.assertEqual(snapshot.activated_rows, 1)
        self.assertEqual(snapshot.deactivated_rows, 0)
        # ``previous_space_id`` guarda o que estava ativo ANTES, e aqui o que
        # estava ativo antes era este mesmo espaco. O auto-laco e o registro
        # honesto; ``deactivated_rows = 0`` ao lado dele diz, sem ambiguidade,
        # que nada foi trocado - apenas ligado.
        self.assertEqual(snapshot.previous_space_id, self.space_a)
        self.assertEqual(len(await self._active_rows()), 1)

    async def test_a_swap_moves_every_chunk_to_the_new_space(self):
        for index in range(3):
            await self._chunk(f"Prosa numero {index} sobre diluicao.")
        await self._backfill(self.space_a)
        await self._activate(self.space_a, expected=3)
        await self._backfill(self.space_b)

        snapshot = await self._activate(self.space_b, expected=3)
        self.assertEqual(snapshot.previous_space_id, self.space_a)
        self.assertEqual(snapshot.activated_rows, 3)
        self.assertEqual(snapshot.deactivated_rows, 3)
        self.assertEqual(
            {space_id for _, space_id in await self._active_rows()}, {self.space_b}
        )
        self.assertEqual(await self._status(self.space_a), "RETIRED")
        self.assertEqual(await self._status(self.space_b), "ACTIVE")

    async def test_no_chunk_ever_has_two_active_representations(self):
        for index in range(3):
            await self._chunk(f"Prosa numero {index}.")
        await self._backfill(self.space_a)
        await self._activate(self.space_a, expected=3)
        await self._backfill(self.space_b)
        await self._activate(self.space_b, expected=3)
        ativos = await self._active_rows()
        chunk_ids = [chunk_id for chunk_id, _ in ativos]
        self.assertEqual(len(chunk_ids), len(set(chunk_ids)))

    async def test_only_the_vector_of_todays_text_is_activated(self):
        """Um espaco guarda varias versoes do mesmo chunk, por desenho.
        Ativar todas violaria a trava e, pior, ativaria o vetor de um texto
        que ja nao existe."""
        chunk_id = await self._chunk("Texto original.")
        await self._backfill(self.space_b)
        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, chunk_id)
            chunk.raw_text = "Texto revisado."
            novo_hash = retrieval_text_hash(raw_text=chunk.raw_text, heading_path=[])
            chunk.text_hash = novo_hash
            await session.commit()
        await self._backfill(self.space_b)
        self.assertEqual(await self._total_rows(), 2)

        snapshot = await self._activate(self.space_b, expected=1)
        self.assertEqual(snapshot.activated_rows, 1)
        async with self.factory() as session:
            ativo = await session.scalar(
                select(KnowledgeChunkEmbedding.text_hash).where(
                    KnowledgeChunkEmbedding.is_active.is_(True)
                )
            )
        self.assertEqual(ativo, novo_hash)

    async def test_no_vector_is_ever_deleted(self):
        await self._chunk("Prosa sobre diluicao.")
        await self._backfill(self.space_a)
        await self._activate(self.space_a, expected=1)
        await self._backfill(self.space_b)
        antes = await self._total_rows()
        await self._activate(self.space_b, expected=1)
        self.assertEqual(await self._total_rows(), antes)


class RollbackTests(_ActivationCase):
    async def _two_spaces_swapped(self, quantos: int = 2):
        for index in range(quantos):
            await self._chunk(f"Prosa numero {index} sobre diluicao.")
        await self._backfill(self.space_a)
        await self._activate(self.space_a, expected=quantos)
        await self._backfill(self.space_b)
        await self._activate(self.space_b, expected=quantos)

    async def test_rollback_reactivates_the_existing_rows(self):
        """A evidencia de que NAO houve nova geracao: ``generated_at`` das
        linhas reativadas e byte a byte o mesmo de antes da troca."""
        await self._two_spaces_swapped()
        async with self.factory() as session:
            rows = await session.execute(
                select(
                    KnowledgeChunkEmbedding.id, KnowledgeChunkEmbedding.generated_at
                ).where(KnowledgeChunkEmbedding.space_id == self.space_a)
            )
            antes = sorted(rows.all())
        total_antes = await self._total_rows()

        snapshot = await self._activate(self.space_a, expected=2, action=ROLLBACK)
        self.assertEqual(snapshot.action, ROLLBACK)
        self.assertEqual(snapshot.activated_rows, 2)
        self.assertEqual(await self._total_rows(), total_antes)

        async with self.factory() as session:
            rows = await session.execute(
                select(
                    KnowledgeChunkEmbedding.id, KnowledgeChunkEmbedding.generated_at
                ).where(KnowledgeChunkEmbedding.space_id == self.space_a)
            )
            self.assertEqual(sorted(rows.all()), antes)
        self.assertEqual(
            {space_id for _, space_id in await self._active_rows()}, {self.space_a}
        )
        self.assertEqual(await self._status(self.space_a), "ACTIVE")
        self.assertEqual(await self._status(self.space_b), "RETIRED")

    async def test_rollback_to_a_retired_space_is_not_a_violation(self):
        await self._two_spaces_swapped()
        self.assertEqual(await self._status(self.space_a), "RETIRED")
        snapshot = await self._activate(self.space_a, expected=2, action=ROLLBACK)
        self.assertFalse(snapshot.degraded)
        self.assertEqual(snapshot.violations, ())

    async def test_a_degraded_rollback_is_refused_by_default(self):
        """Emergencia nao autoriza silencio: por padrao os portoes valem."""
        await self._two_spaces_swapped()
        await self._chunk("Chunk novo, que o espaco A nunca viu.")
        with self.assertRaises(EmbeddingNotReady):
            await self._activate(self.space_a, expected=2, action=ROLLBACK)

    async def test_a_degraded_rollback_must_leave_a_trace(self):
        await self._two_spaces_swapped()
        await self._chunk("Chunk novo, que o espaco A nunca viu.")
        snapshot = await self._activate(
            self.space_a, expected=2, action=ROLLBACK,
            allow_degraded=True, reason="incidente em producao",
        )
        self.assertTrue(snapshot.degraded)
        self.assertIn(
            MISSING_EMBEDDINGS, {item["code"] for item in snapshot.violations}
        )
        async with self.factory() as session:
            gravado = await session.scalar(
                select(KnowledgeEmbeddingActivation).where(
                    KnowledgeEmbeddingActivation.id == snapshot.activation_id
                )
            )
            self.assertTrue(gravado.degraded)
            self.assertEqual(gravado.reason, "incidente em producao")
            self.assertTrue(gravado.violations)


class HistoryTests(_ActivationCase):
    async def test_every_swap_leaves_a_linked_record(self):
        """``activated_at`` na linha do espaco guarda so a ultima vez e e
        sobrescrito no rollback. "Por que o ranking mudou na quinta?" so tem
        resposta porque o EVENTO fica gravado."""
        await self._chunk("Prosa sobre diluicao.")
        await self._backfill(self.space_a)
        await self._activate(self.space_a, expected=1, actor="marco")
        await self._backfill(self.space_b)
        await self._activate(self.space_b, expected=1, actor="marco")
        await self._activate(self.space_a, expected=1, action=ROLLBACK, actor="marco")

        async with self.factory() as session:
            historico = await EmbeddingActivationService(session).history()
        self.assertEqual([item.action for item in historico], [ROLLBACK, ACTIVATE, ACTIVATE])
        self.assertEqual(historico[0].space_id, self.space_a)
        self.assertEqual(historico[0].previous_space_id, self.space_b)
        self.assertEqual(historico[1].previous_space_id, self.space_a)
        # O evento mais antigo e a primeira ativacao do piloto: o espaco A ja
        # era o corrente (migracao 058 o semeia ACTIVE e sem vetor), entao o
        # que estava ativo antes era ele proprio.
        self.assertEqual(historico[2].previous_space_id, self.space_a)
        self.assertEqual(historico[2].deactivated_rows, 0)
        self.assertEqual({item.actor for item in historico}, {"marco"})

    async def test_the_record_carries_the_policy_and_the_population(self):
        await self._chunk("Prosa sobre diluicao.")
        await self._chunk("Sumario ....... 12", role="TABLE_OF_CONTENTS")
        await self._backfill(self.space_a)
        snapshot = await self._activate(self.space_a, expected=1)
        self.assertEqual(snapshot.policy, "ELIGIBLE_ROLES")
        self.assertEqual(snapshot.expected_population, 1)
        self.assertEqual(snapshot.eligible_chunks, 1)


class InducedFailureTests(_ActivationCase):
    async def test_a_failure_mid_transaction_restores_the_previous_state(self):
        """Nao ha logica de compensacao - ha uma transacao. Essa e a razao
        de o servico nao comitar."""
        for index in range(2):
            await self._chunk(f"Prosa numero {index}.")
        await self._backfill(self.space_a)
        await self._activate(self.space_a, expected=2)
        await self._backfill(self.space_b)
        antes_ativos = await self._active_rows()
        antes_total = await self._total_rows()

        class _Boom(RuntimeError):
            pass

        with self.assertRaises(_Boom):
            async with self.factory() as session:
                await EmbeddingActivationService(session).activate(
                    self.space_b, expected_population=2
                )
                raise _Boom("falha depois da troca e antes do commit")

        self.assertEqual(await self._active_rows(), antes_ativos)
        self.assertEqual(await self._total_rows(), antes_total)
        self.assertEqual(await self._status(self.space_a), "ACTIVE")
        self.assertEqual(await self._status(self.space_b), "BACKFILLING")
        async with self.factory() as session:
            gravados = await session.scalar(
                select(func.count()).select_from(KnowledgeEmbeddingActivation)
            )
        self.assertEqual(gravados, 1)  # so a primeira ativacao


if __name__ == "__main__":
    unittest.main()
