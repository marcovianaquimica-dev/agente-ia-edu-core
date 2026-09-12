from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_session_factory,
)
from agente_ia_edu.api.schemas.assessments import (
    AssessmentAssignmentCreateRequest,
    AssessmentAssignmentResponse,
    AssessmentCreateRequest,
    AssessmentItemCreateRequest,
    AssessmentListResponse,
    AssessmentPublicationCreateRequest,
    AssessmentPublicationResponse,
    AssessmentResponse,
    AssessmentVersionCreateRequest,
    AssessmentVersionResponse,
)
from agente_ia_edu.db.models import (
    Assessment,
    AssessmentAssignment,
    AssessmentPublication,
    AssessmentVersion,
    AssessmentWorkflowAudit,
    ContentQuestionLink,
    Question,
    QuestionVersion,
    UserSchoolLink,
)
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.answer_key import resolve_official_answer_key_snapshots
from agente_ia_edu.services.assessments import AssessmentPersistenceService

router = APIRouter(prefix="/api/v1/assessments", tags=["assessments"])
AUTHOR_ROLES = {"TEACHER", "COORDINATOR", "DIRECTOR"}


async def get_assessment_service(
    session_factory=Depends(get_session_factory),
) -> AsyncIterator[AssessmentPersistenceService]:
    async with session_factory() as session:
        yield AssessmentPersistenceService(session)


def _require_author(context: AuthenticatedUserContext, school_id: UUID) -> None:
    if context.role not in AUTHOR_ROLES:
        raise HTTPException(status_code=403, detail="Assessment author role required")
    if context.school_id is None or str(context.school_id) != str(school_id):
        raise HTTPException(status_code=403, detail="Assessment school scope denied")


def _assessment_response(assessment: Assessment) -> AssessmentResponse:
    return AssessmentResponse(
        id=assessment.id,
        title=assessment.title,
        description=assessment.description,
        status=assessment.status,
        institution_id=str(assessment.institution_id) if assessment.institution_id else None,
        school_id=assessment.school_id,
        owner_external_id=assessment.owner_external_id,
        scope_type=assessment.scope_type,
        scope_external_id=assessment.scope_external_id,
        created_by_external_identity=assessment.created_by_external_identity,
    )


def _version_response(version: AssessmentVersion) -> AssessmentVersionResponse:
    return AssessmentVersionResponse(
        id=version.id,
        assessment_id=version.assessment_id,
        version_number=version.version_number,
        title=version.title,
        description=version.description,
        status=version.status,
        created_by_external_identity=version.created_by_external_identity,
        published_at=version.published_at,
    )


def _publication_response(
    publication: AssessmentPublication, assessment_id: UUID
) -> AssessmentPublicationResponse:
    return AssessmentPublicationResponse(
        id=publication.id,
        assessment_id=assessment_id,
        assessment_version_id=publication.assessment_version_id,
        publication_type=publication.publication_type,
        status=publication.status,
        starts_at=publication.starts_at,
        ends_at=publication.ends_at,
        time_limit_seconds=publication.time_limit_seconds,
        attempts_allowed=publication.attempts_allowed,
    )


async def _load_assessment_for_author(session, assessment_id, context):
    assessment = await session.get(Assessment, assessment_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if assessment.school_id is None:
        raise HTTPException(status_code=403, detail="Assessment has no school scope")
    _require_author(context, assessment.school_id)
    return assessment


@router.post("", response_model=AssessmentResponse, status_code=201)
async def create_assessment(
    payload: AssessmentCreateRequest,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> AssessmentResponse:
    school_id = payload.school_id or context.school_id
    if school_id is None:
        raise HTTPException(status_code=403, detail="Assessment requires school scope")
    school_id = UUID(str(school_id))
    _require_author(context, school_id)
    scope_type = (payload.scope_type or context.scope_type or "SCHOOL").upper()
    scope_external_id = payload.scope_external_id or context.scope_external_id
    if context.scope_type not in {"SCHOOL", "PLATFORM"} and (
        scope_type != context.scope_type or scope_external_id != context.scope_external_id
    ):
        raise HTTPException(status_code=403, detail="Assessment academic scope denied")
    async with session_factory() as session:
        service = AssessmentPersistenceService(session)
        created = await service.create_assessment(
            title=payload.title,
            description=payload.description,
            institution_id=payload.institution_id,
            school_id=str(school_id),
            created_by_external_identity=context.external_identity_id,
            owner_external_id=context.external_identity_id,
            visibility_scope="SCHOOL",
            origin_type="TEACHER" if context.role == "TEACHER" else "SCHOOL",
            scope_type=scope_type,
            scope_external_id=scope_external_id,
        )
        assessment = await session.get(Assessment, created.id)
        assessment.metadata_ = {"academic_year": payload.academic_year}
        await session.commit()
        await session.refresh(assessment)
        return _assessment_response(assessment)


@router.get("", response_model=AssessmentListResponse)
async def list_assessments(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> AssessmentListResponse:
    if context.role not in AUTHOR_ROLES or context.school_id is None:
        raise HTTPException(status_code=403, detail="Assessment author role required")
    async with session_factory() as session:
        rows = list((await session.scalars(
            select(Assessment).where(Assessment.school_id == context.school_id)
            .order_by(Assessment.created_at.desc()).offset((page - 1) * limit).limit(limit)
        )).all())
        return AssessmentListResponse(
            items=[_assessment_response(row) for row in rows], total=len(rows)
        )


@router.get("/{assessment_id}", response_model=AssessmentResponse)
async def get_assessment(
    assessment_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> AssessmentResponse:
    async with session_factory() as session:
        return _assessment_response(
            await _load_assessment_for_author(session, assessment_id, context)
        )


@router.post("/{assessment_id}/versions", response_model=AssessmentVersionResponse, status_code=201)
async def create_assessment_version(
    assessment_id: UUID,
    payload: AssessmentVersionCreateRequest,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> AssessmentVersionResponse:
    async with session_factory() as session:
        await _load_assessment_for_author(session, assessment_id, context)
        latest = await session.scalar(
            select(AssessmentVersion.version_number)
            .where(AssessmentVersion.assessment_id == assessment_id)
            .order_by(AssessmentVersion.version_number.desc()).limit(1)
        )
        service = AssessmentPersistenceService(session)
        created = await service.create_version(
            assessment_id=assessment_id,
            version_number=(latest or 0) + 1,
            title=payload.title,
            description=payload.description,
            status="draft",
            created_by_external_identity=context.external_identity_id,
        )
        await session.commit()
        return _version_response(await session.get(AssessmentVersion, created.id))


@router.post("/{assessment_id}/versions/{version_id}/items", response_model=dict, status_code=201)
async def add_assessment_item(
    assessment_id: UUID,
    version_id: UUID,
    payload: AssessmentItemCreateRequest,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        assessment = await _load_assessment_for_author(session, assessment_id, context)
        version = await session.get(AssessmentVersion, version_id)
        if version is None or version.assessment_id != assessment.id:
            raise HTTPException(status_code=404, detail="Assessment version not found")
        if version.status != "draft":
            raise HTTPException(status_code=409, detail="Assessment version is immutable")
        question_version = await session.get(QuestionVersion, payload.question_version_id)
        if question_version is None:
            raise HTTPException(status_code=404, detail="Question version not found")
        question = await session.get(Question, question_version.question_id)
        linked = await session.scalar(
            select(ContentQuestionLink.id).where(
                ContentQuestionLink.question_version_id == question_version.id
            ).limit(1)
        )
        if (
            question is None
            or question.status != "PUBLISHED"
            or str(question.validation_status).lower() not in {"valid", "acceptable", "approved"}
            or question_version.version_kind != "official_original"
            or linked is None
        ):
            raise HTTPException(status_code=422, detail="Question is not eligible")
        if question.visibility_scope not in {"PUBLIC", "SCHOOL"}:
            raise HTTPException(status_code=403, detail="Question visibility denied")
        if question.visibility_scope == "SCHOOL" and question.school_id != assessment.school_id:
            raise HTTPException(status_code=403, detail="Question school scope denied")
        service = AssessmentPersistenceService(session)
        item = await service.add_item(
            assessment_version_id=version_id,
            question_version_id=payload.question_version_id,
            position=payload.position,
            points=payload.points,
            is_required=payload.is_required,
            selection_request_id=payload.selection_request_id,
        )
        await session.commit()
        return {
            "assessment_id": str(assessment_id),
            "version_id": str(version_id),
            "item_id": str(item.id),
            "question_version_id": str(item.question_version_id),
            "position": item.position,
            "points": item.points,
            "is_required": item.is_required,
        }


@router.post("/{assessment_id}/publications", response_model=AssessmentPublicationResponse, status_code=201)
async def create_publication(
    assessment_id: UUID,
    payload: AssessmentPublicationCreateRequest,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> AssessmentPublicationResponse:
    async with session_factory() as session:
        assessment = await _load_assessment_for_author(session, assessment_id, context)
        version = await session.get(AssessmentVersion, payload.assessment_version_id)
        if version is None or version.assessment_id != assessment.id:
            raise HTTPException(status_code=404, detail="Assessment version not found")
        if version.status != "published":
            raise HTTPException(status_code=409, detail="Only published versions can be applied")
        service = AssessmentPersistenceService(session)
        created = await service.create_publication(
            assessment_version_id=version.id,
            publication_type=payload.publication_type,
            released_immediately=payload.released_immediately,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            time_limit_seconds=payload.time_limit_seconds,
            attempts_allowed=payload.attempts_allowed,
            source_display=payload.source_display,
            bncc_display=payload.bncc_display,
            show_difficulty=payload.show_difficulty,
        )
        await session.commit()
        publication = await session.get(AssessmentPublication, created.id)
        return _publication_response(publication, assessment.id)


@router.get("/{assessment_id}/publications", response_model=list[AssessmentPublicationResponse])
async def list_publications(
    assessment_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> list[AssessmentPublicationResponse]:
    async with session_factory() as session:
        assessment = await _load_assessment_for_author(session, assessment_id, context)
        publications = list((await session.scalars(
            select(AssessmentPublication).join(AssessmentVersion).where(
                AssessmentVersion.assessment_id == assessment_id
            ).order_by(AssessmentPublication.created_at.desc())
        )).all())
        return [_publication_response(item, assessment.id) for item in publications]


async def _transition_version(
    session,
    assessment: Assessment,
    version: AssessmentVersion,
    *,
    target: str,
    context: AuthenticatedUserContext,
) -> AssessmentVersionResponse:
    expected = {"review": "draft", "approved": "review", "published": "approved"}
    if version.status != expected[target]:
        raise HTTPException(status_code=409, detail="Invalid assessment workflow transition")
    previous = version.status
    version.status = target
    assessment.status = target
    if target == "published":
        version.published_at = datetime.now(timezone.utc)
    session.add(AssessmentWorkflowAudit(
        assessment_id=assessment.id,
        action=f"ASSESSMENT_VERSION_{target.upper()}",
        previous_status=previous,
        new_status=target,
        performed_by_external_id=context.external_identity_id,
        metadata_={"assessment_version_id": str(version.id)},
    ))
    await session.commit()
    await session.refresh(version)
    return _version_response(version)


async def _version_transition_endpoint(
    assessment_id, version_id, target, context, session_factory
):
    async with session_factory() as session:
        assessment = await _load_assessment_for_author(session, assessment_id, context)
        version = await session.get(AssessmentVersion, version_id)
        if version is None or version.assessment_id != assessment.id:
            raise HTTPException(status_code=404, detail="Assessment version not found")
        return await _transition_version(
            session, assessment, version, target=target, context=context
        )


@router.post("/{assessment_id}/versions/{version_id}/review", response_model=AssessmentVersionResponse)
async def review_assessment_version(
    assessment_id: UUID,
    version_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
):
    return await _version_transition_endpoint(
        assessment_id, version_id, "review", context, session_factory
    )


@router.post("/{assessment_id}/versions/{version_id}/approve", response_model=AssessmentVersionResponse)
async def approve_assessment_version(
    assessment_id: UUID,
    version_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
):
    return await _version_transition_endpoint(
        assessment_id, version_id, "approved", context, session_factory
    )


@router.post("/{assessment_id}/versions/{version_id}/publish", response_model=AssessmentVersionResponse)
async def publish_assessment_version(
    assessment_id: UUID,
    version_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
):
    return await _version_transition_endpoint(
        assessment_id, version_id, "published", context, session_factory
    )


@router.post("/publications/{publication_id}/activate", response_model=AssessmentPublicationResponse)
async def activate_publication(
    publication_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> AssessmentPublicationResponse:
    async with session_factory() as session:
        publication = await session.scalar(
            select(AssessmentPublication)
            .where(AssessmentPublication.id == publication_id)
            .options(
                selectinload(AssessmentPublication.assessment_version)
                .selectinload(AssessmentVersion.items)
            )
        )
        if publication is None:
            raise HTTPException(status_code=404, detail="Publication not found")
        assessment = await _load_assessment_for_author(
            session, publication.assessment_version.assessment_id, context
        )
        if publication.status != "draft" or publication.assessment_version.status != "published":
            raise HTTPException(status_code=409, detail="Publication cannot be activated")
        items = sorted(publication.assessment_version.items, key=lambda item: item.position)
        if not items:
            raise HTTPException(status_code=409, detail="Publication has no items")
        snapshots = await resolve_official_answer_key_snapshots(
            session, [item.question_version_id for item in items]
        )
        if len(snapshots) != len(items):
            raise HTTPException(
                status_code=409,
                detail="Every objective item requires an official answer key",
            )
        for item in items:
            item.answer_key_revision_id, item.frozen_correct_option_id = snapshots[
                item.question_version_id
            ]
        publication.status = "active"
        publication.starts_at = publication.starts_at or datetime.now(timezone.utc)
        session.add(AssessmentWorkflowAudit(
            assessment_id=assessment.id,
            action="ASSESSMENT_PUBLICATION_ACTIVATED",
            previous_status="draft",
            new_status="active",
            performed_by_external_id=context.external_identity_id,
            metadata_={
                "assessment_version_id": str(publication.assessment_version_id),
                "publication_id": str(publication.id),
            },
        ))
        await session.commit()
        await session.refresh(publication)
        return _publication_response(publication, assessment.id)


@router.post(
    "/publications/{publication_id}/assignments",
    response_model=AssessmentAssignmentResponse,
    status_code=201,
)
async def assign_publication_to_student(
    publication_id: UUID,
    payload: AssessmentAssignmentCreateRequest,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> AssessmentAssignmentResponse:
    async with session_factory() as session:
        publication = await session.scalar(
            select(AssessmentPublication)
            .where(AssessmentPublication.id == publication_id)
            .options(selectinload(AssessmentPublication.assessment_version))
        )
        if publication is None:
            raise HTTPException(status_code=404, detail="Publication not found")
        assessment = await _load_assessment_for_author(
            session, publication.assessment_version.assessment_id, context
        )
        if publication.status != "active":
            raise HTTPException(status_code=409, detail="Publication is not active")
        student_link = await session.scalar(
            select(UserSchoolLink).where(
                UserSchoolLink.external_user_id == payload.student_external_id,
                UserSchoolLink.school_id == assessment.school_id,
                UserSchoolLink.role == "STUDENT",
                UserSchoolLink.active.is_(True),
            ).order_by(UserSchoolLink.created_at.desc())
        )
        if student_link is None:
            raise HTTPException(status_code=403, detail="Student is outside the assessment school")
        if context.scope_type == "CLASSROOM" and (
            student_link.scope_type != "CLASSROOM"
            or student_link.scope_external_id != context.scope_external_id
        ):
            raise HTTPException(status_code=403, detail="Student is outside the teacher classroom")
        assignment = AssessmentAssignment(
            assessment_id=assessment.id,
            publication_id=publication.id,
            school_id=assessment.school_id,
            recipient_type="STUDENT",
            recipient_id=payload.student_external_id,
            assigned_by_external_id=context.external_identity_id,
            status="PENDING",
            metadata_={
                "assessment_version_id": str(publication.assessment_version_id),
                "scope_type": student_link.scope_type,
                "scope_external_id": student_link.scope_external_id,
                "academic_context": student_link.metadata_ or {},
            },
        )
        session.add(assignment)
        await session.commit()
        await session.refresh(assignment)
        return AssessmentAssignmentResponse(
            id=assignment.id,
            assessment_id=assessment.id,
            publication_id=publication.id,
            student_external_id=assignment.recipient_id,
            school_id=assignment.school_id,
            status=assignment.status,
        )
