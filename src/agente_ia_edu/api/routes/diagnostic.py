"""
API routes for Initial Diagnostic & Student Mastery Map (Phase 13).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..dependencies import get_current_identity, get_session_factory
from ..schemas.diagnostic import (
    DiagnosticAnswerRequest,
    DiagnosticAnswerResponse,
    DiagnosticQuestionResponse,
    DiagnosticResultResponse,
    DiagnosticStartRequest,
    DiagnosticStartResponse,
)
from ..schemas.learning_path import PracticeQuestionOption
from ...identity import ExternalIdentityContext
from ...db.models import QuestionVersion
from ...services.initial_diagnostic import InitialDiagnosticService
from ...services.knowledge import KnowledgeService

diagnostic_router = APIRouter(
    prefix="/api/v1/student",
    tags=["initial-diagnostic"],
)


async def _to_diagnostic_question_response(session, selection) -> DiagnosticQuestionResponse | None:
    if not selection:
        return None

    stmt = (
        select(QuestionVersion)
        .where(QuestionVersion.id == selection.question_version_id)
        .options(selectinload(QuestionVersion.options))
    )
    res = await session.execute(stmt)
    version = res.scalar_one_or_none()
    if not version:
        return None

    options = [
        PracticeQuestionOption(
            id=opt.id,
            option_key=opt.option_key,
            text=opt.text,
            position=opt.position,
        )
        for opt in sorted(version.options, key=lambda o: o.position)
        if opt.is_valid_option
    ]

    return DiagnosticQuestionResponse(
        selection_id=selection.id,
        position=selection.position,
        question_version_id=selection.question_version_id,
        content_node_id=selection.content_node_id,
        difficulty_level=selection.difficulty_level,
        canonical_text=version.canonical_text,
        options=options,
    )


@diagnostic_router.post(
    "/diagnostic/start",
    status_code=201,
    response_model=DiagnosticStartResponse,
    summary="Start an initial diagnostic session",
    description="Initiates an adaptive diagnostic sondage. school_id is optional (supports Independent students).",
)
async def start_initial_diagnostic(
    request: DiagnosticStartRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> DiagnosticStartResponse:
    student_id = identity.external_user_id
    # If request doesn't provide school_id, fall back to identity context
    school_id = request.school_id or (UUID(identity.institution_id) if identity.institution_id else None)

    async with session_factory() as session:
        ks = KnowledgeService(session)
        service = InitialDiagnosticService(session, ks)

        diagnostic, first_q = await service.start_diagnostic(
            student_id=student_id,
            school_id=school_id,
            classroom_id=request.classroom_id,
            academic_year=request.academic_year,
            grade_level=request.grade_level,
            discipline=request.discipline,
            diagnostic_version=request.diagnostic_version,
            metadata=request.metadata,
        )

        q_resp = await _to_diagnostic_question_response(session, first_q)

        return DiagnosticStartResponse(
            diagnostic_id=diagnostic.id,
            student_id=diagnostic.student_id,
            school_id=diagnostic.school_id,
            is_independent=diagnostic.school_id is None,
            status=diagnostic.status,
            diagnostic_version=diagnostic.diagnostic_version,
            started_at=diagnostic.started_at,
            next_question=q_resp,
        )


@diagnostic_router.get(
    "/diagnostic/{diagnostic_id}",
    response_model=DiagnosticStartResponse,
    summary="Get current state of an initial diagnostic session",
)
async def get_initial_diagnostic(
    diagnostic_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> DiagnosticStartResponse:
    student_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        service = InitialDiagnosticService(session, ks)

        result = await service.get_diagnostic_result(diagnostic_id)
        if result["student_id"] != student_id:
            raise HTTPException(status_code=403, detail="Access denied")

        diag = await session.get(InitialDiagnosticService, diagnostic_id)
        next_q = await service._get_next_question_selection(diag)
        q_resp = await _to_diagnostic_question_response(session, next_q)

        return DiagnosticStartResponse(
            diagnostic_id=diag.id,
            student_id=diag.student_id,
            school_id=diag.school_id,
            is_independent=diag.school_id is None,
            status=diag.status,
            diagnostic_version=diag.diagnostic_version,
            started_at=diag.started_at,
            next_question=q_resp,
        )


@diagnostic_router.post(
    "/diagnostic/{diagnostic_id}/questions/{selection_id}/answer",
    response_model=DiagnosticAnswerResponse,
    summary="Submit answer for a diagnostic question",
)
async def answer_diagnostic_question(
    diagnostic_id: UUID,
    selection_id: UUID,
    request: DiagnosticAnswerRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> DiagnosticAnswerResponse:
    student_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        service = InitialDiagnosticService(session, ks)

        try:
            diag, is_correct, is_complete, next_q = await service.answer_question(
                diagnostic_id=diagnostic_id,
                selection_id=selection_id,
                selected_option_id=request.selected_option_id,
                response_text=request.response_text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        if diag.student_id != student_id:
            raise HTTPException(status_code=403, detail="Access denied")

        q_resp = await _to_diagnostic_question_response(session, next_q)

        return DiagnosticAnswerResponse(
            diagnostic_id=diag.id,
            is_correct=is_correct,
            is_complete=is_complete,
            status=diag.status,
            questions_asked=diag.total_questions_asked,
            overall_confidence=float(diag.overall_confidence),
            next_question=q_resp,
        )


@diagnostic_router.get(
    "/diagnostic/{diagnostic_id}/result",
    response_model=DiagnosticResultResponse,
    summary="Get initial diagnostic result and mastery map",
)
async def get_diagnostic_result(
    diagnostic_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> DiagnosticResultResponse:
    student_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        service = InitialDiagnosticService(session, ks)

        try:
            res = await service.get_diagnostic_result(diagnostic_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

        if res["student_id"] != student_id:
            raise HTTPException(status_code=403, detail="Access denied")

        return DiagnosticResultResponse(**res)


@diagnostic_router.get(
    "/mastery-map",
    summary="Get student's overall initial/current mastery map",
)
async def get_student_mastery_map(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    student_id = identity.external_user_id

    async with session_factory() as session:
        ks = KnowledgeService(session)
        service = InitialDiagnosticService(session, ks)

        stmt = select(InitialDiagnostic).where(
            InitialDiagnostic.student_id == student_id,
            InitialDiagnostic.status == "COMPLETED",
        ).order_by(InitialDiagnostic.completed_at.desc())
        res = await session.execute(stmt)
        latest_diag = res.scalars().first()

        if latest_diag:
            return await service.get_diagnostic_result(latest_diag.id)

        return {
            "student_id": student_id,
            "school_id": identity.institution_id,
            "is_independent": identity.institution_id is None,
            "status": "NOT_STARTED",
            "message": "Nenhum diagnóstico inicial concluído ainda. Inicie o teste para mapear seu nível.",
            "mastery_map": [],
            "probable_gaps": [],
        }
