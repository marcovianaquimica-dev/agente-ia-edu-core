"""Proposta de redacao "da plataforma": tema+enunciado cadastrado pelo
PLATFORM_ADMIN e visivel a professores de QUALQUER escola.

Uma tabela deliberadamente global - nao tem school_id, nao tem
UniqueConstraint("school_id","id") e NUNCA e referenciada por
PromptAssignment/EssaySubmission/EssayBatchUpload nem por qualquer outra
tabela escopada a uma escola. O unico ponteiro para ela vem de
essay_prompts.materialized_from_platform_prompt_id, e so como proveniencia:
quando um professor atribui uma proposta dessas a uma turma, o backend cria
uma copia real e escopada a escola dele em essay_prompts, e dali pra frente
tudo segue exatamente como uma proposta normal (spec 2026-09-29 s4).

Imutavel depois de criada, mesma filosofia de EssayPrompt (ver o docstring
dela em essay_proposal.py): o admin arquiva e cadastra outra, nunca edita.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PlatformEssayPrompt(Base):
    __tablename__ = "platform_essay_prompts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')", name="ck_platform_essay_prompts_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    # ARCHIVED so impede que escolas NOVAS passem a ver a proposta na lista
    # de disponiveis - nenhuma copia ja materializada e afetada.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_by_external_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


__all__ = ["PlatformEssayPrompt"]
