"""FastAPI routes for school reception pre-registration and follow-up."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.schemas.reception import (
    DiagnosticAccessActivationRequest,
    DiagnosticAccessActivationResponse,
    DiagnosticReleaseResponse,
    ReceptionCandidateCreate,
    ReceptionCandidateResponse,
)
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.services.authorization import AuthorizationService
from agente_ia_edu.services.initial_diagnostic import InitialDiagnosticService
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.reception import ReceptionService


reception_router = APIRouter(prefix="/api/v1/reception", tags=["reception"])


async def _require_reception_access(
    session, context: AuthenticatedUserContext, school_id: UUID, candidate=None
) -> None:
    authorization = AuthorizationService(session)
    role_check = await authorization.require_role(context, "DIRECTOR", "COORDINATOR", "SECRETARY")
    school_check = await authorization.require_school_access(context, school_id)
    if not role_check.allowed or not school_check.allowed:
        raise HTTPException(
            status_code=403,
            detail=role_check.reason if not role_check.allowed else school_check.reason,
        )
    if context.role != "SECRETARY" or context.scope_type == "SCHOOL":
        return
    if context.scope_type == "PLATFORM" or not context.scope_external_id:
        raise HTTPException(status_code=403, detail="SECRETARY requires an explicit school scope.")
    if candidate is None:
        return
    scope_values = {
        "UNIT": candidate.unit_id,
        "SEGMENT": candidate.segment_id,
        "GRADE_LEVEL": candidate.grade_level,
        "CLASSROOM": candidate.classroom_id,
    }
    if scope_values.get(context.scope_type) != context.scope_external_id:
        raise HTTPException(status_code=403, detail="Candidate is outside the secretary academic scope.")


async def _candidate_response(
    session, service: ReceptionService, candidate, *, include_result: bool = False
) -> ReceptionCandidateResponse:
    diagnostic = await service.latest_diagnostic(candidate)
    status = candidate.status
    result = None
    if diagnostic:
        if diagnostic.status == "COMPLETED":
            status = "FEEDBACK_AVAILABLE"
            if include_result:
                result = await InitialDiagnosticService(
                    session, KnowledgeService(session)
                ).get_diagnostic_result(diagnostic.id)
        elif diagnostic.status in {"INSUFFICIENT_EVIDENCE", "CANCELLED"}:
            status = "DIAGNOSTIC_COMPLETED"
        else:
            status = "DIAGNOSTIC_IN_PROGRESS"

    return ReceptionCandidateResponse(
        id=candidate.id,
        school_id=candidate.school_id,
        full_name=candidate.full_name,
        preferred_name=candidate.preferred_name,
        birth_date=candidate.birth_date,
        guardian_name=candidate.guardian_name,
        phone=candidate.phone,
        email=candidate.email,
        academic_year=candidate.academic_year,
        unit_id=candidate.unit_id,
        segment_id=candidate.segment_id,
        grade_level=candidate.grade_level,
        classroom_id=candidate.classroom_id,
        status=status,
        released_by_external_id=candidate.released_by_external_id,
        released_at=candidate.released_at,
        external_student_id=candidate.external_student_id,
        diagnostic_id=diagnostic.id if diagnostic else None,
        diagnostic_status=diagnostic.status if diagnostic else None,
        diagnostic_started_at=diagnostic.started_at if diagnostic else None,
        diagnostic_completed_at=diagnostic.completed_at if diagnostic else None,
        questions_answered=diagnostic.total_questions_asked if diagnostic else None,
        progress_percent=None,
        result=result,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
    )


@reception_router.get("/candidates", response_model=list[ReceptionCandidateResponse])
async def list_reception_candidates(
    school_id: UUID = Query(...),
    q: str | None = Query(None, max_length=255),
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        await _require_reception_access(session, context, school_id)
        service = ReceptionService(session)
        candidates = await service.list_candidates(
            school_id=school_id,
            query=q,
            scope_type=context.scope_type if context.role == "SECRETARY" else None,
            scope_external_id=context.scope_external_id if context.role == "SECRETARY" else None,
        )
        return [await _candidate_response(session, service, item) for item in candidates]


@reception_router.post("/candidates", status_code=201, response_model=ReceptionCandidateResponse)
async def create_reception_candidate(
    request: ReceptionCandidateCreate,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        await _require_reception_access(session, context, request.school_id)
        scope_values = {
            "UNIT": request.unit_id,
            "SEGMENT": request.segment_id,
            "GRADE_LEVEL": request.grade_level,
            "CLASSROOM": request.classroom_id,
        }
        if (
            context.role == "SECRETARY"
            and context.scope_type != "SCHOOL"
            and scope_values.get(context.scope_type) != context.scope_external_id
        ):
            raise HTTPException(status_code=403, detail="Candidate is outside the secretary academic scope.")
        service = ReceptionService(session)
        try:
            candidate = await service.create_candidate(
                actor_id=context.external_identity_id, **request.model_dump()
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return await _candidate_response(session, service, candidate)


@reception_router.get("/candidates/{candidate_id}", response_model=ReceptionCandidateResponse)
async def get_reception_candidate(
    candidate_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        service = ReceptionService(session)
        candidate = await service.get_candidate(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Atendimento não encontrado.")
        await _require_reception_access(session, context, candidate.school_id, candidate)
        return await _candidate_response(session, service, candidate, include_result=True)


@reception_router.post(
    "/candidates/{candidate_id}/diagnostic-release", response_model=DiagnosticReleaseResponse
)
async def release_candidate_diagnostic(
    candidate_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        service = ReceptionService(session)
        candidate = await service.get_candidate(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Atendimento não encontrado.")
        await _require_reception_access(session, context, candidate.school_id, candidate)
        try:
            invitation = await service.release_diagnostic(
                candidate=candidate, actor_id=context.external_identity_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return DiagnosticReleaseResponse(
            candidate=await _candidate_response(session, service, candidate),
            activation_token=invitation.token,
            activation_expires_at=invitation.expires_at,
        )


@reception_router.post(
    "/diagnostic-access/activate", response_model=DiagnosticAccessActivationResponse
)
async def activate_candidate_diagnostic_access(
    request: DiagnosticAccessActivationRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        try:
            candidate = await ReceptionService(session).activate_access(
                token=request.token, external_student_id=identity.external_user_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return DiagnosticAccessActivationResponse(
            candidate_id=candidate.id,
            school_id=candidate.school_id,
            external_student_id=identity.external_user_id,
        )


__all__ = ["reception_router"]