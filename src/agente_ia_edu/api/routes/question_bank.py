"""PHASE 12 - Question Bank read/query API.

READ/QUERY only. No write endpoints. The route maps HTTP query params onto
``QuestionBankService`` (which is AI-agnostic) and serialises the resulting
DTOs. Multi-tenant note: the ENEM/official bank is the institutional/global
bank; per-school scoping for authored questions is enforced by the existing
``/api/v1/questions`` governance route and is the documented integration point
for a future tenant filter here (see PHASE 12 report, section "multi-tenant").
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.schemas.question_bank import (
    QBAssignment,
    QBAssignmentCreateRequest,
    QBAssignmentListResponse,
    QBAssignmentUpdateRequest,
    QBAsset,
    QBClassification,
    QBGeneratedListDefinition,
    QBListGenerateRequest,
    QBListPersistRequest,
    QBListUpdateRequest,
    QBOption,
    QBPagination,
    QBPersistedListDetail,
    QBQuestion,
    QBQuestionListResponse,
    QBQuestionSummary,
    QBSelectionEntry,
    QBSelectionRequest,
    QBSelectionResponse,
    QBStoredListListResponse,
    QBStoredListSummary,
)
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.services.activity_assignment_store import (
    ActivityAssignmentStore,
    AssignmentAuthError,
    AssignmentError,
    AssignmentNotFound,
    AssignmentStateError,
)
from agente_ia_edu.services.list_export import (
    build_render_model,
    pdf_available,
    render_docx,
    render_pdf,
)
from agente_ia_edu.services.list_generator import (
    ListConfiguration,
    ListGenerationError,
    ListGeneratorService,
    config_options,
)
from agente_ia_edu.services.question_list_store import (
    ListAuthorizationError,
    ListNotFoundError,
    ListStateError,
    QuestionListStore,
    Requester,
)
from agente_ia_edu.services.question_bank import (
    QuestionBankFilters,
    QuestionBankItem,
    QuestionBankService,
)

question_bank_router = APIRouter(prefix="/api/v1/question-bank", tags=["question-bank"])


_PREVIEW_CHARS = 180


def _classification_schema(c) -> QBClassification | None:
    if c is None:
        return None
    return QBClassification(
        taxonomy_version=c.taxonomy_version, discipline_code=c.discipline_code,
        area_code=c.area_code, content_code=c.content_code, subcontent_code=c.subcontent_code,
        status=c.status, lifecycle=c.lifecycle, source=c.source, provider_name=c.provider_name,
        model_version=c.model_version, prompt_version=c.prompt_version,
        classification_mode=c.classification_mode, confidence=c.confidence,
        numeric_confidence=c.numeric_confidence, review_reason=c.review_reason,
        closure_phase=c.closure_phase, visual_dependency=c.visual_dependency,
        evidence=c.evidence, context=c.context, created_at=c.created_at,
    )


def _to_summary(item: QuestionBankItem) -> QBQuestionSummary:
    body = (item.statement or item.canonical_text or "").strip()
    preview = body if len(body) <= _PREVIEW_CHARS else body[:_PREVIEW_CHARS].rstrip() + "…"
    return QBQuestionSummary(
        question_id=item.question_id,
        question_version_id=item.question_version_id,
        year=item.year, day=item.day, enem_area=item.enem_area,
        enem_area_label=item.enem_area_label, booklet_code=item.booklet_code,
        official_number=item.official_number, position=item.position,
        statement_preview=preview, recommended_difficulty=item.recommended_difficulty,
        classification=_classification_schema(item.classification),
        classification_state=item.classification_state, is_protected=item.is_protected,
        has_visual_dependency=item.has_visual_dependency,
    )


def _to_schema(item: QuestionBankItem) -> QBQuestion:
    c = item.classification
    return QBQuestion(
        question_id=item.question_id,
        question_version_id=item.question_version_id,
        version_kind=item.version_kind,
        year=item.year,
        day=item.day,
        enem_area=item.enem_area,
        enem_area_label=item.enem_area_label,
        booklet_code=item.booklet_code,
        official_number=item.official_number,
        position=item.position,
        canonical_text=item.canonical_text,
        statement=item.statement,
        recommended_difficulty=item.recommended_difficulty,
        options=[
            QBOption(id=o.id, key=o.key, position=o.position, text=o.text)
            for o in item.options
        ],
        classification=_classification_schema(c),
        classification_state=item.classification_state,
        is_protected=item.is_protected,
        has_visual_dependency=item.has_visual_dependency,
        assets=[QBAsset(kind=a.kind, reference=a.reference, present=a.present) for a in item.assets],
        evidence_uri=item.evidence_uri,
    )


@question_bank_router.get("/questions", response_model=QBQuestionListResponse)
async def list_questions(
    year: int | None = Query(default=None, gt=0),
    day: int | None = Query(default=None, ge=1, le=2),
    area: str | None = Query(default=None, description="LC | CH | CN | MT"),
    discipline: str | None = Query(default=None, description="curriculum-v2 discipline code"),
    area_code: str | None = Query(default=None, description="curriculum-v2 AREA code"),
    content: str | None = Query(default=None, description="curriculum-v2 CONTENT code"),
    subcontent: str | None = Query(default=None, description="curriculum-v2 SUBCONTENT code"),
    official_number: int | None = Query(default=None, gt=0),
    booklet: str | None = Query(default=None),
    text: str | None = Query(default=None, description="Free-text search over the question statement"),
    classification_status: str | None = Query(
        default=None,
        description="UNCLASSIFIED | CLASSIFIED | NEEDS_REVIEW | FORCED_CLOSURE | ANY_CLASSIFIED",
    ),
    classification_source: str | None = Query(default=None, description="rule | ai | human | hybrid"),
    classification_mode: str | None = Query(default=None),
    has_classification: bool | None = Query(default=None),
    provisional_only: bool | None = Query(default=None),
    visual_dependency: bool | None = Query(default=None),
    difficulty: str | None = Query(default=None, description="EASY | MEDIUM | HARD"),
    protected_only: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    order_by: str = Query(default="official_number"),
    order_direction: str = Query(default="asc"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> QBQuestionListResponse:
    filters = QuestionBankFilters(
        year=year, day=day, enem_area=area, discipline_code=discipline, area_code=area_code,
        content_code=content, subcontent_code=subcontent, official_number=official_number,
        booklet_code=booklet, text=text, classification_state=classification_status,
        classification_source=classification_source, classification_mode=classification_mode,
        has_classification=has_classification, provisional_only=provisional_only,
        visual_dependency=visual_dependency, difficulty=difficulty, protected_only=protected_only,
    )
    async with session_factory() as session:
        service = QuestionBankService(session)
        try:
            page_result = await service.list_questions(
                filters, page=page, page_size=page_size,
                order_by=order_by, order_direction=order_direction,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return QBQuestionListResponse(
        items=[_to_summary(item) for item in page_result.items],
        pagination=QBPagination(
            page=page_result.page, page_size=page_result.page_size,
            total=page_result.total, total_pages=page_result.total_pages,
        ),
    )


@question_bank_router.get("/questions/{question_id}", response_model=QBQuestion)
async def get_question(
    question_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> QBQuestion:
    async with session_factory() as session:
        service = QuestionBankService(session)
        item = await service.get_question(question_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return _to_schema(item)


@question_bank_router.post("/selections/preview", response_model=QBSelectionResponse)
async def preview_selection(
    payload: QBSelectionRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> QBSelectionResponse:
    """Validate + order a list of official ``question_version_id`` values for the
    future list generator. Pure read: nothing is persisted."""
    async with session_factory() as session:
        service = QuestionBankService(session)
        try:
            selection = await service.build_selection(
                payload.question_version_ids, source=payload.source
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return QBSelectionResponse(
        entries=[
            QBSelectionEntry(
                position=e.position, question_version_id=e.question_version_id,
                question_id=e.question_id, year=e.year, official_number=e.official_number,
                enem_area=e.enem_area, content_code=e.content_code,
            )
            for e in selection.entries
        ],
        source=selection.source,
        count=len(selection.entries),
    )


# ---------------------------------------------------------------------------
# PHASE 14 - list generator (deterministic, read-only, in-memory result)
# ---------------------------------------------------------------------------


@question_bank_router.get("/lists/config-options")
async def list_config_options(
    identity: ExternalIdentityContext = Depends(get_current_identity),
) -> dict:
    """Vocabulary for the professor's list-configuration screen (activity modes,
    answer-key presentations, resolution styles + availability)."""
    return config_options()


@question_bank_router.post("/lists/generate", response_model=QBGeneratedListDefinition)
async def generate_list(
    payload: QBListGenerateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> QBGeneratedListDefinition:
    """Turn a validated selection + configuration into a GeneratedListDefinition.

    Read-only: nothing is persisted. Answer-key data (when requested) is the
    authoritative official key; official step-by-step resolutions do not exist in
    the schema and are returned as unavailable. No LLM is involved.
    """
    configuration = ListConfiguration(
        title=payload.title,
        instructions=payload.instructions,
        activity_mode=payload.activity_mode,
        answer_key_presentation=payload.answer_key_presentation,
        resolution_style=payload.resolution_style,
    )
    async with session_factory() as session:
        service = ListGeneratorService(session)
        try:
            definition = await service.generate(payload.question_version_ids, configuration)
        except ListGenerationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return QBGeneratedListDefinition.model_validate(_definition_to_dict(definition))


def _definition_to_dict(definition) -> dict:
    from dataclasses import asdict

    return asdict(definition)


# ---------------------------------------------------------------------------
# PHASE 15 - list persistence, history & export
# ---------------------------------------------------------------------------


def _requester(ctx: AuthenticatedUserContext) -> Requester:
    return Requester(
        external_user_id=ctx.external_identity_id or ctx.user_id,
        school_id=ctx.school_id,
        role=ctx.role,
        is_platform_admin=bool(getattr(ctx, "is_platform_admin", False)),
    )


def _map_store_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ListNotFoundError):
        return HTTPException(status_code=404, detail="List not found")
    if isinstance(exc, ListAuthorizationError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ListStateError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ListGenerationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _summary_schema(s) -> QBStoredListSummary:
    return QBStoredListSummary(**s.__dict__)


@question_bank_router.post("/lists", response_model=QBStoredListSummary, status_code=201)
async def persist_list(
    payload: QBListPersistRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> QBStoredListSummary:
    config = ListConfiguration(
        title=payload.title, instructions=payload.instructions,
        activity_mode=payload.activity_mode,
        answer_key_presentation=payload.answer_key_presentation,
        resolution_style=payload.resolution_style,
    )
    async with session_factory() as session:
        store = QuestionListStore(session)
        try:
            summary = await store.create(
                configuration=config, question_version_ids=payload.question_version_ids,
                requester=_requester(ctx),
            )
        except (ListGenerationError, ValueError) as exc:
            raise _map_store_error(exc) from exc
    return _summary_schema(summary)


@question_bank_router.get("/lists", response_model=QBStoredListListResponse)
async def my_lists(
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    order_by: str = Query(default="created_at"),
    order_direction: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> QBStoredListListResponse:
    async with session_factory() as session:
        store = QuestionListStore(session)
        rows, total = await store.list_for_scope(
            requester=_requester(ctx), status=status, query=q,
            order_by=order_by, order_direction=order_direction,
            page=page, page_size=page_size,
        )
    return QBStoredListListResponse(
        items=[_summary_schema(r) for r in rows],
        pagination=QBPagination(page=page, page_size=page_size, total=total,
                                total_pages=(total + page_size - 1) // page_size),
    )


@question_bank_router.get("/lists/{list_id}", response_model=QBPersistedListDetail)
async def get_list(
    list_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> QBPersistedListDetail:
    async with session_factory() as session:
        store = QuestionListStore(session)
        try:
            payload = await store.get_definition(list_id, requester=_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_store_error(exc) from exc
    return QBPersistedListDetail.model_validate(payload)


@question_bank_router.patch("/lists/{list_id}", response_model=QBStoredListSummary)
async def update_list(
    list_id: UUID,
    payload: QBListUpdateRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> QBStoredListSummary:
    async with session_factory() as session:
        store = QuestionListStore(session)
        try:
            current = await store.get_definition(list_id, requester=_requester(ctx))
            cur_cfg = current["configuration"]
            config = ListConfiguration(
                title=payload.title if payload.title is not None else cur_cfg["title"],
                instructions=(payload.instructions if payload.instructions is not None
                              else cur_cfg["instructions"]),
                activity_mode=payload.activity_mode or cur_cfg["activity_mode"],
                answer_key_presentation=(payload.answer_key_presentation
                                         or cur_cfg["answer_key_presentation"]),
                resolution_style=(payload.resolution_style
                                  if payload.resolution_style is not None
                                  else cur_cfg["resolution_style"]),
            )
            summary = await store.update_draft(
                list_id, requester=_requester(ctx), configuration=config,
                question_version_ids=payload.question_version_ids,
            )
        except Exception as exc:  # noqa: BLE001
            raise _map_store_error(exc) from exc
    return _summary_schema(summary)


@question_bank_router.delete("/lists/{list_id}", status_code=204)
async def delete_list(
    list_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> Response:
    async with session_factory() as session:
        store = QuestionListStore(session)
        try:
            await store.delete(list_id, requester=_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_store_error(exc) from exc
    return Response(status_code=204)


@question_bank_router.post("/lists/{list_id}/finalize", response_model=QBStoredListSummary)
async def finalize_list(
    list_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> QBStoredListSummary:
    async with session_factory() as session:
        store = QuestionListStore(session)
        try:
            summary = await store.finalize(list_id, requester=_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_store_error(exc) from exc
    return _summary_schema(summary)


async def _export_model(session_factory, list_id, ctx) -> tuple[dict, str]:
    async with session_factory() as session:
        store = QuestionListStore(session)
        try:
            payload = await store.get_definition(list_id, requester=_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_store_error(exc) from exc
    return build_render_model(payload), payload["configuration"]["title"]


# ---------------------------------------------------------------------------
# PHASE 16 - activity distribution (professor / coordination)
# ---------------------------------------------------------------------------


def _map_assignment_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (ListNotFoundError, AssignmentNotFound)):
        return HTTPException(status_code=404, detail="Not found")
    if isinstance(exc, (ListAuthorizationError, AssignmentAuthError)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (ListStateError, AssignmentStateError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (AssignmentError, ListGenerationError, ValueError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _assignment_schema(v) -> QBAssignment:
    return QBAssignment(**v.__dict__)


@question_bank_router.post("/lists/{list_id}/assignments", response_model=QBAssignment, status_code=201)
async def create_assignment(
    list_id: UUID,
    payload: QBAssignmentCreateRequest,
    response: Response,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> QBAssignment:
    async with session_factory() as session:
        store = ActivityAssignmentStore(session)
        try:
            view, existed = await store.create(
                list_id, requester=_requester(ctx),
                target_type=payload.target_type, target_id=payload.target_id,
                available_from=payload.available_from, due_at=payload.due_at,
                academic_year=payload.academic_year,
            )
        except Exception as exc:  # noqa: BLE001
            raise _map_assignment_error(exc) from exc
    if existed:  # idempotent: an equivalent ACTIVE distribution already existed
        response.status_code = 200
        response.headers["X-Idempotent-Replay"] = "true"
    return _assignment_schema(view)


@question_bank_router.get("/lists/{list_id}/assignments", response_model=QBAssignmentListResponse)
async def list_assignments(
    list_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> QBAssignmentListResponse:
    async with session_factory() as session:
        store = ActivityAssignmentStore(session)
        try:
            items = await store.list_for_list(list_id, requester=_requester(ctx))
            summary = await store.distribution_summary(list_id, requester=_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_assignment_error(exc) from exc
    return QBAssignmentListResponse(items=[_assignment_schema(i) for i in items], summary=summary)


@question_bank_router.get("/assignments/{assignment_id}", response_model=QBAssignment)
async def get_assignment(
    assignment_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> QBAssignment:
    async with session_factory() as session:
        store = ActivityAssignmentStore(session)
        try:
            view = await store.get(assignment_id, requester=_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_assignment_error(exc) from exc
    return _assignment_schema(view)


@question_bank_router.patch("/assignments/{assignment_id}", response_model=QBAssignment)
async def update_assignment(
    assignment_id: UUID,
    payload: QBAssignmentUpdateRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> QBAssignment:
    kw: dict = {}
    if payload.clear_available_from:
        kw["available_from"] = None
    elif payload.available_from is not None:
        kw["available_from"] = payload.available_from
    if payload.clear_due_at:
        kw["due_at"] = None
    elif payload.due_at is not None:
        kw["due_at"] = payload.due_at
    async with session_factory() as session:
        store = ActivityAssignmentStore(session)
        try:
            view = await store.update(assignment_id, requester=_requester(ctx),
                                      status=payload.status, **kw)
        except Exception as exc:  # noqa: BLE001
            raise _map_assignment_error(exc) from exc
    return _assignment_schema(view)


@question_bank_router.delete("/assignments/{assignment_id}", response_model=QBAssignment)
async def cancel_assignment(
    assignment_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> QBAssignment:
    async with session_factory() as session:
        store = ActivityAssignmentStore(session)
        try:
            view = await store.cancel(assignment_id, requester=_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_assignment_error(exc) from exc
    return _assignment_schema(view)


# ---------------------------------------------------------------------------
# PHASE 19 - manager pedagogical analysis of a distributed activity's results
# (READ-ONLY; reuses the PHASE 16 assignment view authorisation - no new authz)
# ---------------------------------------------------------------------------

from agente_ia_edu.services.pedagogical_analysis import (  # noqa: E402
    AnalysisAuthError as _AnalysisAuthError,
    AnalysisNotFound as _AnalysisNotFound,
    PedagogicalAnalysisService as _PedagogicalAnalysisService,
)


@question_bank_router.get("/assignments/{assignment_id}/results/analysis")
async def assignment_results_analysis(
    assignment_id: UUID,
    student_external_id: str | None = None,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    """One deterministic pedagogical analysis per corrected result under this
    assignment (optionally filtered to one student), plus a class aggregate.
    The caller must be able to view the assignment (owner / platform admin /
    same-school privileged) - the exact PHASE 16 rule, reused verbatim."""
    async with session_factory() as session:
        svc = _PedagogicalAnalysisService(session)
        try:
            return await svc.analyze_for_manager(
                assignment_id, requester=_requester(ctx),
                student_external_id=student_external_id)
        except _AnalysisNotFound as exc:
            raise HTTPException(status_code=404, detail="Assignment or result not found") from exc
        except _AnalysisAuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# PHASE 20 - manager view of a student's curriculum Domain Map (assignment-scoped;
# reuses the PHASE 16 assignment-view authorisation - no new authz rule)
# ---------------------------------------------------------------------------

from agente_ia_edu.services.curriculum_domain_map import (  # noqa: E402
    CurriculumDomainMapService as _CurriculumDomainMapService,
    DomainMapAuthError as _DomainMapAuthError,
    DomainMapNotFound as _DomainMapNotFound,
)


@question_bank_router.get("/assignments/{assignment_id}/students/domain-map")
async def assignment_students_domain_map(
    assignment_id: UUID,
    student_external_id: str | None = None,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    """The curriculum-v2 Domain Map of every student with a corrected result under
    this assignment (optionally one student). The caller must be able to view the
    assignment (owner / platform admin / same-school privileged - the exact
    PHASE 16 rule). No new authorisation rule is introduced."""
    async with session_factory() as session:
        svc = _CurriculumDomainMapService(session)
        try:
            return await svc.manager_view(assignment_id, requester=_requester(ctx),
                                          student_external_id=student_external_id)
        except _DomainMapNotFound as exc:
            raise HTTPException(status_code=404, detail="Assignment not found") from exc
        except _DomainMapAuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


def _filename(title: str, ext: str) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip() or "lista"
    return f"{safe[:60]}.{ext}"


@question_bank_router.get("/lists/{list_id}/export.pdf")
async def export_list_pdf(
    list_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> Response:
    if not pdf_available():
        raise HTTPException(status_code=503, detail="PDF export requires the 'pymupdf' package")
    model, title = await _export_model(session_factory, list_id, ctx)
    data = render_pdf(model)
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{_filename(title, "pdf")}"'})


@question_bank_router.get("/lists/{list_id}/export.docx")
async def export_list_docx(
    list_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> Response:
    model, title = await _export_model(session_factory, list_id, ctx)
    data = render_docx(model)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{_filename(title, "docx")}"'},
    )


__all__ = ["question_bank_router"]


# ---------------------------------------------------------------------------
# PHASE 21 - manager view of a student's Adaptive Learning Path (assignment-scoped;
# reuses the PHASE 16 assignment-view authorisation - no new authz rule)
# ---------------------------------------------------------------------------

from agente_ia_edu.services.adaptive_learning_path import (  # noqa: E402
    AdaptiveLearningPathService as _AdaptiveLearningPathService,
    LearningPathAuthError as _LearningPathAuthError,
    LearningPathNotFound as _LearningPathNotFound,
    PrerequisiteGraphInvalid as _PrerequisiteGraphInvalid,
)


@question_bank_router.get("/assignments/{assignment_id}/students/study-path")
async def assignment_students_study_path(
    assignment_id: UUID,
    student_external_id: str | None = None,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    """The Adaptive Learning Path of every student with a corrected result under
    this assignment (optionally one student). Caller must be able to view the
    assignment (owner / platform admin / same-school privileged - the exact
    PHASE 16 rule). No new authorisation rule; no automatic intervention."""
    async with session_factory() as session:
        svc = _AdaptiveLearningPathService(session)
        try:
            return await svc.manager_view(assignment_id, requester=_requester(ctx),
                                          student_external_id=student_external_id)
        except _LearningPathNotFound as exc:
            raise HTTPException(status_code=404, detail="Assignment not found") from exc
        except _LearningPathAuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except _PrerequisiteGraphInvalid as exc:
            raise HTTPException(status_code=409, detail={
                "message": str(exc), "state": "PREREQUISITE_GRAPH_INVALID",
                "cycle": getattr(exc, "cycle", [])}) from exc
