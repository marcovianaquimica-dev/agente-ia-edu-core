from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from agente_ia_edu.api.dependencies import get_current_authenticated_context, get_current_identity, get_session_factory
from agente_ia_edu.api.routes.teacher_materials import _load_material, _require_author
from agente_ia_edu.api.schemas.assessments import ModificationProposalCreateRequest, ModificationProposalResponse
from agente_ia_edu.db.models import AssessmentItem, AssessmentVersion, AssessmentWorkflowAudit, ModificationProposal, QuestionOption, QuestionVersion
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.providers.errors import ProviderError
from agente_ia_edu.providers.factory import build_text_provider
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService
from agente_ia_edu.services.question_modification import ModificationType, ProposedQuestion, QuestionModificationAdapter


router = APIRouter(prefix="/api/v1/teacher/questions", tags=["question-modification-proposals"])


def get_modification_provider():
    # AI-agnostic: the concrete text provider is selected by AI_PROVIDER config.
    return build_text_provider()


def _response(proposal: ModificationProposal) -> ModificationProposalResponse:
    return ModificationProposalResponse(
        id=proposal.id, status=proposal.status,
        original_question_version_id=proposal.original_question_version_id,
        assessment_item_id=proposal.assessment_item_id,
        modification_type=proposal.modification_type, instruction=proposal.instruction,
        proposal=proposal.proposed_content, provider=proposal.provider, model=proposal.model,
        requested_by_external_id=proposal.requested_by_external_id, school_id=proposal.school_id,
        created_at=proposal.created_at, question_version_id=proposal.accepted_question_version_id,
    )


@router.post("/{question_version_id}/modification-proposals", response_model=ModificationProposalResponse, status_code=201)
async def create_modification_proposal(
    question_version_id: UUID,
    payload: ModificationProposalCreateRequest,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    provider=Depends(get_modification_provider),
    session_factory=Depends(get_session_factory),
) -> ModificationProposalResponse:
    _require_author(context)
    try:
        modification_type = ModificationType(payload.modification_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Tipo de modificacao nao suportado.") from exc
    async with session_factory() as session:
        item = await session.get(AssessmentItem, payload.assessment_item_id)
        if item is None or item.question_version_id != question_version_id:
            raise HTTPException(status_code=404, detail="Question is not the requested list item")
        version = await session.scalar(select(AssessmentVersion).where(AssessmentVersion.id == item.assessment_version_id))
        if version is None:
            raise HTTPException(status_code=404, detail="Exercise list version not found")
        material, _ = await _load_material(session, version.assessment_id, context)
        original = await session.get(QuestionVersion, question_version_id, options=[selectinload(QuestionVersion.options)])
        if original is None:
            raise HTTPException(status_code=404, detail="Question version not found")
        if context.role == "TEACHER":
            universe_service = PedagogicalUniverseService(session)
            try:
                universe = await universe_service.resolve_active_universe(
                    type("Identity", (), {"external_user_id": context.external_identity_id, "institution_id": str(context.school_id), "metadata": {}})()
                )
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            if not await universe_service.contains_question_version(universe.id, original.id):
                raise HTTPException(status_code=403, detail="A questao esta fora do universo pedagogico autorizado.")
        adapter = QuestionModificationAdapter(provider)
        count = await session.scalar(select(func.count()).select_from(ModificationProposal).where(
            ModificationProposal.original_question_version_id == original.id,
            ModificationProposal.requested_by_external_id == context.external_identity_id,
        ))
        if count >= adapter.max_proposals_per_question:
            raise HTTPException(status_code=429, detail="Limite de propostas de modificacao atingido para esta questao.")
        question_data = {
            "statement": original.statement or original.canonical_text,
            "options": [option.text for option in sorted(original.options, key=lambda option: option.position)],
            "difficulty": original.recommended_difficulty,
        }
        try:
            structured, provider_name, model = await adapter.propose(
                question_data=question_data, modification_type=modification_type, instruction=payload.instruction
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(status_code=503, detail="Nao foi possivel gerar a modificacao. Tente novamente.") from exc
        proposal = ModificationProposal(
            school_id=material.school_id, assessment_item_id=item.id,
            original_question_version_id=original.id, requested_by_external_id=context.external_identity_id,
            modification_type=modification_type.value, instruction=payload.instruction,
            proposed_content=structured.model_dump(mode="json"), provider=provider_name, model=model,
        )
        session.add(proposal)
        await session.commit()
        await session.refresh(proposal)
        return _response(proposal)


@router.post("/modification-proposals/{proposal_id}/cancel", response_model=ModificationProposalResponse)
async def cancel_modification_proposal(
    proposal_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ModificationProposalResponse:
    _require_author(context)
    async with session_factory() as session:
        proposal = await session.get(ModificationProposal, proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="Modification proposal not found")
        if proposal.school_id != context.school_id or proposal.requested_by_external_id != context.external_identity_id:
            raise HTTPException(status_code=403, detail="Modification proposal access denied")
        if proposal.status == "PENDING":
            proposal.status = "CANCELLED"
            proposal.cancelled_at = datetime.now(timezone.utc)
            proposal.cancelled_by_external_id = context.external_identity_id
            await session.commit()
            await session.refresh(proposal)
        return _response(proposal)


@router.post("/modification-proposals/{proposal_id}/accept", response_model=ModificationProposalResponse)
async def accept_modification_proposal(
    proposal_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> ModificationProposalResponse:
    _require_author(context)
    async with session_factory() as session:
        async with session.begin():
            proposal = await session.get(ModificationProposal, proposal_id)
            if proposal is None:
                raise HTTPException(status_code=404, detail="Modification proposal not found")
            if proposal.school_id != context.school_id or proposal.requested_by_external_id != context.external_identity_id:
                raise HTTPException(status_code=403, detail="Modification proposal access denied")
            if proposal.status == "ACCEPTED":
                return _response(proposal)
            if proposal.status != "PENDING":
                raise HTTPException(status_code=409, detail="Only pending proposals can be accepted")
            item = await session.get(AssessmentItem, proposal.assessment_item_id)
            if item is None or item.question_version_id != proposal.original_question_version_id:
                raise HTTPException(status_code=409, detail="Proposal list context is no longer valid")
            assessment_version = await session.get(AssessmentVersion, item.assessment_version_id)
            if assessment_version is None:
                raise HTTPException(status_code=404, detail="Exercise list version not found")
            material, _ = await _load_material(session, assessment_version.assessment_id, context)
            original = await session.get(QuestionVersion, proposal.original_question_version_id)
            if original is None:
                raise HTTPException(status_code=404, detail="Original question version not found")
            if context.role == "TEACHER":
                universe_service = PedagogicalUniverseService(session)
                try:
                    universe = await universe_service.resolve_active_universe(identity)
                except PermissionError as exc:
                    raise HTTPException(status_code=403, detail=str(exc)) from exc
                if not await universe_service.contains_question_version(universe.id, original.id):
                    raise HTTPException(status_code=403, detail="Question is outside the authorized pedagogical universe")
            try:
                content = ProposedQuestion.model_validate(proposal.proposed_content)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Proposal content is invalid") from exc
            derived = QuestionVersion(
                question_id=original.question_id,
                version_kind="teacher_modification",
                parent_version_id=original.id,
                canonical_text=content.statement,
                statement=content.statement,
                content_hash=hashlib.sha256(content.statement.encode("utf-8")).hexdigest(),
                recommended_difficulty=content.difficulty,
                created_by_type=context.role,
                created_by_id=context.external_identity_id,
                change_reason=proposal.instruction or proposal.modification_type,
                metadata_={
                    "origin_type": "TEACHER_MODIFICATION",
                    "proposal_id": str(proposal.id),
                    "provider": proposal.provider,
                    "model": proposal.model,
                },
            )
            session.add(derived)
            await session.flush()
            for position, option_text in enumerate(content.options, start=1):
                session.add(QuestionOption(
                    question_version_id=derived.id, option_key=chr(64 + position), position=position,
                    text=option_text, is_valid_option=option_text == content.correct_option,
                ))
            item.question_version_id = derived.id
            proposal.status = "ACCEPTED"
            proposal.accepted_question_version_id = derived.id
            proposal.accepted_at = datetime.now(timezone.utc)
            proposal.accepted_by_external_id = context.external_identity_id
            session.add(AssessmentWorkflowAudit(
                assessment_id=material.id, action="QUESTION_MODIFICATION_ACCEPTED",
                performed_by_external_id=context.external_identity_id,
                metadata_={"proposal_id": str(proposal.id), "assessment_item_id": str(item.id), "original_question_version_id": str(original.id), "accepted_question_version_id": str(derived.id)},
            ))
        await session.refresh(proposal)
        return _response(proposal)