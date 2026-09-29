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


__all__ = ["PlatformEssayPromptService", "materialization_year"]
