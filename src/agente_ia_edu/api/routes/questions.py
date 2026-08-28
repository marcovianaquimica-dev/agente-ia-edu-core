from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.session import create_session_factory
from agente_ia_edu.repositories.questions import QuestionRepository
from agente_ia_edu.services.questions import QuestionService
from agente_ia_edu.api.schemas.questions import QuestionDetail, QuestionListResponse

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


@router.get("/{question_id}", response_model=QuestionDetail)
async def get_question(
    question_id: UUID,
    include_answer_key: bool = Query(default=False),
    service: QuestionService = Depends(get_question_service),
) -> QuestionDetail:
    question = await service.get_question(question_id, include_answer_key=include_answer_key)
    if question is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Question not found")
    return question
