"""R2 - "Gerenciar proposta" (spec §6): create an EssayPrompt, add its
materials, assign it to a class. TEACHER/COORDINATOR/DIRECTOR/PLATFORM_ADMIN,
the same role set catalog.py's create_material/create_resource already use.
``school_id`` always comes from the resolved context, never the request body
- same rule catalog.py's create_resource established.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_identity, get_session_factory
from ...db.models import EssayPrompt, PromptAssignment, PromptMaterial
from ...identity import ExternalIdentityContext
from ...services.authorization import AuthorizationService
from ...services.essay_proposal import EssayProposalService

essay_prompts_router = APIRouter(prefix="/api/v1/catalog/essay-prompts", tags=["essay-prompts"])


class EssayPromptCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    statement: str = Field(..., min_length=1)
    year: int


class EssayPromptResponse(BaseModel):
    id: UUID
    school_id: UUID
    title: str
    statement: str
    year: int
    status: str


class PromptMaterialCreateRequest(BaseModel):
    material_type: str = Field(..., description="TEXT, IMAGE")
    position: int = 0
    content: Optional[str] = None
    storage_uri: Optional[str] = None


class PromptMaterialResponse(BaseModel):
    id: UUID
    essay_prompt_id: UUID
    material_type: str
    content: Optional[str] = None
    storage_uri: Optional[str] = None
    position: int


class PromptAssignmentCreateRequest(BaseModel):
    class_id: UUID
    due_at: Optional[datetime] = None
    validation_enabled: bool = True


class PromptAssignmentResponse(BaseModel):
    id: UUID
    school_id: UUID
    essay_prompt_id: UUID
    class_id: UUID
    status: str
    validation_enabled: bool


async def _authorize(
    identity: ExternalIdentityContext, session: AsyncSession,
) -> uuid.UUID:
    """Returns the caller's school_id as a real uuid.UUID - AuthenticatedUserContext
    types school_id as str, but AuthorizationService actually populates it from a
    UUID column, so this normalizes either representation defensively (same
    conversion Task 11's routes use)."""
    authz = AuthorizationService(session)
    context = await authz.resolve_context(identity)
    role_check = await authz.require_role(context, "TEACHER", "COORDINATOR", "DIRECTOR", "PLATFORM_ADMIN")
    if not role_check.allowed:
        raise HTTPException(
            status_code=403,
            detail="Managing an essay proposal requires a teacher, coordinator, director, or platform admin role.",
        )
    if context.school_id is None:
        raise HTTPException(status_code=403, detail="An active school context is required.")
    return uuid.UUID(str(context.school_id))


async def _prompt_for_own_school_or_403(
    session: AsyncSession, *, essay_prompt_id: uuid.UUID, school_id: uuid.UUID,
) -> EssayPrompt:
    """Same "403, never 404, for not yours" rule essay_submissions.py's
    helpers use - a prompt from another school is 403, not the 422 a bare
    service-level ValueError would produce."""
    prompt = await session.get(EssayPrompt, essay_prompt_id)
    if prompt is None or prompt.school_id != school_id:
        raise HTTPException(status_code=403, detail="This proposal is not yours.")
    return prompt


@essay_prompts_router.post("", status_code=201, response_model=EssayPromptResponse)
async def create_essay_prompt(
    request: EssayPromptCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayPromptResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        service = EssayProposalService(session)
        prompt = await service.create_prompt(
            school_id=school_id,
            title=request.title,
            statement=request.statement,
            year=request.year,
            created_by_external_identity=identity.external_user_id,
        )
        await session.commit()
        return EssayPromptResponse(
            id=prompt.id, school_id=prompt.school_id, title=prompt.title,
            statement=prompt.statement, year=prompt.year, status=prompt.status,
        )


@essay_prompts_router.post(
    "/{essay_prompt_id}/materials", status_code=201, response_model=PromptMaterialResponse
)
async def add_prompt_material(
    essay_prompt_id: UUID,
    request: PromptMaterialCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> PromptMaterialResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        service = EssayProposalService(session)
        try:
            material = await service.add_material(
                school_id=school_id,
                essay_prompt_id=essay_prompt_id,
                material_type=request.material_type,
                content=request.content,
                storage_uri=request.storage_uri,
                position=request.position,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await session.commit()
        return PromptMaterialResponse(
            id=material.id, essay_prompt_id=material.essay_prompt_id,
            material_type=material.material_type, content=material.content,
            storage_uri=material.storage_uri, position=material.position,
        )


@essay_prompts_router.post(
    "/{essay_prompt_id}/assignments", status_code=201, response_model=PromptAssignmentResponse
)
async def create_prompt_assignment(
    essay_prompt_id: UUID,
    request: PromptAssignmentCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> PromptAssignmentResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        service = EssayProposalService(session)
        try:
            assignment = await service.create_assignment(
                school_id=school_id,
                essay_prompt_id=essay_prompt_id,
                class_id=request.class_id,
                assigned_by_external_identity=identity.external_user_id,
                due_at=request.due_at,
                validation_enabled=request.validation_enabled,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await session.commit()
        return PromptAssignmentResponse(
            id=assignment.id, school_id=assignment.school_id,
            essay_prompt_id=assignment.essay_prompt_id, class_id=assignment.class_id,
            status=assignment.status, validation_enabled=assignment.validation_enabled,
        )


class EssayPromptDetailResponse(EssayPromptResponse):
    materials: list[PromptMaterialResponse]
    assignments: list[PromptAssignmentResponse]


@essay_prompts_router.get("", response_model=list[EssayPromptResponse])
async def list_essay_prompts(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EssayPromptResponse]:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        result = await session.execute(
            select(EssayPrompt)
            .where(EssayPrompt.school_id == school_id)
            .order_by(EssayPrompt.created_at.desc())
        )
        return [
            EssayPromptResponse(
                id=p.id, school_id=p.school_id, title=p.title,
                statement=p.statement, year=p.year, status=p.status,
            )
            for p in result.scalars().all()
        ]


@essay_prompts_router.get("/{essay_prompt_id}", response_model=EssayPromptDetailResponse)
async def get_essay_prompt_detail(
    essay_prompt_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayPromptDetailResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        prompt = await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        materials = (
            await session.execute(
                select(PromptMaterial)
                .where(PromptMaterial.essay_prompt_id == prompt.id)
                .order_by(PromptMaterial.position)
            )
        ).scalars().all()
        assignments = (
            await session.execute(
                select(PromptAssignment).where(PromptAssignment.essay_prompt_id == prompt.id)
            )
        ).scalars().all()
        return EssayPromptDetailResponse(
            id=prompt.id, school_id=prompt.school_id, title=prompt.title,
            statement=prompt.statement, year=prompt.year, status=prompt.status,
            materials=[
                PromptMaterialResponse(
                    id=m.id, essay_prompt_id=m.essay_prompt_id, material_type=m.material_type,
                    content=m.content, storage_uri=m.storage_uri, position=m.position,
                )
                for m in materials
            ],
            assignments=[
                PromptAssignmentResponse(
                    id=a.id, school_id=a.school_id, essay_prompt_id=a.essay_prompt_id,
                    class_id=a.class_id, status=a.status, validation_enabled=a.validation_enabled,
                )
                for a in assignments
            ],
        )
