"""Propostas de redacao da plataforma (spec 2026-09-29).

Dois lados na mesma classe, porque operam sobre o mesmo par de tabelas:
 - o lado do PLATFORM_ADMIN (criar / listar com contagem de uso / arquivar);
 - o lado da materializacao por escola, que a rota de atribuicao do professor
   usa (Task 5).

Autorizacao (papel, escopo de escola) vive na camada de rota, como em todo o
resto do sistema - este servico so trata as regras de entidade.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import EssayPrompt, PlatformEssayPrompt


def materialization_year() -> int:
    """O ano que uma copia materializada recebe em EssayPrompt.year (NOT NULL).

    platform_essay_prompts nao tem ano proprio (spec s3) - a proposta da
    plataforma e atemporal, quem a datou foi a escola que a adotou. O ano UTC
    corrente e o mesmo default que o formulario "Nova proposta" do professor
    usa. A listagem do professor chama este mesmo helper, para que o ano que
    ele ve na lista seja exatamente o ano que a copia vai receber.
    """
    return datetime.now(timezone.utc).year


class PlatformEssayPromptService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- lado do PLATFORM_ADMIN -------------------------------------------

    async def create_prompt(
        self,
        *,
        title: str,
        statement: str,
        created_by_external_identity: str,
    ) -> PlatformEssayPrompt:
        prompt = PlatformEssayPrompt(
            id=uuid.uuid4(),
            title=title,
            statement=statement,
            status="ACTIVE",
            created_by_external_identity=created_by_external_identity,
        )
        self.session.add(prompt)
        await self.session.flush()
        return prompt

    async def list_with_materialization_counts(
        self,
    ) -> list[tuple[PlatformEssayPrompt, int]]:
        """Todas as propostas (ACTIVE e ARCHIVED), mais recentes primeiro, com
        quantas escolas ja materializaram cada uma - a metrica de uso simples
        que a tela do admin mostra (spec s2)."""
        counts = (
            select(
                EssayPrompt.materialized_from_platform_prompt_id.label("origin_id"),
                func.count().label("school_count"),
            )
            .where(EssayPrompt.materialized_from_platform_prompt_id.is_not(None))
            .group_by(EssayPrompt.materialized_from_platform_prompt_id)
            .subquery()
        )
        result = await self.session.execute(
            select(PlatformEssayPrompt, func.coalesce(counts.c.school_count, 0))
            .outerjoin(counts, counts.c.origin_id == PlatformEssayPrompt.id)
            .order_by(PlatformEssayPrompt.created_at.desc())
        )
        return [(row[0], int(row[1])) for row in result.all()]

    async def archive_prompt(
        self, *, platform_essay_prompt_id: uuid.UUID
    ) -> PlatformEssayPrompt:
        """ARCHIVED so tira a proposta da lista de disponiveis para escolas que
        ainda NAO a materializaram. Nenhuma copia ja materializada e tocada -
        elas continuam existindo e atribuiveis normalmente (spec s2)."""
        prompt = await self.session.get(PlatformEssayPrompt, platform_essay_prompt_id)
        if prompt is None:
            raise ValueError(f"PlatformEssayPrompt not found: {platform_essay_prompt_id}")
        prompt.status = "ARCHIVED"
        await self.session.flush()
        return prompt

    # ---- lado da materializacao por escola --------------------------------

    async def get_platform_prompt(
        self, platform_essay_prompt_id: uuid.UUID
    ) -> PlatformEssayPrompt | None:
        """Devolve None quando o id NAO e de uma proposta da plataforma - e
        assim que a rota de atribuicao decide se precisa materializar ou se
        esta lidando com um EssayPrompt normal, ja que o frontend manda o
        mesmo campo nos dois casos (spec s2)."""
        return await self.session.get(PlatformEssayPrompt, platform_essay_prompt_id)

    async def find_materialized(
        self, *, platform_essay_prompt_id: uuid.UUID, school_id: uuid.UUID
    ) -> EssayPrompt | None:
        """A copia desta escola para esta origem, se ja existir.

        Devolve tambem uma copia que o professor mandou para a Lixeira
        (deleted_at preenchido) - a constraint unica nao distingue, entao
        reaproveitar a linha existente e a unica saida correta; quem barra o
        uso de uma proposta na lixeira e _prompt_for_own_school_or_403 na
        camada de rota, com a mensagem certa ("esta proposta esta na
        lixeira"), e o caminho do professor e restaurar.
        """
        result = await self.session.execute(
            select(EssayPrompt).where(
                EssayPrompt.school_id == school_id,
                EssayPrompt.materialized_from_platform_prompt_id == platform_essay_prompt_id,
            )
        )
        return result.scalars().first()

    async def materialize_for_school(
        self,
        *,
        platform_essay_prompt_id: uuid.UUID,
        school_id: uuid.UUID,
        created_by_external_identity: str,
    ) -> EssayPrompt:
        """Buscar-ou-criar idempotente da copia desta escola (spec s4).

        Uma proposta da plataforma ARQUIVADA ainda materializa: arquivar so
        tira a proposta da lista de escolas que ainda nao a adotaram, e um
        professor que ja tinha a lista aberta quando o admin arquivou nao
        deve receber um erro no meio da atribuicao.

        O IntegrityError capturado e a corrida de duas requisicoes
        simultaneas da mesma escola: a constraint unica rejeita a segunda
        insercao e nos relemos a linha que a primeira criou, em vez de
        estourar. E seguro fazer rollback aqui porque a materializacao
        acontece ANTES de qualquer PromptAssignment ser criada na requisicao.
        """
        existing = await self.find_materialized(
            platform_essay_prompt_id=platform_essay_prompt_id, school_id=school_id
        )
        if existing is not None:
            return existing

        origin = await self.session.get(PlatformEssayPrompt, platform_essay_prompt_id)
        if origin is None:
            raise ValueError(f"PlatformEssayPrompt not found: {platform_essay_prompt_id}")

        copy = EssayPrompt(
            id=uuid.uuid4(),
            school_id=school_id,
            title=origin.title,
            statement=origin.statement,
            year=materialization_year(),
            status="ACTIVE",
            is_free_theme=False,
            created_by_external_identity=created_by_external_identity,
            materialized_from_platform_prompt_id=origin.id,
        )
        self.session.add(copy)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.find_materialized(
                platform_essay_prompt_id=platform_essay_prompt_id, school_id=school_id
            )
            if existing is None:
                raise
            return existing
        return copy

    async def list_available_for_school(
        self, *, school_id: uuid.UUID
    ) -> list[PlatformEssayPrompt]:
        """As propostas ACTIVE da plataforma que esta escola ainda NAO
        materializou - exatamente o que entra na lista do professor junto das
        propostas proprias da escola (spec s2).

        Uma origem cuja copia esta na Lixeira desta escola continua fora
        desta lista: a copia existe, a constraint unica impediria uma
        segunda, e o caminho certo e restaurar a copia pela Lixeira.
        """
        materialized = select(EssayPrompt.materialized_from_platform_prompt_id).where(
            EssayPrompt.school_id == school_id,
            EssayPrompt.materialized_from_platform_prompt_id.is_not(None),
        )
        result = await self.session.execute(
            select(PlatformEssayPrompt)
            .where(
                PlatformEssayPrompt.status == "ACTIVE",
                PlatformEssayPrompt.id.not_in(materialized),
            )
            .order_by(PlatformEssayPrompt.created_at.desc())
        )
        return list(result.scalars().all())


__all__ = ["PlatformEssayPromptService", "materialization_year"]
