"""Curadoria do vinculo entre no de curriculo e habilidade da BNCC.

POR QUE ISTO E CURADO E NAO INFERIDO. A Fase 0 mediu: "estequiometria",
"reagente limitante" e "diluicao" NAO aparecem uma unica vez no texto da area
de Ciencias da Natureza; "solucoes" aparece em *"propor solucoes"*. As
habilidades da BNCC sao enunciados de COMPETENCIA, nao rotulos de conteudo.
Casar por similaridade textual produziria ruido com aparencia de rigor.

A TRAVA CENTRAL esta no banco, nao num service:

    CHECK (status <> 'VALIDATED' OR validated_by_external_identity IS NOT NULL)
    CHECK (status <> 'VALIDATED' OR rationale IS NOT NULL)

Uma sugestao de IA NAO consegue virar relacao validada, porque validar exige
identidade humana e justificativa registradas. E o mesmo padrao topologico que
impede uma fonte COMMERCIAL_REFERENCE de alcancar o aluno.

IDENTIDADE NORMATIVA COMPOSTA (spec 22.3). O vinculo guarda ``taxonomy_id``
ALEM de ``taxonomy_node_id``: ``EM13CNT301`` isolado nao e identidade eterna,
e uma curadoria feita sob a BNCC de 2018 nao pode parecer valida sob uma BNCC
futura sem que ninguem a tenha revisado.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CurriculumBnccLink(Base):
    """Relacao curada entre um ``CatalogNode`` e uma habilidade da BNCC."""

    __tablename__ = "curriculum_bncc_links"
    __table_args__ = (
        # PREREQUISITE fica FORA de proposito: pre-requisito ja pertence ao
        # grafo curricular (CatalogNodePrerequisite), e um tipo novo de
        # relacao BNCC so entra com necessidade pedagogica concreta.
        CheckConstraint(
            "relation_type IN ('PRIMARY', 'SUPPORTING')",
            name="ck_curriculum_bncc_links_relation_type",
        ),
        CheckConstraint(
            "status IN ('PROPOSED', 'VALIDATED', 'REJECTED', 'SUPERSEDED')",
            name="ck_curriculum_bncc_links_status",
        ),
        CheckConstraint(
            "origin IN ('MANUAL', 'AI_SUGGESTION', 'IMPORT')",
            name="ck_curriculum_bncc_links_origin",
        ),
        # As duas travas que tornam impossivel uma sugestao de IA se
        # autovalidar. Nao sao `if` num service: sao o banco recusando.
        CheckConstraint(
            "status <> 'VALIDATED' OR validated_by_external_identity IS NOT NULL",
            name="ck_curriculum_bncc_links_validated_needs_identity",
        ),
        CheckConstraint(
            "status <> 'VALIDATED' OR rationale IS NOT NULL",
            name="ck_curriculum_bncc_links_validated_needs_rationale",
        ),
        # Confianca so faz sentido para sugestao automatica; exigi-la impede
        # uma sugestao sem medida de confianca passar por curadoria humana.
        CheckConstraint(
            "origin <> 'AI_SUGGESTION' OR confidence IS NOT NULL",
            name="ck_curriculum_bncc_links_suggestion_needs_confidence",
        ),
        # Varias propostas por par; um so vinculo VALIDADO por versao da BNCC.
        Index(
            "uq_curriculum_bncc_links_validated",
            "content_node_id",
            "taxonomy_node_id",
            "taxonomy_id",
            unique=True,
            postgresql_where=text("status = 'VALIDATED'"),
            sqlite_where=text("status = 'VALIDATED'"),
        ),
        Index("ix_curriculum_bncc_links_content_node_id", "content_node_id"),
        Index("ix_curriculum_bncc_links_taxonomy_node_id", "taxonomy_node_id"),
        Index("ix_curriculum_bncc_links_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    content_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("catalog_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    taxonomy_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("taxonomy_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    # Redundante em relacao ao no, e deliberado: a identidade normativa e a
    # tripla, e perguntar "quais vinculos existem para a BNCC EM-2018" nao
    # deve exigir join.
    taxonomy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("taxonomies.id", ondelete="RESTRICT"), nullable=False
    )

    relation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PROPOSED")
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default="MANUAL")
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    #: Justificativa PEDAGOGICA. Obrigatoria para validar - um vinculo sem
    #: razao registrada nao e auditavel depois.
    rationale: Mapped[str | None] = mapped_column(Text)

    validated_by_external_identity: Mapped[str | None] = mapped_column(String(255))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Versionamento do proprio vinculo: revisar uma curadoria nao apaga a
    #: anterior.
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("curriculum_bncc_links.id", ondelete="RESTRICT")
    )

    proposed_by_external_identity: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    reviews: Mapped[list[CurriculumBnccLinkReview]] = relationship(back_populates="link")


class CurriculumBnccLinkReview(Base):
    """Trilha APPEND-ONLY de tudo que aconteceu com um vinculo.

    Espelha ``PedagogicalClassificationReview`` (PHASE 30): nenhuma linha e
    sobrescrita. Responde "quem validou este vinculo, quando, com que
    justificativa" anos depois - inclusive quando a curadoria foi revista.
    """

    __tablename__ = "curriculum_bncc_link_reviews"
    __table_args__ = (
        CheckConstraint(
            "action IN ('PROPOSE', 'VALIDATE', 'REJECT', 'SUPERSEDE')",
            name="ck_curriculum_bncc_link_reviews_action",
        ),
        CheckConstraint(
            "actor_type IN ('AI', 'TEACHER', 'COORDINATOR', 'DIRECTOR', "
            "'PLATFORM_ADMIN', 'SYSTEM')",
            name="ck_curriculum_bncc_link_reviews_actor_type",
        ),
        # Uma acao de IA nunca pode ser VALIDATE. A trilha recusa registrar o
        # que a tabela de vinculos ja recusa fazer - defesa em profundidade.
        CheckConstraint(
            "actor_type <> 'AI' OR action = 'PROPOSE'",
            name="ck_curriculum_bncc_link_reviews_ai_only_proposes",
        ),
        Index("ix_curriculum_bncc_link_reviews_link_id", "link_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    link_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("curriculum_bncc_links.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    previous_value: Mapped[dict[str, Any] | None] = mapped_column(JSONBCompatible)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONBCompatible)
    reason: Mapped[str | None] = mapped_column(Text)
    #: Versao do sugeridor, quando a acao vem de IA - para que uma sugestao
    #: ruim possa ser atribuida a uma versao especifica.
    suggester_version: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    link: Mapped[CurriculumBnccLink] = relationship(back_populates="reviews")
