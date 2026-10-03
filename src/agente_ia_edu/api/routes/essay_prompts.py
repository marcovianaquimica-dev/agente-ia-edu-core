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
from ...db.models import EssayPrompt, PromptAssignment, PromptMaterial, School
from ...identity import ExternalIdentityContext
from ...services.authorization import AuthorizationService
from ...services.essay_answer_sheet import answer_sheet_available, render_answer_sheet_pdf
from ...services.essay_dashboard_export import build_essay_dashboard_xlsx, xlsx_media_type
from ...services.essay_proposal import EssayProposalService, as_aware_utc
from ...services.essay_teacher_dashboard import EssayDashboardResponse, build_essay_prompt_dashboard
from ...services.material_storage import MaterialStorage
from ...services.platform_essay_prompt import PlatformEssayPromptService, materialization_year

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
    # Para uma proposta da plataforma ainda NAO materializada, este e o
    # school_id da escola do professor que esta lendo - a escola que vai
    # receber a copia se ele atribuir. Nunca vem do corpo da requisicao.
    school_id: UUID
    title: str
    statement: str
    year: int
    status: str
    # True tanto para uma proposta da plataforma ainda nao materializada
    # quanto para a copia dela ja materializada nesta escola: em ambos os
    # casos ela e somente-leitura para o professor (spec, decisao 3).
    is_platform: bool = False
    # A origem em platform_essay_prompts, quando houver.
    platform_prompt_id: Optional[UUID] = None


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
    class_id: Optional[UUID] = None
    student_id: Optional[UUID] = None
    status: str
    validation_enabled: bool


class PromptAssignmentBulkCreateRequest(BaseModel):
    class_ids: list[UUID] = Field(..., min_length=1)
    due_at: Optional[datetime] = None
    validation_enabled: bool = True


class PromptAssignmentBulkCreateResponse(BaseModel):
    assigned: list[PromptAssignmentResponse]
    failures: dict[str, str]


class PromptAssignmentCombinedRequest(BaseModel):
    class_ids: list[UUID] = Field(default_factory=list)
    grade_level_ids: list[UUID] = Field(default_factory=list)
    student_ids: list[UUID] = Field(default_factory=list)
    due_at: Optional[datetime] = None
    validation_enabled: bool = True


class PromptAssignmentCombinedResponse(BaseModel):
    created_count: int
    already_assigned_count: int
    log_id: UUID


class StudentSearchResultResponse(BaseModel):
    student_id: UUID
    full_name: str
    document_number: Optional[str] = None
    class_name: str


class PromptAssignmentLogResponse(BaseModel):
    id: UUID
    created_at: datetime
    assigned_by_external_identity: str
    target_summary: dict


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


async def _materialize_if_platform_prompt(
    session: AsyncSession,
    *,
    essay_prompt_id: uuid.UUID,
    school_id: uuid.UUID,
    created_by_external_identity: str,
) -> uuid.UUID:
    """Devolve sempre o id de um EssayPrompt REAL e escopado a esta escola.

    A lista do professor mistura propostas da escola e propostas da
    plataforma ainda nao materializadas sob o mesmo campo de id (spec s2) -
    e aqui que o backend decide qual e qual. Se o id for de uma
    PlatformEssayPrompt, busca-ou-cria a copia desta escola (spec s4) e
    devolve o id dela; caso contrario devolve o id recebido, intacto.

    Do ponto de vista de PromptAssignment pra frente nada muda: ele sempre
    aponta para um EssayPrompt escopado a escola, como sempre apontou.

    A materializacao acontece ANTES da validacao da turma, entao uma
    atribuicao que depois falhar (turma de outra escola, turma ja atribuida)
    deixa a copia criada na escola do professor. E inofensivo: a copia e uma
    proposta comum daquela escola, sem turma nenhuma, e a proxima tentativa a
    reaproveita em vez de criar outra.
    """
    service = PlatformEssayPromptService(session)
    origin = await service.get_platform_prompt(essay_prompt_id)
    if origin is None:
        return essay_prompt_id

    copy = await service.materialize_for_school(
        platform_essay_prompt_id=origin.id,
        school_id=school_id,
        created_by_external_identity=created_by_external_identity,
    )
    # Ler copy.id ANTES do commit: commit() expira o objeto
    # (expire_on_commit=True em producao) e a leitura seguinte viraria um
    # lazy-load sincrono com MissingGreenlet.
    copy_id = copy.id
    # Commit imediato, antes de qualquer atribuicao: create_assignments_bulk
    # faz rollback por turma que falha (ver o docstring dele em
    # services/essay_proposal.py) e um rollback depois deste ponto
    # descartaria a copia recem-criada enquanto as turmas seguintes do mesmo
    # lote continuariam apontando para o id dela.
    await session.commit()
    return copy_id


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
        essay_prompt_id = await _materialize_if_platform_prompt(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id,
            created_by_external_identity=identity.external_user_id,
        )
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
        essay_prompt_id = await _materialize_if_platform_prompt(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id,
            created_by_external_identity=identity.external_user_id,
        )
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


async def _require_school_wide_scope_for_series(
    identity: ExternalIdentityContext, session: AsyncSession
) -> None:
    """Atribuir a uma serie inteira expande pra TODAS as turmas dela,
    potencialmente fora do escopo de um professor de turma unica - mesma
    regra que api/routes/essay_batches.py's _require_school_wide_scope ja
    usa pro mesmo risco no envio em lote: DIRECTOR/COORDINATOR/PLATFORM_ADMIN
    sempre passam; TEACHER so passa com escopo PLATFORM ou SCHOOL. So e
    chamada quando grade_level_ids nao esta vazio - escopo de turma/aluno
    direto continua sem esse gate (mesmo nivel de exposicao que
    assignments/bulk ja tem hoje pra class_ids, pre-existente, fora do
    escopo desta correcao)."""
    authz = AuthorizationService(session)
    context = await authz.resolve_context(identity)
    if context.role.upper() in {"DIRECTOR", "COORDINATOR", "PLATFORM_ADMIN"}:
        return
    if context.role.upper() == "TEACHER" and context.scope_type.upper() in {"PLATFORM", "SCHOOL"}:
        return
    raise HTTPException(
        status_code=403,
        detail="Atribuir a uma serie inteira exige escopo de toda a escola "
        "(diretor, coordenador, ou professor com abrangencia de escola).",
    )


@essay_prompts_router.post(
    "/{essay_prompt_id}/assignments/combined",
    response_model=PromptAssignmentCombinedResponse,
)
async def create_prompt_assignments_combined(
    essay_prompt_id: UUID,
    request: PromptAssignmentCombinedRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> PromptAssignmentCombinedResponse:
    """Atribui a proposta a um publico combinado - series inteiras
    (expandidas em turmas reais), turmas inteiras e alunos especificos,
    tudo numa chamada so. Idempotente: reatribuir um alvo que ja tinha
    essa proposta nao e erro."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        if request.grade_level_ids:
            await _require_school_wide_scope_for_series(identity, session)
        essay_prompt_id = await _materialize_if_platform_prompt(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id,
            created_by_external_identity=identity.external_user_id,
        )
        await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        service = EssayProposalService(session)
        try:
            result = await service.create_assignments_combined(
                school_id=school_id, essay_prompt_id=essay_prompt_id,
                class_ids=request.class_ids, grade_level_ids=request.grade_level_ids,
                student_ids=request.student_ids,
                assigned_by_external_identity=identity.external_user_id,
                due_at=request.due_at, validation_enabled=request.validation_enabled,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response = PromptAssignmentCombinedResponse(**result)
        await session.commit()
        return response


@essay_prompts_router.get("/students-search", response_model=list[StudentSearchResultResponse])
async def search_prompt_assignment_students(
    class_ids: list[UUID] = Query(default_factory=list),
    q: str = Query(""),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[StudentSearchResultResponse]:
    """Busca de alunos pro seletor de publico - restrita as turmas que o
    CHAMADOR ja confirmou estarem no escopo autorizado do professor
    (TeacherPortalService.list_teacher_classrooms, a mesma rota que ja
    preenche o seletor de Turma hoje); esta rota nao resolve autorizacao
    de turma sozinha, so confia nos class_ids que recebe - um class_id de
    fora da escola do professor simplesmente nao aparece no resultado
    porque o filtro ja inclui school_id."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        service = EssayProposalService(session)
        results = await service.search_students_for_assignment(
            school_id=school_id, class_ids=class_ids, query=q,
        )
        return [
            StudentSearchResultResponse(
                student_id=student_id, full_name=full_name,
                document_number=document_number, class_name=class_name,
            )
            for student_id, full_name, document_number, class_name in results
        ]


@essay_prompts_router.get(
    "/{essay_prompt_id}/assignment-log", response_model=list[PromptAssignmentLogResponse]
)
async def get_prompt_assignment_log(
    essay_prompt_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[PromptAssignmentLogResponse]:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        service = EssayProposalService(session)
        logs = await service.list_assignment_log(
            school_id=school_id, essay_prompt_id=essay_prompt_id
        )
        return [
            PromptAssignmentLogResponse(
                id=log.id, created_at=log.created_at,
                assigned_by_external_identity=log.assigned_by_external_identity,
                target_summary=log.target_summary,
            )
            for log in logs
        ]


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
    # False so na pre-visualizacao de uma proposta da plataforma que esta
    # escola ainda nao adotou: nao existe linha em essay_prompts ainda, entao
    # nada que dependa de um EssayPrompt real (folha de resposta, materiais,
    # dashboard) funciona nela - so o formulario de atribuicao, que e o que
    # dispara a materializacao.
    materialized: bool = True


@essay_prompts_router.get("/{essay_prompt_id}/answer-sheet.pdf")
async def get_essay_prompt_answer_sheet(
    essay_prompt_id: UUID,
    copies: int = Query(1, ge=1, le=60),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> Response:
    """A folha de resposta em branco desta proposta, pronta pra imprimir - uma
    folha por pagina do PDF gerado.

    O teto de 60 copias e o mesmo teto de paginas de um lote: mais folhas do que
    cabem num envio nao teriam pra onde ir.

    Logo nao cadastrada (ou ilegivel) nunca bloqueia a geracao (spec s7) - a
    folha sai com a logo do Nucleo Edu 360 no lugar, em vez de ficar em
    branco (services/essay_answer_sheet.py e o dono dessa regra).
    """
    if not answer_sheet_available():
        raise HTTPException(
            status_code=503, detail="PDF export requires the 'pymupdf' package"
        )
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        prompt = await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        school = await session.get(School, school_id)
        logo_path = school.logo_storage_uri if school is not None else None
        data = render_answer_sheet_pdf(logo_path=logo_path, copies=copies)
        safe_title = "".join(
            c if c.isalnum() or c in " -_" else "_" for c in prompt.title
        ).strip() or "redacao"
        filename = f"folha-de-redacao-{safe_title[:60]}.pdf"
        return Response(
            content=data, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@essay_prompts_router.get("/answer-sheet.pdf")
async def get_generic_answer_sheet(
    copies: int = Query(1, ge=1, le=60),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> Response:
    """A folha de resposta em branco, independente de proposta - a folha e
    generica (nao menciona tema nenhum, services/essay_answer_sheet.py), entao
    nao ha motivo pra exigir que o professor/coordenador escolha uma proposta
    so pra baixar a mesma folha que qualquer outra proposta geraria. Mesma
    autorizacao de get_essay_prompt_answer_sheet (_authorize), so sem o lookup
    de uma proposta especifica.
    """
    if not answer_sheet_available():
        raise HTTPException(
            status_code=503, detail="PDF export requires the 'pymupdf' package"
        )
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        school = await session.get(School, school_id)
        logo_path = school.logo_storage_uri if school is not None else None
        data = render_answer_sheet_pdf(logo_path=logo_path, copies=copies)
        return Response(
            content=data, media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="folha-de-redacao.pdf"'},
        )


@essay_prompts_router.get("", response_model=list[EssayPromptResponse])
async def list_essay_prompts(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EssayPromptResponse]:
    """As propostas da escola do professor, seguidas das propostas da
    plataforma ACTIVE que esta escola ainda nao materializou - na MESMA
    lista, com o campo is_platform como unica diferenca (spec, decisao 2).
    Uma proposta da plataforma ja adotada por esta escola aparece so uma
    vez, como a copia dela (que tambem carrega is_platform=True)."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        result = await session.execute(
            select(EssayPrompt)
            .where(EssayPrompt.school_id == school_id, EssayPrompt.deleted_at.is_(None))
            .order_by(EssayPrompt.created_at.desc())
        )
        items = [
            EssayPromptResponse(
                id=p.id, school_id=p.school_id, title=p.title,
                statement=p.statement, year=p.year, status=p.status,
                is_platform=p.materialized_from_platform_prompt_id is not None,
                platform_prompt_id=p.materialized_from_platform_prompt_id,
            )
            for p in result.scalars().all()
        ]
        available = await PlatformEssayPromptService(session).list_available_for_school(
            school_id=school_id
        )
        # O mesmo ano que materialize_for_school vai gravar em
        # EssayPrompt.year, para o professor nao ver um ano na lista e outro
        # depois de atribuir.
        year = materialization_year()
        items.extend(
            EssayPromptResponse(
                id=origin.id, school_id=school_id, title=origin.title,
                statement=origin.statement, year=year,
                # A copia nasce ACTIVE - e o status que a proposta tera nesta
                # escola. list_available_for_school ja so devolve origens
                # ACTIVE, entao nao ha ARCHIVED para propagar aqui.
                status="ACTIVE",
                is_platform=True, platform_prompt_id=origin.id,
            )
            for origin in available
        )
        return items


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

        # A tela do professor lista proposta propria e proposta da plataforma
        # sob o mesmo campo de id, e so abre o detalhe antes de atribuir - sem
        # este trecho, abrir uma proposta da plataforma daria 403 e o fluxo
        # nunca chegaria ao formulario de atribuicao.
        platform_service = PlatformEssayPromptService(session)
        origin = await platform_service.get_platform_prompt(essay_prompt_id)
        if origin is not None:
            copy = await platform_service.find_materialized(
                platform_essay_prompt_id=origin.id, school_id=school_id
            )
            if copy is None:
                return EssayPromptDetailResponse(
                    id=origin.id, school_id=school_id, title=origin.title,
                    statement=origin.statement, year=materialization_year(),
                    status="ACTIVE", is_platform=True, platform_prompt_id=origin.id,
                    materialized=False, materials=[], assignments=[],
                )
            essay_prompt_id = copy.id

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
            is_platform=prompt.materialized_from_platform_prompt_id is not None,
            platform_prompt_id=prompt.materialized_from_platform_prompt_id,
            materialized=True,
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
                    class_id=a.class_id, student_id=a.student_id,
                    status=a.status, validation_enabled=a.validation_enabled,
                )
                for a in assignments
            ],
        )
