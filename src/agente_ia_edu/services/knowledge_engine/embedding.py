"""Geracao e persistencia dos vetores do corpus - Fase 6.

ESTE MODULO NAO CONHECE FORNECEDOR ALGUM
========================================

Nenhum nome de fornecedor, nenhum nome de modelo e nenhuma dimensao literal
aparecem aqui - e ha teste que varre este diretorio para garanti-lo, inclusive
em comentario. Provider, modelo e dimensao sao DADO: vem da linha de
``KnowledgeEmbeddingSpace``. O servico recebe um objeto que satisfaz
``EmbeddingProvider`` e lhe entrega ``EmbeddingRequest(model=espaco.model)``.
Trocar de fornecedor e inserir uma linha e passar outro adapter - nenhuma
linha deste arquivo muda. ``tests/test_openai_embedding_adapter.py`` prova o
isolamento por varredura de AST, nao por convencao.

O TEXTO ENVIADO E O MESMO QUE O ``text_hash`` COBRE
===================================================

``build_retrieval_text(raw_text, heading_path)`` - exatamente a funcao de que
``retrieval_text_hash`` deriva desde a Fase 3. Dai sai um invariante forte e
barato: o ``text_hash`` que o adapter calcula sobre o texto que de fato
enviou tem de ser IGUAL ao ``text_hash`` do chunk. Se divergir, o par
texto/vetor esta trocado, e isso aborta antes de qualquer escrita. Nenhuma
heuristica de ordenacao substitui essa verificacao.

Consequencia aceita: os codigos BNCC que ``_context_text`` acrescenta ao
campo de CONTEXTO do indice lexical NAO entram no texto do embedding.
Acrescenta-los mudaria o ``text_hash`` de toda a Fase 4. O codigo
``EM13CNT301`` continua buscavel pela perna lexical, que e onde ele importa -
ninguem procura uma habilidade por similaridade semantica do seu codigo.

A DIMENSAO E VALIDADA AQUI PORQUE O BANCO NAO A VALIDA
======================================================

A coluna ``embedding`` e ``vector`` SEM tamanho, por desenho: e isso que faz
de "trocar para um modelo de outra dimensao" uma linha nova em vez de uma
migracao de tabela. O preco e que o banco aceitaria, calado, um vetor de
comprimento errado - e um corpus com duas geometrias misturadas produz
distancias sem sentido, sem erro algum. A trava e deste servico, e ela age
ANTES de ``session.add``.

Divergencia de dimensao NAO e retentada: e erro de configuracao, e tentar de
novo so queima dinheiro.

IDEMPOTENCIA
============

Identidade ``(chunk_id, space_id, text_hash)``, UNIQUE desde a Fase 1. O
servico consulta ANTES de chamar o provider, porque a chamada e que custa: a
segunda execucao sobre um corpus ja vetorizado faz zero chamadas.

Reembeddar e sempre INSERT, nunca UPDATE. A linha antiga permanece, com o
hash do texto que ela de fato representa - sem isso nao haveria como
responder "este vetor corresponde a qual versao do texto?".

FALHA PARCIAL NAO DERRUBA A EXECUCAO
====================================

5.900 chunks e uma rede no meio. Um lote que esgota as tentativas e
REGISTRADO em ``failures`` e a execucao segue; retomar depois e seguro
justamente por causa do UNIQUE - o que ja entrou e pulado sem custo.

O servico NAO comita. Ele adiciona a sessao e devolve um snapshot congelado
(``MissingGreenlet``: montado antes de qualquer commit do chamador).

ELEGIBILIDADE DE EMBEDDING != ELEGIBILIDADE DE RECUPERACAO
==========================================================

``editorial_role_is_eligible`` responde "este papel pode ser RECUPERADO neste
proposito?". E uma questao de politica pedagogica e de direitos, avaliada na
busca, por proposito.

Quais chunks ganham VETOR e outra pergunta, e e de CUSTO. A politica do
piloto, ``ELIGIBLE_ROLES``, e: paga embedding por papel que seja recuperavel
em ao menos um proposito. Aparato de navegacao - sumario, indice, front e
back matter - nunca e recuperavel em proposito algum, entao vetoriza-lo seria
desperdicio puro.

As duas politicas sao independentes de proposito:

- mudar a elegibilidade de recuperacao de um chunk NAO muda o seu
  ``text_hash`` e NAO exige espaco novo. O vetor ja gravado continua valido -
  ``test_changing_eligibility_changes_neither_hash_nor_space`` fixa isso.
- ``include_roles="ALL"`` embedda tudo. ``retrieval_eligibility=False`` nunca
  significou "este chunk jamais pode ter vetor"; significa "nao entregue este
  chunk nesta busca".

A politica aplicada vai NO SNAPSHOT, com a lista de papeis. Nao e uma escolha
escondida no codigo: e um fato observavel de cada execucao.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import (
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeEmbeddingSpace,
)
from ...knowledge_chunking_policy.v1 import CHARS_PER_TOKEN, build_retrieval_text
from ...knowledge_retrieval_policy.v1 import statistical_corpus_roles
from ...providers.contracts import EmbeddingProvider
from ...providers.errors import ProviderError
from ...providers.models import EmbeddingRequest

#: Nome estavel da politica de backfill do piloto. Vai no snapshot.
ELIGIBLE_ROLES_POLICY = "ELIGIBLE_ROLES"
#: Politica de escape: vetoriza tudo, inclusive aparato de navegacao.
ALL_CHUNKS_POLICY = "ALL_CHUNKS"

#: Teto de textos por requisicao. Nao e limite de fornecedor nenhum em
#: particular - e um lote conservador o bastante para caber em qualquer um, e
#: pequeno o bastante para que uma falha custe pouco ao ser retentada.
DEFAULT_BATCH_SIZE = 64

#: Teto de CARACTERES por requisicao, o segundo limite - o que vier primeiro.
#: Sessenta e quatro chunks grandes estouram o teto de tokens por requisicao
#: de qualquer provider; contar textos sozinho nao protege disso. Em tokens
#: estimados: 100_000, pela mesma razao de 4 caracteres por token da Fase 3.
DEFAULT_MAX_BATCH_CHARS = 100_000 * CHARS_PER_TOKEN

#: Tentativas por lote, incluindo a primeira.
DEFAULT_MAX_ATTEMPTS = 3
#: Espera da primeira retentativa, dobrando a cada falha.
DEFAULT_BACKOFF_SECONDS = 2.0

#: Tolerancia para dizer "norma 1". Observabilidade, nunca criterio de aceite.
_UNIT_NORM_TOLERANCE = 1e-3


def embedding_eligible_roles() -> tuple[str, ...]:
    """Papeis editoriais que o piloto vetoriza.

    DERIVADO de ``statistical_corpus_roles()``, nao uma terceira lista: sao os
    papeis com ao menos um proposito de recuperacao aberto. Tres listas
    paralelas sairiam de sincronia na primeira mudanca de taxonomia; esta nao
    pode, porque nao e uma lista - e uma consulta a tabela de elegibilidade.
    """
    return statistical_corpus_roles()


class EmbeddingError(RuntimeError):
    """Falha de indexacao vetorial que nao e culpa do provider."""


class EmbeddingSpaceNotFound(EmbeddingError):
    pass


class EmbeddingDimensionMismatch(EmbeddingError):
    """Vetor com comprimento diferente do declarado pelo espaco.

    Levantada ANTES de persistir. Nao e retentada: provider que devolve um
    comprimento diferente do que o espaco declara esta mal configurado, e a
    proxima chamada devolveria o mesmo comprimento cobrando de novo.
    """


class EmbeddingContentMismatch(EmbeddingError):
    """O ``text_hash`` devolvido pelo adapter nao e o do chunk.

    Significa que vetor e texto estao pareados errado. Persistir isso seria
    gravar, em silencio, o vetor de um enunciado no chunk de outro.
    """


@dataclass(frozen=True)
class EmbeddingFailure:
    """Um lote que esgotou as tentativas. Congelado e serializavel."""

    chunk_ids: tuple[UUID, ...]
    attempts: int
    error_type: str
    error: str


@dataclass(frozen=True)
class BackfillSnapshot:
    space_id: UUID
    provider: str
    model: str
    dimensions: int
    #: Politica de elegibilidade de EMBEDDING aplicada nesta execucao.
    policy: str
    eligible_roles: tuple[str, ...]
    candidates: int
    #: Descartados pela politica de embedding - nao sao erro.
    skipped_by_policy: int
    #: Ja tinham vetor com este ``text_hash`` neste espaco. Custo zero.
    already_present: int
    embedded: int
    #: Chunks em lotes que esgotaram as tentativas.
    failed: int
    failures: tuple[dict[str, object], ...] = ()
    batches: int = 0
    provider_calls: int = 0
    #: Norma MEDIA dos vetores recebidos. OBSERVABILIDADE: o cosseno e
    #: calculado de verdade e nada aqui pressupoe vetor unitario.
    vector_norm_mean: float = 0.0
    vectors_are_unit_norm: bool = False
    input_tokens: int | None = None


@dataclass(frozen=True)
class CoverageSnapshot:
    space_id: UUID
    policy: str
    eligible_roles: tuple[str, ...]
    #: Chunks que a politica de embedding manda vetorizar.
    eligible_chunks: int
    #: Desses, quantos tem vetor com o ``text_hash`` ATUAL.
    embedded: int
    missing: int
    #: Tem vetor neste espaco, mas de um texto que ja mudou. Conta tambem em
    #: ``missing``: o vetor velho existe, mas nao responde pelo texto de hoje.
    stale: int


@dataclass
class _Accumulator:
    norm_sum: float = 0.0
    norm_count: int = 0
    unit_norm: bool = True
    input_tokens: int | None = None
    failures: list[EmbeddingFailure] = field(default_factory=list)


class EmbeddingBackfillService:
    """Vetoriza o corpus de um espaco de embedding.

    ``provider`` e qualquer coisa que satisfaca ``EmbeddingProvider`` - o
    ``FakeProvider`` nos testes, um ``ProviderRouter`` com fallback em
    producao. O servico nao distingue.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        provider: EmbeddingProvider,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._session = session
        self._provider = provider
        self._sleep = sleep

    async def backfill(
        self,
        space_id: UUID,
        *,
        include_roles: Sequence[str] | str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_batch_chars: int = DEFAULT_MAX_BATCH_CHARS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        limit: int | None = None,
    ) -> BackfillSnapshot:
        """Gera e persiste os vetores que faltam neste espaco.

        NAO comita. Devolve snapshot congelado, montado antes de o chamador
        comitar - a regra de ``MissingGreenlet`` do projeto.
        """
        space = await self._space(space_id)
        policy, roles = _resolve_policy(include_roles)

        candidates = await self._candidates(roles, policy)
        pending, already_present = await self._pending(space_id, candidates)
        if limit is not None:
            pending = pending[:limit]

        accumulator = _Accumulator()
        embedded = 0
        batches = 0
        provider_calls = 0

        for batch in _batched(pending, batch_size, max_batch_chars):
            batches += 1
            artifacts, calls = await self._embed_batch(
                batch,
                space=space,
                max_attempts=max_attempts,
                backoff_seconds=backoff_seconds,
                accumulator=accumulator,
            )
            provider_calls += calls
            if artifacts is None:
                continue
            # Validacao COMPLETA do lote antes de qualquer `add`: um lote so
            # entra inteiro ou nao entra.
            for chunk, artifact in zip(batch, artifacts):
                _validate(chunk=chunk, artifact=artifact, space=space)
            for chunk, artifact in zip(batch, artifacts):
                self._session.add(
                    KnowledgeChunkEmbedding(
                        chunk_id=chunk.id,
                        space_id=space.id,
                        embedding=list(artifact.vector),
                        text_hash=chunk.text_hash,
                        is_active=False,
                    )
                )
                embedded += 1
            await self._session.flush()

        failed = sum(len(failure.chunk_ids) for failure in accumulator.failures)
        return BackfillSnapshot(
            space_id=space.id,
            provider=space.provider,
            model=space.model,
            dimensions=space.dimensions,
            policy=policy,
            eligible_roles=roles,
            candidates=len(candidates),
            skipped_by_policy=await self._skipped(roles, policy),
            already_present=already_present,
            embedded=embedded,
            failed=failed,
            failures=tuple(
                {
                    "chunk_ids": [str(chunk_id) for chunk_id in failure.chunk_ids],
                    "attempts": failure.attempts,
                    "error_type": failure.error_type,
                    "error": failure.error,
                }
                for failure in accumulator.failures
            ),
            batches=batches,
            provider_calls=provider_calls,
            vector_norm_mean=(
                accumulator.norm_sum / accumulator.norm_count
                if accumulator.norm_count
                else 0.0
            ),
            vectors_are_unit_norm=bool(accumulator.norm_count) and accumulator.unit_norm,
            input_tokens=accumulator.input_tokens,
        )

    async def coverage(
        self,
        space_id: UUID,
        *,
        include_roles: Sequence[str] | str | None = None,
    ) -> CoverageSnapshot:
        """Atalho para :func:`coverage`, a funcao de modulo.

        A contagem vive fora da classe porque o servico de ATIVACAO precisa
        exatamente dela e nao pode depender de um provider para obte-la. Duas
        implementacoes de "o que falta" divergiriam, e divergiriam justamente
        no momento em que a resposta decide se um espaco pode ser ativado.
        """
        return await coverage(self._session, space_id, include_roles=include_roles)

    # -- internos ---------------------------------------------------------

    async def _space(self, space_id: UUID) -> KnowledgeEmbeddingSpace:
        return await _load_space(self._session, space_id)

    def _candidate_query(self, roles: tuple[str, ...], policy: str):
        return _candidate_query(roles, policy)

    async def _candidates(
        self, roles: tuple[str, ...], policy: str
    ) -> list[KnowledgeChunk]:
        return await _candidates(self._session, roles, policy)

    async def _skipped(self, roles: tuple[str, ...], policy: str) -> int:
        if policy != ELIGIBLE_ROLES_POLICY:
            return 0
        total = (
            await self._session.scalar(select(func.count()).select_from(KnowledgeChunk))
        ) or 0
        eligible = (
            await self._session.scalar(
                select(func.count()).select_from(
                    _candidate_query(roles, policy).subquery()
                )
            )
        ) or 0
        return total - eligible

    async def _pending(
        self, space_id: UUID, candidates: Sequence[KnowledgeChunk]
    ) -> tuple[list[KnowledgeChunk], int]:
        return await _pending(self._session, space_id, candidates)

    async def _embed_batch(
        self,
        batch: Sequence[KnowledgeChunk],
        *,
        space: KnowledgeEmbeddingSpace,
        max_attempts: int,
        backoff_seconds: float,
        accumulator: _Accumulator,
    ):
        texts = tuple(_canonical_text(chunk) for chunk in batch)
        request = EmbeddingRequest(texts=texts, model=space.model)

        calls = 0
        last: ProviderError | None = None
        for attempt in range(1, max(1, max_attempts) + 1):
            calls += 1
            try:
                result = await self._provider.embed(request)
            except ProviderError as error:
                last = error
                if attempt < max_attempts:
                    # Exponencial. Limite de taxa nao melhora com insistencia
                    # imediata - melhora com espera que cresce.
                    await self._sleep(backoff_seconds * (2 ** (attempt - 1)))
                continue

            if len(result.artifacts) != len(batch):
                raise EmbeddingContentMismatch(
                    f"Provider devolveu {len(result.artifacts)} vetores para "
                    f"{len(batch)} textos"
                )
            _observe(result, batch_size=len(batch), accumulator=accumulator)
            return result.artifacts, calls

        accumulator.failures.append(
            EmbeddingFailure(
                chunk_ids=tuple(chunk.id for chunk in batch),
                attempts=calls,
                error_type=type(last).__name__ if last else "ProviderError",
                error=str(last) if last else "falha desconhecida",
            )
        )
        return None, calls


async def _load_space(
    session: AsyncSession, space_id: UUID
) -> KnowledgeEmbeddingSpace:
    space = await session.get(KnowledgeEmbeddingSpace, space_id)
    if space is None:
        raise EmbeddingSpaceNotFound(f"Espaco de embedding {space_id} nao existe")
    return space


def _candidate_query(roles: tuple[str, ...], policy: str):
    query = select(KnowledgeChunk)
    if policy == ELIGIBLE_ROLES_POLICY:
        query = query.where(
            func.coalesce(KnowledgeChunk.editorial_role, "UNKNOWN").in_(roles)
        )
    return query


async def _candidates(
    session: AsyncSession, roles: tuple[str, ...], policy: str
) -> list[KnowledgeChunk]:
    rows = await session.scalars(
        _candidate_query(roles, policy).order_by(
            KnowledgeChunk.document_id, KnowledgeChunk.ordinal
        )
    )
    return list(rows.all())


async def _pending(
    session: AsyncSession, space_id: UUID, candidates: Sequence[KnowledgeChunk]
) -> tuple[list[KnowledgeChunk], int]:
    """Separa o que falta do que ja existe - ANTES de chamar o provider,
    porque e a chamada que custa."""
    if not candidates:
        return [], 0
    rows = await session.execute(
        select(
            KnowledgeChunkEmbedding.chunk_id, KnowledgeChunkEmbedding.text_hash
        ).where(KnowledgeChunkEmbedding.space_id == space_id)
    )
    existing = {(chunk_id, text_hash) for chunk_id, text_hash in rows.all()}
    pending = [
        chunk for chunk in candidates if (chunk.id, chunk.text_hash) not in existing
    ]
    return pending, len(candidates) - len(pending)


async def coverage(
    session: AsyncSession,
    space_id: UUID,
    *,
    include_roles: Sequence[str] | str | None = None,
) -> CoverageSnapshot:
    """Quanto do corpus elegivel ja tem vetor NESTE espaco, com o texto de hoje.

    FUNCAO DE MODULO, e nao metodo, de proposito: quem decide ativar um
    espaco precisa desta contagem e NAO pode precisar de um provider para
    obte-la. Ver ``embedding_activation.py``.

    Cuidado, e o passo 3 depende disto: esta resposta e uma RAZAO. Com o
    corpus vazio, ``missing`` e zero e a cobertura e trivialmente completa.
    Por isso ``coverage()`` sozinha nao autoriza ativacao - ver
    ``ActivationReadiness``.

    AGREGACAO PURA, DESDE O PASSO 4.1
    =================================

    Quatro numeros saem de UMA consulta, e nenhuma linha e materializada. A
    versao anterior carregava os chunks elegiveis como entidades ORM
    COMPLETAS - ``raw_text`` inclusive - so para contar quantos eram: 230 ms
    por busca no corpus de 5.911, porque o portao de cobertura roda a cada
    consulta. Era o mesmo erro que a Fase 5 ja tinha cometido e corrigido na
    busca lexical, reaparecendo aqui.

    Ler ``raw_text`` para contar cobertura e pior que lento: carrega literal
    de obra comercial para a memoria do processo sem necessidade alguma.
    ``tests/test_knowledge_coverage_sql.py`` captura o SQL emitido e prova
    que isso nao acontece.
    """
    space = await _load_space(session, space_id)
    policy, roles = _resolve_policy(include_roles)

    elegivel = _candidate_query(roles, policy).with_only_columns(
        KnowledgeChunk.id, KnowledgeChunk.text_hash
    ).subquery()

    # "Tem vetor COM O TEXTO DE HOJE" e "tem ALGUM vetor" sao duas perguntas,
    # e a diferenca entre elas e exatamente o obsoleto. Os dois EXISTS abaixo
    # as separam sem carregar linha alguma.
    atual = (
        select(1)
        .select_from(KnowledgeChunkEmbedding)
        .where(KnowledgeChunkEmbedding.space_id == space.id)
        .where(KnowledgeChunkEmbedding.chunk_id == elegivel.c.id)
        .where(KnowledgeChunkEmbedding.text_hash == elegivel.c.text_hash)
        .exists()
    )
    qualquer = (
        select(1)
        .select_from(KnowledgeChunkEmbedding)
        .where(KnowledgeChunkEmbedding.space_id == space.id)
        .where(KnowledgeChunkEmbedding.chunk_id == elegivel.c.id)
        .exists()
    )

    linha = (
        await session.execute(
            select(
                func.count().label("elegiveis"),
                func.count().filter(atual).label("com_vetor"),
                func.count().filter(~atual & qualquer).label("obsoletos"),
            ).select_from(elegivel)
        )
    ).one()

    return CoverageSnapshot(
        space_id=space.id,
        policy=policy,
        eligible_roles=roles,
        eligible_chunks=linha.elegiveis or 0,
        embedded=linha.com_vetor or 0,
        missing=(linha.elegiveis or 0) - (linha.com_vetor or 0),
        stale=linha.obsoletos or 0,
    )


def _canonical_text(chunk: KnowledgeChunk) -> str:
    """O texto EXATO que o ``text_hash`` do chunk cobre. Verbatim."""
    return build_retrieval_text(
        raw_text=chunk.raw_text, heading_path=list(chunk.heading_path or ())
    )


def _validate(*, chunk: KnowledgeChunk, artifact, space: KnowledgeEmbeddingSpace) -> None:
    if len(artifact.vector) != space.dimensions:
        raise EmbeddingDimensionMismatch(
            f"Espaco {space.id} declara {space.dimensions} dimensoes; provider "
            f"devolveu vetor de {len(artifact.vector)} para o chunk {chunk.id}"
        )
    if artifact.text_hash != chunk.text_hash:
        raise EmbeddingContentMismatch(
            f"Vetor pareado com texto errado: chunk {chunk.id} tem hash "
            f"{chunk.text_hash[:12]}..., adapter devolveu "
            f"{artifact.text_hash[:12]}..."
        )


def _observe(result, *, batch_size: int, accumulator: _Accumulator) -> None:
    """Mede a norma dos vetores recebidos.

    O adapter ja reporta a sua (``EmbeddingResult.vector_norm_mean``), mas
    medir aqui tambem faz o sinal existir para QUALQUER provider, inclusive um
    que nao reporte nada - um 0,0 vindo de um campo nao preenchido seria
    indistinguivel de um vetor realmente nulo.
    """
    for artifact in result.artifacts:
        norm = math.sqrt(sum(value * value for value in artifact.vector))
        accumulator.norm_sum += norm
        accumulator.norm_count += 1
        if abs(norm - 1.0) > _UNIT_NORM_TOLERANCE:
            accumulator.unit_norm = False
    if result.input_tokens is not None:
        accumulator.input_tokens = (accumulator.input_tokens or 0) + result.input_tokens


def _resolve_policy(
    include_roles: Sequence[str] | str | None,
) -> tuple[str, tuple[str, ...]]:
    if include_roles is None:
        return ELIGIBLE_ROLES_POLICY, embedding_eligible_roles()
    if isinstance(include_roles, str):
        if include_roles.upper() == "ALL":
            return ALL_CHUNKS_POLICY, embedding_eligible_roles()
        return ELIGIBLE_ROLES_POLICY, (include_roles,)
    return ELIGIBLE_ROLES_POLICY, tuple(include_roles)


def _batched(
    chunks: Sequence[KnowledgeChunk], batch_size: int, max_batch_chars: int
):
    """Dois limites, o que vier primeiro.

    Um texto sozinho maior que ``max_batch_chars`` vai sozinho em vez de ser
    descartado ou truncado: truncar seria perder conteudo em silencio, e o
    teto de chunk da Fase 3 ja garante que ele cabe numa requisicao.
    """
    batch: list[KnowledgeChunk] = []
    chars = 0
    for chunk in chunks:
        size = len(chunk.raw_text or "")
        if batch and (len(batch) >= batch_size or chars + size > max_batch_chars):
            yield batch
            batch, chars = [], 0
        batch.append(chunk)
        chars += size
    if batch:
        yield batch
