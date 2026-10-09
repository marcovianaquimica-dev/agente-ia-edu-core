"""Ativacao, rollback e historico de espacos de embedding - Fase 6, passo 3.

ESTE SERVICO NAO SABE GERAR EMBEDDING, E ISSO E ESTRUTURAL
==========================================================

``__init__`` recebe UMA coisa: a sessao. Nao ha parametro de provider,
portanto nao ha caminho de codigo - nem por engano, nem por uma futura
"otimizacao" - em que ativar ou reverter um espaco gaste dinheiro com o
fornecedor. Rollback e reativacao de linhas que ja existem, sempre.

POR QUE ``coverage()`` SOZINHA NAO AUTORIZA ATIVACAO
====================================================

Foi o requisito mais importante deste passo, e a razao e aritmetica:
cobertura e uma RAZAO. Com o corpus vazio, ``missing = 0`` e a cobertura e
trivialmente completa - e um acervo truncado por acidente passaria no portao
exibindo 100%.

Por isso ``readiness()`` exige ``expected_population``: um numero ABSOLUTO,
declarado ANTES de medir, sobre a mesma politica de backfill com que o espaco
foi preenchido. A politica entra junto porque "cobertura 100%" nao significa
nada sem responder "100% de QUAL populacao?" - 100% sob ``ELIGIBLE_ROLES`` e
uma populacao, 100% sob ``ALL_CHUNKS`` e outra bem maior.

Os portoes, cada um com codigo estavel:

    SPACE_NOT_READY           espaco RETIRED nao pode ser ativado direto
    NO_EMBEDDINGS             o espaco nao tem vetor nenhum
    POPULATION_BELOW_EXPECTED a populacao elegivel encolheu - ver acima
    MISSING_EMBEDDINGS        ha chunk elegivel sem vetor
    STALE_EMBEDDINGS          ha vetor de um texto que ja mudou
    DIMENSION_MISMATCH        ha vetor GRAVADO com comprimento errado

O ultimo e deliberadamente redundante com a validacao do backfill. A do
backfill verifica o que o provider devolveu; esta verifica o que esta no
disco - inclusive linha inserida por script, por migracao, ou por uma versao
anterior do servico. O portao de ativacao nao confia na geracao: ele le.

O PROTOCOLO DE TROCA, E POR QUE A ORDEM E ESSA
===============================================

Tudo numa transacao so. O servico NAO comita - quem chama comita, e e isso
que faz da falha no meio um rollback completo, sem nenhuma logica de
compensacao para dar errado.

    1. trava as linhas de espaco envolvidas (FOR UPDATE, em ordem de id)
    2. reavalia o estado DEPOIS da trava
    3. desativa as linhas do espaco anterior
    4. ativa as linhas do espaco novo cujo ``text_hash`` casa com o do chunk
    5. espaco anterior -> RETIRED
    6. espaco novo -> ACTIVE
    7. grava a linha de historico

3 antes de 4, e 5 antes de 6, por causa de duas travas topologicas que nao
sao DEFERRABLE: ``uq_knowledge_chunk_embeddings_one_active_per_chunk`` e
``uq_knowledge_embedding_spaces_single_active``. A ordem inversa violaria o
indice no meio da propria transacao.

O ``text_hash`` no passo 4 NAO e detalhe. Um espaco pode conter varias linhas
do mesmo chunk - o texto mudou, foi reembeddado, e a linha antiga ficou, por
desenho. Ativar "todas as linhas do espaco" ativaria duas para o mesmo chunk
e esbarraria no indice. Ativar so a linha cujo hash e o do chunk de hoje e a
unica leitura correta de "o vetor que representa este chunk".

CONCORRENCIA
============

Duas ativacoes simultaneas sao tratadas em dois niveis:

- ``SELECT ... FOR UPDATE`` nas linhas de espaco, em ordem de id, para que
  duas transacoes nunca travem em ordens opostas. A segunda bloqueia, e ao
  passar reavalia o mundo - e descobre que o espaco ativo mudou.
- as travas topologicas, como ultima linha de defesa, para o caso que o lock
  nao cobre (INSERT direto, outro processo, bug futuro).

No SQLite o ``FOR UPDATE`` e ignorado. E por isso que a prova de atomicidade
e de concorrencia vive em ``*_postgresql.py``, com duas conexoes de verdade.

O QUE NUNCA ACONTECE AQUI
=========================

Nenhum DELETE. Desativar e UPDATE; o vetor antigo permanece com o
``text_hash`` do texto que representa. E isso que torna o rollback uma
reativacao barata, e nao uma nova geracao paga.
"""

from __future__ import annotations


from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID, uuid4

from sqlalchemy import exists, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import (
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeEmbeddingActivation,
    KnowledgeEmbeddingSpace,
)
from .embedding import CoverageSnapshot, EmbeddingSpaceNotFound, coverage

ACTIVATE = "ACTIVATE"
ROLLBACK = "ROLLBACK"

#: Codigos estaveis. Entram no historico e podem ser consumidos por operacao.
SPACE_NOT_READY = "SPACE_NOT_READY"
NO_EMBEDDINGS = "NO_EMBEDDINGS"
POPULATION_BELOW_EXPECTED = "POPULATION_BELOW_EXPECTED"
MISSING_EMBEDDINGS = "MISSING_EMBEDDINGS"
STALE_EMBEDDINGS = "STALE_EMBEDDINGS"
DIMENSION_MISMATCH = "DIMENSION_MISMATCH"

#: Quantas linhas por vez na varredura de dimensao. Mantem a memoria limitada
#: num acervo de milhares de vetores sem precisar de SQL por dialeto - uma
#: implementacao so, identica no SQLite e no PostgreSQL.
_DIMENSION_SCAN_BATCH = 500


def ann_index_name(space_id: UUID) -> str:
    """Nome do indice ANN do espaco - a MESMA convencao da migracao 058.

    O nome ser identico e o que faz a paridade funcionar: um banco montado
    por migracao ja tem o indice com este nome, e ``CREATE INDEX IF NOT
    EXISTS`` o reconhece em vez de criar um segundo.
    """
    return f"ix_kce_hnsw_{space_id.hex}"


def ann_index_ddl(space_id: UUID, dimensions: int) -> str:
    """O DDL do indice ANN parcial do espaco. FONTE UNICA.

    POR QUE ELE NAO PODE SER UM HOOK DE ``create_all``
    ==================================================

    Porque ele depende de DADO, nao de schema: do ``space_id`` e da dimensao
    daquele espaco. Na hora em que as tabelas sao criadas nao existe espaco
    algum, entao nao ha indice a criar. A extensao pgvector e um hook de
    ``before_create`` justamente por ser o contrario - ela nao depende de
    linha nenhuma.

    O indice pertence, portanto, ao CICLO DE VIDA DO ESPACO, e e por isso que
    quem o cria e ``ensure_ann_index``, chamada na ativacao. A migracao 058 o
    cria para o espaco que ela propria semeia, que e o mesmo motivo.

    O cast explicito e o que permite a coluna seguir SEM dimensao: o indice
    sabe o tamanho, a tabela nao. Sem o cast identico ao da consulta, o
    planejador nao usa o indice - e e exatamente isso que o teste de EXPLAIN
    verifica.
    """
    dims = int(dimensions)
    return (
        f"CREATE INDEX IF NOT EXISTS {ann_index_name(space_id)} "
        "ON knowledge_chunk_embeddings "
        f"USING hnsw ((embedding::vector({dims})) vector_cosine_ops) "
        f"WHERE space_id = '{space_id}'"
    )


class EmbeddingActivationError(RuntimeError):
    """Pedido de ativacao que nao pode ser atendido."""


class EmbeddingNotReady(EmbeddingActivationError):
    """Portoes de prontidao violados. ``violations`` carrega os codigos."""

    def __init__(self, violations: Sequence[dict[str, object]]) -> None:
        self.violations = tuple(violations)
        codigos = ", ".join(str(item["code"]) for item in self.violations)
        super().__init__(f"Espaco nao esta pronto para ativacao: {codigos}")


class EmbeddingActivationConflict(EmbeddingActivationError):
    """Outra ativacao mudou o espaco ativo durante esta. Repetir e seguro."""


@dataclass(frozen=True)
class ActivationReadiness:
    space_id: UUID
    policy: str
    expected_population: int
    coverage: CoverageSnapshot
    dimension_violations: int
    violations: tuple[dict[str, object], ...]
    #: Observacao, nao portao: no SQLite nao ha ANN, e num corpus pequeno a
    #: varredura sequencial e correta. Sai na resposta para que "a busca esta
    #: lenta" tenha onde ser respondido.
    ann_index_present: bool = False

    @property
    def is_ready(self) -> bool:
        return not self.violations


@dataclass(frozen=True)
class ActivationSnapshot:
    activation_id: UUID
    space_id: UUID
    previous_space_id: UUID | None
    action: str
    policy: str
    expected_population: int
    eligible_chunks: int
    embedded: int
    missing: int
    stale: int
    dimension_violations: int
    activated_rows: int
    deactivated_rows: int
    degraded: bool
    violations: tuple[dict[str, object], ...]
    actor: str | None
    reason: str | None
    #: O indice ANN do espaco existe depois desta operacao? Falso no SQLite,
    #: que nao tem ANN - e um fato observado, nao uma promessa.
    ann_index_present: bool = False


class EmbeddingActivationService:
    """Troca qual espaco de embedding a busca enxerga.

    Nao gera vetor. Nao apaga vetor. Nao comita.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- prontidao --------------------------------------------------------

    async def readiness(
        self,
        space_id: UUID,
        *,
        expected_population: int,
        include_roles: Sequence[str] | str | None = None,
    ) -> ActivationReadiness:
        """Avalia TODOS os portoes e devolve a lista inteira de violacoes.

        Nao para na primeira: quem opera precisa ver o quadro completo numa
        passada, nao descobrir um problema por execucao.
        """
        space = await self._space(space_id)
        cobertura = await coverage(self._session, space_id, include_roles=include_roles)
        dimensao = await self._dimension_violations(space)

        violations: list[dict[str, object]] = []
        if space.status == "RETIRED":
            violations.append(
                {
                    "code": SPACE_NOT_READY,
                    "detail": "espaco RETIRED; use rollback_to para reativa-lo",
                }
            )
        if cobertura.embedded == 0:
            violations.append({"code": NO_EMBEDDINGS, "detail": "espaco sem vetores"})
        if cobertura.eligible_chunks < expected_population:
            # O portao que ``coverage()`` sozinha nao da: razao completa sobre
            # populacao encolhida continua sendo 100%.
            violations.append(
                {
                    "code": POPULATION_BELOW_EXPECTED,
                    "detail": (
                        f"populacao elegivel {cobertura.eligible_chunks} abaixo da "
                        f"esperada {expected_population} sob a politica "
                        f"{cobertura.policy}"
                    ),
                    "observed": cobertura.eligible_chunks,
                    "expected": expected_population,
                }
            )
        if cobertura.missing:
            violations.append(
                {"code": MISSING_EMBEDDINGS, "detail": f"{cobertura.missing} sem vetor"}
            )
        if cobertura.stale:
            violations.append(
                {"code": STALE_EMBEDDINGS, "detail": f"{cobertura.stale} desatualizados"}
            )
        if dimensao:
            violations.append(
                {
                    "code": DIMENSION_MISMATCH,
                    "detail": (
                        f"{dimensao} vetores gravados com comprimento diferente de "
                        f"{space.dimensions}"
                    ),
                }
            )

        return ActivationReadiness(
            ann_index_present=await self._ann_index_exists(space.id),
            space_id=space.id,
            policy=cobertura.policy,
            expected_population=expected_population,
            coverage=cobertura,
            dimension_violations=dimensao,
            violations=tuple(violations),
        )

    # -- troca ------------------------------------------------------------

    async def activate(
        self,
        space_id: UUID,
        *,
        expected_population: int,
        include_roles: Sequence[str] | str | None = None,
        actor: str | None = None,
        reason: str | None = None,
    ) -> ActivationSnapshot:
        """Promove ``space_id`` a espaco ativo. Transacao unica, sem commit."""
        return await self._swap(
            space_id,
            action=ACTIVATE,
            expected_population=expected_population,
            include_roles=include_roles,
            actor=actor,
            reason=reason,
            allow_degraded=False,
        )

    async def rollback_to(
        self,
        space_id: UUID,
        *,
        expected_population: int,
        include_roles: Sequence[str] | str | None = None,
        actor: str | None = None,
        reason: str | None = None,
        allow_degraded: bool = False,
    ) -> ActivationSnapshot:
        """Volta a um espaco anterior REATIVANDO os vetores que ja existem.

        Mesmo caminho do ``activate``, com duas diferencas que importam:

        - ``SPACE_NOT_READY`` nao se aplica: voltar a um espaco RETIRED e
          exatamente o que rollback significa;
        - ``allow_degraded=True`` permite seguir com portoes violados, porque
          rollback e operacao de emergencia e exigir corpus perfeito poderia
          deixar o sistema preso num espaco quebrado. O preco e obrigatorio:
          ``degraded=True`` e a lista de portoes aceitos ficam gravadas no
          historico. Degradacao silenciosa nao existe aqui.

        Zero chamadas ao fornecedor - este servico nao tem um.
        """
        return await self._swap(
            space_id,
            action=ROLLBACK,
            expected_population=expected_population,
            include_roles=include_roles,
            actor=actor,
            reason=reason,
            allow_degraded=allow_degraded,
        )

    async def ensure_ann_index(self, space_id: UUID) -> bool:
        """Garante o indice ANN do espaco. Idempotente. Devolve se ele existe.

        No SQLite nao existe ANN e a chamada e um no-op honesto: devolve
        ``False`` em vez de fingir que criou algo.

        E chamada automaticamente pela ativacao. Isso fecha a divergencia que
        o passo 5 expos: um banco montado por ``Base.metadata.create_all`` -
        que e como o corpus de avaliacao e TODOS os testes sao montados -
        nao tinha indice ANN nenhum, enquanto um montado por migracao tinha.
        O caminho de producao passava a existir so no banco que ninguem usava.

        O preco, declarado: construir o indice acontece DENTRO da transacao
        de ativacao e estende o seu bloqueio. No piloto, 5.911 vetores, isso
        custa segundos. Num acervo muito maior, a construcao deve sair para
        uma etapa propria antes da ativacao - que e exatamente por que este
        metodo e publico.
        """
        if self._session.get_bind().dialect.name != "postgresql":
            return False
        space = await self._space(space_id)
        await self._session.execute(text(ann_index_ddl(space.id, space.dimensions)))
        return True

    async def _ann_index_exists(self, space_id: UUID) -> bool:
        if self._session.get_bind().dialect.name != "postgresql":
            return False
        return bool(
            await self._session.scalar(
                text(
                    "SELECT 1 FROM pg_indexes WHERE indexname = :nome"
                ).bindparams(nome=ann_index_name(space_id))
            )
        )

    async def history(self, *, limit: int = 50) -> tuple[ActivationSnapshot, ...]:
        rows = await self._session.scalars(
            select(KnowledgeEmbeddingActivation)
            .order_by(KnowledgeEmbeddingActivation.created_at.desc())
            .limit(limit)
        )
        return tuple(_to_snapshot(row) for row in rows.all())

    async def active_space_id(self) -> UUID | None:
        return await self._session.scalar(
            select(KnowledgeEmbeddingSpace.id).where(
                KnowledgeEmbeddingSpace.status == "ACTIVE"
            )
        )

    # -- internos ---------------------------------------------------------

    async def _swap(
        self,
        space_id: UUID,
        *,
        action: str,
        expected_population: int,
        include_roles: Sequence[str] | str | None,
        actor: str | None,
        reason: str | None,
        allow_degraded: bool,
    ) -> ActivationSnapshot:
        before = await self._lock(space_id)

        readiness = await self.readiness(
            space_id,
            expected_population=expected_population,
            include_roles=include_roles,
        )
        violations = [
            item
            for item in readiness.violations
            # Voltar a um espaco RETIRED E o proposito do rollback.
            if not (action == ROLLBACK and item["code"] == SPACE_NOT_READY)
        ]
        degraded = False
        if violations:
            if not (action == ROLLBACK and allow_degraded):
                raise EmbeddingNotReady(violations)
            degraded = True

        # O indice ANN do espaco, ANTES da troca: um espaco so se torna
        # buscavel ao ser ativado, e e nesse instante que ele precisa do
        # indice. Idempotente - no-op se a migracao ja o criou.
        ann_index = await self.ensure_ann_index(space_id)

        now = datetime.now(timezone.utc)

        # 1. Desativa o anterior. ANTES de ativar o novo - o indice parcial
        #    unico por chunk nao e DEFERRABLE.
        #
        # ``before == space_id`` NAO e erro: e a primeira ativacao do piloto.
        # A migracao 058 semeou o espaco inicial como ACTIVE sem vetor algum,
        # entao "ligar as linhas do espaco que ja e o corrente" e justamente
        # a operacao que falta. Nao ha o que desativar nesse caso.
        deactivated = 0
        if before is not None and before != space_id:
            resultado = await self._session.execute(
                update(KnowledgeChunkEmbedding)
                .where(KnowledgeChunkEmbedding.space_id == before)
                .where(KnowledgeChunkEmbedding.is_active.is_(True))
                .values(is_active=False)
            )
            deactivated = resultado.rowcount or 0

        # 2. Ativa SO as linhas cujo hash e o do chunk de hoje. Um espaco pode
        #    guardar varias versoes do mesmo chunk; ativar todas violaria o
        #    indice e, pior, ativaria o vetor de um texto que ja nao existe.
        try:
            resultado = await self._session.execute(
                update(KnowledgeChunkEmbedding)
                .where(KnowledgeChunkEmbedding.space_id == space_id)
                .where(
                    exists().where(
                        KnowledgeChunk.id == KnowledgeChunkEmbedding.chunk_id,
                        KnowledgeChunk.text_hash == KnowledgeChunkEmbedding.text_hash,
                    )
                )
                .values(is_active=True)
            )
            activated = resultado.rowcount or 0
            # Flush explicito depois do UPDATE: queremos que a trava
            # topologica dispare AQUI, com contexto, e nao la na frente no
            # commit do chamador, onde a mensagem nao diria de que operacao
            # veio. O ``try`` cobre o UPDATE e o flush porque o PostgreSQL
            # pode levantar nos DOIS pontos - a instrucao sai com RETURNING e
            # ja checa o indice na hora.
            await self._session.flush()
        except IntegrityError as error:
            raise EmbeddingActivationConflict(
                "A ativacao produziria duas representacoes ativas do mesmo "
                "chunk. Outra troca correu em paralelo; repetir e seguro."
            ) from error

        # 3. Estado dos espacos, tambem na ordem que a trava parcial exige.
        if before is not None and before != space_id:
            await self._session.execute(
                update(KnowledgeEmbeddingSpace)
                .where(KnowledgeEmbeddingSpace.id == before)
                .values(status="RETIRED", retired_at=now)
            )
        await self._session.execute(
            update(KnowledgeEmbeddingSpace)
            .where(KnowledgeEmbeddingSpace.id == space_id)
            .values(status="ACTIVE", activated_at=now, retired_at=None)
        )

        registro = KnowledgeEmbeddingActivation(
            id=uuid4(),
            space_id=space_id,
            previous_space_id=before,
            action=action,
            policy=readiness.coverage.policy,
            expected_population=expected_population,
            eligible_chunks=readiness.coverage.eligible_chunks,
            embedded=readiness.coverage.embedded,
            missing=readiness.coverage.missing,
            stale=readiness.coverage.stale,
            dimension_violations=readiness.dimension_violations,
            activated_rows=activated,
            deactivated_rows=deactivated,
            degraded=degraded,
            violations=[dict(item) for item in violations],
            actor=actor,
            reason=reason,
            created_at=now,
        )
        self._session.add(registro)
        await self._session.flush()

        return ActivationSnapshot(
            activation_id=registro.id,
            space_id=space_id,
            previous_space_id=before,
            action=action,
            policy=readiness.coverage.policy,
            expected_population=expected_population,
            eligible_chunks=readiness.coverage.eligible_chunks,
            embedded=readiness.coverage.embedded,
            missing=readiness.coverage.missing,
            stale=readiness.coverage.stale,
            dimension_violations=readiness.dimension_violations,
            activated_rows=activated,
            deactivated_rows=deactivated,
            degraded=degraded,
            violations=tuple(violations),
            actor=actor,
            reason=reason,
            ann_index_present=ann_index,
        )

    async def _lock(self, space_id: UUID) -> UUID | None:
        """Trava as linhas de espaco envolvidas e devolve o ativo de agora.

        Ordem de id, sempre: duas transacoes travando em ordens opostas e
        deadlock, e deadlock numa troca de espaco e indisponibilidade da
        busca inteira.
        """
        await self._session.execute(
            select(KnowledgeEmbeddingSpace.id)
            .where(
                (KnowledgeEmbeddingSpace.status == "ACTIVE")
                | (KnowledgeEmbeddingSpace.id == space_id)
            )
            .order_by(KnowledgeEmbeddingSpace.id)
            .with_for_update()
        )
        # So DEPOIS da trava a leitura vale alguma coisa.
        return await self.active_space_id()

    async def _space(self, space_id: UUID) -> KnowledgeEmbeddingSpace:
        space = await self._session.get(KnowledgeEmbeddingSpace, space_id)
        if space is None:
            raise EmbeddingSpaceNotFound(f"Espaco de embedding {space_id} nao existe")
        return space

    async def _dimension_violations(self, space: KnowledgeEmbeddingSpace) -> int:
        """Conta vetores GRAVADOS cujo comprimento diverge do espaco.

        Varredura em Python, em lotes, e nao SQL por dialeto. ``vector_dims``
        existe no PostgreSQL e nao no SQLite, e duas implementacoes de um
        portao de seguranca acabariam discordando exatamente no caso raro.
        """
        total = (
            await self._session.scalar(
                select(func.count())
                .select_from(KnowledgeChunkEmbedding)
                .where(KnowledgeChunkEmbedding.space_id == space.id)
            )
        ) or 0
        if not total:
            return 0

        violations = 0
        for offset in range(0, total, _DIMENSION_SCAN_BATCH):
            rows = await self._session.scalars(
                select(KnowledgeChunkEmbedding.embedding)
                .where(KnowledgeChunkEmbedding.space_id == space.id)
                .order_by(KnowledgeChunkEmbedding.id)
                .offset(offset)
                .limit(_DIMENSION_SCAN_BATCH)
            )
            for vector in rows.all():
                if vector is None or len(vector) != space.dimensions:
                    violations += 1
        return violations


def _to_snapshot(row: KnowledgeEmbeddingActivation) -> ActivationSnapshot:
    return ActivationSnapshot(
        activation_id=row.id,
        space_id=row.space_id,
        previous_space_id=row.previous_space_id,
        action=row.action,
        policy=row.policy,
        expected_population=row.expected_population,
        eligible_chunks=row.eligible_chunks,
        embedded=row.embedded,
        missing=row.missing,
        stale=row.stale,
        dimension_violations=row.dimension_violations,
        activated_rows=row.activated_rows,
        deactivated_rows=row.deactivated_rows,
        degraded=row.degraded,
        violations=tuple(row.violations or ()),
        actor=row.actor,
        reason=row.reason,
    )
