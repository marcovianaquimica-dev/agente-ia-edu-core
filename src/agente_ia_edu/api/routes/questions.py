from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.session import create_session_factory
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.repositories.questions import QuestionRepository
from agente_ia_edu.services.content_authoring import QuestionAuthoringService
from agente_ia_edu.services.questions import QuestionService
from agente_ia_edu.api.schemas.questions import (
    QuestionAuthoringRequest,
    QuestionAuthoringResponse,
    QuestionDetail,
    QuestionListResponse,
    QuestionReviewRequest,
)

router = APIRouter(prefix="/api/v1/questions", tags=["questions"])


async def get_question_service() -> AsyncIterator[QuestionService]:
    session_factory = create_session_factory()
    async with session_factory() as session:
        yield QuestionService(QuestionRepository(session))


@router.get("", response_model=QuestionListResponse)
async def list_questions(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    institution_code: str | None = None,
    exam_code: str | None = None,
    year: int | None = Query(default=None, gt=0),
    content: str | None = None,
    service: QuestionService = Depends(get_question_service),
) -> QuestionListResponse:
    return await service.list_questions(
        page=page,
        limit=limit,
        institution_code=institution_code,
        exam_code=exam_code,
        year=year,
        content=content,
    )


@router.post("", response_model=QuestionAuthoringResponse, status_code=201)
async def create_question_authoring(
    request: QuestionAuthoringRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> QuestionAuthoringResponse:
    async with session_factory() as session:
        service = QuestionAuthoringService(session)
        try:
            result = await service.create_question(
                created_by_external_identity=identity.external_user_id or identity.provider,
                statement=request.statement,
                options=request.options,
                correct_option=request.correct_option,
                author_type=request.author_type,
                metadata={**(request.metadata or {}), "subject": request.subject or "general"},
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        return QuestionAuthoringResponse(
            question_id=result.question_id,
            version_id=result.version_id,
            status=result.status,
        )


@router.post("/{question_id}/review", response_model=QuestionAuthoringResponse)
async def review_question_authoring(
    question_id: UUID,
    request: QuestionReviewRequest,
    session_factory=Depends(get_session_factory),
) -> QuestionAuthoringResponse:
    async with session_factory() as session:
        service = QuestionAuthoringService(session)
        try:
            if request.action == "submit":
                question = await service.submit_for_review(question_id)
            elif request.action == "approve":
                question = await service.approve(question_id)
            elif request.action == "reject":
                question = await service.reject(question_id, reason=request.reason)
            elif request.action == "archive":
                question = await service.archive_question(question_id)
            else:
                raise ValueError("Unsupported review action.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        version = await service.get_current_version(question_id)
        return QuestionAuthoringResponse(
            question_id=question.id,
            version_id=version.id,
            status=question.validation_status,
        )


@router.get("/{question_id}", response_model=QuestionDetail)
async def get_question(
    question_id: UUID,
    include_answer_key: bool = Query(default=False),
    service: QuestionService = Depends(get_question_service),
) -> QuestionDetail:
    question = await service.get_question(question_id, include_answer_key=include_answer_key)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return question
