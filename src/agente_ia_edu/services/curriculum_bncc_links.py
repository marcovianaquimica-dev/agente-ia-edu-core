"""Curadoria do vinculo entre no de curriculo e habilidade da BNCC.

DOMINIO DE CURRICULO, nao do Knowledge Engine: quem decide que
"Estequiometria" se relaciona a ``EM13CNT101`` e uma decisao pedagogica, nao
uma inferencia de recuperacao.

A Fase 0 mediu por que isto tem de ser curado: "estequiometria", "reagente
limitante" e "diluicao" NAO aparecem uma unica vez no texto da area de
Ciencias da Natureza, e "solucoes" aparece em *"propor solucoes"*. As
habilidades sao enunciados de competencia, nao rotulos de conteudo.

SUGESTAO DE IA NUNCA SE AUTOVALIDA. ``propose`` aceita
``origin='AI_SUGGESTION'`` e forca ``status='PROPOSED'``; ``validate`` exige
identidade humana e justificativa. As duas regras tambem estao no banco como
CheckConstraint - este service e a primeira linha, nao a unica.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..bncc_contract.v1 import RELATION_TYPES
from ..db.models import (
    CatalogNode,
    CurriculumBnccLink,
    CurriculumBnccLinkReview,
    Taxonomy,
    TaxonomyNode,
)

#: Papeis que podem VALIDAR uma relacao. ``AI`` esta fora de proposito.
HUMAN_ACTOR_TYPES: tuple[str, ...] = (
    "TEACHER",
    "COORDINATOR",
    "DIRECTOR",
    "PLATFORM_ADMIN",
)


class CurriculumBnccLinkError(ValueError):
    """A operacao de curadoria nao e permitida. ``code`` e estavel."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CurriculumBnccLinkNotFound(LookupError):
    """Vinculo inexistente."""


@dataclass(frozen=True)
class LinkSnapshot:
    id: UUID
    content_node_id: UUID
    taxonomy_node_id: UUID
    taxonomy_id: UUID
    taxonomy_version: str
    node_code: str
    relation_type: str
    status: str
    origin: str
    confidence: float | None
    rationale: str | None
    validated_by_external_identity: str | None
    validated_at: datetime | None
    supersedes_id: UUID | None
    created_at: datetime


class CurriculumBnccLinkService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def propose(
        self,
        *,
        content_node_id: UUID,
        taxonomy_node_id: UUID,
        relation_type: str,
        origin: str = "MANUAL",
        confidence: float | None = None,
        rationale: str | None = None,
        actor: str,
        actor_type: str,
        suggester_version: str | None = None,
    ) -> LinkSnapshot:
        """Cria uma relacao PROPOSTA. Nunca validada, qualquer que seja a
        origem."""
        if relation_type not in RELATION_TYPES:
            raise CurriculumBnccLinkError(
                "INVALID_RELATION_TYPE",
                f"relation_type invalido: {relation_type!r}; esperado um de "
                f"{list(RELATION_TYPES)}. PREREQUISITE nao existe nesta versao - "
                "pre-requisito pertence ao grafo curricular",
            )
        if origin == "AI_SUGGESTION" and confidence is None:
            raise CurriculumBnccLinkError(
                "SUGGESTION_NEEDS_CONFIDENCE",
                "uma sugestao automatica precisa declarar confidence",
            )
        if actor_type == "AI" and origin != "AI_SUGGESTION":
            raise CurriculumBnccLinkError(
                "AI_ACTOR_MUST_SUGGEST",
                "um ator AI so pode registrar origin='AI_SUGGESTION'",
            )

        node = await self.session.get(CatalogNode, content_node_id)
        if node is None:
            raise CurriculumBnccLinkNotFound(f"CatalogNode {content_node_id}")
        skill = await self.session.get(TaxonomyNode, taxonomy_node_id)
        if skill is None:
            raise CurriculumBnccLinkNotFound(f"TaxonomyNode {taxonomy_node_id}")
        if skill.node_type != "skill":
            raise CurriculumBnccLinkError(
                "TARGET_IS_NOT_A_SKILL",
                f"o alvo do vinculo e um no '{skill.node_type}'; a relacao liga "
                "no de curriculo a HABILIDADE da BNCC",
            )

        link = CurriculumBnccLink(
            content_node_id=content_node_id,
            taxonomy_node_id=taxonomy_node_id,
            taxonomy_id=skill.taxonomy_id,
            relation_type=relation_type,
            # Qualquer origem nasce PROPOSED. A validacao e um passo humano
            # separado, e o banco recusa VALIDATED sem identidade.
            status="PROPOSED",
            origin=origin,
            confidence=confidence,
            rationale=rationale,
            proposed_by_external_identity=actor,
        )
        self.session.add(link)
        await self.session.flush()
        self._record(
            link,
            action="PROPOSE",
            actor=actor,
            actor_type=actor_type,
            previous_value=None,
            new_value={"status": "PROPOSED", "relation_type": relation_type},
            reason=rationale,
            suggester_version=suggester_version,
        )
        snapshot = await self._snapshot(link)
        await self.session.commit()
        return snapshot

    async def validate(
        self,
        link_id: UUID,
        *,
        rationale: str,
        actor: str,
        actor_type: str,
    ) -> LinkSnapshot:
        """Promove uma proposta a relacao VALIDADA.

        Exige identidade humana e justificativa. Um ator ``AI`` e recusado
        aqui, e o banco recusaria de novo se este service fosse contornado.
        """
        link = await self._load(link_id)
        if actor_type not in HUMAN_ACTOR_TYPES:
            raise CurriculumBnccLinkError(
                "VALIDATION_REQUIRES_HUMAN",
                f"actor_type {actor_type!r} nao pode validar; esperado um de "
                f"{list(HUMAN_ACTOR_TYPES)}. Uma sugestao automatica nunca se "
                "autovalida",
            )
        if not rationale or not rationale.strip():
            raise CurriculumBnccLinkError(
                "VALIDATION_REQUIRES_RATIONALE",
                "validar exige justificativa pedagogica registrada: um vinculo "
                "sem razao nao e auditavel depois",
            )
        if link.status == "VALIDATED":
            raise CurriculumBnccLinkError(
                "ALREADY_VALIDATED", f"o vinculo {link_id} ja esta validado"
            )

        previous = link.status
        link.status = "VALIDATED"
        link.rationale = rationale.strip()
        link.validated_by_external_identity = actor
        link.validated_at = datetime.now(timezone.utc)
        await self.session.flush()
        self._record(
            link,
            action="VALIDATE",
            actor=actor,
            actor_type=actor_type,
            previous_value={"status": previous},
            new_value={"status": "VALIDATED"},
            reason=rationale.strip(),
        )
        snapshot = await self._snapshot(link)
        await self.session.commit()
        return snapshot

    async def reject(
        self, link_id: UUID, *, reason: str, actor: str, actor_type: str
    ) -> LinkSnapshot:
        link = await self._load(link_id)
        if actor_type not in HUMAN_ACTOR_TYPES:
            raise CurriculumBnccLinkError(
                "REJECTION_REQUIRES_HUMAN",
                f"actor_type {actor_type!r} nao pode rejeitar",
            )
        previous = link.status
        link.status = "REJECTED"
        await self.session.flush()
        self._record(
            link,
            action="REJECT",
            actor=actor,
            actor_type=actor_type,
            previous_value={"status": previous},
            new_value={"status": "REJECTED"},
            reason=reason,
        )
        snapshot = await self._snapshot(link)
        await self.session.commit()
        return snapshot

    async def list_for_node(
        self, content_node_id: UUID, *, status: str | None = None
    ) -> list[LinkSnapshot]:
        query = select(CurriculumBnccLink).where(
            CurriculumBnccLink.content_node_id == content_node_id
        )
        if status is not None:
            query = query.where(CurriculumBnccLink.status == status)
        links = (await self.session.scalars(query.order_by(CurriculumBnccLink.created_at))).all()
        return [await self._snapshot(link) for link in links]

    async def reviews_for(self, link_id: UUID) -> list[dict]:
        rows = await self.session.scalars(
            select(CurriculumBnccLinkReview)
            .where(CurriculumBnccLinkReview.link_id == link_id)
            .order_by(CurriculumBnccLinkReview.created_at, CurriculumBnccLinkReview.id)
        )
        return [
            {
                "action": review.action,
                "actor": review.actor,
                "actor_type": review.actor_type,
                "previous_value": review.previous_value,
                "new_value": review.new_value,
                "reason": review.reason,
                "suggester_version": review.suggester_version,
            }
            for review in rows.all()
        ]

    # -- internos --------------------------------------------------------

    async def _load(self, link_id: UUID) -> CurriculumBnccLink:
        link = await self.session.get(CurriculumBnccLink, link_id)
        if link is None:
            raise CurriculumBnccLinkNotFound(str(link_id))
        return link

    def _record(
        self,
        link: CurriculumBnccLink,
        *,
        action: str,
        actor: str,
        actor_type: str,
        previous_value: dict | None,
        new_value: dict | None,
        reason: str | None,
        suggester_version: str | None = None,
    ) -> None:
        """Acrescenta uma linha a trilha. NUNCA atualiza uma existente."""
        self.session.add(
            CurriculumBnccLinkReview(
                link_id=link.id,
                action=action,
                actor=actor,
                actor_type=actor_type,
                previous_value=previous_value,
                new_value=new_value,
                reason=reason,
                suggester_version=suggester_version,
            )
        )

    async def _snapshot(self, link: CurriculumBnccLink) -> LinkSnapshot:
        skill = await self.session.get(TaxonomyNode, link.taxonomy_node_id)
        taxonomy = await self.session.get(Taxonomy, link.taxonomy_id)
        return LinkSnapshot(
            id=link.id,
            content_node_id=link.content_node_id,
            taxonomy_node_id=link.taxonomy_node_id,
            taxonomy_id=link.taxonomy_id,
            taxonomy_version=taxonomy.version if taxonomy else "",
            node_code=skill.code if skill else "",
            relation_type=link.relation_type,
            status=link.status,
            origin=link.origin,
            confidence=float(link.confidence) if link.confidence is not None else None,
            rationale=link.rationale,
            validated_by_external_identity=link.validated_by_external_identity,
            validated_at=link.validated_at,
            supersedes_id=link.supersedes_id,
            created_at=link.created_at,
        )
