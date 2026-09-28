"""R2 - "Gerenciar proposta" (spec §6): create an EssayPrompt, add its
materials, assign it to a class. TEACHER/COORDINATOR/DIRECTOR/PLATFORM_ADMIN,
the same role set catalog.py's create_material/create_resource already use.
``school_id`` always comes from the resolved context, never the request body
- same rule catalog.py's create_resource established.
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_identity, get_session_factory
from ...db.models import EssayPrompt, PromptAssignment, PromptMaterial
from ...identity import ExternalIdentityContext
from ...services.authorization import AuthorizationService
from ...services.essay_dashboard_export import build_essay_dashboard_xlsx, xlsx_media_type
from ...services.essay_proposal import EssayProposalService, as_aware_utc
from ...services.essay_teacher_dashboard import EssayDashboardResponse, build_essay_prompt_dashboard
from ...services.material_storage import MaterialStorage

# Same cap essay_submissions.py's page/document uploads already use - no
# reason for a teacher's motivational-material upload to be more permissive.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024

essay_prompts_router = APIRouter(prefix="/api/v1/catalog/essay-prompts", tags=["essay-prompts"])


class EssayPromptCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    statement: str = Field(..., min_length=1)
    year: int


class EssayPromptTrashResponse(BaseModel):
    id: UUID
    school_id: UUID
    title: str
    statement: str
    year: int
    status: str
    deleted_at: datetime
    days_remaining: int


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


class PromptAssignmentBulkCreateRequest(BaseModel):
    class_ids: list[UUID] = Field(..., min_length=1)
    due_at: Optional[datetime] = None
    validation_enabled: bool = True


class PromptAssignmentBulkCreateResponse(BaseModel):
    assigned: list[PromptAssignmentResponse]
    failures: dict[str, str]


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
    include_deleted: bool = False,
) -> EssayPrompt:
    """Same "403, never 404, for not yours" rule essay_submissions.py's
    helpers use - a prompt from another school is 403, not the 422 a bare
    service-level ValueError would produce. A prompt in the Lixeira behaves
    the same way by default (403) for every normal route (detail, materials,
    assignments, dashboard) - only the restore route needs it, via
    include_deleted=True."""
    prompt = await session.get(EssayPrompt, essay_prompt_id)
    if prompt is None or prompt.school_id != school_id:
        raise HTTPException(status_code=403, detail="This proposal is not yours.")
    if prompt.deleted_at is not None and not include_deleted:
        raise HTTPException(status_code=403, detail="This proposal is in the trash.")
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
        # Build the response BEFORE commit: commit() expires `prompt`
        # (expire_on_commit=True in production - see db/session.py), and
        # accessing its attributes afterwards triggers a synchronous
        # lazy-load that raises MissingGreenlet in an async context. Test
        # fixtures using expire_on_commit=False mask this.
        response = EssayPromptResponse(
            id=prompt.id, school_id=prompt.school_id, title=prompt.title,
            statement=prompt.statement, year=prompt.year, status=prompt.status,
        )
        await session.commit()
        return response


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
        # See create_essay_prompt above: build the response before commit()
        # expires `material`, to avoid a MissingGreenlet error in
        # production (expire_on_commit=True).
        response = PromptMaterialResponse(
            id=material.id, essay_prompt_id=material.essay_prompt_id,
            material_type=material.material_type, content=material.content,
            storage_uri=material.storage_uri, position=material.position,
        )
        await session.commit()
        return response


@essay_prompts_router.post(
    "/{essay_prompt_id}/materials/upload", status_code=201, response_model=PromptMaterialResponse
)
async def upload_prompt_material(
    essay_prompt_id: UUID,
    position: int = Form(...),
    file: UploadFile = File(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> PromptMaterialResponse:
    """Real file upload for a "texto motivador" (any file type - a teacher
    may attach a reportagem PDF, an infographic image, a chart screenshot,
    whatever the proposal needs). Mirrors essay_submissions.py's page/
    document upload: stream to a capped temp file first, THEN hand the real
    path to MaterialStorage (the same content-addressed local store student
    essay uploads already use), so a bad/huge upload never lands a partial
    file in managed storage."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )

        tmp_dir = Path(tempfile.mkdtemp(prefix="r2_prompt_material_upload_"))
        tmp_path = tmp_dir / (file.filename or "material")
        size = 0
        with open(tmp_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    out.close()
                    tmp_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="file too large (max 25MB)")
                out.write(chunk)

        managed_path, _digest = MaterialStorage().store(tmp_path)

        service = EssayProposalService(session)
        try:
            material = await service.add_material(
                school_id=school_id,
                essay_prompt_id=essay_prompt_id,
                material_type="FILE",
                storage_uri=str(managed_path),
                position=position,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # See create_essay_prompt above: build the response before commit()
        # expires `material`, to avoid a MissingGreenlet error in
        # production (expire_on_commit=True).
        response = PromptMaterialResponse(
            id=material.id, essay_prompt_id=material.essay_prompt_id,
            material_type=material.material_type, content=material.content,
            storage_uri=material.storage_uri, position=material.position,
        )
        await session.commit()
        return response


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
        # See create_essay_prompt above: build the response before commit()
        # expires `assignment`, to avoid a MissingGreenlet error in
        # production (expire_on_commit=True).
        response = PromptAssignmentResponse(
            id=assignment.id, school_id=assignment.school_id,
            essay_prompt_id=assignment.essay_prompt_id, class_id=assignment.class_id,
            status=assignment.status, validation_enabled=assignment.validation_enabled,
        )
        await session.commit()
        return response


@essay_prompts_router.post(
    "/{essay_prompt_id}/assignments/bulk", response_model=PromptAssignmentBulkCreateResponse
)
async def create_prompt_assignments_bulk(
    essay_prompt_id: UUID,
    request: PromptAssignmentBulkCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> PromptAssignmentBulkCreateResponse:
    """Atribui a mesma proposta a varias turmas de uma vez (pedido do
    professor: escolher multiplas turmas ao inves de repetir o fluxo de
    atribuicao turma por turma). Best-effort por turma, igual ao
    bulk-approve de correcoes - uma turma ja atribuida antes nao derruba as
    demais."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        service = EssayProposalService(session)
        assigned, failures = await service.create_assignments_bulk(
            school_id=school_id,
            essay_prompt_id=essay_prompt_id,
            class_ids=request.class_ids,
            assigned_by_external_identity=identity.external_user_id,
            due_at=request.due_at,
            validation_enabled=request.validation_enabled,
        )
        return PromptAssignmentBulkCreateResponse(
            assigned=[PromptAssignmentResponse(**a) for a in assigned],
            failures={str(class_id): reason for class_id, reason in failures.items()},
        )


@essay_prompts_router.get("/{essay_prompt_id}/dashboard", response_model=EssayDashboardResponse)
async def get_essay_prompt_dashboard(
    essay_prompt_id: UUID,
    grade_level_id: Optional[UUID] = Query(None),
    class_id: Optional[UUID] = Query(None),
    student_id: Optional[UUID] = Query(None),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayDashboardResponse:
    """% de entrega, media geral, media por competencia, lista de quem
    entregou/nao entregou e o plano de acao da turma para esta proposta -
    ve services/essay_teacher_dashboard.py para a logica de agregacao
    (determinstica, sem chamada de IA)."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        try:
            return await build_essay_prompt_dashboard(
                session, school_id=school_id, essay_prompt_id=essay_prompt_id,
                grade_level_id=grade_level_id, class_id=class_id, student_id=student_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@essay_prompts_router.get("/{essay_prompt_id}/dashboard/export.xlsx")
async def export_essay_prompt_dashboard_xlsx(
    essay_prompt_id: UUID,
    report_type: str = Query(..., pattern="^(grades_total|grades_per_competency|submission_list)$"),
    grade_level_id: Optional[UUID] = Query(None),
    class_id: Optional[UUID] = Query(None),
    student_id: Optional[UUID] = Query(None),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> Response:
    """Same three views the dashboard screen offers (nota total / nota por
    competencia / quem entregou), exported as a real XLSX file - the same
    filters (serie/turma/aluno) narrow the export exactly like they narrow
    the on-screen dashboard, since both read from the same
    build_essay_prompt_dashboard() call."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        prompt = await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        try:
            dashboard = await build_essay_prompt_dashboard(
                session, school_id=school_id, essay_prompt_id=essay_prompt_id,
                grade_level_id=grade_level_id, class_id=class_id, student_id=student_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        data = build_essay_dashboard_xlsx(dashboard, report_type=report_type)
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in prompt.title).strip() or "redacao"
        filename = f"{report_type}-{safe_title[:60]}.xlsx"
        return Response(
            content=data, media_type=xlsx_media_type(),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
            .where(EssayPrompt.school_id == school_id, EssayPrompt.deleted_at.is_(None))
            .order_by(EssayPrompt.created_at.desc())
        )
        return [
            EssayPromptResponse(
                id=p.id, school_id=p.school_id, title=p.title,
                statement=p.statement, year=p.year, status=p.status,
            )
            for p in result.scalars().all()
        ]


@essay_prompts_router.get("/trash", response_model=list[EssayPromptTrashResponse])
async def list_essay_prompts_trash(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EssayPromptTrashResponse]:
    # Registered before GET "/{essay_prompt_id}" on purpose - FastAPI matches
    # routes in registration order, and "trash" would otherwise be swallowed
    # as an (invalid) essay_prompt_id by that catch-all path param.
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        service = EssayProposalService(session)
        prompts = await service.list_trash(school_id=school_id)
        now = datetime.now(timezone.utc)
        return [
            EssayPromptTrashResponse(
                id=p.id, school_id=p.school_id, title=p.title, statement=p.statement,
                year=p.year, status=p.status, deleted_at=p.deleted_at,
                days_remaining=max(
                    0,
                    EssayProposalService.TRASH_RETENTION_DAYS - (now - as_aware_utc(p.deleted_at)).days,
                ),
            )
            for p in prompts
        ]


@essay_prompts_router.delete("/{essay_prompt_id}", status_code=204)
async def delete_essay_prompt(
    essay_prompt_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> None:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        service = EssayProposalService(session)
        try:
            await service.soft_delete_prompt(school_id=school_id, essay_prompt_id=essay_prompt_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await session.commit()


@essay_prompts_router.post("/{essay_prompt_id}/restore", response_model=EssayPromptResponse)
async def restore_essay_prompt(
    essay_prompt_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayPromptResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        service = EssayProposalService(session)
        try:
            prompt = await service.restore_prompt(school_id=school_id, essay_prompt_id=essay_prompt_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response = EssayPromptResponse(
            id=prompt.id, school_id=prompt.school_id, title=prompt.title,
            statement=prompt.statement, year=prompt.year, status=prompt.status,
        )
        await session.commit()
        return response


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
