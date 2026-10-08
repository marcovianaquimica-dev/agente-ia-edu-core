"""
API routes for Student Dashboard and Student Experience (Phase 11).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from ..dependencies import get_current_identity, get_session_factory
from ..schemas.student import (
    StudentDashboardResponse,
    StudentEvolutionResponse,
    StudentLearningPathResponse,
    StudentModulesResponse,
    StudySearchResponse,
)
from ...identity import ExternalIdentityContext
from ...services.authorization import AuthorizationService
from ...services.knowledge import KnowledgeService
from ...services.recommendation import RecommendationEngine
from ...services.student_dashboard import StudentDashboardService
from ...services.study_search import StudySearchService
from ...services.video_engine import VideoRecommendationEngine

student_router = APIRouter(
    prefix="/api/v1/student",
    tags=["student-experience"],
)


@student_router.get(
    "/dashboard",
    response_model=StudentDashboardResponse,
    summary="Get student dashboard overview",
    description="Returns aggregated student stats, active recommendation, action plan, and period filters.",
)
async def get_student_dashboard(
    time_period: str = Query("academic_year", description="academic_year, last_30_days, bimester, semester"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentDashboardResponse:
    student_id = identity.external_user_id
    institution_id = identity.institution_id
    classroom_id = identity.classroom_id

    async with session_factory() as session:
        knowledge_service = KnowledgeService(session)
        recommendation_engine = RecommendationEngine(session, knowledge_service)
        video_engine = VideoRecommendationEngine(session, knowledge_service)

        dashboard_service = StudentDashboardService(
            session=session,
            knowledge_service=knowledge_service,
            recommendation_engine=recommendation_engine,
            video_engine=video_engine,
        )

        res = await dashboard_service.get_dashboard(
            student_id=student_id,
            institution_id=institution_id,
            classroom_id=classroom_id,
            time_period=time_period,
        )
        return StudentDashboardResponse(**res)


@student_router.get(
    "/evolution",
    response_model=StudentEvolutionResponse,
    summary="Get student evolution timeline and analytics",
)
async def get_student_evolution(
    time_period: str = Query("academic_year", description="academic_year, last_30_days, bimester, semester"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentEvolutionResponse:
    student_id = identity.external_user_id

    async with session_factory() as session:
        knowledge_service = KnowledgeService(session)
        recommendation_engine = RecommendationEngine(session, knowledge_service)
        video_engine = VideoRecommendationEngine(session, knowledge_service)

        dashboard_service = StudentDashboardService(
            session=session,
            knowledge_service=knowledge_service,
            recommendation_engine=recommendation_engine,
            video_engine=video_engine,
        )

        res = await dashboard_service.get_evolution(
            student_id=student_id,
            time_period=time_period,
        )
        return StudentEvolutionResponse(**res)


@student_router.get(
    "/learning-path",
    response_model=StudentLearningPathResponse,
    summary="Get active step-by-step student learning path",
)
async def get_student_learning_path(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentLearningPathResponse:
    student_id = identity.external_user_id
    institution_id = identity.institution_id
    classroom_id = identity.classroom_id

    async with session_factory() as session:
        knowledge_service = KnowledgeService(session)
        recommendation_engine = RecommendationEngine(session, knowledge_service)
        video_engine = VideoRecommendationEngine(session, knowledge_service)

        dashboard_service = StudentDashboardService(
            session=session,
            knowledge_service=knowledge_service,
            recommendation_engine=recommendation_engine,
            video_engine=video_engine,
        )

        res = await dashboard_service.get_learning_path(
            student_id=student_id,
            institution_id=institution_id,
            classroom_id=classroom_id,
        )
        return StudentLearningPathResponse(**res)


@student_router.get(
    "/search",
    response_model=StudySearchResponse,
    summary="Generic student content discovery by natural-language query",
    description="Parses the query into a structured intent/context and resolves the closest questions and materials through the knowledge layer.",
)
async def search_student_content(
    q: str = Query(..., description="Free-form student search request"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    resource_type: str | None = Query(None, description="QUESTION, MATERIAL, VIDEO, or ALL"),
    difficulty: str | None = Query(None),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudySearchResponse:
    async with session_factory() as session:
        scope_type = identity.metadata.get("scope_type") if isinstance(identity.metadata, dict) else None
        if scope_type is None:
            scope_type = "CLASSROOM" if identity.classroom_id else "SCHOOL" if identity.institution_id else "PLATFORM"

        scope_identifiers = [
            identity.student_id,
            identity.classroom_id,
            identity.metadata.get("school_code") if isinstance(identity.metadata, dict) else None,
            identity.metadata.get("institution_code") if isinstance(identity.metadata, dict) else None,
            identity.institution_id,
        ]
        requester_scope_external_id = tuple(value for value in scope_identifiers if value)
        if len(requester_scope_external_id) == 1:
            requester_scope_external_id = requester_scope_external_id[0]

        payload = await StudySearchService.search(
            q,
            session=session,
            difficulty=difficulty,
            resource_type=resource_type,
            page=page,
            limit=limit,
            institution_id=identity.institution_id,
            requester_scope_type=scope_type,
            requester_scope_external_id=requester_scope_external_id,
        )
        return StudySearchResponse(**payload)


@student_router.get(
    "/modules",
    response_model=StudentModulesResponse,
    summary="Get which platform modules are enabled for the student's own school",
)
async def get_student_modules(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentModulesResponse:
    async with session_factory() as session:
        authz = AuthorizationService(session)
        context = await authz.resolve_context(identity)
        enabled = set(context.modules)
        return StudentModulesResponse(
            AGENTE_IA_EDU="AGENTE_IA_EDU" in enabled,
            REDACAO_IA="REDACAO_IA" in enabled,
        )


# ---------------------------------------------------------------------------
# PHASE 16 - activity visibility (no execution)
# ---------------------------------------------------------------------------

from uuid import UUID as _UUID  # noqa: E402

from ..dependencies import get_current_authenticated_context  # noqa: E402
from ..schemas.question_bank import (  # noqa: E402
    QBStudentActivity,
    QBStudentActivityListResponse,
)
from ...identity import AuthenticatedUserContext  # noqa: E402
from ...services.activity_assignment_store import (  # noqa: E402
    ActivityAssignmentStore,
    AssignmentAuthError,
    AssignmentNotFound,
)
from ...services.question_list_store import Requester as _Requester  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def _student_requester(ctx: AuthenticatedUserContext) -> _Requester:
    return _Requester(
        external_user_id=ctx.external_identity_id or ctx.user_id,
        school_id=ctx.school_id, role=ctx.role,
        is_platform_admin=bool(getattr(ctx, "is_platform_admin", False)),
    )


@student_router.get("/activities", response_model=QBStudentActivityListResponse,
                    summary="List activities assigned to the current student (visibility only)")
async def list_student_activities(
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> QBStudentActivityListResponse:
    from ...services.activity_player_store import ActivityPlayerStore
    from ...services.proximo_passo import PASSO_ATIVIDADE, cta_para

    requester = _student_requester(ctx)
    async with session_factory() as session:
        rows = await ActivityAssignmentStore(session).student_activities(
            requester=requester)
        # O estado de cada atividade, numa consulta so - nao uma por linha.
        # Antes a tela buscava isso item a item em `/attempt`, N+1 na rede, e
        # ainda escrevia o rotulo do botao no JavaScript, com uma tabela
        # paralela que ja divergia da matriz ("Abrir" onde a matriz diz
        # "Comecar atividade"). O rotulo sai da matriz, aqui.
        estados = await ActivityPlayerStore(session).statuses_for(
            [_UUID(r["assignment_id"]) for r in rows], requester=requester)
    for r in rows:
        r["state"] = estados.get(r["assignment_id"], "NOT_STARTED")
        r["cta"] = cta_para(PASSO_ATIVIDADE, r["state"]) or ""
    return QBStudentActivityListResponse(items=[QBStudentActivity(**r) for r in rows])


@student_router.get("/activities/{assignment_id}",
                    summary="Activity entry screen (no attempt is created)")
async def get_student_activity(
    assignment_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        store = ActivityAssignmentStore(session)
        try:
            return await store.student_activity_detail(assignment_id, requester=_student_requester(ctx))
        except (AssignmentNotFound, AssignmentAuthError) as exc:
            raise HTTPException(
                status_code=404, detail=RECURSO_PRIVADO_NAO_ENCONTRADO) from exc


# ---------------------------------------------------------------------------
# PHASE 17 - student activity PLAYER (execution state; no correction/score)
# ---------------------------------------------------------------------------

from ..schemas.student import (  # noqa: E402
    ActivityAnswerSaveRequest,
    ActivityAnswerSaveResponse,
    ActivityPlayerState,
)
from ...services.activity_player_store import (  # noqa: E402
    ActivityPlayerStore,
    PlayerAuthError,
    PlayerError,
    PlayerNotFound,
    PlayerStateError,
)


# ============================== ANTI-ENUMERACAO (rotas do ALUNO) ===========
# Um recurso inexistente respondia 404; um recurso real de outro aluno
# respondia 403 com "this activity is not assigned to you". A diferenca entre
# as duas respostas e um oraculo: quem varre UUIDs aprende quais existem.
#
# Nas rotas DO ALUNO o recurso e privado por definicao - ele nunca tem motivo
# legitimo para saber que existe uma atividade que nao e dele. Entao "nao
# existe" e "nao e seu" respondem IGUAL, com o mesmo corpo.
#
# O QUE NAO MUDA, DE PROPOSITO:
#   - as rotas de GESTAO (professor, coordenacao) continuam 403. La o
#     requester pode listar as distribuicoes da escola, entao esconder a
#     existencia nao protege nada e esconderia um erro de permissao de quem
#     precisa corrigi-lo;
#   - `AssignmentAuthError` continua sendo o que era. Mexer na excecao mudaria
#     o contrato de pratica, diagnostico e gestao de uma vez; o que muda aqui
#     e so como a camada HTTP DO ALUNO a traduz.
RECURSO_PRIVADO_NAO_ENCONTRADO = "Activity not found"


def _map_player_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PlayerNotFound):
        return HTTPException(status_code=404, detail="Activity not found")
    if isinstance(exc, PlayerAuthError):
        # Mesma resposta de PlayerNotFound, acima: indistinguivel de proposito.
        return HTTPException(status_code=404,
                             detail=RECURSO_PRIVADO_NAO_ENCONTRADO)
    if isinstance(exc, PlayerStateError):
        detail = {"message": str(exc), **(getattr(exc, "payload", {}) or {})}
        return HTTPException(status_code=409, detail=detail)
    if isinstance(exc, (PlayerError, ValueError)):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc  # pragma: no cover


@student_router.post("/activities/{assignment_id}/attempt", response_model=ActivityPlayerState,
                     summary="Start (or resume) the current student's attempt - idempotent")
async def start_activity_attempt(
    assignment_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ActivityPlayerState:
    async with session_factory() as session:
        store = ActivityPlayerStore(session)
        try:
            return ActivityPlayerState(**await store.start(assignment_id, requester=_student_requester(ctx)))
        except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
            raise _map_player_error(exc) from exc


@student_router.get("/activities/{assignment_id}/attempt", response_model=ActivityPlayerState,
                    summary="Current attempt state: questions in frozen order + answer flags (no key)")
async def get_activity_attempt(
    assignment_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ActivityPlayerState:
    async with session_factory() as session:
        store = ActivityPlayerStore(session)
        try:
            return ActivityPlayerState(**await store.get_state(assignment_id, requester=_student_requester(ctx)))
        except Exception as exc:  # noqa: BLE001
            raise _map_player_error(exc) from exc


@student_router.put("/activities/{assignment_id}/attempt/answers/{question_version_id}",
                    response_model=ActivityAnswerSaveResponse,
                    summary="Autosave the current choice for one question - idempotent, no duplicates")
async def save_activity_answer(
    assignment_id: _UUID,
    question_version_id: _UUID,
    payload: ActivityAnswerSaveRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ActivityAnswerSaveResponse:
    async with session_factory() as session:
        store = ActivityPlayerStore(session)
        try:
            data = await store.save_answer(
                assignment_id, question_version_id,
                requester=_student_requester(ctx), selected_option=payload.selected_option)
            return ActivityAnswerSaveResponse(**data)
        except Exception as exc:  # noqa: BLE001
            raise _map_player_error(exc) from exc


@student_router.put("/activities/{assignment_id}/attempt/position",
                    summary="Persist the last viewed question index (resume aid)")
async def set_activity_position(
    assignment_id: _UUID,
    position: int,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        store = ActivityPlayerStore(session)
        try:
            return await store.set_current_position(
                assignment_id, position, requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_player_error(exc) from exc


@student_router.post("/activities/{assignment_id}/attempt/complete", response_model=ActivityPlayerState,
                     summary="Finalise the attempt - the BACKEND validates completeness")
async def complete_activity_attempt(
    assignment_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ActivityPlayerState:
    async with session_factory() as session:
        store = ActivityPlayerStore(session)
        try:
            return ActivityPlayerState(**await store.complete(assignment_id, requester=_student_requester(ctx)))
        except Exception as exc:  # noqa: BLE001
            raise _map_player_error(exc) from exc


# ---------------------------------------------------------------------------
# PHASE 18 - deterministic correction + student result (post-completion only)
# ---------------------------------------------------------------------------

from ..schemas.student import ActivityResultView  # noqa: E402
from ...services.activity_correction_store import (  # noqa: E402
    ActivityCorrectionStore,
    CorrectionAuthError,
    CorrectionError,
    CorrectionNotFound,
    CorrectionSnapshotError,
    CorrectionStateError,
)


def _map_correction_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (CorrectionNotFound, CorrectionAuthError)):
        return HTTPException(status_code=404,
                             detail=RECURSO_PRIVADO_NAO_ENCONTRADO)
    if isinstance(exc, CorrectionSnapshotError):
        return HTTPException(status_code=409, detail={"message": str(exc), "reason": "snapshot_inconsistent",
                                                      **(getattr(exc, "payload", {}) or {})})
    if isinstance(exc, CorrectionStateError):
        return HTTPException(status_code=409, detail={"message": str(exc),
                                                     **(getattr(exc, "payload", {}) or {})})
    if isinstance(exc, (CorrectionError, ValueError)):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc  # pragma: no cover


@student_router.post("/activities/{assignment_id}/attempt/correct", response_model=ActivityResultView,
                     summary="Deterministically correct the COMPLETED attempt - idempotent")
async def correct_activity_attempt(
    assignment_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ActivityResultView:
    async with session_factory() as session:
        store = ActivityCorrectionStore(session)
        try:
            return ActivityResultView(**await store.correct(assignment_id, requester=_student_requester(ctx)))
        except Exception as exc:  # noqa: BLE001
            raise _map_correction_error(exc) from exc


@student_router.get("/activities/{assignment_id}/attempt/result", response_model=ActivityResultView,
                    summary="The student's own corrected result (answer key visible here only)")
async def get_activity_result(
    assignment_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ActivityResultView:
    async with session_factory() as session:
        store = ActivityCorrectionStore(session)
        try:
            return ActivityResultView(**await store.get_result(assignment_id, requester=_student_requester(ctx)))
        except Exception as exc:  # noqa: BLE001
            raise _map_correction_error(exc) from exc


# ---------------------------------------------------------------------------
# ENTENDER O ERRO - a intervenção que faltava entre errar e tentar de novo
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _ModeloDeEntrada  # noqa: E402

from ...services.explicacao_do_erro import (  # noqa: E402
    ExplicacaoDoErro,
    proxima_estrategia,
)


class _ExplicacaoRequest(_ModeloDeEntrada):
    question_version_id: _UUID
    # A ABORDAGEM ANTERIOR, quando o aluno pede OUTRO JEITO. Não é a tela
    # escolhendo a estratégia: ela diz qual já mostrou, e `proxima_estrategia`
    # - do domínio - decide a seguinte. Sem isto, "explique de outro jeito"
    # devolveria o mesmo texto, que é a definição do problema.
    previous_strategy: str | None = None


@student_router.post("/activities/{assignment_id}/attempt/result/explanation",
                     summary="Explain ONE wrong answer of the caller's own "
                             "corrected result (curated > AI > fallback)")
async def explain_activity_result_error(
    assignment_id: _UUID,
    payload: _ExplicacaoRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    """Uma explicação não é evidência, e esta rota não tem como torná-la uma.

    Ela lê (contexto da questão já corrigida) e devolve texto. `ExplicacaoDoErro`
    não recebe sessão: não há caminho daqui para `domain_content_mastery`.
    """
    async with session_factory() as session:
        store = ActivityCorrectionStore(session)
        try:
            contexto = await store.contexto_do_erro(
                assignment_id, payload.question_version_id,
                requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_correction_error(exc) from exc

    estrategia = proxima_estrategia(payload.previous_strategy)
    return await ExplicacaoDoErro().explicar(
        resolucao_curada=contexto.pop("resolucao_curada", None),
        contexto=contexto, estrategia=estrategia)


# ---------------------------------------------------------------------------
# PHASE 19 - pedagogical analysis (READ-ONLY aggregation over the PHASE 18 result)
# ---------------------------------------------------------------------------

from ...services.pedagogical_analysis import (  # noqa: E402
    AnalysisAuthError,
    AnalysisError,
    AnalysisNotFound,
    PedagogicalAnalysisService,
)


def _map_analysis_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (AnalysisNotFound, AnalysisAuthError)):
        return HTTPException(status_code=404,
                             detail=RECURSO_PRIVADO_NAO_ENCONTRADO)
    if isinstance(exc, (AnalysisError, ValueError)):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc  # pragma: no cover


@student_router.get("/activities/{assignment_id}/attempt/result/analysis",
                    summary="Deterministic pedagogical analysis of the student's own corrected result")
async def get_activity_result_analysis(
    assignment_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        svc = PedagogicalAnalysisService(session)
        try:
            return await svc.analyze_for_student(assignment_id, requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_analysis_error(exc) from exc


# ---------------------------------------------------------------------------
# PHASE 20 - curriculum-v2 Domain Map (persistent, DERIVED from ActivityResult)
# ---------------------------------------------------------------------------

from ...services.curriculum_domain_map import (  # noqa: E402
    CurriculumDomainMapService,
    DomainMapAuthError,
    DomainMapError,
    DomainMapNotFound,
)


def _map_domain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DomainMapNotFound):
        return HTTPException(status_code=404, detail="Domain map entry not found")
    if isinstance(exc, DomainMapAuthError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (DomainMapError, ValueError)):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc  # pragma: no cover


def _me(ctx: AuthenticatedUserContext) -> str:
    return _student_requester(ctx).external_user_id


@student_router.get("/domain", summary="The student's own curriculum-v2 Domain Map (derived, cached)")
async def get_curriculum_domain(
    since: str | None = None,
    until: str | None = None,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        svc = CurriculumDomainMapService(session)
        try:
            return await svc.get_map(_me(ctx), requester=_student_requester(ctx),
                                     since=since, until=until)
        except Exception as exc:  # noqa: BLE001
            raise _map_domain_error(exc) from exc


@student_router.get("/progress",
                    summary="Meu Progresso - the student's own domain map, in three plain bands")
async def get_student_progress(
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    """The same Domain Map as ``/domain``, translated for the student.

    ``/domain`` is the engine's view: accuracy, sample size, evidence state,
    origin breakdown, curriculum codes. Useful for the teacher, wrong for a
    15-year-old - a number next to his name invites him to read it as a grade.

    This returns only band names and content names. No cut-off is decided
    here: the bands come from PerformanceThresholdPolicy, the single source of
    truth the whole engine already uses. See services/student_progress.py.
    """
    from ...services.student_progress import panorama_do_aluno  # noqa: PLC0415

    async with session_factory() as session:
        svc = CurriculumDomainMapService(session)
        try:
            mapa = await svc.get_map(_me(ctx), requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_domain_error(exc) from exc
    return panorama_do_aluno(mapa)


@student_router.post("/domain/rebuild",
                     summary="Recompute the caller's own Domain Map from the ActivityResult history (idempotent)")
async def rebuild_curriculum_domain(
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        svc = CurriculumDomainMapService(session)
        try:
            return await svc.rebuild_student(_me(ctx), requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_domain_error(exc) from exc


@student_router.get("/domain/content/{content_code}",
                    summary="One curriculum content's domain detail for the caller")
async def get_curriculum_domain_content(
    content_code: str,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        svc = CurriculumDomainMapService(session)
        try:
            return await svc.get_content(_me(ctx), content_code, requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_domain_error(exc) from exc


@student_router.get("/domain/discipline/{discipline_code}",
                    summary="One discipline's domain rollup for the caller")
async def get_curriculum_domain_discipline(
    discipline_code: str,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        svc = CurriculumDomainMapService(session)
        try:
            return await svc.get_discipline(_me(ctx), discipline_code,
                                            requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_domain_error(exc) from exc


@student_router.get("/domain/evidence/{content_code}",
                    summary="The individual observations behind one content (traceable to result items)")
async def get_curriculum_domain_evidence(
    content_code: str,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        svc = CurriculumDomainMapService(session)
        try:
            return await svc.get_evidence(_me(ctx), content_code, requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_domain_error(exc) from exc


# ---------------------------------------------------------------------------
# PHASE 21 - Adaptive Learning Path (DERIVED decision layer; writes nothing).
# NOTE: /learning-path is owned by the LEGACY taxonomy/diagnostic pipeline; the
# new activity/domain-map-based path is exposed under /study-path.
# ---------------------------------------------------------------------------

from ...services.adaptive_learning_path import (  # noqa: E402
    AdaptiveLearningPathService,
    LearningPathAuthError,
    LearningPathError,
    LearningPathNotFound,
    PrerequisiteGraphInvalid,
)


def _map_path_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LearningPathNotFound):
        return HTTPException(status_code=404, detail="Learning path entry not found")
    if isinstance(exc, LearningPathAuthError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, PrerequisiteGraphInvalid):
        return HTTPException(status_code=409, detail={"message": str(exc),
                                                     "state": "PREREQUISITE_GRAPH_INVALID",
                                                     "cycle": getattr(exc, "cycle", [])})
    if isinstance(exc, (LearningPathError, ValueError)):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc  # pragma: no cover


@student_router.get("/study-path",
                    summary="The caller's own Adaptive Learning Path (derived from the Domain Map)")
async def get_student_study_path(
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        svc = AdaptiveLearningPathService(session)
        try:
            return await svc.build_path(_me(ctx), requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_path_error(exc) from exc


@student_router.get("/study-path/next",
                    summary="The caller's top next pedagogical actions")
async def get_student_study_path_next(
    limit: int = 3,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        svc = AdaptiveLearningPathService(session)
        try:
            return await svc.get_next_actions(_me(ctx), requester=_student_requester(ctx), limit=limit)
        except Exception as exc:  # noqa: BLE001
            raise _map_path_error(exc) from exc


@student_router.get("/study-path/content/{content_code}",
                    summary="One content's recommendation + explanation for the caller")
async def get_student_study_path_content(
    content_code: str,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        svc = AdaptiveLearningPathService(session)
        try:
            return await svc.get_content_recommendation(_me(ctx), content_code,
                                                        requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_path_error(exc) from exc


# ---------------------------------------------------------------------------
# PHASE 22 - Adaptive Practice: turn a learning-path recommendation into a real,
# executable practice Activity (origin=PRACTICE). The player / correction /
# result / analysis flows are the EXISTING /activities/{id}/... endpoints.
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _BaseModel, Field as _Field  # noqa: E402
from ...services.adaptive_practice import (  # noqa: E402
    AdaptivePracticeService,
    MODE_CONTENT,
    PROPOSITO_PRATICA,
    PracticeAuthError,
    PracticeError,
    PracticeNotFound,
)
from ...services.verificacao_da_habilidade import (  # noqa: E402
    selecao_para_pratica,
)


class _PracticeCreateRequest(_BaseModel):
    content_code: str = _Field(min_length=1, max_length=100)
    question_count: int = _Field(default=10, ge=1, le=20)
    mode: str = MODE_CONTENT
    # PARA QUE ESTE LOTE EXISTE. A verificacao do assessor usa o mesmo motor
    # com menos questoes; sem isto ela ficava indistinguivel de uma pratica
    # depois de corrigida, e uma verificacao FALHADA era lida como "mais uma
    # pratica fraca" - o achado medido em 2026-10-06. Lista fechada: um valor
    # novo e decisao consciente, nao campo livre vindo do navegador.
    # A lista fechada vive no servico (`PROPOSITOS`), que recusa o que nao
    # conhece com 422 - aqui seria uma segunda copia para divergir depois.
    purpose: str = PROPOSITO_PRATICA


def _map_practice_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PracticeNotFound):
        return HTTPException(status_code=404, detail="Practice not found")
    if isinstance(exc, PracticeAuthError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (PracticeError, ValueError)):
        return HTTPException(status_code=422, detail={"message": str(exc),
                                                     **(getattr(exc, "payload", {}) or {})})
    raise exc  # pragma: no cover


@student_router.post("/practice",
                     summary="Create an adaptive practice Activity for the caller (origin=PRACTICE)")
async def create_student_practice(
    payload: _PracticeCreateRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        svc = AdaptivePracticeService(session)
        aluno = _me(ctx)
        requester = _student_requester(ctx)
        try:
            # A SELECAO POR MICRO-HABILIDADE, quando existe item curado para
            # verificar a lacuna sem apoio.
            #
            # Ate 2026-10-07 este lote vinha sempre da selecao por CONTEUDO,
            # e para Estequiometria isso significava questoes de proporcao e
            # de relacao massa-mol: a micro-habilidade que acabou de ser
            # ensinada nunca era verificada sozinha. A habilidade e decidida
            # no SERVIDOR - ver `selecao_para_pratica`.
            #
            # Sem item curado a selecao devolve vazio e tudo segue como
            # antes. Nenhum conteudo perde o que ja tinha.
            escolha = await selecao_para_pratica(
                session, aluno=aluno, conteudo=payload.content_code,
                quantas=payload.question_count, requester=requester)
            saida = await svc.create_practice(
                aluno, requester=requester,
                content_code=payload.content_code, mode=payload.mode,
                question_count=payload.question_count,
                purpose=payload.purpose,
                question_version_ids=(escolha.get("question_version_ids")
                                      or None))
            if escolha:
                # A ORIGEM DA SELECAO VIAJA COMO DADO. Sem isto, descobrir
                # por que o aluno recebeu Na2O e nao uma questao qualquer do
                # conteudo exigiria reexecutar a selecao.
                saida["skill_verified"] = escolha["skill"]
                saida["selection_reason"] = escolha["motivo"]
            return saida
        except Exception as exc:  # noqa: BLE001
            raise _map_practice_error(exc) from exc


@student_router.get("/practice", summary="The caller's own practice activities")
async def list_student_practice(
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        svc = AdaptivePracticeService(session)
        try:
            return await svc.list_practices(_me(ctx), requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_practice_error(exc) from exc


@student_router.get("/practice/{practice_id}", summary="One of the caller's practice activities")
async def get_student_practice(
    practice_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        svc = AdaptivePracticeService(session)
        try:
            return await svc.get_practice(_me(ctx), practice_id, requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_practice_error(exc) from exc


# ============================================================================
# PHASE 24 - Study Session / Momento de Aprendizado (orchestration only).
# Reuses PHASE 21 path + PHASE 23 material availability + PHASE 22 practice +
# the PHASE 17/18 player/correction endpoints. Always self. Zero AI.
# ============================================================================
from ...services.study_session import (  # noqa: E402
    StudySessionService,
    StudySessionAuthError,
    StudySessionError,
    StudySessionNotFound,
)


class _StudySessionCreateRequest(_BaseModel):
    available_minutes: int | None = _Field(default=None, ge=5, le=600)
    no_timer: bool = False
    target_content_codes: list[str] | None = _Field(default=None, max_length=8)


class _BlockCompleteRequest(_BaseModel):
    skipped: bool = False


def _map_study_session_error(exc: Exception) -> HTTPException:
    if isinstance(exc, StudySessionNotFound):
        return HTTPException(status_code=404, detail="Study session not found")
    if isinstance(exc, StudySessionAuthError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (StudySessionError, ValueError)):
        return HTTPException(status_code=422, detail={"message": str(exc)})
    raise exc  # pragma: no cover


@student_router.get("/study-session/today",
                    summary="The caller's study session for today (school-scheduled or free)")
async def student_study_session_today(
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await StudySessionService(session).get_today(
                _me(ctx), requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_study_session_error(exc) from exc


@student_router.post("/study-session",
                     summary="Create a free (student-defined) study session for the caller")
async def create_student_study_session(
    payload: _StudySessionCreateRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await StudySessionService(session).create_student_session(
                _me(ctx), requester=_student_requester(ctx),
                available_minutes=payload.available_minutes, no_timer=payload.no_timer,
                target_content_codes=payload.target_content_codes)
        except Exception as exc:  # noqa: BLE001
            raise _map_study_session_error(exc) from exc


@student_router.get("/study-session/{session_id}", summary="One of the caller's study sessions")
async def get_student_study_session(
    session_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await StudySessionService(session).get_session(
                _me(ctx), session_id, requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_study_session_error(exc) from exc


@student_router.post("/study-session/{session_id}/start", summary="Start the study session")
async def start_student_study_session(
    session_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await StudySessionService(session).start_session(
                _me(ctx), session_id, requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_study_session_error(exc) from exc


@student_router.post("/study-session/{session_id}/blocks/{index}/start",
                     summary="Start one block (materializes a PRACTICE block's activity, idempotent)")
async def start_student_study_session_block(
    session_id: _UUID,
    index: int,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await StudySessionService(session).start_block(
                _me(ctx), session_id, int(index), requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_study_session_error(exc) from exc


@student_router.post("/study-session/{session_id}/blocks/{index}/complete",
                     summary="Mark one block done (or skipped)")
async def complete_student_study_session_block(
    session_id: _UUID,
    index: int,
    payload: _BlockCompleteRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await StudySessionService(session).complete_block(
                _me(ctx), session_id, int(index), requester=_student_requester(ctx),
                skipped=payload.skipped)
        except Exception as exc:  # noqa: BLE001
            raise _map_study_session_error(exc) from exc


@student_router.post("/study-session/{session_id}/complete", summary="Finish the study session")
async def complete_student_study_session(
    session_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await StudySessionService(session).complete_session(
                _me(ctx), session_id, requester=_student_requester(ctx))
        except Exception as exc:  # noqa: BLE001
            raise _map_study_session_error(exc) from exc


# ============================================================================
# PHASE 25 - Material Delivery & Study Integration (Material Player).
# Reuses the PHASE 23 material model as-is (no second model) and PHASE 25's
# tenant-aware MaterialAvailabilityService.visible_to_student() for authz.
# Never touches domain_content_mastery - reading a material is not mastery
# evidence. Associated exercises are never played here - the frontend uses
# the existing POST /student/practice (AdaptivePracticeService) entry point.
# ============================================================================
from ...services.student_material import (  # noqa: E402
    MaterialAccessError,
    MaterialNotFoundError,
    StudentMaterialService,
)


class _MaterialProgressSaveRequest(_BaseModel):
    current_section_id: _UUID | None = None
    current_block_id: _UUID | None = None
    completed: bool = False


def _map_material_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MaterialNotFoundError):
        return HTTPException(status_code=404, detail=str(exc) or "Material not found")
    if isinstance(exc, MaterialAccessError):
        return HTTPException(status_code=403, detail=str(exc) or "Material not visible to this student")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail={"message": str(exc)})
    raise exc  # pragma: no cover


@student_router.get("/materials", summary="Materials visible to the caller (optionally filtered by content)")
async def list_student_materials(
    content_code: Optional[str] = Query(default=None),
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> list[dict]:
    async with session_factory() as session:
        try:
            return await StudentMaterialService(session).list_materials(
                requester_school_id=ctx.school_id, content_code=content_code)
        except Exception as exc:  # noqa: BLE001
            raise _map_material_error(exc) from exc


@student_router.get("/materials/{material_id}", summary="One material's overview")
async def get_student_material(
    material_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await StudentMaterialService(session).get_material(
                material_id, requester_school_id=ctx.school_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_material_error(exc) from exc


@student_router.get("/materials/{material_id}/sections",
                    summary="Ordered sections (with blocks and exercises) of one material")
async def get_student_material_sections(
    material_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> list[dict]:
    async with session_factory() as session:
        try:
            return await StudentMaterialService(session).get_sections(
                material_id, requester_school_id=ctx.school_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_material_error(exc) from exc


@student_router.get("/materials/{material_id}/progress",
                    summary="The caller's own reading progress for this material")
async def get_student_material_progress(
    material_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await StudentMaterialService(session).get_progress(
                material_id, _me(ctx), requester_school_id=ctx.school_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_material_error(exc) from exc


@student_router.put("/materials/{material_id}/progress",
                    summary="Save the caller's current reading position (idempotent upsert)")
async def save_student_material_progress(
    material_id: _UUID,
    payload: _MaterialProgressSaveRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await StudentMaterialService(session).save_progress(
                material_id, _me(ctx), requester_school_id=ctx.school_id,
                current_section_id=payload.current_section_id,
                current_block_id=payload.current_block_id,
                completed=payload.completed)
        except Exception as exc:  # noqa: BLE001
            raise _map_material_error(exc) from exc


# ============================================================================
# PILOTO ZERO - prontidao para a tarefa da escola e o microdiagnostico.
#
# Tres endpoints finos sobre servicos que ja existiam. Nenhum motor novo:
#   readiness       -> ReadinessRouteService (planejador + conteudos exigidos)
#   micro-diagnostic-> MicroDiagnosticService (que reusa AdaptivePracticeService)
#   decision        -> MicroDiagnosticService.decidir sobre o dominio RECALCULADO
#
# O que eles acrescentam e o CAMINHO: ate aqui o aluno via a decisao de
# prontidao calculada no proprio navegador, a partir de um MOCK.
# ============================================================================
from ...services.micro_diagnostic import (  # noqa: E402
    DECISION_INSUFFICIENT,
    MicroDiagnosticService,
)
from ...services.diagnostico_por_habilidade import (  # noqa: E402
    diagnostico_por_habilidade,
)
from ...services.feedback_pedagogico import feedback_do_diagnostico  # noqa: E402
from ...services.readiness_route import (  # noqa: E402
    AtividadeNaoVisivel,
    ReadinessRouteService,
)


async def _respostas_por_habilidade(session, assignment_id, aluno: str) -> list[dict]:
    """Casa cada resposta corrigida com a micro-habilidade que o item mede.

    A habilidade vive em `PedagogicalClassification.subcontent`, gravada pelo
    gerador do Diagnostic Bank. Um item sem habilidade declarada fica de fora
    da agregacao - contaria como evidencia sobre algo que nao sabemos o que e.
    """
    from sqlalchemy import select as _select

    from ...db.models import ActivityResult, ActivityResultItem
    from ...db.models.pedagogical import PedagogicalClassification as _PC

    resultado = (await session.execute(
        _select(ActivityResult).where(
            ActivityResult.assignment_id == assignment_id,
            ActivityResult.student_external_id == aluno))).scalar_one_or_none()
    if resultado is None:
        return []
    itens = (await session.execute(
        _select(ActivityResultItem).where(
            ActivityResultItem.result_id == resultado.id))).scalars().all()
    if not itens:
        return []

    vids = {i.question_version_id for i in itens}
    skills = dict((await session.execute(
        _select(_PC.question_version_id, _PC.subcontent).where(
            _PC.question_version_id.in_(vids),
            _PC.lifecycle == "ACTIVE"))).all())
    return [{"diagnostic_skill": skills.get(i.question_version_id),
             "is_correct": bool(i.is_correct)} for i in itens]


@student_router.get("/activities/{assignment_id}/readiness",
                    summary="Pode comecar esta atividade? (DIRECT / DIAGNOSTIC / PREREQUISITE_PREPARATION)")
async def get_activity_readiness(
    assignment_id: _UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await ReadinessRouteService(session).para_atividade(
                assignment_id, _me(ctx), requester=_student_requester(ctx))
        except (AtividadeNaoVisivel, AssignmentNotFound, PlayerNotFound,
                AssignmentAuthError, PlayerAuthError) as exc:
            raise HTTPException(
                status_code=404, detail=RECURSO_PRIVADO_NAO_ENCONTRADO) from exc


# ============================================================================
# CONVERSAR COM O ASSESSOR
#
# Uma duvida, DENTRO da intervencao que o aluno esta vivendo - nao um chat de
# uso geral com um campo de texto.
#
# O CONTEXTO E MONTADO AQUI, NO BACKEND, a partir da prontidao - que ja e a
# autoridade sobre o passo pedagogico e ja so devolve atividade DESTE aluno.
# Isso resolve duas coisas de uma vez: o isolamento (quem pergunta sobre a
# atividade de outro recebe 404, pelo mesmo caminho de sempre) e a regra de
# que o JavaScript nao constroi verdade pedagogica.
#
# E o que NAO vai: alternativa, enunciado e resposta correta. A protecao
# contra "me diga a letra" nao e uma instrucao no prompt - e a ausencia do
# dado. Ver `conversa_do_assessor.CAMPOS_DO_CONTEXTO`.
# ============================================================================


class _TurnoDaConversa(_BaseModel):
    de: str = _Field(max_length=16)
    texto: str = _Field(max_length=600)


class _ConversaRequest(_BaseModel):
    assignment_id: str
    message: str = _Field(min_length=1, max_length=600)
    # O historico vem do navegador porque a conversa NAO E PERSISTIDA nesta
    # versao: recarregar a pagina a perde, e esta dito na tela. Guardar texto
    # de aluno exige decisao de retencao que este bloco nao tomou.
    history: list[_TurnoDaConversa] = _Field(default_factory=list, max_length=20)


@student_router.post("/assessor/conversation",
                     summary="Pergunta ao Assessor, no contexto da intervencao atual")
async def conversar_com_o_assessor(
    payload: _ConversaRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    from agente_ia_edu.services.conversa_do_assessor import (
        ConversaDoAssessor, PerguntaInvalida,
    )

    try:
        alvo = _UUID(payload.assignment_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404,
                            detail=RECURSO_PRIVADO_NAO_ENCONTRADO) from exc

    async with session_factory() as session:
        try:
            prontidao = await ReadinessRouteService(session).para_atividade(
                alvo, _me(ctx), requester=_student_requester(ctx))
        except (AtividadeNaoVisivel, AssignmentNotFound, PlayerNotFound,
                AssignmentAuthError, PlayerAuthError) as exc:
            raise HTTPException(
                status_code=404, detail=RECURSO_PRIVADO_NAO_ENCONTRADO) from exc

    contexto = _contexto_da_conversa(prontidao)
    try:
        resposta = await ConversaDoAssessor().responder(
            pergunta=payload.message,
            contexto=contexto,
            historico=[t.model_dump() for t in payload.history])
    except PerguntaInvalida as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # O passo continua sendo do sistema: a conversa nao o move, e a tela usa
    # este campo para o botao "voltar ao percurso".
    resposta["next_step"] = {
        "kind": (prontidao.get("next_step") or {}).get("kind"),
        "cta": (prontidao.get("next_step") or {}).get("cta"),
    }
    return resposta


def _contexto_da_conversa(prontidao: dict) -> dict:
    """So o que a conversa precisa saber - e nada que ela nao deva."""
    passo = prontidao.get("next_step") or {}
    intervencao = passo.get("intervention") or {}
    return {
        "objetivo": passo.get("for_content_name") or prontidao.get("title"),
        "conteudo": passo.get("content_name"),
        "habilidade": intervencao.get("skill_name"),
        "passo": passo.get("kind"),
        "ciclo": intervencao.get("cycle"),
        "tendencia": intervencao.get("trend"),
        # Ha questao de avaliacao aberta? Entao o Assessor ensina o caminho e
        # nao a alternativa - e ele nem recebe a alternativa para poder errar.
        "avaliacao_aberta": passo.get("kind") in (
            "DIAGNOSTIC", "PRACTICE", "VERIFY", "ACTIVITY"),
    }


class _MicroDiagnosticRequest(_BaseModel):
    content_code: str = _Field(min_length=1, max_length=100)
    # A tarefa da escola continua sendo o OBJETIVO enquanto o aluno se prepara.
    objective_assignment_id: str | None = None


@student_router.post("/micro-diagnostic",
                     summary="Abre o microdiagnostico de um conteudo (evidencia MICRO_DIAGNOSTIC)")
async def start_micro_diagnostic(
    payload: _MicroDiagnosticRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        svc = MicroDiagnosticService(session)
        try:
            out = await svc.start(_me(ctx), requester=_student_requester(ctx),
                                  content_code=payload.content_code)
        except Exception as exc:  # noqa: BLE001
            raise _map_practice_error(exc) from exc
        out["objective_assignment_id"] = payload.objective_assignment_id
        out["objective_completed"] = False
        # 200 mesmo quando o banco nao tem questoes suficientes: nao e erro do
        # cliente, e um fato sobre o acervo, e a tela precisa dizer qual.
        return out


@student_router.get("/micro-diagnostic/{assignment_id}/decision",
                    summary="A decisao, sobre o dominio RECALCULADO depois da correcao")
async def get_micro_diagnostic_decision(
    assignment_id: _UUID,
    content_code: str,
    objective_assignment_id: str | None = None,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    """Le o estado NOVO do aluno, nunca a decisao que estava em memoria.

    Entre o inicio do diagnostico e esta chamada entraram respostas, correcao
    e evidencia; reaproveitar a decisao anterior seria responder a pergunta
    antiga. Por isso o dominio e reconstruido aqui, e so entao a politica
    decide.
    """
    async with session_factory() as session:
        requester = _student_requester(ctx)
        aluno = _me(ctx)

        dominio = CurriculumDomainMapService(session)
        try:
            await dominio.rebuild_student(aluno, requester=requester)
            envelope = await dominio.get_content(aluno, content_code, requester=requester)
        except Exception as exc:  # noqa: BLE001
            raise _map_domain_error(exc) from exc

        # `get_content` devolve {student, taxonomy, discipline, content}. Ler
        # do envelope em vez de `content` daria sempre 0 respostas, e o
        # diagnostico concluiria INSUFFICIENT_EVIDENCE para todo mundo.
        conteudo = envelope.get("content") or {}
        respondidas = int(conteudo.get("questions_answered") or 0)
        acerto = conteudo.get("accuracy")

        # Ha pre-requisito a preparar? Sem isso, "prepare o pre-requisito"
        # seria um conselho sem destino.
        caminho = AdaptiveLearningPathService(session)
        faltando = None
        try:
            rec = await caminho.get_content_recommendation(
                aluno, content_code, requester=requester)
            pendentes = (rec.get("recommendation") or {}).get("unsatisfied_prerequisites") or []
            faltando = (pendentes[0].get("code") if pendentes else None)
        except Exception:  # noqa: BLE001 - conteudo fora do caminho: sem pre-requisito conhecido
            faltando = None

        decisao = MicroDiagnosticService(session).decidir(
            answered=respondidas, accuracy=acerto, prerequisito_em_falta=faltando)
        decisao["content_code"] = content_code
        decisao["content_name"] = (conteudo.get("content_name") or content_code)
        decisao["assignment_id"] = str(assignment_id)
        decisao["evidence_origin"] = "MICRO_DIAGNOSTIC"
        # Diagnostico NAO conclui a tarefa da escola.
        decisao["objective_completed"] = False

        # O TEXTO E DAQUI, nao do JavaScript. Ate 2026-10-04 a tela montava a
        # frase num `switch` sobre a decisao, e dizia "agora falta
        # Balanceamento" a quem acabara de demonstrar Balanceamento.
        objetivo_nome = None
        proximo = None
        if objective_assignment_id:
            try:
                prontidao = await ReadinessRouteService(session).para_atividade(
                    _UUID(objective_assignment_id), aluno, requester=requester)
                # O nome do OBJETIVO so faz sentido quando ele e OUTRO
                # conteudo. Quando o diagnostico e do proprio conteudo da
                # atividade, `for_content_name` e igual ao que acabou de ser
                # diagnosticado, e cair no titulo produzia a frase
                # "Estequiometria e base para avancarmos em Atividade de
                # Estequiometria".
                alvo_nome = (prontidao.get("next_step") or {}).get("for_content_name")
                objetivo_nome = (alvo_nome
                                 if alvo_nome and alvo_nome != content_code
                                 and alvo_nome != conteudo.get("content_name")
                                 else None)
                proximo = prontidao.get("next_step")
            except Exception:  # noqa: BLE001 - sem objetivo visivel, segue sem ele
                objetivo_nome = None

        # O ALVO DA INTERVENCAO ENTRA NA FRASE.
        #
        # Sem ele, a tela dizia "bom dominio de Estequiometria" enquanto o
        # botao logo abaixo levava a investigar massa molar. `proximo` ja foi
        # calculado acima e carrega a intervencao - e usa-lo aqui faz a
        # mensagem seguir a decisao mais especifica que existe, em vez da
        # mais geral.
        alvo_da_intervencao = (
            ((proximo or {}).get("intervention") or {}).get("skill_name"))
        decisao["feedback"] = feedback_do_diagnostico(
            decision=decisao["decision"], band=decisao["band"],
            content_name=decisao["content_name"], objective_name=objetivo_nome,
            alvo_nome=alvo_da_intervencao)

        # POR HABILIDADE - so quando a amostra sustenta.
        #
        # Tres perguntas por sessao e quatro habilidades: o normal e cada
        # habilidade receber UMA resposta, e uma resposta nao distingue quem
        # sabe de quem chutou. O modulo cala sozinho nesse caso, e a tela so
        # mostra `texto` quando ele existe.
        try:
            por_habilidade = await _respostas_por_habilidade(
                session, assignment_id, aluno)
            decisao["skills"] = diagnostico_por_habilidade(por_habilidade)
        except Exception:  # noqa: BLE001 - detalhe opcional nunca derruba a decisao
            decisao["skills"] = {"por_habilidade": {}, "suficiente": False,
                                 "texto": None}
        # O proximo passo ja recalculado sobre o estado NOVO, para a tela nao
        # precisar de uma segunda chamada nem adivinhar.
        decisao["next_step"] = proximo
        return decisao


# ============================================================================
# PRATICA GUIADA - o aluno tenta, e a ajuda chega quando precisa.
#
# Tres rotas finas sobre `PraticaGuiadaService`. Nenhum motor novo: a
# conferencia e a progressao da ajuda estao no servico, e a tela so desenha.
#
# NADA DAQUI ESCREVE DOMINIO. A interacao assistida vai para
# `guided_practice_items`, que o mapa de dominio nao le - acertar com ajuda
# nao e dominar sozinho, e a comprovacao continua exigindo pratica autonoma.
# ============================================================================
from ...services.pratica_guiada import (  # noqa: E402
    PraticaGuiadaService,
    SemItemGuiado,
)


class _RespostaGuiadaRequest(_BaseModel):
    selected_option: str = _Field(default="", max_length=8)


def _guiada_404(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=RECURSO_PRIVADO_NAO_ENCONTRADO)


@student_router.get("/guided-practice",
                    summary="Abre (ou retoma) a pratica guiada de um conteudo")
async def abrir_pratica_guiada(
    content_code: str,
    skill: str | None = None,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await PraticaGuiadaService(session).abrir(
                _me(ctx), content_code, skill,
                requester=_student_requester(ctx))
        except SemItemGuiado as exc:
            raise _guiada_404(exc) from exc
        except PermissionError as exc:
            # Mesma resposta de "nao existe": quem nao pode ver tambem nao
            # pode descobrir que existe.
            raise _guiada_404(exc) from exc


@student_router.post("/guided-practice/{item_key}/answer",
                     summary="Uma tentativa na pratica guiada")
async def responder_pratica_guiada(
    item_key: str,
    payload: _RespostaGuiadaRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await PraticaGuiadaService(session).responder(
                _me(ctx), item_key, payload.selected_option,
                requester=_student_requester(ctx))
        except SemItemGuiado as exc:
            raise _guiada_404(exc) from exc
        except PermissionError as exc:
            raise _guiada_404(exc) from exc


# ============================================================================
# INVESTIGACAO - o degrau mais alto da escada de apoio.
#
# Duas rotas finas sobre `InvestigacaoService`. Nenhum motor novo: a cadeia
# curada esta em `investigacao_do_erro`, a conferencia esta no servico, e a
# tela so desenha.
#
# NADA DAQUI ESCREVE DOMINIO. Cada etapa respondida vai para
# `guided_practice_items` - a mesma tabela da pratica guiada, que o mapa de
# dominio nao le. Responder uma micropergunta logo depois de o sistema dizer
# qual etapa e nao e a mesma coisa que resolver o problema sozinho.
# ============================================================================
from ...services.servico_de_investigacao import (  # noqa: E402
    InvestigacaoService,
    SemInvestigacao,
)


class _RespostaDaEtapaRequest(_BaseModel):
    ordem: int = _Field(ge=1, le=20)
    # A LETRA OU O QUE ELE ESCREVEU. Numa conversa o aluno digita "3", nao
    # "C"; exigir a letra o obrigaria a traduzir a propria resposta para o
    # formato interno da tela. O limite subiu de 8 para caber uma frase
    # curta - "acho que sao 3" -, e a leitura continua deterministica.
    selected_option: str = _Field(default="", max_length=200)


def _investigacao_404(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=RECURSO_PRIVADO_NAO_ENCONTRADO)


@student_router.get("/investigation",
                    summary="Abre (ou retoma) a investigacao de uma lacuna")
async def abrir_investigacao(
    content_code: str,
    skill: str | None = None,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await InvestigacaoService(session).abrir(
                _me(ctx), content_code, skill,
                requester=_student_requester(ctx))
        except SemInvestigacao as exc:
            raise _investigacao_404(exc) from exc
        except PermissionError as exc:
            # Mesma resposta de "nao existe": quem nao pode ver tambem nao
            # pode descobrir que existe.
            raise _investigacao_404(exc) from exc


class _RespostaAbertaRequest(_BaseModel):
    # Texto livre, curto. O limite existe para nao transformar a caixa de
    # resposta num campo de redacao: a pergunta pede um numero ou uma frase.
    texto: str = _Field(default="", max_length=400)


@student_router.post("/investigation/{inv_key}/opening",
                     summary="Responde, por escrito, a pergunta de abertura")
async def responder_abertura_da_investigacao(
    inv_key: str,
    payload: _RespostaAbertaRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    """A resposta ESCRITA que abre a conversa.

    O que volta carrega a observacao e, quando o valor sugerir uma, a frase
    HEDGEADA da hipotese - nunca uma afirmacao sobre o raciocinio do aluno.
    A conferencia e aqui, como sempre: o cliente nao sabe o gabarito.
    """
    async with session_factory() as session:
        try:
            return await InvestigacaoService(session).responder_abertura(
                _me(ctx), inv_key, payload.texto,
                requester=_student_requester(ctx))
        except SemInvestigacao as exc:
            raise _investigacao_404(exc) from exc
        except PermissionError as exc:
            raise _investigacao_404(exc) from exc


@student_router.post("/investigation/{inv_key}/answer",
                     summary="Responde uma etapa da investigacao")
async def responder_etapa_da_investigacao(
    inv_key: str,
    payload: _RespostaDaEtapaRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await InvestigacaoService(session).responder(
                _me(ctx), inv_key, payload.ordem, payload.selected_option,
                requester=_student_requester(ctx))
        except SemInvestigacao as exc:
            raise _investigacao_404(exc) from exc
        except PermissionError as exc:
            raise _investigacao_404(exc) from exc


@student_router.post("/guided-practice/{item_key}/hint",
                     summary="Libera o PROXIMO nivel de ajuda - um por vez")
async def pedir_ajuda_pratica_guiada(
    item_key: str,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        try:
            return await PraticaGuiadaService(session).pedir_ajuda(
                _me(ctx), item_key, requester=_student_requester(ctx))
        except SemItemGuiado as exc:
            raise _guiada_404(exc) from exc
        except PermissionError as exc:
            raise _guiada_404(exc) from exc
