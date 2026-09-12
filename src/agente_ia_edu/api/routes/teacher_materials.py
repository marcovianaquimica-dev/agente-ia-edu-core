from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.schemas.assessments import (
    QuestionOptionResponse,
    TeacherMaterialCandidateResponse,
    TeacherMaterialCreateRequest,
    TeacherMaterialItemCreateRequest,
    TeacherMaterialQuestionResponse,
    TeacherMaterialReorderRequest,
    TeacherMaterialResponse,
)
from agente_ia_edu.db.models import (
    Assessment,
    AssessmentItem,
    AssessmentVersion,
    CatalogNode,
    ContentQuestionLink,
    Question,
    QuestionVersion,
)
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService
from agente_ia_edu.services.teacher_material_policy import TeacherMaterialPolicy


router = APIRouter(prefix="/api/v1/teacher/materials", tags=["teacher-materials"])
AUTHOR_ROLES = {"TEACHER", "COORDINATOR", "DIRECTOR"}


def _require_author(context: AuthenticatedUserContext) -> None:
    if context.role not in AUTHOR_ROLES or context.school_id is None:
        raise HTTPException(status_code=403, detail="Teacher material author role required")


async def _load_material(
    session: AsyncSession, material_id: UUID, context: AuthenticatedUserContext
) -> tuple[Assessment, AssessmentVersion]:
    material = await session.get(Assessment, material_id)
    if material is None or material.material_type != "EXERCISE_LIST":
        raise HTTPException(status_code=404, detail="Exercise list not found")
    if material.school_id != context.school_id:
        raise HTTPException(status_code=403, detail="Exercise list school scope denied")
    if context.role == "TEACHER" and material.owner_external_id != context.external_identity_id:
        raise HTTPException(status_code=403, detail="Exercise list owner scope denied")
    if context.scope_type not in {"SCHOOL", "PLATFORM"} and (
        material.scope_type != context.scope_type
        or material.scope_external_id != context.scope_external_id
    ):
        raise HTTPException(status_code=403, detail="Exercise list academic scope denied")
    version = await session.scalar(
        select(AssessmentVersion)
        .where(AssessmentVersion.assessment_id == material.id)
        .order_by(AssessmentVersion.version_number.desc())
        .limit(1)
    )
    if version is None:
        raise HTTPException(status_code=409, detail="Exercise list has no draft version")
    return material, version


async def _question_rows(
    session: AsyncSession, version: AssessmentVersion
) -> list[tuple[AssessmentItem, QuestionVersion, CatalogNode]]:
    result = await session.execute(
        select(AssessmentItem, QuestionVersion, CatalogNode)
        .join(QuestionVersion, QuestionVersion.id == AssessmentItem.question_version_id)
        .join(ContentQuestionLink, ContentQuestionLink.question_version_id == QuestionVersion.id)
        .join(CatalogNode, CatalogNode.id == ContentQuestionLink.content_node_id)
        .where(AssessmentItem.assessment_version_id == version.id)
        .options(selectinload(QuestionVersion.options))
        .order_by(AssessmentItem.position)
    )
    return list(result.all())


def _question_response(
    *, item_id: UUID, position: int, question: QuestionVersion, content: CatalogNode
) -> TeacherMaterialQuestionResponse:
    return TeacherMaterialQuestionResponse(
        id=item_id,
        question_version_id=question.id,
        question_number=position,
        stem=question.statement or question.canonical_text,
        alternatives=[
            QuestionOptionResponse(
                id=option.id, option_key=option.option_key, text=option.text, position=option.position
            )
            for option in sorted(question.options, key=lambda option: option.position)
        ],
        difficulty=question.recommended_difficulty or "UNCLASSIFIED",
        content=content.name,
        modified=(question.metadata_ or {}).get("origin_type") == "TEACHER_MODIFICATION",
    )


async def _material_response(
    session: AsyncSession, material: Assessment, version: AssessmentVersion
) -> TeacherMaterialResponse:
    rows = await _question_rows(session, version)
    return TeacherMaterialResponse(
        id=material.id,
        title=material.title,
        material_type=material.material_type,
        status=material.status,
        configuration=material.metadata_ or {},
        items=[
            _question_response(item_id=item.id, position=item.position, question=question, content=content)
            for item, question, content in rows
        ],
    )


async def _authorized_candidate(
    session: AsyncSession,
    *,
    material: Assessment,
    question_version_id: UUID,
    content_node_id: UUID,
) -> tuple[QuestionVersion, CatalogNode] | None:
    row = (await session.execute(
        select(QuestionVersion, CatalogNode)
        .join(Question, Question.id == QuestionVersion.question_id)
        .join(ContentQuestionLink, ContentQuestionLink.question_version_id == QuestionVersion.id)
        .join(CatalogNode, CatalogNode.id == ContentQuestionLink.content_node_id)
        .where(
            QuestionVersion.id == question_version_id,
            ContentQuestionLink.content_node_id == content_node_id,
            QuestionVersion.version_kind == "official_original",
            Question.status == "PUBLISHED",
            Question.validation_status.in_(("valid", "acceptable", "approved")),
            QuestionVersion.recommended_difficulty.isnot(None),
            (Question.visibility_scope == "PUBLIC") | (Question.school_id == material.school_id),
        )
        .options(selectinload(QuestionVersion.options))
    )).first()
    return row


@router.post("", response_model=TeacherMaterialResponse, status_code=201)
async def create_teacher_material(
    payload: TeacherMaterialCreateRequest,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TeacherMaterialResponse:
    _require_author(context)
    try:
        distribution = TeacherMaterialPolicy.validate_configuration(
            payload.quantity, payload.difficulty_distribution
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    async with session_factory() as session:
        universe_service = PedagogicalUniverseService(session)
        try:
            universe = await universe_service.resolve_active_universe(identity)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if not await universe_service.contains_catalog_node(universe.id, payload.content_node_id):
            raise HTTPException(status_code=403, detail="Content is outside the authorized pedagogical universe")
        content = await session.get(CatalogNode, payload.content_node_id)
        if content is None or not content.active:
            raise HTTPException(status_code=404, detail="Content not found")
        material = Assessment(
            school_id=context.school_id,
            created_by_external_identity=context.external_identity_id,
            owner_external_id=context.external_identity_id,
            visibility_scope="PRIVATE",
            origin_type="TEACHER",
            material_type="EXERCISE_LIST",
            scope_type=context.scope_type,
            scope_external_id=context.scope_external_id,
            title=payload.title,
            status="draft",
            metadata_={
                "content_node_id": str(content.id),
                "content_name": content.name,
                "quantity": payload.quantity,
                "difficulty_distribution": distribution,
                "academic_context": context.metadata.get("school_link_metadata", {}),
            },
        )
        session.add(material)
        await session.flush()
        version = AssessmentVersion(
            assessment_id=material.id, version_number=1, title=material.title,
            status="draft", created_by_external_identity=context.external_identity_id,
        )
        session.add(version)
        await session.commit()
        return await _material_response(session, material, version)


@router.get("", response_model=list[TeacherMaterialResponse])
async def list_teacher_materials(
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> list[TeacherMaterialResponse]:
    _require_author(context)
    async with session_factory() as session:
        materials = list((await session.scalars(
            select(Assessment).where(
                Assessment.school_id == context.school_id,
                Assessment.material_type == "EXERCISE_LIST",
                Assessment.owner_external_id == context.external_identity_id if context.role == "TEACHER" else True,
            ).order_by(Assessment.updated_at.desc())
        )).all())
        responses = []
        for material in materials:
            _, version = await _load_material(session, material.id, context)
            responses.append(await _material_response(session, material, version))
        return responses


@router.get("/{material_id}", response_model=TeacherMaterialResponse)
async def get_teacher_material(
    material_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> TeacherMaterialResponse:
    _require_author(context)
    async with session_factory() as session:
        material, version = await _load_material(session, material_id, context)
        return await _material_response(session, material, version)


@router.get("/{material_id}/candidates", response_model=TeacherMaterialCandidateResponse)
async def list_material_candidates(
    material_id: UUID,
    difficulty: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=24, ge=1, le=100),
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TeacherMaterialCandidateResponse:
    _require_author(context)
    async with session_factory() as session:
        material, version = await _load_material(session, material_id, context)
        content_node_id = UUID(material.metadata_["content_node_id"])
        universe_service = PedagogicalUniverseService(session)
        try:
            universe = await universe_service.resolve_active_universe(identity)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if not await universe_service.contains_catalog_node(universe.id, content_node_id):
            raise HTTPException(status_code=403, detail="Content is outside the authorized pedagogical universe")
        filters = [
            ContentQuestionLink.content_node_id == content_node_id,
            QuestionVersion.version_kind == "official_original",
            Question.status == "PUBLISHED",
            Question.validation_status.in_(("valid", "acceptable", "approved")),
            QuestionVersion.recommended_difficulty.isnot(None),
            (Question.visibility_scope == "PUBLIC") | (Question.school_id == material.school_id),
        ]
        if difficulty:
            normalized_difficulty = difficulty.upper()
            if normalized_difficulty not in TeacherMaterialPolicy.difficulty_levels:
                raise HTTPException(status_code=422, detail="Unsupported difficulty filter")
            filters.append(QuestionVersion.recommended_difficulty == normalized_difficulty)
        base = (
            select(QuestionVersion, CatalogNode)
            .join(Question, Question.id == QuestionVersion.question_id)
            .join(ContentQuestionLink, ContentQuestionLink.question_version_id == QuestionVersion.id)
            .join(CatalogNode, CatalogNode.id == ContentQuestionLink.content_node_id)
            .where(*filters)
            .order_by(QuestionVersion.created_at)
        )
        total = await session.scalar(select(func.count()).select_from(base.subquery()))
        rows = (await session.execute(
            base.options(selectinload(QuestionVersion.options)).offset((page - 1) * limit).limit(limit)
        )).all()
        availability = {level: 0 for level in TeacherMaterialPolicy.difficulty_levels}
        items = []
        for question, content in rows:
            availability[question.recommended_difficulty] += 1
            items.append(_question_response(item_id=question.id, position=0, question=question, content=content))
        return TeacherMaterialCandidateResponse(
            availability=availability,
            items=items,
            pagination={"page": page, "limit": limit, "total": total or 0, "total_pages": ((total or 0) + limit - 1) // limit},
        )


@router.post("/{material_id}/items", response_model=TeacherMaterialQuestionResponse, status_code=201)
async def add_teacher_material_item(
    material_id: UUID,
    payload: TeacherMaterialItemCreateRequest,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> TeacherMaterialQuestionResponse:
    _require_author(context)
    async with session_factory() as session:
        material, version = await _load_material(session, material_id, context)
        content_node_id = UUID(material.metadata_["content_node_id"])
        universe_service = PedagogicalUniverseService(session)
        try:
            universe = await universe_service.resolve_active_universe(identity)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if not await universe_service.contains_catalog_node(universe.id, content_node_id):
            raise HTTPException(status_code=403, detail="Content is outside the authorized pedagogical universe")
        candidate = await _authorized_candidate(
            session, material=material, question_version_id=payload.question_version_id,
            content_node_id=content_node_id,
        )
        if candidate is None:
            raise HTTPException(status_code=403, detail="Question is outside the authorized list scope")
        duplicate = await session.scalar(select(AssessmentItem.id).where(
            AssessmentItem.assessment_version_id == version.id,
            AssessmentItem.question_version_id == payload.question_version_id,
        ))
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Question is already in this list")
        current_count = await session.scalar(select(func.count()).select_from(AssessmentItem).where(
            AssessmentItem.assessment_version_id == version.id
        ))
        if current_count >= material.metadata_["quantity"]:
            raise HTTPException(status_code=409, detail="Exercise list already has the configured quantity")
        position = current_count + 1
        item = AssessmentItem(
            assessment_version_id=version.id, question_version_id=payload.question_version_id,
            position=position,
        )
        session.add(item)
        await session.commit()
        question, content = candidate
        return _question_response(item_id=item.id, position=item.position, question=question, content=content)


@router.delete("/{material_id}/items/{item_id}", status_code=204)
async def remove_teacher_material_item(
    material_id: UUID,
    item_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> Response:
    _require_author(context)
    async with session_factory() as session:
        _, version = await _load_material(session, material_id, context)
        item = await session.get(AssessmentItem, item_id)
        if item is None or item.assessment_version_id != version.id:
            raise HTTPException(status_code=404, detail="Exercise list item not found")
        await session.delete(item)
        await session.flush()
        remaining = list((await session.scalars(select(AssessmentItem).where(
            AssessmentItem.assessment_version_id == version.id
        ).order_by(AssessmentItem.position))).all())
        for temporary_position, remaining_item in enumerate(remaining, start=1):
            remaining_item.position = -temporary_position
        await session.flush()
        for position, remaining_item in enumerate(remaining, start=1):
            remaining_item.position = position
        await session.commit()
    return Response(status_code=204)


@router.patch("/{material_id}/items/reorder", response_model=TeacherMaterialResponse)
async def reorder_teacher_material_items(
    material_id: UUID,
    payload: TeacherMaterialReorderRequest,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> TeacherMaterialResponse:
    _require_author(context)
    async with session_factory() as session:
        material, version = await _load_material(session, material_id, context)
        items = list((await session.scalars(select(AssessmentItem).where(
            AssessmentItem.assessment_version_id == version.id
        ).order_by(AssessmentItem.position))).all())
        item_ids = [item.id for item in items]
        if len(payload.item_ids) != len(item_ids) or set(payload.item_ids) != set(item_ids):
            raise HTTPException(status_code=422, detail="Reorder must contain every list item exactly once")
        by_id = {item.id: item for item in items}
        for temporary_position, item_id in enumerate(payload.item_ids, start=1):
            by_id[item_id].position = -temporary_position
        await session.flush()
        for position, item_id in enumerate(payload.item_ids, start=1):
            by_id[item_id].position = position
        await session.commit()
        return await _material_response(session, material, version)