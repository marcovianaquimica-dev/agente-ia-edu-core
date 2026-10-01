"""Rotas de CURADORIA do vinculo curriculo <-> BNCC (Fase 4).

Router SEPARADO do Knowledge Engine de proposito: quem decide que
"Estequiometria" se relaciona a ``EM13CNT101`` esta fazendo uma escolha
PEDAGOGICA, nao uma operacao de recuperacao. Juntar as duas superficies
convidaria o engine a criar vinculo por conta propria, que e exatamente o que
a Fase 0 mostrou nao funcionar.

A validacao exige identidade humana e justificativa - aqui, no service, e no
banco. Uma sugestao de IA nao consegue se autovalidar por nenhum dos tres
caminhos.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ...identity import ExternalIdentityContext
from ...services.curriculum_bncc_links import (
    CurriculumBnccLinkError,
    CurriculumBnccLinkNotFound,
    CurriculumBnccLinkService,
)
from ..dependencies import get_session_factory
from ..schemas.knowledge_engine import (
    BnccLinkDecisionRequest,
    BnccLinkProposeRequest,
    BnccLinkResponse,
)
from .admin import require_platform_admin

curriculum_bncc_router = APIRouter(
    prefix="/api/v1/catalog",
    tags=["curriculum-bncc"],
)


def _response(snapshot) -> BnccLinkResponse:
    return BnccLinkResponse(
        id=snapshot.id,
        content_node_id=snapshot.content_node_id,
        node_code=snapshot.node_code,
        taxonomy_version=snapshot.taxonomy_version,
        relation_type=snapshot.relation_type,
        status=snapshot.status,
        origin=snapshot.origin,
        confidence=snapshot.confidence,
        rationale=snapshot.rationale,
        validated_by_external_identity=snapshot.validated_by_external_identity,
        validated_at=snapshot.validated_at,
    )


@curriculum_bncc_router.post(
    "/nodes/{content_node_id}/bncc-links",
    response_model=BnccLinkResponse,
    status_code=201,
)
async def propose_link(
    content_node_id: UUID,
    payload: BnccLinkProposeRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> BnccLinkResponse:
    """Propoe um vinculo. SEMPRE nasce PROPOSED, qualquer que seja a origem."""
    async with session_factory() as session:
        service = CurriculumBnccLinkService(session)
        try:
            snapshot = await service.propose(
                content_node_id=content_node_id,
                taxonomy_node_id=payload.taxonomy_node_id,
                relation_type=payload.relation_type,
                origin=payload.origin,
                confidence=payload.confidence,
                rationale=payload.rationale,
                actor=identity.external_user_id,
                actor_type=payload.actor_type,
                suggester_version=payload.suggester_version,
            )
        except CurriculumBnccLinkNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CurriculumBnccLinkError as exc:
            raise HTTPException(status_code=422, detail=f"{exc.code}: {exc}") from exc
    return _response(snapshot)


@curriculum_bncc_router.post(
    "/bncc-links/{link_id}/validate", response_model=BnccLinkResponse
)
async def validate_link(
    link_id: UUID,
    payload: BnccLinkDecisionRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> BnccLinkResponse:
    """Valida um vinculo. Exige justificativa, e registra QUEM validou."""
    async with session_factory() as session:
        service = CurriculumBnccLinkService(session)
        try:
            snapshot = await service.validate(
                link_id,
                rationale=payload.rationale,
                actor=identity.external_user_id,
                actor_type=payload.actor_type,
            )
        except CurriculumBnccLinkNotFound as exc:
            raise HTTPException(status_code=404, detail="vinculo nao encontrado") from exc
        except CurriculumBnccLinkError as exc:
            raise HTTPException(status_code=422, detail=f"{exc.code}: {exc}") from exc
    return _response(snapshot)


@curriculum_bncc_router.post(
    "/bncc-links/{link_id}/reject", response_model=BnccLinkResponse
)
async def reject_link(
    link_id: UUID,
    payload: BnccLinkDecisionRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> BnccLinkResponse:
    async with session_factory() as session:
        service = CurriculumBnccLinkService(session)
        try:
            snapshot = await service.reject(
                link_id,
                reason=payload.rationale,
                actor=identity.external_user_id,
                actor_type=payload.actor_type,
            )
        except CurriculumBnccLinkNotFound as exc:
            raise HTTPException(status_code=404, detail="vinculo nao encontrado") from exc
        except CurriculumBnccLinkError as exc:
            raise HTTPException(status_code=422, detail=f"{exc.code}: {exc}") from exc
    return _response(snapshot)


@curriculum_bncc_router.get(
    "/nodes/{content_node_id}/bncc-links", response_model=list[BnccLinkResponse]
)
async def list_links(
    content_node_id: UUID,
    status: str | None = Query(None, description="PROPOSED | VALIDATED | REJECTED"),
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> list[BnccLinkResponse]:
    async with session_factory() as session:
        snapshots = await CurriculumBnccLinkService(session).list_for_node(
            content_node_id, status=status
        )
    return [_response(snapshot) for snapshot in snapshots]
