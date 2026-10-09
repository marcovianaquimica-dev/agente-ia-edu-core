"""CEREBRO - Fase 6: o servico de embedding do corpus.

O QUE ELE GARANTE, E POR QUE CADA GARANTIA EXISTE
=================================================

**Provider-neutro.** Provider, modelo e dimensao vem da linha de
``KnowledgeEmbeddingSpace``. O servico monta ``EmbeddingRequest(model=...)``
com o modelo do ESPACO e nunca nomeia fornecedor nem modelo.

**Dimensao validada ANTES de persistir.** A coluna ``vector`` nao tem
tamanho - por desenho, para que trocar de modelo nao seja migracao de tabela
-, entao o banco aceitaria um vetor de comprimento errado em silencio. A
validacao e do servico, e e ela que impede um corpus meio-1536 meio-outra
coisa.

**Idempotencia por ``(chunk_id, space_id, text_hash)``.** Ja existe como
UNIQUE desde a Fase 1. O servico consulta ANTES de chamar o provider: o
custo de reembeddar e dinheiro, e a segunda execucao tem de custar zero.

**Falha parcial nao derruba a execucao.** Um lote que falha e registrado e a
execucao segue; retomar e seguro justamente por causa do UNIQUE.

**Elegibilidade de EMBEDDING e de RECUPERACAO sao coisas diferentes.**
``retrieval_eligibility=False`` impede recuperar. Nao significa "este chunk
jamais pode ter vetor". A politica de backfill do piloto e uma escolha de
CUSTO, declarada e observavel, e mudar a elegibilidade de um chunk nao muda
o seu ``text_hash`` nem exige espaco novo.
"""

from __future__ import annotations

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
    KnowledgeEmbeddingSpace,
    KnowledgeSource,
)
from agente_ia_edu.providers.adapters.fake import FakeProvider
from agente_ia_edu.providers.errors import ProviderRateLimitError
from agente_ia_edu.providers.models import EmbeddingResult
from agente_ia_edu.services.knowledge_engine.embedding import (
    EmbeddingBackfillService,
    EmbeddingDimensionMismatch,
    embedding_eligible_roles,
)


class _CountingProvider(FakeProvider):
    """``FakeProvider`` que conta chamadas - e assim que a idempotencia se
    verifica sem rede: a segunda execucao tem de custar ZERO chamadas."""

    def __init__(self, dimensions: int = 8):
        self.embedding_dimensions = dimensions
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, request):
        self.calls.append(tuple(request.texts))
        return await super().embed(request)


class _WrongDimensionProvider(FakeProvider):
    def __init__(self, dimensions: int):
        self.embedding_dimensions = dimensions


class _FlakyProvider(FakeProvider):
    """Falha nas ``failures`` primeiras chamadas, depois funciona."""

    def __init__(self, failures: int, dimensions: int = 8):
        self.embedding_dimensions = dimensions
        self._remaining = failures
        self.calls = 0

    async def embed(self, request):
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise ProviderRateLimitError("limite atingido")
        return await super().embed(request)


class _AlwaysFailsOnOneText(FakeProvider):
    """Falha so no lote que contiver um texto especifico."""

    def __init__(self, poison: str, dimensions: int = 8):
        self.embedding_dimensions = dimensions
        self._poison = poison

    async def embed(self, request):
        if any(self._poison in text for text in request.texts):
            raise ProviderRateLimitError("lote envenenado")
        return await super().embed(request)


class _EmbeddingCase(unittest.IsolatedAsyncioTestCase):
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
            space = KnowledgeEmbeddingSpace(
                provider="fake", model="modelo-de-teste",
                dimensions=self.dimensions, distance_metric="cosine",
                status="ACTIVE",
            )
            session.add(space)
            await session.flush()
            self.source_id = source.id
            self.document_id = document.id
            self.space_id = space.id
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _chunk(
        self, raw_text: str, *, role: str = "CONTENT", heading: list[str] | None = None
    ) -> uuid.UUID:
        from agente_ia_edu.knowledge_chunking_policy.v1 import retrieval_text_hash

        self._ordinal += 1
        heading = heading or []
        async with self.factory() as session:
            chunk = KnowledgeChunk(
                source_id=self.source_id, document_id=self.document_id,
                ordinal=self._ordinal, chunk_type="PROSE",
                heading_path=heading, page_start=self._ordinal,
                page_end=self._ordinal, raw_text=raw_text,
                text_hash=retrieval_text_hash(raw_text=raw_text, heading_path=heading),
                char_count=len(raw_text), editorial_role=role,
                editorial_detector_version="v1",
            )
            session.add(chunk)
            await session.flush()
            chunk_id = chunk.id
            await session.commit()
        return chunk_id

    async def _backfill(self, provider=None, **kwargs):
        """A espera entre retentativas e REGISTRADA, nunca dormida: um teste
        que gasta tempo de parede real deixa de ser rodado."""
        provider = provider or _CountingProvider(self.dimensions)
        self.sleeps: list[float] = []

        async def _record(seconds: float) -> None:
            self.sleeps.append(seconds)

        async with self.factory() as session:
            snapshot = await EmbeddingBackfillService(
                session, provider=provider, sleep=_record
            ).backfill(self.space_id, **kwargs)
            await session.commit()
        return snapshot, provider

    async def _count(self) -> int:
        async with self.factory() as session:
            return await session.scalar(
                select(func.count()).select_from(KnowledgeChunkEmbedding)
            )


class ProviderNeutralityTests(_EmbeddingCase):
    async def test_the_model_sent_to_the_provider_comes_from_the_space(self):
        """O servico nao conhece modelo nenhum: ele le a linha do espaco."""
        await self._chunk("A diluicao reduz a concentracao.")
        provider = _CountingProvider(self.dimensions)

        class _Spy(_CountingProvider):
            def __init__(self, dimensions):
                super().__init__(dimensions)
                self.models: list[str | None] = []

            async def embed(self, request):
                self.models.append(request.model)
                return await super().embed(request)

        spy = _Spy(self.dimensions)
        await self._backfill(spy)
        self.assertEqual(spy.models, ["modelo-de-teste"])

    async def test_the_canonical_text_is_the_retrieval_text(self):
        """O que vai para o provider e ``build_retrieval_text`` - o MESMO
        texto que o ``text_hash`` cobre desde a Fase 3."""
        from agente_ia_edu.knowledge_chunking_policy.v1 import build_retrieval_text

        await self._chunk("Corpo do texto.", heading=["Capitulo 1 - Solucoes"])
        _, provider = await self._backfill()
        esperado = build_retrieval_text(
            raw_text="Corpo do texto.", heading_path=["Capitulo 1 - Solucoes"]
        )
        self.assertEqual(provider.calls[0][0], esperado)

    async def test_the_stored_hash_matches_the_chunk_hash(self):
        chunk_id = await self._chunk("A diluicao reduz a concentracao.")
        await self._backfill()
        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, chunk_id)
            row = await session.scalar(
                select(KnowledgeChunkEmbedding).where(
                    KnowledgeChunkEmbedding.chunk_id == chunk_id
                )
            )
        self.assertEqual(row.text_hash, chunk.text_hash)


class DimensionValidationTests(_EmbeddingCase):
    async def test_a_wrong_dimension_fails_before_persisting(self):
        """A coluna ``vector`` nao tem tamanho - o banco aceitaria. A trava e
        do servico, e ela tem de agir ANTES de escrever."""
        await self._chunk("A diluicao reduz a concentracao.")
        with self.assertRaises(EmbeddingDimensionMismatch) as caught:
            await self._backfill(_WrongDimensionProvider(16))
        self.assertIn("16", str(caught.exception))
        self.assertIn("8", str(caught.exception))
        self.assertEqual(await self._count(), 0)

    async def test_nothing_at_all_is_written_when_one_vector_diverges(self):
        for index in range(5):
            await self._chunk(f"Texto numero {index} sobre quimica geral.")
        with self.assertRaises(EmbeddingDimensionMismatch):
            await self._backfill(_WrongDimensionProvider(4))
        self.assertEqual(await self._count(), 0)


class PairingTests(_EmbeddingCase):
    """O par texto/vetor nao e verificado por ordem - e por hash.

    ``text_hash`` do chunk e sha256 de ``build_retrieval_text``; o adapter
    devolve o sha256 do texto que de fato enviou. Tem de ser o MESMO numero.
    Confiar na ordem de chegada gravaria, em silencio, o vetor de um enunciado
    no chunk de outro.
    """

    async def test_a_misaligned_vector_is_caught_before_persisting(self):
        class _Shuffles(FakeProvider):
            embedding_dimensions = 8

            async def embed(self, request):
                result = await super().embed(request)
                return EmbeddingResult(
                    artifacts=tuple(reversed(result.artifacts)),
                    provider=result.provider,
                    model=result.model,
                    dimensions=result.dimensions,
                )

        await self._chunk("Primeiro texto sobre diluicao.")
        await self._chunk("Segundo texto sobre concentracao.")
        from agente_ia_edu.services.knowledge_engine.embedding import (
            EmbeddingContentMismatch,
        )

        with self.assertRaises(EmbeddingContentMismatch):
            await self._backfill(_Shuffles())
        self.assertEqual(await self._count(), 0)

    async def test_a_short_response_is_caught(self):
        class _Drops(FakeProvider):
            embedding_dimensions = 8

            async def embed(self, request):
                result = await super().embed(request)
                return EmbeddingResult(
                    artifacts=result.artifacts[:-1],
                    provider=result.provider,
                    model=result.model,
                    dimensions=result.dimensions,
                )

        await self._chunk("Primeiro texto sobre diluicao.")
        await self._chunk("Segundo texto sobre concentracao.")
        from agente_ia_edu.services.knowledge_engine.embedding import (
            EmbeddingContentMismatch,
        )

        with self.assertRaises(EmbeddingContentMismatch):
            await self._backfill(_Drops())
        self.assertEqual(await self._count(), 0)


class IdempotencyTests(_EmbeddingCase):
    async def test_the_second_run_costs_zero_provider_calls(self):
        """Reembeddar e dinheiro. A segunda execucao nao pode chamar nada."""
        for index in range(3):
            await self._chunk(f"Texto {index} sobre diluicao e concentracao.")
        first, _ = await self._backfill()
        self.assertEqual(first.embedded, 3)

        segunda, provider = await self._backfill()
        self.assertEqual(segunda.embedded, 0)
        self.assertEqual(segunda.already_present, 3)
        self.assertEqual(provider.calls, [])
        self.assertEqual(await self._count(), 3)

    async def test_a_changed_chunk_is_re_embedded_and_the_old_row_survives(self):
        """Reembeddar e INSERT, nunca UPDATE: a linha antiga permanece, com o
        hash do texto que ela de fato representa."""
        from agente_ia_edu.knowledge_chunking_policy.v1 import retrieval_text_hash

        chunk_id = await self._chunk("Texto original sobre diluicao.")
        await self._backfill()
        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, chunk_id)
            chunk.raw_text = "Texto REVISADO sobre diluicao."
            chunk.text_hash = retrieval_text_hash(
                raw_text=chunk.raw_text, heading_path=[]
            )
            await session.commit()

        snapshot, _ = await self._backfill()
        self.assertEqual(snapshot.embedded, 1)
        self.assertEqual(await self._count(), 2)

    async def test_the_same_text_in_two_spaces_is_embedded_twice(self):
        """A identidade inclui o ESPACO: o mesmo texto noutro modelo e outro
        vetor, e os dois coexistem."""
        await self._chunk("Texto sobre diluicao.")
        await self._backfill()
        async with self.factory() as session:
            outro = KnowledgeEmbeddingSpace(
                provider="fake", model="outro-modelo", dimensions=self.dimensions,
                distance_metric="cosine", status="BACKFILLING",
            )
            session.add(outro)
            await session.flush()
            outro_id = outro.id
            await session.commit()
        async with self.factory() as session:
            await EmbeddingBackfillService(
                session, provider=_CountingProvider(self.dimensions)
            ).backfill(outro_id)
            await session.commit()
        self.assertEqual(await self._count(), 2)

    async def test_new_rows_are_inactive_until_someone_activates_them(self):
        """Default ``is_active=False``: um espaco recem-preenchido nao aparece
        na busca sozinho. Falha fechada, nunca meio-ativa."""
        await self._chunk("Texto sobre diluicao.")
        await self._backfill()
        async with self.factory() as session:
            ativos = await session.scalar(
                select(func.count()).select_from(KnowledgeChunkEmbedding)
                .where(KnowledgeChunkEmbedding.is_active.is_(True))
            )
        self.assertEqual(ativos, 0)


class BatchingTests(_EmbeddingCase):
    async def test_chunks_are_sent_in_batches(self):
        for index in range(7):
            await self._chunk(f"Texto numero {index} sobre quimica geral.")
        _, provider = await self._backfill(batch_size=3)
        self.assertEqual([len(call) for call in provider.calls], [3, 3, 1])

    async def test_a_batch_is_also_capped_by_characters(self):
        """Dois limites, o que vier primeiro: o provider tem teto de tokens
        por requisicao, e um lote de poucos textos enormes o estoura."""
        for index in range(4):
            await self._chunk("x" * 900 + f" {index}")
        _, provider = await self._backfill(batch_size=100, max_batch_chars=2000)
        self.assertTrue(all(len(call) <= 2 for call in provider.calls))
        self.assertEqual(sum(len(call) for call in provider.calls), 4)

    async def test_a_single_oversized_text_still_goes_alone(self):
        await self._chunk("y" * 5000)
        _, provider = await self._backfill(max_batch_chars=1000)
        self.assertEqual([len(call) for call in provider.calls], [1])


class PartialFailureTests(_EmbeddingCase):
    async def test_a_transient_failure_is_retried(self):
        await self._chunk("Texto sobre diluicao.")
        provider = _FlakyProvider(failures=2)
        snapshot, _ = await self._backfill(
            provider, max_attempts=3, backoff_seconds=2.0
        )
        self.assertEqual(snapshot.embedded, 1)
        self.assertEqual(provider.calls, 3)

    async def test_the_backoff_grows_between_attempts(self):
        """Limite de taxa nao melhora com insistencia imediata."""
        await self._chunk("Texto sobre diluicao.")
        await self._backfill(
            _FlakyProvider(failures=2), max_attempts=3, backoff_seconds=2.0
        )
        self.assertEqual(self.sleeps, [2.0, 4.0])

    async def test_the_last_attempt_does_not_sleep_for_nothing(self):
        await self._chunk("Texto sobre diluicao.")
        await self._backfill(
            _AlwaysFailsOnOneText("Texto"), max_attempts=2, backoff_seconds=1.0
        )
        self.assertEqual(self.sleeps, [1.0])

    async def test_a_batch_that_keeps_failing_does_not_stop_the_run(self):
        """Falha parcial e registrada; o resto do corpus segue."""
        await self._chunk("Texto bom numero um.")
        await self._chunk("VENENO neste lote.")
        await self._chunk("Texto bom numero dois.")
        provider = _AlwaysFailsOnOneText("VENENO")
        snapshot, _ = await self._backfill(
            provider, batch_size=1, max_attempts=2
        )
        self.assertEqual(snapshot.embedded, 2)
        self.assertEqual(snapshot.failed, 1)
        self.assertEqual(len(snapshot.failures), 1)
        self.assertIn("lote envenenado", snapshot.failures[0]["error"])
        self.assertEqual(await self._count(), 2)

    async def test_the_run_is_resumable_after_a_partial_failure(self):
        await self._chunk("Texto bom numero um.")
        await self._chunk("VENENO neste lote.")
        await self._backfill(_AlwaysFailsOnOneText("VENENO"), batch_size=1,
                             max_attempts=1)
        self.assertEqual(await self._count(), 1)

        # provider saudavel: so o que faltava e processado
        snapshot, provider = await self._backfill(batch_size=1)
        self.assertEqual(snapshot.embedded, 1)
        self.assertEqual(snapshot.already_present, 1)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(await self._count(), 2)

    async def test_a_dimension_mismatch_is_not_retried(self):
        """Erro de configuracao nao melhora com tentativa: retry so para
        falha transitoria."""
        await self._chunk("Texto sobre diluicao.")
        with self.assertRaises(EmbeddingDimensionMismatch):
            await self._backfill(_WrongDimensionProvider(16), max_attempts=5)


class EmbeddingEligibilityTests(_EmbeddingCase):
    """Elegibilidade de EMBEDDING e de RECUPERACAO sao conceitos distintos."""

    async def test_the_pilot_policy_is_explicit_and_observable(self):
        self.assertTrue(embedding_eligible_roles())
        self.assertIn("CONTENT", embedding_eligible_roles())
        self.assertIn("UNKNOWN", embedding_eligible_roles())

    async def test_navigation_apparatus_is_skipped_by_the_pilot_policy(self):
        """Decisao de CUSTO, nao de arquitetura: sumario nunca e recuperavel
        em proposito algum, entao pagar embedding por ele seria desperdicio."""
        await self._chunk("Prosa de verdade sobre diluicao.")
        await self._chunk("Capitulo 5 ....... 120", role="TABLE_OF_CONTENTS")
        snapshot, provider = await self._backfill()
        self.assertEqual(snapshot.embedded, 1)
        self.assertEqual(snapshot.skipped_by_policy, 1)
        self.assertEqual(len(provider.calls[0]), 1)

    async def test_the_policy_can_be_overridden_without_a_new_space(self):
        """``retrieval_eligibility=False`` NAO significa "jamais pode ter
        vetor". Embeddar material ineligivel e uma escolha de quem chama."""
        await self._chunk("Capitulo 5 ....... 120", role="TABLE_OF_CONTENTS")
        snapshot, _ = await self._backfill(include_roles="ALL")
        self.assertEqual(snapshot.embedded, 1)
        self.assertEqual(snapshot.skipped_by_policy, 0)

    async def test_changing_eligibility_changes_neither_hash_nor_space(self):
        """A prova de que as duas politicas sao independentes: um chunk
        reclassificado continua com o mesmo ``text_hash`` e o mesmo vetor,
        no mesmo espaco. Mudar elegibilidade nao e reembeddar."""
        chunk_id = await self._chunk("Prosa sobre diluicao.")
        await self._backfill()
        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, chunk_id)
            hash_antes = chunk.text_hash
            row = await session.scalar(
                select(KnowledgeChunkEmbedding).where(
                    KnowledgeChunkEmbedding.chunk_id == chunk_id
                )
            )
            vetor_antes, space_antes = list(row.embedding), row.space_id
            chunk.editorial_role = "TABLE_OF_CONTENTS"
            await session.commit()

        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, chunk_id)
            row = await session.scalar(
                select(KnowledgeChunkEmbedding).where(
                    KnowledgeChunkEmbedding.chunk_id == chunk_id
                )
            )
            self.assertEqual(chunk.text_hash, hash_antes)
            self.assertEqual(list(row.embedding), vetor_antes)
            self.assertEqual(row.space_id, space_antes)
        self.assertEqual(await self._count(), 1)

    async def test_the_snapshot_reports_the_policy_it_applied(self):
        await self._chunk("Prosa sobre diluicao.")
        snapshot, _ = await self._backfill()
        self.assertEqual(snapshot.policy, "ELIGIBLE_ROLES")
        self.assertEqual(
            tuple(snapshot.eligible_roles), embedding_eligible_roles()
        )


class CoverageTests(_EmbeddingCase):
    async def test_coverage_reports_what_is_missing(self):
        await self._chunk("Texto um sobre diluicao.")
        await self._chunk("Texto dois sobre diluicao.")
        await self._chunk("Sumario ....... 12", role="TABLE_OF_CONTENTS")
        async with self.factory() as session:
            antes = await EmbeddingBackfillService(
                session, provider=_CountingProvider(self.dimensions)
            ).coverage(self.space_id)
        self.assertEqual(antes.eligible_chunks, 2)
        self.assertEqual(antes.embedded, 0)
        self.assertEqual(antes.missing, 2)

        await self._backfill()
        async with self.factory() as session:
            depois = await EmbeddingBackfillService(
                session, provider=_CountingProvider(self.dimensions)
            ).coverage(self.space_id)
        self.assertEqual(depois.embedded, 2)
        self.assertEqual(depois.missing, 0)
        self.assertEqual(depois.stale, 0)

    async def test_coverage_detects_a_stale_embedding(self):
        from agente_ia_edu.knowledge_chunking_policy.v1 import retrieval_text_hash

        chunk_id = await self._chunk("Texto original.")
        await self._backfill()
        async with self.factory() as session:
            chunk = await session.get(KnowledgeChunk, chunk_id)
            chunk.raw_text = "Texto alterado."
            chunk.text_hash = retrieval_text_hash(
                raw_text=chunk.raw_text, heading_path=[]
            )
            await session.commit()
        async with self.factory() as session:
            cobertura = await EmbeddingBackfillService(
                session, provider=_CountingProvider(self.dimensions)
            ).coverage(self.space_id)
        self.assertEqual(cobertura.stale, 1)
        self.assertEqual(cobertura.missing, 1)

    async def test_coverage_reports_the_norm_observed_in_the_last_run(self):
        await self._chunk("Texto sobre diluicao.")
        snapshot, _ = await self._backfill()
        self.assertIsNotNone(snapshot.vector_norm_mean)
        self.assertIsInstance(snapshot.vectors_are_unit_norm, bool)


if __name__ == "__main__":
    unittest.main()
