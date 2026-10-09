"""Pratica guiada - a INTERACAO assistida, que nao e evidencia de dominio.

Esta tabela guarda o que aconteceu enquanto o Assessor ajudava: quantas
tentativas, quantos niveis de ajuda, o maior nivel alcancado, e se o aluno
resolveu antes de qualquer ajuda.

NUNCA E LIDA PELO MAPA DE DOMINIO
==================================
Exatamente como `material_progress` (PHASE 25) nao e lido por ele. E
deliberado, e e a garantia estrutural de que:

    conseguir com ajuda  !=  dominar sozinho

O motor de dominio conta toda resposta respondida em `answered`/`correct`,
sem ponderar ajuda. Se a pratica guiada passasse por ali, acertar depois de
quatro dicas entraria como acerto igual a acertar sozinho - e "conseguiu com
ajuda" viraria "domina" sem ninguem ter decidido isso.

A comprovacao continua exigindo uma pratica AUTONOMA, pelo caminho de
sempre (`activity_attempts` / `activity_answers` / `ActivityResult`).

UMA LINHA POR (ALUNO, ITEM)
============================
Idempotente: voltar reabre a mesma linha. Uma segunda linha zeraria os
contadores pedagogicos, e o aluno que usou quatro dicas apareceria como
quem usou zero.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class GuidedPracticeItem(Base):
    __tablename__ = "guided_practice_items"
    __table_args__ = (
        UniqueConstraint("student_external_id", "item_key",
                         name="uq_guided_practice_student_item"),
        CheckConstraint("attempts >= 0", name="ck_guided_practice_attempts"),
        CheckConstraint("hints_used >= 0", name="ck_guided_practice_hints"),
        CheckConstraint("max_hint_level >= 0", name="ck_guided_practice_max_hint"),
        # Resolver sem ajuda e incompativel com ter usado ajuda.
        CheckConstraint("NOT solved_unaided OR hints_used = 0",
                        name="ck_guided_practice_unaided_has_no_hints"),
        CheckConstraint("help_requests >= 0",
                        name="ck_guided_practice_help_requests"),
        # ... nem com ter PEDIDO ajuda. Dizer "nao sei" e acertar depois nao
        # e resolver sozinho, e a trava fica no banco porque o servico
        # sozinho nao a garantiria contra o proximo caminho de escrita.
        CheckConstraint("NOT solved_unaided OR help_requests = 0",
                        name="ck_guided_practice_unaided_has_no_help"),
        Index("ix_guided_practice_student_content",
              "student_external_id", "content_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Sem ForeignKey: registro historico. Apagar um item do conteudo nao pode
    # apagar nem travar o fato de que o aluno praticou.
    item_key: Mapped[str] = mapped_column(String(100), nullable=False)
    content_code: Mapped[str] = mapped_column(String(100), nullable=False)
    # NULL = o diagnostico nao distinguiu a micro-habilidade. Inventar uma
    # seria pior que admitir que nao se sabe.
    skill: Mapped[str | None] = mapped_column(String(100))

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                          server_default="0")
    hints_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            server_default="0")
    max_hint_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                                server_default="0")
    # A unica coluna que distingue "conseguiu" de "conseguiu sozinho".
    solved_unaided: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"))
    completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"))

    # O QUE O ALUNO ESCREVEU, nas palavras dele.
    #
    # O unico fato da conversa que nenhuma funcao pura reconstroi: ele nao e
    # consequencia de nada, e a entrada. A resposta normalizada, a observacao
    # pedagogica e a hipotese saem DAQUI a cada leitura - guardar qualquer
    # uma delas seria uma segunda fonte de verdade para algo recalculavel.
    #
    # NULL quando nao houve fala lida: ambiguidade e ausencia nao viram
    # resposta, e inventar uma seria pior que admitir que nao se leu.
    response_text: Mapped[str | None] = mapped_column(String(400))

    # QUANTAS VEZES ELE PEDIU AJUDA nesta etapa - "nao sei", "me ajuda".
    #
    # Nao e tentativa: dizer "nao sei" nao e errar. Mas tambem nao e nada,
    # e some-lo em `hints_used` faria "pediu ajuda" e "chegou ao nivel 1 da
    # dica" ficarem indistinguiveis.
    help_requests: Mapped[int] = mapped_column(Integer, nullable=False,
                                               default=0, server_default="0")

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc))


__all__ = ["GuidedPracticeItem"]
